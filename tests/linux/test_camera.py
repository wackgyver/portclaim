"""Virtual camera lifecycle mocks plus synthetic FFmpeg decoding (no /dev/video)."""
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from client.linux import camera
from proto.mjpeg import Mode, validate_jpeg
from tests.common.test_camera import jpeg


class LoopbackTests(unittest.TestCase):
    def fake_ioctl(self, fd, number, data, direction=3):
        if number == 0:
            data[:16] = b"v4l2 loopback".ljust(16, b"\0")
            data[16:48] = camera.LABEL.ljust(32, b"\0")
            struct.pack_into("=II", data, 84, 0x80000002, 2)
        return data

    def patches(self):
        return [patch.object(camera.Path, "resolve", return_value=Path("/sys/devices/virtual/video4linux/video42")),
                patch.object(camera.os, "open", return_value=123),
                patch.object(camera.os, "fstat", return_value=SimpleNamespace(
                    st_mode=stat.S_IFCHR, st_rdev=os.makedev(81, 42))),
                patch.object(camera.fcntl, "flock"), patch.object(camera.os, "close")]

    def test_only_owned_loopback_is_configured_and_blackened(self):
        patches = self.patches()
        for p in patches:p.start()
        try:
            with patch.object(camera, "ioctl", side_effect=self.fake_ioctl) as ioctl, patch.object(
                    camera.os, "write", side_effect=lambda fd, frame: len(frame)) as write:
                output = camera.Loopback("/dev/video42", Mode())
                output.close()
                self.assertEqual(write.call_count, 2)  # Initial and final synthetic black, not camera images.
                self.assertEqual(bytes(write.call_args.args[1][:4]), b"\x10\x80\x10\x80")
                controls = [struct.unpack("=Ii", call.args[2]) for call in ioctl.call_args_list if call.args[1] == 28]
                self.assertIn((camera.CID_BASE + 2, 1000), controls)
                self.assertIn((camera.CID_BASE + 1, 0), controls)
        finally:
            for p in reversed(patches):p.stop()

    def test_foreign_label_nonexclusive_or_busy_device_never_gets_controls(self):
        for bad in ("label", "caps", "driver"):
            patches = self.patches()
            for p in patches:p.start()
            try:
                def invalid(fd, number, data, direction=3):
                    self.fake_ioctl(fd, number, data, direction)
                    if bad == "label":data[16:48] = b"Other Camera".ljust(32, b"\0")
                    if bad == "caps":struct.pack_into("=I", data, 88, 3)
                    if bad == "driver":data[:16] = b"uvcvideo".ljust(16, b"\0")
                    return data
                with patch.object(camera, "ioctl", side_effect=invalid) as ioctl, patch.object(camera.os, "write") as write:
                    with self.assertRaises(OSError):camera.Loopback("/dev/video42", Mode())
                    self.assertEqual(ioctl.call_count, 1)
                    write.assert_not_called()
            finally:
                for p in reversed(patches):p.stop()

    def test_racing_producer_fails_before_any_control_change(self):
        patches = self.patches()
        for p in patches:p.start()
        try:
            def busy(fd, number, data, direction=3):
                if number == 5:raise OSError("output format already owned")
                return self.fake_ioctl(fd, number, data, direction)
            with patch.object(camera, "ioctl", side_effect=busy) as ioctl:
                with self.assertRaises(OSError):camera.Loopback("/dev/video42", Mode())
                self.assertNotIn(28, [call.args[1] for call in ioctl.call_args_list])
        finally:
            for p in reversed(patches):p.stop()

    def test_unsafe_output_path_fails_before_open(self):
        with patch.object(camera.os, "open") as opened:
            for name in ("", "/tmp/video0", "/dev/v4l/by-id/something", "/dev/null"):
                with self.assertRaises(OSError):camera.Loopback(name, Mode())
        opened.assert_not_called()

    def test_final_blank_failure_is_reported_but_fd_still_closed(self):
        output = camera.Loopback.__new__(camera.Loopback)
        output.fd, output.configured, output.black, output.frame_size = 123, True, b"test", 4
        with patch.object(camera.os, "write", side_effect=OSError("gone")), patch.object(camera.os, "close") as close:
            with self.assertRaises(OSError):output.close()
        close.assert_called_once_with(123)
        self.assertIsNone(output.fd)


