"""USB microphone capture for the usb-loom hub (ALSA / arecord)."""

from __future__ import annotations

import array
import re
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

MAGIC = 0x30315541
HEADER_SIZE = 16
SAMPLE_RATE = 44100
CHANNELS = 1
BITS = 16
FRAME_MS = 10
FRAME_BYTES = SAMPLE_RATE * CHANNELS * (BITS // 8) * FRAME_MS // 1000
CAPTURE_GAIN = 6


@dataclass(frozen=True)
class AlsaCapture:
    card: int
    device: int
    name: str

    @property
    def alsa(self) -> str:
        # This DCMT stick is full-speed mono; plughw 48 kHz collapses to near-silence.
        # Native hw @ 44.1 kHz keeps the condenser alive.
        if self.usb:
            return f"hw:{self.card},{self.device}"
        return f"plughw:{self.card},{self.device}"

    @property
    def usb(self) -> bool:
        blob = f"{self.name}".lower()
        return "usb" in blob or "pnp" in blob or "condenser" in blob


def encode_au10(seq: int, pcm: bytes, rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> bytes:
    if len(pcm) > 65535:
        pcm = pcm[:65535]
    header = struct.pack(
        "<I I I B B H",
        MAGIC,
        seq & 0xFFFFFFFF,
        rate,
        channels,
        BITS,
        len(pcm),
    )
    return header + pcm


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


def list_captures() -> list[AlsaCapture]:
    cards: dict[int, str] = {}
    cards_path = Path("/proc/asound/cards")
    if cards_path.exists():
        for match in re.finditer(
            r"^\s*(\d+)\s+\[[^\]]+\]:\s+(.+)$",
            cards_path.read_text(encoding="utf-8", errors="replace"),
            re.M,
        ):
            cards[int(match.group(1))] = match.group(2).strip()

    found: list[AlsaCapture] = []
    try:
        listing = subprocess.check_output(["arecord", "-l"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return found

    for match in re.finditer(
        r"card (\d+): .*?, device (\d+): ([^\n]+)",
        listing,
    ):
        card = int(match.group(1))
        device = int(match.group(2))
        name = cards.get(card) or match.group(3).strip()
        found.append(AlsaCapture(card, device, name))
    return found


def _usb_ids(card: int) -> tuple[str, str]:
    uevent = Path(f"/sys/class/sound/card{card}/device/uevent")
    if not uevent.is_file():
        return "", ""
    try:
        text = uevent.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    for line in text.splitlines():
        if line.startswith("PRODUCT="):
            parts = line.split("=", 1)[1].strip().split("/")
            if len(parts) >= 2:
                return parts[0].upper().zfill(4)[-4:], parts[1].upper().zfill(4)[-4:]
    return "", ""


def pick_mic(captures: list[AlsaCapture] | None = None) -> AlsaCapture | None:
    rows = captures if captures is not None else list_captures()
    usb = [c for c in rows if "usb" in c.name.lower() or c.usb]
    if usb:
        return usb[0]
    return rows[0] if rows else None


def inventory() -> list[dict]:
    chosen = pick_mic()
    rows = []
    for cap in list_captures():
        vid, pid = _usb_ids(cap.card)
        rows.append(
            {
                "path": cap.alsa,
                "name": cap.name,
                "vid": vid,
                "pid": pid,
                "adapters": ["mic"] if chosen and cap.alsa == chosen.alsa else [],
                "kind": "audio",
            }
        )
    return rows


def apply_gain(pcm: bytes, gain: int = CAPTURE_GAIN) -> bytes:
    if gain <= 1 or len(pcm) < 2:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    for i, sample in enumerate(samples):
        value = sample * gain
        if value > 32767:
            value = 32767
        elif value < -32768:
            value = -32768
        samples[i] = value
    return samples.tobytes()


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


def capture_pcm(device: str):
    cmd = [
        "arecord",
        "-D",
        device,
        "-f",
        "S16_LE",
        "-r",
        str(SAMPLE_RATE),
        "-c",
        str(CHANNELS),
        "-t",
        "raw",
        "-q",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def stream_mic(route_fn, adapter_name: str = "mic") -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = 0
    while True:
        route = route_fn(adapter_name)
        if route is None:
            time.sleep(0.2)
            continue
        mic = pick_mic()
        if mic is None:
            print("mic: no ALSA capture yet (plug USB mic, install alsa-utils)", file=sys.stderr)
            time.sleep(1.0)
            continue
        print(f"stream mic {mic.alsa} ({mic.name}) {SAMPLE_RATE}Hz gain={CAPTURE_GAIN} -> {route['dest_host']}:{route['dest_port']}", flush=True)
        proc = capture_pcm(mic.alsa)
        try:
            while route_fn(adapter_name):
                assert proc.stdout is not None
                pcm = proc.stdout.read(FRAME_BYTES)
                if not pcm:
                    print(f"mic: arecord died on {mic.alsa}")
                    break
                pcm = apply_gain(pcm)
                seq = (seq + 1) & 0xFFFFFFFF
                current = route_fn(adapter_name)
                if current is None:
                    break
                sock.sendto(encode_au10(seq, pcm), (current["dest_host"], current["dest_port"]))
                if seq == 1 or seq % 200 == 0:
                    rms, peak = pcm_levels(pcm)
                    print(f"mic frames {seq}  rms={rms:.0f} peak={peak:.0f}", flush=True)
        finally:
            proc.kill()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.terminate()
        time.sleep(0.3)
