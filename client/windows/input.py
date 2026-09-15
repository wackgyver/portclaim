"""SendInput and legacy Windows drag-threshold handling. No Linux imports."""
from __future__ import annotations
import atexit
import ctypes
from ctypes import Structure, Union, sizeof, wintypes
import sys
from client.common.input_codes import *
ULONG_PTR = ctypes.c_void_p
NAME = "SendInput"

SPI_GETDRAGWIDTH = 0x004C
SPI_GETDRAGHEIGHT = 0x004D
SPI_SETDRAGWIDTH = 0x004C
SPI_SETDRAGHEIGHT = 0x004D
OLE_PARK_PX = 20000
OLE_DRAG_DEFAULT = 4
OLE_PARK = True
_parked_drag: tuple[int, int] | None = None


class MOUSEINPUT(Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class KEYBDINPUT(Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class HARDWAREINPUT(Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]

class INPUT_UNION(Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

class INPUT(Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]

user32 = ctypes.windll.user32 if sys.platform == "win32" else None


def _send(inputs: list) -> None:
    if user32 is None or not inputs or INPUT is None:
        return
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, sizeof(INPUT))


def mouse_move(dx: int, dy: int) -> None:
    if dx == 0 and dy == 0:
        return
    ev = INPUT()
    ev.type = INPUT_MOUSE
    ev.union.mi.dx = int(dx)
    ev.union.mi.dy = int(dy)
    ev.union.mi.dwFlags = MOUSEEVENTF_MOVE
    _send([ev])


def mouse_btn(flags: int) -> None:
    ev = INPUT()
    ev.type = INPUT_MOUSE
    ev.union.mi.dwFlags = flags
    _send([ev])


def mouse_wheel(vertical: int = 0, horizontal: int = 0) -> None:
    evs = []
    if vertical:
        ev = INPUT()
        ev.type = INPUT_MOUSE
        ev.union.mi.mouseData = int(vertical)
        ev.union.mi.dwFlags = MOUSEEVENTF_WHEEL
        evs.append(ev)
    if horizontal:
        ev = INPUT()
        ev.type = INPUT_MOUSE
        ev.union.mi.mouseData = int(horizontal)
        ev.union.mi.dwFlags = MOUSEEVENTF_HWHEEL
        evs.append(ev)
    _send(evs)


def key_ctrl(down: bool) -> None:
    ev = INPUT()
    ev.type = INPUT_KEYBOARD
    ev.union.ki.wVk = VK_CONTROL
    ev.union.ki.dwFlags = 0 if down else KEYEVENTF_KEYUP
    _send([ev])


def key_chord(vks: list[int]) -> None:
    if not vks:
        return
    evs = []
    for vk in vks:
        ev = INPUT()
        ev.type = INPUT_KEYBOARD
        ev.union.ki.wVk = vk
        evs.append(ev)
    for vk in reversed(vks):
        ev = INPUT()
        ev.type = INPUT_KEYBOARD
        ev.union.ki.wVk = vk
        ev.union.ki.dwFlags = KEYEVENTF_KEYUP
        evs.append(ev)
    _send(evs)


def park_ole_drag() -> None:
    """Raise SM_CXDRAG/SM_CYDRAG so a mid-mark click cannot start OLE text-move."""
    global _parked_drag
    if not OLE_PARK or user32 is None or _parked_drag is not None:
        return
    width = wintypes.UINT(0)
    height = wintypes.UINT(0)
    if not user32.SystemParametersInfoW(SPI_GETDRAGWIDTH, 0, ctypes.byref(width), 0):
        return
    if not user32.SystemParametersInfoW(SPI_GETDRAGHEIGHT, 0, ctypes.byref(height), 0):
        return
    orig_w = int(width.value)
    orig_h = int(height.value)
    if orig_w <= 0 or orig_w >= OLE_PARK_PX:
        orig_w = OLE_DRAG_DEFAULT
    if orig_h <= 0 or orig_h >= OLE_PARK_PX:
        orig_h = OLE_DRAG_DEFAULT
    user32.SystemParametersInfoW(SPI_SETDRAGWIDTH, OLE_PARK_PX, None, 0)
    user32.SystemParametersInfoW(SPI_SETDRAGHEIGHT, OLE_PARK_PX, None, 0)
    _parked_drag = (orig_w, orig_h)


def unpark_ole_drag() -> None:
    global _parked_drag
    if user32 is None or _parked_drag is None:
        return
    orig_w, orig_h = _parked_drag
    _parked_drag = None
    user32.SystemParametersInfoW(SPI_SETDRAGWIDTH, orig_w, None, 0)
    user32.SystemParametersInfoW(SPI_SETDRAGHEIGHT, orig_h, None, 0)


def restore_ole_drag_defaults() -> None:
    """Kill-safe: Stop-Process skips atexit and can leave SM_CXDRAG parked."""
    global _parked_drag
    _parked_drag = None
    if user32 is None:
        return
    user32.SystemParametersInfoW(SPI_SETDRAGWIDTH, OLE_DRAG_DEFAULT, None, 0)
    user32.SystemParametersInfoW(SPI_SETDRAGHEIGHT, OLE_DRAG_DEFAULT, None, 0)


def open():
    if user32 is None:
        raise OSError("Windows SendInput is unavailable on this platform")


def close():
    unpark_ole_drag()


atexit.register(unpark_ole_drag)
