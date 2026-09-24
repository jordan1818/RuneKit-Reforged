import json
import logging
import os
import secrets
import tempfile
from typing import Optional

from PySide6.QtCore import QSettings

logger = logging.getLogger(__name__)

KWIN_SERVICE = "org.kde.KWin"
KWIN_SCRIPTING_PATH = "/Scripting"
KWIN_SCRIPTING_IFACE = "org.kde.kwin.Scripting"
# The per-script object registered by loadScript() is NOT at "/{id}" under
# the org.kde.kwin.Scripting interface -- KWin's source
# (src/scripting/scripting.cpp, AbstractScript::AbstractScript) registers it
# at "/Scripting/Script{id}" using a separate "org.kde.kwin.Script"
# interface (org.kde.kwin.Script.xml) that only exposes run()/stop().
# Confirmed via KWin 6 source inspection after this bit real-machine trouble
# ("GDBus.Error:...UnknownObject: No such object path '/2'") using the
# incorrect "/{id}" + "org.kde.kwin.Scripting" combination during the
# spike -- see spike/wayland/test_overlay_kwin_script.py and ROADMAP.md
# Phase 5.
KWIN_SCRIPT_IFACE = "org.kde.kwin.Script"

# Mirrors the QSettings key style used by portal.py's
# RESTORE_TOKEN_SETTINGS_KEY (e.g. "wayland/screencastRestoreToken").
KEEP_ABOVE_CONSENT_SETTINGS_KEY = "wayland/kwinKeepAboveConsent"

# Matches windows by their Qt windowTitle() (KWin's `caption`), NOT by
# resourceClass/resourceName (the Wayland app_id): RuneKit's app_id is
# shared across all of its windows (main app windows, settings dialog,
# this overlay), so matching by app_id would force EVERY RuneKit window
# always-on-top, not just the overlay. The caller is responsible for giving
# the target window a unique title before loading this script -- see
# WaylandGameManager._setup_overlay()'s OVERLAY_WINDOW_TITLE.
KEEP_ABOVE_SCRIPT_TEMPLATE = """
function applyKeepAbove(win) {
    if (!win) return;
    if (win.caption === %(title)s) {
        win.keepAbove = true;
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


def load_keep_above_consent() -> Optional[bool]:
    """Returns whether the user has previously consented to RuneKit loading
    the KWin always-on-top script, or None if they have not been asked yet.

    See ROADMAP.md Phase 5: KWin's scripting D-Bus interface has no
    compositor-level consent dialog of its own (unlike the
    xdg-desktop-portal portals used elsewhere in this backend), so RuneKit
    prompts once and remembers the answer via QSettings (mirroring
    portal.py's restore_token persistence).
    """
    settings = QSettings()
    value = settings.value(KEEP_ABOVE_CONSENT_SETTINGS_KEY, None)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


def save_keep_above_consent(consent: bool):
    settings = QSettings()
    settings.setValue(KEEP_ABOVE_CONSENT_SETTINGS_KEY, consent)


class KWinScriptError(Exception):
    """Raised when the KWin scripting D-Bus session cannot be established."""


class KeepAboveKWinScript:
    """Loads/unloads a small KWin JavaScript script at runtime, via KWin's
    own org.kde.kwin.Scripting/org.kde.kwin.Script D-Bus interfaces, to
    force `keepAbove = true` on a window matched by its title.

    Implements ROADMAP.md Phase 5's desktop-wide overlay always-on-top
    workaround: Qt.WindowType.WindowStaysOnTopHint (what DesktopWideOverlay
    already uses on X11 -- see runekit/game/overlay.py) is known-unreliable
    under KWin/Wayland (confirmed via spike/wayland/test_overlay_qt_flags.py
    on a real machine: it did not hold, including vs. a normal window).
    This class was promoted from spike/wayland/test_overlay_kwin_script.py,
    which validated (GO, real machine, incl. vs. a fullscreen window) that
    loading this KWin script instead reliably fixes always-on-top.

    Uses PyGObject's Gio (GDBus) exclusively -- the same dependency already
    approved and in use by portal.py/globalshortcuts.py for Phases 2-4, not
    a new dependency. `import gi` is deferred into methods, matching
    portal.py's convention, so importing this module does not require
    PyGObject to be installed on platforms/sessions that never construct a
    WaylandGameManager.

    No permanent trace is left in KWin's configuration: the script is
    written to a temp file and unload() removes it from KWin's runtime
    script list (nothing is written to ~/.config/kwinrulesrc).
    """

    def __init__(self, window_title: str):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.window_title = window_title
        self.plugin_name = "runekit-keepabove-" + secrets.token_hex(4)
        self._bus = None
        self._script_path: Optional[str] = None
        self._loaded = False

    def load_and_start(self):
        """Write the KWin script to a temp file, load it into KWin, and
        start it. Raises KWinScriptError on any failure (e.g. KWin's
        scripting interface is unavailable, or the D-Bus calls fail) --
        callers should catch this and continue without the always-on-top
        enhancement rather than crash the whole overlay setup."""
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        script_body = KEEP_ABOVE_SCRIPT_TEMPLATE % {
            "title": json.dumps(self.window_title)
        }
        fd, path = tempfile.mkstemp(prefix="runekit_keepabove_", suffix=".js")
        with os.fdopen(fd, "w") as f:
            f.write(script_body)
        self._script_path = path
        self.logger.debug("Wrote temp KWin script: %s", path)

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            raise KWinScriptError(f"Could not connect to session bus: {exc}") from exc
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
        if script_id < 0:
            raise KWinScriptError(
                f"loadScript returned {script_id} (a script named "
                f"{self.plugin_name!r} may already be loaded)"
            )
        self.logger.debug("loadScript returned script id: %s", script_id)

        script_object_path = f"/Scripting/Script{script_id}"
        try:
            script_proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None,
                KWIN_SERVICE, script_object_path, KWIN_SCRIPT_IFACE, None,
            )
            script_proxy.call_sync("run", None, Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error as exc:
            raise KWinScriptError(
                f"run() on loaded script (path={script_object_path}) failed: {exc}"
            ) from exc

        self._loaded = True
        self.logger.info(
            "Loaded KWin always-on-top script for window title %r (plugin=%s)",
            self.window_title,
            self.plugin_name,
        )

    def unload(self):
        """Unload the KWin script, if loaded. Safe to call multiple times."""
        if not self._loaded or self._bus is None:
            return

        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

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
            self.logger.debug("Unloaded KWin script %s", self.plugin_name)
        except GLib.Error as exc:
            self.logger.warning("Failed to unload KWin script: %s", exc)
        finally:
            self._loaded = False
            self._bus = None
            if self._script_path and os.path.exists(self._script_path):
                try:
                    os.unlink(self._script_path)
                except OSError:
                    pass
                self._script_path = None
