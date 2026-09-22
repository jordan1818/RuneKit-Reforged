# Wayland Support Roadmap (Plan A: Wayland + X11)

Status: Phase 0 complete (all spikes GO). Phase 1 complete (session
detection & scaffolding implemented). Phase 2 complete (screen capture
pipeline implemented and validated on a real KDE Plasma Wayland machine).
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

- **Decision: PyGObject only, no `pywayland`.** Window discovery, screen
  capture, and the global hotkey are all implemented on top of
  `xdg-desktop-portal-kde` D-Bus portals, driven entirely through
  PyGObject's `Gio` (GDBus) and `Gst`/`GstApp` bindings. This replaces the
  originally proposed `pywayland` (for `plasmawindowmanagement` binding and
  a hand-rolled layer-shell client) and the separate `dbus-next`/`pydbus`
  D-Bus library — one dependency (`PyGObject`) covers D-Bus, GStreamer, and
  PipeWire consumption instead of three. See `spike/wayland/` for the
  validation scripts and logs backing this decision.
- `xdg-desktop-portal-kde` supports the `ScreenCast` portal with a `WINDOW`
  source type (not just full-monitor) and `restore_token`/`persist_mode` to
  avoid re-prompting the user on every launch.
  **Validated (GO):** `spike/wayland/test_screencast_portal.py` confirms the
  full `CreateSession` → `SelectSources(types=WINDOW)` → `Start` →
  `OpenPipeWireRemote` flow works via PyGObject's `Gio`, the KWin window
  picker is shown for `WINDOW` source type, and the `restore_token` is
  honored on subsequent runs (no re-prompt). Note: `CreateSession`'s options
  vardict must include a `session_handle_token` in addition to
  `handle_token`, or the portal backend fails the call with
  `GDBus.Error:org.freedesktop.DBus.Error.NoReply: Remote peer disconnected`.
- PipeWire stream consumption via PyGObject's `Gst`/`GstApp`
  (`pipewiresrc ! videoconvert ! appsink`) is used instead of a manual
  PipeWire/GStreamer CLI integration.
  **Validated (GO):** `spike/wayland/test_screencast_full_pipeline.py`
  chains the portal picker with a live `pipewiresrc` pipeline end-to-end and
  captured real frames (2560×1394 in the reference run) from the picked
  window, confirming the buffer → numpy BGRA32 conversion math is correct.
- `xdg-desktop-portal-kde` supports the `GlobalShortcuts` portal (since
  Plasma 6.1), usable for the global Alt+1 hotkey currently implemented via
  raw X `KeyPress` events in `runekit/game/x11/instance.py`.
  **Validated (GO):** `spike/wayland/test_globalshortcuts_portal.py`
  confirms `CreateSession` → `BindShortcuts` → `Activated` signal delivery
  works via PyGObject's `Gio` (same `session_handle_token` requirement as
  `ScreenCast.CreateSession` applies here too).
- Qt6's `QGuiApplication.screen().grabWindow()` (used by the shared capture
  mixin in `runekit/game/qt.py`) does not work under Wayland and must not be
  reused for the Wayland backend's screen capture.
- **Window discovery relies solely on the `ScreenCast` portal's own `WINDOW`
  picker** (cached via `restore_token`), not `plasmawindowmanagement`. This
  means there is no live compositor-pushed geometry/focus-change event
  source on Wayland — see Phase 3 and Non-goals below for the accepted
  tradeoff.
- KWin's layer-shell integration (`layershellv1integration`), which could
  back the desktop-wide overlay in `runekit/game/overlay.py`, was **not**
  spiked (no `pywayland` client was built). Phase 5 needs its own
  feasibility pass to decide how the overlay is implemented without
  `pywayland` — see Phase 5 below.

## Phases

### Phase 0 — Spike / feasibility validation ✅ COMPLETE — all GO

Ran on the reference Bazzite/KDE Plasma Wayland machine using ad hoc
`pip install --user PyGObject` (no `pyproject.toml` change, per `CLAUDE.md`).
Scripts and logs are in `spike/wayland/`:

