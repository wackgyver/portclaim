"""USB microphone capture for the usb-loom hub (ALSA / arecord)."""

from __future__ import annotations

import array
import os
import re
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

MAGIC = 0x30315541
HEADER_SIZE = 16
SAMPLE_RATE = 44100
CHANNELS = 1
BITS = 16
FRAME_MS = 10
FRAME_BYTES = SAMPLE_RATE * CHANNELS * (BITS // 8) * FRAME_MS // 1000
CAPTURE_GAIN = 6
SOUND_ROOT = Path("/sys/class/sound")
ASOUND_ROOT = Path("/proc/asound")


@dataclass(frozen=True)
class CaptureIdentity:
    usb_id: tuple[str, str]
    alsa_id: str
    generation: tuple[str, str]


@dataclass(frozen=True)
class AlsaCapture:
    card: int
    device: int
    name: str
    identity: CaptureIdentity | None = field(default=None, repr=False)

    @property
    def alsa(self) -> str:
        if self.identity is not None:
            return f"hw:CARD={self.identity.alsa_id},DEV={self.device}"
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
    cards_path = ASOUND_ROOT / "cards"
    try:
        card_text = cards_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        card_text = ""
    for match in re.finditer(r"^\s*(\d+)\s+\[[^\]]+\]:\s+(.+)$", card_text, re.M):
        cards[int(match.group(1))] = match.group(2).strip()

    found: list[AlsaCapture] = []
    try:
        listing = subprocess.check_output(["arecord", "-l"], text=True, stderr=subprocess.DEVNULL,
                                          timeout=5, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
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


def _usb_parent(card: int) -> Path | None:
    try:
        device = (SOUND_ROOT / f"card{card}/device").resolve(strict=True)
        return next((p for p in (device, *device.parents) if (p / "idVendor").is_file()), None)
    except (OSError, RuntimeError):
        return None


def _usb_ids(card: int) -> tuple[str, str]:
    parent = _usb_parent(card)
    if parent is not None:
        try:
            vendor = (parent / "idVendor").read_text().strip().upper()
            product = (parent / "idProduct").read_text().strip().upper()
            if re.fullmatch(r"[0-9A-F]{4}", vendor) and re.fullmatch(r"[0-9A-F]{4}", product):
                return vendor, product
        except OSError:
            pass
    return "", ""


def capture_identity(card: int) -> CaptureIdentity | None:
    parent = _usb_parent(card)
    if parent is None:
        return None
    try:
        alsa_id = (ASOUND_ROOT / f"card{card}/id").read_text(errors="replace").strip()
        devnum = (parent / "devnum").read_text().strip()
        ids = _usb_ids(card)
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alsa_id)
                or not devnum.isascii() or not devnum.isdecimal() or not all(ids)):
            return None
        return CaptureIdentity(ids, alsa_id, (str(parent), devnum))
    except (OSError, RuntimeError):
        return None


def select_mic(captures: list[AlsaCapture] | None = None) -> tuple[AlsaCapture | None, str]:
    """Resolve one pinned USB capture. Blank/invalid/missing/ambiguous pins never fall back.

    Preserve legacy selection only when the pin is genuinely unset AND webcam
    support is off. Enabling a composite USB camera requires an explicit mic pin.
    """
    pin = os.environ.get("USB_LOOM_MIC_USB_ID")
    if pin is None and os.environ.get("USB_LOOM_CAMERA_ENABLED") == "1":
        return None, "USB_LOOM_MIC_USB_ID is required when webcam support is enabled"
    if pin is not None and not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}", pin.strip()):
        return None, "invalid USB_LOOM_MIC_USB_ID; microphone capture disabled"
    rows = captures if captures is not None else list_captures()
    if pin is None:
        usb = [c for c in rows if c.usb]
        chosen = usb[0] if usb else (rows[0] if rows else None)
        return chosen, ("legacy automatic selection (not pinned)" if chosen
                        else "no ALSA capture available (legacy selection)")
    wanted = tuple(pin.strip().upper().split(":"))
    matches = [cap for cap in rows if _usb_ids(cap.card) == wanted]
    if len(matches) != 1:
        reason = "unavailable" if not matches else "ambiguous"
        return None, f"pinned USB microphone {reason}; no fallback capture"
    chosen = matches[0]
    identity = capture_identity(chosen.card)
    if identity is None or identity.usb_id != wanted:
        return None, "pinned USB microphone identity unavailable or changed; no fallback capture"
    # Resolve by ALSA ID, not a numeric slot that hotplug can hand to a webcam.
    return replace(chosen, identity=identity), "pinned USB microphone"


def pick_mic(captures: list[AlsaCapture] | None = None) -> AlsaCapture | None:
    return select_mic(captures)[0]


def inventory() -> list[dict]:
    captures = list_captures()
    chosen, selection = select_mic(captures)
    rows = []
    for cap in captures:
        vid, pid = _usb_ids(cap.card)
        rows.append(
            {
                "path": chosen.alsa if chosen and (cap.card, cap.device) == (chosen.card, chosen.device) else cap.alsa,
                "name": cap.name,
                "vid": vid,
                "pid": pid,
                "adapters": ["mic"] if chosen and (cap.card, cap.device) == (chosen.card, chosen.device) else [],
                "kind": "audio",
                "mic_selection": selection,
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
    waiting_reason = None
    while True:
        route = route_fn(adapter_name)
        if route is None:
            time.sleep(0.2)
            continue
        mic, selection = select_mic()
        if mic is None:
            if selection != waiting_reason:
                print(f"mic: {selection}", file=sys.stderr)
                waiting_reason = selection
            time.sleep(1.0)
            continue
        if mic.identity is not None and capture_identity(mic.card) != mic.identity:
            if waiting_reason != "identity changed":
                print("mic: pinned identity changed before open; waiting without capture", file=sys.stderr)
                waiting_reason = "identity changed"
            time.sleep(0.3)
            continue
        try:
            proc = capture_pcm(mic.alsa)
        except OSError:
            if waiting_reason != "arecord unavailable":
                print("mic: arecord unavailable; capture not started", file=sys.stderr)
                waiting_reason = "arecord unavailable"
            time.sleep(1.0)
            continue
        waiting_reason = None
        print(f"stream mic {mic.alsa} ({mic.name}) {SAMPLE_RATE}Hz gain={CAPTURE_GAIN} -> {route['dest_host']}:{route['dest_port']}", flush=True)
        verify_first_frame = mic.identity is not None
        try:
            while route_fn(adapter_name):
                assert proc.stdout is not None
                pcm = proc.stdout.read(FRAME_BYTES)
                if not pcm:
                    print(f"mic: arecord died on {mic.alsa}")
                    break
                if verify_first_frame:
                    # Verify after arecord opened, but before forwarding any PCM.
                    # A disconnected hw PCM never migrates to another card; a
                    # new capture attempt always goes through the pin again.
                    if capture_identity(mic.card) != mic.identity:
                        print("mic: source changed during open; PCM discarded", file=sys.stderr)
                        break
                    verify_first_frame = False
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
            finally:
                if proc.stdout is not None:
                    proc.stdout.close()
        time.sleep(0.3)
