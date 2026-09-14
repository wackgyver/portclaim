"""Magic Trackpad adapter — Type B contacts out as TP10.

The hub does not recognize gestures. It grabs the MT evdev node and
forwards contacts to the claimed sink.
"""

from __future__ import annotations

import secrets
import selectors
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROTO = ROOT.parent / "proto"
for candidate in (ROOT, PROTO):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import tp10  # noqa: E402
import tp_native  # noqa: E402
from hid import (  # noqa: E402
    ABS_MT_ORIENTATION,
    ABS_MT_POSITION_X,
    ABS_MT_POSITION_Y,
    ABS_MT_PRESSURE,
    ABS_MT_SLOT,
    ABS_MT_TOUCH_MAJOR,
    ABS_MT_TOUCH_MINOR,
    ABS_MT_TRACKING_ID,
    BTN_LEFT,
    EvdevDevice,
    iter_event_nodes,
)

MAGIC_VID = 0x05AC
MAGIC_PID = 0x0265
ADAPTER = "magictrackpad"


def matches(dev: EvdevDevice) -> bool:
    return (
        dev.id.vendor == MAGIC_VID
        and dev.id.product == MAGIC_PID
        and ABS_MT_SLOT in dev.abs
        and ABS_MT_POSITION_X in dev.abs
    )


def open_trackpads() -> list[EvdevDevice]:
    found: list[EvdevDevice] = []
    for path in iter_event_nodes():
        try:
            dev = EvdevDevice(path)
        except OSError:
            continue
        if matches(dev):
            found.append(dev)
        else:
            dev.close()
    return found


def _close_trackpads(devs: list[EvdevDevice]) -> None:
    for dev in devs:
        try:
            dev.close()
        except OSError:
            pass


def wait_for_trackpads(ready: float = 3.0, want: int = 2) -> list[EvdevDevice]:
    """hid-magicmouse publishes iface 0 first; iface 1 follows ~1s later.

    Opening the first node and grabbing it is how Ultrabase plugs go silent
    (empty TP10, error -32 on the sibling interface).
    """
    deadline = time.monotonic() + max(0.2, ready)
    sibling_deadline = None
    while True:
        found = open_trackpads()
        # Bluetooth exposes one interface; this USB model needs both before grab.
        required = 1 if found and found[0].id.bustype == 5 else want
        if len(found) >= required:
            return found
        now = time.monotonic()
        if found and sibling_deadline is None:
            sibling_deadline = now + max(0.2, ready)
        _close_trackpads(found)
        if now >= (sibling_deadline if sibling_deadline is not None else deadline):
            return []  # Never grab a half-initialized USB pair.
        time.sleep(0.2)


def merge_contacts(devs: list[EvdevDevice]) -> list[tuple[int, int, int, int, int, int, int]]:
    by_id: dict[int, tuple[int, int, int, int, int, int, int]] = {}
    for dev in devs:
        for row in contacts_of(dev):
            by_id[row[1]] = row
    rows = list(by_id.values())
    rows.sort(key=lambda r: r[0])
    return rows[: tp10.MAX_CONTACTS]


def native_contacts(devs: list[EvdevDevice]) -> tuple[tp_native.Contact, ...]:
    by_id = {}
    for dev in devs:
        for slot, rec in sorted(dev.mt_slots.items()):
            tid = rec.get(ABS_MT_TRACKING_ID, -1)
            if tid < 0 or ABS_MT_POSITION_X not in rec or ABS_MT_POSITION_Y not in rec:
                continue
            by_id[tid] = tp_native.Contact(
                slot, tid, rec[ABS_MT_POSITION_X], rec[ABS_MT_POSITION_Y],
                rec.get(ABS_MT_PRESSURE, 0), rec.get(ABS_MT_TOUCH_MAJOR, 0),
                rec.get(ABS_MT_TOUCH_MINOR, 0), rec.get(ABS_MT_ORIENTATION, 0),
            )
    return tuple(sorted(by_id.values(), key=lambda c: c.tracking_id)[:tp10.MAX_CONTACTS])


def descriptor() -> dict:
    """Real kernel geometry, queried only at native receiver initialization."""
    devs = open_trackpads()
    try:
        if not devs:
            raise OSError("Magic Trackpad is not connected")
        dev = devs[0]
        codes = (ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_PRESSURE,
                 ABS_MT_TOUCH_MAJOR, ABS_MT_TOUCH_MINOR, ABS_MT_ORIENTATION)
        if any(code not in dev.abs for code in codes):
            raise OSError("Magic Trackpad geometry is incomplete")
        return {
            "schema": 1, "transport": "TP10/N1", "name": dev.name,
            "vendor": dev.id.vendor, "product": dev.id.product,
            "bustype": dev.id.bustype, "version": dev.id.version,
            "max_contacts": tp10.MAX_CONTACTS,
            "axes": {str(code): {"minimum": dev.abs[code].minimum,
                                  "maximum": dev.abs[code].maximum,
                                  "resolution": dev.abs[code].resolution}
                     for code in codes},
        }
    finally:
        _close_trackpads(devs)


