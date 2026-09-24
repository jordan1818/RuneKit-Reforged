import logging

from PySide6.QtCore import QObject, Signal

from .portal import PORTAL_BUS_NAME, PORTAL_OBJECT_PATH, SESSION_IFACE, PortalRequest

logger = logging.getLogger(__name__)

GLOBALSHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
SHORTCUT_ID = "runekit-alt1"


class GlobalShortcutsPortalError(Exception):
    """Raised when the GlobalShortcuts portal session cannot be established."""


class GlobalShortcutsSession(QObject):
    """Owns a single org.freedesktop.portal.GlobalShortcuts session backing
    the Wayland Alt+1 hotkey (ROADMAP.md Phase 4).

    Fix note (found during real-machine validation): an earlier revision
    of this class ran the whole CreateSession -> BindShortcuts -> Activated
    flow on a background QThread with its own GLib.MainLoop(), mirroring
    WaylandCaptureWorker's (capture.py) QThread pattern. That does NOT work
    for this portal: GLib.MainLoop() with no explicit context argument
    always binds to the *global default* GMainContext, never to a
    thread-default context set via push_thread_default() (PyGObject/GLib
    docs are explicit that push_thread_default() "does not affect... the
    context used by functions like g_idle_add()", and g_timeout_add()/
    g_main_loop_new(NULL) follow the same "NULL means global default"
    rule). Qt's own main thread is *already* continuously iterating that
    same global default context once app.exec() is running (PySide6's
    glib event-dispatcher integration on Linux) -- so a second
    GLib.MainLoop() on a second thread, also trying to run that global
    context, is redundant and racy by construction. Meanwhile
    Gio.DBusConnection.signal_subscribe()'s callback dispatch *does*
    follow the thread-default context of whichever thread subscribed --
    so pushing a private context on the worker thread just made the
    Activated/Response subscriptions land in a context nothing was ever
    iterating, while self.loop.run() (inside the unmodified PortalRequest)
    kept waiting on the global default context instead. Net effect:
    CreateSession's Response was never observed, so it timed out after
    60s and BindShortcuts (and its KDE grant dialog) never ran.

    The fix: don't use a second thread/context at all. Run CreateSession
    and BindShortcuts synchronously on the caller's (main) thread, exactly
    like ScreenCastSession.open() (portal.py) already does successfully.
    For the ongoing Activated listening, no dedicated loop is needed
    either: the signal_subscribe() callback below is registered from the
    main thread, so it lands on the global default context that Qt's own
    event loop is already driving for the lifetime of the app -- the
    Activated signal is delivered "for free" as part of normal Qt event
    processing, with zero extra threads or main loops.

    Requires xdg-desktop-portal-kde >= Plasma 6.1. If the portal interface
    or the BindShortcuts call is unavailable/rejected, open() raises
    GlobalShortcutsPortalError so the caller can log a warning and
    continue without the hotkey, rather than crashing the rest of the
    Wayland backend -- see ROADMAP.md Phase 4.
    """

    activated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._bus = None
        self._session_handle = None
        self._sub_id = None

    def open(self):
        """Run CreateSession -> BindShortcuts synchronously (may show KDE's
        shortcut-grant dialog) and start listening for Activated.

        Raises GlobalShortcutsPortalError if the portal is unavailable or
        the user declines the grant dialog.
        """
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
            raise GlobalShortcutsPortalError(
                f"GlobalShortcuts portal unavailable ({exc}); requires "
                "xdg-desktop-portal-kde >= Plasma 6.1"
            ) from exc

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
        except GLib.Error as exc:
            if session_handle is not None:
                self._close_session(bus, session_handle, Gio, GLib)
            raise GlobalShortcutsPortalError(str(exc)) from exc
        except GlobalShortcutsPortalError:
            if session_handle is not None:
                self._close_session(bus, session_handle, Gio, GLib)
            raise

        self.logger.info("Wayland Alt+1 hotkey bound via GlobalShortcuts portal")

        # Registered from the main thread, so this callback is dispatched
        # on the global default GMainContext -- the same one Qt's own
        # event loop (app.exec()) is already driving for as long as the
        # app runs. No dedicated GLib.MainLoop()/thread needed; see the
        # class docstring's fix note above.
        self._sub_id = bus.signal_subscribe(
            PORTAL_BUS_NAME,
            GLOBALSHORTCUTS_IFACE,
            "Activated",
            PORTAL_OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_activated,
        )
        self._bus = bus
        self._session_handle = session_handle

    def _on_activated(
        self, connection, sender_name, object_path, interface_name, signal_name, parameters
    ):
        _session_handle, shortcut_id, _timestamp, _options = parameters.unpack()
        if shortcut_id == SHORTCUT_ID:
            self.activated.emit()

    def _close_session(self, bus, session_handle, Gio, GLib):
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

    def close(self):
        """Close the session, if open. Safe to call multiple times."""
        if self._bus is None or self._session_handle is None:
            return

        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        if self._sub_id is not None:
            self._bus.signal_unsubscribe(self._sub_id)
            self._sub_id = None

        self._close_session(self._bus, self._session_handle, Gio, GLib)
        self._bus = None
        self._session_handle = None
