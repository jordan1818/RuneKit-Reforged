"""
Phase 0 spike (PyGObject variant): validate the org.freedesktop.portal.ScreenCast
D-Bus session flow (CreateSession -> SelectSources -> Start -> OpenPipeWireRemote)
using PyGObject's Gio (GDBus) bindings.

This replaces two things from the original ROADMAP.md Phase 0/2/3 plan:
  - The plasmawindowmanagement/pywayland window-discovery spike: here we use
    the ScreenCast portal's own WINDOW-source picker (SelectSources types=WINDOW)
    as the *only* window-discovery mechanism, so no pywayland dependency and no
    KWin restrictedInterfaces allowlist concern.
  - The dbus-next/pydbus choice for Phase 2's D-Bus calls: here we use
    PyGObject's Gio.DBusProxy/Gio.DBusConnection (GDBus) exclusively.

What this validates:
  - The WINDOW source type picker actually shows a per-window (not just
    per-monitor) choice under KWin's xdg-desktop-portal-kde backend.
  - restore_token + persist_mode avoids re-prompting the user on subsequent runs.
  - The whole flow works end-to-end through PyGObject's GDBus bindings alone.

Setup (one-time, on the Linux/KDE Plasma Wayland machine):
    pip install --user PyGObject

    # System requirement: xdg-desktop-portal + xdg-desktop-portal-kde running
    # (already required by any KDE Plasma Wayland session).

Run:
    python test_screencast_portal.py

    # First run: a KWin window-picker dialog should appear; pick any window.
    # Re-run: should NOT prompt again (uses the persisted restore_token).

Reset the persisted restore_token and force the picker to show again:
    python test_screencast_portal.py --cleanup

Cleanup the spike-only dependency when done:
    pip uninstall PyGObject
"""
import argparse
import json
import secrets
from pathlib import Path

TOKEN_FILE = Path(__file__).parent / ".screencast_restore_token.json"

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"

SOURCE_TYPE_WINDOW = 2  # bitmask: MONITOR=1, WINDOW=2, VIRTUAL=4
CURSOR_MODE_HIDDEN = 1
PERSIST_MODE_PERSISTENT = 2  # 0=none, 1=until app closes, 2=until explicitly revoked

Gio = None
GLib = None


def _ensure_gi():
    global Gio, GLib
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import Gio as _Gio, GLib as _GLib

    Gio = _Gio
    GLib = _GLib


def _load_restore_token():
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text()).get("restore_token")
        except Exception:
            return None
    return None


def _save_restore_token(token):
    if token:
        TOKEN_FILE.write_text(json.dumps({"restore_token": token}))


def cleanup():
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        print(f"Removed persisted restore token at {TOKEN_FILE}")
    else:
        print(f"No persisted restore token found at {TOKEN_FILE}, nothing to clean up")


def _signature_for(method_name):
    return {
        "CreateSession": "(a{sv})",
        "SelectSources": "(oa{sv})",
        "Start": "(osa{sv})",
    }[method_name]


class PortalRequest:
    """Waits for the Response signal on an org.freedesktop.portal.Request object."""

    def __init__(self, bus):
        self.bus = bus
        self.loop = GLib.MainLoop()
        self.response_code = None
        self.results = None
        self._sub_id = None

    def _on_response(self, connection, sender_name, object_path, interface_name, signal_name, parameters):
        self.response_code, self.results = parameters.unpack()
        self.loop.quit()

    def call(self, proxy, method_name, arg_tuple, timeout_sec=60):
        token = "runekit_spike_" + secrets.token_hex(6)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        # Inject handle_token into the trailing options dict (a{sv}) so we can
        # predict the request object path ahead of the call.
        *head, options = arg_tuple
        options = dict(options)
        options["handle_token"] = GLib.Variant("s", token)
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
            print(f"TIMEOUT waiting for {method_name} response after {timeout_sec}s")
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
                print(f"NOTE: request path mismatch (expected {request_path}, got {returned_path})")

            self.loop.run()
        finally:
            GLib.source_remove(timeout_id)
            self.bus.signal_unsubscribe(self._sub_id)

        return self.response_code, self.results


