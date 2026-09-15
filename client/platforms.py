"""Application-edge platform selection. No device/driver is opened by selection."""
from __future__ import annotations

import os
import sys


def name():
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "linux":
        return "linux"
    raise OSError(f"PortClaim receiver does not support {sys.platform}")


def native_backend():
    return name() == "linux" and os.environ.get("USB_LOOM_TP_BACKEND", "native") == "native"


def audio_backend():
    if name() == "windows":
        from client.windows import audio
    else:
        from client.linux import audio
    return audio


def input_backend():
    if name() == "windows":
        from client.windows import input as backend
    elif native_backend():
        from client.linux import native_touchpad as backend
    else:
        from client.linux import legacy_input as backend
    return backend


def gamepad_backend():
    if name() == "windows":
        from client.windows import gamepad
    else:
        from client.linux import gamepad
    return gamepad


def desktop_backend():
    if name() == "windows":
        from client.windows import desktop
    else:
        from client.linux import desktop
    return desktop


def diagnostics():
    """Driver-free packaging smoke: import only the selected adapters."""
    return {
        "platform": name(), "native_touchpad": native_backend(),
        "audio_backend": audio_backend().__name__, "input_backend": input_backend().__name__,
        "gamepad_backend": gamepad_backend().__name__, "desktop_backend": desktop_backend().__name__,
        "driver_imported": "vgamepad" in sys.modules,
        "linux_backend_imported": any(n.startswith("client.linux.") for n in sys.modules),
        "windows_backend_imported": any(n.startswith("client.windows.") for n in sys.modules),
    }