- `test_screencast_portal.py` — **GO**. `ScreenCast` `CreateSession` →
  `SelectSources(types=WINDOW)` → `Start` (KWin window picker shown) →
  `OpenPipeWireRemote`, all via PyGObject's `Gio`. `restore_token` persisted
  and honored on re-run (no re-prompt).
- `test_pipewire_capture.py` — **GO (partial/self-test)**. Validated the
  `Gst`/`GstApp` appsink-pull + BGRA32 numpy conversion path in isolation
  using `videotestsrc`, ahead of wiring up the real PipeWire stream.
- `test_screencast_full_pipeline.py` — **GO (end-to-end)**. Chained the
  portal picker with a live `pipewiresrc ! videoconvert ! appsink` pipeline
  in one process and captured 5 real frames from the picked window
  (2560×1394 in the reference run), saved as PNGs and visually confirmed.
  This is the primary evidence for the Phase 2 capture pipeline decision.
- `test_globalshortcuts_portal.py` — **GO**. `GlobalShortcuts`
  `CreateSession` → `BindShortcuts` → 5x `Activated` signal deliveries for a
  bound Alt+1 shortcut, via PyGObject's `Gio`.
- `plasmawindowmanagement`/layer-shell spikes (both `pywayland`-based) were
  **not run** — per project-owner decision, `pywayland` is dropped from the
  plan entirely rather than validated, and the unrun spike scripts were
  later deleted from `spike/wayland/`. Window discovery uses the
  `ScreenCast` picker instead (see Feasibility notes and Phase 3); the
  layer-shell overlay question is deferred to Phase 5.

**Fix note:** all `CreateSession` calls (`ScreenCast` and `GlobalShortcuts`)
must include a `session_handle_token` string in the options vardict in
addition to `handle_token`, or the call fails with
`GDBus.Error:org.freedesktop.DBus.Error.NoReply: Remote peer disconnected`
(the portal backend cannot construct the `Session` object without it).

**Outcome / decision:** proceed with **PyGObject as the sole new
dependency** for Phases 2–4 (`Gio` for all portal D-Bus calls, `Gst`/
`GstApp` for PipeWire consumption). Do not add `pywayland`, `dbus-next`, or
`pydbus`. Window discovery (Phase 3) is based entirely on the `ScreenCast`
portal's own picker + cached `restore_token`, not `plasmawindowmanagement`.

### Phase 1 — Session detection & scaffolding ✅ COMPLETE

- Extended `runekit/game/__init__.py::get_platform_manager()` with a
  `_is_wayland_session()` helper that detects a Wayland session
  (`XDG_SESSION_TYPE=wayland` / `WAYLAND_DISPLAY` set) and routes to a new
  `WaylandGameManager`, falling back to `X11GameManager` for X11/XWayland
  sessions (unchanged). The `darwin` → `QuartzGameManager` branch is
  untouched. No new dependency was needed for this phase.
- Scaffolded the `runekit/game/wayland/` package
  (`runekit/game/wayland/manager.py`, `runekit/game/wayland/instance.py`)
  with stub `WaylandGameManager`/`WaylandGameInstance` implementations
  satisfying the existing `GameManager`/`GameInstance` ABCs.
  `WaylandGameManager` currently reports no instances and logs a warning
  that Wayland support is a work in progress; `WaylandGameInstance` raises
  `NotImplementedError` (pointing at the relevant later phase) for
  geometry/scaling/focus/capture/overlay methods, to be implemented in
  Phases 2–5.
- Fixed an unrelated pre-existing bug found while validating this phase:
  `runekit/__init__.py` had an accidental duplicate/misplaced copy of
  `get_platform_manager()` (importing from nonexistent
  `runekit/instance.py`/`runekit/manager.py`) that broke `import runekit`
  entirely; restored it to empty, matching upstream.