def run():
    _ensure_gi()

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    try:
        proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            PORTAL_BUS_NAME, PORTAL_OBJECT_PATH, SCREENCAST_IFACE, None,
        )
    except GLib.Error as exc:
        print(f"RESULT: could not create proxy for {SCREENCAST_IFACE}: {exc}")
        print("=> NO-GO: portal interface unavailable (check xdg-desktop-portal-kde is running).")
        return

    req = PortalRequest(bus)

    print("Calling CreateSession...")
    try:
        code, results = req.call(proxy, "CreateSession", ({},))
    except GLib.Error as exc:
        print(f"RESULT: CreateSession call failed: {exc}")
        print("=> NO-GO: ScreenCast portal not implemented by this backend.")
        return

    if code != 0:
        print(f"RESULT: CreateSession failed with response code {code}")
        print("=> NO-GO: cannot even open a ScreenCast session.")
        return

    session_handle = results["session_handle"]
    print(f"Session created: {session_handle}")

    restore_token = _load_restore_token()
    select_options = {
        "types": GLib.Variant("u", SOURCE_TYPE_WINDOW),
        "multiple": GLib.Variant("b", False),
        "cursor_mode": GLib.Variant("u", CURSOR_MODE_HIDDEN),
        "persist_mode": GLib.Variant("u", PERSIST_MODE_PERSISTENT),
    }
    if restore_token:
        select_options["restore_token"] = GLib.Variant("s", restore_token)
        print("Using persisted restore_token (picker should NOT prompt)...")
    else:
        print("No persisted restore_token -- picker SHOULD prompt now...")

    print("Calling SelectSources (WINDOW source type)...")
    code, results = req.call(proxy, "SelectSources", (session_handle, select_options))
    if code != 0:
        print(f"RESULT: SelectSources failed with response code {code}")
        print("=> NO-GO: WINDOW source type may not be supported by this portal backend.")
        return
    print("SelectSources succeeded.")

    print("Calling Start (triggers the compositor window picker if no restore_token)...")
    code, results = req.call(proxy, "Start", (session_handle, "", {}), timeout_sec=120)
    if code != 0:
        print(f"RESULT: Start failed/cancelled with response code {code}")
        print("=> NO-GO: user cancelled, or portal rejected the session.")
        return

    streams = results.get("streams", [])
    new_restore_token = results.get("restore_token")
    print(f"RESULT: Start succeeded. {len(streams)} stream(s) selected:")
    for node_id, props in streams:
        print(f"  - node_id={node_id} props={props}")

    if new_restore_token:
        _save_restore_token(new_restore_token)
        print(f"Persisted new restore_token to {TOKEN_FILE}")

    print("Calling OpenPipeWireRemote...")
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
    print(f"RESULT: got PipeWire fd={pipewire_fd} for the node_id(s) printed above.")
    print("=> GO: ScreenCast WINDOW-picker + OpenPipeWireRemote flow works via PyGObject/GDBus.")
    print("This confirms no pywayland/plasmawindowmanagement dependency is needed for")
    print("window discovery (Phase 3 can rely on this picker + caching the selection).")
    print("See test_screencast_full_pipeline.py to feed this fd/node_id into a live Gst pipeline.")

    try:
        session_proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None, PORTAL_BUS_NAME, session_handle,
            "org.freedesktop.portal.Session", None,
        )
        session_proxy.call_sync("Close", None, Gio.DBusCallFlags.NONE, -1, None)
        print("Session closed.")
    except GLib.Error as exc:
        print(f"(non-fatal) failed to close session: {exc}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove the persisted restore_token file and exit (no D-Bus calls made)",
    )
    args = parser.parse_args()

    if args.cleanup:
        cleanup()
        return

    run()
    print("\nReminder: run with --cleanup to reset the persisted restore_token,")
    print("and `pip uninstall PyGObject` to remove the spike-only dependency.")


if __name__ == "__main__":
    main()
