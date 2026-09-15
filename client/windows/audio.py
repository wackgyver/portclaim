"""Windows AU10 -> WASAPI, with the existing waveOut fallback."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import socket
import sys
import time
if sys.platform == "win32":
    from client.windows import wasapi_out
from client.common.audio import (
    MAGIC, INJECT_RATE, STATS, MONITOR, PROBE_PATH, ProbeWriter, Agc,
    decode_au10, pcm_levels, resample_s16, upmix_stereo,
)

PREFERRED = (
    "cable input",
    "voicemeeter input",
    "voicemeeter aux input",
    "steam streaming microphone",
    "steam streaming micro",
)
POOL = 16
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


def serve(port: int, device_name: str, monitor: bool = False) -> None:
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
