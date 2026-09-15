#!/usr/bin/env python3
"""Claim or release a hub device from any machine with Python 3.

Hub, dest, and token come from flags or env (USB_LOOM_HUB, USB_LOOM_SELF,
USB_LOOM_TOKEN). There is no baked LAN default.

    python client/claim.py --hub http://HUB:27180 devices
    python client/claim.py --hub http://HUB:27180 claim ta320 --dest DEST:27182
    python client/claim.py --hub http://HUB:27180 claim magictrackpad --dest DEST:27184
    python client/claim.py --hub http://HUB:27180 connect
    python client/claim.py --hub http://HUB:27180 release ta320
"""

from __future__ import annotations

if not __package__:
    import bootstrap
    bootstrap.setup()

import argparse
import json
import os
import socket
import subprocess
import sys
from client import platforms
from client.common import claims
from client.common.environment import load_receiver_env as _load_receiver_env
from client.common.claims import DEFAULT_PORTS, GENESIS, CONNECT, request, guess_dest, _port_open, _self_host, _token


def _register_and_claim(hub, client_id, adapter, dest):
    return claims._register_and_claim(hub, client_id, adapter, dest,
                                      native_touchpad=platforms.native_backend())


def _start_sidestick():
    if platforms.name() != "windows":
        print("SidestickBridge is Windows-only; Linux can claim ta320 but has no bundled mapper.")
        return
    from client.windows.sidecar import _start_sidestick as start
    start()


def _start_xbox_sink() -> None:
    if _port_open(27185):
        print("xboxelite sink already listening on :27185")
        return
    if getattr(sys, "frozen", False):
        import threading

        from client import xbox_sink

        threading.Thread(target=xbox_sink.serve, args=(27185,), name="xb10-sink", daemon=True).start()
        print("starting xboxelite ViGEm sink on :27185 (in-process)")
        return
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "xbox_sink.py")
    creation = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([sys.executable, script, "--port", "27185"], cwd=here, creationflags=creation)
    print("starting xboxelite ViGEm sink on :27185")


def _connect(hub: str, client_id: str, dest_host: str) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import threading

    from client import mic_sink, receiver_app, trackpad_sink

    _start_sidestick()
    _start_xbox_sink()
    for adapter in CONNECT:
        dest = f"{dest_host}:{DEFAULT_PORTS[adapter]}"
        result = _register_and_claim(hub, client_id, adapter, dest)
        print(json.dumps(result, indent=2))
    print(f"claimed -> {dest_host}:27182 (ta320)  :27184 (magictrackpad)  :27183 (mic)  :27185 (xboxelite)")
    print("Receiver window is the mapping surface. Run elevated if games ignore the pointer.")
    if not _port_open(27184):
        threading.Thread(target=trackpad_sink.serve, args=(27184,), name="tp10-sink", daemon=True).start()
    if not _port_open(27183):
        threading.Thread(
            target=mic_sink.serve,
            args=(27183, "", False),
            name="au10-sink",
            daemon=True,
        ).start()
    if not _port_open(27185):
        from client import xbox_sink

        threading.Thread(target=xbox_sink.serve, args=(27185,), name="xb10-sink", daemon=True).start()
    return receiver_app.main(hub=hub, dest_host=dest_host, client_id=client_id)


def main(argv: list[str] | None = None) -> int:
    _load_receiver_env()
    parser = argparse.ArgumentParser(description="PortClaim claim client")
    parser.add_argument("--hub", default=os.environ.get("USB_LOOM_HUB", ""))
    parser.add_argument("--client-id", default=os.environ.get("USB_LOOM_CLIENT_ID", socket.gethostname()))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices")
    sub.add_parser("routes")

    claim = sub.add_parser("claim")
    claim.add_argument("adapter")
    claim.add_argument("--dest", default="", help="host:port for the data plane")
    claim.add_argument("--data-port", type=int, default=0)

    rel = sub.add_parser("release")
    rel.add_argument("adapter")

    sink = sub.add_parser("sink", help="Windows AU10 mic injector (Handy)")
    sink.add_argument("adapter", nargs="?", default="mic")
    sink.add_argument("--port", type=int, default=27183)
    sink.add_argument("--device", default="")
    sink.add_argument("--list-devices", action="store_true")

    conn = sub.add_parser("connect", help="Claim genesis devices and run the Receiver")
    conn.add_argument("--dest-host", default="")
    conn.add_argument("--no-trackpad", action="store_true", help="claim only, do not run the sink")

    args = parser.parse_args(argv)
    if not args.hub:
        raise SystemExit("set --hub or USB_LOOM_HUB")
    hub = args.hub if args.hub.startswith("http") else f"http://{args.hub}:27180"

    if args.cmd == "devices":
        print(json.dumps(request(hub, "GET", "/v1/devices"), indent=2))
        return 0
    if args.cmd == "routes":
        print(json.dumps(request(hub, "GET", "/v1/routes"), indent=2))
        return 0
    if args.cmd == "claim":
        port = args.data_port or DEFAULT_PORTS.get(args.adapter, 27182)
        dest = args.dest or guess_dest(port)
        print(json.dumps(_register_and_claim(hub, args.client_id, args.adapter, dest), indent=2))
        return 0
    if args.cmd == "release":
        print(json.dumps(request(hub, "POST", f"/v1/devices/{args.adapter}/release", {}), indent=2))
        return 0
    if args.cmd == "connect":
        dest_host = (args.dest_host or _self_host()).strip()
        if not dest_host:
            raise SystemExit("set --dest-host or USB_LOOM_SELF")
        if args.no_trackpad:
            _start_sidestick()
            for adapter in CONNECT:
                dest = f"{dest_host}:{DEFAULT_PORTS[adapter]}"
                print(json.dumps(_register_and_claim(hub, args.client_id, adapter, dest), indent=2))
            return 0
        return _connect(hub, args.client_id, dest_host)
    if args.cmd == "sink":
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from client import mic_sink

        if args.list_devices:
            return mic_sink.main(["--list"])
        extra = ["--port", str(args.port)]
        if args.device:
            extra += ["--device", args.device]
        return mic_sink.main(extra)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
