"""
Phase 5 spike (Track A, CHECK 1c): validate a fully self-contained,
programmatic alternative to CHECK 1b's manual KWin window-rule workaround --
loading a small KWin JavaScript script at runtime via KWin's own
org.kde.kwin.Scripting D-Bus interface to force `keepAbove = true` on the
overlay window, with no permanent config file and no manual System Settings
steps required from the user.

Why this exists: test_overlay_qt_flags.py's CHECK 1/2 found that
Qt.WindowType.WindowStaysOnTopHint does not reliably keep the overlay above
other windows under KWin/Wayland (see ROADMAP.md Phase 5 -- this is a known
upstream limitation, not a bug in that spike). CHECK 1b showed a *manual*
KWin window rule ("Keep above other windows", forced) can work around this,
but requires the user to open System Settings and configure it by hand --
not self-contained. This script automates the equivalent effect using
KWin's documented scripting D-Bus API instead:

    org.kde.kwin.Scripting.loadScript(path, pluginName) -> scriptId
    org.kde.kwin.Scripting (at /{scriptId}).run()
    org.kde.kwin.Scripting.unloadScript(pluginName)

The KWin script itself just matches windows by resourceClass (the Wayland
app_id) and sets `window.keepAbove = true`, both for windows already open
(workspace.windowList()) and any created afterward (workspace.windowAdded).
This uses PyGObject's Gio (GDBus) -- the same dependency already approved
and used by portal.py/globalshortcuts.py for Phases 2-4 -- NOT a new
dependency. No permanent file is left on disk (the script is written to a
temp file and unloaded from KWin on exit); nothing is written to
~/.config/kwinrulesrc.

If this works reliably (including against a fullscreen window), it means
RuneKit's Wayland overlay can implement "always on top" by prompting the
user once for consent (e.g. a QMessageBox, mirroring the accessibility
prompt pattern in runekit/game/quartz/manager.py) and then loading/unloading
this KWin script for the lifetime of the app -- no pywayland, no permanent
KWin config changes, and no manual user setup.

Setup (one-time, on the Linux/KDE Plasma Wayland machine):
    pip install --user PyGObject   # same as the Phase 0-4 spikes

Run:
    python test_overlay_kwin_script.py [--duration SECONDS] [--forever]

    # No manual KWin configuration needed. Watch the console for
    # "keepAbove set on ..." printed from inside the KWin script (proves
    # the script loaded and matched the window). Then manually judge:
    #   - does the overlay stay above a normal window?
    #   - does the overlay stay above a FULLSCREEN window (the real RS3
    #     scenario)?
    # Ctrl+C or wait for --duration to exit; the KWin script is unloaded
    # automatically on exit either way (also best-effort on Ctrl+C/crash).

Cleanup the spike-only dependency when done:
    pip uninstall PyGObject
"""
import argparse
import json
import logging
import os
import secrets
import signal
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import test_overlay_qt_flags as overlay_spike  # noqa: E402 - reuse OverlaySpikeWindow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("test_overlay_kwin_script")

APP_ID = "runekit-overlay-spike"
KWIN_SERVICE = "org.kde.KWin"
KWIN_SCRIPTING_PATH = "/Scripting"
KWIN_SCRIPTING_IFACE = "org.kde.kwin.Scripting"

Gio = None
GLib = None


def _ensure_gi():
    global Gio, GLib
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import Gio as _Gio, GLib as _GLib

    Gio = _Gio
    GLib = _GLib


KWIN_SCRIPT_TEMPLATE = """
function applyKeepAbove(win) {
    if (!win) return;
    if (win.resourceClass === %(app_id)s || win.resourceName === %(app_id)s) {
        win.keepAbove = true;
        print("runekit-overlay-spike: keepAbove set on " + win.caption);
    }
}

var existing = (typeof workspace.windowList === "function")
    ? workspace.windowList()
    : workspace.clientList();
for (var i = 0; i < existing.length; i++) {
    applyKeepAbove(existing[i]);
}

workspace.windowAdded.connect(applyKeepAbove);
"""


class KWinScriptError(Exception):
    """Raised when the KWin scripting D-Bus session cannot be established."""