- Validated (on a non-Linux dev machine, since real portal/KWin behavior
  isn't exercised until Phase 2+): `import runekit`/`import runekit.game`
  succeed, `_is_wayland_session()` returns the correct result for
  `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY` set, and X11/no-env-var
  cases, `get_platform_manager()` routes to `WaylandGameManager` under a
  simulated Wayland session, and the stub classes instantiate and satisfy
  the ABCs without error. The X11 and macOS branches are unaffected.

### Phase 2 — Screen capture pipeline ✅ COMPLETE

- Implemented the `org.freedesktop.portal.ScreenCast` D-Bus session flow
  (`CreateSession` → `SelectSources` → `Start` → `OpenPipeWireRemote`) in
  `runekit/game/wayland/portal.py` (`ScreenCastSession`/`PortalRequest`)
  using PyGObject's `Gio.DBusProxy`/`Gio.bus_get_sync` (GDBus) exclusively —
  no `dbus-next`/`pydbus`. `CreateSession`'s options vardict includes both
  `handle_token` and `session_handle_token` (see Phase 0 fix note above).
  `import gi` is deferred into methods so the module imports cleanly on
  platforms/sessions without PyGObject installed.
- Consume the PipeWire stream via PyGObject's `Gst`/`GstApp` bindings
  (`pipewiresrc ! videoconvert ! appsink`) in
  `runekit/game/wayland/capture.py` (`PipeWireCapture`), converting frames
  to the numpy BGRA32 format expected by `GameInstance.grab_game`/
  `grab_desktop`/`grab_region`. Runs on a background `QThread`
  (`WaylandCaptureWorker`/`WaylandCapturePipeline`), mirroring the existing
  `X11GameManager`/`X11EventWorker` pattern, so the blocking D-Bus/portal
  picker/appsink calls don't stall Qt's event loop.
- `WaylandGameInstance.grab_game()` (`runekit/game/wayland/instance.py`)
  lazily starts the capture pipeline on first call and returns the cached
  latest frame respecting `refresh_rate`; `grab_desktop()`/`grab_region()`
  crop from that same window-stream frame via the existing `np_crop` helper
  (there is no separate `MONITOR`-type portal session in this phase).
  `stop()`/`__del__` close the portal session and Gst pipeline on teardown.
- Persist and reuse the `restore_token` via `QSettings`
  (`wayland/screencastRestoreToken`, mirroring how other settings are
  stored in `runekit/host/settings.py`) to avoid re-prompting the user
  every launch, replacing the Phase 0 spike's JSON-file storage.
- **Fix note:** `GstApp` must be actually imported (`from gi.repository
  import Gst, GstApp`), not merely passed to `gi.require_version()`, or
  PyGObject never attaches the `GstApp.AppSink` overrides (e.g.
  `try_pull_sample`) to the appsink element, causing `AttributeError:
  'GstAppSink' object has no attribute 'try_pull_sample'` even though the
  pipeline itself builds and reaches `PLAYING` state successfully. This
  only surfaced on a real KDE Plasma Wayland machine, since the Phase 0
  spike script imported `GstApp` directly and never hit it.
- Validated end-to-end on a real Bazzite/KDE Plasma Wayland machine via
  `runekit/game/wayland/manual_validate_capture.py` (manual validation
  script exercising the production classes above; this project has no
  automated test suite, so this mirrors how the Phase 0 spikes were
  validated): the window picker prompted once, the `QSettings`-persisted
  `restore_token` avoided re-prompting on subsequent runs, 5 frames were
  captured at 2560×1394 (matching the Phase 0 reference capture) with no
  errors after the `GstApp` fix above, and the saved PNGs were opened and
  visually confirmed to show the picked window's live contents.

### Phase 3 — Window discovery, geometry, focus tracking

- Window discovery uses the `ScreenCast` portal's own `WINDOW`-source picker
  exclusively (no `plasmawindowmanagement`/`pywayland`): prompt via
  `SelectSources(types=WINDOW)` + `Start()` on first launch, then persist
  and reuse the `restore_token` (per Phase 2) so subsequent launches reuse
  the same window without re-prompting. Provide a UI action to re-pick the
  window (discarding the stored `restore_token`) if the user needs to
  target a different game window/instance.
- Accept the resulting limitation: there is no live compositor-pushed
  `positionChanged`/`focusChanged` event source. Implement `get_position`
  and `get_scaling` from the stream's negotiated size/metadata at
  session-start time (static until the user re-picks); implement
  `is_focused` on a best-effort basis (e.g. treating the instance as
  focused whenever the RuneKit process itself has input focus, since an
  unprivileged Wayland client cannot query another window's activation
  state without a privileged protocol). Document this as a known Wayland
  limitation rather than attempting to fully replicate X11 behavior.

### Phase 4 — Global hotkey (Alt+1)

- Implement an `org.freedesktop.portal.GlobalShortcuts` session
  (`CreateSession` → `BindShortcuts`), listen for the `Activated` signal, and
  emit the existing `GameInstance.alt1_pressed` signal — using PyGObject's
  `Gio` exclusively, same pattern as Phase 2. Validated in
  `spike/wayland/test_globalshortcuts_portal.py`. Requires
  `xdg-desktop-portal-kde` >= Plasma 6.1; detect and gracefully no-op (log a
  warning) if the portal interface is unavailable on older Plasma.

### Phase 5 — Desktop-wide overlay

- **Open question, needs its own spike before implementation**: with
  `pywayland` dropped from the plan (see Phase 0/Feasibility notes), the
  layer-shell approach originally proposed for the click-through,
  always-on-top overlay is no longer assumed. Before implementing this
  phase, spike whether Qt6's own window flags (`FramelessWindowHint`,
  `WindowTransparentForInput`, `WindowStaysOnTopHint` — the same flags
  `DesktopWideOverlay` already uses on X11) produce an acceptable
  click-through/always-on-top overlay under KWin's Wayland compositor
  without any layer-shell protocol. If not acceptable, revisit whether a
  minimal hand-written Wayland client for `wlr-layer-shell`/KDE's
  layer-shell integration is worth reintroducing `pywayland` for just this
  one phase, and get that dependency re-approved per `CLAUDE.md` if so.
