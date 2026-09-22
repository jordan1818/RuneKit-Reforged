import logging
from typing import Dict, List, Union

from ..instance import GameInstance
from ..manager import GameManager
from .instance import WaylandGameInstance


class WaylandGameManager(GameManager):
    """Stub GameManager for KDE Plasma/KWin Wayland sessions.

    This is Phase 1 (session detection & scaffolding) of the Wayland
    support roadmap -- see ROADMAP.md. Window discovery, screen capture,
    the global hotkey, and the overlay are implemented in later phases via
    xdg-desktop-portal-kde D-Bus portals (PyGObject's Gio/Gst). No such
    dependency is added yet; this manager currently reports no instances.
    """

    _instances: Dict[int, WaylandGameInstance]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._instances = {}
        self.logger = logging.getLogger(__name__ + "." + self.__class__.__name__)
        self.logger.warning(
            "Wayland support is a work in progress: window discovery, capture, "
            "hotkey, and overlay are not implemented yet (see ROADMAP.md)"
        )

    def get_instances(self) -> List[GameInstance]:
        return list(self._instances.values())

    def get_active_instance(self) -> Union[GameInstance, None]:
        for instance in self._instances.values():
            if instance.is_focused():
                return instance

        return None
