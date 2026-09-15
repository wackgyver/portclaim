"""Linux AU10 -> native-rate PCM -> PipeWire. No Windows audio dependencies."""
from __future__ import annotations
import os
import socket
import time
from client.common.stream_guard import StreamGuard
from client.common.audio import (
    MAGIC, INJECT_RATE, STATS, MONITOR, PROBE_PATH, ProbeWriter, Agc,
    decode_au10, pcm_levels, resample_s16, upmix_stereo,
)

LINUX_SINK = "portclaim_mic"

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
        import subprocess
        try:
            self.proc.terminate()
            self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
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
        "no PipeWire sink portclaim_mic — start portclaim.service (owned mic helper) "
        f"(have: {names or 'none'})"
    )


def serve(port: int, device_name: str, monitor: bool = False) -> None:
    STATS.update(error="", listening=False)
    player = None
    probe = ProbeWriter(PROBE_PATH)
    try:
        sink = pick_linux_sink(device_name)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("0.0.0.0", port))
            sock.settimeout(0.1)
            STATS.update(au10_device=sink, listening=True, monitor=False)
            MONITOR["enabled"] = False
            print(f"AU10 listening UDP {port} (native-rate PCM → PipeWire {sink})", flush=True)
            if monitor:
                print("Linux speaker monitoring is not implemented; leaving it off", flush=True)
            agc = Agc()
            guard = StreamGuard(timeout=0.5)
            packets = 0
            while True:
                try:
                    data, _addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                decoded = decode_au10(data)
                if decoded is None:
                    continue
                seq, rate, channels, pcm = decoded
                if channels != 1:
                    STATS["error"] = "AU10 Linux expects mono PCM"
                    continue
                if not guard.accept(seq, time.monotonic()):
                    continue
                if player is None or player.rate != rate:
                    if player is not None:
                        player.close()
                    # PipeWire's stateful resampler owns conversion, not a fresh
                    # Python interpolation loop on every ten-millisecond packet.
                    player = PulsePaplay(sink, rate)
                probe.push(pcm, rate)
                levels = pcm_levels(pcm)
                player.write(agc.apply(pcm, levels))
                packets += 1
                STATS.update(error="", au10_last=time.time(), au10_packets=packets,
                             au10_rms=levels[0], au10_peak=levels[1], au10_rate=rate)
                if packets == 1 or packets % 500 == 0:
                    print(f"AU10 frames {packets}; native rate {rate}Hz; rms={levels[0]:.0f}", flush=True)
    except KeyboardInterrupt:
        print("AU10 sink stopped", flush=True)
    except (OSError, ValueError) as exc:
        STATS["error"] = str(exc)
        print(f"AU10 linux inject failed: {exc}", flush=True)
    finally:
        STATS["listening"] = False
        probe.flush()
        if player is not None:
            player.close()
