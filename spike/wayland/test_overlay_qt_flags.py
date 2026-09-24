"""
Phase 5 spike (Track A): validate whether DesktopWideOverlay's existing Qt
window-flag combination (FramelessWindowHint | BypassWindowManagerHint |
WindowTransparentForInput | WindowStaysOnTopHint -- see
runekit/game/overlay.py) produces an acceptable click-through, always-on-top,
transparent, multi-monitor overlay under KWin's native Wayland compositor.

Unlike the Phase 0-4 spikes, this uses PySide6 exclusively (already a
project dependency -- no PyGObject/pywayland involved), since it tests Qt's
own QPA window-flag behavior rather than a D-Bus portal. See ROADMAP.md
Phase 5 for the open question this answers: with pywayland dropped from the
plan, is a hand-written layer-shell client (Track B) actually needed, or do
Qt's own flags already work under KWin/Wayland?

Background: DesktopWideOverlay (runekit/game/overlay.py) already uses this
flag combination successfully on X11. Qt.WindowType.BypassWindowManagerHint
has no Wayland equivalent (Wayland has no "bypass the compositor" concept),
and WindowTransparentForInput/WindowStaysOnTopHint are known from other Qt-
on-Wayland projects to be unreliable on some QPA versions/compositors. This
script mirrors DesktopWideOverlay's construction as closely as possible
(virtual-geometry spanning, translucent background, frameless) and adds
instrumentation plus an on-screen visual test pattern so a human observer on
the real KDE Plasma Wayland machine can judge GO/NO-GO against 5 criteria.

What this does NOT validate: DesktopWideOverlay.check_compatibility()'s
black-screen self-test uses QGuiApplication.primaryScreen().grabWindow(0),
which ROADMAP.md's Feasibility notes already establish does not work on
Wayland -- that self-test will need a separate fix/bypass on the Wayland
backend regardless of this spike's Track A/B outcome, and is out of scope
here.

Setup: none -- PySide6 is already a project dependency (pyproject.toml).
No pip install needed, unlike the PyGObject-based Phase 0-4 spikes.

Run (on the real Bazzite/KDE Plasma Wayland machine, inside a graphical
session):
    python test_overlay_qt_flags.py [--duration SECONDS]

Manual test procedure (read this before running):
  1. Open a normal (non-maximized) terminal or text editor window BEFORE
     running this script, positioned somewhere on your primary screen,
     roughly under where the center marker will appear (middle of your
     combined virtual desktop).
  2. Run this script. A translucent overlay should appear covering your
     entire virtual desktop (all monitors), with a colored rectangle/label
     per screen plus a white center marker rectangle.
  3. CHECK 1 (always-on-top, normal window): does the overlay stay visually
     above the terminal/editor window from step 1?
  4. CHECK 2 (always-on-top, fullscreen/maximized): maximize or fullscreen
     that window (or another app) -- does the overlay still stay on top?
     This is the closest proxy to the real RS3 game window scenario.
  5. CHECK 3 (click-through): click inside the white center marker, on top
     of the terminal/editor, and type a few characters. Do the clicks/keys
     land in the terminal/editor (not consumed by the overlay)? This script
     also auto-detects the FAILURE case: if the overlay itself ever
     receives a mouse or key event, it prints "!!! OVERLAY RECEIVED INPUT
     EVENT !!!" immediately, which alone proves click-through is broken.
  6. CHECK 4 (transparent compositing): is there any black or opaque box
     anywhere in the overlay's geometry (over live desktop content), or
     does it composite cleanly as translucent color?
  7. CHECK 1b (KWin window-rule workaround, only run if CHECK 1/2 FAILED):
     WindowStaysOnTopHint has no Wayland protocol equivalent and is known
     to be unreliable on KWin -- see ROADMAP.md Phase 5 notes. Before
     concluding a layer-shell client (Track B) is required, test the
     dependency-free KWin "Keep above others" window rule instead:
       a. With this script running (use --duration with a large value, or
          --forever, so it stays up while you do this), open KDE System
          Settings -> Window Management -> Window Rules -> Add New.
       b. Click "Detect Window Properties..." and click on the overlay
          window (or any part of the translucent area) to auto-fill its
          Window class -- this works even without an installed .desktop
          file. This script sets Qt's applicationName to
          "runekit-overlay-spike" so the detected class/app_id is easy to
          recognize if you'd rather type it in directly instead of using
          Detect.
       c. Under the "Arrangement & Access" tab, add the "Keep above other
          windows" property, set it to "Force" / "Yes".
       d. Apply, then close and re-run this script (rules are most
          reliably picked up on window creation) and re-check CHECK 1/2
          with the rule active. Also test against a fullscreen app, since
          a real KWin bug report found rules not consistently enforced
          for some properties/activities -- see ROADMAP.md Phase 5.
       e. Record whether the rule fixed always-on-top reliably, including
          against a fullscreen window, in ROADMAP.md's Phase 5 section.
  8. CHECK 5 (multi-monitor spanning, only if you have 2+ monitors): does
     each screen's colored rectangle/label align correctly with that
     physical monitor's actual bounds (no offset/misalignment/gaps)?

  The script exits automatically after --duration seconds (default 30),
  or use --forever to keep it up indefinitely (needed for CHECK 1b's
  setup/re-test workflow), and press Ctrl+C to exit.

GO requires CHECK 1-5 to all pass -> log the outcome in ROADMAP.md's Phase 5
section (per the project's existing spike-documentation convention -- see
how Phase 0's GO/NO-GO results were recorded). If CHECK 1/2 (plain Qt flags)
fail but CHECK 1b's KWin window-rule workaround reliably fixes always-on-top
(including against fullscreen windows), that dependency-free workaround may
be an acceptable substitute for CHECK 1/2 -- record this explicitly, since
it changes the implementation approach (ship a KWin rule/instructions
instead of relying on the flag alone) without needing Track B. If neither
plain flags nor the window-rule workaround are reliable, Track A is NO-GO
and Phase 5 should proceed to Track B (reintroducing pywayland for a
minimal wlr-layer-shell/KDE layer-shell client, pending re-approval of that
dependency per CLAUDE.md).
"""
import argparse
import logging
import signal
import sys

