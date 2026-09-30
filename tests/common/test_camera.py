"""Camera wire/client tests: synthetic bytes only, no cameras or drivers."""
import io
import unittest
from unittest.mock import MagicMock, patch

from client.common import claims
from client.common.camera import CameraClient
from client import platforms, ui
from proto import mjpeg


def jpeg(mode=None):
    """A structural test frame, not a photograph or a decoder fixture."""
    mode = mode or mjpeg.Mode()
    sof = (b"\xff\xc0\x00\x11\x08" + mode.height.to_bytes(2, "big") + mode.width.to_bytes(2, "big")
           + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    sos = b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00"
    return b"\xff\xd8" + sof + sos + b"synthetic-not-an-image" + b"\xff\xd9"


def multipart(frame=None, sequence=1):
    frame = frame or jpeg()
    return mjpeg.frame_header(len(frame), sequence) + frame + b"\r\n"


class CameraWireTests(unittest.TestCase):
    def test_modes_reject_bad_types_and_unbounded_values(self):
        for values in ({"width": True}, {"height": "720"}, {"fps": float("nan")}, {"fps": 1000},
                       {"width": 65535}, {"codec": "other"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                mjpeg.Mode.parse(values)
        self.assertEqual(mjpeg.Mode.parse({}), mjpeg.Mode(1280, 720, 30))

    def test_round_trip_multiple_frames_and_short_reads(self):
        class Short(io.BytesIO):
            def read1(self, size):return super().read1(min(size, 3))
        reader = mjpeg.FrameReader(Short(multipart() + multipart(sequence=2)), mjpeg.Mode())
        self.assertEqual(reader.read(), jpeg())
        self.assertEqual(reader.read(), jpeg())
        self.assertEqual(reader.sequence, 2)

    def test_jpeg_bounds_dimensions_and_embedded_thumbnail(self):
        frame = jpeg()
        thumbnail = b"\xff\xd8private-thumb\xff\xd9"
        app = b"\xff\xe1" + (len(thumbnail) + 2).to_bytes(2, "big") + thumbnail
        mjpeg.validate_jpeg(frame[:2] + app + frame[2:], mjpeg.Mode())
        for bad in (b"", b"no image", frame[:-2], frame + frame, b"\xff\xd8" + b"x" * mjpeg.MAX_FRAME,
                    frame + b"\0", frame + b"\0" * 3, frame + b"\0" * 7,
                    jpeg(mjpeg.Mode(640, 480)), frame.replace(b"\xff\xc0", b"\xff\xc2"),
                    frame[:-2] + b"\xff\xd8second-image" + frame[-2:]):
            with self.subTest(size=len(bad)), self.assertRaises(ValueError):
                mjpeg.validate_jpeg(bad, mjpeg.Mode())

    def test_malformed_and_oversized_multipart_is_rejected(self):
        good = multipart()
        variants = [good.replace(b"Content-Type: image/jpeg", b"Content-Type: text/plain"),
                    good.replace(b"Content-Length:", b"Content-Length: 999999999"),
                    good.replace(b"X-PortClaim-Sequence: 1", b"X-PortClaim-Sequence: -1"),
                    good.replace(b"\r\n\r\n", b"\r\nContent-Type: image/jpeg\r\n\r\n"),
                    good.replace(b"--portclaim-mjpeg-v1", b"--other"), good[:-1],
                    b"--" + mjpeg.BOUNDARY + b"\r\n" + b"x" * 300]
        for value in variants:
            with self.subTest(size=len(value)), self.assertRaises((ValueError, EOFError)):
                mjpeg.FrameReader(io.BytesIO(value), mjpeg.Mode()).read()

    def test_replay_and_total_deadline(self):
        reader = mjpeg.FrameReader(io.BytesIO(multipart() * 2), mjpeg.Mode())
        reader.read()
        with self.assertRaises(ValueError):reader.read()
        clock = [0.0]
        class Trickle(io.BytesIO):
            def read1(self, size):
                clock[0] += .5
                return super().read1(1)
        reader = mjpeg.FrameReader(Trickle(multipart()), mjpeg.Mode(), lambda: clock[0])
        with self.assertRaises(TimeoutError):reader.read()


class CameraClientTests(unittest.TestCase):
    def test_invalid_origins_and_missing_token(self):
        for url in ("file:///dev/video0", "http://token@hub", "http://hub?secret=x", "http://hub/camera", ""):
            with self.subTest(url=url), self.assertRaises(ValueError):CameraClient(url, "test-only", "test")
        with self.assertRaises(ValueError):CameraClient("http://hub", "", "test")

    def test_no_redirects_and_header_only_credentials(self):
        client = CameraClient("http://hub.invalid", "test-only", "test")
        conn = MagicMock()
        conn.getresponse.return_value.status = 302
        with patch.object(client, "_connection", return_value=conn), self.assertRaises(OSError):
            client.claim(mjpeg.Mode())
        method, path, body, headers = conn.request.call_args.args
        self.assertEqual(path, "/v1/devices/webcam/claim")
        self.assertNotIn("test-only", path)
        self.assertNotIn("test-only", body.decode())
        self.assertEqual(headers["X-Usb-Loom-Token"], "test-only")
        conn.close.assert_called_once()

    def test_claim_mismatch_releases_lease_and_does_not_accept_changed_path(self):
        client = CameraClient("http://hub.invalid", "test-only", "test")
        response = {"lease": "x" * 43, "mode": mjpeg.Mode().as_dict(), "stream_path": "/other"}
        with patch.object(client, "_post", side_effect=[response, {}]) as post:
            with self.assertRaises(ValueError):client.claim(mjpeg.Mode())
        self.assertIsNone(client.lease)
        self.assertEqual(post.call_args.args, ("release", {}))

    def test_malformed_returned_mode_also_releases_lease(self):
        client = CameraClient("http://hub.invalid", "test-only", "test")
        response = {"lease": "x" * 43, "mode": None, "stream_path": mjpeg.STREAM_PATH}
        with patch.object(client, "_post", side_effect=[response, {}]) as post:
            with self.assertRaises(ValueError):client.claim(mjpeg.Mode())
        self.assertIsNone(client.lease)
        self.assertEqual(post.call_args.args, ("release", {}))

    def test_expired_release_is_idempotent_but_auth_failure_is_not(self):
        for status in (403, 401):
            client = CameraClient("http://hub.invalid", "test-only", "test")
            client.lease = "x" * 43
            conn = MagicMock()
            conn.getresponse.return_value.status = status
            with patch.object(client, "_connection", return_value=conn):
                if status == 401:
                    with self.assertRaises(OSError):client.release()
                else:client.release()
            self.assertIsNone(client.lease)

    def test_stream_credentials_stay_in_headers(self):
        client = CameraClient("http://hub.invalid", "test-only", "test")
        client.lease = "x" * 43
        conn = MagicMock()
        response = conn.getresponse.return_value
        response.status = 200
        response.getheader.return_value = mjpeg.CONTENT_TYPE
        with patch.object(client, "_connection", return_value=conn):
            with client.stream(mjpeg.Mode()):pass
        self.assertEqual(conn.request.call_args.args, ("GET", mjpeg.STREAM_PATH))
        self.assertEqual(conn.request.call_args.kwargs["headers"][mjpeg.LEASE_HEADER], "x" * 43)
        response.close.assert_called_once()

    def test_oversized_control_response(self):
        client = CameraClient("http://hub.invalid", "test-only", "test")
        conn = MagicMock()
        conn.getresponse.return_value.status = 200
        conn.getresponse.return_value.read.return_value = b"x" * 32769
        with patch.object(client, "_connection", return_value=conn), self.assertRaises(ValueError):
            client.claim(mjpeg.Mode())


class CameraBoundaryTests(unittest.TestCase):
    def test_camera_is_never_in_bulk_connect_or_udp_ports(self):
        self.assertNotIn("webcam", claims.CONNECT)
        self.assertNotIn("webcam", claims.GENESIS)
        self.assertNotIn("webcam", claims.DEFAULT_PORTS)

    def test_generic_claim_cannot_register_or_claim_a_camera(self):
        with patch.object(claims, "request") as request:
            with self.assertRaises(SystemExit):
                claims._register_and_claim("http://hub.invalid", "test", "webcam", "192.0.2.20:27182")
        request.assert_not_called()

    def test_inventory_refresh_preserves_camera_pane_when_claim_label_changes(self):
        app = ui.ReceiverApp.__new__(ui.ReceiverApp)
        app.device_var = MagicMock(); app.device_var.get.return_value = "Webcam (not claimed)"
        app._device_meta = {"Webcam (not claimed)": {"adapter": "webcam"}}
        app._inventory = lambda: (["Magic Trackpad", "Webcam (claimed)"], {
            "Magic Trackpad": {"adapter": "magictrackpad"}, "Webcam (claimed)": {"adapter": "webcam"}})
        app.device_combo = MagicMock(); app._show_pane = MagicMock()
        app._refresh_devices()
        app.device_var.set.assert_called_once_with("Webcam (claimed)")

    def test_windows_does_not_claim_camera_or_import_linux_backend(self):
        app = ui.ReceiverApp.__new__(ui.ReceiverApp)
        app.status = MagicMock()
        with patch.object(platforms.sys, "platform", "win32"), patch.object(ui.claim, "request") as request:
            self.assertIsNone(platforms.camera_backend())
            app._start_camera()
        request.assert_not_called()
        self.assertIn("nothing claimed", app.status.configure.call_args.kwargs["text"])

    def test_camera_claim_bypasses_udp_and_destination_requirements(self):
        app = ui.ReceiverApp.__new__(ui.ReceiverApp)
        app._selected_adapter = lambda: "webcam"
        app._start_camera = MagicMock()
        with patch.object(ui.claim, "_register_and_claim") as claim:
            app._claim_selected()
        app._start_camera.assert_called_once()
        claim.assert_not_called()

    def test_state_text_counts_written_not_merely_received_frames(self):
        app = ui.ReceiverApp.__new__(ui.ReceiverApp)
        app.camera = MagicMock()
        app.camera.snapshot.return_value = {"state": "streaming", "error": "", "frames": 7,
                                            "last_frame": 0, "busy": True}
        app.camera_status = MagicMock(); app.camera_start = MagicMock(); app.camera_stop = MagicMock()
        app._camera_status_tick()
        self.assertIn("7 frames written", app.camera_status.configure.call_args.kwargs["text"])
