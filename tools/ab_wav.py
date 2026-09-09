#!/usr/bin/env python3
"""Compare AU10 probe vs Handy recording (pitch / RMS / duration)."""

from __future__ import annotations

import struct
import sys
import wave
from pathlib import Path


def load(path: Path) -> tuple[int, list[float]]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        sw = handle.getsampwidth()
        nch = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    if sw == 2:
        n = len(raw) // 2
        ints = struct.unpack("<" + "h" * n, raw[: n * 2])
        samples = [s / 32768.0 for s in ints]
    elif sw == 4:
        n = len(raw) // 4
        samples = list(struct.unpack("<" + "f" * n, raw[: n * 4]))
    else:
        raise SystemExit(f"{path}: unsupported width {sw}")
    if nch > 1:
        samples = [sum(samples[i : i + nch]) / nch for i in range(0, len(samples) - nch + 1, nch)]
    return rate, samples


def pitch_corr(samples: list[float], rate: int) -> tuple[float, float]:
    if len(samples) < rate // 10:
        return 0.0, 0.0
    win = rate // 50
    best_i = max(range(0, max(1, len(samples) - win * 4), win), key=lambda i: sum(x * x for x in samples[i : i + win * 4]))
    sl = samples[best_i : best_i + win * 4]
    n = len(sl)
    best_lag, best_c = 0, 0.0
    for lag in range(rate // 400, max(rate // 400 + 1, rate // 60)):
        if lag >= n:
            break
        c = sum(sl[i] * sl[i + lag] for i in range(n - lag)) / (n - lag)
        if c > best_c:
            best_c, best_lag = c, lag
    hz = rate / best_lag if best_lag else 0.0
    return hz, best_c


def report(label: str, path: Path) -> None:
    if not path.is_file():
        print(f"{label}: missing {path}")
        return
    rate, samples = load(path)
    if not samples:
        print(f"{label}: empty")
        return
    peak = max(abs(s) for s in samples)
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
    hz, corr = pitch_corr(samples, rate)
    speechy = corr >= 0.3
    print(
        f"{label}: {path.name}  {rate}Hz  {len(samples) / rate:.2f}s  "
        f"rms={rms:.4f} peak={peak:.4f} pitch={hz:.0f}Hz corr={corr:.3f} "
        f"{'SPEECH-LIKE' if speechy else 'MUSH'}"
    )


def main() -> int:
    local = Path(os_localapp())
    probe = Path(sys.argv[1]) if len(sys.argv) > 1 else local / "portclaim" / "au10-probe.wav"
    if not probe.is_file():
        legacy = local / "usb-loom" / "au10-probe.wav"
        if legacy.is_file():
            probe = legacy
    handy_dir = Path.home() / "AppData" / "Roaming" / "com.pais.handy" / "recordings"
    latest = max(handy_dir.glob("handy-*.wav"), key=lambda p: p.stat().st_mtime, default=None)
    report("probe (pre-WASAPI AU10)", probe)
    if latest:
        report("handy (post-VAD Whisper input)", latest)
    else:
        print("handy: no recordings")
    return 0


def os_localapp() -> str:
    import os

    return os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))


if __name__ == "__main__":
    raise SystemExit(main())
