#!/usr/bin/env python3
"""HID adapters + SB10 codec for the usb-loom hub.

Reads Linux evdev. The hub (server.py) owns routes. This module does not
choose a destination — claiming lives on the control plane.
"""

from __future__ import annotations

import argparse
import array
import os
import selectors
import socket
import struct
import sys
import time

try:
    import fcntl
except ImportError:  # Windows host can still unit-test the SB10 codec
    fcntl = None  # type: ignore[assignment]
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

MAGIC = 0x30314253  # ASCII "SB10" little-endian
PACKET_SIZE = 24
DEFAULT_PORT = 27182
TA320_VID = 0x044F
TA320_PID = 0x0406

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
SYN_REPORT = 0
REL_X, REL_Y, REL_WHEEL, REL_HWHEEL = 0x00, 0x01, 0x08, 0x06

ABS_X, ABS_Y, ABS_Z = 0x00, 0x01, 0x02
ABS_RX, ABS_RY, ABS_RZ = 0x03, 0x04, 0x05
ABS_THROTTLE, ABS_RUDDER = 0x06, 0x07
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11
ABS_MT_SLOT = 0x2F
ABS_MT_TOUCH_MAJOR = 0x30
ABS_MT_TOUCH_MINOR = 0x31
ABS_MT_ORIENTATION = 0x34
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39
ABS_MT_PRESSURE = 0x3A
BTN_LEFT = 0x110
BTN_JOYSTICK = 0x120
BTN_GAMEPAD = 0x130
BTN_A, BTN_B, BTN_X, BTN_Y = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR = 0x136, 0x137
BTN_SELECT, BTN_START, BTN_MODE = 0x13A, 0x13B, 0x13C
BTN_THUMBL, BTN_THUMBR = 0x13D, 0x13E
BTN_MISC = 0x100
KEY_MAX = 0x2FF
ABS_MAX = 0x3F
XBOX_VID = 0x045E

IOC_READ = 2
IOC_WRITE = 1


def _ioc(direction: int, type_char: str, nr: int, size: int) -> int:
    return direction << 30 | size << 16 | ord(type_char) << 8 | nr


def _ior(type_char: str, nr: int, size: int) -> int:
    return _ioc(IOC_READ, type_char, nr, size)


def _iow(type_char: str, nr: int, size: int) -> int:
    return _ioc(IOC_WRITE, type_char, nr, size)


EVIOCGID = _ior("E", 0x02, 8)
EVIOCGRAB = _iow("E", 0x90, 4)
EVIOCGNAME = _ior("E", 0x06, 256)


def eviocgabs(axis: int) -> int:
    return _ior("E", 0x40 + axis, 24)


def eviocgbit(ev_type: int, length: int) -> int:
    return _ior("E", 0x20 + ev_type, length)


EVENT_FORMAT = "llHHi" if sys.maxsize <= 2**32 else "QQHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)


@dataclass(frozen=True)
class DeviceId:
    bustype: int
    vendor: int
    product: int
    version: int


@dataclass
class AbsAxis:
    code: int
    value: int
    minimum: int
    maximum: int

    def scaled_u16(self) -> int:
        span = self.maximum - self.minimum
        if span <= 0:
            return 32767
        ratio = (self.value - self.minimum) / span
        return int(max(0.0, min(1.0, ratio)) * 65535)


@dataclass
class JoyState:
    x: int = 32767
    y: int = 32767
    z: int = 32767
    r: int = 32767
    buttons: int = 0
    pov: int = 65535


def encode_sb10(seq: int, state: JoyState) -> bytes:
    return struct.pack(
        "<I I H H H H I I",
        MAGIC,
        seq & 0xFFFFFFFF,
        state.x & 0xFFFF,
        state.y & 0xFFFF,
        state.z & 0xFFFF,
        state.r & 0xFFFF,
        state.buttons & 0xFFFFFFFF,
        state.pov & 0xFFFFFFFF,
    )


def hat_to_pov(hat_x: int, hat_y: int) -> int:
    if hat_x == 0 and hat_y == 0:
        return 65535
    table = {
        (0, -1): 0,
        (1, -1): 4500,
        (1, 0): 9000,
        (1, 1): 13500,
        (0, 1): 18000,
        (-1, 1): 22500,
        (-1, 0): 27000,
        (-1, -1): 31500,
    }
    return table.get((hat_x, hat_y), 65535)


