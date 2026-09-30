"""OS-independent claim protocol and network helpers. No configuration is read on import."""
from __future__ import annotations
import json
import os
import socket
import urllib.error
import urllib.request

DEFAULT_PORTS = {
    "ta320": 27182,
    "generic": 27182,
    "mic": 27183,
    "magictrackpad": 27184,
    "xboxelite": 27185,
}


GENESIS = ("ta320", "magictrackpad")
CONNECT = ("ta320", "magictrackpad", "mic", "xboxelite")

def _token() -> str:
    return os.environ.get("USB_LOOM_TOKEN", "").strip()


def _self_host() -> str:
    env = os.environ.get("USB_LOOM_SELF", "").strip()
    if env:
        return env
    hostname = socket.gethostname()
    try:
        ip = socket.gethostbyname(hostname)
    except OSError:
        ip = ""
    if ip and not ip.startswith("127."):
        return ip
    return ""


def request(hub: str, method: str, path: str, body: dict | None = None) -> dict:
    if not _token():
        raise SystemExit("USB_LOOM_TOKEN is unset")
    if not hub:
        raise SystemExit("set --hub or USB_LOOM_HUB")
    url = hub.rstrip("/") + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Usb-Loom-Token": _token(),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"{exc.code} {path}: {detail}") from exc


def guess_dest(port: int) -> str:
    ip = _self_host()
    if not ip:
        raise SystemExit("set USB_LOOM_SELF or --dest (no LAN default)")
    return f"{ip}:{port}"


def _port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        sock.close()


def _register_and_claim(hub: str, client_id: str, adapter: str, dest: str, *, native_touchpad: bool = False) -> dict:
    if adapter == "webcam":
        raise SystemExit("Use the explicit Webcam Start action or webcam_sink.py --start; camera is not a UDP claim")
    request(
        hub,
        "POST",
        "/v1/clients",
        {
            "id": client_id,
            "name": client_id,
            "host": dest.rsplit(":", 1)[0],
            "data_port": int(dest.rsplit(":", 1)[-1]),
        },
    )
    return request(
        hub,
        "POST",
        f"/v1/devices/{adapter}/claim",
        {"client_id": client_id, "dest": dest,
         **({"native_touchpad": native_touchpad}
            if adapter == "magictrackpad" else {})},
    )
