"""Linux single-plane V4L2 MMAP capture, forwarding camera-native MJPEG only.

No camera is opened on import. Structs are checked against Linux C headers by
an offline test. Keep OS ioctls out of the shared camera wire module.
"""
from __future__ import annotations

import ctypes as C
import fcntl
import mmap
import os
import select
import stat

U32 = C.c_uint32


class Capability(C.Structure):
    _fields_ = [("driver", C.c_char * 16), ("card", C.c_char * 32),
                ("bus_info", C.c_char * 32), ("version", U32),
                ("capabilities", U32), ("device_caps", U32), ("reserved", U32 * 3)]


class FormatUnion(C.Union):
    # v4l2_window contains pointers; they determine the union's alignment.
    _fields_ = [("raw", U32 * 50), ("alignment", C.c_void_p)]


class Format(C.Structure):
    _fields_ = [("type", U32), ("fmt", FormatUnion)]


class StreamParm(C.Structure):
    _fields_ = [("type", U32), ("raw", U32 * 50)]


class RequestBuffers(C.Structure):
    _fields_ = [("count", U32), ("type", U32), ("memory", U32),
                ("capabilities", U32), ("flags_reserved", U32)]


class Timeval(C.Structure):
    _fields_ = [("seconds", C.c_long), ("microseconds", C.c_long)]


class Timecode(C.Structure):
    _fields_ = [("type", U32), ("flags", U32), ("data", C.c_uint8 * 8)]


class BufferMemory(C.Union):
    _fields_ = [("offset", U32), ("userptr", C.c_ulong), ("planes", C.c_void_p), ("fd", C.c_int32)]


class Buffer(C.Structure):
    _fields_ = [("index", U32), ("type", U32), ("bytesused", U32),
                ("flags", U32), ("field", U32), ("timestamp", Timeval),
                ("timecode", Timecode), ("sequence", U32), ("memory", U32),
                ("m", BufferMemory), ("length", U32), ("reserved2", U32), ("request_fd", C.c_int32)]


def request(number, kind, direction=3):
    return (direction << 30) | (C.sizeof(kind) << 16) | (ord("V") << 8) | number


def ioctl(fd, number, value, direction=3):
    fcntl.ioctl(fd, request(number, type(value), direction), value)
    return value


def strip_mjpeg_alignment_padding(data):
    """Remove only the 1–7 zero bytes used to align some UVC buffers to 8 bytes.

    The strict JPEG validator still runs afterwards, before network forwarding.
    Never search for an embedded EOI or discard arbitrary/trailing nonzero data.
    """
    if len(data) % 8 == 0 and not data.endswith(b"\xff\xd9"):
        for padding in range(1, 8):
            if data.endswith(b"\xff\xd9" + b"\0" * padding):
                return data[:-padding]
    return data


class Capture:
    MAX_BUFFER = 8 * 1024 * 1024

    def __init__(self, source, mode):
        self.fd = None
        self.buffers = []
        self.streaming = False
        try:
            source.verify()
            self.fd = os.open(source.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
            st = os.fstat(self.fd)
            if not stat.S_ISCHR(st.st_mode) or os.major(st.st_rdev) != 81:
                raise OSError("selected camera is not a V4L2 character device")
            source.verify()  # No replacement/reconnect between claim and open.
            cap = ioctl(self.fd, 0, Capability(), 2)
            caps = cap.device_caps if cap.capabilities & 0x80000000 else cap.capabilities
            if cap.driver != b"uvcvideo" or caps & 0x04000001 != 0x04000001:
                raise OSError("camera must support UVC single-plane streaming capture")
            fmt = Format(type=1)
            fmt.fmt.raw[:4] = (mode.width, mode.height, int.from_bytes(b"MJPG", "little"), 1)
            ioctl(self.fd, 5, fmt)
            if tuple(fmt.fmt.raw[:3]) != (mode.width, mode.height, int.from_bytes(b"MJPG", "little")):
                raise OSError("camera refused the requested native MJPEG resolution")
            parm = StreamParm(type=1)
            parm.raw[2], parm.raw[3] = 1, mode.fps
            ioctl(self.fd, 22, parm)
            if not parm.raw[2] or parm.raw[3] != parm.raw[2] * mode.fps:
                raise OSError("camera refused the requested frame rate")
            req = ioctl(self.fd, 8, RequestBuffers(count=3, type=1, memory=1))
            if not 2 <= req.count <= 8:
                raise OSError("invalid camera buffer count")
            for index in range(req.count):
                buf = ioctl(self.fd, 9, Buffer(index=index, type=1, memory=1))
                if not 0 < buf.length <= self.MAX_BUFFER:
                    raise OSError("invalid camera buffer size")
                self.buffers.append(mmap.mmap(self.fd, buf.length, flags=mmap.MAP_SHARED,
                                              prot=mmap.PROT_READ | mmap.PROT_WRITE, offset=buf.m.offset))
                ioctl(self.fd, 15, buf)
            ioctl(self.fd, 18, C.c_int(1), 1)
            self.streaming = True
        except BaseException:
            self.close()
            raise

    def read(self, timeout=0.25):
        if not select.select([self.fd], [], [], timeout)[0]:
            return None
        newest = None
        # Drain at most one buffer-ring, not an unbounded producer queue.
        for _ in range(len(self.buffers)):
            try:
                buf = ioctl(self.fd, 17, Buffer(type=1, memory=1))
            except BlockingIOError:
                break
            if buf.index >= len(self.buffers):
                raise OSError("invalid camera buffer index")
            try:
                if buf.bytesused > len(self.buffers[buf.index]):
                    raise OSError("camera payload exceeds its buffer")
                if buf.bytesused and not buf.flags & 0x40:
                    newest = strip_mjpeg_alignment_padding(self.buffers[buf.index][:buf.bytesused])
            finally:
                ioctl(self.fd, 15, buf)
        return newest

    def close(self):
        if self.fd is not None:
            if self.streaming:
                try:
                    ioctl(self.fd, 19, C.c_int(1), 1)
                except OSError:
                    pass  # Disconnect can make STREAMOFF unavailable; still close fd/maps.
                self.streaming = False
            for buffer in self.buffers:
                buffer.close()
            self.buffers.clear()
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
