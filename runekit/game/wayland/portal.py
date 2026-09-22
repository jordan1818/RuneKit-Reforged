import logging
import secrets
from typing import Any, Dict, Optional, Tuple

from PySide6.QtCore import QSettings

logger = logging.getLogger(__name__)

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

SOURCE_TYPE_WINDOW = 2  # bitmask: MONITOR=1, WINDOW=2, VIRTUAL=4
CURSOR_MODE_HIDDEN = 1
PERSIST_MODE_PERSISTENT = 2  # 0=none, 1=until app closes, 2=until explicitly revoked

# Mirrors the QSettings key style used by runekit/host/settings.py
# (e.g. "settings/tooltip", "settings/styledBorder").
RESTORE_TOKEN_SETTINGS_KEY = "wayland/screencastRestoreToken"


def load_restore_token() -> Optional[str]:
    """Load the persisted ScreenCast restore_token, if any.

    Uses QSettings (not a JSON file, unlike the Phase 0 spike) so it's
    stored alongside RuneKit's other settings -- see ROADMAP.md Phase 2.
    """
    settings = QSettings()
    value = settings.value(RESTORE_TOKEN_SETTINGS_KEY, None)
    return value or None


def save_restore_token(token: Optional[str]):
    if not token:
        return

    settings = QSettings()
    settings.setValue(RESTORE_TOKEN_SETTINGS_KEY, token)


def clear_restore_token():
    """Discard the persisted restore_token, forcing the next open() to show
    the window picker again. Intended to back a future "re-pick window" UI
    action (see ROADMAP.md Phase 3)."""
    settings = QSettings()
    settings.remove(RESTORE_TOKEN_SETTINGS_KEY)


def _signature_for(method_name: str) -> str:
    return {
        "CreateSession": "(a{sv})",
        "SelectSources": "(oa{sv})",
        "Start": "(osa{sv})",
    }[method_name]


class ScreenCastPortalError(Exception):
    """Raised when the ScreenCast portal session cannot be established."""


