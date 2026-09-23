import logging
import threading
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .portal import PORTAL_BUS_NAME, PORTAL_OBJECT_PATH, SESSION_IFACE, PortalRequest

logger = logging.getLogger(__name__)

GLOBALSHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
SHORTCUT_ID = "runekit-alt1"

# How often the worker's GLib main loop wakes up to check whether stop() has
# been requested. Mirrors WaylandCaptureWorker's 1s appsink pull timeout in
# spirit (a short, bounded poll interval rather than a cross-thread
# GLib.MainLoop.quit() call, since quit() is invoked from the loop's own
# thread via this timeout, not from stop()'s caller thread).
STOP_POLL_INTERVAL_MS = 200


class GlobalShortcutsPortalError(Exception):
    """Raised when the GlobalShortcuts portal session cannot be established."""


class GlobalShortcutsWorker(QObject):
    """Runs the org.freedesktop.portal.GlobalShortcuts CreateSession ->
    BindShortcuts -> Activated flow on a background thread, per ROADMAP.md
    Phase 4.

    Unlike ScreenCastSession's one-shot open() (portal.py), listening for
    Activated signals is inherently long-running, so this worker keeps its
    own GLib.MainLoop alive for the lifetime of the hotkey feature rather
    than returning after a single request/response round trip. This
    mirrors WaylandCaptureWorker's QThread pattern (capture.py) so the
    blocking D-Bus/portal calls don't stall Qt's own event loop.

    Requires xdg-desktop-portal-kde >= Plasma 6.1. If the portal interface
    or the BindShortcuts call is unavailable/rejected, this logs a warning
    and returns without raising, so the Alt+1 hotkey silently no-ops on
    older Plasma rather than breaking the rest of the Wayland backend --
    see ROADMAP.md Phase 4.
    """

    activated = Signal()
    unavailable = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._stop_requested = threading.Event()
        self._loop = None

    @Slot()
    def run(self):
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                PORTAL_BUS_NAME,
                PORTAL_OBJECT_PATH,
                GLOBALSHORTCUTS_IFACE,
                None,
            )
        except GLib.Error as exc:
            self.logger.warning(
                "GlobalShortcuts portal unavailable (%s); Alt+1 hotkey disabled. "
                "Requires xdg-desktop-portal-kde >= Plasma 6.1.",
                exc,
            )
            self.unavailable.emit(str(exc))
            return

        req = PortalRequest(bus, GLib, Gio)
        session_handle = None
        try:
            self.logger.debug("Calling CreateSession")
            code, results = req.call(proxy, "CreateSession", ({},))
            if code != 0:
                raise GlobalShortcutsPortalError(
                    f"CreateSession failed with response code {code}"
                )
            session_handle = results["session_handle"]
            self.logger.debug("GlobalShortcuts session created: %s", session_handle)

            shortcuts = [
                (
                    SHORTCUT_ID,
                    {"description": GLib.Variant("s", "RuneKit Reforged: Alt+1")},
                )
            ]
            self.logger.debug("Calling BindShortcuts (requesting id=%r)", SHORTCUT_ID)
            code, results = req.call(
                proxy, "BindShortcuts", (session_handle, shortcuts, "", {}), timeout_sec=120
            )
            if code != 0:
                raise GlobalShortcutsPortalError(
                    f"BindShortcuts failed/cancelled with response code {code} "
                    "(user may have declined the shortcut-grant dialog)"
                )
        except (GLib.Error, GlobalShortcutsPortalError) as exc:
            self.logger.warning("Could not bind the Wayland Alt+1 hotkey: %s", exc)
            self.unavailable.emit(str(exc))
            if session_handle is not None:
                self._close_session(bus, session_handle)
            return

        self.logger.info("Wayland Alt+1 hotkey bound via GlobalShortcuts portal")

        sub_id = bus.signal_subscribe(
            PORTAL_BUS_NAME,
            GLOBALSHORTCUTS_IFACE,
            "Activated",
            PORTAL_OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_activated,
        )

        self._loop = GLib.MainLoop()
        GLib.timeout_add(STOP_POLL_INTERVAL_MS, self._check_stop)
        try:
            self._loop.run()
        finally:
            bus.signal_unsubscribe(sub_id)
            self._close_session(bus, session_handle)

    def _on_activated(
        self, connection, sender_name, object_path, interface_name, signal_name, parameters
    ):
        _session_handle, shortcut_id, _timestamp, _options = parameters.unpack()
        if shortcut_id == SHORTCUT_ID:
            self.activated.emit()

    def _check_stop(self) -> bool:
        if self._stop_requested.is_set():
            self._loop.quit()
            return False
        return True

    def _close_session(self, bus, session_handle):
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        try:
            session_proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                PORTAL_BUS_NAME,
                session_handle,
                SESSION_IFACE,
                None,
            )
            session_proxy.call_sync("Close", None, Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error as exc:
            self.logger.warning("Failed to close GlobalShortcuts session: %s", exc)

    def stop(self):
        self._stop_requested.set()


class GlobalShortcutsPipeline(QObject):
    """Owns the background QThread + GlobalShortcutsWorker pair backing the
    Wayland Alt+1 hotkey (ROADMAP.md Phase 4).

    Usage mirrors WaylandCapturePipeline (capture.py): construct, connect to
    alt1_pressed, call start() once, and call stop() on teardown.
    """

    alt1_pressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.last_error: Optional[str] = None

        self.thread = QThread(self)
        self.worker = GlobalShortcutsWorker()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.thread.finished.connect(self.worker.deleteLater)
        self.worker.activated.connect(self.alt1_pressed)
        self.worker.unavailable.connect(self._on_unavailable)

        self._started = False

    def _on_unavailable(self, message: str):
        self.last_error = message

    def start(self):
        if self._started:
            return
        self._started = True
        self.thread.start()

    def stop(self):
        if not self._started:
            return
        self.worker.stop()
        self.thread.quit()
        self.thread.wait()
        self._started = False
