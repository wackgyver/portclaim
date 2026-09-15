#!/usr/bin/env python3
"""Compatible AU10 CLI; Windows WASAPI and Linux PipeWire have separate modules."""
from __future__ import annotations

if not __package__:
    import bootstrap
    bootstrap.setup()

import argparse
import os
from client import platforms
from client.common.audio import STATS, MONITOR, ProbeWriter, Agc, decode_au10, pcm_levels


def serve(port, device_name, monitor=False):
    try:
        platforms.audio_backend().serve(port, device_name, monitor)
    except (Exception, SystemExit) as exc:
        STATS.update(listening=False, error=str(exc) or type(exc).__name__)
        print(f"Audio sink failed: {STATS['error']}", flush=True)


def main(argv=None):
    from client.common.environment import load_receiver_env
    load_receiver_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--port", type=int, default=int(os.environ.get("USB_LOOM_AUDIO_PORT", 27183)))
    parser.add_argument("--device", default=os.environ.get("USB_LOOM_MIC_DEVICE", ""))
    parser.add_argument("--speakers", action="store_true", help="Windows speaker debug target")
    parser.add_argument("--monitor", action="store_true", default=os.environ.get("USB_LOOM_MIC_MONITOR", "") in {"1", "true", "yes"})
    args = parser.parse_args(argv)
    backend = platforms.audio_backend()
    if args.list:
        rows = backend.list_wave_devices() if platforms.name() == "windows" else list(enumerate(backend._pulse_sinks()))
        for index, device in rows:
            print(f"{index:3}  {device}")
        return 0
    device = args.device
    if args.speakers:
        if platforms.name() != "windows":
            parser.error("--speakers is Windows-only; Linux monitoring is not implemented")
        os.environ["USB_LOOM_MIC_DEVICE"] = ""
        devices = backend.list_wave_devices()
        device = devices[0][1] if devices else ""
    serve(args.port, device, args.monitor)
    return 1 if STATS.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
