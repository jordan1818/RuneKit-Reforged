"""
Phase 0 spike (PyGObject variant): consume a PipeWire video stream via
PyGObject's Gst/GstApp bindings (`pipewiresrc ! videoconvert ! appsink`,
per ROADMAP.md Phase 2) and convert a frame to the numpy BGRA32 format
expected by GameInstance.grab_game/grab_desktop/grab_region (see DESIGN.md).

This script takes an already-open PipeWire fd + node_id (as produced by
test_screencast_portal.py) rather than doing the portal dance itself, so it
can be iterated on independently. See test_screencast_full_pipeline.py for
the end-to-end version that does both steps in one process.

Setup (one-time, on the Linux/KDE Plasma Wayland machine):
    pip install --user PyGObject
    # System requirement: gstreamer1.0-plugins-good (or equivalent) providing
    # the `pipewiresrc` element, plus a GStreamer/GObject-Introspection
    # typelib for Gst (usually gir1.2-gstreamer-1.0 / gstreamer1.0-devel).

Run (fd must be a duplicable/inheritable fd from an active portal session --
easiest is to run this from the same process as the portal call, which is
exactly what test_screencast_full_pipeline.py does; this standalone script
is mainly useful for iterating on the Gst pipeline string / buffer conversion
in isolation using --self-test):

    python test_pipewire_capture.py --self-test
"""
import argparse
import os
import tempfile

import numpy as np

Gst = None
GstApp = None
GLib = None


def _ensure_gi():
    global Gst, GstApp, GLib
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstApp", "1.0")
    from gi.repository import Gst as _Gst, GstApp as _GstApp, GLib as _GLib

    _Gst.init(None)
    Gst = _Gst
    GstApp = _GstApp
    GLib = _GLib


def gst_sample_to_bgra_np(sample) -> np.ndarray:
    """Convert a Gst.Sample (BGRA-negotiated caps) to a (h, w, 4) numpy array
    matching the BGRA32 format documented in DESIGN.md."""
    buf = sample.get_buffer()
    caps = sample.get_caps()
    structure = caps.get_structure(0)
    width = structure.get_value("width")
    height = structure.get_value("height")

    ok, mapinfo = buf.map(Gst.MapFlags.READ)
    if not ok:
        raise RuntimeError("Failed to map Gst buffer for reading")
    try:
        arr = np.frombuffer(mapinfo.data, dtype=np.uint8)
        stride = mapinfo.size // height
        arr = arr.reshape((height, stride // 4, 4))[:, :width, :]
        return arr.copy()
    finally:
        buf.unmap(mapinfo)


def _np_save_bgra(image: np.ndarray, out_path: str):
    from PIL import Image

    size = (image.shape[1], image.shape[0])
    Image.frombuffer(
        "RGBA", size, image[:, :, [2, 1, 0, 3]].tobytes(), "raw", "RGBA", 0, 1
    ).save(out_path)


def build_pipeline(pipewire_fd=None, node_id=None, use_test_source=False) -> "Gst.Element":
    if use_test_source:
        # Isolated pipeline sanity check -- no portal/PipeWire session needed.
        # Validates the appsink pull + numpy conversion path on its own.
        launch = (
            "videotestsrc pattern=smpte num-buffers=5 ! "
            "video/x-raw,format=BGRA,width=320,height=240 ! "
            "appsink name=sink emit-signals=false sync=false max-buffers=1 drop=true"
        )
    else:
        if pipewire_fd is None or node_id is None:
            raise ValueError("pipewire_fd and node_id are required unless use_test_source=True")
        launch = (
            f"pipewiresrc fd={pipewire_fd} path={node_id} ! "
            "videoconvert ! video/x-raw,format=BGRA ! "
            "appsink name=sink emit-signals=false sync=false max-buffers=1 drop=true"
        )

    print(f"Gst pipeline: {launch}")
    return Gst.parse_launch(launch)


def pull_frames(pipeline, count=5, timeout_sec=10):
    sink = pipeline.get_by_name("sink")
    pipeline.set_state(Gst.State.PLAYING)

    state_change = pipeline.get_state(Gst.SECOND * 5)
    print(f"Pipeline state change result: {state_change}")

    frames = []
    try:
        for i in range(count):
            sample = sink.try_pull_sample(timeout_sec * Gst.SECOND)
            if sample is None:
                print(f"RESULT: no sample received for frame {i} within {timeout_sec}s")
                break
            frame = gst_sample_to_bgra_np(sample)
            print(f"Pulled frame {i}: shape={frame.shape} dtype={frame.dtype}")
            frames.append(frame)
    finally:
        pipeline.set_state(Gst.State.NULL)

    return frames


def run_self_test():
    """Validates the Gst pipeline + appsink + numpy conversion path using
    videotestsrc, with no PipeWire/portal session required. Useful for
    iterating on gst_sample_to_bgra_np() before wiring up the real capture."""
    _ensure_gi()
    pipeline = build_pipeline(use_test_source=True)
    frames = pull_frames(pipeline, count=3)

    if not frames:
        print("RESULT: self-test produced no frames.")
        print("=> NO-GO: appsink pull / numpy conversion path is broken (fix before real capture).")
        return

    out_path = os.path.join(tempfile.gettempdir(), "runekit-spike-selftest.png")
    _np_save_bgra(frames[-1], out_path)
    print(f"RESULT: self-test produced {len(frames)} frame(s). Last frame saved to {out_path}")
    print("Open that PNG and confirm it shows an SMPTE colour-bar test pattern.")
    print("=> GO (partial): Gst/GstApp appsink pull + BGRA numpy conversion works.")
    print("Next: run test_screencast_full_pipeline.py to validate the real pipewiresrc path.")


def run_with_pipewire(pipewire_fd: int, node_id: int, count: int = 5):
    _ensure_gi()
    pipeline = build_pipeline(pipewire_fd=pipewire_fd, node_id=node_id)
    frames = pull_frames(pipeline, count=count)

    if not frames:
        print("RESULT: no frames received from pipewiresrc.")
        print("=> NO-GO: check node_id/fd validity, and that the portal session is still open.")
        return

    out_path = os.path.join(tempfile.gettempdir(), "runekit-spike-pipewire.png")
    _np_save_bgra(frames[-1], out_path)
    print(f"RESULT: received {len(frames)} frame(s) from pipewiresrc. Last frame saved to {out_path}")
    print("Open that PNG and confirm it shows the picked window's live contents.")
    print("=> GO: pipewiresrc -> appsink -> numpy BGRA32 pipeline works via PyGObject/Gst.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run an isolated videotestsrc pipeline (no portal/PipeWire session needed)",
    )
    parser.add_argument("--fd", type=int, help="PipeWire fd from OpenPipeWireRemote")
    parser.add_argument("--node-id", type=int, help="PipeWire node_id from the Start() stream list")
    parser.add_argument("--count", type=int, default=5, help="Number of frames to pull")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return

    if args.fd is None or args.node_id is None:
        parser.error("--fd and --node-id are required unless --self-test is given")

    run_with_pipewire(args.fd, args.node_id, count=args.count)


if __name__ == "__main__":
    main()
