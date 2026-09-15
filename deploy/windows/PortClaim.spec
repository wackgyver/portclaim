# -*- mode: python ; coding: utf-8 -*-
"""Windows-only frozen receiver. This spec never imports a hardware driver."""
from pathlib import Path
import json
import hashlib
import sys

if sys.platform != "win32":
    raise RuntimeError("Build the Windows receiver on Windows")
root = Path(globals().get("PORTCLAIM_ROOT", Path(SPECPATH).resolve().parents[1]))
sys.path.insert(0, str(root))
from deploy.windows.prepare_vgamepad import REQUIRED, SHA256, VERSION
vendor = root / "build/windows-vendor"
marker = json.loads((vendor / "portclaim-vgamepad.json").read_text())
if (marker.get("archive_sha256") != SHA256 or marker.get("version") != VERSION
        or set(marker.get("files", {})) != REQUIRED):
    raise RuntimeError("Prepare the reviewed vgamepad dependency before building")
for name, checksum in marker["files"].items():
    if hashlib.sha256((vendor / name).read_bytes()).hexdigest() != checksum:
        raise RuntimeError("Prepared dependency was modified")

binaries = [(str(vendor / name), str(Path(name).parent))
            for name in marker["files"] if name.endswith(".dll")]
datas = [(str(vendor / "vgamepad.LICENSE"), "licenses")]
hidden = [
    "client.windows.audio", "client.windows.wasapi_out", "client.windows.input",
    "client.windows.gamepad", "client.windows.desktop", "client.windows.sidecar",
    "client.common.legacy_receiver", "client.common.legacy_gestures",
    "vgamepad", "vgamepad.win.virtual_gamepad", "vgamepad.win.vigem_client", "vgamepad.win.vigem_commons",
]
a = Analysis(
    [str(root / "client/receiver_app.py")],
    pathex=[str(root), str(root / "client"), str(vendor)],
    binaries=binaries, datas=datas, hiddenimports=hidden,
    hookspath=[str(root / "deploy/windows/hooks")],
    hooksconfig={}, runtime_hooks=[],
    excludes=["client.linux", "vgamepad.lin", "evdev", "libevdev", "gi", "pystray", "Xlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="PortClaim",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PortClaim")