- Implement a layer-shell-based (or Qt-flags-based, depending on the spike
  above) overlay matching the `DesktopWideOverlay` contract in
  `runekit/game/overlay.py` (`add_instance()` returning a `QGraphicsItem`
  plus a disconnect callback), so `Host`, `App`, and window/UI code require
  no changes.

### Phase 6 — Integration & regression

- Wire the Wayland backend into `Host`/`App`/UI code paths
  (`runekit/host/host.py`, `runekit/app/app.py`,
  `runekit/app/view/*`, `runekit/ui/*`).
- Verify the existing X11 path is completely unaffected by these changes.

### Phase 7 — Packaging

- Add Linux+Wayland-only extras to `pyproject.toml`: `PyGObject` (covers
  D-Bus via `Gio`/GDBus and GStreamer/PipeWire consumption via `Gst`/
  `GstApp` — see Phase 0/Feasibility notes) plus GStreamer/PipeWire plugin
  system dependencies (the `pipewiresrc` GStreamer element and its GI
  typelib). `pywayland`, `dbus-next`, and `pydbus` are **not** needed given
  the Phase 0 spike results, unless Phase 5's overlay spike determines a
  layer-shell client is required after all (see Phase 5).
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
  without a companion GNOME Shell extension. (Moot for window discovery
  specifically, since this roadmap no longer depends on
  `plasmawindowmanagement` at all — see Phase 3 — but the overlay's
  layer-shell question in Phase 5 would still be KDE/KWin-specific.)
- Live, compositor-pushed window geometry/focus-change tracking (what
  `plasmawindowmanagement` would have provided) is an explicit non-goal for
  the initial Wayland backend. `get_position()`/`is_focused()` are
  best-effort/static per Phase 3, refreshed only when the user re-picks the
  window. This may be revisited later if a need arises to bind
  `plasmawindowmanagement` (reintroducing `pywayland`, pending approval).
- Removing the X11 backend is not part of this roadmap (see Plan B in
  project discussion history) and should only be considered after the
  Wayland backend reaches parity on KDE Plasma.
