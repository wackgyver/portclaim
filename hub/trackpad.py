"""Magic Trackpad adapter — Type B contacts out as TP10.

The hub does not recognize gestures. It grabs the MT evdev node and
forwards contacts to the claimed sink.
"""

from __future__ import annotations

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
from hid import (  # noqa: E402
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


def merge_contacts(devs: list[EvdevDevice]) -> list[tuple[int, int, int, int, int, int, int]]:
    by_id: dict[int, tuple[int, int, int, int, int, int, int]] = {}
    for dev in devs:
        for row in contacts_of(dev):
            by_id[row[1]] = row
    rows = list(by_id.values())
    rows.sort(key=lambda r: r[0])
    return rows[: tp10.MAX_CONTACTS]


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
    period = 1.0 / max(30, hz)
    seq = 0
    miss_log = 0.0
    while True:
        route = route_fn(adapter_name)
        if route is None:
            time.sleep(0.2)
            continue
        devs = open_trackpads()
        if not devs:
            now = time.monotonic()
            if now - miss_log >= 10.0:
                print("trackpad: no Magic Trackpad MT node yet", file=sys.stderr, flush=True)
                miss_log = now
            time.sleep(0.5)
            continue
        paths = ",".join(str(d.path) for d in devs)
        print(f"stream {adapter_name} {paths} -> {route['dest_host']}:{route['dest_port']}", flush=True)
        # hid-magicmouse rewrites the battery descriptor and sends the MT
        # feature report. EVIOCGRAB in that window drops the pad on a hub
        # (Ultrabase / Ultra Dock) with error -32.
        time.sleep(3.0)
        if grab:
            for dev in devs:
                dev.grab(True)
        last = 0.0
        try:
            while route_fn(adapter_name):
                lost = False
                for dev in devs:
                    try:
                        dev.pump()
                    except OSError:
                        print(f"lost {dev.path}", flush=True)
                        lost = True
                        break
                if lost:
                    break
                now = time.monotonic()
                if now - last < period:
                    time.sleep(max(0.0, period - (now - last)))
                    continue
                last = now
                seq = (seq + 1) & 0xFFFFFFFF
                current = route_fn(adapter_name)
                if current is None:
                    break
                contacts = merge_contacts(devs)
                buttons = merge_buttons(devs)
                rel = [0, 0, 0, 0]
                for dev in devs:
                    dx, dy, wh, hw = dev.take_rel()
                    rel[0] += dx
                    rel[1] += dy
                    rel[2] += wh
                    rel[3] += hw
                if seq == 1 or seq % 125 == 0 or contacts or any(rel) or buttons:
                    print(
                        f"tp10 seq={seq} fingers={len(contacts)} buttons={buttons} "
                        f"rel={rel}",
                        flush=True,
                    )
                sock.sendto(
                    tp10.encode_tp10(seq, buttons, contacts, tuple(rel)),
                    (current["dest_host"], current["dest_port"]),
                )
        finally:
            for dev in devs:
                dev.close()
        time.sleep(0.3)
