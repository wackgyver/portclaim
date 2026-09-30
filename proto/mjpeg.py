"""PortClaim MJPEG/1: bounded multipart frames over the authenticated control HTTP.

No drivers, configuration, image decoder or network I/O on import. This is a
camera stream, not desktop video and not another use of the HID UDP protocols.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time

BOUNDARY = b"portclaim-mjpeg-v1"
CONTENT_TYPE = "multipart/x-mixed-replace; boundary=" + BOUNDARY.decode()
MAX_FRAME = 4 * 1024 * 1024
FRAME_TIMEOUT = 1.5
LEASE_HEADER = "X-PortClaim-Camera-Lease"
STREAM_PATH = "/v1/devices/webcam/stream"


@dataclass(frozen=True)
class Mode:
    width: int = 1280
    height: int = 720
    fps: int = 30

    def __post_init__(self):
        if any(type(v) is not int for v in (self.width, self.height, self.fps)):
            raise ValueError("camera mode values must be integers")
        if (self.width, self.height) not in {(640, 480), (1280, 720), (1920, 1080)}:
            raise ValueError("camera resolution must be 640x480, 1280x720 or 1920x1080")
        if self.fps not in {5, 10, 15, 20, 25, 30}:
            raise ValueError("camera fps must be 5, 10, 15, 20, 25 or 30")

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) - {"width", "height", "fps"}:
            raise ValueError("invalid camera mode")
        return cls(**value)

    def as_dict(self):
        return {"width": self.width, "height": self.height, "fps": self.fps}


def validate_jpeg(data, mode):
    """Check framing and dimensions before feeding an image decoder.

    Not an image-decoder sandbox: use a maintained FFmpeg on trusted LANs.
    Skip complete JPEG marker segments (including embedded APP thumbnails), not
    a naive search for SOI/EOI inside compressed data.
    """
    if not 4 <= len(data) <= MAX_FRAME or data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        raise ValueError("invalid or oversized JPEG frame")
    offset = 2
    found = False
    for _ in range(128):
        if offset >= min(len(data) - 2, 65536) or data[offset] != 0xff:
            break
        while offset < len(data) - 2 and data[offset] == 0xff:
            offset += 1
        marker = data[offset]
        offset += 1
        if marker in {0, 0xd8, 0xd9} or 0xd0 <= marker <= 0xd7:
            break
        if offset + 2 > len(data) - 2:
            break
        size = int.from_bytes(data[offset:offset + 2], "big")
        if size < 2 or offset + size > len(data) - 2:
            break
        if marker == 0xc0:
            if found or size < 8:
                break
            bits = data[offset + 2]
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            components = data[offset + 7]
            if (bits != 8 or (width, height) != (mode.width, mode.height)
                    or components not in {1, 3} or size != 8 + 3 * components):
                raise ValueError("JPEG dimensions/encoding do not match the camera mode")
            found = True
        elif 0xc0 <= marker <= 0xcf and marker not in {0xc4, 0xcc}:
            raise ValueError("unsupported JPEG frame encoding")
        if marker == 0xda:
            if found:
                # UVC baseline, one interleaved scan only. Reject concatenated
                # images/extra SOFs before a decoder can allocate for a second
                # unchecked resolution. APP thumbnails were skipped above.
                entropy = data[offset + size:-2]
                if re.search(rb"\xff[^\x00\xd0-\xd7\xff]", entropy):
                    raise ValueError("unexpected marker or second image in JPEG scan")
                return
            break
        offset += size
    raise ValueError("missing or malformed JPEG image header")


def frame_header(size, sequence):
    if not 4 <= size <= MAX_FRAME or not 0 < sequence < 2**63:
        raise ValueError("invalid MJPEG frame header")
    return (b"--" + BOUNDARY + b"\r\nContent-Type: image/jpeg\r\n"
            + f"Content-Length: {size}\r\nX-PortClaim-Sequence: {sequence}\r\n\r\n".encode())


class FrameReader:
    """Strict, bounded parser; rejects truncation, replay and total-frame timeout.

    The underlying read1 must itself have a finite socket timeout. read1 (not
    buffered read(n)) also lets the total deadline catch a slow trickle of bytes.
    """
    def __init__(self, stream, mode, clock=time.monotonic):
        self.stream, self.mode, self.clock = stream, mode, clock
        self.buffer = bytearray()
        self.sequence = 0
        self.deadline = 0.0

    def _fill(self):
        if self.clock() >= self.deadline:
            raise TimeoutError("camera frame deadline exceeded")
        data = self.stream.read1(65536)
        if self.clock() >= self.deadline:
            raise TimeoutError("camera frame deadline exceeded")
        if not data:
            raise EOFError("camera stream ended")
        if len(data) > 65536:
            raise ValueError("camera reader exceeded its bounded read")
        self.buffer.extend(data)

    def _line(self):
        while b"\n" not in self.buffer:
            if len(self.buffer) > 256:
                raise ValueError("camera header too long")
            self._fill()
        end = self.buffer.index(b"\n") + 1
        if end > 256 or self.buffer[end - 2:end] != b"\r\n":
            raise ValueError("invalid camera header line")
        line = bytes(self.buffer[:end - 2])
        del self.buffer[:end]
        return line

    def _exact(self, size):
        chunks = []
        while size:
            if not self.buffer:
                self._fill()
            count = min(size, len(self.buffer))
            chunks.append(bytes(self.buffer[:count]))
            del self.buffer[:count]
            size -= count
        return b"".join(chunks)

    def read(self):
        self.deadline = self.clock() + FRAME_TIMEOUT
        if self._line() != b"--" + BOUNDARY:
            raise ValueError("invalid camera multipart boundary")
        headers = {}
        for _ in range(8):
            line = self._line()
            if not line:
                break
            key, separator, value = line.partition(b":")
            key = key.lower()
            if not separator or key in headers:
                raise ValueError("invalid/duplicate camera header")
            headers[key] = value.strip()
        else:
            raise ValueError("too many camera headers")
        length = headers.get(b"content-length", b"")
        sequence = headers.get(b"x-portclaim-sequence", b"")
        if (headers.get(b"content-type") != b"image/jpeg" or not length.isdigit()
                or not sequence.isdigit() or len(length) > 8 or len(sequence) > 19):
            raise ValueError("invalid camera frame metadata")
        length, sequence = int(length), int(sequence)
        if not 4 <= length <= MAX_FRAME or not self.sequence < sequence < 2**63:
            raise ValueError("oversized or non-monotonic camera frame")
        frame = self._exact(length)
        if self._exact(2) != b"\r\n":
            raise ValueError("invalid camera frame terminator")
        validate_jpeg(frame, self.mode)
        if self.clock() >= self.deadline:
            raise TimeoutError("camera frame deadline exceeded")
        self.sequence = sequence
        return frame
