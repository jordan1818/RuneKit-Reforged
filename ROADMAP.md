# Wayland Support Roadmap (Plan A: Wayland + X11)

Status: Phase 0 complete (all spikes GO). Phase 1 complete (session
detection & scaffolding implemented). Phase 2 complete (screen capture
pipeline implemented and validated on a real KDE Plasma Wayland machine).
Phase 3 complete (window discovery, static geometry, best-effort focus
tracking implemented and validated on a real KDE Plasma Wayland machine).
Phase 4 complete (global Alt+1 hotkey implemented and validated on a real
KDE Plasma Wayland machine).
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

### Phase 3 — Window discovery, geometry, focus tracking ✅ COMPLETE

- Window discovery uses the `ScreenCast` portal's own `WINDOW`-source picker
  exclusively (no `plasmawindowmanagement`/`pywayland`): `WaylandGameManager`
  (`runekit/game/wayland/manager.py`) lazily opens a `ScreenCastSession`
  (`SelectSources(types=WINDOW)` + `Start()`) the first time
  `get_instances()`/`get_active_instance()` is called, then persists and
  reuses the `restore_token` (per Phase 2) so subsequent launches reuse the
  same window without re-prompting. The opened session's fd/node_id are
  handed directly to the resulting `WaylandGameInstance`
  (`runekit/game/wayland/instance.py`), which passes them into
  `WaylandCapturePipeline`/`WaylandCaptureWorker`
  (`runekit/game/wayland/capture.py`) — so the picker now runs once, at
  discovery time, instead of being deferred to the first `grab_game()` call
  as it was in the Phase 2 implementation. A `repick_window()` method
  (`Host.repick_window()` / a conditional "Re-pick game window" tray menu
  action in `runekit/ui/tray.py`, shown only when
  `Host.supports_repick_window()` is true) discards the stored
  `restore_token` and the current instance (emitting `instance_removed`),
  forcing the picker again on the next discovery call.
- `ScreenCastSession.open()` (`runekit/game/wayland/portal.py`) parses the
  `size (ii)` property out of the picked stream's properties vardict
  (`streams[0][1]`) into `ScreenCastSession.stream_size`, when present. Per
  the upstream `org.freedesktop.portal.ScreenCast` interface docs, both
  `position` and `size` are **optional** stream properties, and `position`
  is only meaningful for monitor streams.
  **Fix note (found during real-machine validation):** `size` is also, in
  practice, frequently **absent** for `WINDOW`-type streams on
  `xdg-desktop-portal-kde` (it's primarily populated for `MONITOR`
  streams) — an initial implementation that only used `stream_size` and
  fell back to `(0, 0, 0, 0)` when it was missing produced a useless
  `get_position()` for every real window pick. `WaylandGameInstance` now
  treats `stream_size` as an optional fast path only: when absent, it
  starts the capture pipeline early and derives the window's `(w, h)` from
  the shape of the first real captured PipeWire frame instead (bounded to
  a 5s wait), which is always accurate regardless of portal metadata
  availability.
- Accepted the resulting limitation: there is no live compositor-pushed
  `positionChanged`/`focusChanged` event source. `get_position()` returns a
  static `QRect(0, 0, width, height)` (the `(0, 0)` origin is a known
  limitation, documented in code, not a bug — anchoring elsewhere would be
  equally arbitrary without real compositor coordinates); `is_focused()`
  is best-effort, treating the instance as focused whenever RuneKit's own
  `QGuiApplication.applicationState()` is `ApplicationActive` (wired via
  `applicationStateChanged` to still emit the existing `focusChanged` Qt
  signal, so `overlay.py`/`GameManager.get_active_instance()` keep working
  unchanged) — since an unprivileged Wayland client cannot query another
  window's activation state without a privileged protocol. This is a known
  Wayland limitation rather than an attempt to fully replicate X11
  behavior.
