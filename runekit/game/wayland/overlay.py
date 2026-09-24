import logging

from PySide6.QtWidgets import QMessageBox

from ..overlay import DesktopWideOverlay
from .kwin_script import (
    KeepAboveKWinScript,
    KWinScriptError,
    load_keep_above_consent,
    save_keep_above_consent,
)

logger = logging.getLogger(__name__)

# Unique title so kwin_script.py's KEEP_ABOVE_SCRIPT_TEMPLATE matches only
# this window, not any other RuneKit window (main app windows, settings
# dialog) that happens to share the same Wayland app_id -- see
# kwin_script.py's module docstring.
OVERLAY_WINDOW_TITLE = "__runekit_wayland_overlay__"

CONSENT_PROMPT_TEXT = (
    "RuneKit would like to keep its overlay always on top of the game "
    "window.\n\n"
    "On Wayland, this requires loading a small script into KWin (KDE's "
    "window manager) at runtime, using KWin's own scripting interface. "
    "No permanent changes are made to your KWin configuration -- the "
    "script is removed automatically when RuneKit closes.\n\n"
    "Allow this?"
)


class WaylandDesktopWideOverlay(DesktopWideOverlay):
    """DesktopWideOverlay subclass for KDE Plasma/KWin Wayland sessions.

    Qt.WindowType.WindowStaysOnTopHint (what the base DesktopWideOverlay
    relies on -- see runekit/game/overlay.py) is known-unreliable under
    KWin/Wayland (confirmed via spike/wayland/test_overlay_qt_flags.py on a
    real machine: it did not hold, even against a normal window). This
    subclass works around that with a small KWin JavaScript script loaded/
    unloaded at runtime via KWin's own scripting D-Bus interface (see
    kwin_script.py) -- validated GO end-to-end (always-on-top incl. vs. a
    fullscreen window, click-through, transparent compositing, multi-
    monitor spanning) on a real Bazzite/KDE Plasma Wayland machine. See
    ROADMAP.md Phase 5.
    """

    def __init__(self):
        super().__init__()
        # KWin matches windows by title (see kwin_script.py's module
        # docstring for why title, not the Wayland app_id, is used), so
        # give this window a unique, unlikely-to-collide title.
        self.setWindowTitle(OVERLAY_WINDOW_TITLE)
        self._keep_above_script = None
        self._keep_above_attempted = False

    def setup_keep_above(self):
        """Prompt for consent (once, remembered via QSettings) and load the
        KWin always-on-top script if granted.

        Idempotent: safe to call every time a window is (re)discovered
        (e.g. after WaylandGameManager.repick_window()) without re-prompting
        or reloading an already-loaded script.

        Safe to call even if the portal/KWin scripting interface is
        unavailable -- logs a warning and leaves the overlay working
        without the always-on-top enhancement (falling back to the base
        class's Qt flags alone, which are necessary but not sufficient on
        Wayland -- see ROADMAP.md Phase 5).
        """
        if self._keep_above_attempted:
            return
        self._keep_above_attempted = True

        consent = load_keep_above_consent()
        if consent is None:
            answer = QMessageBox.question(
                self,
                "RuneKit overlay",
                CONSENT_PROMPT_TEXT,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            consent = answer == QMessageBox.StandardButton.Yes
            save_keep_above_consent(consent)

        if not consent:
            self.logger.info(
                "User declined KWin always-on-top script; overlay may not "
                "stay above the game window reliably (see ROADMAP.md Phase 5)"
            )
            return

        self._keep_above_script = KeepAboveKWinScript(OVERLAY_WINDOW_TITLE)
        try:
            self._keep_above_script.load_and_start()
        except KWinScriptError as exc:
            self.logger.warning(
                "Could not load KWin always-on-top script: %s. Overlay may "
                "not stay above the game window reliably.",
                exc,
            )
            self._keep_above_script = None

    def stop_keep_above(self):
        """Unload the KWin script, if loaded. Safe to call multiple times."""
        if self._keep_above_script is not None:
            self._keep_above_script.unload()
            self._keep_above_script = None

    def check_compatibility(self):
        # The base class's check_compatibility() self-test uses
        # QGuiApplication.primaryScreen().grabWindow(0), which this
        # roadmap's Feasibility notes already establish does not work on
        # Wayland -- it would misfire and disable an otherwise-working
        # overlay. Skip it: the KWin-script approach above doesn't share
        # the "may cause a black screen" X11 failure mode that self-test
        # was originally guarding against. See ROADMAP.md Phase 5.
        self.logger.debug(
            "Skipping check_compatibility() black-screen self-test on "
            "Wayland (QGuiApplication.primaryScreen().grabWindow(0) does "
            "not work under Wayland QPA)"
        )
