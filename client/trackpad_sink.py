#!/usr/bin/env python3
"""Windows TP10 sink — Mac-like Magic Trackpad gestures via SendInput.

    python client/trackpad_sink.py --port 27184
"""

from __future__ import annotations

import argparse
import atexit
import ctypes
import math
import os
import socket
import struct
import sys
import time
from collections import deque
from ctypes import Structure, Union, c_void_p, sizeof, windll
from ctypes import wintypes

import trackpad_config

MAGIC = 0x30315054
HEADER_SIZE = 12
CONTACT_SIZE = 12
MAX_CONTACTS = 5
BTN_CLICK = 0x01

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
DRAG_JUMP_PX = 64.0
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_LWIN = 0x5B
VK_TAB = 0x09
VK_N = 0x4E
VK_D = 0x44
WHEEL_DELTA = 120
SWIPE_UNITS = 280.0
EDGE_RATIO = 0.82
INERTIA_WINDOW = 0.15
INERTIA_FLICK = 28.0
INERTIA_FIRM = 48.0
INERTIA_SOFT_V = 34.0
INERTIA_FIRM_V = 70.0
INERTIA_DECAY = 1.2
INERTIA_DECAY_VREF = 52.0
INERTIA_FRICTION = 26.0
INERTIA_KAPPA = INERTIA_DECAY * INERTIA_DECAY_VREF / INERTIA_FRICTION
INERTIA_TICK_CAP = 8
INERTIA_DT_MAX = 0.016
INERTIA_MIN_SPAN = 0.012
INERTIA_WEIGHT_TAU = 0.045
INERTIA_AXIS_RATIO = 1.6
TAP_GHOST_S = 0.08

ULONG_PTR = c_void_p

STATS = {"tp10_last": 0.0, "tp10_packets": 0, "tp10_mode": "", "tp10_sticky": False, "listening": False}

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


user32 = windll.user32 if sys.platform == "win32" else None


def decode_tp10(
    packet: bytes,
) -> tuple[int, int, list[tuple[int, int, int, int, int, int, int]], tuple[int, int, int, int]] | None:
    if len(packet) < HEADER_SIZE:
        return None
    magic, seq, buttons, n, _res = struct.unpack("<I I B B H", packet[:HEADER_SIZE])
    if magic != MAGIC:
        return None
    n = min(n, MAX_CONTACTS)
    contacts: list[tuple[int, int, int, int, int, int, int]] = []
    for i in range(n):
        start = HEADER_SIZE + i * CONTACT_SIZE
        end = start + CONTACT_SIZE
        if len(packet) < end:
            return None
        slot, _pad, tracking_id, x, y, pressure, major, minor = struct.unpack("<B B h h h H B B", packet[start:end])
        if tracking_id < 0:
            continue
        contacts.append((slot, tracking_id, x, y, pressure, major, minor))
    rel = (0, 0, 0, 0)
    rel_at = HEADER_SIZE + CONTACT_SIZE * MAX_CONTACTS
    if len(packet) >= rel_at + 8:
        rel = struct.unpack("<h h h h", packet[rel_at : rel_at + 8])
    return seq, buttons, contacts, rel


def _send(inputs: list[INPUT]) -> None:
    if user32 is None or not inputs:
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


atexit.register(unpark_ole_drag)


def mouse_wheel(vertical: int = 0, horizontal: int = 0) -> None:
    evs: list[INPUT] = []
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
    evs: list[INPUT] = []
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


def _centroid(contacts: list[tuple[int, int, int, int, int, int, int]]) -> tuple[float, float]:
    xs = [c[2] for c in contacts]
    ys = [c[3] for c in contacts]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _span(contacts: list[tuple[int, int, int, int, int, int, int]]) -> float:
    if len(contacts) < 2:
        return 0.0
    a, b = contacts[0], contacts[1]
    return math.hypot(a[2] - b[2], a[3] - b[3])


def coast_decay_for_speed(
    v0: float,
    *,
    friction: float = INERTIA_FRICTION,
    kappa: float = INERTIA_KAPPA,
) -> float:
    """Decay from throw meeting table. k = κ a / v0; not a third slider."""
    speed = max(float(v0), 1e-6)
    return float(kappa) * float(friction) / speed


