import logging
import time
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QCursor, QGuiApplication
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
        self._stopped = False

        # Cached, static geometry. There is no on-screen x/y for WINDOW-type
        # ScreenCast streams, so this is always anchored at (0, 0) -- a
        # known Wayland limitation, not a bug. If the portal supplied a
        # stream size up front, use it; otherwise (observed in practice:
        # xdg-desktop-portal-kde often omits "size" for WINDOW streams --
        # see portal.py) leave it unresolved and derive it lazily from the
        # first captured PipeWire frame in get_position() -- see
        # ROADMAP.md Phase 3.
        if session.stream_size:
            width, height = session.stream_size
            self._position: Optional[QRect] = QRect(0, 0, width, height)
        else:
            self._position = None

        self._is_focused = (
            QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        )
        QGuiApplication.instance().applicationStateChanged.connect(
            self._on_application_state_changed
        )

    def get_position(self) -> QRect:
        if self._position is None:
            self._resolve_position_from_frame()

        return self._position or QRect(0, 0, 0, 0)

    def _resolve_position_from_frame(self):
        """Derive the static (0, 0, w, h) position from the first captured
        PipeWire frame, when the portal did not supply a stream "size"
        property up front (observed in practice with WINDOW-type streams
        on xdg-desktop-portal-kde -- see portal.py/ScreenCastSession).

        This starts the capture pipeline early (normally lazy, on first
        grab_game() call) so a position is available before any frame has
        actually been requested by a caller. Safe to call repeatedly; it
        is a no-op once resolved.
        """
        try:
            self._ensure_capture_started()
        except Exception:
            self.logger.warning("Could not start capture pipeline", exc_info=True)
            return

        # The capture pipeline starts asynchronously on a background
        # QThread (portal D-Bus round trip + Gst pipeline startup), so the
        # first frame is not available immediately -- poll briefly rather
        # than giving up after a single check.
        frame = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            frame = self._capture_pipeline.get_latest_frame()
            if frame is not None:
                break
            if self._capture_pipeline.last_error:
                self.logger.warning(
                    "Cannot derive window size: capture failed: %s",
                    self._capture_pipeline.last_error,
                )
                return
            time.sleep(0.1)

        if frame is not None:
            height, width = frame.shape[0], frame.shape[1]
            self._position = QRect(0, 0, width, height)
            self.logger.debug("Resolved window size from captured frame: %dx%d", width, height)
        else:
            self.logger.warning("Timed out waiting for a frame to derive window size")

    def get_scaling(self) -> float:
        # No QWindow handle exists for a portal-picked window (unlike
        # X11's QWindow.fromWinId), so there is no way to look up "the
        # screen this window is actually on" directly. KDE Plasma Wayland
        # supports independent per-monitor scaling (unlike X11's
        # global-only scaling), so on a multi-monitor setup, always using
        # QGuiApplication.primaryScreen() would silently report the wrong
        # value whenever the game isn't on the primary display.
        #
        # Best-effort improvement (still an approximation, per ROADMAP.md
        # Phase 3): use whichever screen the mouse cursor is currently on,
        # since that's a much better live proxy for "the screen the user
        # is looking at/interacting with" than a fixed primary-display
        # assumption -- falling back to primaryScreen() if the cursor
        # isn't over any known screen (e.g. it moved off all outputs
        # momentarily).
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
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

        Idempotent: safe to call multiple times (e.g. once explicitly and
        again from __del__ at interpreter shutdown). PySide6 reports a
        failed disconnect() via a RuntimeWarning rather than a catchable
        exception, so this guards against double-disconnecting with an
        explicit flag instead of relying on try/except.
        """
        if self._stopped:
            return
        self._stopped = True

        if self._capture_pipeline is not None:
            self._capture_pipeline.stop()
            self._capture_pipeline = None
        else:
            self._session.close()

        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.disconnect(self._on_application_state_changed)

    def __del__(self):
        self.stop()

    def get_overlay_area(self) -> QGraphicsItem:
        raise NotImplementedError(
            "Wayland overlay not implemented yet (see ROADMAP.md Phase 5)"
        )
