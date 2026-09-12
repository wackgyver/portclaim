#!/usr/bin/env python3
"""Windows AU10 sink — play hub mic PCM into a virtual cable Handy can record.

Search order for the render device:
  1. VB-CABLE Input
  2. VoiceMeeter Input
  3. Speakers (Steam Streaming Microphone)  — already on this RIG
  4. --speakers / USB_LOOM_MIC_DEVICE

Handy must record the matching capture pin (CABLE Output, or
Microphone (Steam Streaming Microphone)).

    python client/mic_sink.py --list
    python client/mic_sink.py --port 27183
"""

from __future__ import annotations

import argparse
import array
import ctypes
import math
import os
import socket
import struct
import sys
import time
import wave
from ctypes import wintypes
from pathlib import Path

import wasapi_out

MAGIC = 0x30315541
HEADER_SIZE = 16
PREFERRED = (
    "cable input",
    "voicemeeter input",
    "voicemeeter aux input",
    "steam streaming microphone",
    "steam streaming micro",
)
POOL = 16
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
LINUX_SINK = "portclaim_mic"

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


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
    ]


WHDR_DONE = 0x00000001
WAVE_MAPPER = 0xFFFFFFFF
WOM_DONE = 0x3BD
MMSYSERR_NOERROR = 0
WAVE_FORMAT_PCM = 1
MAXPNAMELEN = 32


class WAVEOUTCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.DWORD),
        ("szPname", wintypes.WCHAR * MAXPNAMELEN),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
        ("dwSupport", wintypes.DWORD),
    ]


winmm = ctypes.windll.winmm if sys.platform == "win32" else None


def decode_au10(packet: bytes) -> tuple[int, int, int, bytes] | None:
    if len(packet) < HEADER_SIZE:
        return None
    magic, seq, rate, channels, bits, nbytes = struct.unpack("<I I I B B H", packet[:HEADER_SIZE])
    if magic != MAGIC or bits != 16:
        return None
    pcm = packet[HEADER_SIZE : HEADER_SIZE + nbytes]
    if len(pcm) != nbytes:
        return None
    return seq, rate, channels, pcm


def list_wave_devices() -> list[tuple[int, str]]:
    if winmm is None:
        return []
    count = winmm.waveOutGetNumDevs()
    rows = []
    for i in range(count):
        caps = WAVEOUTCAPSW()
        if winmm.waveOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            rows.append((i, caps.szPname))
    return rows


def pick_device(explicit: str) -> int:
    devices = list_wave_devices()
    if not devices:
        raise SystemExit("no waveOut devices — not Windows, or no audio stack")
    if explicit:
        needle = explicit.lower()
        for idx, name in devices:
            if needle in name.lower():
                return idx
        raise SystemExit(f"no waveOut matching {explicit!r}. Have: {devices}")
    for prefer in PREFERRED:
        for idx, name in devices:
            if prefer in name.lower():
                print(f"injecting into waveOut {idx}: {name}")
                return idx
    print("no virtual cable found; using wave mapper (speakers). Handy cannot record this.")
    print("install VB-CABLE, or pass --device \"Steam Streaming Microphone\" if that pin exists.")
    return WAVE_MAPPER


def pick_monitor_devices() -> list[int]:
    chosen: list[int] = [WAVE_MAPPER]
    print("monitor WAVE_MAPPER (Windows default playback)", flush=True)
    for idx, name in list_wave_devices():
        low = name.lower()
        if "steam" in low or "cable" in low or "voicemeeter" in low:
            continue
        print(f"monitor waveOut {idx}: {name}", flush=True)
        chosen.append(idx)
        break
    return chosen


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

    def apply(self, pcm: bytes) -> bytes:
        rms, _peak = pcm_levels(pcm)
        if rms >= SILENCE_RMS:
            desired = min(TARGET_RMS / max(rms, 1.0), AGC_MAX)
            self.gain = self.gain * 0.9 + desired * 0.1
        return apply_gain(pcm, self.gain)


class ProbeWriter:
    """Ring of raw AU10 PCM (pre-WASAPI) written to %LOCALAPPDATA%\\portclaim\\au10-probe.wav."""

    def __init__(self, path: Path, seconds: int = PROBE_SECONDS) -> None:
        self.path = path
        self.seconds = seconds
        self.rate = 44100
        self._buf = bytearray()
        self._last_write = 0.0

    def push(self, pcm: bytes, rate: int) -> None:
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


