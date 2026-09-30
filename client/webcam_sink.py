#!/usr/bin/env python3
"""Explicit video-only camera activation; never part of bulk Connect/autostart."""
from __future__ import annotations

if __package__ in {None, ""}:
    from bootstrap import setup
    setup()

import argparse
import os
import signal
import socket
import time

from client import platforms
from client.common.environment import load_receiver_env
from proto.mjpeg import Mode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="explicitly activate the hub webcam until stopped")
    parser.add_argument("--size", default="1280x720", choices=("640x480", "1280x720", "1920x1080"))
    parser.add_argument("--fps", type=int, choices=(5, 10, 15, 20, 25, 30), default=30)
    args = parser.parse_args(argv)
    if not args.start:
        parser.error("camera stays off without --start")
    backend = platforms.camera_backend()
    if backend is None:
        parser.error("webcam receiving is currently Linux-only; nothing claimed")
    load_receiver_env()
    width, height = map(int, args.size.split("x"))
    controller = backend.Controller()
    previous = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: controller.stop())
        controller.start(os.environ.get("USB_LOOM_HUB", ""), os.environ.get("USB_LOOM_TOKEN", ""),
                         os.environ.get("USB_LOOM_CLIENT_ID") or socket.gethostname(), Mode(width, height, args.fps))
        last = None
        while True:
            state = controller.snapshot()
            if state["state"] != last:
                print("Camera " + state["state"] + (": " + state["error"] if state["error"] else ""), flush=True)
                last = state["state"]
            if not state["busy"]:
                return 1 if state["error"] else 0
            time.sleep(0.1)
    finally:
        try:
            controller.close()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