def coast_step(
    speed: float,
    dt: float,
    *,
    decay: float = INERTIA_DECAY,
    friction: float = INERTIA_FRICTION,
) -> tuple[float, float]:
    """Hybrid ice: multiplicative decay, then a Coulomb bite. Rest is a last triangle."""
    if dt <= 0.0 or speed <= 0.0:
        return 0.0, 0.0
    v_exp = speed * math.exp(-decay * dt)
    v_new = v_exp - friction * dt
    if v_new <= 0.0:
        return 0.0, 0.5 * speed * dt
    return v_new, 0.5 * (speed + v_new) * dt


class GestureEngine:
    """Mac-like gestures. Knobs come from trackpad.json (env overrides)."""

    def __init__(self, cfg: trackpad_config.TrackpadConfig | None = None) -> None:
        self.cfg = cfg if cfg is not None else trackpad_config.load()
        self._cfg_locked = cfg is not None
        self._cfg_mtime = 0.0 if self._cfg_locked else trackpad_config.mtime()
        self._cfg_check = 0.0
        self.tap_ms = float(os.environ.get("USB_LOOM_TP_TAP_MS", "300"))
        self.tap_slop = float(os.environ.get("USB_LOOM_TP_TAP_SLOP", "45"))
        self.inertia_flick = float(os.environ.get("USB_LOOM_TP_INERTIA_FLICK", str(INERTIA_FLICK)))
        self._decay_locked = os.environ.get("USB_LOOM_TP_INERTIA_DECAY") is not None
        self.inertia_decay_ref = float(os.environ.get("USB_LOOM_TP_INERTIA_DECAY", str(INERTIA_DECAY)))
        self.inertia_decay = self.inertia_decay_ref
        self.inertia_firm_v = INERTIA_FIRM_V
        self.inertia_friction = INERTIA_FRICTION
        self.prev: list[tuple[int, int, int, int, int, int, int]] = []
        self.prev_n = 0
        self.anchor: tuple[float, float] | None = None
        self.span0 = 0.0
        self.down_at = 0.0
        self.last_activity = 0.0
        self.stroke_dist = 0.0
        self.drag_hold_frames = 0
        self.peak_n = 0
        self.wheel_v = 0.0
        self.wheel_h = 0.0
        self.scroll_flicker_until = 0.0
        self.scroll_samples: deque[tuple[float, float, float]] = deque()
        self.coasting = False
        self.coast_vx = 0.0
        self.coast_vy = 0.0
        self.coast_t = 0.0
        self.linger_tap = False
        self.linger_tap_at = 0.0
        self._one_finger_at = 0.0
        self.moved = False
        self.sticky = False
        self.left_down = False
        self.prev_clicked = False
        self.ctrl_down = False
        self.mode = ""
        self.armed_tap = 0
        self.swipe_origin: tuple[float, float] | None = None
        self.swipe_n = 0
        self.edge_from_right = False
        self.pad_max_x = 1.0
        self._apply_cfg()

    def _apply_cfg(self) -> None:
        self.scale = self.cfg.scale()
        self.scroll_scale = self.cfg.scroll_scale()
        self.natural = self.cfg.natural()
        self.invert_x = self.cfg.invert_horizontal()
        self.invert_y = self.cfg.invert_vertical()
        self.drag_idle_ms = self.cfg.idle_ms()
        firm_env = os.environ.get("USB_LOOM_TP_INERTIA_FIRM_V")
        self.inertia_firm_v = float(firm_env) if firm_env else self.cfg.firm_v()
        fric_env = os.environ.get("USB_LOOM_TP_INERTIA_FRICTION")
        self.inertia_friction = float(fric_env) if fric_env else self.cfg.table_friction()

    def _pointer_move(self, dx: float, dy: float) -> None:
        if self.invert_x:
            dx = -dx
        if self.invert_y:
            dy = -dy
        mouse_move(int(round(dx)), int(round(dy)))

    def maybe_reload(self) -> None:
        if self._cfg_locked:
            return
        now = time.monotonic()
        if now - self._cfg_check < 0.25:
            return
        self._cfg_check = now
        stamp = trackpad_config.mtime()
        if stamp == self._cfg_mtime:
            return
        self._cfg_mtime = stamp
        self.cfg = trackpad_config.load()
        self._apply_cfg()

    def reset_stroke(self) -> None:
        self.anchor = None
        self.span0 = 0.0
        self.down_at = 0.0
        self.last_activity = 0.0
        self.stroke_dist = 0.0
        self.drag_hold_frames = 0
        self.peak_n = 0
        self.scroll_flicker_until = 0.0
        self.scroll_samples.clear()
        self.linger_tap = False
        self.linger_tap_at = 0.0
        self._one_finger_at = 0.0
        self.moved = False
        self.sticky = False
        self.mode = ""
        self.armed_tap = 0
        self.swipe_origin = None
        self.swipe_n = 0
        self.edge_from_right = False
        if self.ctrl_down:
            key_ctrl(False)
            self.ctrl_down = False

    def _ensure_left(self, down: bool) -> None:
        if down and not self.left_down:
            mouse_btn(MOUSEEVENTF_LEFTDOWN)
            self.left_down = True
        elif not down and self.left_down:
            mouse_btn(MOUSEEVENTF_LEFTUP)
            self.left_down = False
            unpark_ole_drag()

    def _delta(self, contacts: list[tuple[int, int, int, int, int, int, int]]) -> tuple[float, float]:
        cx, cy = _centroid(contacts)
        if self.anchor is None:
            self.anchor = (cx, cy)
            return 0.0, 0.0
        dx = (cx - self.anchor[0]) * self.scale
        dy = (self.anchor[1] - cy) * self.scale
        self.anchor = (cx, cy)
        return dx, dy

    def _rebase(self, contacts: list[tuple[int, int, int, int, int, int, int]]) -> None:
        self.anchor = _centroid(contacts) if contacts else None

    def _sticky_move(
        self,
        contacts: list[tuple[int, int, int, int, int, int, int]],
        clock: float,
    ) -> None:
        mdx, mdy = self._delta(contacts)
        if math.hypot(mdx, mdy) > DRAG_JUMP_PX:
            return
        self._mark_move(mdx, mdy, clock)
        self._pointer_move(mdx, mdy)
        self._hold_drag_frame(clock)

    def _flush_scroll(self, mdx: float, mdy: float, tick_cap: int | None = None) -> None:
        vx = -mdy if self.natural else mdy
        hx = -mdx if self.natural else mdx
        add_v = vx * self.scroll_scale * 20.0
        add_h = hx * self.scroll_scale * 20.0
        if tick_cap is not None:
            cap = float(tick_cap)
            add_v = max(-cap, min(cap, add_v))
            add_h = max(-cap, min(cap, add_h))
        self.wheel_v += add_v
        self.wheel_h += add_h
        ticks_v = int(self.wheel_v)
        ticks_h = int(self.wheel_h)
        if tick_cap is not None:
            ticks_v = max(-tick_cap, min(tick_cap, ticks_v))
            ticks_h = max(-tick_cap, min(tick_cap, ticks_h))
        self.wheel_v -= ticks_v
        self.wheel_h -= ticks_h
        if ticks_v or ticks_h:
            mouse_wheel(vertical=ticks_v, horizontal=ticks_h)

    def _flush_wheel_remainder(self) -> None:
        ticks_v = int(round(self.wheel_v))
        ticks_h = int(round(self.wheel_h))
        cap = INERTIA_TICK_CAP
        ticks_v = max(-cap, min(cap, ticks_v))
        ticks_h = max(-cap, min(cap, ticks_h))
        self.wheel_v = 0.0
        self.wheel_h = 0.0
        if ticks_v or ticks_h:
            mouse_wheel(vertical=ticks_v, horizontal=ticks_h)

    def _sample_scroll(self, contacts: list[tuple[int, int, int, int, int, int, int]], now: float) -> None:
        if self.mode != "scroll" or len(contacts) < 2:
            return
        cx, cy = _centroid(contacts)
        self.scroll_samples.append((now, cx, cy))
        while self.scroll_samples and now - self.scroll_samples[0][0] > INERTIA_WINDOW:
            self.scroll_samples.popleft()

    def _estimate_scroll_velocity(self) -> tuple[float, float]:
        samples = self.scroll_samples
        if len(samples) < 2:
            return 0.0, 0.0
        if samples[-1][0] - samples[0][0] < INERTIA_MIN_SPAN:
            return 0.0, 0.0
        t_end = samples[-1][0]
        wsum = 0.0
        vx_acc = 0.0
        vy_acc = 0.0
        for i in range(1, len(samples)):
            t0, x0, y0 = samples[i - 1]
            t1, x1, y1 = samples[i]
            dt = t1 - t0
            if dt < 1e-4:
                continue
            age = t_end - (t0 + t1) * 0.5
            weight = math.exp(-age / INERTIA_WEIGHT_TAU)
            vx_acc += weight * (x1 - x0) / dt
            vy_acc += weight * (y0 - y1) / dt
            wsum += weight
        if wsum <= 0.0:
            return 0.0, 0.0
        return (vx_acc / wsum) * self.scale, (vy_acc / wsum) * self.scale

    def _axis_lock(self, vx: float, vy: float) -> tuple[float, float]:
        ax, ay = abs(vx), abs(vy)
        if ay >= INERTIA_AXIS_RATIO * ax:
            return 0.0, vy
        if ax >= INERTIA_AXIS_RATIO * ay:
            return vx, 0.0
        return vx, vy

    def _snap_coast_gear(self, vx: float, vy: float) -> tuple[float, float] | None:
        speed = math.hypot(vx, vy)
        if speed < self.inertia_flick:
            return None
        gear = self.inertia_firm_v if speed >= INERTIA_FIRM else INERTIA_SOFT_V
        scale = gear / speed
        return vx * scale, vy * scale

    def _stop_coast(self, flush: bool = False) -> None:
        if flush:
            self._flush_wheel_remainder()
        self.coasting = False
        self.coast_vx = 0.0
        self.coast_vy = 0.0
        self.coast_t = 0.0

    def _maybe_start_coast(self, now: float) -> None:
        if self.mode != "scroll" or not self.moved or len(self.scroll_samples) < 2:
            return
        vx, vy = self._estimate_scroll_velocity()
        vx, vy = self._axis_lock(vx, vy)
        snapped = self._snap_coast_gear(vx, vy)
        if snapped is None:
            return
        vx, vy = snapped
        self.coasting = True
        self.coast_vx = vx
        self.coast_vy = vy
        self.coast_t = now
        if not self._decay_locked:
            self.inertia_decay = coast_decay_for_speed(
                math.hypot(vx, vy),
                friction=self.inertia_friction,
            )

    def _coast_step(self, speed: float, dt: float) -> tuple[float, float]:
        return coast_step(speed, dt, decay=self.inertia_decay, friction=self.inertia_friction)

    def _tick_coast(self, now: float) -> None:
        if not self.coasting:
            return
        dt = now - self.coast_t
        if dt <= 0:
            return
        dt = min(dt, INERTIA_DT_MAX)
        self.coast_t = now
        speed = math.hypot(self.coast_vx, self.coast_vy)
        if speed <= 1e-6:
            self._stop_coast(flush=True)
            return
        ux = self.coast_vx / speed
        uy = self.coast_vy / speed
        v_new, dist = self._coast_step(speed, dt)
        self._flush_scroll(ux * dist, uy * dist, tick_cap=INERTIA_TICK_CAP)
        if v_new <= 0.0:
            self._stop_coast(flush=True)
            return
        self.coast_vx = ux * v_new
        self.coast_vy = uy * v_new

    def _fire_tap(self, fingers: int) -> None:
        cfg = self.cfg
        if fingers == 2:
            return
        if fingers == 1 and cfg.tap_to_click:
            mouse_btn(MOUSEEVENTF_LEFTDOWN)
            mouse_btn(MOUSEEVENTF_LEFTUP)
        elif fingers == 4 and cfg.launchpad == "start":
            key_chord([VK_LWIN])

    def _pulse_click(self) -> None:
        mouse_btn(MOUSEEVENTF_LEFTDOWN)
        mouse_btn(MOUSEEVENTF_LEFTUP)

    def _arm_sticky(self, now: float) -> None:
        if self.mode == "drag3" and self.left_down:
            self.sticky = True
            self.last_activity = now

    def _mark_move(self, dx: float, dy: float, now: float) -> None:
        dist = math.hypot(dx, dy)
        self.stroke_dist += dist
        slop = self.tap_slop * self.scale * 0.25
        if dist > slop or self.stroke_dist > slop:
            self.moved = True
            self.armed_tap = 0
        if self.mode == "drag3" and self.left_down:
            if dist > 0.0 or self.stroke_dist > 0.0:
                self._arm_sticky(now)

    def _hold_drag_frame(self, now: float) -> None:
        if self.mode != "drag3" or not self.left_down:
            return
        self.drag_hold_frames += 1
        if self.drag_hold_frames >= 3:
            self._arm_sticky(now)

    def _end_mark_to_pointer(self) -> None:
        self._ensure_left(False)
        self.reset_stroke()
        self.mode = "pointer"

    def _maybe_expire_sticky(self, now: float, n: int, moving: bool) -> None:
        if not self.sticky:
            return
        if n >= 1 or moving:
            self.last_activity = now
            return
        if (now - self.last_activity) * 1000 <= self.drag_idle_ms:
            return
        self._ensure_left(False)
        self.reset_stroke()

    def _swipe_dir(self, contacts: list[tuple[int, int, int, int, int, int, int]]) -> str:
        if self.swipe_origin is None or not contacts:
            return ""
        cx, cy = _centroid(contacts)
        dx = cx - self.swipe_origin[0]
        dy = cy - self.swipe_origin[1]
        if max(abs(dx), abs(dy)) < SWIPE_UNITS:
            return ""
        if abs(dx) >= abs(dy):
            return "right" if dx > 0 else "left"
        return "up" if dy < 0 else "down"

    def _fire_swipe(self, direction: str, fingers: int) -> None:
        cfg = self.cfg
        if direction in ("left", "right") and cfg.swipe_pages == "back-forward":
            key_chord([VK_MENU, VK_LEFT if direction == "left" else VK_RIGHT])
            return
        if direction in ("left", "right") and cfg.swipe_fullscreen == "desktops":
            key_chord([VK_LWIN, VK_CONTROL, VK_LEFT if direction == "left" else VK_RIGHT])
            return
        if direction == "up" and cfg.mission_control == "task-view":
            key_chord([VK_LWIN, VK_TAB])
            return
        if direction == "down" and cfg.show_desktop == "win-d":
            key_chord([VK_LWIN, VK_D])
            return
        if fingers >= 4 and not direction and cfg.launchpad == "start":
            key_chord([VK_LWIN])

    def _note_edge(self, contacts: list[tuple[int, int, int, int, int, int, int]]) -> None:
        for _slot, _tid, x, _y, _p, _maj, _min in contacts:
            if x > self.pad_max_x:
                self.pad_max_x = float(x)
        if not contacts:
            return
        cx, _cy = _centroid(contacts)
        self.edge_from_right = cx >= self.pad_max_x * EDGE_RATIO

    def feed(
        self,
        buttons: int,
        contacts: list[tuple[int, int, int, int, int, int, int]],
        rel: tuple[int, int, int, int] = (0, 0, 0, 0),
        now: float | None = None,
    ) -> None:
        self.maybe_reload()
        cfg = self.cfg
        clock = time.monotonic() if now is None else now
        dx, dy, wheel, hwheel = rel
        if (dx or dy) and self.mode != "drag3" and not self.sticky:
            self._pointer_move(dx, -dy)
        if wheel or hwheel:
            vx = -wheel if self.natural else wheel
            hx = -hwheel if self.natural else hwheel
            mouse_wheel(vertical=int(vx * WHEEL_DELTA), horizontal=int(hx * WHEEL_DELTA))
        n = len(contacts)
        if n >= 1:
            self._stop_coast()
        if n > self.peak_n:
            self.peak_n = n
        clicked = bool(buttons & BTN_CLICK)
        try:
            self._feed_contacts(cfg, clock, n, clicked, contacts, dx, dy)
        finally:
            self.prev_clicked = clicked

    def _feed_contacts(
        self,
        cfg: trackpad_config.TrackpadConfig,
        clock: float,
        n: int,
        clicked: bool,
        contacts: list[tuple[int, int, int, int, int, int, int]],
        dx: int,
        dy: int,
    ) -> None:
        self._maybe_expire_sticky(clock, n, False)

        if n == 0:
            if self.sticky:
                if (
                    self.linger_tap
                    and (clock - self.linger_tap_at) * 1000 <= self.tap_ms
                ):
                    self._ensure_left(False)
                    self.reset_stroke()
                    if cfg.tap_to_click:
                        self._fire_tap(1)
                    self.prev = []
                    self.prev_n = 0
                    return
                self.prev = []
                self.prev_n = 0
                return
            if self.armed_tap and not self.moved and (clock - self.down_at) * 1000 <= self.tap_ms:
                self._fire_tap(self.armed_tap)
            if self.swipe_origin is not None and self.swipe_n >= 2:
                direction = self._swipe_dir(self.prev) if self.prev else ""
                if self.swipe_n == 2 and direction == "left" and self.edge_from_right:
                    if cfg.notification_center == "win-n":
                        key_chord([VK_LWIN, VK_N])
                elif self.swipe_n >= 4:
                    self._fire_swipe(direction, self.swipe_n)
            was_scroll = self.mode == "scroll" and self.moved
            self._ensure_left(False)
            if was_scroll:
                self._maybe_start_coast(clock)
            self.reset_stroke()
            self._tick_coast(clock)
            self.prev = []
            self.prev_n = 0
            return

        if n != self.prev_n:
            if self.sticky:
                if n == 1 and not clicked and self.prev_n == 0:
                    self.linger_tap = True
                    self.linger_tap_at = clock
                    self._one_finger_at = 0.0
                    self.anchor = None
                    self.prev_n = n
                    self.prev = contacts
                    return
                if n == 1:
                    if self._one_finger_at == 0.0:
                        self._one_finger_at = clock
                    self.linger_tap = False
                    self.armed_tap = 0
                    self.prev_n = n
                    self.prev = contacts
                    return
                if self.linger_tap and n != 1:
                    self.linger_tap = False
                self._one_finger_at = 0.0
                if self.mode != "drag3":
                    self.mode = "drag3"
                self.armed_tap = 0
                self._ensure_left(True)
                self._arm_sticky(clock)
                self._rebase(contacts)
                self.prev_n = n
                self.prev = contacts
                return
            if (
                self.mode == "pointer"
                and self.armed_tap == 1
                and n == 2
                and (clock - self.down_at) <= TAP_GHOST_S
            ):
                return
            if self.mode == "scroll" and n == 1:
                self.scroll_flicker_until = clock + 0.08
                self.anchor = None
                self.prev_n = n
                self.prev = contacts
                return
            if self.mode == "scroll" and n == 2:
                self.scroll_flicker_until = 0.0
                self.anchor = None
                self.prev_n = n
                self.prev = contacts
                return
            self.anchor = None
            self.span0 = _span(contacts) if n >= 2 else 0.0
            self.down_at = clock
            self.moved = False
            self.swipe_origin = _centroid(contacts)
            self.swipe_n = n
            self._note_edge(contacts)
            if n == 1:
                self.mode = "pointer"
                self.armed_tap = 0 if clicked else 1
                self._ensure_left(False)
                if clicked and not self.prev_clicked:
                    self._pulse_click()
            elif n == 2:
                self.mode = "scroll"
                self.armed_tap = 0
                self._ensure_left(False)
            else:
                self.mode = "drag3"
                self.armed_tap = 0
                self._ensure_left(True)
                self._arm_sticky(clock)
            self.prev_n = n
            self.prev = contacts
            return

        if self.sticky:
            if n == 1:
                if clicked and not self.prev_clicked:
                    self._end_mark_to_pointer()
                    self._pulse_click()
                    self.prev = contacts
                    self.prev_n = n
                    return
                flicker = self._one_finger_at and (clock - self._one_finger_at) <= TAP_GHOST_S
                if flicker and not self.linger_tap:
                    self._ensure_left(True)
                    self.prev = contacts
                    self.prev_n = n
                    return
                mdx, mdy = self._delta(contacts)
                slop = self.tap_slop * self.scale * 0.25
                moved = math.hypot(mdx, mdy) > slop
                if self.linger_tap and not moved:
                    self.prev = contacts
                    self.prev_n = n
                    return
                self._end_mark_to_pointer()
                self.mode = "pointer"
                if moved:
                    self._pointer_move(mdx, mdy)
                    self.moved = True
                    self.armed_tap = 0
                self.prev = contacts
                self.prev_n = n
                return
            self._ensure_left(True)
            self._sticky_move(contacts, clock)
            self.prev = contacts
            self.prev_n = n
            return

        if n == 1:
            if self.mode == "scroll" and self.scroll_flicker_until and clock < self.scroll_flicker_until:
                self.prev = contacts
                self.prev_n = n
                return
            if self.mode == "scroll":
                self.mode = "pointer"
            self.mode = "pointer"
            self._ensure_left(False)
            if clicked and not self.prev_clicked:
                self._pulse_click()
                self.armed_tap = 0
            if not (dx or dy):
                mdx, mdy = self._delta(contacts)
                self._mark_move(mdx, mdy, clock)
                self._pointer_move(mdx, mdy)
        elif n == 2:
            mdx, mdy = self._delta(contacts)
            self._mark_move(mdx, mdy, clock)
            self._flush_scroll(mdx, mdy)
            self._sample_scroll(contacts, clock)
        elif n >= 3:
            self._ensure_left(True)
            self._sticky_move(contacts, clock)
        else:
            self._mark_move(*self._delta(contacts), clock)
            self._ensure_left(False)

        self.prev = contacts
        self.prev_n = n

    def close(self) -> None:
        self._stop_coast()
        self._ensure_left(False)
        self.reset_stroke()


