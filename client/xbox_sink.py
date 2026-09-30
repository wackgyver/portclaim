#!/usr/bin/env python3
"""Compatible SB10 Xbox entry point; platform factories supply the actual device."""
from __future__ import annotations

if not __package__:
    import bootstrap
    bootstrap.setup()

import argparse
import os
from client import platforms
from client.common import gamepad
from client.common.gamepad import STATS, GamepadWriter, decode_sb10, u16_to_thumb, pov_to_hat


def serve(port):
    backend = platforms.gamepad_backend()
    gamepad.serve(port, backend.create_pad, invert_y=backend.INVERT_Y)


def main(argv=None):
    from client.common.environment import load_receiver_env
    load_receiver_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("USB_LOOM_XBOX_PORT", 27185)))
    serve(parser.parse_args(argv).port)
    return 1 if STATS.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