class WaveOut:
    def __init__(self, device: int, rate: int, channels: int):
        if winmm is None:
            raise SystemExit("mic_sink is Windows-only (winmm)")
        self._bufs: list[ctypes.Array] = []
        self._hdrs: list[WAVEHDR] = []
        fmt = WAVEFORMATEX(
            wFormatTag=WAVE_FORMAT_PCM,
            nChannels=channels,
            nSamplesPerSec=rate,
            wBitsPerSample=16,
            nBlockAlign=channels * 2,
            nAvgBytesPerSec=rate * channels * 2,
            cbSize=0,
        )
        self.handle = wintypes.HANDLE()
        err = winmm.waveOutOpen(
            ctypes.byref(self.handle),
            device,
            ctypes.byref(fmt),
            0,
            0,
            0,
        )
        if err != MMSYSERR_NOERROR:
            raise SystemExit(f"waveOutOpen failed: {err}")
        winmm.waveOutSetVolume(self.handle, 0xFFFFFFFF)

    def write(self, pcm: bytes) -> None:
        self._reap()
        if len(self._hdrs) >= POOL:
            return
        buf = ctypes.create_string_buffer(pcm, len(pcm))
        hdr = WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = len(pcm)
        err = winmm.waveOutPrepareHeader(self.handle, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
        if err != MMSYSERR_NOERROR:
            return
        err = winmm.waveOutWrite(self.handle, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
        if err != MMSYSERR_NOERROR:
            winmm.waveOutUnprepareHeader(self.handle, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
            return
        self._bufs.append(buf)
        self._hdrs.append(hdr)

    def _reap(self) -> None:
        keep_b: list[ctypes.Array] = []
        keep_h: list[WAVEHDR] = []
        for buf, hdr in zip(self._bufs, self._hdrs):
            if hdr.dwFlags & WHDR_DONE:
                winmm.waveOutUnprepareHeader(self.handle, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
            else:
                keep_b.append(buf)
                keep_h.append(hdr)
        self._bufs = keep_b
        self._hdrs = keep_h

    def close(self) -> None:
        if winmm is None:
            return
        winmm.waveOutReset(self.handle)
        for hdr in self._hdrs:
            try:
                winmm.waveOutUnprepareHeader(self.handle, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
            except OSError:
                pass
        winmm.waveOutClose(self.handle)


class PulsePaplay:
    """Play s16le mono into a Pulse/PipeWire sink (PortClaim Mic)."""

    def __init__(self, sink: str, rate: int = INJECT_RATE) -> None:
        import subprocess

        self.proc = subprocess.Popen(
            [
                "paplay",
                "--raw",
                f"--rate={rate}",
                "--channels=1",
                "--format=s16le",
                f"--device={sink}",
            ],
            stdin=subprocess.PIPE,
        )
        self.sink = sink
        self.rate = rate

    def write(self, pcm: bytes) -> None:
        if not self.proc.stdin:
            return
        if self.proc.poll() is not None:
            raise OSError(f"paplay exited {self.proc.returncode}")
        self.proc.stdin.write(pcm)
        self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.stdin:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        try:
            self.proc.terminate()
        except OSError:
            pass


def _pulse_sinks() -> list[str]:
    import subprocess

    try:
        out = subprocess.check_output(["pactl", "list", "short", "sinks"], text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            names.append(parts[1])
    return names


def pick_linux_sink(explicit: str) -> str:
    names = _pulse_sinks()
    want = (explicit or os.environ.get("USB_LOOM_MIC_DEVICE") or LINUX_SINK).strip()
    if want in names:
        return want
    needle = want.lower()
    for name in names:
        if needle and needle in name.lower():
            return name
    if LINUX_SINK in names:
        return LINUX_SINK
    raise OSError(
        "no PipeWire sink portclaim_mic — start portclaim-virtmic.service "
        f"(have: {names or 'none'})"
    )


def _serve_linux(port: int, device_name: str, monitor: bool = False) -> None:
    STATS["error"] = ""
    STATS["listening"] = False
    try:
        sink = pick_linux_sink(device_name)
        player = PulsePaplay(sink, INJECT_RATE)
    except OSError as exc:
        STATS["error"] = str(exc)
        STATS["au10_device"] = ""
        print(STATS["error"], flush=True)
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    STATS["au10_device"] = sink
    STATS["listening"] = True
    MONITOR["enabled"] = monitor
    STATS["monitor"] = monitor
    print(f"AU10 sink listening UDP {port}  (PipeWire {sink})", flush=True)
    if monitor:
        print("monitor on — dock condenser into speakers howls", flush=True)
    agc = Agc()
    packets = 0
    probe = ProbeWriter(PROBE_PATH)
    try:
        while True:
            data, _addr = sock.recvfrom(65535)
            decoded = decode_au10(data)
            if decoded is None:
                continue
            _seq, pkt_rate, _pkt_ch, pcm = decoded
            probe.push(pcm, pkt_rate)
            shaped = agc.apply(pcm)
            mono = resample_s16(shaped, pkt_rate, INJECT_RATE)
            player.write(mono)
            packets += 1
            rms, peak = pcm_levels(pcm)
            STATS["au10_last"] = time.time()
            STATS["au10_packets"] = packets
            STATS["au10_rms"] = rms
            STATS["au10_peak"] = peak
            if packets == 1 or packets % 50 == 0:
                print(f"frames {packets}  inject=paplay:{sink}  rms={rms:.0f} peak={peak:.0f}", flush=True)
    except KeyboardInterrupt:
        print("stopped")
    except OSError as exc:
        STATS["error"] = str(exc)
        print(f"AU10 linux inject failed: {exc}", flush=True)
    finally:
        STATS["listening"] = False
        probe.flush()
        player.close()


def serve(port: int, device_name: str, monitor: bool = False) -> None:
    if sys.platform != "win32":
        _serve_linux(port, device_name, monitor)
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    print(f"AU10 sink listening UDP {port}", flush=True)
    player: WaveOut | None = None
    wasapi: wasapi_out.WasapiOut | None = None
    speakers: list[WaveOut] = []
    rate = INJECT_RATE
    agc = Agc()
    device = pick_device(device_name)
    devices = {idx: name for idx, name in list_wave_devices()}
    STATS["au10_device"] = devices.get(device, str(device))
    STATS["listening"] = True
    MONITOR["enabled"] = monitor
    STATS["monitor"] = monitor
    packets = 0
    probe = ProbeWriter(PROBE_PATH)
    try:
        wasapi = wasapi_out.WasapiOut(PREFERRED)
        STATS["au10_device"] = wasapi.name
    except OSError as exc:
        print(f"WASAPI inject failed ({exc}); falling back to waveOut", flush=True)
        wasapi = None
    print(f"AU10 probe WAV {PROBE_PATH}", flush=True)
    try:
        while True:
            data, _addr = sock.recvfrom(65535)
            decoded = decode_au10(data)
            if decoded is None:
                continue
            _seq, pkt_rate, pkt_ch, pcm = decoded
            probe.push(pcm, pkt_rate)
            shaped = agc.apply(pcm)
            if wasapi is not None:
                wasapi.write_s16_mono(shaped, pkt_rate)
            else:
                stereo = upmix_stereo(resample_s16(shaped, pkt_rate, INJECT_RATE), pkt_ch)
                if player is None:
                    player = WaveOut(device, INJECT_RATE, 2)
                player.write(stereo)
            packets += 1
            rms, peak = pcm_levels(pcm)
            STATS["au10_last"] = time.time()
            STATS["au10_packets"] = packets
            STATS["au10_rms"] = rms
            STATS["au10_peak"] = peak
            if packets == 1 or packets % 50 == 0:
                print(
                    f"frames {packets}  inject={'wasapi' if wasapi else 'waveOut'}  "
                    f"rms={rms:.0f} peak={peak:.0f}",
                    flush=True,
                )
    except KeyboardInterrupt:
        print("stopped")
    finally:
        STATS["listening"] = False
        probe.flush()
        if player is not None:
            player.close()
        for old in speakers:
            old.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="usb-loom microphone sink")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--port", type=int, default=int(os.environ.get("USB_LOOM_AUDIO_PORT", 27183)))
    parser.add_argument("--device", default=os.environ.get("USB_LOOM_MIC_DEVICE", ""))
    parser.add_argument("--speakers", action="store_true", help="force default speakers (debug only)")
    parser.add_argument(
        "--monitor",
        action="store_true",
        default=os.environ.get("USB_LOOM_MIC_MONITOR", "") in {"1", "true", "yes"},
        help="also play the stream on local speakers so you can hear it",
    )
    args = parser.parse_args(argv)
    if args.list:
        if sys.platform != "win32":
            print("PipeWire/Pulse sinks:")
            for name in _pulse_sinks():
                print(f"  {name}")
            return 0
        print("waveOut (render):")
        for idx, name in list_wave_devices():
            print(f"{idx:3}  {name}")
        return 0
    device = "mapper-debug" if args.speakers else args.device
    if args.speakers:
        os.environ["USB_LOOM_MIC_DEVICE"] = ""
        # WAVE_MAPPER via empty prefer miss + speakers flag
        devices = list_wave_devices()
        print("debug: speakers / wave mapper")
        serve(args.port, devices[0][1] if devices else "", monitor=args.monitor)
        return 0
    serve(args.port, device, monitor=args.monitor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
