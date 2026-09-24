"""
Manual validation script for ROADMAP.md Phase 5 (Desktop-wide overlay).

Like manual_validate_capture.py (Phase 2), manual_validate_discovery.py
(Phase 3), and manual_validate_hotkey.py (Phase 4), this exercises the
actual production classes (runekit/game/wayland/manager.py, instance.py,
overlay.py, kwin_script.py) rather than throwaway spike code, since this
project has no automated test suite (see CLAUDE.md/ROADMAP.md).

Setup (one-time, on the Bazzite/KDE Plasma Wayland machine):
    pip install --user PyGObject
    # System requirement: xdg-desktop-portal + xdg-desktop-portal-kde
    # running (already required by Phases 2-4).

Run:
    python -m runekit.game.wayland.manual_validate_overlay

    # 1. KWin's window picker will prompt once (reused from Phase 3's
    #    discovery flow) -- pick a real window (e.g. a text editor) to
    #    stand in for the game window.
    # 2. A consent dialog should appear asking to load the KWin
    #    always-on-top script -- click Yes.
    # 3. A translucent overlay rectangle should appear over the picked
    #    window's (0, 0)-anchored area (see ROADMAP.md Phase 3's note on
    #    why Wayland overlays are always anchored at the origin).

What to check after running:
    1. The consent dialog appeared once; approving it did not error.
    2. The overlay rectangle is visible and positioned correctly.
    3. The overlay stays ABOVE the picked window, including when that
       window is maximized/fullscreened.
    4. Clicking/typing over the overlay's area reaches the window
       underneath (click-through), not the overlay itself.
    5. Ctrl+C stops the script cleanly, and the KWin script is unloaded
       (check `qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.
       isScriptLoaded <plugin_name>` returns false afterwards, or just
       confirm no leftover always-on-top behavior on the picked window).
    6. Re-run the script: since consent was already granted, the consent
       dialog should NOT appear again (remembered via QSettings).
Report back GO/NO-GO so ROADMAP.md Phase 5 can be marked complete or fixed.
"""
import logging
import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from .manager import WaylandGameManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("cupco.de")
    app.setOrganizationDomain("cupco.de")
    app.setApplicationName("RuneKit")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)

    print("Creating WaylandGameManager (production code path)...")
    print("This will show the overlay window immediately, then a consent")
    print("dialog for the KWin always-on-top script.")
    manager = WaylandGameManager()

    print("\nDiscovering a window (KWin's picker may prompt now)...")
    instance = manager.get_active_instance()
    if instance is None:
        print("RESULT: no window was picked (cancelled or portal error).")
        print("=> Cannot validate overlay placement without a picked window.")
        manager.stop()
        return

    print(f"Picked window, position={instance.get_position()}")
    print("\nOverlay should now be visible over that position.")
    print("Follow the manual checks in this script's module docstring.")
    print("Press Ctrl+C to stop.\n")

    try:
        app.exec()
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping manager (unloads the KWin script, closes sessions)...")
        manager.stop()
        print("=> Report back GO/NO-GO so ROADMAP.md Phase 5 can be marked complete.")


if __name__ == "__main__":
    main()