from PySide6.QtCore import Qt, QRect, QEvent, QTimer, QObject
from PySide6.QtGui import QGuiApplication, QColor, QBrush, QPen, QFont
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsRectItem,
    QGraphicsTextItem,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("test_overlay_qt_flags")

SCREEN_COLORS = [
    QColor(255, 0, 0, 90),
    QColor(0, 200, 0, 90),
    QColor(0, 128, 255, 90),
    QColor(255, 220, 0, 90),
    QColor(255, 0, 255, 90),
]


class InputEventSpy(QObject):
    """Installed as an event filter on the overlay window/view/viewport to
    auto-detect the click-through FAILURE case: if WindowTransparentForInput
    actually works, none of these events should ever be delivered here --
    they should all pass through to whatever real window is underneath."""

    WATCHED = {
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseMove,
        QEvent.Type.KeyPress,
        QEvent.Type.KeyRelease,
        QEvent.Type.Enter,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hit_count = 0

    def eventFilter(self, obj, event):
        if event.type() in self.WATCHED:
            self.hit_count += 1
            logger.warning(
                "!!! OVERLAY RECEIVED INPUT EVENT !!! type=%s obj=%s "
                "(click-through is BROKEN if you see this)",
                event.type(),
                obj,
            )
        return False


class OverlaySpikeWindow(QMainWindow):
    """Mirrors DesktopWideOverlay's __init__ (runekit/game/overlay.py) as
    closely as possible: same window flags, same widget attributes, same
    virtual-geometry-spanning QGraphicsView/QGraphicsScene setup -- plus a
    visual test pattern and an input-event spy for this spike's checks."""

    def __init__(self):
        super().__init__(
            flags=Qt.WindowType.Widget
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.BypassWindowManagerHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent")

        virtual_screen = QRect(0, 0, 0, 0)
        self.screen_geometries = []
        for screen in QGuiApplication.screens():
            # Matches DesktopWideOverlay exactly: union of virtualGeometry()
            # gives the combined virtual-desktop bounding rect.
            virtual_screen = virtual_screen.united(screen.virtualGeometry())
            # geometry() (not virtualGeometry()) gives *this* screen's own
            # bounds within that shared virtual-desktop coordinate system --
            # what we actually want to draw per-monitor test rectangles at.
            self.screen_geometries.append((screen, screen.geometry()))

        self.scene = QGraphicsScene(
            0, 0, virtual_screen.width(), virtual_screen.height(), parent=self
        )
        self.view = QGraphicsView(self.scene, self)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setStyleSheet("background: transparent")
        self.view.setGeometry(0, 0, virtual_screen.width(), virtual_screen.height())
        self.view.setInteractive(False)

        self.setGeometry(virtual_screen)
        self._draw_test_pattern(virtual_screen)

        self.spy = InputEventSpy()
        self.installEventFilter(self.spy)
        self.view.installEventFilter(self.spy)
        self.view.viewport().installEventFilter(self.spy)

    def _draw_test_pattern(self, virtual_screen: QRect):
        pen = QPen()
        pen.setBrush(Qt.BrushStyle.NoBrush)
        font = QFont()
        font.setPointSize(16)

        for i, (screen, geom) in enumerate(self.screen_geometries):
            color = SCREEN_COLORS[i % len(SCREEN_COLORS)]
            rect = QGraphicsRectItem(geom.x(), geom.y(), geom.width(), geom.height())
            rect.setPen(pen)
            rect.setBrush(QBrush(color))
            self.scene.addItem(rect)

            label = QGraphicsTextItem(
                f"Screen {i}: {screen.name()}\n"
                f"{geom.width()}x{geom.height()} @ ({geom.x()},{geom.y()})\n"
                f"devicePixelRatio={screen.devicePixelRatio()}"
            )
            label.setDefaultTextColor(QColor(255, 255, 255))
            label.setFont(font)
            label.setPos(geom.x() + 40, geom.y() + 40)
            self.scene.addItem(label)

        # Center marker spanning the middle of the whole virtual desktop --
        # place a real window (terminal/editor) here before running, per the
        # module docstring's manual test procedure, to judge always-on-top
        # and click-through against it.
        cx, cy = virtual_screen.width() // 2, virtual_screen.height() // 2
        marker = QGraphicsRectItem(cx - 200, cy - 100, 400, 200)
        marker.setPen(pen)
        marker.setBrush(QBrush(QColor(255, 255, 255, 60)))
        self.scene.addItem(marker)

        marker_label = QGraphicsTextItem(
            "RuneKit overlay spike\n"
            "Click/type here -- input should reach\n"
            "the window BEHIND this overlay, not this overlay."
        )
        marker_label.setDefaultTextColor(QColor(0, 0, 0))
        marker_label.setFont(font)
        marker_label.setPos(cx - 190, cy - 90)
        self.scene.addItem(marker_label)


def log_environment():
    app = QGuiApplication.instance()
    platform_name = app.platformName()
    logger.info("QGuiApplication.platformName() = %r", platform_name)
    if platform_name.lower() != "wayland":
        logger.warning(
            "Not running under the 'wayland' QPA platform (got %r). "
            "This spike must run in a real Wayland session to be "
            "meaningful -- set QT_QPA_PLATFORM=wayland and ensure "
            "WAYLAND_DISPLAY is set.",
            platform_name,
        )

    for i, screen in enumerate(app.screens()):
        logger.info(
            "Screen %d: name=%r geometry=%s virtualGeometry=%s "
            "devicePixelRatio=%s",
            i,
            screen.name(),
            screen.geometry(),
            screen.virtualGeometry(),
            screen.devicePixelRatio(),
        )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Seconds to show the overlay before auto-exit (default: 30)",
    )
    parser.add_argument(
        "--forever",
        action="store_true",
        help=(
            "Don't auto-exit; keep the overlay up until Ctrl+C. Use this "
            "while setting up/testing the CHECK 1b KWin window rule "
            "workaround, since that requires closing and re-opening the "
            "window to pick up a newly-applied rule."
        ),
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)
    # Gives the window a recognizable Wayland app_id/window class for the
    # CHECK 1b KWin "Detect Window Properties" workflow (see module
    # docstring) -- without this, Qt defaults to the script's filename.
    app.setApplicationName("runekit-overlay-spike")
    app.setDesktopFileName("runekit-overlay-spike")
    signal.signal(signal.SIGINT, lambda *_: app.quit())

    log_environment()

    window = OverlaySpikeWindow()
    logger.info("Requested window flags: %s", window.windowFlags())
    window.show()
    logger.info("Actual window flags after show(): %s", window.windowFlags())

    def report_activation():
        if window.isActiveWindow():
            logger.warning(
                "!!! OVERLAY WINDOW BECAME ACTIVE !!! (isActiveWindow=True) "
                "-- this suggests it may be stealing focus/input despite "
                "WindowTransparentForInput; check CHECK 3 carefully."
            )

    handle = window.windowHandle()
    if handle is not None:
        handle.activeChanged.connect(report_activation)

    print("\n" + "=" * 70)
    print("Overlay shown. Follow the manual checks described in this")
    print("script's module docstring (open the .py file, or run --help).")
    if args.forever:
        print("Running with --forever: press Ctrl+C to exit.")
    else:
        print(f"Auto-exiting in {args.duration:.0f}s, or press Ctrl+C.")
    print("=" * 70 + "\n")

    if not args.forever:
        QTimer.singleShot(int(args.duration * 1000), app.quit)

    exit_code = app.exec()

    if window.spy.hit_count > 0:
        print(f"\nRESULT: overlay received {window.spy.hit_count} input event(s) directly.")
        print("=> NO-GO on CHECK 3 (click-through): WindowTransparentForInput did not work.")
    else:
        print("\nRESULT: overlay received 0 direct input events during this run.")
        print("=> Consistent with click-through working, PROVIDED you actually clicked/typed")
        print("   over the overlay's area during the run and saw those inputs land in the")
        print("   window underneath (re-run and test explicitly if you did not).")

    print("\nRemaining checks (always-on-top x2, transparent compositing, multi-monitor")
    print("spanning) require your visual judgement per the module docstring above.")
    print("Record the GO/NO-GO outcome for each of the 5 checks in ROADMAP.md's Phase 5")
    print("section, per the project's existing spike documentation convention.")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