class ControllerTests(unittest.TestCase):
    def test_missing_dependency_does_not_claim_or_spawn(self):
        controller = camera.Controller()
        with patch.object(camera.shutil, "which", return_value=None), patch.object(camera, "CameraClient") as client:
            with self.assertRaises(OSError):controller.start("http://hub", "test-only", "test")
        client.assert_not_called()
        self.assertFalse(controller.snapshot()["busy"])
        self.assertEqual(controller.snapshot()["state"], "error")
        self.assertIn("FFmpeg", controller.snapshot()["error"])

    def test_output_failure_does_not_claim_or_start_decoder(self):
        controller = camera.Controller()
        client = MagicMock()
        with patch.object(camera, "Loopback", side_effect=OSError("no loopback")), patch.object(camera, "Decoder") as decoder:
            controller._run(client, "/usr/bin/ffmpeg", "/dev/video42", Mode())
        decoder.assert_not_called(); client.claim.assert_not_called()
        self.assertEqual(controller.snapshot()["error"], "no loopback")

    def test_disconnect_cleans_decoder_output_and_only_own_lease_without_retry(self):
        controller = camera.Controller()
        client = MagicMock()
        client.stream.return_value.__enter__.return_value.read.side_effect = [jpeg(), EOFError("disconnected")]
        order = []
        output = MagicMock(); output.close.side_effect = lambda: order.append("blank/close")
        decoder = MagicMock(); decoder.close.side_effect = lambda: order.append("decoder/join")
        decoder.feed.side_effect = lambda frame: controller._frame()
        client.release.side_effect = lambda: order.append("release")
        with patch.object(camera, "Loopback", return_value=output), patch.object(camera, "Decoder", return_value=decoder):
            controller._run(client, "/usr/bin/ffmpeg", "/dev/video42", Mode())
        self.assertEqual(order, ["decoder/join", "blank/close", "release"])
        self.assertEqual(controller.snapshot()["frames"], 1)
        self.assertEqual(controller.snapshot()["state"], "error")
        client.claim.assert_called_once()

    def test_stop_before_claim_and_unacknowledged_release_are_visible(self):
        controller = camera.Controller(); controller.stop()
        client = MagicMock(); client.release.side_effect = OSError("offline")
        with patch.object(camera, "Loopback"), patch.object(camera, "Decoder"):
            controller._run(client, "/usr/bin/ffmpeg", "/dev/video42", Mode())
        client.claim.assert_not_called()
        self.assertIn("release not acknowledged", controller.snapshot()["error"])

    def test_decoder_arguments_have_no_network_driver_or_recording_output(self):
        args = camera.decoder_command("/usr/bin/ffmpeg", Mode())
        self.assertEqual(args[-1], "pipe:1")
        self.assertEqual(args[args.index("-protocol_whitelist") + 1], "pipe")
        self.assertNotIn("/dev/video42", args)
        self.assertNotIn("-y", args)


@unittest.skipUnless(shutil.which("ffmpeg"), "optional FFmpeg synthetic decoder check")
class SyntheticDecoderTests(unittest.TestCase):
    def test_camera_negotiation_time_does_not_count_as_decoder_stall(self):
        output = MagicMock()
        decoder = camera.Decoder(shutil.which("ffmpeg"), Mode(), output, threading.Event(), lambda: None)
        try:
            time.sleep(3.2)  # No media yet: an HTTP claim/camera negotiation may still be pending.
            self.assertIsNone(decoder.error)
            output.write.assert_not_called()
        finally:
            decoder.close()

    def test_actual_ffmpeg_decodes_generated_color_without_devices_or_recordings(self):
        exe = shutil.which("ffmpeg")
        mode = Mode(640, 480, 10)
        generated = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                    "color=c=blue:s=640x480:r=10", "-frames:v", "1", "-threads", "1",
                                    "-c:v", "mjpeg", "-f", "image2pipe", "pipe:1"],
                                   check=True, capture_output=True, timeout=15,
                                   env={"PATH": "/usr/bin:/bin", "LANG": "C"})
        frame = generated.stdout
        validate_jpeg(frame, mode)
        received = []
        ready = threading.Event()
        output = SimpleNamespace(write=lambda data: received.append(bytes(data)))
        stop = threading.Event()
        decoder = camera.Decoder(exe, mode, output, stop, ready.set)
        try:
            for _ in range(12):
                decoder.feed(frame)
                time.sleep(.03)
            self.assertTrue(ready.wait(2), decoder.error or "no decoded output")
            self.assertIsNone(decoder.error)
            self.assertTrue(all(len(raw) == 640 * 480 * 2 for raw in received))
            # Source is constant color; output is actual YUYV bytes, not JPEG or placeholder black.
            self.assertNotEqual(received[0][:4], b"\x10\x80\x10\x80")
        finally:
            decoder.close()
        self.assertIsNotNone(decoder.process.poll())
        self.assertFalse(decoder.thread.is_alive())

    def test_http_controller_and_real_decoder_stop_without_leaking_lease_or_child(self):
        # Reuse the localhost fake-capture fixture, not a live hub or video node.
        from tests.hub.test_webcam import HttpCameraTests
        fixture = HttpCameraTests("test_auth_and_lease_required_before_any_capture")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        exe = shutil.which("ffmpeg")
        generated = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                    "color=c=blue:s=640x480:r=10", "-frames:v", "1", "-threads", "1",
                                    "-c:v", "mjpeg", "-f", "image2pipe", "pipe:1"],
                                   check=True, capture_output=True, timeout=15,
                                   env={"PATH": "/usr/bin:/bin", "LANG": "C"})
        frame = generated.stdout
        def source_frame(_):
            time.sleep(.02)
            return frame
        written = []
        ready = threading.Event()
        def write(data):
            written.append(len(data))
            if len(written) >= 3:ready.set()
        output = SimpleNamespace(write=write, close=MagicMock())
        controller = camera.Controller()
        children = []
        original = camera.Decoder
        def make_decoder(*args):
            decoder = original(*args)
            children.append(decoder)
            return decoder
        with patch.object(fixture.registry.camera.capture, "read", source_frame), patch.object(
                camera, "Loopback", return_value=output), patch.object(camera, "Decoder", side_effect=make_decoder), patch.dict(
                os.environ, {"USB_LOOM_CAMERA_OUTPUT": "/dev/video42"}):
            try:
                controller.start(f"http://127.0.0.1:{fixture.httpd.server_port}", "test-only", "pipeline", Mode(640, 480, 10))
                self.assertTrue(ready.wait(4), controller.snapshot())
            finally:
                controller.close()
        self.assertTrue(fixture.closed.wait(2))
        self.assertEqual(controller.snapshot()["state"], "off", controller.snapshot())
        self.assertIsNone(fixture.registry.camera.route())
        self.assertTrue(written and all(size == 640 * 480 * 2 for size in written))
        output.close.assert_called_once()
        for decoder in children:
            self.assertIsNotNone(decoder.process.poll())
            self.assertFalse(decoder.thread.is_alive())