class KWinScriptSession:
    """Loads, starts, and unloads a temporary KWin JavaScript script via
    org.kde.kwin.Scripting, using PyGObject's Gio (GDBus) -- same dependency
    already approved/used by portal.py/globalshortcuts.py, no new pip
    package. Nothing is written outside a temp file, and unload() removes
    the script from KWin's runtime script list (no persistent KWin config
    is touched, unlike CHECK 1b's manual window-rule workaround)."""

    def __init__(self, app_id: str):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.app_id = app_id
        self.plugin_name = "runekit-overlay-spike-" + secrets.token_hex(4)
        self._bus = None
        self._script_path: Optional[str] = None
        self._loaded = False

    def load_and_start(self):
        _ensure_gi()

        script_body = KWIN_SCRIPT_TEMPLATE % {"app_id": json.dumps(self.app_id)}
        fd, path = tempfile.mkstemp(prefix="runekit_overlay_spike_", suffix=".js")
        with os.fdopen(fd, "w") as f:
            f.write(script_body)
        self._script_path = path
        self.logger.info("Wrote temp KWin script: %s", path)

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._bus = bus

        try:
            scripting_proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None,
                KWIN_SERVICE, KWIN_SCRIPTING_PATH, KWIN_SCRIPTING_IFACE, None,
            )
        except GLib.Error as exc:
            raise KWinScriptError(
                f"Could not create proxy for {KWIN_SCRIPTING_IFACE}: {exc}"
            ) from exc

        try:
            reply = scripting_proxy.call_sync(
                "loadScript",
                GLib.Variant("(ss)", (path, self.plugin_name)),
                Gio.DBusCallFlags.NONE, -1, None,
            )
        except GLib.Error as exc:
            raise KWinScriptError(f"loadScript failed: {exc}") from exc

        script_id = reply.unpack()[0]
        self.logger.info("loadScript returned script id: %s", script_id)

        try:
            script_proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None,
                KWIN_SERVICE, f"/{script_id}", KWIN_SCRIPTING_IFACE, None,
            )
            script_proxy.call_sync("run", None, Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error as exc:
            raise KWinScriptError(f"run() on loaded script failed: {exc}") from exc

        self._loaded = True
        self.logger.info(
            "KWin script started (plugin_name=%s). Watch for "
            "'runekit-overlay-spike: keepAbove set on ...' below.",
            self.plugin_name,
        )

    def unload(self):
        if not self._loaded or self._bus is None:
            return

        try:
            scripting_proxy = Gio.DBusProxy.new_sync(
                self._bus, Gio.DBusProxyFlags.NONE, None,
                KWIN_SERVICE, KWIN_SCRIPTING_PATH, KWIN_SCRIPTING_IFACE, None,
            )
            scripting_proxy.call_sync(
                "unloadScript",
                GLib.Variant("(s)", (self.plugin_name,)),
                Gio.DBusCallFlags.NONE, -1, None,
            )
            self.logger.info("Unloaded KWin script %s", self.plugin_name)
        except GLib.Error as exc:
            self.logger.warning("Failed to unload KWin script: %s", exc)
        finally:
            self._loaded = False
            if self._script_path and os.path.exists(self._script_path):
                try:
                    os.unlink(self._script_path)
                except OSError:
                    pass


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--duration", type=float, default=30.0,
        help="Seconds to show the overlay before auto-exit (default: 30)",
    )
    parser.add_argument(
        "--forever", action="store_true",
        help="Don't auto-exit; keep the overlay up until Ctrl+C.",
    )
    args = parser.parse_args()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setDesktopFileName(APP_ID)
    signal.signal(signal.SIGINT, lambda *_: app.quit())

    overlay_spike.log_environment()

    window = overlay_spike.OverlaySpikeWindow()
    window.show()

    kwin_session = KWinScriptSession(APP_ID)
    load_ok = True
    try:
        kwin_session.load_and_start()
    except KWinScriptError as exc:
        load_ok = False
        logger.error("Failed to load/start KWin script: %s", exc)
        print("\n=> NO-GO (CHECK 1c setup): could not load a KWin script via D-Bus.")
        print(f"   Error: {exc}")

    print("\n" + "=" * 70)
    if load_ok:
        print("Overlay shown + KWin script loaded (no manual configuration done).")
        print("Look for 'runekit-overlay-spike: keepAbove set on ...' above --")
        print("that confirms the script loaded AND matched this window.")
        print("Now manually judge:")
        print("  - does the overlay stay ABOVE a normal window?")
        print("  - does the overlay stay ABOVE a FULLSCREEN window (real RS3 case)?")
    if args.forever:
        print("Running with --forever: press Ctrl+C to exit.")
    else:
        print(f"Auto-exiting in {args.duration:.0f}s, or press Ctrl+C.")
    print("=" * 70 + "\n")

    if not args.forever:
        QTimer.singleShot(int(args.duration * 1000), app.quit)

    exit_code = app.exec()

    kwin_session.unload()

    print("\nKWin script unloaded (no permanent trace left in KWin config).")
    print("Record whether 'keepAbove' held reliably (incl. vs. fullscreen)")
    print("in ROADMAP.md's Phase 5 section as CHECK 1c's result.")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
