"""Explicit camera control/stream client. No redirects, URL secrets or auto-claim."""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import json
from urllib.parse import urlsplit

from proto.mjpeg import CONTENT_TYPE, FrameReader, LEASE_HEADER, Mode, STREAM_PATH


class CameraClient:
    def __init__(self, hub, token, client_id):
        url = urlsplit(hub)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment or url.path not in {"", "/"}):
            raise ValueError("camera Hub must be an http(s) origin without credentials, query or path")
        if not token or any(c in token for c in "\r\n"):
            raise ValueError("USB_LOOM_TOKEN is required")
        self.url, self.token, self.client_id = url, token, client_id
        self.lease = None

    def _connection(self, timeout=5):
        cls = http.client.HTTPSConnection if self.url.scheme == "https" else http.client.HTTPConnection
        return cls(self.url.hostname, self.url.port, timeout=timeout)

    def _headers(self):
        headers = {"X-Usb-Loom-Token": self.token, "Content-Type": "application/json"}
        if self.lease:
            headers[LEASE_HEADER] = self.lease
        return headers

    def _post(self, action, body):
        connection = self._connection()
        try:
            connection.request("POST", f"/v1/devices/webcam/{action}", json.dumps(body).encode(), self._headers())
            response = connection.getresponse()
            # http.client never follows redirects or forwards credentials elsewhere.
            if action == "release" and response.status == 403:
                return {}  # Stream closure/expiry already invalidated this lease; never release another owner.
            if response.status != 200:
                raise OSError(f"camera {action} failed (HTTP {response.status})")
            raw = response.read(32769)
            if len(raw) > 32768:
                raise ValueError("oversized camera control response")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("invalid camera control response")
            return result
        finally:
            connection.close()

    def claim(self, mode):
        if self.lease is not None:
            raise RuntimeError("camera client already owns a lease")
        result = self._post("claim", {"client_id": self.client_id, "mode": mode.as_dict()})
        lease = result.get("lease")
        if (not isinstance(lease, str) or not 32 <= len(lease) <= 128
                or not all(c.isascii() and (c.isalnum() or c in "-_") for c in lease)):
            raise ValueError("invalid camera lease response")
        self.lease = lease
        try:
            if result.get("stream_path") != STREAM_PATH or Mode.parse(result.get("mode")) != mode:
                raise ValueError("unexpected stream/mode")
        except (TypeError, ValueError):
            try:
                self.release()
            finally:
                raise ValueError("camera response changed the requested stream/mode") from None

    @contextmanager
    def stream(self, mode):
        if not self.lease:
            raise RuntimeError("claim camera explicitly before streaming")
        connection = self._connection(timeout=6)
        response = None
        try:
            connection.request("GET", STREAM_PATH, headers=self._headers())
            wire_socket = connection.sock
            response = connection.getresponse()
            if response.status != 200 or response.getheader("Content-Type") != CONTENT_TYPE:
                raise OSError(f"camera stream unavailable (HTTP {response.status})")
            wire_socket.settimeout(1)
            yield FrameReader(response, mode)
        finally:
            if response is not None:
                response.close()
            connection.close()

    def release(self):
        if self.lease is None:
            return
        try:
            self._post("release", {})
        finally:
            self.lease = None
