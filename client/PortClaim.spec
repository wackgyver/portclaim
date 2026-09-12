# -*- mode: python ; coding: utf-8 -*-
"""Frozen PortClaim Receiver (windowed, no Python REPL)."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

hidden = [
    "wasapi_out",
    "claim",
    "mic_sink",
    "trackpad_config",
    "trackpad_sink",
    "xbox_sink",
    "tkinter",
    "tkinter.ttk",
]
hidden += collect_submodules("tkinter")
hidden += collect_submodules("vgamepad")

vg_datas, vg_binaries, vg_hidden = collect_all("vgamepad")
hidden += vg_hidden


def _not_msi(item) -> bool:
    src = item[0] if isinstance(item, (list, tuple)) else item
    return not str(src).lower().endswith(".msi")


vg_datas = [item for item in vg_datas if _not_msi(item)]
vg_binaries = [item for item in vg_binaries if _not_msi(item)]

import vgamepad

_dll = Path(vgamepad.__file__).parent / "win" / "vigem" / "client" / "x64" / "ViGEmClient.dll"
_dll_dest = "vgamepad/win/vigem/client/x64"
if _dll.is_file() and not any(str(item[1]).replace("\\", "/") == _dll_dest for item in vg_binaries):
    vg_binaries.append((str(_dll), _dll_dest))

a = Analysis(
    ["receiver_app.py"],
    pathex=["."],
    binaries=vg_binaries,
    datas=vg_datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PortClaim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="PortClaim",
)
