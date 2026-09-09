# -*- mode: python ; coding: utf-8 -*-
"""Frozen PortClaim Receiver (windowed, no Python REPL)."""

from PyInstaller.utils.hooks import collect_submodules

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

a = Analysis(
    ["receiver_app.py"],
    pathex=["."],
    binaries=[],
    datas=[],
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
