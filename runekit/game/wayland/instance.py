import logging
import time
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QGraphicsItem

from runekit.image.np_utils import np_crop
from ..instance import GameInstance, ImageType
from .capture import WaylandCapturePipeline

if TYPE_CHECKING:
    from .manager import WaylandGameManager


class WaylandGameInstance(GameInstance):
    """GameInstance for KDE Plasma/KWin Wayland sessions.

    Screen capture (ROADMAP.md Phase 2) is implemented via the
    org.freedesktop.portal.ScreenCast WINDOW picker + a PipeWire/Gst
    pipeline (see .portal/.capture), run on a background QThread so the
    blocking D-Bus/portal-picker/appsink calls don't stall Qt's event loop.

    Window discovery, geometry/focus tracking, the global hotkey, and the
    overlay are implemented in later phases -- see ROADMAP.md Phases 3-5.
    """

    overlay: QGraphicsItem
    refresh_rate = 100

    def __init__(self, manager: "WaylandGameManager", **kwargs):
        super().__init__(**kwargs)
        self.manager = manager
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._capture_pipeline: Optional[WaylandCapturePipeline] = None
        self._game_last_grab = 0.0
        self._game_last_image = None

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

    def _ensure_capture_started(self):
        if self._capture_pipeline is None:
            self._capture_pipeline = WaylandCapturePipeline()
            self._capture_pipeline.start()

    def grab_game(self) -> ImageType:
        """Return the most recent captured frame as a BGRA32 numpy array.

        Implements ROADMAP.md Phase 2: the frame comes from a background
        ScreenCast portal + PipeWire/Gst pipeline (see .capture), lazily
        started on first call (which may show KWin's window picker once).
        """
        if (time.monotonic() - self._game_last_grab) * 1000 < self.refresh_rate:
            if self._game_last_image is not None:
                return self._game_last_image

        self._ensure_capture_started()

        frame = self._capture_pipeline.get_latest_frame()
        if frame is None:
            if self._capture_pipeline.last_error:
                raise RuntimeError(
                    f"Wayland screen capture failed: {self._capture_pipeline.last_error}"
                )
            if self._game_last_image is not None:
                return self._game_last_image
            raise RuntimeError("Wayland screen capture has not produced a frame yet")

        self._game_last_image = frame
        self._game_last_grab = time.monotonic()
        return frame

    def grab_desktop(self, x: int, y: int, w: int, h: int) -> ImageType:
        """Crop a region out of the captured window's latest frame.

        There is no separate full-desktop (MONITOR-type) portal session in
        Phase 2 -- only the WINDOW picker used by grab_game() -- so this
        crops from the same frame. See ROADMAP.md Phase 2.
        """
        image = self.grab_game()
        return np_crop(image, x, y, w, h)

    def stop(self):
        """Stop the background capture pipeline, if running."""
        if self._capture_pipeline is not None:
            self._capture_pipeline.stop()
            self._capture_pipeline = None

    def __del__(self):
        self.stop()

    def get_overlay_area(self) -> QGraphicsItem:
        raise NotImplementedError(
            "Wayland overlay not implemented yet (see ROADMAP.md Phase 5)"
        )
