import logging
import time
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QGraphicsItem

from runekit.image.np_utils import np_crop
from ..instance import GameInstance, ImageType
from .capture import WaylandCapturePipeline
from .portal import ScreenCastSession

if TYPE_CHECKING:
    from .manager import WaylandGameManager


class WaylandGameInstance(GameInstance):
    """GameInstance for KDE Plasma/KWin Wayland sessions.

    Screen capture (ROADMAP.md Phase 2) is implemented via the
    org.freedesktop.portal.ScreenCast WINDOW picker + a PipeWire/Gst
    pipeline (see .portal/.capture), run on a background QThread so the
    blocking D-Bus/portal-picker/appsink calls don't stall Qt's event loop.

    Window discovery (ROADMAP.md Phase 3) opens the ScreenCastSession
    synchronously in WaylandGameManager and hands it to this instance, so
    the KWin window-picker prompt happens once at discovery time. Geometry
    (get_position/get_scaling) and focus tracking (is_focused) are
    best-effort/static, per the Phase 3 plan: the ScreenCast portal's
    WINDOW-type streams do not expose an on-screen position (only a
    negotiated size -- see portal.py's ScreenCastSession.stream_size), and
    there is no live compositor-pushed geometry/focus-change event source
    available without plasmawindowmanagement (dropped from this project's
    plan -- see ROADMAP.md Feasibility notes/Non-goals).

    The global hotkey and the overlay are implemented in later phases --
    see ROADMAP.md Phases 4-5.
    """

    overlay: QGraphicsItem
    refresh_rate = 100

    def __init__(
        self,
        manager: "WaylandGameManager",
        wid: int,
        session: ScreenCastSession,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.manager = manager
        # Synthetic id (not a real X11-style window id) used to key this
        # instance the same way DesktopWideOverlay/GameManager key X11/
        # Quartz instances by `wid` -- see ROADMAP.md Phase 3.
        self.wid = wid
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._session = session
        self._capture_pipeline: Optional[WaylandCapturePipeline] = None
        self._game_last_grab = 0.0
        self._game_last_image = None

        # Cached, static geometry from the stream's negotiated size at
        # session-start time. There is no on-screen x/y for WINDOW-type
        # ScreenCast streams (see ScreenCastSession.stream_size), so this
        # is anchored at (0, 0) -- a known Wayland limitation, not a bug.
        width, height = session.stream_size or (0, 0)
        self._position = QRect(0, 0, width, height)

        self._is_focused = (
            QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        )
        QGuiApplication.instance().applicationStateChanged.connect(
            self._on_application_state_changed
        )

    def get_position(self) -> QRect:
        return self._position

    def get_scaling(self) -> float:
        # No QWindow handle exists for a portal-picked window (unlike
        # X11's QWindow.fromWinId), so fall back to the scaling of
        # whichever screen RuneKit's own windows are on. Best-effort, per
        # ROADMAP.md Phase 3.
        screen = QGuiApplication.primaryScreen()
        return screen.devicePixelRatio() if screen else 1.0

    def is_focused(self) -> bool:
        return self._is_focused

    def _on_application_state_changed(self, state):
        # Best-effort focus tracking: an unprivileged Wayland client cannot
        # query another window's activation state without a privileged
        # protocol, so treat this instance as focused whenever RuneKit
        # itself has input focus -- see ROADMAP.md Phase 3.
        focused = state == Qt.ApplicationState.ApplicationActive
        if focused != self._is_focused:
            self._is_focused = focused
            self.focusChanged.emit(focused)

    def get_world(self) -> Optional[int]:
        return None

    def _ensure_capture_started(self):
        if self._capture_pipeline is None:
            self._capture_pipeline = WaylandCapturePipeline(self._session)
            self._capture_pipeline.start()

    def grab_game(self) -> ImageType:
        """Return the most recent captured frame as a BGRA32 numpy array.

        Implements ROADMAP.md Phase 2: the frame comes from a background
        PipeWire/Gst pipeline (see .capture) consuming the ScreenCastSession
        opened during discovery (ROADMAP.md Phase 3), lazily started on
        first call. The KWin window picker (if shown) happens during
        discovery, not here.
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
        """Stop the background capture pipeline, if running.

        If grab_game() was never called (so the capture pipeline never
        started and never took ownership of closing the session -- see
        WaylandCaptureWorker.run()), close the ScreenCastSession directly
        here so it isn't left open/orphaned.
        """
        if self._capture_pipeline is not None:
            self._capture_pipeline.stop()
            self._capture_pipeline = None
        else:
            self._session.close()

        app = QGuiApplication.instance()
        if app is not None:
            try:
                app.applicationStateChanged.disconnect(self._on_application_state_changed)
            except (RuntimeError, TypeError):
                pass

    def __del__(self):
        self.stop()

    def get_overlay_area(self) -> QGraphicsItem:
        raise NotImplementedError(
            "Wayland overlay not implemented yet (see ROADMAP.md Phase 5)"
        )