def merge_buttons(devs: list[EvdevDevice]) -> int:
    bits = 0
    for dev in devs:
        bits |= buttons_of(dev)
    return bits


def inventory() -> list[dict]:
    rows = []
    seen: set[str] = set()
    for path in iter_event_nodes():
        try:
            dev = EvdevDevice(path)
        except OSError:
            continue
        if matches(dev) and str(dev.path) not in seen:
            seen.add(str(dev.path))
            rows.append(
                {
                    "path": str(dev.path),
                    "name": dev.name,
                    "vid": f"{dev.id.vendor:04X}",
                    "pid": f"{dev.id.product:04X}",
                    "adapters": [ADAPTER],
                    "kind": "hid",
                }
            )
        dev.close()
    return rows


def _axis(rec: dict[int, int], code: int, fallback: int = 0) -> int:
    return int(rec.get(code, fallback))


def contacts_of(dev: EvdevDevice) -> list[tuple[int, int, int, int, int, int, int]]:
    rows: list[tuple[int, int, int, int, int, int, int]] = []
    for slot, rec in sorted(dev.mt_slots.items()):
        tracking = _axis(rec, ABS_MT_TRACKING_ID, slot)
        if _axis(rec, ABS_MT_TRACKING_ID, -1) < 0 and ABS_MT_POSITION_X not in rec:
            continue
        rows.append(
            (
                slot,
                tracking,
                _axis(rec, ABS_MT_POSITION_X),
                _axis(rec, ABS_MT_POSITION_Y),
                _axis(rec, ABS_MT_PRESSURE),
                _axis(rec, ABS_MT_TOUCH_MAJOR),
                _axis(rec, ABS_MT_TOUCH_MINOR),
            )
        )
        if len(rows) >= tp10.MAX_CONTACTS:
            break
    return rows


def buttons_of(dev: EvdevDevice) -> int:
    bits = 0
    if BTN_LEFT in dev.key_down:
        bits |= tp10.BTN_CLICK
    return bits


def stream_trackpad(route_fn, adapter_name: str = ADAPTER, hz: int = 125, grab: bool = True) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = 0
    miss_log = 0.0
    while True:
        route = route_fn(adapter_name)
        if route is None:
            time.sleep(0.2)
            continue
        devs = wait_for_trackpads()
        if not devs:
            now = time.monotonic()
            if now - miss_log >= 10.0:
                print("trackpad: waiting for complete Magic Trackpad interfaces", flush=True)
                miss_log = now
            time.sleep(0.5)
            continue
        paths = ",".join(str(d.path) for d in devs)
        print(f"stream {adapter_name} {paths} -> {route['dest_host']}:{route['dest_port']}", flush=True)
        epoch = secrets.randbits(32)
        last_send = 0.0
        last_log = 0.0

        def send(capture_us, discontinuity=False, neutral=False):
            nonlocal seq, epoch, last_send, last_log
            current = route_fn(adapter_name)
            if current is None:
                return
            if discontinuity:
                epoch = secrets.randbits(32)
            contacts = () if neutral else native_contacts(devs)
            buttons = 0 if neutral else merge_buttons(devs)
            rel = [0, 0, 0, 0]
            for dev in devs:
                for i, value in enumerate(dev.take_rel()):
                    rel[i] += value
            seq = (seq + 1) & 0xFFFFFFFF
            packet = tp_native.encode(tp_native.Frame(seq, buttons, contacts, epoch, capture_us,
                                                     (0, 0, 0, 0) if neutral else tuple(rel)))
            sock.sendto(packet, (current["dest_host"], current["dest_port"]))
            last_send = time.monotonic()
            if last_send - last_log >= 5.0:
                print(f"tp10/n1 seq={seq} fingers={len(contacts)} buttons={buttons}", flush=True)
                last_log = last_send

        def report(_dev, capture_us, discontinuity):
            send(capture_us, discontinuity)

        try:
            with selectors.DefaultSelector() as selector:
                for dev in devs:
                    if grab:
                        dev.grab(True)
                        if not dev.grabbed:
                            raise OSError(f"cannot exclusively capture {dev.path}")
                    dev.sync_state()
                    selector.register(dev.fd, selectors.EVENT_READ, dev)
                send(time.monotonic_ns() // 1000)
                while True:
                    current = route_fn(adapter_name)
                    if current is None:
                        break
                    # Native gestures are clocked locally by libinput/apps. Legacy
                    # Windows GestureEngine still needs its 125 Hz empty frames.
                    period = 0.1 if current.get("native_touchpad") else 1.0 / max(30, hz)
                    timeout = max(0.0, last_send + period - time.monotonic())
                    ready = selector.select(timeout)
                    for key, _mask in ready:
                        key.data.pump(on_frame=report)
                    if time.monotonic() - last_send >= period:
                        send(time.monotonic_ns() // 1000)
        except OSError as exc:
            print(f"trackpad capture interrupted: {exc}", flush=True)
            try:
                send(time.monotonic_ns() // 1000, discontinuity=True, neutral=True)
            except OSError:
                pass  # Receiver watchdog independently cancels held gestures.
        finally:
            _close_trackpads(devs)
        time.sleep(0.3)
