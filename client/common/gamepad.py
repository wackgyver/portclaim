#!/usr/bin/env python3
"""SB10 identity Xbox 360 sink: changed reports, with independent fail-safe release."""
from __future__ import annotations

import argparse
import os
import socket
import struct
import time

from client.common.stream_guard import StreamGuard

MAGIC = 0x30314253
HEADER = struct.Struct("<I I H H H H I I")
STATS = {"xb10_last": 0.0, "xb10_packets": 0, "xb10_updates": 0, "listening": False, "error": ""}
INVERT_Y = True  # Linux xpad Y is inverted vs XInput.


def decode_sb10(packet: bytes) -> tuple[int, int, int, int, int, int, int] | None:
    if len(packet) != HEADER.size:
        return None
    magic, seq, x, y, z, r, buttons, pov = HEADER.unpack(packet)
    if magic != MAGIC:
        return None
    return seq, x, y, z, r, buttons, pov


def u16_to_thumb(raw: int, invert: bool = False) -> int:
    value = int(raw) - 32768
    if invert:
        value = -value
    return max(-32768, min(32767, value))


def pov_to_hat(pov: int) -> tuple[int, int]:
    return {0: (0, 1), 4500: (1, 1), 9000: (1, 0), 13500: (1, -1),
            18000: (0, -1), 22500: (-1, -1), 27000: (-1, 0), 31500: (-1, 1)}.get(int(pov), (0, 0))


class GamepadWriter:
    def __init__(self, pad, vg):
        self.pad = pad
        self.last_state = None
        names = ("A", "B", "X", "Y", "LEFT_SHOULDER", "RIGHT_SHOULDER", "BACK", "START", "LEFT_THUMB", "RIGHT_THUMB", "GUIDE")
        self.mapping = tuple((bit, getattr(vg.XUSB_BUTTON, "XUSB_GAMEPAD_" + name))
                             for bit, name in enumerate(names)
                             if hasattr(vg.XUSB_BUTTON, "XUSB_GAMEPAD_" + name))
        self.dpad = tuple(getattr(vg.XUSB_BUTTON, "XUSB_GAMEPAD_DPAD_" + name)
                          for name in ("UP", "DOWN", "LEFT", "RIGHT"))

    def apply(self, state):
        if state == self.last_state:
            return False
        x, y, z, r, buttons, pov = state
        pad = self.pad
        pad.left_joystick(u16_to_thumb(x), u16_to_thumb(y, INVERT_Y))
        pad.right_joystick(u16_to_thumb(z), u16_to_thumb(r, INVERT_Y))
        pad.left_trigger((buttons >> 16) & 0xFF)
        pad.right_trigger((buttons >> 24) & 0xFF)
        for bit, btn in self.mapping:
            (pad.press_button if buttons & (1 << bit) else pad.release_button)(btn)
        hx, hy = pov_to_hat(pov)
        for down, btn in zip((hy > 0, hy < 0, hx < 0, hx > 0), self.dpad):
            (pad.press_button if down else pad.release_button)(btn)
        pad.update()
        self.last_state = state
        return True

    def neutral(self):
        if self.last_state is not None:
            self.pad.reset()
            self.pad.update()
            self.last_state = None


def serve(port: int, create_pad) -> None:
    STATS.update(error="", listening=False)
    sock = None
    writer = None
    try:
        pad, vg = create_pad()
        writer = GamepadWriter(pad, vg)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", port))
        sock.settimeout(0.1)
        guard = StreamGuard(timeout=0.5)
        STATS["listening"] = True
        print(f"xboxelite listening UDP {port} (Xbox 360; changed reports only)", flush=True)
        packets = 0
        while True:
            if guard.expired(time.monotonic()):
                writer.neutral()
            try:
                data, _addr = sock.recvfrom(64)
            except socket.timeout:
                continue
            decoded = decode_sb10(data)
            if decoded is None or not guard.accept(decoded[0], time.monotonic()):
                continue
            if guard.resync:
                writer.neutral()
            if writer.apply(decoded[1:]):
                STATS["xb10_updates"] += 1
            packets += 1
            STATS.update(xb10_last=time.time(), xb10_packets=packets)
            if packets == 1 or packets % 1000 == 0:
                print(f"Xbox frames {packets}; changed reports {STATS['xb10_updates']}", flush=True)
    except KeyboardInterrupt:
        print("Xbox sink stopped", flush=True)
    except Exception as exc:
        STATS["error"] = str(exc) or type(exc).__name__
        print(f"Xbox sink failed: {STATS['error']}", flush=True)
    finally:
        STATS["listening"] = False
        if sock is not None:
            sock.close()
        if writer is not None:
            writer.neutral()
