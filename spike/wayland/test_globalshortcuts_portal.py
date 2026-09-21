"""
Phase 0 spike (PyGObject variant): validate the
org.freedesktop.portal.GlobalShortcuts D-Bus session flow (CreateSession ->
BindShortcuts -> Activated signal) using PyGObject's Gio (GDBus) bindings.

This validates ROADMAP.md Phase 4 (Global hotkey / Alt+1): replacing the raw
X11 KeyPress-event approach in runekit/game/x11/instance.py with a portal
session that emits GameInstance.alt1_pressed on Wayland, driven entirely by
PyGObject's Gio rather than a separate dbus-next/pydbus dependency.

Requires xdg-desktop-portal-kde >= Plasma 6.1 (per ROADMAP.md's feasibility
notes) -- on older Plasma this portal will simply not be present.

Setup (one-time, on the Linux/KDE Plasma Wayland machine):
    pip install --user PyGObject

Run:
    python test_globalshortcuts_portal.py

    # A KDE "grant shortcuts" dialog should appear once; approve it.
    # Then press Alt+1 (or whichever key you bound) and watch for the
    # "Activated" printout below. Ctrl+C to stop.

Cleanup the spike-only dependency when done:
    pip uninstall PyGObject
"""
import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_screencast_portal as portal_spike  # noqa: E402 - reuse PortalRequest/_ensure_gi

PORTAL_BUS_NAME = portal_spike.PORTAL_BUS_NAME
PORTAL_OBJECT_PATH = portal_spike.PORTAL_OBJECT_PATH
GLOBALSHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"

SHORTCUT_ID = "runekit-spike-alt1"

Gio = None
GLib = None


def _ensure_gi():
    global Gio, GLib
    portal_spike._ensure_gi()
    Gio = portal_spike.Gio
    GLib = portal_spike.GLib


class GlobalShortcutsRequest(portal_spike.PortalRequest):
    """Same Request-object waiting logic as PortalRequest, but with its own
    method-signature table since GlobalShortcuts' methods differ from
    ScreenCast's."""

    _SIGNATURES = {
        "CreateSession": "(a{sv})",
        "BindShortcuts": "(oa(sa{sv})sa{sv})",
    }

    def call(self, proxy, method_name, arg_tuple, timeout_sec=60):
        # Reimplemented (rather than calling super().call) because the parent
        # looks up signatures from test_screencast_portal._signature_for,
        # which only knows about ScreenCast's methods.
        token = "runekit_spike_" + secrets.token_hex(6)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        *head, options = arg_tuple
        options = dict(options)
        options["handle_token"] = GLib.Variant("s", token)

        # CreateSession also requires session_handle_token: without it, some
        # backends (observed on xdg-desktop-portal-kde) fail to construct the
        # Session object and the whole D-Bus call dies mid-flight, which the
        # client sees as "GDBus.Error:org.freedesktop.DBus.Error.NoReply:
        # Remote peer disconnected" rather than a clean error Response.
        if method_name == "CreateSession":
            options["session_handle_token"] = GLib.Variant(
                "s", "runekit_spike_session_" + secrets.token_hex(6)
            )

        arg_tuple = tuple(head) + (options,)

        self._sub_id = self.bus.signal_subscribe(
            portal_spike.PORTAL_BUS_NAME,
            portal_spike.REQUEST_IFACE,
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
                GLib.Variant(self._SIGNATURES[method_name], arg_tuple),
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
            PORTAL_BUS_NAME, PORTAL_OBJECT_PATH, GLOBALSHORTCUTS_IFACE, None,
        )
    except GLib.Error as exc:
        print(f"RESULT: could not create proxy for {GLOBALSHORTCUTS_IFACE}: {exc}")
        print("=> NO-GO: portal interface unavailable (needs xdg-desktop-portal-kde >= Plasma 6.1).")
        return

    req = GlobalShortcutsRequest(bus)

    print("Calling CreateSession...")
    try:
        code, results = req.call(proxy, "CreateSession", ({},))
    except GLib.Error as exc:
        print(f"RESULT: CreateSession call failed: {exc}")
        print("=> NO-GO: GlobalShortcuts portal not implemented by this backend.")
        return

    if code != 0:
        print(f"RESULT: CreateSession failed with response code {code}")
        print("=> NO-GO: cannot open a GlobalShortcuts session.")
        return

    session_handle = results["session_handle"]
    print(f"Session created: {session_handle}")

    shortcuts = [
        (SHORTCUT_ID, {"description": GLib.Variant("s", "RuneKit Reforged spike: Alt+1")}),
    ]
    print(f"Calling BindShortcuts (requesting id={SHORTCUT_ID!r})...")
    print("A KDE dialog may appear asking you to assign a key combo -- pick Alt+1 if possible.")
    code, results = req.call(
        proxy, "BindShortcuts", (session_handle, shortcuts, "", {}), timeout_sec=60
    )
    if code != 0:
        print(f"RESULT: BindShortcuts failed/cancelled with response code {code}")
        print("=> NO-GO: user cancelled, or portal rejected the shortcut binding.")
        return

    bound = results.get("shortcuts", [])
    print(f"RESULT: BindShortcuts succeeded. Bound shortcut(s): {bound}")

    activation_count = 0

    def _on_activated(connection, sender_name, object_path, interface_name, signal_name, parameters):
        nonlocal activation_count
        session_handle_arg, shortcut_id, timestamp, options = parameters.unpack()
        activation_count += 1
        print(f"Activated: shortcut_id={shortcut_id!r} timestamp={timestamp} (count={activation_count})")

    sub_id = bus.signal_subscribe(
        PORTAL_BUS_NAME, GLOBALSHORTCUTS_IFACE, "Activated", PORTAL_OBJECT_PATH,
        None, Gio.DBusSignalFlags.NONE, _on_activated,
    )

    print("\nListening for 'Activated' signals for 20 seconds. Press your bound key now...")
    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(20, lambda: (loop.quit(), False)[1])
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        bus.signal_unsubscribe(sub_id)

    if activation_count > 0:
        print(f"\nRESULT: received {activation_count} Activated signal(s).")
        print("=> GO: GlobalShortcuts portal works via PyGObject/GDBus for the Alt+1 hotkey (Phase 4).")
    else:
        print("\nRESULT: no Activated signal received in 20 seconds.")
        print("=> INCONCLUSIVE: either the key wasn't pressed, or delivery is broken -- retry and")
        print("   make sure you press the exact combo shown in the KDE shortcut-assignment dialog.")

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
    parser.parse_args()
    run()
    print("\nReminder: `pip uninstall PyGObject` to remove the spike-only dependency when done.")


if __name__ == "__main__":
    main()