- **`get_scaling()` — known upstream limitations (not fully fixable from
  RuneKit's side) and current mitigation.** `get_scaling()` has no
  `QWindow` handle for a portal-picked window (unlike X11's
  `QWindow.fromWinId`), so it cannot look up that window's actual screen
  directly, and two independent, real-hardware-confirmed KDE Plasma
  Wayland platform bugs make any automatic screen/scale lookup unreliable
  on multi-monitor setups (found while validating on a 3-display
  (2 real monitors + a 4K dummy HDMI output) reference machine):
  - **kscreen's "primary display" assignment is unreliable on 3+ monitor
    Wayland setups.** Explicitly setting a different primary display in
    KDE System Settings did not take effect, and after a further change it
    was observed on a different, still-incorrect output — a known class of
    upstream `kscreen`/KWin bug on multi-monitor Wayland configurations,
    not something RuneKit's code can influence or correct.
  - **Qt's Wayland QPA rounds fractional `devicePixelRatio()` up to the
    next integer.** With a real display set to 125% scaling,
    `screen.devicePixelRatio()` reported `2.0` instead of `1.25` — a known
    Qt-on-Wayland limitation (fractional scale support requires the
    specific window to have negotiated `wp_fractional_scale_v1`, which
    Qt's default integer-rounding path does not do), not something fixable
    without patching Qt itself.
  - **`QCursor.pos()` cannot return a real global position on native
    Wayland for a client with no window under the pointer** (Wayland's
    security model does not let a client query the desktop-wide pointer
    position without actual pointer focus). An earlier revision of
    `get_scaling()` used `QGuiApplication.screenAt(QCursor.pos())` as its
    primary heuristic; real-machine testing showed this returning a
    stale/incorrect position and screen, confirming this approach is
    fundamentally unsound on Wayland (not just wrong in the headless
    validation script) and it has been removed.
  - **Current implementation:** `get_scaling()` now checks, in order: (1)
    an optional manual override via the `RK_WAYLAND_SCALING` environment
    variable (e.g. `RK_WAYLAND_SCALING=1.25`), for a user to hardcode the
    correct value on a setup affected by either upstream bug above; (2)
    the `devicePixelRatio()` of whichever RuneKit top-level `QWindow` is
    currently visible, found via `QGuiApplication.topLevelWindows()` — a
    `QWindow`'s `screen()` is tracked reliably via real compositor
    surface-enter/leave events, unlike a cursor-position query, though it
    still inherits Qt's fractional-scale rounding bug above and only
    identifies "a RuneKit window's screen," not necessarily the game
    window's screen; (3) `QGuiApplication.primaryScreen()` as the final
    fallback (e.g. before any RuneKit window exists), which inherits both
    upstream bugs. Validated end-to-end (mocked portal calls, real Qt/
    PySide6) that all three paths select correctly and take priority in
    the documented order; the window-based and primary-screen paths were
    not exercised with a real fractional/mismatched-primary Wayland
    configuration (only confirmed via KDE Plasma Wayland real-machine
    testing that the two underlying platform bugs exist as described
    above), so `RK_WAYLAND_SCALING` remains the only fully-reliable
    mitigation for an affected multi-monitor Wayland user today.
- **Fix note:** `WaylandGameInstance.stop()` (called both explicitly by
  callers like `repick_window()`/`Host` teardown, and again from
  `__del__` at interpreter shutdown) must be idempotent. PySide6 reports a
  failed/double `disconnect()` via a `RuntimeWarning` printed directly to
  the log rather than a catchable `RuntimeError`/`TypeError`, so an
  initial `try/except` guard around
  `applicationStateChanged.disconnect(...)` silently failed to suppress
  it. Fixed with an explicit `self._stopped` flag so `stop()`'s body
  (including the disconnect) only ever runs once per instance.
- **Fix note:** `WaylandGameManager.get_active_instance()` originally
  filtered `get_instances()` by `is_focused()`, mirroring the X11/Quartz
  pattern where multiple instances can genuinely compete for focus. But
  this manager only ever tracks at most one instance (the `ScreenCast`
  portal only hands back one picked window per session), and
  `is_focused()` is a proxy for "does *RuneKit's own* window have OS
  focus" (see above) — which is essentially never simultaneously true
  with the game window having focus, since they're different
  applications. That combination made `get_active_instance()` return
  `None` in ordinary use, not just when no window is picked. Fixed:
  `get_active_instance()` now returns the sole tracked instance directly
  (or `None` only if discovery hasn't succeeded), without consulting
  `is_focused()`. (`Host.launch_app()` already had a fallback to
  `get_instances()[0]` when `get_active_instance()` was `None`, so this
  bug did not block app launching, but the `None` return itself was
  incorrect and worth fixing for any other code that may rely on it.)
- Validated end-to-end on a real Bazzite/KDE Plasma Wayland machine (two
  runs) via `runekit/game/wayland/manual_validate_discovery.py` (a manual
  validation script mirroring Phase 2's `manual_validate_capture.py`,
  since this project has no automated test suite): the window picker
  prompted once; `get_position()` correctly reports the real picked
  window's dimensions (derived from the captured frame, confirming the
  `stream_size` fix above); the disconnect `RuntimeWarning` is gone;
  `get_scaling()` correctly reported `1.0` on the reference machine's
  100%-scale display; `is_focused()` reported `False` (expected, since the
  script is a headless `QGuiApplication` with no visible window and thus
  never has OS focus -- not a bug, see the `is_focused()` proxy design
  above); and, after the `get_active_instance()` fix above,
  `get_active_instance()` correctly returned the same instance as
  `get_instances()[0]`.

### Phase 4 — Global hotkey (Alt+1) ✅ COMPLETE

- Implemented the `org.freedesktop.portal.GlobalShortcuts` session flow
  (`CreateSession` → `BindShortcuts`) in `runekit/game/wayland/globalshortcuts.py`
  (`GlobalShortcutsSession`), using PyGObject's `Gio` exclusively — same
  pattern as Phase 2/3's `ScreenCastSession` (`portal.py`), including reusing
  the shared `PortalRequest` helper (`portal.py`'s `_signature_for` gained a
  `BindShortcuts` entry). Listens for the `Activated` D-Bus signal and emits
  it as the existing `GameInstance.alt1_pressed` signal, connected in
  `WaylandGameInstance.__init__` (`runekit/game/wayland/instance.py`).
  `GlobalShortcutsSession.open()` is called synchronously and eagerly in
  `WaylandGameInstance.__init__` (unlike screen capture, which is lazy,
  since there's no other natural trigger for it) — this may show KDE's
  one-time shortcut-grant dialog. If the portal interface or `BindShortcuts`
  is unavailable/declined (e.g. pre-Plasma 6.1), `open()` raises
  `GlobalShortcutsPortalError`, which `WaylandGameInstance.__init__` catches
  and logs as a warning, continuing without the hotkey rather than breaking
  the rest of the Wayland backend. Validated in
  `spike/wayland/test_globalshortcuts_portal.py`.
- **Does not run at RuneKit startup.** `WaylandGameManager` never calls
  `get_instances()`/`get_active_instance()` eagerly (unlike
  `X11GameManager`, whose `__init__` does), so `WaylandGameInstance.__init__`
  — and therefore the `GlobalShortcuts` grant dialog above — only runs the
  first time something triggers window discovery (per Phase 3), typically
  the first time a user launches an app via `Host.launch_app()`. Plain
  RuneKit startup (tray icon only, no app launched) never touches the game
  manager at all, so this cannot block app startup itself; it blocks the
  first app-launch action the same way Phase 3's window picker already does,
  now with one more sequential dialog after it.
- **Fix note (threading model, found during real-machine validation): do
  not run the portal Request/Response wait or the `Activated` signal
  subscription on a background `QThread` with its own `GLib.MainLoop()`.**
  An initial implementation mirrored `WaylandCaptureWorker`'s (`capture.py`)
  QThread pattern, running the whole `CreateSession` → `BindShortcuts` →
  `Activated` flow on a background thread. This does not work: `GLib.MainLoop()`
  called with no explicit context always binds to the *global default*
  `GMainContext`, never to a thread-default context set via
  `push_thread_default()` (GLib's own docs note `push_thread_default()`
  "does not affect... the context used by functions like `g_idle_add()`",
  and `g_timeout_add()`/`g_main_loop_new(NULL)` follow the same rule). Qt's
  main thread already continuously iterates that same global default
  context once `app.exec()` is running (PySide6's glib event-dispatcher
  integration on Linux), so a second `GLib.MainLoop()` on a second thread
  trying to run that same global context is racy by construction. Pushing a
  private `GMainContext` on the worker thread does not fix this either:
  `Gio.DBusConnection.signal_subscribe()`'s callback dispatch *does* follow
  the subscribing thread's thread-default context, so doing so only made
  the `Response`/`Activated` subscriptions land in a context nothing was
  ever iterating, while the unmodified `PortalRequest`'s `loop.run()` kept
  waiting on the global default context instead. Observed symptom:
  `CreateSession` timed out after 60s with no error and no KDE dialog ever
  appeared, even though the equivalent flow worked in
  `spike/wayland/test_globalshortcuts_portal.py` (a standalone process with
  no competing Qt event loop, so no context contention). **Fixed** by
  dropping the background thread entirely: `GlobalShortcutsSession.open()`
  runs `CreateSession`/`BindShortcuts` synchronously on the caller's (main)
  thread, exactly like `ScreenCastSession.open()` already does successfully.
  No dedicated loop/thread is needed for the ongoing `Activated` listening
  either, since the signal subscription registered from the main thread
  lands on the global default context that Qt's own event loop is already
  driving for the app's whole lifetime.
- **Fix note (minor, `portal.py`):** `PortalRequest.call()`'s `finally`
  block unconditionally called `GLib.source_remove(timeout_id)`, but a fired
  timeout callback returning `False` already tells GLib to auto-destroy that
  source, so removing it again on the timeout path logged a harmless but
  noisy `Source ID N was not found when attempting to remove it` warning.
  Fixed with a `timed_out` flag so removal is only attempted on the normal
  (non-timeout) response path.
- Validated end-to-end on a real Bazzite/KDE Plasma Wayland machine via
  `runekit/game/wayland/manual_validate_hotkey.py` (a manual validation
  script mirroring Phases 2/3's `manual_validate_capture.py`/
  `manual_validate_discovery.py`, since this project has no automated test
  suite): the KDE shortcut-grant dialog appeared once, synchronously, before
  the script's "Bound successfully" message; each Alt+1 press printed
  exactly one `alt1_pressed` activation, with no dedicated background
  thread/main loop involved; and Ctrl+C stopped the script cleanly.

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
  - `layer-shell-qt` (KDE's own C++ `LayerShellQt::Window` component) was
    considered and rejected as a way to avoid the `pywayland` question: it
    has no PySide6/Shiboken binding, and while it does ship a QML plugin,
    adopting it would mean introducing a second UI paradigm (QML) alongside
    this project's existing `QGraphicsView`/Widgets-based overlay
    (`runekit/game/overlay.py`) just for one platform's surface role — a
    larger architectural change than either trying Qt's own flags first or
    reintroducing `pywayland` to bind `zwlr_layer_shell_v1` directly
    underneath the existing widget stack (via the target `QWindow`'s native
    handle) if Track A below turns out to be a NO-GO.
  - **Track A spike script prepared**: `spike/wayland/test_overlay_qt_flags.py`
    mirrors `DesktopWideOverlay`'s exact window-flag/attribute construction,
    draws a per-monitor visual test pattern plus a center click-through
    marker, and auto-detects the click-through failure mode via an
    installed input-event filter. It requires no new dependency (PySide6
    only, already in `pyproject.toml`) and needs to be run interactively on
    the real Bazzite/KDE Plasma Wayland machine to judge all 5 GO/NO-GO
    criteria documented in the script's module docstring (always-on-top vs.
    normal window, always-on-top vs. fullscreen, click-through,
    transparent compositing, multi-monitor spanning). Results are not yet
    recorded here — pending a real-machine run.
  - Also flagged (separately from the Track A/B decision):
    `DesktopWideOverlay.check_compatibility()`'s black-screen self-test uses
    `QGuiApplication.primaryScreen().grabWindow(0)`, which this roadmap's
    Feasibility notes already establish does not work on Wayland. This will
    need its own fix/bypass on the Wayland backend regardless of which
    overlay track is chosen.
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
