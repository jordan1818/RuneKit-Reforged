"""
Manual validation script for ROADMAP.md Phase 2 (Screen capture pipeline).

Unlike Phase 0's spike/wayland/*.py scripts (which validated the portal/
PipeWire mechanics in isolation with throwaway code), this script exercises
the actual production classes added in Phase 2
(runekit/game/wayland/portal.py, capture.py, instance.py) so a successful
run here is direct evidence the real implementation works end-to-end.

This project has no automated test suite (see CLAUDE.md/ROADMAP.md -- the
Phase 0 spikes are the only precedent, and they are manual/interactive
too), so this script -- run and visually verified by a human on a real KDE
Plasma Wayland session -- is the validation step for Phase 2 before it is
marked complete in ROADMAP.md.

Setup (one-time, on the Bazzite/KDE Plasma Wayland machine):
    pip install --user PyGObject
    # System requirement: xdg-desktop-portal + xdg-desktop-portal-kde
    # running, plus a GStreamer install with the pipewiresrc element (see
    # spike/wayland/test_pipewire_capture.py's docstring for package names).

Run:
    python -m runekit.game.wayland.manual_validate_capture

    # First run: a KWin window-picker dialog should appear; pick a window
    # (e.g. RuneScape, or any window for a quick smoke test).
    # Re-run: should NOT prompt again (uses the QSettings-persisted
    # restore_token, unlike the Phase 0 spike's JSON file).

To capture more/fewer frames or change the output directory:
    python -m runekit.game.wayland.manual_validate_capture --count 10 --out-dir /tmp/runekit-phase2

Reset the persisted restore_token and force the picker to show again:
    python -m runekit.game.wayland.manual_validate_capture --cleanup

What to check after running:
    1. The window picker appeared on the first run only.
    2. The printed frame shapes look sane (match the picked window's size).
    3. The saved PNGs in --out-dir visually show the picked window's live
       contents (open them and compare against the actual window).
    4. Re-running the script does NOT show the picker again.
Report back GO/NO-GO (and any error output) so ROADMAP.md Phase 2 can be
marked complete or fixed.
"""
import argparse
import os
import sys
import tempfile

from PIL import Image
from PySide6.QtCore import QCoreApplication, QSettings

from .capture import PipeWireCapture
from .portal import ScreenCastSession, clear_restore_token


def _init_settings():
    # Match runekit/main.py's QApplication setup so the restore_token is
    # persisted to (and reused from) the same settings file the real app
    # uses, and so QSettings() (a no-arg constructor) has an organization/
    # application name to work with.
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv)
    app.setOrganizationName("cupco.de")
    app.setOrganizationDomain("cupco.de")
    app.setApplicationName("RuneKit")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)


def _save_bgra(image, out_path: str):
    size = (image.shape[1], image.shape[0])
    Image.frombuffer(
        "RGBA", size, image[:, :, [2, 1, 0, 3]].tobytes(), "raw", "RGBA", 0, 1
    ).save(out_path)


def run(count: int, out_dir: str):
    session = ScreenCastSession()
    capture = PipeWireCapture()

    print("Opening ScreenCast portal session (production ScreenCastSession)...")
    try:
        fd, node_id = session.open()
    except Exception as exc:
        print(f"RESULT: failed to open ScreenCast session: {exc}")
        print("=> NO-GO: see traceback above / re-run with more logging if needed.")
        raise

    print(f"Got PipeWire fd={fd} node_id={node_id}")

    print("Starting PipeWireCapture pipeline...")
    capture.start(fd, node_id)

    frames = []
    for i in range(count):
        frame = capture.get_latest_frame(timeout_sec=10.0)
        if frame is None:
            print(f"RESULT: no frame received for frame {i} within 10s")
            break
        print(f"Pulled frame {i}: shape={frame.shape} dtype={frame.dtype}")
        frames.append(frame)

    capture.stop()
    session.close()

    if not frames:
        print("RESULT: portal/capture started but no frames were received.")
        print("=> NO-GO: check node_id/fd validity and GStreamer pipewiresrc availability.")
        return

    os.makedirs(out_dir, exist_ok=True)
    for i, frame in enumerate(frames):
        out_path = os.path.join(out_dir, f"phase2_frame_{i:03d}.png")
        _save_bgra(frame, out_path)
        print(f"Saved {out_path} (shape={frame.shape})")

    print(f"\nRESULT: captured {len(frames)} live frame(s) via the production Phase 2 code path.")
    print(f"Open the PNGs in {out_dir} and confirm they show the picked window's live contents.")
    print("=> Report back GO/NO-GO so ROADMAP.md Phase 2 can be marked complete.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Clear the QSettings-persisted restore_token and exit (no D-Bus/Gst calls made)",
    )
    parser.add_argument("--count", type=int, default=5, help="Number of frames to capture")
    parser.add_argument(
        "--out-dir",
        default=os.path.join(tempfile.gettempdir(), "runekit-phase2-frames"),
        help="Directory to write captured PNG frames to",
    )
    args = parser.parse_args()

    _init_settings()

    if args.cleanup:
        clear_restore_token()
        print("Cleared persisted restore_token (QSettings). Next run will show the picker.")
        return

    run(count=args.count, out_dir=args.out_dir)


if __name__ == "__main__":
    main()
