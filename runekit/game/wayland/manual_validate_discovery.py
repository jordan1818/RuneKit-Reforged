"""
Manual validation script for ROADMAP.md Phase 3 (Window discovery,
geometry, focus tracking).

Like manual_validate_capture.py (Phase 2), this exercises the actual
production classes (runekit/game/wayland/manager.py, instance.py,
portal.py) rather than throwaway spike code, since this project has no
automated test suite (see CLAUDE.md/ROADMAP.md).

Setup (one-time, on the Bazzite/KDE Plasma Wayland machine):
    pip install --user PyGObject
    # System requirement: xdg-desktop-portal + xdg-desktop-portal-kde running.

Run:
    python -m runekit.game.wayland.manual_validate_discovery

    # First run: a KWin window-picker dialog should appear; pick a window.
    # get_instances()/get_active_instance() are exercised, and the picked
    # window's (static) position/scaling/focus are printed.

Re-run (should NOT prompt again, reusing the persisted restore_token):
    python -m runekit.game.wayland.manual_validate_discovery

Force the picker to run again for a different window:
    python -m runekit.game.wayland.manual_validate_discovery --repick

What to check after running:
    1. The window picker appeared on the first run only (not on plain re-runs).
    2. get_position() reports a sane (width, height) matching the picked
       window (x/y will always be 0,0 -- this is expected, see ROADMAP.md
       Phase 3: WINDOW-type ScreenCast streams don't expose an on-screen
       position).
    3. get_scaling() reports a plausible DPI scale factor.
    4. is_focused() prints True while this script's (invisible, since it's
       a QGuiApplication with no window) process is the active application,
       and toggles if you can trigger applicationStateChanged another way;
       this is a best-effort approximation, not real per-window focus.
    5. --repick discards the persisted restore_token and instance, and the
       next run (without --repick) shows the picker again.
Report back GO/NO-GO so ROADMAP.md Phase 3 can be marked complete or fixed.
"""
import argparse
import sys

from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication

from .manager import WaylandGameManager
from .portal import clear_restore_token


def _init_settings():
    # Match runekit/main.py's QApplication setup so the restore_token is
    # persisted to (and reused from) the same settings file the real app
    # uses.
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv)
    app.setOrganizationName("cupco.de")
    app.setOrganizationDomain("cupco.de")
    app.setApplicationName("RuneKit")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    return app


def run():
    manager = WaylandGameManager()

    print("Calling get_instances() (may show the KWin window picker)...")
    instances = manager.get_instances()
    if not instances:
        print("RESULT: no instance was created (picker cancelled or portal call failed).")
        print("=> NO-GO: see warning log above.")
        return

    instance = instances[0]
    print(f"Discovered instance wid={instance.wid}")

    pos = instance.get_position()
    print(f"get_position() -> x={pos.x()} y={pos.y()} w={pos.width()} h={pos.height()}")

    scaling = instance.get_scaling()
    print(f"get_scaling() -> {scaling}")

    focused = instance.is_focused()
    print(f"is_focused() -> {focused}")

    active = manager.get_active_instance()
    print(f"get_active_instance() -> {'same instance' if active is instance else active}")

    print("\nRESULT: discovery, static geometry, and focus queries all ran without error.")
    print("=> Report back GO/NO-GO so ROADMAP.md Phase 3 can be marked complete.")

    manager.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repick",
        action="store_true",
        help="Clear the persisted restore_token and exit (next plain run re-prompts)",
    )
    args = parser.parse_args()

    _init_settings()

    if args.repick:
        clear_restore_token()
        print("Cleared persisted restore_token. Next run will show the picker.")
        return

    run()


if __name__ == "__main__":
    main()
