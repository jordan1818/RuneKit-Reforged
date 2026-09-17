"""
Phase 0 spike: check whether an unprivileged Wayland client can bind
org_kde_plasma_window_management under KWin.

Setup (one-time, on the Linux/KDE Plasma Wayland machine):
    pip install --user pywayland

    # Locate plasma-window-management.xml, e.g.:
    #   Fedora/Bazzite: /usr/share/kde-wayland-protocols/plasma-window-management.xml
    #   Debian/Ubuntu:  /usr/share/plasma-wayland-protocols/plasma-window-management.xml
    #   Arch:           /usr/share/plasma-wayland-protocols/plasma-window-management.xml

    # Generate bindings once, into a local ./protocols package:
    python -m pywayland.scanner -i <path-to-xml> -o ./protocols

Run (from the same directory as ./protocols):
    python test_plasma_window_management.py

Cleanup after you're done with the spike:
    python test_plasma_window_management.py --cleanup
    pip uninstall pywayland
"""
import argparse
import shutil
import time
from pathlib import Path

PROTOCOLS_DIR = Path(__file__).parent / "protocols"

WINDOW_MANAGEMENT_INTERFACE = "org_kde_plasma_window_management"

seen_globals = []
management = None


def handle_new_window(management_proxy, window_id):
    print(f"window event received (deprecated internal id={window_id})")


def handle_global(registry, id_, interface, version):
    seen_globals.append(interface)
    global management
    if interface == WINDOW_MANAGEMENT_INTERFACE:
        print(f"FOUND {interface} (name={id_}, version={version}) -- attempting bind...")
        try:
            from protocols.plasma_window_management import OrgKdePlasmaWindowManagement

            management = registry.bind(id_, OrgKdePlasmaWindowManagement, min(version, 17))
            management.dispatcher["window"] = handle_new_window
            print("BIND SUCCEEDED")
        except Exception as exc:
            print(f"BIND FAILED: {exc}")


def cleanup_generated_bindings():
    if PROTOCOLS_DIR.exists():
        shutil.rmtree(PROTOCOLS_DIR)
        print(f"Removed generated bindings at {PROTOCOLS_DIR}")
    else:
        print(f"No generated bindings found at {PROTOCOLS_DIR}, nothing to clean up")


def run():
    from pywayland.client import Display

    display = None
    try:
        display = Display()
        display.connect()
        print("Connected to Wayland display")

        registry = display.get_registry()
        registry.dispatcher["global"] = handle_global
        display.roundtrip()  # collect the initial burst of globals

        print(f"\nSaw {len(seen_globals)} globals total:")
        for name in sorted(set(seen_globals)):
            print(f"  - {name}")

        print()
        if WINDOW_MANAGEMENT_INTERFACE not in seen_globals:
            print(f"RESULT: {WINDOW_MANAGEMENT_INTERFACE} was NOT advertised to this client.")
            print("This matches KWin's restrictedInterfaces allowlist behavior (silent, no error).")
            print("=> NO-GO: design the manual window-picker fallback for Phase 3.")
        elif management is not None:
            print(f"RESULT: {WINDOW_MANAGEMENT_INTERFACE} was advertised and bind succeeded.")
            print("Listening for window events for 5 seconds (open/focus a window now)...")
            end = time.monotonic() + 5
            while time.monotonic() < end:
                display.dispatch(block=False)
                time.sleep(0.1)
            print("=> GO: plasmawindowmanagement can be used for window discovery in Phase 3.")
        else:
            print(f"RESULT: {WINDOW_MANAGEMENT_INTERFACE} was advertised but bind FAILED.")
            print("=> NO-GO: design the manual window-picker fallback for Phase 3.")
    finally:
        if management is not None:
            try:
                management.destroy()
            except Exception:
                pass
        if display is not None:
            display.disconnect()
            print("Disconnected from Wayland display")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove the generated ./protocols bindings directory and exit (no Wayland connection made)",
    )
    args = parser.parse_args()

    if args.cleanup:
        cleanup_generated_bindings()
        return

    run()
    print("\nReminder: run with --cleanup to remove generated bindings when done,")
    print("and `pip uninstall pywayland` to remove the spike-only dependency.")


if __name__ == "__main__":
    main()