class EvdevDevice:
    def __init__(self, path: Path):
        if fcntl is None:
            raise OSError("evdev requires Linux")
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.grabbed = False
        self.id = self._read_id()
        self.name = self._read_name()
        self.abs: dict[int, AbsAxis] = {}
        self.keys: list[int] = []
        self._load_caps()
        self.hat_x = 0
        self.hat_y = 0
        self.key_down: set[int] = set()
        self.mt_slot = 0
        self.mt_slots: dict[int, dict[int, int]] = {}
        self.rel_x = 0
        self.rel_y = 0
        self.rel_wheel = 0
        self.rel_hwheel = 0

    def _ioctl_in(self, request: int, size: int) -> bytes:
        buf = array.array("B", [0] * size)
        fcntl.ioctl(self.fd, request, buf, True)
        return buf.tobytes()

    def _read_id(self) -> DeviceId:
        raw = self._ioctl_in(EVIOCGID, 8)
        bustype, vendor, product, version = struct.unpack("HHHH", raw)
        return DeviceId(bustype, vendor, product, version)

    def _read_name(self) -> str:
        try:
            raw = bytearray(256)
            fcntl.ioctl(self.fd, EVIOCGNAME, raw)
            return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
        except OSError:
            return self.path.name

    def _bits(self, ev_type: int, length: int) -> list[int]:
        buf = array.array("B", [0] * length)
        try:
            fcntl.ioctl(self.fd, eviocgbit(ev_type, length), buf, True)
        except OSError:
            return []
        found: list[int] = []
        for i, byte in enumerate(buf):
            for bit in range(8):
                if byte & (1 << bit):
                    found.append(i * 8 + bit)
        return found

    def _load_caps(self) -> None:
        for code in self._bits(EV_ABS, (ABS_MAX + 7) // 8):
            raw = bytearray(24)
            try:
                fcntl.ioctl(self.fd, eviocgabs(code), raw, True)
            except OSError:
                continue
            value, minimum, maximum, _fuzz, _flat, _res = struct.unpack("iiiiii", raw)
            self.abs[code] = AbsAxis(code, value, minimum, maximum)
        key_bytes = (KEY_MAX + 7) // 8
        self.keys = [c for c in self._bits(EV_KEY, key_bytes) if c >= BTN_MISC]

    def grab(self, enable: bool = True) -> None:
        try:
            fcntl.ioctl(self.fd, EVIOCGRAB, 1 if enable else 0)
            self.grabbed = enable
        except OSError as exc:
            print(f"grab failed on {self.path}: {exc}", file=sys.stderr)

    def close(self) -> None:
        if self.grabbed:
            try:
                self.grab(False)
            except OSError:
                pass
        os.close(self.fd)

    def pump(self) -> bool:
        changed = False
        while True:
            try:
                chunk = os.read(self.fd, EVENT_SIZE * 32)
            except BlockingIOError:
                break
            if not chunk:
                break
            for offset in range(0, len(chunk) // EVENT_SIZE * EVENT_SIZE, EVENT_SIZE):
                _s, _us, ev_type, code, value = struct.unpack(
                    EVENT_FORMAT, chunk[offset : offset + EVENT_SIZE]
                )
                if ev_type == EV_ABS and code in self.abs:
                    self.abs[code].value = value
                    if code == ABS_HAT0X:
                        self.hat_x = value
                    elif code == ABS_HAT0Y:
                        self.hat_y = value
                    elif code == ABS_MT_SLOT:
                        self.mt_slot = value
                    elif ABS_MT_SLOT < code <= ABS_MT_PRESSURE:
                        if code == ABS_MT_TRACKING_ID and value < 0:
                            self.mt_slots.pop(self.mt_slot, None)
                        else:
                            rec = self.mt_slots.setdefault(self.mt_slot, {})
                            rec[code] = value
                    changed = True
                elif ev_type == EV_REL:
                    if code == REL_X:
                        self.rel_x += value
                    elif code == REL_Y:
                        self.rel_y += value
                    elif code == REL_WHEEL:
                        self.rel_wheel += value
                    elif code == REL_HWHEEL:
                        self.rel_hwheel += value
                    changed = True
                elif ev_type == EV_KEY:
                    if value:
                        self.key_down.add(code)
                    else:
                        self.key_down.discard(code)
                    changed = True
                elif ev_type == EV_SYN and code == SYN_REPORT:
                    changed = True
        return changed

    def take_rel(self) -> tuple[int, int, int, int]:
        got = (self.rel_x, self.rel_y, self.rel_wheel, self.rel_hwheel)
        self.rel_x = self.rel_y = self.rel_wheel = self.rel_hwheel = 0
        return got


def iter_event_nodes() -> Iterable[Path]:
    root = Path("/dev/input")
    if not root.is_dir():
        return []
    return sorted(root.glob("event*"))


def open_matching(pred: Callable[[EvdevDevice], bool]) -> EvdevDevice | None:
    for path in iter_event_nodes():
        try:
            dev = EvdevDevice(path)
        except OSError:
            continue
        if pred(dev) and ABS_X in dev.abs and ABS_Y in dev.abs:
            return dev
        dev.close()
    return None


def pick_axis(dev: EvdevDevice, candidates: tuple[int, ...]) -> AbsAxis | None:
    for code in candidates:
        if code in dev.abs:
            return dev.abs[code]
    return None


def buttons_mask(dev: EvdevDevice, codes: list[int]) -> int:
    mask = 0
    for i, code in enumerate(codes[:32]):
        if code in dev.key_down:
            mask |= 1 << i
    return mask


class Adapter:
    name = "base"

    def matches(self, dev: EvdevDevice) -> bool:
        raise NotImplementedError

    def to_state(self, dev: EvdevDevice) -> JoyState:
        raise NotImplementedError


class Ta320Adapter(Adapter):
    name = "ta320"

    def matches(self, dev: EvdevDevice) -> bool:
        return dev.id.vendor == TA320_VID and dev.id.product == TA320_PID

    def to_state(self, dev: EvdevDevice) -> JoyState:
        x = pick_axis(dev, (ABS_X,))
        y = pick_axis(dev, (ABS_Y,))
        z = pick_axis(dev, (ABS_Z, ABS_THROTTLE, ABS_RX))
        r = pick_axis(dev, (ABS_RZ, ABS_RUDDER, ABS_RY))
        codes = [c for c in sorted(dev.keys) if c >= BTN_JOYSTICK] or sorted(dev.keys)
        return JoyState(
            x=x.scaled_u16() if x else 32767,
            y=y.scaled_u16() if y else 32767,
            z=z.scaled_u16() if z else 32767,
            r=r.scaled_u16() if r else 32767,
            buttons=buttons_mask(dev, codes),
            pov=hat_to_pov(dev.hat_x, dev.hat_y),
        )


class GenericJoystickAdapter(Adapter):
    name = "generic"

    def matches(self, dev: EvdevDevice) -> bool:
        # ABS_X/Y + any key matches trackpads. Require a joystick or gamepad
        # button range so mice and Magic Trackpads stay silent.
        if ABS_X not in dev.abs or ABS_Y not in dev.abs:
            return False
        if Ta320Adapter().matches(dev) or XboxEliteAdapter().matches(dev):
            return False
        return any(BTN_JOYSTICK <= code < BTN_GAMEPAD + 16 for code in dev.keys)

    def to_state(self, dev: EvdevDevice) -> JoyState:
        return Ta320Adapter().to_state(dev)


class XboxEliteAdapter(Adapter):
    """Microsoft xpad (Elite / One / 360). SB10: LX LY RX RY; triggers in button high bytes."""

    name = "xboxelite"

    def matches(self, dev: EvdevDevice) -> bool:
        if dev.id.vendor != XBOX_VID:
            return False
        if ABS_X not in dev.abs or ABS_Y not in dev.abs:
            return False
        return any(code >= BTN_GAMEPAD for code in dev.keys)

    def to_state(self, dev: EvdevDevice) -> JoyState:
        x = pick_axis(dev, (ABS_X,))
        y = pick_axis(dev, (ABS_Y,))
        rx = pick_axis(dev, (ABS_RX,))
        ry = pick_axis(dev, (ABS_RY,))
        lt = pick_axis(dev, (ABS_Z,))
        rt = pick_axis(dev, (ABS_RZ,))
        bits = 0
        for i, code in enumerate(
            (BTN_A, BTN_B, BTN_X, BTN_Y, BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_THUMBL, BTN_THUMBR, BTN_MODE)
        ):
            if code in dev.key_down:
                bits |= 1 << i
        lt8 = (lt.scaled_u16() >> 8) if lt else 0
        rt8 = (rt.scaled_u16() >> 8) if rt else 0
        bits |= (lt8 & 0xFF) << 16
        bits |= (rt8 & 0xFF) << 24
        return JoyState(
            x=x.scaled_u16() if x else 32767,
            y=y.scaled_u16() if y else 32767,
            z=rx.scaled_u16() if rx else 32767,
            r=ry.scaled_u16() if ry else 32767,
            buttons=bits,
            pov=hat_to_pov(dev.hat_x, dev.hat_y),
        )


ADAPTERS: dict[str, Adapter] = {
    "ta320": Ta320Adapter(),
    "xboxelite": XboxEliteAdapter(),
    "generic": GenericJoystickAdapter(),
}


def list_devices() -> None:
    print(f"{'path':<22} {'vid:pid':<11} {'abs':<18} name")
    for path in iter_event_nodes():
        try:
            dev = EvdevDevice(path)
        except OSError as exc:
            print(f"{path}  (open failed: {exc})")
            continue
        abs_names = []
        for code, label in (
            (ABS_X, "X"),
            (ABS_Y, "Y"),
            (ABS_Z, "Z"),
            (ABS_RZ, "RZ"),
            (ABS_THROTTLE, "THR"),
            (ABS_HAT0X, "HAT"),
        ):
            if code in dev.abs:
                abs_names.append(label)
        print(
            f"{str(path):<22} {dev.id.vendor:04X}:{dev.id.product:04X}  "
            f"{','.join(abs_names) or '-':<18} {dev.name}"
        )
        dev.close()


def wait_for_device(adapter: Adapter, timeout: float | None) -> EvdevDevice:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        dev = open_matching(adapter.matches)
        if dev is not None:
            return dev
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f"no device for adapter {adapter.name}")
        time.sleep(0.5)


def run_loop(host: str, port: int, adapter: Adapter, grab: bool, hz: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    endpoint = (host, port)
    period = 1.0 / max(30, hz)
    seq = 0
    print(f"usb-passer adapter={adapter.name} -> {host}:{port}  (Ctrl-C to stop)")
    while True:
        dev = wait_for_device(adapter, timeout=None)
        print(
            f"bound {dev.path}  {dev.name}  "
            f"VID_{dev.id.vendor:04X} PID_{dev.id.product:04X}"
        )
        if grab:
            dev.grab(True)
        selector = selectors.DefaultSelector()
        selector.register(dev.fd, selectors.EVENT_READ)
        last_send = 0.0
        try:
            while True:
                selector.select(timeout=period)
                try:
                    dev.pump()
                except OSError:
                    print(f"lost {dev.path}, waiting for replug")
                    break
                now = time.monotonic()
                if now - last_send < period:
                    continue
                last_send = now
                seq = (seq + 1) & 0xFFFFFFFF
                state = adapter.to_state(dev)
                sock.sendto(encode_sb10(seq, state), endpoint)
        finally:
            selector.close()
            dev.close()
        time.sleep(0.4)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Headless HID passer for SidestickBridge")
    parser.add_argument("--list", action="store_true", help="list evdev nodes and exit")
    parser.add_argument("--host", default=os.environ.get("USB_PASSER_HOST", "").strip())
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("USB_PASSER_PORT", DEFAULT_PORT)),
    )
    parser.add_argument(
        "--adapter",
        choices=sorted(ADAPTERS),
        default=os.environ.get("USB_PASSER_ADAPTER", "ta320"),
    )
    parser.add_argument("--no-grab", action="store_true", help="do not exclusive-grab the HID node")
    parser.add_argument("--hz", type=int, default=125)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.list:
        list_devices()
        return 0
    if not args.host:
        print("set --host or USB_PASSER_HOST (no LAN default)", file=sys.stderr)
        return 2
    if os.geteuid() != 0 and not Path("/dev/input").exists():
        print("need /dev/input — run as root or in the input group", file=sys.stderr)
        return 2
    adapter = ADAPTERS[args.adapter]
    try:
        run_loop(args.host, args.port, adapter, grab=not args.no_grab, hz=args.hz)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
