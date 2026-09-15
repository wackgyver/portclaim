"""Pure AU10 decoding, PCM helpers, optional probes, and shared audio status."""
from __future__ import annotations
import array
import math
import os
from pathlib import Path
import struct
import sys
import time
import wave

MAGIC = 0x30315541
HEADER_SIZE = 16
INJECT_RATE = 48000
TARGET_RMS = 2500.0
SILENCE_RMS = 80.0
AGC_MAX = 8.0
PROBE_SECONDS = 8
def _probe_path() -> Path:
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA", ".")
        return Path(root) / "portclaim" / "au10-probe.wav"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "portclaim" / "au10-probe.wav"


PROBE_PATH = _probe_path()

STATS = {
    "au10_last": 0.0,
    "au10_packets": 0,
    "au10_device": "",
    "au10_rms": 0.0,
    "au10_peak": 0.0,
    "listening": False,
    "monitor": False,
    "error": "",
}
MONITOR = {"enabled": False}


def decode_au10(packet: bytes) -> tuple[int, int, int, bytes] | None:
    if len(packet) < HEADER_SIZE:
        return None
    magic, seq, rate, channels, bits, nbytes = struct.unpack("<I I I B B H", packet[:HEADER_SIZE])
    if (magic != MAGIC or bits != 16 or not 8000 <= rate <= 192000
            or not 1 <= channels <= 8 or not nbytes or nbytes % (channels * 2)):
        return None
    pcm = packet[HEADER_SIZE : HEADER_SIZE + nbytes]
    if len(pcm) != nbytes:
        return None
    return seq, rate, channels, pcm


def apply_gain(pcm: bytes, gain: float) -> bytes:
    if gain <= 1.01 or len(pcm) < 2:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    for i, sample in enumerate(samples):
        value = int(sample * gain)
        if value > 32767:
            value = 32767
        elif value < -32768:
            value = -32768
        samples[i] = value
    return samples.tobytes()


def resample_s16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate or len(pcm) < 2:
        return pcm
    src = array.array("h")
    src.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not src:
        return pcm
    n_out = max(1, int(round(len(src) * dst_rate / src_rate)))
    last = len(src) - 1
    scale = (len(src) - 1) / max(1, n_out - 1)
    out = array.array("h")
    for i in range(n_out):
        pos = i * scale
        i0 = int(pos)
        i1 = i0 + 1 if i0 < last else last
        frac = pos - i0
        out.append(int(src[i0] * (1.0 - frac) + src[i1] * frac))
    return out.tobytes()


class Agc:
    def __init__(self) -> None:
        self.gain = 3.0

    def apply(self, pcm: bytes, levels: tuple[float, float] | None = None) -> bytes:
        rms, _peak = pcm_levels(pcm) if levels is None else levels
        if rms >= SILENCE_RMS:
            desired = min(TARGET_RMS / max(rms, 1.0), AGC_MAX)
            self.gain = self.gain * 0.9 + desired * 0.1
        return apply_gain(pcm, self.gain)


class ProbeWriter:
    """Ring of raw AU10 PCM (pre-WASAPI) written to %LOCALAPPDATA%\\portclaim\\au10-probe.wav."""

    def __init__(self, path: Path, seconds: int = PROBE_SECONDS) -> None:
        self.path = path
        self.enabled = os.environ.get("USB_LOOM_AUDIO_PROBE", "1").lower() not in {"0", "false", "no"}
        self.seconds = seconds
        self.rate = 44100
        self._buf = bytearray()
        self._last_write = 0.0

    def push(self, pcm: bytes, rate: int) -> None:
        if not self.enabled:
            return
        if rate:
            self.rate = rate
        self._buf.extend(pcm)
        cap = self.rate * 2 * self.seconds
        if len(self._buf) > cap:
            del self._buf[: len(self._buf) - cap]
        now = time.time()
        if now - self._last_write >= 2.0:
            self.flush()

    def flush(self) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(self.path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.rate)
            handle.writeframes(bytes(self._buf))
        self._last_write = time.time()


def monitor_beep(rate: int, ms: int = 250, hz: float = 880.0) -> bytes:
    n = max(1, rate * ms // 1000)
    samples = array.array("h")
    for i in range(n):
        value = int(12000 * math.sin(2 * math.pi * hz * i / rate))
        samples.append(value)
        samples.append(value)
    return samples.tobytes()


def upmix_stereo(pcm: bytes, channels: int) -> bytes:
    if channels >= 2:
        return pcm
    mono = array.array("h")
    mono.frombytes(pcm)
    stereo = array.array("h")
    stereo.extend(s for sample in mono for s in (sample, sample))
    return stereo.tobytes()


def pcm_levels(pcm: bytes) -> tuple[float, float]:
    if len(pcm) < 2:
        return 0.0, 0.0
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0.0, 0.0
    peak = max(abs(s) for s in samples)
    acc = sum(s * s for s in samples)
    return (acc / len(samples)) ** 0.5, float(peak)
