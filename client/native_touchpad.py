"""Linux Type-B touchpad injection. No pointer scaling or gesture recognition here."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import socket
import sys
import time
from urllib.parse import urlparse

PROTO = Path(__file__).resolve().parents[1] / "proto"
if str(PROTO) not in sys.path:
    sys.path.insert(0, str(PROTO))
import tp_native
from stream_guard import StreamGuard

# Linux input ABI codes; evdev is loaded only when opening a real device.
EV_KEY, EV_ABS = 1, 3
ABS_X, ABS_Y, ABS_PRESSURE = 0, 1, 24
SLOT, MAJOR, MINOR, ORIENTATION, X, Y, TRACKING, PRESSURE = 47, 48, 49, 52, 53, 54, 57, 58
BTN_LEFT, BTN_TOUCH = 272, 330
TOOL_KEYS = (325, 333, 334, 335, 328)  # finger, double, triple, quad, quint
AXES = (X, Y, PRESSURE, MAJOR, MINOR, ORIENTATION)
NAME = "PortClaim Magic Trackpad"


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid native touchpad descriptor value")
    return value


@dataclass(frozen=True)
class Axis:
    minimum: int
    maximum: int
    resolution: int

    def contains(self, value):
        return self.minimum <= value <= self.maximum


class Descriptor:
    def __init__(self, raw):
        if not isinstance(raw, dict) or raw.get("schema") != 1 or raw.get("transport") != "TP10/N1":
            raise ValueError("hub does not provide a TP10/N1 touchpad descriptor")
        self.vendor = _integer(raw.get("vendor"), 1, 65535)
        self.product = _integer(raw.get("product"), 1, 65535)
        self.bustype = _integer(raw.get("bustype"), 1, 65535)
        self.version = _integer(raw.get("version"), 0, 65535)
        self.slots = _integer(raw.get("max_contacts"), 1, 5)
        self.axes = {}
        for code in AXES:
            info = raw["axes"][str(code)]
            signed = code in (X, Y, ORIENTATION)
            limit = 32767 if signed else 65535
            low = _integer(info["minimum"], -32768 if signed else 0, limit)
            high = _integer(info["maximum"], low + 1, limit)
            resolution = _integer(info["resolution"], 1 if code in (X, Y) else 0, 10000)
            self.axes[code] = Axis(low, high, resolution)
        for code in (X, Y):
            axis = self.axes[code]
            if not 10 <= (axis.maximum - axis.minimum) / axis.resolution <= 1000:
                raise ValueError("implausible native touchpad dimensions")


class NativeTouchpad:
    def __init__(self, descriptor: Descriptor, *, ui_factory=None, name=NAME):
        self.descriptor = descriptor
        if ui_factory is None:
            from evdev import UInput
            ui_factory = UInput
        self.ui_factory, self.name = ui_factory, name
        self.ui = None
        self.slots = {}
        self.values = {}
        self.state = None
        self._open()

    def _open(self):
        desc = self.descriptor
        axes = [(SLOT, (0, 0, desc.slots - 1, 0, 0, 0)),
                (TRACKING, (-1, 0, 0x7FFFFFFF, 0, 0, 0))]
        for code in AXES:
            axis = desc.axes[code]
            # Source input-core filtering has already happened. Do not defuzz twice.
            axes.append((code, (0, axis.minimum, axis.maximum, 0, 0, axis.resolution)))
        for code, source in ((ABS_X, X), (ABS_Y, Y), (ABS_PRESSURE, PRESSURE)):
            axis = desc.axes[source]
            axes.append((code, (0, axis.minimum, axis.maximum, 0, 0, axis.resolution)))
        self.ui = self.ui_factory(
            {EV_KEY: [BTN_LEFT, BTN_TOUCH, *TOOL_KEYS], EV_ABS: axes},
            name=self.name, vendor=desc.vendor, product=desc.product,
            version=desc.version, bustype=desc.bustype, phys="portclaim/native-touchpad",
            input_props=[0, 2],  # INPUT_PROP_POINTER | INPUT_PROP_BUTTONPAD
        )
        self.slots.clear()
        self.values.clear()
        self.state = None

    @property
    def active(self):
        return bool(self.state and (self.state[0] or self.state[1]))

    def valid(self, frame):
        if len(frame.contacts) > self.descriptor.slots or frame.buttons & ~1:
            return False
        seen = set()
        for c in frame.contacts:
            if not 0 <= c.tracking_id <= 0x7FFFFFFF or c.tracking_id in seen:
                return False
            seen.add(c.tracking_id)
            for code, value in zip(AXES, (c.x, c.y, c.pressure, c.major, c.minor, c.orientation)):
                if not self.descriptor.axes[code].contains(value):
                    return False
        return True

    def _emit(self, kind, code, value, slot=None):
        key = (slot, kind, code)
        if self.values.get(key) != value:
            self.ui.write(kind, code, value)
            self.values[key] = value

    def feed(self, frame):
        if not self.valid(frame):
            return False
        contacts = tuple(sorted(frame.contacts, key=lambda c: c.tracking_id))
        state = (frame.buttons, contacts)
        if state == self.state:
            return True  # Heartbeat freshness belongs to the transport, not uinput.
        if self.ui is None:
            self._open()
        ids = {c.tracking_id for c in contacts}
        self._emit(EV_KEY, BTN_TOUCH, int(bool(contacts)))
        for tid in tuple(self.slots):
            if tid not in ids:
                slot = self.slots.pop(tid)
                self.ui.write(EV_ABS, SLOT, slot)
                self._emit(EV_ABS, TRACKING, -1, slot)
        free = sorted(set(range(self.descriptor.slots)) - set(self.slots.values()))
        for c in contacts:
            if c.tracking_id not in self.slots:
                self.slots[c.tracking_id] = free.pop(0)
            slot = self.slots[c.tracking_id]
            self.ui.write(EV_ABS, SLOT, slot)
            self._emit(EV_ABS, TRACKING, c.tracking_id, slot)
            for code, value in zip(AXES, (c.x, c.y, c.pressure, c.major, c.minor, c.orientation)):
                self._emit(EV_ABS, code, value, slot)
        if contacts:
            first = min(contacts, key=lambda c: self.slots[c.tracking_id])
            for code, value in ((ABS_X, first.x), (ABS_Y, first.y), (ABS_PRESSURE, first.pressure)):
                self._emit(EV_ABS, code, value)
        else:
            self._emit(EV_ABS, ABS_PRESSURE, 0)
        for count, code in enumerate(TOOL_KEYS, 1):
            self._emit(EV_KEY, code, int(len(contacts) == count))
        self._emit(EV_KEY, BTN_LEFT, frame.buttons & 1)
        self.ui.syn()
        self.state = state
        return True

    def cancel(self):
        # Removal cancels libinput gestures/drag locks without synthesizing a tap.
        # Recreate lazily on the next valid frame, never in a restart loop.
        if self.ui is not None:
            self.ui.close()
            self.ui = None
        self.slots.clear()
        self.values.clear()
        self.state = None

    close = cancel


def discard_pending(sock):
    """Never replay input accumulated while a device/descriptor was initializing."""
    discarded = 0
    sock.setblocking(False)
    try:
        for _ in range(512):
            try:
                sock.recvfrom(2048)
                discarded += 1
            except BlockingIOError:
                break
    finally:
        sock.settimeout(0.1)
    return discarded


def serve(port, stats):
    import claim
    stats.update(listening=False, error="", backend="native", tp10_mode="native: initializing")
    pad = None
    try:
        hub = os.environ.get("USB_LOOM_HUB", "").strip()
        host = urlparse(hub).hostname
        if not host:
            raise ValueError("native touchpad needs USB_LOOM_HUB")
        allowed = {r[4][0] for r in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)}
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("0.0.0.0", port))
            sock.settimeout(0.1)
            retry = 2.0
            while True:
                try:
                    desc = Descriptor(claim.request(hub, "GET", "/v1/devices/magictrackpad/descriptor"))
                    break
                except (OSError, ValueError, KeyError, TypeError, SystemExit) as exc:
                    stats["error"] = f"Waiting for native geometry: {exc}"
                    print(stats["error"], flush=True)
                    time.sleep(retry)
                    retry = min(30.0, retry * 2)
            pad = NativeTouchpad(desc)
            stats["tp10_startup_discarded"] = discard_pending(sock)
            stats["error"] = ""
            guard = StreamGuard(timeout=0.5)
            stats.update(listening=True, tp10_mode="native: waiting")
            print(f"TP10/N1 listening UDP {port} (native libinput touchpad)", flush=True)
            while True:
                now = time.monotonic()
                if guard.expired(now):
                    if pad.active:
                        pad.cancel()
                    stats["tp10_mode"] = "native: disconnected"
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                if addr[0] not in allowed:
                    continue
                frame = tp_native.decode(data)
                if frame is None or not pad.valid(frame):
                    continue
                if not guard.accept(frame.seq, time.monotonic(), frame.epoch):
                    continue
                if guard.resync and pad.active:
                    pad.cancel()
                if pad.ui is None:
                    pad._open()
                    stats["tp10_startup_discarded"] += discard_pending(sock) + 1
                    continue
                pad.feed(frame)
                stats.update(tp10_last=time.time(), tp10_packets=stats.get("tp10_packets", 0) + 1,
                             tp10_mode=f"native: {len(frame.contacts)} contacts", tp10_sticky=False,
                             tp10_missing=guard.missing, tp10_rejected=guard.rejected)
    except (Exception, SystemExit) as exc:
        stats["error"] = str(exc) or type(exc).__name__
        print(f"Native touchpad failed: {stats['error']}", flush=True)
    finally:
        stats["listening"] = False
        if pad is not None:
            pad.close()
