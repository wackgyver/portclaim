# Platform separation — proposal

**Status: proposed layout and release gates, not a completed directory migration.**
The native-touchpad work adds guarded Linux implementations to the existing tree.
Do not mistake Linux validation or a review-branch push for a tested Windows release.

## Decision recommended

Keep **one PortClaim repository, one hub, and one versioned wire contract**.
Separate the receiver's OS backends, dependencies, packaging, tests, and release
artifacts. Omarchy is a Linux integration profile, not a second claim fabric.

A separate `portclaim-omarchy` repository would duplicate the hub/protocol or
require a third shared package immediately. Fixing framing, authentication, or
compatibility would then require coordinated releases across repositories. The
receivers are small enough that enforceable module boundaries are the simpler
way to protect Windows while improving Linux.

Separate repositories become reasonable if the receivers acquire independent
maintainers, permissions, or substantially different release cadences. In that
case, first extract a versioned protocol/control-client package; do not copy the
hub into two products.

## Current boundaries

| Surface | Windows | Linux |
|---------|---------|-------|
| Trackpad | Existing `GestureEngine` + SendInput | `native_touchpad.py` + uinput/libinput; explicit legacy fallback |
| Microphone | Existing WASAPI + waveOut fallback / VB-CABLE | `_serve_linux()` native-rate PCM / PipeWire |
| Xbox | vgamepad / ViGEm | vgamepad / uinput |
| Window lifecycle | Existing visible-window lifecycle | Optional AppIndicator, close-to-tray, show/hide signals |
| Build/install | `PortClaim.spec`, `deploy/*Receiver*.ps1` | Existing Arch bootstrap plus separately configured local service |

The native selector cannot activate on Windows, even if
`USB_LOOM_TP_BACKEND=native` is present. Linux-only tray initialization is guarded.
Native trackpad claims opt into a slower idle heartbeat; Windows/legacy claims
retain their original timer cadence. TP10/N1 preserves the legacy packet prefix.

Codecs, claim handling, configuration validation, and Xbox changed-state/watchdog
logic are shared. Therefore, **the entire Windows application is not byte-for-byte
unchanged**. Shared changes need tests for both platforms. `wasapi_out.py`, the
legacy gesture engine, and Windows deployment scripts were not rewritten by the
native feature.

`client/test_platform_boundaries.py` checks backend selection, claim flags,
Windows window lifecycle, WASAPI routing, waveOut fallback, and Linux dispatch
with mocked I/O. Gamepad unit tests use plain ABI constants instead of importing
vgamepad, whose Windows import connects to the real ViGEm bus. These are contract
tests, not proof that a frozen executable works with Windows drivers.

## Proposed layout

```text
hub/                         # One Linux USB capture/control service
proto/                       # Shared codecs and compatibility contract
client/
  common/                    # Claims, transport, pure shared helpers
  windows/                   # SendInput, WASAPI/waveOut, ViGEm adapters/UI
  linux/                     # Native touchpad, PipeWire, tray/lifecycle
  receiver_app.py             # Thin platform selection + common shell
integrations/
  omarchy/                   # Opt-in Hyprland profile and desktop instructions
deploy/
  hub/
  windows/                   # Windows installer/build and dependency set
  linux/                     # Linux launcher, systemd unit, mic ownership helper
tests/                       # See test partitions below
```

The `tests/` partitions should be `common/`, `hub/`, `windows/`, and `linux/`.
The layout is illustrative: retain compatibility entry points while moving code,
and fix package imports/build manifests rather than moving files blindly.

### Boundary rules

- Common code must not import Windows DLLs, GTK, PipeWire, evdev, or a driver that
  connects to hardware at import time. Select adapters at the application edge.
- Native Linux input must not call the legacy gesture engine. If the explicit
  Linux legacy fallback remains, share its pure gesture logic, not Windows APIs.
- Windows must not require Linux GUI/input packages; Linux must not install
  ViGEm, VB-CABLE, or Windows DLLs. Keep separate dependency/build manifests.
- Compositor gestures and Omarchy keybindings belong in opt-in integration files,
  not the shared protocol, hub, or Windows UI.
- Shared hub/protocol changes require backward-compatibility fixtures, including
  old receivers reading the TP10/N1 prefix and legacy heartbeat behavior.

## GitHub workflow and releases

Use a single integration branch (`main`) after both platform gates are in place.
Use short-lived feature/review branches—not permanent `windows` and `linux`
branches that accumulate separate protocol fixes.

Recommended independent GitHub Actions checks:

1. **Protocol/common:** codecs, claim schema, platform-selection contracts,
   malformed packets, sequencing, and legacy compatibility.
2. **Linux/hub:** mocked evdev/uinput/audio tests, shell/unit validation, dependency
   isolation, and packaging. Hardware/libinput acceptance remains a separate test.
3. **Windows:** platform tests and a frozen executable build/import smoke check.
   Keep driver-free tests separate from ViGEm/VB-CABLE hardware integration.

Publish distinct release assets from the same reviewed source revision, for
example `portclaim-windows-x64.zip` and `portclaim-linux-x86_64.tar.gz`, with a hub
compatibility declaration. Mark a platform preview as such rather than implying
that a successful build on the other OS validates it. No workflows, branch
protection, repository creation, or release publication are implemented by this
proposal.

## Safe migration sequence

1. Preserve the working native implementation on a review branch. Keep the
   existing default branch until the platform gates have actually passed.
2. Bring Linux packaging into line with the intended runtime (below), in an
   independently reviewable change. Do not alter Windows deployment while doing it.
3. Extract OS adapters with no intentional behavior change; keep existing command
   entry points and the wire protocol stable. Add import-boundary/dependency tests.
4. Add independent CI/build jobs and release artifacts. Test Windows on Windows;
   Linux mocks alone cannot certify its frozen application or device behavior.
5. Merge only after required gates and the relevant hardware smoke checks. Avoid
   redeploying the hub for a receiver-only organizational change.

### Known Linux packaging gap

The checked-in `deploy/install-receiver.sh` predates the safer configured runtime.
It uses system/user Python rather than the isolated receiver venv, does not install
the optional tray dependencies/launcher, grants `input` group membership, starts
a separate microphone service that changes the default source, and installs a
receiver unit with automatic restart. Its live env template is installed mode 0644.
It must **not** be rerun over an existing working installation as a harmless update.

The next Linux packaging change should provide generic, credential-free versions
of the single-instance launcher, owned temporary microphone helper, desktop entry,
and start-hidden service; private env mode 0600; isolated dependencies; explicit
permission checks; no login autostart or restart loop by default; and no global
microphone/keybinding changes. A tray failure must retain visible controls.
Keep device/profile changes opt-in and test installation/rollback on a clean host.
Machine-specific credentials, SSH keys, and live user configuration remain outside
this repository.
