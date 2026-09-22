import logging
import threading
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal, Slot

from .portal import ScreenCastSession

logger = logging.getLogger(__name__)


class PipeWireCaptureError(Exception):
    """Raised when the PipeWire/Gst capture pipeline cannot be built or started."""


def gst_sample_to_bgra_np(sample) -> np.ndarray:
    """Convert a Gst.Sample (BGRA-negotiated caps) to a (h, w, 4) numpy array
    matching the BGRA32 format documented in DESIGN.md.

    Promoted unchanged (aside from import style) from
    spike/wayland/test_pipewire_capture.py, which validated this conversion
    both in isolation (videotestsrc self-test) and against real PipeWire
    frames from a picked window (test_screencast_full_pipeline.py).
    """
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    buf = sample.get_buffer()
    caps = sample.get_caps()
    structure = caps.get_structure(0)
    width = structure.get_value("width")
    height = structure.get_value("height")

    ok, mapinfo = buf.map(Gst.MapFlags.READ)
    if not ok:
        raise PipeWireCaptureError("Failed to map Gst buffer for reading")
    try:
        arr = np.frombuffer(mapinfo.data, dtype=np.uint8)
        stride = mapinfo.size // height
        arr = arr.reshape((height, stride // 4, 4))[:, :width, :]
        return arr.copy()
    finally:
        buf.unmap(mapinfo)


class PipeWireCapture:
    """Consumes a PipeWire video stream via PyGObject's Gst/GstApp bindings
    (`pipewiresrc ! videoconvert ! appsink`), per ROADMAP.md Phase 2.

    Only the latest frame is kept (appsink max-buffers=1 drop=true), matching
    the "always return the most recent frame" semantics of
    GameInstance.grab_game on the other backends.

    `import gi` is deferred to start() so importing this module does not
    require PyGObject/GStreamer to be installed unless a capture is actually
    started.
    """

    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._pipeline = None
        self._sink = None

    def start(self, pipewire_fd: int, node_id: int):
        """Build and start the Gst pipeline for the given PipeWire fd/node_id."""
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("GstApp", "1.0")
        # GstApp must actually be imported (not just require_version'd) for
        # PyGObject to attach the AppSink overrides (e.g. try_pull_sample)
        # to the appsink element returned by pipeline.get_by_name() below --
        # omitting this import causes
        # "AttributeError: 'GstAppSink' object has no attribute
        # 'try_pull_sample'" even though the pipeline itself builds fine.
        from gi.repository import Gst, GstApp  # noqa: F401

        Gst.init(None)

        launch = (
            f"pipewiresrc fd={pipewire_fd} path={node_id} ! "
            "videoconvert ! video/x-raw,format=BGRA ! "
            "appsink name=sink emit-signals=false sync=false "
            "max-buffers=1 drop=true"
        )
        self.logger.debug("Starting Gst pipeline: %s", launch)
        pipeline = Gst.parse_launch(launch)
        sink = pipeline.get_by_name("sink")

        pipeline.set_state(Gst.State.PLAYING)
        state_change = pipeline.get_state(Gst.SECOND * 5)
        self.logger.debug("Pipeline state change result: %s", state_change)

        self._pipeline = pipeline
        self._sink = sink

    def get_latest_frame(self, timeout_sec: float = 5.0) -> Optional[np.ndarray]:
        """Pull and return the most recent frame as a BGRA32 numpy array, or
        None if no frame arrived within timeout_sec."""
        if self._sink is None:
            raise PipeWireCaptureError("Capture pipeline is not started")

        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        sample = self._sink.try_pull_sample(int(timeout_sec * Gst.SECOND))
        if sample is None:
            return None

        return gst_sample_to_bgra_np(sample)

    def stop(self):
        """Stop and release the Gst pipeline, if running. Safe to call
        multiple times."""
        if self._pipeline is None:
            return

        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None
        self._sink = None


class WaylandCaptureWorker(QObject):
    """Runs the PipeWire pull loop on a background thread, consuming an
    already-opened ScreenCastSession.

    As of ROADMAP.md Phase 3, the ScreenCastSession is opened once by
    WaylandGameManager during window discovery (so the KWin window-picker
    prompt happens as part of discovery, not lazily on first grab_game()
    call), and the resulting session is handed to this worker to consume.
    This worker owns closing the session once capture stops.

    The Gst appsink pulls would otherwise stall Qt's own event loop, so this
    mirrors the QThread worker pattern already used by X11GameManager's
    event_thread/X11EventWorker (see runekit/game/x11/manager.py) rather
    than introducing a new pattern.
    """

    frame_ready = Signal()
    error = Signal(str)

    def __init__(self, session: ScreenCastSession, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._session = session
        self._capture = PipeWireCapture()
        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._stop_requested = threading.Event()

    @Slot()
    def run(self):
        try:
            self._capture.start(self._session.pipewire_fd, self._session.node_id)
        except Exception as exc:
            self.logger.error("Failed to start Wayland capture pipeline", exc_info=True)
            self.error.emit(str(exc))
            return

        while not self._stop_requested.is_set():
            try:
                frame = self._capture.get_latest_frame(timeout_sec=1.0)
            except Exception:
                self.logger.error("Error pulling frame from PipeWire", exc_info=True)
                break

            if frame is not None:
                with self._lock:
                    self._latest_frame = frame
                self.frame_ready.emit()

        self._capture.stop()
        self._session.close()

    def get_latest_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._latest_frame

    def stop(self):
        self._stop_requested.set()


class WaylandCapturePipeline(QObject):
    """Owns the background QThread + WaylandCaptureWorker pair for a single
    WaylandGameInstance's capture session.

    Takes an already-opened ScreenCastSession (opened synchronously by
    WaylandGameManager during window discovery, per ROADMAP.md Phase 3) so
    the KWin window-picker prompt happens once, at discovery time, rather
    than being deferred to the first grab_game() call.

    Usage mirrors X11GameManager's event_thread setup: construct, call
    start() once, poll get_latest_frame() from the Qt (main) thread, and
    call stop() on teardown.
    """

    def __init__(self, session: ScreenCastSession, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.last_error: Optional[str] = None

        self.thread = QThread(self)
        self.worker = WaylandCaptureWorker(session)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.thread.finished.connect(self.worker.deleteLater)
        self.worker.error.connect(self._on_error)

        self._started = False

    def _on_error(self, message: str):
        self.last_error = message
        self.logger.error("Wayland capture pipeline error: %s", message)

    def start(self):
        if self._started:
            return
        self._started = True
        self.thread.start()

    def get_latest_frame(self) -> Optional[np.ndarray]:
        return self.worker.get_latest_frame()

    def stop(self):
        if not self._started:
            return
        self.worker.stop()
        self.thread.quit()
        self.thread.wait()
        self._started = False
