"""
Phase 0 spike (PyGObject variant): end-to-end integration of the ScreenCast
portal window-picker flow (test_screencast_portal.py) with a live Gst/GstApp
PipeWire capture pipeline (test_pipewire_capture.py), in a single process.

This is the single script that proves the *entire* Phase 2 capture pipeline
works together: portal picker -> fd/node_id -> live Gst pipeline -> numpy
BGRA32 frames -> dumped to disk for manual visual verification. It is the
most convincing artifact for the ROADMAP.md Phase 0 go/no-go writeup, since
test_screencast_portal.py and test_pipewire_capture.py only prove their
halves in isolation (the latter via --self-test's videotestsrc stand-in).

Setup (one-time, on the Linux/KDE Plasma Wayland machine):
    pip install --user PyGObject
    # System requirement: xdg-desktop-portal + xdg-desktop-portal-kde running,
    # plus a GStreamer install with the pipewiresrc element (see
    # test_pipewire_capture.py's docstring for typical package names).

Run:
    python test_screencast_full_pipeline.py

    # First run: a KWin window-picker dialog should appear; pick any window.
    # Re-run: should NOT prompt again (uses the persisted restore_token from
    # test_screencast_portal.py's .screencast_restore_token.json).

To capture more/fewer frames or change the output directory:
    python test_screencast_full_pipeline.py --count 10 --out-dir /tmp/runekit-frames

Reset the persisted restore_token and force the picker to show again:
    python test_screencast_full_pipeline.py --cleanup

Cleanup the spike-only dependency when done:
    pip uninstall PyGObject
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import test_screencast_portal as portal_spike  # noqa: E402
import test_pipewire_capture as capture_spike  # noqa: E402


def run(count: int, out_dir: str):
    portal_spike._ensure_gi()
    capture_spike._ensure_gi()

    Gio = portal_spike.Gio
    GLib = portal_spike.GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    proxy = Gio.DBusProxy.new_sync(
        bus, Gio.DBusProxyFlags.NONE, None,
        portal_spike.PORTAL_BUS_NAME, portal_spike.PORTAL_OBJECT_PATH,
        portal_spike.SCREENCAST_IFACE, None,
    )
    req = portal_spike.PortalRequest(bus)

    print("Calling CreateSession...")
    code, results = req.call(proxy, "CreateSession", ({},))
    if code != 0:
        print(f"RESULT: CreateSession failed with response code {code}")
        print("=> NO-GO: cannot open a ScreenCast session.")
        return
    session_handle = results["session_handle"]
    print(f"Session created: {session_handle}")

    restore_token = portal_spike._load_restore_token()
    select_options = {
        "types": GLib.Variant("u", portal_spike.SOURCE_TYPE_WINDOW),
        "multiple": GLib.Variant("b", False),
        "cursor_mode": GLib.Variant("u", portal_spike.CURSOR_MODE_HIDDEN),
        "persist_mode": GLib.Variant("u", portal_spike.PERSIST_MODE_PERSISTENT),
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

    print("Calling Start (triggers the compositor window picker if no restore_token)...")
    code, results = req.call(proxy, "Start", (session_handle, "", {}), timeout_sec=120)
    if code != 0:
        print(f"RESULT: Start failed/cancelled with response code {code}")
        print("=> NO-GO: user cancelled, or portal rejected the session.")
        return

    streams = results.get("streams", [])
    new_restore_token = results.get("restore_token")
    if not streams:
        print("RESULT: Start succeeded but no streams were returned.")
        print("=> NO-GO: cannot proceed without a node_id.")
        return

    node_id = streams[0][0]
    print(f"Selected stream node_id={node_id}")
    if new_restore_token:
        portal_spike._save_restore_token(new_restore_token)
        print(f"Persisted new restore_token to {portal_spike.TOKEN_FILE}")

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
    print(f"Got PipeWire fd={pipewire_fd} for node_id={node_id}")

    print(f"Building Gst pipeline and pulling {count} frame(s)...")
    pipeline = capture_spike.build_pipeline(pipewire_fd=pipewire_fd, node_id=node_id)
    frames = capture_spike.pull_frames(pipeline, count=count)

    try:
        session_proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None, portal_spike.PORTAL_BUS_NAME,
            session_handle, "org.freedesktop.portal.Session", None,
        )
        session_proxy.call_sync("Close", None, Gio.DBusCallFlags.NONE, -1, None)
        print("Session closed.")
    except GLib.Error as exc:
        print(f"(non-fatal) failed to close session: {exc}")

    if not frames:
        print("RESULT: portal session opened successfully but no frames were received.")
        print("=> NO-GO: pipewiresrc could not consume the portal-provided fd/node_id.")
        return

    os.makedirs(out_dir, exist_ok=True)
    for i, frame in enumerate(frames):
        out_path = os.path.join(out_dir, f"frame_{i:03d}.png")
        capture_spike._np_save_bgra(frame, out_path)
        print(f"Saved {out_path} (shape={frame.shape})")

    print(f"\nRESULT: captured {len(frames)} live frame(s) end-to-end. Frames saved to {out_dir}")
    print("Open the PNGs and confirm they show the picked window's live contents.")
    print("=> GO: full ScreenCast portal (WINDOW picker) + PyGObject/Gst PipeWire capture")
    print("pipeline works together. This validates dropping pywayland/plasmawindowmanagement")
    print("and dbus-next/pydbus in favour of PyGObject alone for Phase 2/3 of ROADMAP.md.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove the persisted restore_token file and exit (no D-Bus/Gst calls made)",
    )
    parser.add_argument("--count", type=int, default=5, help="Number of frames to capture")
    parser.add_argument(
        "--out-dir",
        default=os.path.join(tempfile.gettempdir(), "runekit-spike-frames"),
        help="Directory to write captured PNG frames to",
    )
    args = parser.parse_args()

    if args.cleanup:
        portal_spike.cleanup()
        return

    run(count=args.count, out_dir=args.out_dir)
    print("\nReminder: run with --cleanup to reset the persisted restore_token,")
    print("and `pip uninstall PyGObject` to remove the spike-only dependency.")


if __name__ == "__main__":
    main()
