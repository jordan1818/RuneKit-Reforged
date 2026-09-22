import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QGraphicsItem

from ..instance import GameInstance, ImageType

if TYPE_CHECKING:
    from .manager import WaylandGameManager


class WaylandGameInstance(GameInstance):
    """Stub GameInstance for KDE Plasma/KWin Wayland sessions.

    This satisfies the GameInstance ABC so WaylandGameManager can be
    instantiated and wired up in Phase 1 (session detection & scaffolding).
    Real behavior (ScreenCast-based capture, window discovery, hotkey,
    overlay) lands in later roadmap phases -- see ROADMAP.md.
    """

    overlay: QGraphicsItem

    def __init__(self, manager: "WaylandGameManager", **kwargs):
        super().__init__(**kwargs)
        self.manager = manager
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def get_position(self) -> QRect:
        raise NotImplementedError(
            "Wayland window geometry not implemented yet (see ROADMAP.md Phase 3)"
        )

    def get_scaling(self) -> float:
        raise NotImplementedError(
            "Wayland scaling detection not implemented yet (see ROADMAP.md Phase 3)"
        )

    def is_focused(self) -> bool:
        raise NotImplementedError(
            "Wayland focus tracking not implemented yet (see ROADMAP.md Phase 3)"
        )

    def get_world(self) -> Optional[int]:
        return None

    def grab_game(self) -> ImageType:
        raise NotImplementedError(
            "Wayland screen capture not implemented yet (see ROADMAP.md Phase 2)"
        )

    def grab_desktop(self, x: int, y: int, w: int, h: int) -> ImageType:
        raise NotImplementedError(
            "Wayland screen capture not implemented yet (see ROADMAP.md Phase 2)"
        )

    def get_overlay_area(self) -> QGraphicsItem:
        raise NotImplementedError(
            "Wayland overlay not implemented yet (see ROADMAP.md Phase 5)"
        )
