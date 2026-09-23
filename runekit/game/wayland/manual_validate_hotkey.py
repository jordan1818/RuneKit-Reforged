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

    # A KDE "grant shortcuts" dialog should appear once; approve it and
    # accept (or set) the Alt+1 binding. Then press Alt+1 a few times and
    # watch for the "alt1_pressed" printout below. Ctrl+C to stop.

What to check after running:
    1. The KDE shortcut-grant dialog appeared once, and the shortcut is
       listed under KDE's System Settings > Shortcuts > Global Shortcuts
       afterwards.
    2. Each Alt+1 press prints exactly one "alt1_pressed" line (no missed
       or duplicated activations).
    3. Ctrl+C stops the script cleanly (no hung thread / traceback).
    4. On a pre-Plasma-6.1 system (or with xdg-desktop-portal-kde missing
       the GlobalShortcuts interface), the script should print a warning
       and exit gracefully instead of crashing.
Report back GO/NO-GO so ROADMAP.md Phase 4 can be marked complete or fixed.
"""
import sys

from PySide6.QtCore import QCoreApplication, QTimer

from .globalshortcuts import GlobalShortcutsPipeline


def main():
    app = QCoreApplication(sys.argv)

    pipeline = GlobalShortcutsPipeline()

    count = 0

    def _on_activated():
        nonlocal count
        count += 1
        print(f"alt1_pressed (count={count})")

    pipeline.alt1_pressed.connect(_on_activated)

    print("Starting GlobalShortcutsPipeline (production code path)...")
    print("Approve the KDE shortcut-grant dialog if shown, then press Alt+1.")
    print("Press Ctrl+C to stop.\n")
    pipeline.start()

    # Poll for the worker reporting the portal as unavailable (e.g. pre-
    # Plasma 6.1), so this script can exit informatively instead of just
    # hanging with no visible activity.
    def _check_unavailable():
        if pipeline.last_error:
            print(f"\nRESULT: GlobalShortcuts portal unavailable: {pipeline.last_error}")
            print("=> NO-GO on this machine (requires xdg-desktop-portal-kde >= Plasma 6.1).")
            app.quit()

    timer = QTimer()
    timer.timeout.connect(_check_unavailable)
    timer.start(1000)

    try:
        app.exec()
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\nRESULT: received {count} alt1_pressed signal(s) this run.")
        print("=> Report back GO/NO-GO so ROADMAP.md Phase 4 can be marked complete.")
        pipeline.stop()


if __name__ == "__main__":
    main()