class PortalRequest:
    """Waits for the Response signal on an org.freedesktop.portal.Request object.

    Promoted from spike/wayland/test_screencast_portal.py's PortalRequest
    (validated GO against xdg-desktop-portal-kde), with the print()-based
    diagnostics replaced by exceptions/logging for production use.
    """

    def __init__(self, bus, glib, gio):
        self.bus = bus
        self._GLib = glib
        self._Gio = gio
        self.loop = glib.MainLoop()
        self.response_code = None
        self.results = None
        self._sub_id = None

    def _on_response(
        self, connection, sender_name, object_path, interface_name, signal_name, parameters
    ):
        self.response_code, self.results = parameters.unpack()
        self.loop.quit()

    def call(self, proxy, method_name: str, arg_tuple: Tuple, timeout_sec: int = 60):
        GLib = self._GLib
        Gio = self._Gio

        token = "runekit_" + secrets.token_hex(6)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        # Inject handle_token into the trailing options dict (a{sv}) so we
        # can predict the request object path ahead of the call.
        *head, options = arg_tuple
        options = dict(options)
        options["handle_token"] = GLib.Variant("s", token)

        # CreateSession also requires session_handle_token: without it,
        # xdg-desktop-portal-kde fails to construct the Session object and
        # the whole call dies mid-flight ("GDBus.Error:...NoReply: Remote
        # peer disconnected") -- see ROADMAP.md Phase 0 fix note.
        if method_name == "CreateSession":
            options["session_handle_token"] = GLib.Variant(
                "s", "runekit_session_" + secrets.token_hex(6)
            )

        arg_tuple = tuple(head) + (options,)

        self._sub_id = self.bus.signal_subscribe(
            PORTAL_BUS_NAME,
            REQUEST_IFACE,
            "Response",
            request_path,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_response,
        )

        def _on_timeout():
            logger.warning(
                "Timed out waiting for %s response after %ds", method_name, timeout_sec
            )
            self.loop.quit()
            return False

        timeout_id = GLib.timeout_add_seconds(timeout_sec, _on_timeout)
        try:
            reply = proxy.call_sync(
                method_name,
                GLib.Variant(_signature_for(method_name), arg_tuple),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            returned_path = reply.unpack()[0]
            if returned_path != request_path:
                logger.debug(
                    "Request path mismatch (expected %s, got %s)",
                    request_path,
                    returned_path,
                )

            self.loop.run()
        finally:
            GLib.source_remove(timeout_id)
            self.bus.signal_unsubscribe(self._sub_id)

        return self.response_code, self.results


class ScreenCastSession:
    """Owns a single org.freedesktop.portal.ScreenCast session.

    Implements the CreateSession -> SelectSources(WINDOW) -> Start ->
    OpenPipeWireRemote flow using PyGObject's Gio (GDBus) exclusively, per
    ROADMAP.md Phase 2 / Feasibility notes. Promoted from
    spike/wayland/test_screencast_portal.py, which validated this flow
    end-to-end (GO) against xdg-desktop-portal-kde.

    `import gi` is deferred to open()/close() so importing this module (and
    runekit.game.wayland generally) does not require PyGObject to be
    installed on platforms/sessions that never construct a
    WaylandGameManager.
    """

    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._bus = None
        self._session_handle: Optional[str] = None
        self.node_id: Optional[int] = None
        self.pipewire_fd: Optional[int] = None

    def open(self, use_restore_token: bool = True) -> Tuple[int, int]:
        """Run the portal flow, returning (pipewire_fd, node_id).

        On first run (no persisted restore_token) this shows KWin's window
        picker dialog. Subsequent calls reuse the persisted restore_token
        (see save_restore_token/load_restore_token) to avoid re-prompting.
        """
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        try:
            proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                PORTAL_BUS_NAME,
                PORTAL_OBJECT_PATH,
                SCREENCAST_IFACE,
                None,
            )
        except GLib.Error as exc:
            raise ScreenCastPortalError(
                f"Could not create proxy for {SCREENCAST_IFACE}: {exc}"
            ) from exc

        req = PortalRequest(bus, GLib, Gio)

        self.logger.debug("Calling CreateSession")
        code, results = req.call(proxy, "CreateSession", ({},))
        if code != 0:
            raise ScreenCastPortalError(f"CreateSession failed with response code {code}")

        session_handle = results["session_handle"]
        self._session_handle = session_handle
        self.logger.debug("ScreenCast session created: %s", session_handle)

        restore_token = load_restore_token() if use_restore_token else None
        select_options: Dict[str, Any] = {
            "types": GLib.Variant("u", SOURCE_TYPE_WINDOW),
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", CURSOR_MODE_HIDDEN),
            "persist_mode": GLib.Variant("u", PERSIST_MODE_PERSISTENT),
        }
        if restore_token:
            select_options["restore_token"] = GLib.Variant("s", restore_token)
            self.logger.debug("Using persisted restore_token, picker should not prompt")
        else:
            self.logger.info("No persisted restore_token, window picker will prompt")

        code, results = req.call(proxy, "SelectSources", (session_handle, select_options))
        if code != 0:
            raise ScreenCastPortalError(f"SelectSources failed with response code {code}")

        code, results = req.call(proxy, "Start", (session_handle, "", {}), timeout_sec=120)
        if code != 0:
            raise ScreenCastPortalError(
                f"Start failed/cancelled with response code {code} "
                "(user may have cancelled the window picker)"
            )

        streams = results.get("streams", [])
        if not streams:
            raise ScreenCastPortalError("Start succeeded but no streams were returned")

        new_restore_token = results.get("restore_token")
        if new_restore_token:
            save_restore_token(new_restore_token)
            self.logger.debug("Persisted new restore_token")

        node_id = streams[0][0]

        self.logger.debug("Calling OpenPipeWireRemote")
        reply, fd_list = proxy.call_with_unix_fd_list_sync(
            "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (session_handle, {})),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            None,
        )
        fd_index = reply.unpack()[0]
        pipewire_fd = fd_list.get(fd_index)

        self._bus = bus
        self.node_id = node_id
        self.pipewire_fd = pipewire_fd

        return pipewire_fd, node_id

    def close(self):
        """Close the portal session, if open. Safe to call multiple times."""
        if self._session_handle is None or self._bus is None:
            return

        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        try:
            session_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.NONE,
                None,
                PORTAL_BUS_NAME,
                self._session_handle,
                SESSION_IFACE,
                None,
            )
            session_proxy.call_sync("Close", None, Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error as exc:
            self.logger.warning("Failed to close ScreenCast session: %s", exc)
        finally:
            self._session_handle = None
            self._bus = None
