"""
Manual validation script for ROADMAP.md Phase 5 (Desktop-wide overlay).

Like manual_validate_capture.py (Phase 2), manual_validate_discovery.py
(Phase 3), and manual_validate_hotkey.py (Phase 4), this exercises the
actual production classes (runekit/game/wayland/manager.py, instance.py,
overlay.py, kwin_script.py) rather than throwaway spike code, since this
project has no automated test suite (see CLAUDE.md/ROADMAP.md).

IMPORTANT: the overlay item returned by get_overlay_area() is a bare
QGraphicsRectItem drawn with a fully transparent pen/no brush (see
DesktopWideOverlay.add_instance() in runekit/game/overlay.py) -- it is
INVISIBLE by design. In real use, Alt1 apps draw their own visible content
(rects/text/images) as children of it via OverlayApi
(runekit/browser/overlay.py). This script draws a simple visible test
rectangle + label as a child of that item -- WITHOUT this, running the
script produces no visible on-screen change at all, even if window
discovery/consent/KWin-scripting are all working correctly. (This bit an
earlier revision of this script: real hardware showed the ScreenCast
portal picking a window and the WaylandGameManager/WaylandGameInstance
constructing successfully, but nothing was visible, since no test content
was ever drawn.)

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
    #    always-on-top script -- click Yes. (Skipped if a previous run
    #    already answered it -- see check 6 below; use --reset-consent to
    #    force it to ask again.)
    # 3. A visible red-bordered rectangle with a text label should appear
    #    over the picked window's (0, 0)-anchored area (see ROADMAP.md
    #    Phase 3's note on why Wayland overlays are always anchored at the
    #    origin).

What to check after running:
    1. The consent dialog appeared (or was correctly skipped -- see check
       6); approving it did not error.
    2. The red-bordered test rectangle + label is visible and positioned
       correctly over the picked window.
    3. The overlay stays ABOVE the picked window, including when that
       window is maximized/fullscreened.
    4. Clicking/typing over the overlay's area reaches the window
       underneath (click-through), not the overlay itself.
    5. Ctrl+C stops the script cleanly, and the KWin script is unloaded
       (check `qdbus org.kde.KWin /Scripting org.kde.kwin.Scripting.
       isScriptLoaded <plugin_name>` returns false afterwards, or just
       confirm no leftover always-on-top behavior on the picked window).
    6. Re-run the script: since consent was already granted, the consent
       dialog should NOT appear again (remembered via QSettings). Use
       --reset-consent to clear it and force the prompt again.
Report back GO/NO-GO so ROADMAP.md Phase 5 can be marked complete or fixed.
"""
import argparse
import logging
import signal
import sys

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QColor, QBrush, QPen
from PySide6.QtWidgets import QApplication, QGraphicsRectItem, QGraphicsTextItem

from .kwin_script import KEEP_ABOVE_CONSENT_SETTINGS_KEY
from .manager import WaylandGameManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def draw_test_pattern(instance):
    """Draw a visible rectangle + label as a child of the instance's
    overlay item, since that item itself is an invisible bare container
    (see this module's docstring)."""
    overlay_area = instance.get_overlay_area()
    pos = instance.get_position()

    rect = QGraphicsRectItem(0, 0, pos.width(), pos.height(), parent=overlay_area)
    pen = QPen(QColor(255, 0, 0, 220))
    pen.setWidth(6)
    rect.setPen(pen)
    rect.setBrush(QBrush(QColor(255, 0, 0, 40)))

    label = QGraphicsTextItem(
        "RuneKit overlay test pattern\n"
        "(should stay above this window, incl. fullscreen)",
        parent=overlay_area,
    )
    label.setDefaultTextColor(QColor(255, 255, 255))
    label.setPos(20, 20)

    return rect, label


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--reset-consent",
        action="store_true",
        help=(
            "Clear the previously-remembered KWin always-on-top consent "
            "answer (QSettings), forcing the consent dialog to appear "
            "again this run."
        ),
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setOrganizationName("cupco.de")
    app.setOrganizationDomain("cupco.de")
    app.setApplicationName("RuneKit")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)

    # Ctrl+C (SIGINT) is not delivered promptly while control is inside
    # Qt's C++ event loop (app.exec()) -- Python's signal handler only runs
    # between bytecode instructions, which doesn't happen there. Mirrors
    # runekit/main.py's workaround: install a SIGINT handler that calls
    # app.quit(), plus a periodic no-op QTimer to force Qt to briefly hand
    # control back to the Python interpreter often enough for that handler
    # to actually fire. Without this, Ctrl+C appears to do nothing and the
    # window must be force-closed instead -- which also means
    # manager.stop() (and therefore KeepAboveKWinScript.unload()) never
    # runs, leaving the temporary KWin script loaded until its matched
    # window closes and its /tmp .js file undeleted.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    timer = QTimer()
    timer.start(300)
    timer.timeout.connect(lambda: None)

    if args.reset_consent:
        QSettings().remove(KEEP_ABOVE_CONSENT_SETTINGS_KEY)
        print("Cleared remembered consent answer; the dialog should appear this run.\n")

    print("Creating WaylandGameManager (production code path)...")
    print("This will show the overlay window immediately, then a consent")
    print(
        "dialog for the KWin always-on-top script (unless already answered "
        "in a previous run -- use --reset-consent to force it)."
    )
    manager = WaylandGameManager()

    print("\nDiscovering a window (KWin's picker may prompt now)...")
    instance = manager.get_active_instance()
    if instance is None:
        print("RESULT: no window was picked (cancelled or portal error).")
        print("=> Cannot validate overlay placement without a picked window.")
        manager.stop()
        return

    print(f"Picked window, position={instance.get_position()}")
    draw_test_pattern(instance)
    print("\nA red-bordered test rectangle + label should now be visible")
    print("over that position (see this module's docstring if you see")
    print("nothing -- the bare overlay item itself is invisible by design).")
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
