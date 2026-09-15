"""OS selection and claim flags remain application-edge decisions."""
import os
import unittest
from unittest.mock import patch
from client import claim, trackpad_sink, platforms
from client.common import claims


class PlatformSelectionTests(unittest.TestCase):
    def test_windows_never_selects_linux_native_touchpad(self):
        for backend in (None, "native", "legacy"):
            with self.subTest(backend=backend), patch.dict(os.environ), patch.object(platforms.sys, "platform", "win32"):
                os.environ.pop("USB_LOOM_TP_BACKEND", None)
                if backend is not None:
                    os.environ["USB_LOOM_TP_BACKEND"] = backend
                self.assertFalse(trackpad_sink.native_backend())

    def test_linux_native_default_and_explicit_legacy_fallback(self):
        for backend, expected in ((None, True), ("native", True), ("legacy", False)):
            with self.subTest(backend=backend), patch.dict(os.environ), patch.object(platforms.sys, "platform", "linux"):
                os.environ.pop("USB_LOOM_TP_BACKEND", None)
                if backend is not None:
                    os.environ["USB_LOOM_TP_BACKEND"] = backend
                self.assertEqual(trackpad_sink.native_backend(), expected)

    def test_claim_mode_matches_selected_backend_and_preserves_destination(self):
        for platform in ("win32", "linux"):
            for backend in (None, "native", "legacy"):
                with self.subTest(platform=platform, backend=backend), patch.dict(os.environ), patch.object(claim.sys, "platform", platform), patch.object(claims, "request", return_value={}) as request:
                    os.environ.pop("USB_LOOM_TP_BACKEND", None)
                    if backend is not None:
                        os.environ["USB_LOOM_TP_BACKEND"] = backend
                    expected = trackpad_sink.native_backend()
                    claim._register_and_claim("http://hub.invalid", "test-client", "magictrackpad", "192.0.2.20:27184")
                    self.assertEqual(request.call_count, 2)
                    request.assert_called_with(
                        "http://hub.invalid", "POST", "/v1/devices/magictrackpad/claim",
                        {"client_id": "test-client", "dest": "192.0.2.20:27184", "native_touchpad": expected},
                    )

    def test_native_claim_option_does_not_leak_to_other_devices(self):
        for adapter in ("mic", "xboxelite", "ta320"):
            with self.subTest(adapter=adapter), patch.dict(os.environ, {"USB_LOOM_TP_BACKEND": "native"}), patch.object(claims, "request", return_value={}) as request:
                dest = f"192.0.2.20:{claim.DEFAULT_PORTS[adapter]}"
                claim._register_and_claim("http://hub.invalid", "test-client", adapter, dest)
                self.assertEqual(request.call_args.args[3], {"client_id": "test-client", "dest": dest})
