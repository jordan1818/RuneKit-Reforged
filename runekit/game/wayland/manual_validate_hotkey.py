"""
Manual validation script for ROADMAP.md Phase 4 (Global hotkey / Alt+1).

Like manual_validate_capture.py (Phase 2) and manual_validate_discovery.py
(Phase 3), this exercises the actual production classes
(runekit/game/wayland/globalshortcuts.py) rather than throwaway spike code,
since this project has no automated test suite (see CLAUDE.md/ROADMAP.md).

Setup (one-time, on the Bazzite/KDE Plasma Wayland machine):
    pip install --user PyGObject
    # System requirement: xdg-desktop-portal + xdg-desktop-portal-kde
    # (>= Plasma 6.1) running.

Run:
    python -m runekit.game.wayland.manual_validate_hotkey

    # A KDE "grant shortcuts" dialog should appear once, synchronously
    # (before the "Press Alt+1..." message is printed); approve it and
    # accept (or set) the Alt+1 binding. Then press Alt+1 a few times and
    # watch for the "alt1_pressed" printout below. Ctrl+C to stop.

What to check after running:
    1. The KDE shortcut-grant dialog appeared once, synchronously (before
       "Bound successfully" is printed), and the shortcut is listed under
       KDE's System Settings > Shortcuts > Global Shortcuts afterwards.
    2. Each Alt+1 press prints exactly one "alt1_pressed" line (no missed
       or duplicated activations) -- this confirms Activated signal
       delivery works via Qt's own main-thread event loop with no
       dedicated GLib.MainLoop()/thread (see GlobalShortcutsSession's
       docstring for why an earlier revision needed one and didn't work).
    3. Ctrl+C stops the script cleanly (no hung thread / traceback).
    4. On a pre-Plasma-6.1 system (or with xdg-desktop-portal-kde missing
       the GlobalShortcuts interface), the script should print a warning
       and exit gracefully instead of crashing/hanging.
Report back GO/NO-GO so ROADMAP.md Phase 4 can be marked complete or fixed.
"""
import sys

from PySide6.QtCore import QCoreApplication

from .globalshortcuts import GlobalShortcutsPortalError, GlobalShortcutsSession


def main():
    app = QCoreApplication(sys.argv)

    session = GlobalShortcutsSession()

    count = 0

    def _on_activated():
        nonlocal count
        count += 1
        print(f"alt1_pressed (count={count})")

    session.activated.connect(_on_activated)

    print("Opening GlobalShortcutsSession (production code path)...")
    print("A KDE shortcut-grant dialog may appear now -- approve it if shown.")
    try:
        session.open()
    except GlobalShortcutsPortalError as exc:
        print(f"\nRESULT: GlobalShortcuts portal unavailable: {exc}")
        print("=> NO-GO on this machine (requires xdg-desktop-portal-kde >= Plasma 6.1).")
        return

    print("\nBound successfully. Press Alt+1 now (Ctrl+C to stop).\n")

    try:
        app.exec()
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\nRESULT: received {count} alt1_pressed signal(s) this run.")
        print("=> Report back GO/NO-GO so ROADMAP.md Phase 4 can be marked complete.")
        session.close()


if __name__ == "__main__":
    main()
