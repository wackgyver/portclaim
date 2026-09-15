#!/usr/bin/env python3
"""Compatible TP10 entry point; native Linux and legacy OS adapters are separate."""
from __future__ import annotations

if not __package__:
    import bootstrap
    bootstrap.setup()

import argparse
import os
from client import platforms
from client.common.state import TRACKPAD as STATS
from client.common.tp10_legacy import decode_tp10

native_backend = platforms.native_backend


def serve(port):
    try:
        backend = platforms.input_backend()
        if native_backend():
            backend.serve(port, STATS)
        else:
            from client.common.legacy_receiver import serve as legacy_serve
            legacy_serve(port, backend, STATS)
    except (Exception, SystemExit) as exc:
        STATS.update(listening=False, error=str(exc) or type(exc).__name__)
        print(f"Trackpad sink failed: {STATS['error']}", flush=True)


def __getattr__(attribute):
    # Existing pure-engine imports remain available without loading any OS APIs.
    if attribute.startswith("__"):
        raise AttributeError(attribute)
    from client.common import legacy_gestures
    return getattr(legacy_gestures, attribute)


def main(argv=None):
    from client.common.environment import load_receiver_env
    load_receiver_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("USB_LOOM_TRACKPAD_PORT", 27184)))
    args = parser.parse_args(argv)
    serve(args.port)
    return 1 if STATS.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
