#!/usr/bin/env python3
"""usb-loom hub — inventory + claim routes + HID stream.

Control: HTTP :27180
Data:    UDP SB10 (HID), AU10 (mic), or TP10 (trackpad) to the claimed dest

    python3 hub/server.py --control-port 27180
    python3 client/claim.py --hub http://HUB:27180 claim ta320 --dest DEST:27182
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import audio as audio_hub  # noqa: E402
import trackpad as trackpad_hub  # noqa: E402
from hid import (  # noqa: E402
    ADAPTERS,
    EvdevDevice,
    encode_sb10,
    iter_event_nodes,
    open_matching,
)

CLAIMABLE = set(ADAPTERS) | {"mic", "magictrackpad"}
HID_ADAPTERS = set(ADAPTERS)

DEFAULT_TOKEN = os.environ.get("USB_LOOM_TOKEN", "").strip()


class Registry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        # device adapter name -> {client_id, dest_host, dest_port, claimed_at}
        self.routes: dict[str, dict] = {}
        self.clients: dict[str, dict] = {}

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "clients": dict(self.clients),
                "routes": dict(self.routes),
            }

    def register(self, body: dict) -> dict:
        client_id = str(body.get("id") or "").strip()
        if not client_id:
            raise ValueError("id required")
        rec = {
            "id": client_id,
            "name": body.get("name") or client_id,
            "host": body.get("host") or "",
            "data_port": int(body.get("data_port") or 27182),
            "seen": time.time(),
        }
        with self.lock:
            self.clients[client_id] = rec
        return rec

    def claim(self, adapter: str, body: dict) -> dict:
        if adapter not in CLAIMABLE:
            raise ValueError(f"unknown adapter {adapter}")
        dest = body.get("dest") or ""
        if ":" not in str(dest):
            raise ValueError("dest must be host:port")
        host, port_s = str(dest).rsplit(":", 1)
        route = {
            "adapter": adapter,
            "client_id": body.get("client_id") or host,
            "dest_host": host,
            "dest_port": int(port_s),
            "claimed_at": time.time(),
        }
        with self.lock:
            self.routes[adapter] = route
        return route

    def release(self, adapter: str) -> None:
        with self.lock:
            self.routes.pop(adapter, None)

    def route(self, adapter: str) -> dict | None:
        with self.lock:
            rec = self.routes.get(adapter)
            return dict(rec) if rec else None


REG = Registry()


def inventory() -> list[dict]:
    rows = []
    for path in iter_event_nodes():
        try:
            dev = EvdevDevice(path)
        except OSError:
            continue
        adapters = [name for name, adp in ADAPTERS.items() if adp.matches(dev)]
        if trackpad_hub.matches(dev):
            adapters.append("magictrackpad")
        named = [name for name in adapters if name != "generic"]
        if named:
            adapters = named
        rows.append(
            {
                "path": str(dev.path),
                "name": dev.name,
                "vid": f"{dev.id.vendor:04X}",
                "pid": f"{dev.id.product:04X}",
                "adapters": adapters,
                "kind": "hid",
            }
        )
        dev.close()
    rows.extend(audio_hub.inventory())
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("hub: " + (fmt % args) + "\n")

    def _auth(self) -> bool:
        token = self.headers.get("X-Usb-Loom-Token", "")
        return bool(DEFAULT_TOKEN) and token == DEFAULT_TOKEN

    def _json(self, code: int, payload: dict | list) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_GET(self) -> None:
        if not self._auth():
            return self._json(401, {"error": "token"})
        path = urlparse(self.path).path
        if path == "/v1/health":
            return self._json(200, {"ok": True, "role": "hub"})
        if path == "/v1/devices":
            return self._json(200, {"devices": inventory(), **REG.snapshot()})
        if path == "/v1/routes":
            return self._json(200, REG.snapshot())
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._auth():
            return self._json(401, {"error": "token"})
        path = urlparse(self.path).path.rstrip("/")
        try:
            body = self._read_body()
            if path == "/v1/clients":
                return self._json(200, REG.register(body))
            if path.startswith("/v1/devices/") and path.endswith("/claim"):
                adapter = path.split("/")[3]
                return self._json(200, REG.claim(adapter, body))
            if path.startswith("/v1/devices/") and path.endswith("/release"):
                adapter = path.split("/")[3]
                REG.release(adapter)
                return self._json(200, {"released": adapter})
        except (ValueError, json.JSONDecodeError, KeyError, IndexError) as exc:
            return self._json(400, {"error": str(exc)})
        return self._json(404, {"error": "not found"})


def stream_loop(adapter_name: str, hz: int, grab: bool) -> None:
    adapter = ADAPTERS[adapter_name]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    period = 1.0 / max(30, hz)
    seq = 0
    while True:
        route = REG.route(adapter_name)
        if route is None:
            time.sleep(0.2)
            continue
        dev = open_matching(adapter.matches)
        if dev is None:
            time.sleep(0.5)
            continue
        print(f"stream {adapter_name} {dev.path} -> {route['dest_host']}:{route['dest_port']}")
        if adapter_name == "xboxelite":
            # xpad finishes GIP init after the node appears; reopen so we do not grab a dead fd.
            time.sleep(1.0)
            path = dev.path
            dev.close()
            dev = open_matching(adapter.matches)
            if dev is None:
                time.sleep(0.3)
                continue
            if dev.path != path:
                print(f"xboxelite reopened {dev.path} (was {path})")
        # Skip exclusive grab on xboxelite: Ultrabase TT resets made EVIOCGRAB return ENODEV.
        if grab and adapter_name != "xboxelite":
            dev.grab(True)
        last = 0.0
        try:
            while REG.route(adapter_name):
                try:
                    dev.pump()
                except OSError:
                    print(f"lost {dev.path}")
                    break
                now = time.monotonic()
                if now - last < period:
                    time.sleep(max(0.0, period - (now - last)))
                    continue
                last = now
                seq = (seq + 1) & 0xFFFFFFFF
                current = REG.route(adapter_name)
                if current is None:
                    break
                sock.sendto(
                    encode_sb10(seq, adapter.to_state(dev)),
                    (current["dest_host"], current["dest_port"]),
                )
        finally:
            dev.close()
        time.sleep(0.3)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="usb-loom hub")
    parser.add_argument("--control-port", type=int, default=int(os.environ.get("USB_LOOM_CONTROL_PORT", 27180)))
    parser.add_argument("--bind", default=os.environ.get("USB_LOOM_BIND", "0.0.0.0"))
    parser.add_argument("--adapter", action="append", default=None, help="stream adapters (default: all)")
    parser.add_argument("--hz", type=int, default=125)
    parser.add_argument("--no-grab", action="store_true")
    args = parser.parse_args(argv)

    if not DEFAULT_TOKEN:
        print("USB_LOOM_TOKEN is required", file=sys.stderr)
        return 2
    adapters = args.adapter or (list(ADAPTERS) + ["mic", "magictrackpad"])
    httpd = ThreadingHTTPServer((args.bind, args.control_port), Handler)
    print(f"usb-loom hub control http://{args.bind}:{args.control_port}  (token is set)")
    for name in adapters:
        if name == "mic":
            threading.Thread(
                target=audio_hub.stream_mic,
                args=(REG.route, "mic"),
                name="stream-mic",
                daemon=True,
            ).start()
            continue
        if name == "magictrackpad":
            threading.Thread(
                target=trackpad_hub.stream_trackpad,
                args=(REG.route, "magictrackpad", args.hz, not args.no_grab),
                name="stream-magictrackpad",
                daemon=True,
            ).start()
            continue
        if name not in HID_ADAPTERS:
            print(f"skip unknown adapter {name}", file=sys.stderr)
            continue
        threading.Thread(
            target=stream_loop,
            args=(name, args.hz, not args.no_grab),
            name=f"stream-{name}",
            daemon=True,
        ).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
