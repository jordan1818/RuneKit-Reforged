"""
Phase 0 spike: probe a wlr-layer-shell overlay surface under KWin
(the layer-shell equivalent of overlay.py's current BypassWindowManagerHint /
WindowTransparentForInput / WindowStaysOnTopHint combo).

Setup (one-time):
    pip install --user pywayland

    # Locate wlr-layer-shell-unstable-v1.xml, e.g.:
    #   /usr/share/wayland-protocols/unstable/wlr-layer-shell/wlr-layer-shell-unstable-v1.xml

    # Generate bindings once, into a local ./protocols package:
    python -m pywayland.scanner -i <path-to-xml> -o ./protocols
    python -m pywayland.scanner --with-protocols -o ./protocols  # also generates core wl_compositor/wl_shm

Run:
    python test_layer_shell.py

Cleanup after you're done with the spike:
    python test_layer_shell.py --cleanup
    pip uninstall pywayland
"""
import argparse
import mmap
import os
import shutil
import tempfile
import time
from pathlib import Path

PROTOCOLS_DIR = Path(__file__).parent / "protocols"

WIDTH, HEIGHT = 400, 300
ANCHOR_ALL = 1 | 2 | 4 | 8  # top | bottom | left | right
KEYBOARD_INTERACTIVITY_NONE = 0

compositor = None
layer_shell = None
shm = None
configured = False


def handle_global(registry, id_, interface, version):
    global compositor, layer_shell, shm
    from protocols.wayland import WlCompositor, WlShm
    from protocols.wlr_layer_shell_unstable_v1 import ZwlrLayerShellV1

    if interface == "wl_compositor":
        compositor = registry.bind(id_, WlCompositor, min(version, 4))
    elif interface == "wl_shm":
        shm = registry.bind(id_, WlShm, 1)
    elif interface == "zwlr_layer_shell_v1":
        print(f"FOUND zwlr_layer_shell_v1 (version={version}) -- binding...")
        layer_shell = registry.bind(id_, ZwlrLayerShellV1, min(version, 4))


def handle_configure(layer_surface, serial, width, height):
    global configured
    print(f"configure received: serial={serial} size={width}x{height}")
    layer_surface.ack_configure(serial)
    configured = True


def handle_closed(layer_surface):
    print("layer surface closed by compositor")


def cleanup_generated_bindings():
    if PROTOCOLS_DIR.exists():
        shutil.rmtree(PROTOCOLS_DIR)
        print(f"Removed generated bindings at {PROTOCOLS_DIR}")
    else:
        print(f"No generated bindings found at {PROTOCOLS_DIR}, nothing to clean up")


def run():
    from pywayland.client import Display
    from protocols.wayland import WlShm

    display = None
    layer_surface = None
    surface = None
    pool = None
    buf = None
    mm = None
    fd = None

    try:
        display = Display()
        display.connect()
        print("Connected to Wayland display")

        registry = display.get_registry()
        registry.dispatcher["global"] = handle_global
        display.roundtrip()

        if not compositor or not shm:
            print("RESULT: wl_compositor or wl_shm not available -- cannot continue.")
            return
        if not layer_shell:
            print("RESULT: zwlr_layer_shell_v1 NOT advertised by this compositor.")
            print("=> Layer-shell overlay approach is not usable here.")
            return

        from protocols.wlr_layer_shell_unstable_v1 import ZwlrLayerShellV1

        surface = compositor.create_surface()
        layer_surface = layer_shell.get_layer_surface(
            surface, None, ZwlrLayerShellV1.layer.overlay.value, "runekit-spike"
        )
        layer_surface.dispatcher["configure"] = handle_configure
        layer_surface.dispatcher["closed"] = handle_closed

        layer_surface.set_anchor(ANCHOR_ALL)
        layer_surface.set_exclusive_zone(-1)
        layer_surface.set_keyboard_interactivity(KEYBOARD_INTERACTIVITY_NONE)
        layer_surface.set_size(WIDTH, HEIGHT)
        surface.commit()

        print("Waiting for initial configure...")
        end = time.monotonic() + 5
        while not configured and time.monotonic() < end:
            display.dispatch(block=False)
            time.sleep(0.05)

        if not configured:
            print("RESULT: never received a configure event -- something is wrong.")
            return

        stride = WIDTH * 4
        size = stride * HEIGHT
        fd, path = tempfile.mkstemp(prefix="runekit-spike-")
        os.unlink(path)
        os.ftruncate(fd, size)
        mm = mmap.mmap(fd, size)
        mm.write(bytes((0x80, 0x00, 0xFF, 0x00)) * (WIDTH * HEIGHT))  # translucent green

        pool = shm.create_pool(fd, size)
        buf = pool.create_buffer(0, WIDTH, HEIGHT, stride, WlShm.format.argb8888.value)

        surface.attach(buf, 0, 0)
        surface.damage(0, 0, WIDTH, HEIGHT)
        surface.commit()

        print("Overlay should now be visible (semi-transparent green box).")
        print("Move another window under it and confirm you can still click through it.")
        print("Displaying for 15 seconds...")
        end = time.monotonic() + 15
        while time.monotonic() < end:
            display.dispatch(block=False)
            time.sleep(0.1)

        print("=> Record your observations for the go/no-go writeup.")
    finally:
        # Tear down Wayland protocol objects in reverse order of creation
        if buf is not None:
            try:
                buf.destroy()
            except Exception:
                pass
        if pool is not None:
            try:
                pool.destroy()
            except Exception:
                pass
        if layer_surface is not None:
            try:
                layer_surface.destroy()
            except Exception:
                pass
        if surface is not None:
            try:
                surface.destroy()
            except Exception:
                pass
        if display is not None:
            display.disconnect()
            print("Disconnected from Wayland display")

        # Release local resources
        if mm is not None:
            mm.close()
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove the generated ./protocols bindings directory and exit (no Wayland connection made)",
    )
    args = parser.parse_args()

    if args.cleanup:
        cleanup_generated_bindings()
        return

    run()
    print("\nReminder: run with --cleanup to remove generated bindings when done,")
    print("and `pip uninstall pywayland` to remove the spike-only dependency.")


if __name__ == "__main__":
    main()
