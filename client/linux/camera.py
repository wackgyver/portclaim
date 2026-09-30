"""Explicit Linux webcam receiver: MJPEG -> FFmpeg -> owned V4L2 loopback.

No driver/module installation, camera capture or subprocess on import/startup.
The output must be a separately provisioned exclusive-caps PortClaim Camera.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import re
import select
import shutil
import stat
import struct
import subprocess
import threading
import time

from client.common.camera import CameraClient
from proto.mjpeg import Mode, validate_jpeg

LABEL = b"PortClaim Camera"
CID_BASE = 0x00980900 | 0xf000


def ioctl(fd, number, data, direction=3):
    request = (direction << 30) | (len(data) << 16) | (ord("V") << 8) | number
    fcntl.ioctl(fd, request, data, True)
    return data


class Loopback:
    def __init__(self, path, mode):
        self.fd = None
        self.configured = False
        self.frame_size = mode.width * mode.height * 2
        self.black = b"\x10\x80\x10\x80" * (mode.width * mode.height // 2)
        try:
            path = Path(path)
            if not re.fullmatch(r"/dev/video[0-9]+", str(path)):
                raise OSError("set USB_LOOM_CAMERA_OUTPUT to the provisioned /dev/videoN")
            sys_path = (Path("/sys/class/video4linux") / path.name).resolve()
            if not sys_path.is_relative_to("/sys/devices/virtual/video4linux"):
                raise OSError("camera output must be a virtual device, not a physical camera")
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
            st = os.fstat(self.fd)
            if not stat.S_ISCHR(st.st_mode) or os.major(st.st_rdev) != 81:
                raise OSError("camera output is not a V4L2 character device")
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            cap = ioctl(self.fd, 0, bytearray(104), 2)
            driver = bytes(cap[:16]).split(b"\0", 1)[0]
            label = bytes(cap[16:48]).split(b"\0", 1)[0]
            caps, device_caps = struct.unpack_from("=II", cap, 84)
            caps = device_caps if caps & 0x80000000 else caps
            if driver != b"v4l2 loopback" or label != LABEL or caps & 3 != 2:
                raise OSError("need an unused exclusive-caps v4l2loopback named PortClaim Camera")
            keep = ioctl(self.fd, 27, bytearray(struct.pack("=Ii", CID_BASE, 0)))
            if struct.unpack("=Ii", keep)[1] != 0:
                raise OSError("loopback keep_format must be off before PortClaim takes ownership")
            offset = 8 if struct.calcsize("P") == 8 else 4
            fmt = bytearray(offset + 200)
            struct.pack_into("=I", fmt, 0, 2)  # VIDEO_OUTPUT
            struct.pack_into("=7I", fmt, offset, mode.width, mode.height,
                             int.from_bytes(b"YUYV", "little"), 1,
                             mode.width * 2, self.frame_size, 3)  # REC709, limited range
            ioctl(self.fd, 5, fmt)
            if struct.unpack_from("=6I", fmt, offset) != (
                    mode.width, mode.height, int.from_bytes(b"YUYV", "little"),
                    1, mode.width * 2, self.frame_size):
                raise OSError("loopback refused the requested raw video format")
            self.configured = True
            # S_FMT acquired the kernel's OUTPUT format token before changing
            # controls. A racing producer must fail before its controls are touched.
            # Keep timeout armed after stop; no indefinite last frame after a crash.
            self._control(CID_BASE + 1, 0)     # no frame duplication/fake liveness
            self._control(CID_BASE + 2, 1000)  # kernel NULL frame after 1s
            parm = bytearray(204)
            struct.pack_into("=I", parm, 0, 2)
            struct.pack_into("=II", parm, 12, 1, mode.fps)
            ioctl(self.fd, 22, parm)
            numerator, denominator = struct.unpack_from("=II", parm, 12)
            if not numerator or denominator != numerator * mode.fps:
                raise OSError("loopback refused the requested frame rate")
            self.write(self.black)
        except BaseException:
            self.close()
            raise

    def _control(self, control, value):
        data = bytearray(struct.pack("=Ii", control, value))
        ioctl(self.fd, 28, data)
        ioctl(self.fd, 27, data)
        if struct.unpack("=Ii", data) != (control, value):
            raise OSError("loopback safety controls could not be verified")

    def write(self, frame):
        if len(frame) != self.frame_size:
            raise ValueError("wrong decoded camera frame size")
        # V4L2 write() is frame-based: never retry a short write as another frame.
        if os.write(self.fd, frame) != len(frame):
            raise OSError("short write to virtual camera")

    def close(self):
        if self.fd is not None:
            try:
                if self.configured:
                    self.write(self.black)
            finally:
                os.close(self.fd)
                self.fd = None
                self.configured = False


def decoder_command(executable, mode):
    return [executable, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-threads", "1", "-filter_threads", "1", "-protocol_whitelist", "pipe",
            "-f", "mjpeg", "-framerate", str(mode.fps), "-probesize", "32", "-analyzeduration", "0",
            "-i", "pipe:0", "-an", "-sn", "-dn", "-threads", "1",
            "-vf", "scale=in_range=full:out_range=limited:out_color_matrix=bt709",
            "-pix_fmt", "yuyv422", "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]


class Decoder:
    def __init__(self, executable, mode, output, stop, on_frame):
        self.mode, self.output, self.stop, self.on_frame = mode, output, stop, on_frame
        self.error = None
        self.last_input = None
        self.first_input = None
        self.closed = threading.Event()
        self.process = subprocess.Popen(decoder_command(executable, mode), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                                        # No token, FFREPORT, preload or unrelated credential environment.
                                        env={"PATH": "/usr/bin:/bin", "LANG": "C"}, close_fds=True)
        try:
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
            self.thread = threading.Thread(target=self._drain, name="camera-decode", daemon=True)
            self.thread.start()
        except BaseException:
            self.process.kill()
            self.process.wait(timeout=2)
            self.process.stdin.close()
            self.process.stdout.close()
            raise

    def _drain(self):
        buffer = bytearray()
        size = self.mode.width * self.mode.height * 2
        last_output = None
        try:
            while not self.closed.is_set() and not self.stop.is_set():
                deadline_from = last_output if last_output is not None else self.first_input
                if deadline_from is not None and time.monotonic() - deadline_from > 3:
                    raise TimeoutError("camera decoder produced no output for 3 seconds")
                if not select.select([self.process.stdout], [], [], 0.1)[0]:
                    continue
                data = os.read(self.process.stdout.fileno(), min(65536, size - len(buffer)))
                if not data:
                    raise OSError("camera decoder exited or closed its output")
                buffer.extend(data)
                if len(buffer) == size:
                    if self.closed.is_set() or self.stop.is_set():
                        break
                    if self.last_input is None or time.monotonic() - self.last_input > 1:
                        raise TimeoutError("discarding stale decoded camera output")
                    self.output.write(buffer)
                    buffer.clear()
                    last_output = time.monotonic()
                    self.on_frame()
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__

    def feed(self, frame):
        validate_jpeg(frame, self.mode)
        deadline = time.monotonic() + 1
        self.last_input = time.monotonic()
        if self.first_input is None:
            self.first_input = self.last_input
        remaining = memoryview(frame)
        while remaining:
            if self.error:
                raise OSError(self.error)
            if self.stop.is_set() or self.closed.is_set():
                raise InterruptedError("camera stopped")
            if time.monotonic() >= deadline:
                raise TimeoutError("camera decoder backpressure exceeded 1 second")
            if not select.select([], [self.process.stdin], [], 0.1)[1]:
                continue
            try:
                count = os.write(self.process.stdin.fileno(), remaining[:65536])
            except BlockingIOError:
                continue
            if not count:
                raise OSError("camera decoder stopped accepting input")
            remaining = remaining[count:]

    def close(self):
        self.closed.set()
        try:
            if self.process.poll() is None:
                self.process.kill()  # Only this camera's child, never another FFmpeg/OBS process.
            self.process.wait(timeout=2)
        finally:
            self.thread.join(timeout=2)
            self.process.stdin.close()
            self.process.stdout.close()
            if self.thread.is_alive():
                raise RuntimeError("camera decoder worker did not stop")


class Controller:
    """One explicit activation per receiver process; failures never auto-reclaim."""
    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker = None
        self.state = "off"
        self.error = ""
        self.frames = 0
        self.last_frame = 0.0

    def snapshot(self):
        with self.lock:
            return {"state": self.state, "error": self.error, "frames": self.frames,
                    "last_frame": self.last_frame, "busy": bool(self.worker and self.worker.is_alive())}

    def start(self, hub, token, client_id, mode=None):
        mode = mode or Mode()
        try:
            executable = shutil.which("ffmpeg")
            path = os.environ.get("USB_LOOM_CAMERA_OUTPUT", "").strip()
            if not executable or not path:
                raise OSError("webcam needs FFmpeg and a provisioned USB_LOOM_CAMERA_OUTPUT; see docs/webcam.md")
            client = CameraClient(hub, token, client_id)
        except Exception as exc:
            with self.lock:
                if not self.worker or not self.worker.is_alive():
                    self.state, self.error = "error", str(exc) or type(exc).__name__
            raise
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise RuntimeError("camera already active or stopping")
            self.stop_event.clear()
            self.state, self.error, self.frames, self.last_frame = "preparing", "", 0, 0.0
            self.worker = threading.Thread(target=self._run, args=(client, executable, path, mode),
                                           name="camera-receiver", daemon=True)
            self.worker.start()

    def _frame(self):
        with self.lock:
            self.frames += 1
            self.last_frame = time.monotonic()
            if not self.stop_event.is_set():
                self.state = "streaming"

    def _run(self, client, executable, path, mode):
        output = decoder = None
        failure = ""
        try:
            # Validate/own local output before acquiring or opening a hub camera.
            output = Loopback(path, mode)
            decoder = Decoder(executable, mode, output, self.stop_event, self._frame)
            if self.stop_event.is_set():
                return
            client.claim(mode)
            if self.stop_event.is_set():
                return
            with client.stream(mode) as reader:
                while not self.stop_event.is_set():
                    decoder.feed(reader.read())
        except Exception as exc:
            if not self.stop_event.is_set():
                failure = str(exc) or type(exc).__name__
        finally:
            # No raw output can race the final black frame after the decoder joins.
            for resource in (decoder, output):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as exc:
                        failure = (failure + "; " if failure else "") + "camera cleanup: " + str(exc)
            try:
                client.release()
            except Exception:
                # The hub revokes its own lease when streaming closes/expires.
                # Report loss of the explicit release acknowledgement separately.
                failure = (failure + "; " if failure else "") + "release not acknowledged; hub lease expires automatically"
            with self.lock:
                self.state = "error" if failure else "off"
                self.error = failure

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.worker and self.worker.is_alive():
                self.state = "stopping"

    def close(self):
        self.stop()
        if self.worker:
            self.worker.join(timeout=10)
        if self.worker and self.worker.is_alive():
            raise RuntimeError("camera shutdown not acknowledged; local timeout and hub lease remain the safety backstops")
