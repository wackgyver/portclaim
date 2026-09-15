# Platform separation

**One repository, one hub, one shared protocol.** Receiver code, dependencies,
packaging and tests are separated by platform. Omarchy is an opt-in Linux
integration profile, not a fork of the claim fabric.

This describes the source layout and configured build gates. A local Linux test
run does not certify Windows, and adding a workflow does not mean it has run.
Do not merge/release a platform until its relevant gates actually pass.

## Layout and ownership

```text
hub/                         Shared Linux USB capture/control service (unchanged)
proto/                       SB10, AU10, legacy TP10 and native TP10/N1
client/
  common/                    HTTP claims, codecs/helpers, config, gesture math,
                             shared gamepad state/transport and stream guards
  windows/                   SendInput, WASAPI/waveOut, ViGEm factory, sidecar,
                             normal visible-window lifecycle
  linux/                     Native touchpad, PipeWire, uinput factories,
                             AppIndicator/signal lifecycle and native settings pane
  platforms.py               Lazy adapter selection; driver-free diagnostics
  receiver_app.py            Compatible CLI / application entry point
  ui.py                      Shared Tk mapping/claim shell
  legacy_ui.py               Shared legacy gesture controls
  {claim,mic_sink,trackpad_sink,xbox_sink}.py  Compatible CLI facades
integrations/omarchy/         Opt-in device profile; never auto-applied
deploy/linux/                User-only installer, launcher, unit and owned mic
deploy/windows/              Build/source installation, static dependency prep
tests/{common,hub,windows,linux}/
.github/workflows/platforms.yml
```

Hub deployment assets remain at their existing `deploy/` paths. Moving those
would add deployment risk without improving receiver isolation. Flat Windows
PowerShell/spec commands forward to `deploy/windows/`; the Linux shell installer
forwards to `deploy/linux/install.py`. Compatibility imports remain for moved
config, stream-guard, native-touchpad, tray and WASAPI modules.

## Boundary rules

- Common modules do not import Windows DLLs, Tk/GTK, evdev or driver packages.
  Gesture math takes an explicit output adapter; its default cannot inject input.
- The application edge selects adapters. Windows always uses the legacy gesture
  engine, even with `USB_LOOM_TP_BACKEND=native`. Native Linux bypasses that engine.
- Gamepad factories load vgamepad only when starting the sink. Import/build probes
  must not connect to ViGEm or create uinput devices.
- Linux runtime/source artifacts exclude Windows adapters. The Windows frozen
  build excludes Linux adapters and their input/tray dependencies.
- Shared UI has no Windows process/DLL or Linux tray/signal implementation.
  Linux native settings direct users to the compositor; Windows/legacy settings
  retain the existing gesture controls. Windows-only speaker monitoring stays
  absent from the Linux controls.
- Configuration loads explicitly at application entry, not on common API import.
  Credentials, filled env files and machine-specific settings are never packaged.

## Stable contracts

The extraction retains the legacy gesture algorithm, SendInput payloads,
WASAPI/waveOut path, shared gamepad mapping and native input protocol. Transport
bytes, hub capture and the HTTP API are not redesigned. The native codec supports
both package imports and existing flat hub installations.

Native trackpad claims still request `native_touchpad: true` and 100 ms idle
heartbeats; Windows/legacy retain the original cadence. TP10/N1 preserves the old
prefix/trailer. Common tests exercise the actual legacy receiver decoder, not
only the encoder. Receiver-only packaging work needs no hub restart.

Shared code is **not** platform-independent release evidence. Changes to claims,
configuration, gesture math, codecs or gamepad logic require both OS gates.
The UI extraction also fixes deferred error reporting: it captures an exception's
message before Tk invokes the callback, and reports claim failures instead of
leaving the operation busy. It does not change claim ownership semantics.

## Packaging safety

See [Linux installation/migration/rollback](../deploy/linux/README.md) and
[Windows build/installation](../deploy/windows/README.md).

The new Linux installer uses immutable source snapshots and reusable private
venvs, private env mode 0600, managed-file checks, explicit stopped upgrades and
rollback backups. It never grants input-group access, starts/enables services,
changes desktop audio defaults, applies Hyprland settings or changes claims.
Existing manual/legacy installations require a reviewed migration; do not run
this over a working installation as an automatic conversion. Older flat
`deploy/portclaim*.service`/virtmic assets are legacy, not the new installation.

Windows build dependency preparation verifies the upstream vgamepad archive and
copies only approved bindings/DLLs/license without running its MSI-launching
setup script. `-BuildOnly` never installs drivers or shortcuts. Explicit Windows
installation commands retain their system-changing driver/shortcut behavior and
are not CI steps.

## Independent validation gates

1. **Common:** Python 3.12/3.14 on Linux and Windows. Wire compatibility, gesture
   behavior, claim flags, configuration, gamepad mapping and strict import guards.
2. **Linux/hub:** mocked native input/audio/tray/capture, installation transactions,
   mic ownership, shell syntax, offline verification of the rendered systemd unit
   (including spaces/percent paths), dependency imports, deterministic source
   packaging and an unpacked artifact import probe. Mocked systemd/uinput tests are not clean-host desktop
   or real libinput acceptance.
3. **Windows:** mocked audio/input/lifecycle, actual Windows ctypes ABI, safe
   dependency prep, PowerShell parsing, frozen executable build and import probe.
   ViGEmBus, VB-CABLE, real SendInput and audio/device acceptance remain separate.

Local commands from the repository root:

```sh
python -m unittest discover -s tests/common -t .
python -m unittest discover -s tests/linux -t .    # Linux
python -m unittest discover -s tests/hub -t .      # Linux
python -m unittest discover -s tests/windows -t . # Windows; mocks can also run on Linux
python tools/check_linux_unit.py                 # Offline syntax only; no service changes
python tools/package_linux.py
```

`python client/receiver_app.py --check-platform /tmp/platform.json` imports the
selected adapters and UI, writes bounded non-secret diagnostics, and exits without
starting the receiver. Windows uses the same flag with an appropriate local path.

CI uploads separate **preview build artifacts**, not releases. The Linux archive
is source plus an installer, not a bundled cross-distribution binary; it needs
host libraries and dependency installation. The Windows artifact is the frozen
x64 directory. Keep `main` protected through review and actual required checks;
this change does not configure repository branch protection or publish a release.
Use short-lived review branches, not permanent divergent OS branches.

Split repositories only if independent maintainers/permissions/release cadences
justify it later; first extract a versioned shared protocol/control-client package
instead of duplicating the hub.
