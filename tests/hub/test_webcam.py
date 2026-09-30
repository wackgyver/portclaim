"""Hub camera tests: fake capture or cached metadata fixtures, never real video."""
from contextlib import contextmanager
import http.client
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hub"))
import server
import webcam
import v4l2_capture as v4l2
from client.common.camera import CameraClient
from proto import mjpeg
from tests.common.test_camera import jpeg


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.clock = [0.0]
        self.source = MagicMock()
        self.capture = MagicMock()
        self.camera = webcam.Camera(lambda: self.source, self.capture, lambda: self.clock[0])
        self.body = {"client_id": "test", "mode": {}}

    def claim(self):return self.camera.claim(self.body, "192.0.2.20")

    def test_claim_is_reservation_only_and_snapshot_is_secret_free(self):
        result = self.claim()
        self.capture.assert_not_called()
        self.assertNotIn("lease", self.camera.route())
        self.assertNotIn(result["lease"], repr(self.camera.lease))
        self.assertFalse(self.camera.route()["streaming"])

    def test_busy_claim_never_steals_or_reopens_camera(self):
        first = self.claim()
        with self.assertRaises(webcam.CameraError) as failure:self.claim()
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual(self.camera.lease.token, first["lease"])
        self.capture.assert_not_called()

    def test_pending_lease_expiry_and_old_release_cannot_clear_new_owner(self):
        first = self.claim()
        self.clock[0] = 6
        self.assertIsNone(self.camera.route())
        second = self.claim()
        with self.assertRaises(webcam.CameraError):self.camera.release(first["lease"], "192.0.2.20")
        self.assertEqual(self.camera.lease.token, second["lease"])

    def test_wrong_peer_token_and_unicode_fail_before_capture(self):
        result = self.claim()
        for token, peer in (("bad", "192.0.2.20"), (result["lease"], "192.0.2.21"), ("🧵", "192.0.2.20")):
            with self.subTest(peer=peer), self.assertRaises(webcam.CameraError):
                with self.camera.session(token, peer):pass
        self.capture.assert_not_called()

    def test_single_stream_release_and_cleanup(self):
        result = self.claim()
        with self.camera.session(result["lease"], "192.0.2.20") as (lease, _):
            self.assertTrue(self.camera.route()["streaming"])
            with self.assertRaises(webcam.CameraError):
                with self.camera.session(result["lease"], "192.0.2.20"):pass
            self.camera.release(result["lease"], "192.0.2.20")
            self.assertFalse(self.camera.alive(lease))
            with self.assertRaises(webcam.CameraError):self.claim()
        self.capture.return_value.__exit__.assert_called_once()
        self.assertIsNone(self.camera.route())
        self.claim()  # Physical stream lock was released.

    def test_capture_failure_expires_claim_and_unlocks(self):
        self.capture.side_effect = OSError("unplugged")
        result = self.claim()
        with self.assertRaises(OSError):
            with self.camera.session(result["lease"], "192.0.2.20"):pass
        self.assertIsNone(self.camera.route())
        self.assertFalse(self.camera.stream_lock.locked())

    def test_expiry_during_capture_preparation_closes_camera(self):
        @contextmanager
        def delayed(*args):
            self.clock[0] = 6
            try:yield MagicMock()
            finally:self.closed = True
        self.closed = False
        self.camera.capture = delayed
        result = self.claim()
        with self.assertRaises(webcam.CameraError):
            with self.camera.session(result["lease"], "192.0.2.20"):pass
        self.assertTrue(self.closed)
        self.assertFalse(self.camera.stream_lock.locked())

    def test_hub_requires_operator_opt_in(self):
        with patch.dict(server.os.environ, {"USB_LOOM_CAMERA_ENABLED": "0"}):
            registry = server.Registry()
        with patch.object(registry.camera, "select_source") as source:
            with self.assertRaises(webcam.CameraError):registry.claim("webcam", self.body, "192.0.2.20")
        source.assert_not_called()
        self.assertIsNone(registry.camera.route())

    def test_stream_lease_expires_without_successful_writes(self):
        result = self.claim()
        with self.camera.session(result["lease"], "192.0.2.20") as (lease, _):
            self.clock[0] = 2
            self.camera.touch(lease)
            self.clock[0] = 5
            self.assertFalse(self.camera.alive(lease))
        self.assertFalse(self.camera.stream_lock.locked())

    def test_bad_claims_do_not_touch_capture(self):
        for body in ({}, {"client_id": "x", "dest": "somewhere:27186"},
                     {"client_id": "x", "mode": {"fps": True}}, {"client_id": "\n"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.camera.claim(body, "192.0.2.20")
        self.capture.assert_not_called()


class FlatDeploymentTests(unittest.TestCase):
    def test_flat_hub_contains_all_camera_dependencies_and_opens_nothing(self):
        root = Path(__file__).resolve().parents[2]
        modules = ("hid.py", "server.py", "audio.py", "trackpad.py", "webcam.py", "v4l2_capture.py")
        with tempfile.TemporaryDirectory() as directory:
            for name in modules:
                shutil.copyfile(root / "hub" / name, Path(directory) / name)
            for source in (root / "proto").glob("*.py"):
                shutil.copyfile(source, Path(directory) / source.name)
            code = '''
import os, socket, subprocess, ssl, http.server
def denied(*args, **kwargs): raise AssertionError("flat import performed I/O")
os.open = socket.socket = subprocess.Popen = denied
import server
assert not server.REG.camera.enabled
assert server.REG.camera.route() is None
assert 'webcam' in server.CLAIMABLE
'''
            result = subprocess.run([sys.executable, "-c", code], cwd=directory,
                                    env={**os.environ, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1",
                                         "PYTHONDONTWRITEBYTECODE": "1", "USB_LOOM_CAMERA_ENABLED": "0"},
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        installer = (root / "deploy/install.sh").read_text()
        for name in ("webcam.py", "v4l2_capture.py", "mjpeg.py"):
            self.assertIn(name, installer)


class DiscoveryTests(unittest.TestCase):
    def test_sysfs_only_discovery_filters_metadata_and_detects_replug(self):
        with tempfile.TemporaryDirectory() as directory:
            usb = Path(directory) / "usb/1-1"
            interface = usb / "1-1:1.0"
            node = interface / "video4linux/video0"
            node.mkdir(parents=True)
            for key, value in {"idVendor": "0c45", "idProduct": "6366", "devnum": "7"}.items():
                (usb / key).write_text(value)
            (node / "index").write_text("0")
            (node / "name").write_text("Synthetic USB Camera")
            (node / "device").symlink_to(interface)
            (interface / "driver").symlink_to(Path(directory) / "uvcvideo")
            source = webcam.source_from_sysfs(node)
            self.assertEqual(source.vid, "0C45")
            source.verify()
            (usb / "devnum").write_text("8")
            with self.assertRaises(OSError):source.verify()
            (node / "index").write_text("1")
            self.assertIsNone(webcam.source_from_sysfs(node))

    def test_ambiguous_absent_and_unstable_selection_fail_closed(self):
        for rows in ([], [MagicMock(), MagicMock()]):
            with patch.object(webcam, "sources", return_value=rows), patch.dict(webcam.os.environ, {"USB_LOOM_CAMERA_DEVICE": ""}):
                with self.assertRaises(webcam.CameraError):webcam.choose_source()
        with patch.object(webcam, "sources", return_value=[MagicMock()]), patch.dict(
                webcam.os.environ, {"USB_LOOM_CAMERA_DEVICE": "/dev/video0"}):
            with self.assertRaises(webcam.CameraError):webcam.choose_source()


class V4L2CaptureTests(unittest.TestCase):
    def test_native_eight_byte_alignment_padding_only(self):
        for extra in range(8):
            frame = jpeg()[:-2] + b"x" * extra + b"\xff\xd9"
            padding = (-len(frame)) % 8
            padded = frame + b"\0" * padding
            self.assertEqual(v4l2.strip_mjpeg_alignment_padding(padded), frame)
            mjpeg.validate_jpeg(v4l2.strip_mjpeg_alignment_padding(padded), mjpeg.Mode())
            if padding:
                with self.assertRaises(ValueError):mjpeg.validate_jpeg(padded, mjpeg.Mode())

    def test_padding_does_not_accept_garbage_unaligned_or_long_trailers(self):
        frame = jpeg()[:-2]
        frame += b"x" * (-(len(frame) + 2) % 8) + b"\xff\xd9"
        for raw in (frame + b"\0", frame + b"\0" * 4, frame + b"\0" * 8, frame + b"\0" * 64,
                    frame + b"garbage\0", frame[:-2] + b"\0" * 4):
            self.assertEqual(v4l2.strip_mjpeg_alignment_padding(raw), raw)
            with self.assertRaises(ValueError):mjpeg.validate_jpeg(raw, mjpeg.Mode())

    def test_padded_concatenated_images_still_fail_strict_validation(self):
        raw = jpeg() + jpeg()
        raw += b"\0" * (-len(raw) % 8)
        with self.assertRaises(ValueError):
            mjpeg.validate_jpeg(v4l2.strip_mjpeg_alignment_padding(raw), mjpeg.Mode())

    def test_capture_strips_alignment_before_return_and_requeues(self):
        frame = jpeg()
        raw = frame + b"\0" * (-len(frame) % 8)
        capture = v4l2.Capture.__new__(v4l2.Capture)
        capture.fd, capture.buffers = 123, [raw]
        def dq(fd, number, value, direction=3):
            if number == 17:value.index = 0; value.bytesused = len(raw)
            return value
        with patch.object(v4l2.select, "select", return_value=([123], [], [])), patch.object(
                v4l2, "ioctl", side_effect=dq) as ioctl:
            self.assertEqual(capture.read(), frame)
            self.assertEqual(ioctl.call_args.args[1], 15)

    def fake_ioctl(self, fd, number, value, direction=3):
        if number == 0:
            value.driver = b"uvcvideo"; value.capabilities = 0x04000001
        elif number == 9:
            value.length = 4096; value.m.offset = value.index * 4096
        return value

    def test_failed_setup_closes_partial_maps_and_fd(self):
        first = MagicMock()
        with patch.object(v4l2.os, "open", return_value=123), patch.object(v4l2.os, "fstat",
                return_value=SimpleNamespace(st_mode=0o20600, st_rdev=v4l2.os.makedev(81, 0))), patch.object(
                v4l2.os, "close") as close, patch.object(v4l2, "ioctl", side_effect=self.fake_ioctl), patch.object(
                v4l2.mmap, "mmap", side_effect=[first, OSError("map failed")]):
            with self.assertRaises(OSError):v4l2.Capture(MagicMock(path="/dev/video0"), mjpeg.Mode())
        first.close.assert_called_once(); close.assert_called_once_with(123)

    def test_queue_errors_and_timeout(self):
        capture = v4l2.Capture.__new__(v4l2.Capture)
        capture.fd = 123
        capture.buffers = [b"frame-a", b"frame-b"]
        def dq(fd, number, value, direction=3):
            if number == 17:
                value.index = 0; value.bytesused = 100
            return value
        with patch.object(v4l2.select, "select", return_value=([123], [], [])), patch.object(
                v4l2, "ioctl", side_effect=dq) as ioctl:
            with self.assertRaises(OSError):capture.read()
            self.assertEqual(ioctl.call_args.args[1], 15)  # Always requeue valid buffer indices.
        with patch.object(v4l2.select, "select", return_value=([], [], [])):
            self.assertIsNone(capture.read())

    def test_bad_buffer_index_is_not_dereferenced(self):
        capture = v4l2.Capture.__new__(v4l2.Capture)
        capture.fd, capture.buffers = 123, [b"frame"]
        with patch.object(v4l2.select, "select", return_value=([123], [], [])), patch.object(
                v4l2, "ioctl", return_value=SimpleNamespace(index=999)):
            with self.assertRaises(OSError):capture.read()


class HttpCameraTests(unittest.TestCase):
    def setUp(self):
        self.closed = threading.Event()
        self.opened = threading.Event()
        closed, opened = self.closed, self.opened
        class FakeCapture:
            def __init__(self, source, mode):self.mode = mode; opened.set()
            def __enter__(self):return self
            def __exit__(self, *args):closed.set()
            def read(self):time.sleep(.01); return jpeg(self.mode)
        self.registry = server.Registry()
        self.registry.camera = webcam.Camera(lambda: MagicMock(), FakeCapture)
        self.patches = [patch.object(server, "REG", self.registry), patch.object(server, "DEFAULT_TOKEN", "test-only"),
                        patch.object(server.Handler, "log_message")]
        for p in self.patches:p.start()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.client = CameraClient(f"http://127.0.0.1:{self.httpd.server_port}", "test-only", "test")

    def tearDown(self):
        try:self.client.release()
        except OSError:pass
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(timeout=2)
        for p in reversed(self.patches):p.stop()

    def get(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=2)
        try:
            conn.request("GET", path, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read()
        finally:conn.close()

    def test_auth_and_lease_required_before_any_capture(self):
        self.assertEqual(self.get(mjpeg.STREAM_PATH)[0], 401)
        self.assertEqual(self.get(mjpeg.STREAM_PATH, {"X-Usb-Loom-Token": "test-only"})[0], 403)
        self.assertFalse(self.opened.is_set())

    def test_real_http_roundtrip_and_release_preserves_other_routes(self):
        self.registry.claim("mic", {"dest": "192.0.2.20:27183", "client_id": "audio-owner"})
        self.client.claim(mjpeg.Mode())
        self.assertFalse(self.opened.is_set())
        status, raw = self.get("/v1/routes", {"X-Usb-Loom-Token": "test-only"})
        self.assertEqual(status, 200)
        self.assertNotIn(self.client.lease.encode(), raw)
        self.assertEqual(json.loads(raw)["routes"]["webcam"]["client_id"], "test")
        with self.client.stream(mjpeg.Mode()) as reader:
            self.assertEqual(reader.read(), jpeg())
            self.client.release()
        self.assertTrue(self.closed.wait(2))
        self.assertIsNone(self.registry.camera.route())
        self.assertEqual(self.registry.route("mic")["client_id"], "audio-owner")

    def test_control_body_must_be_bounded_object(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=2)
        try:
            conn.request("POST", "/v1/devices/webcam/claim", b"[]", {"X-Usb-Loom-Token": "test-only"})
            self.assertEqual(conn.getresponse().status, 400)
        finally:conn.close()
        self.assertFalse(self.opened.is_set())
