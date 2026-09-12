#!/usr/bin/env python3
"""SB10 sink for xboxelite — identity Xbox 360 pad (ViGEm on Windows, uinput on Linux).

Does not share SidestickBridge (that map is the T.A320).

    python client/xbox_sink.py --port 27185
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import time

MAGIC = 0x30314253
HEADER = struct.Struct("<I I H H H H I I")

STATS = {"xb10_last": 0.0, "xb10_packets": 0, "listening": False, "error": ""}

# Linux xpad Y is inverted vs XInput.
INVERT_Y = True


def decode_sb10(packet: bytes) -> tuple[int, int, int, int, int, int, int] | None:
    if len(packet) < HEADER.size:
        return None
    magic, seq, x, y, z, r, buttons, pov = HEADER.unpack(packet[: HEADER.size])
    if magic != MAGIC:
        return None
    return seq, x, y, z, r, buttons, pov


def u16_to_thumb(raw: int, invert: bool = False) -> int:
    value = int(raw) - 32768
    if invert:
        value = -value
    if value > 32767:
        return 32767
    if value < -32768:
        return -32768
    return value


def pov_to_hat(pov: int) -> tuple[int, int]:
    if pov == 65535:
        return 0, 0
    table = {
        0: (0, 1),
        4500: (1, 1),
        9000: (1, 0),
        13500: (1, -1),
        18000: (0, -1),
        22500: (-1, -1),
        27000: (-1, 0),
        31500: (-1, 1),
    }
    return table.get(int(pov), (0, 0))


def serve(port: int) -> None:
    STATS["error"] = ""
    STATS["listening"] = False
    try:
        import vgamepad as vg
    except ImportError:
        if sys.platform == "win32":
            STATS["error"] = "vgamepad missing from this Receiver (rebuild with ViGEmClient.dll)"
        else:
            STATS["error"] = "vgamepad missing (pip install vgamepad; needs /dev/uinput)"
        print(STATS["error"], flush=True)
        return

    try:
        pad = vg.VX360Gamepad()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", port))
    except OSError as exc:
        STATS["error"] = f"ViGEm/uinput bind failed: {exc}"
        print(STATS["error"], flush=True)
        return
    except Exception as exc:
        STATS["error"] = str(exc) or type(exc).__name__
        print(f"xbox_sink failed: {STATS['error']}", flush=True)
        return

    STATS["listening"] = True
    print(f"xboxelite sink listening UDP {port}  (ViGEm identity Xbox 360)", flush=True)
    packets = 0
    try:
        while True:
            data, _addr = sock.recvfrom(64)
            decoded = decode_sb10(data)
            if decoded is None:
                continue
            _seq, x, y, z, r, buttons, pov = decoded
            pad.left_joystick(u16_to_thumb(x), u16_to_thumb(y, INVERT_Y))
            pad.right_joystick(u16_to_thumb(z), u16_to_thumb(r, INVERT_Y))
            pad.left_trigger((buttons >> 16) & 0xFF)
            pad.right_trigger((buttons >> 24) & 0xFF)
            mapping = [
                (0, vg.XUSB_BUTTON.XUSB_GAMEPAD_A),
                (1, vg.XUSB_BUTTON.XUSB_GAMEPAD_B),
                (2, vg.XUSB_BUTTON.XUSB_GAMEPAD_X),
                (3, vg.XUSB_BUTTON.XUSB_GAMEPAD_Y),
                (4, vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER),
                (5, vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER),
                (6, vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK),
                (7, vg.XUSB_BUTTON.XUSB_GAMEPAD_START),
                (8, vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB),
                (9, vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB),
            ]
            guide = getattr(vg.XUSB_BUTTON, "XUSB_GAMEPAD_GUIDE", None)
            if guide is not None:
                mapping.append((10, guide))
            for bit, btn in mapping:
                if buttons & (1 << bit):
                    pad.press_button(btn)
                else:
                    pad.release_button(btn)
            hx, hy = pov_to_hat(pov)
            dpad = 0
            if hy > 0:
                dpad |= int(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
            if hy < 0:
                dpad |= int(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
            if hx > 0:
                dpad |= int(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)
            if hx < 0:
                dpad |= int(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
            for name in (
                vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
                vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
                vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
                vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
            ):
                if dpad & int(name):
                    pad.press_button(name)
                else:
                    pad.release_button(name)
            pad.update()
            packets += 1
            STATS["xb10_last"] = time.time()
            STATS["xb10_packets"] = packets
            if packets == 1 or packets % 200 == 0:
                print(f"frames {packets}  buttons={buttons & 0xFFFF:04x}  lt={(buttons >> 16) & 0xFF} rt={(buttons >> 24) & 0xFF}", flush=True)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        STATS["listening"] = False
        try:
            pad.reset()
            pad.update()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="usb-loom Xbox Elite ViGEm sink")
    parser.add_argument("--port", type=int, default=int(os.environ.get("USB_LOOM_XBOX_PORT", 27185)))
    args = parser.parse_args(argv)
    serve(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
