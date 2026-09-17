# Wayland Support Roadmap (Plan A: Wayland + X11)

Status: Not started
Target: KDE Plasma (KWin) on Wayland, additive to existing X11 support

## Background

RuneKit Reforged currently only supports X11 on Linux. Distros/images that
default to Wayland-only sessions (e.g. Bazzite with KDE Plasma) have no
supported path today — `runekit/game/x11/*` relies on X11-specific
primitives (XComposite/XShm window capture, raw X window-tree enumeration,
`_NET_ACTIVE_WINDOW` focus tracking, `BypassWindowManagerHint` overlays) that
have no direct equivalent on Wayland.

The existing `game` package design (see `DESIGN.md`) already anticipates
this: `GameManager`/`GameInstance` in `runekit/game/manager.py` and
`instance.py` are abstract base classes specifically so a new platform can be
supported by adding a sibling implementation (like `runekit/game/x11/` and
`runekit/game/quartz/`) without changing `app/`, `ui/`, or `host/` code.

This roadmap tracks adding a `runekit/game/wayland/` implementation targeting
KDE Plasma/KWin (the compositor in use on the reference Wayland machine),
while keeping the X11 backend fully intact for existing users.

## Feasibility notes (KWin-specific)

- KWin implements its own layer-shell integration (`layershellv1integration`)
  which can back the desktop-wide, click-through, always-on-top overlay
  currently implemented with X11 window flags in `runekit/game/overlay.py`.
- KWin implements the KDE-specific `plasmawindowmanagement` protocol (not
  `wlr-foreign-toplevel-management`) for enumerating toplevels, geometry, and
  focus/activation state. It is not yet confirmed whether an unprivileged
  client can bind this protocol under KWin's `restrictedInterfaces`
  allowlist — this must be validated before committing to it as the primary
  window-discovery mechanism.
- `xdg-desktop-portal-kde` supports the `ScreenCast` portal with a `WINDOW`
  source type (not just full-monitor) and `restore_token`/`persist_mode` to
  avoid re-prompting the user on every launch.
- `xdg-desktop-portal-kde` supports the `GlobalShortcuts` portal (since
  Plasma 6.1), usable for the global Alt+1 hotkey currently implemented via
  raw X `KeyPress` events in `runekit/game/x11/instance.py`.
- Qt6's `QGuiApplication.screen().grabWindow()` (used by the shared capture
  mixin in `runekit/game/qt.py`) does not work under Wayland and must not be
  reused for the Wayland backend's screen capture.

## Phases

### Phase 0 — Spike / feasibility validation

- Build a minimal `pywayland` client that binds `plasmawindowmanagement`
  under KWin and confirm read access is not blocked by KWin's
  restricted-interfaces allowlist.
- Build a minimal layer-shell surface to validate how it integrates with a
  PySide6/Qt process (in-process vs. a separate helper Wayland client).
- Exit criteria: go/no-go decision on `plasmawindowmanagement` for window
  discovery; if blocked, design the manual window-picker fallback (driven by
  the ScreenCast portal's own picker) instead.

### Phase 1 — Session detection & scaffolding

- Extend `runekit/game/__init__.py::get_platform_manager()` to detect a
  Wayland session (`XDG_SESSION_TYPE=wayland` / `WAYLAND_DISPLAY` set) and
  route to a new `WaylandGameManager`, falling back to `X11GameManager` for
  X11/XWayland sessions.
- Scaffold `runekit/game/wayland/` package with stub `GameManager` /
  `GameInstance` implementations satisfying the existing ABCs.

### Phase 2 — Screen capture pipeline

- Implement the `org.freedesktop.portal.ScreenCast` D-Bus session flow
  (`CreateSession` → `SelectSources` → `Start` → `OpenPipeWireRemote`).
- Consume the PipeWire stream (GStreamer `pipewiresrc` + appsink or
  equivalent) and convert frames to the numpy BGRA32 format expected by
  `GameInstance.grab_game`/`grab_desktop`/`grab_region`.
- Persist and reuse the `restore_token` (e.g. via `QSettings`, mirroring how
  other settings are stored in `runekit/host/settings.py`) to avoid
  re-prompting the user every launch.

### Phase 3 — Window discovery, geometry, focus tracking

- If Phase 0 is green: implement `get_position`, `get_scaling`, `is_focused`,
  and change notifications using `plasmawindowmanagement` toplevel events.
- If Phase 0 is red: implement the manual window-picker fallback, caching the
  user's selection for subsequent launches.

### Phase 4 — Global hotkey (Alt+1)

- Implement an `org.freedesktop.portal.GlobalShortcuts` session
  (`CreateSession` → `BindShortcuts`), listen for the `Activated` signal, and
  emit the existing `GameInstance.alt1_pressed` signal.

### Phase 5 — Desktop-wide overlay

- Implement a layer-shell-based overlay matching the `DesktopWideOverlay`
  contract in `runekit/game/overlay.py` (`add_instance()` returning a
  `QGraphicsItem` plus a disconnect callback), so `Host`, `App`, and
  window/UI code require no changes.

### Phase 6 — Integration & regression

- Wire the Wayland backend into `Host`/`App`/UI code paths
  (`runekit/host/host.py`, `runekit/app/app.py`,
  `runekit/app/view/*`, `runekit/ui/*`).
- Verify the existing X11 path is completely unaffected by these changes.

### Phase 7 — Packaging

- Add Linux+Wayland-only extras to `pyproject.toml`: `pywayland`, a D-Bus
  library (`dbus-next` or `pydbus`), `PyGObject`, and GStreamer/PipeWire
  plugin dependencies.
- Update `Makefile`, `deploy/runekit-appimage.sh`, and
  `.github/workflows/build.yml` to bundle the new runtime dependencies
  (GI typelibs, GStreamer PipeWire plugin) into the AppImage build.
- **Any new/changed dependency (Python package, system library, or CI/build
  tool) must be proposed to and approved by the project owner before being
  added** — see `CLAUDE.md`.

### Phase 8 — Docs & testing

- Update `README.md` and `compatibility.md` with a per-compositor support
  matrix instead of the current blanket "Wayland is not supported" language.
- Manually validate the full flow (capture, window detection/fallback,
  hotkey, overlay) on a real Bazzite/KDE Plasma Wayland session.

## Non-goals (for this roadmap)

- GNOME/Mutter support is out of scope — GNOME does not expose an
  equivalent to `plasmawindowmanagement`/layer-shell for arbitrary apps
  without a companion GNOME Shell extension.
- Removing the X11 backend is not part of this roadmap (see Plan B in
  project discussion history) and should only be considered after the
  Wayland backend reaches parity on KDE Plasma.
