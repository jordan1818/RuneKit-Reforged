import itertools
import logging
from typing import Dict, List, Optional, Union

from ..instance import GameInstance
from ..manager import GameManager
from .instance import WaylandGameInstance
from .overlay import WaylandDesktopWideOverlay
from .portal import ScreenCastPortalError, ScreenCastSession, clear_restore_token


class WaylandGameManager(GameManager):
    """GameManager for KDE Plasma/KWin Wayland sessions.

    Window discovery (ROADMAP.md Phase 3) uses the ScreenCast portal's own
    WINDOW-source picker exclusively (no plasmawindowmanagement/pywayland,
    per ROADMAP.md Feasibility notes): get_instances()/get_active_instance()
    lazily open a ScreenCastSession on first call, which may show KWin's
    window picker (or silently reuse the persisted restore_token -- see
    portal.py). The opened session is handed to a new WaylandGameInstance
    so its capture pipeline (ROADMAP.md Phase 2) doesn't need to open its
    own session/re-prompt.

    Since the portal only ever hands back one picked window per session,
    this manager supports at most one instance at a time. Use
    repick_window() to discard the current instance/restore_token and force
    the picker again for a different window.
    """

    _instances: Dict[int, WaylandGameInstance]
    overlay: WaylandDesktopWideOverlay

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._instances = {}
        self._wid_counter = itertools.count(1)
        self._discovery_failed = False
        self.logger = logging.getLogger(__name__ + "." + self.__class__.__name__)
        self._setup_overlay()

    def _setup_overlay(self):
        """Create the desktop-wide overlay window (ROADMAP.md Phase 5).

        Mirrors X11GameManager._setup_overlay()/QuartzGameManager's
        equivalent, but using WaylandDesktopWideOverlay (see overlay.py),
        which adds a KWin-scripting-based always-on-top workaround on top
        of the base DesktopWideOverlay's Qt flags (necessary but not
        sufficient for always-on-top under KWin/Wayland -- validated via
        spike/wayland/test_overlay_qt_flags.py on a real machine).
        """
        self.overlay = WaylandDesktopWideOverlay()
        self.overlay.show()
        self.overlay.check_compatibility()
        self.overlay.setup_keep_above()

    def get_instances(self) -> List[GameInstance]:
        if not self._instances and not self._discovery_failed:
            self._discover_window()

        return list(self._instances.values())

    def get_active_instance(self) -> Union[GameInstance, None]:
        # Unlike X11/Quartz, this manager only ever tracks at most one
        # instance (the ScreenCast portal only hands back one picked
        # window per session -- see class docstring), so there's no real
        # "which instance is active" disambiguation to do here. Return it
        # directly rather than filtering by is_focused(): that proxy
        # tracks whether *RuneKit's own* windows have OS focus (see
        # WaylandGameInstance.is_focused()), which is almost never true at
        # the same time as the game window having focus, and would make
        # this incorrectly return None during normal use.
        instances = self.get_instances()
        return instances[0] if instances else None

    def _discover_window(self) -> Optional[WaylandGameInstance]:
        """Run the ScreenCast portal flow to pick a window, and construct a
        WaylandGameInstance from the result.

        May block on the KWin window-picker dialog (unless a persisted
        restore_token lets it skip the prompt). Returns None (and marks
        discovery as failed for this manager's lifetime, until
        repick_window() is called) if the user cancels or the portal call
        fails, so callers fall back to their existing "no instances found"
        handling.
        """
        session = ScreenCastSession()
        try:
            session.open()
        except ScreenCastPortalError as exc:
            self.logger.warning("Wayland window picker failed or was cancelled: %s", exc)
            self._discovery_failed = True
            return None

        wid = next(self._wid_counter)
        instance = WaylandGameInstance(self, wid, session, parent=self)
        self._instances[wid] = instance
        self.logger.info("Picked Wayland window, instance id %d", wid)
        self.instance_added.emit(instance)
        self.instance_changed.emit()

        return instance

    def repick_window(self):
        """Discard the current instance (if any) and its persisted
        restore_token, so the next get_instances()/get_active_instance()
        call shows the KWin window picker again for a different window.

        Intended to back a UI action (e.g. tray menu), since there is no
        live compositor-pushed way to detect the user wants to switch
        windows -- see ROADMAP.md Phase 3.
        """
        clear_restore_token()
        self._discovery_failed = False

        for wid, instance in list(self._instances.items()):
            del self._instances[wid]
            instance.stop()
            self.instance_removed.emit(instance)

        self.instance_changed.emit()

    def stop(self):
        for instance in list(self._instances.values()):
            instance.stop()
        self._instances = {}

        self.overlay.stop_keep_above()
        try:
            self.overlay.hide()
            self.overlay.deleteLater()
        except RuntimeError:
            pass

