#!/usr/bin/env python3
"""PortClaim Receiver entry point. Platform adapters and shared UI are separate."""
from __future__ import annotations

if not __package__:
    import bootstrap
    bootstrap.setup()

import argparse
import json
from pathlib import Path
import sys
from client import platforms
from client.common.environment import load_receiver_env


def main(hub=None, dest_host=None, client_id=None, start_hidden=False):
    load_receiver_env()
    from client.ui import main as run
    return run(hub=hub, dest_host=dest_host, client_id=client_id, start_hidden=start_hidden)


def __getattr__(name):
    if name == "ReceiverApp":
        from client.ui import ReceiverApp
        return ReceiverApp
    raise AttributeError(name)


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-hidden", action="store_true", help="Start in the Linux system tray")
    parser.add_argument("--check-platform", metavar="JSON_PATH", help="Write driver-free import/build diagnostics and exit without UI, sockets, or claims")
    args = parser.parse_args(argv)
    if args.check_platform:
        # Import the complete view without constructing Tk or starting workers.
        from client import ui
        report = platforms.diagnostics()
        report["ui_imported"] = ui.ReceiverApp is not None
        report["frozen"] = bool(getattr(sys, "frozen", False))
        Path(args.check_platform).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 0
    return main(start_hidden=args.start_hidden)


if __name__ == "__main__":
    raise SystemExit(cli())