def serve(port: int) -> None:
    if user32 is None:
        raise SystemExit("trackpad_sink is Windows-only (SendInput)")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as exc:
        raise SystemExit(f"TP10 sink cannot bind UDP {port}: {exc}") from exc
    log_path = os.environ.get("USB_LOOM_TP_LOG", os.path.join(os.environ.get("TEMP", "."), "usb-loom-tp10.log"))
    print(f"TP10 sink listening UDP {port}  (Mac-like gestures, SendInput)", flush=True)
    restore_ole_drag_defaults()
    engine = GestureEngine()
    packets = 0
    last_fingers = -1
    STATS["listening"] = True
    try:
        while True:
            data, _addr = sock.recvfrom(2048)
            decoded = decode_tp10(data)
            if decoded is None:
                continue
            _seq, buttons, contacts, rel = decoded
            engine.feed(buttons, contacts, rel)
            packets += 1
            STATS["tp10_last"] = time.time()
            STATS["tp10_packets"] = packets
            STATS["tp10_mode"] = engine.mode or "-"
            STATS["tp10_sticky"] = bool(engine.sticky)
            fingers = len(contacts)
            if packets == 1 or packets % 400 == 0 or fingers != last_fingers or any(rel):
                line = (
                    f"frames {packets}  fingers={fingers}  buttons={buttons}  "
                    f"rel={rel}  mode={engine.mode or '-'}  sticky={int(engine.sticky)}"
                )
                print(line, flush=True)
                try:
                    with open(log_path, "a", encoding="utf-8") as log:
                        log.write(line + "\n")
                except OSError:
                    pass
                last_fingers = fingers
    except KeyboardInterrupt:
        print("stopped")
    finally:
        STATS["listening"] = False
        engine.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="usb-loom Magic Trackpad sink (Windows)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("USB_LOOM_TRACKPAD_PORT", 27184)))
    args = parser.parse_args(argv)
    serve(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
