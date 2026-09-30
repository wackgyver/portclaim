"""Opt-in UVC camera discovery and single-owner, short-lived stream leases.

Discovery reads sysfs only. Claim reserves; an authenticated stream request is
what opens the camera. No auto-claim, audio capture, encoder, recording or retry.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
for candidate in (ROOT, ROOT.parent / "proto"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
import mjpeg
from v4l2_capture import Capture


class CameraError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _text(path):
    return path.read_text(errors="replace").strip()


@dataclass(frozen=True)
class Source:
    path: str
    name: str
    vid: str
    pid: str
    identity: str
    sys_node: Path = field(repr=False)

    def verify(self):
        current = source_from_sysfs(self.sys_node)
        if current is None or current.identity != self.identity or current.path != self.path:
            raise OSError("camera disconnected or changed since selection; claim again explicitly")

    def public(self):
        return {"path": self.path, "name": self.name, "vid": self.vid, "pid": self.pid,
                "kind": "video", "adapters": ["webcam"]}


def source_from_sysfs(node):
    try:
        if _text(node / "index") != "0" or (node / "device/driver").resolve().name != "uvcvideo":
            return None
        parent = next(p for p in node.resolve().parents if (p / "idVendor").exists())
        vid, pid = _text(parent / "idVendor"), _text(parent / "idProduct")
        # A reconnect gets a new devnum. Never switch a live lease to another
        # camera (even one with the same model) after hotplug/node-number reuse.
        identity = hashlib.sha256((str(node.resolve()) + ":" + vid + ":" + pid + ":"
                                   + _text(parent / "devnum")).encode()).hexdigest()
        return Source("/dev/" + node.name, _text(node / "name"), vid.upper(), pid.upper(), identity, node)
    except (OSError, StopIteration, RuntimeError):
        return None


def sources():
    return [source for node in sorted(Path("/sys/class/video4linux").glob("video*"))
            if (source := source_from_sysfs(node)) is not None]


def choose_source():
    rows = sources()
    selected = os.environ.get("USB_LOOM_CAMERA_DEVICE", "").strip()
    if selected:
        path = Path(selected)
        if not path.is_absolute() or str(path.parent) not in {"/dev/v4l/by-id", "/dev/v4l/by-path"}:
            raise CameraError(503, "USB_LOOM_CAMERA_DEVICE must be a stable /dev/v4l/by-id or by-path link")
        try:
            resolved = str(path.resolve())
        except (OSError, RuntimeError):
            raise CameraError(503, "selected USB camera link is unavailable") from None
        rows = [row for row in rows if row.path == resolved]
    if len(rows) != 1:
        raise CameraError(503, "select one available USB camera with USB_LOOM_CAMERA_DEVICE")
    return rows[0]


def inventory():
    return [source.public() for source in sources()]


@dataclass
class Lease:
    owner: str
    peer: str
    source: Source
    mode: mjpeg.Mode
    token: str = field(repr=False)
    deadline: float
    claimed_at: float
    streaming: bool = False

    def public(self):
        return {"adapter": "webcam", "client_id": self.owner, "transport": "http-mjpeg-v1",
                "mode": self.mode.as_dict(), "claimed_at": self.claimed_at, "streaming": self.streaming}


class Camera:
    def __init__(self, select_source=choose_source, capture=Capture, clock=time.monotonic, *, enabled=True):
        self.select_source, self.capture, self.clock = select_source, capture, clock
        self.enabled = enabled
        self.lock = threading.Lock()
        self.stream_lock = threading.Lock()
        self.lease = None

    def _current(self):
        if self.lease is not None and self.clock() >= self.lease.deadline:
            self.lease = None
        return self.lease

    def route(self):
        with self.lock:
            lease = self._current()
            return lease.public() if lease else None

    def claim(self, body, peer):
        if not self.enabled:
            raise CameraError(503, "webcam disabled on hub; operator must opt in with USB_LOOM_CAMERA_ENABLED=1")
        if not isinstance(body, dict) or set(body) - {"client_id", "mode"}:
            raise ValueError("webcam claim accepts only client_id and mode")
        owner = body.get("client_id")
        if not isinstance(owner, str) or not 1 <= len(owner) <= 128 or not owner.isprintable():
            raise ValueError("webcam client_id required (1-128 printable characters)")
        mode = mjpeg.Mode.parse(body.get("mode", {}))
        source = self.select_source()
        with self.lock:
            if self._current() or self.stream_lock.locked():
                raise CameraError(409, "webcam already owned or stopping; release it before claiming")
            lease = Lease(owner, peer, source, mode, secrets.token_urlsafe(32), self.clock() + 5, time.time())
            self.lease = lease
            # Lease secret appears ONLY in this authenticated claim response.
            return {**lease.public(), "lease": lease.token, "stream_path": mjpeg.STREAM_PATH}

    def _authorized(self, token, peer):
        lease = self._current()
        if (lease is None or not isinstance(token, str) or not token.isascii()
                or not hmac.compare_digest(lease.token, token) or lease.peer != peer):
            raise CameraError(403, "invalid or expired webcam lease")
        return lease

    def release(self, token, peer):
        with self.lock:
            self._authorized(token, peer)
            self.lease = None

    def alive(self, lease):
        with self.lock:
            return self._current() is lease

    def touch(self, lease):
        with self.lock:
            if self._current() is not lease:
                raise CameraError(403, "webcam lease ended")
            lease.deadline = self.clock() + 3

    @contextmanager
    def session(self, token, peer):
        with self.lock:
            lease = self._authorized(token, peer)
            if not self.stream_lock.acquire(blocking=False):
                raise CameraError(409, "webcam stream already open")
        try:
            with self.capture(lease.source, lease.mode) as capture:
                if not self.alive(lease):
                    raise CameraError(403, "webcam lease ended during capture preparation")
                with self.lock:
                    lease.streaming = True
                yield lease, capture
        finally:
            with self.lock:
                if self.lease is lease:
                    self.lease = None
                self.stream_lock.release()
