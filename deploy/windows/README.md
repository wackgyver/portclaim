# Windows receiver packaging

Windows adapters live in `client/windows/`; shared claims, codecs, gesture math,
configuration and gamepad state handling live in `client/common/` and `proto/`.
Linux GTK, AppIndicator, PipeWire, evdev and compositor integration are excluded
from the frozen build. Legacy `deploy/*Receiver*.ps1` and `client/PortClaim.spec`
commands forward to this directory.

## Build without installing drivers or changing the desktop

On Windows, with Python 3.12–3.14 (including Tk):

```powershell
python -m pip install -r deploy/windows/requirements-build.txt
.\deploy\Build-Receiver.ps1 -BuildOnly
# Windowed executable; wait for it before reading the diagnostic file:
Start-Process .\dist\PortClaim\PortClaim.exe -ArgumentList '--check-platform platform.json' -Wait
Get-Content platform.json
```

The diagnostic imports the common UI and Windows adapters without constructing
Tk, opening UDP sockets, claiming devices, playing audio, or connecting to ViGEm.
It is an import/packaging check, not a hardware test.

`vgamepad==0.1.0`'s upstream source installer can execute a ViGEm MSI on Windows.
Do **not** pip-install that sdist as a build prerequisite. `prepare_vgamepad.py`
downloads the pinned archive, verifies SHA256, and copies only an explicit list
of Windows Python bindings, client DLLs and the supplied license. It never runs
`setup.py`, MSI, or the binding itself. Existing modified vendor files are refused.
An offline `--archive` input is supported and still checksum-verified. PyInstaller
uses a static hook rather than importing the package to discover its files.

Build tools live in `requirements-build.txt`; the runtime binding version/hash
live in `prepare_vgamepad.py`. No Linux dependency manifest is used on Windows.

## Existing installation commands

Without `-BuildOnly`, `Build-Receiver.ps1` retains the explicit build-and-install
flow: mirror the artifact into `%LOCALAPPDATA%\portclaim\Receiver`, then install
shortcuts. Stop the Receiver yourself first. It does not start the app or install
drivers. The dedicated Receiver directory is mirrored: keep private files outside it.

`Install-Receiver.ps1` remains an **explicit system-changing Windows installation**:
it copies the source package (now including common/Windows subpackages), installs
ViGEmBus if missing, prepares the binding, and writes the existing elevated
shortcuts. It is never invoked by CI. VB-CABLE installation remains separately
explicit through `Install-VBCABLE.ps1`. Review upstream driver installers and any
reboot request; a successful build does not imply these drivers are installed.
Private `USB_LOOM_*` configuration and existing trackpad settings remain external.

## Release gates and rollback

CI separately runs common tests on Windows, Windows mocked adapter/ABI tests,
PowerShell parsing, a frozen build and its import probe. CI artifacts are previews,
not automatically published releases. Run real SendInput, ViGEmBus/controller,
WASAPI/VB-CABLE, fallback audio and claim switching checks on a Windows test host
before declaring runtime support for a revision.

Before installation, back up the dedicated Receiver directory and source package.
Rollback while stopped by restoring the previous matching artifact/source plus
binding, preserving private configuration. Do not roll back or reinstall system
drivers merely to revert receiver code. The receiver-only split needs no hub restart.
