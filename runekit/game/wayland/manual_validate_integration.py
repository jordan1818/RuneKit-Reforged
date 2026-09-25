"""
Manual validation script for ROADMAP.md Phase 6 (Integration & regression).

Unlike manual_validate_capture.py/manual_validate_discovery.py/
manual_validate_hotkey.py/manual_validate_overlay.py (Phases 2-5), which
each exercise runekit/game/wayland/* classes directly, this script drives
the real end-to-end path used by the actual application: runekit.main's
QApplication/Host/App/AppWindow/QtWebEngine setup, launching a real Alt1
app manifest URL exactly like `python main.py <app_url>` does. This is the
first point in the roadmap where the wiring added in
runekit/host/host.py, runekit/ui/tray.py, runekit/app/app.py,
runekit/app/view/window.py, and runekit/browser/* is exercised together
against the Wayland backend, rather than just runekit/game/wayland/*
in isolation.

Since this project has no automated test suite (see CLAUDE.md/ROADMAP.md),
this mirrors the manual_validate_*.py convention used by every prior phase.

Setup (one-time, on the Bazzite/KDE Plasma Wayland machine):
    pip install --user PyGObject
    # System requirement: xdg-desktop-portal + xdg-desktop-portal-kde
    # running (already required by Phases 2-5).

Run (defaults to the AFKScape example app from README.md):
    python -m runekit.game.wayland.manual_validate_integration

    Or with a specific app manifest URL:
    python -m runekit.game.wayland.manual_validate_integration \\
        https://runeapps.org/apps/alt1/afkscape/appconfig.json

    # 1. KWin's window picker may prompt once (reused/cached via
    #    restore_token from earlier phases) -- pick a real window (e.g.
    #    RuneScape if running, otherwise any window) to stand in for the
    #    game window.
    # 2. The overlay's one-time KWin-script consent dialog may appear
    #    (skipped if already answered in a previous phase's run) -- click
    #    Yes so the always-on-top checks below are meaningful.
    # 3. The Alt1 app's window should open and load.

What to check after running:
    1. The app window opens, loads its content, and is NOT buried under
       the picked "game" window -- including if you click on/focus the
       picked window afterward. This is the main new behavior Phase 6
       adds: AppWindow.keep_window_above() loading a per-window KWin
       always-on-top script (see runekit/game/wayland/instance.py),
       since Qt.WindowType.WindowStaysOnTopHint alone (what AppWindow
       already sets) is known-unreliable under KWin/Wayland (Phase 5).
    2. If the picked window is fullscreened, the app window should still
       stay above it.
    3. The app window is positioned near (0, 0) of your primary display
       (or wherever the overlay/picked-window rect landed) -- this is
       expected, not a bug: WaylandGameInstance.get_position() is always
       anchored at (0, 0) per ROADMAP.md Phase 3 (no real on-screen
       position is available from the ScreenCast portal for WINDOW-type
       streams).
    4. If the app draws an overlay (alt1.overlayRect/Text/Image etc.), it
       should render above the picked window at that same (0, 0)-anchored
       area, consistent with Phase 5's overlay validation.
    5. Alt+1 (if the app registers an activator) should trigger its
       configured behavior while the picked window is focused.
    6. alt1.currentWorld will most likely report -1/unknown -- this is
       an accepted, documented Wayland limitation (see
       WaylandGameInstance.get_world()'s docstring), not a bug to report,
       unless it raises an exception instead of degrading gracefully.
    7. Closing the app window cleanly unloads its KWin keep-above script
       (no visible effect to check directly, but confirms no leftover
       always-on-top behavior lingers on that window's title if reused).
    8. Check runekit.log (~/.config/cupco.de/RuneKit/logs/runekit.log)
       for any unexpected errors/tracebacks during the whole flow.
    9. Ctrl+C (or closing the app window) exits cleanly, with the
       GameManager (and its overlay/KWin scripts) torn down without
       warnings.
Report back GO/NO-GO (and any fix notes, same as prior phases) so
ROADMAP.md Phase 6 can be marked complete or fixed.
"""
import logging
import signal
import sys

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from runekit import browser
from runekit.game import get_platform_manager
from runekit.host import Host

DEFAULT_APP_URL = "https://runeapps.org/apps/alt1/afkscape/appconfig.json"


def main():
    logging.basicConfig(level=logging.DEBUG)

    app_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_APP_URL

    print("Initializing QtWebEngine...")
    browser.init()

    app = QApplication(["runekit-phase6-validate"])
    app.setQuitOnLastWindowClosed(False)
    app.setOrganizationName("cupco.de")
    app.setOrganizationDomain("cupco.de")
    app.setApplicationName("RuneKit")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)

    # Same Ctrl+C workaround as runekit/main.py -- SIGINT is not delivered
    # promptly while control is inside Qt's C++ event loop.
    signal.signal(signal.SIGINT, lambda no, frame: app.quit())
    timer = QTimer()
    timer.start(300)
    timer.timeout.connect(lambda: None)

    print("Creating GameManager via get_platform_manager() (production path)...")
    game_manager = get_platform_manager()
    host = Host(game_manager)

    print(f"\nLaunching app: {app_url}")
    print("(KWin's window picker and/or the overlay consent dialog may prompt now)\n")
    try:
        game_app = host.launch_app_from_url(app_url)
    except Exception:
        logging.exception("Failed to launch app")
        game_manager.stop()
        return

    if game_app is None or game_app.window is None:
        print("RESULT: app failed to launch (see log above).")
        print("=> NO-GO: could not reach the app-window stage.")
        game_manager.stop()
        return

    game_app.window.destroyed.connect(app.quit)

    print("App window opened. Follow the manual checks in this module's docstring.")
    print("Close the app window (or Ctrl+C) to stop.\n")

    try:
        app.exec()
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping game manager (unloads KWin scripts, closes sessions)...")
        game_manager.stop()
        print("=> Report back GO/NO-GO so ROADMAP.md Phase 6 can be marked complete.")


if __name__ == "__main__":
    main()
