"""Platform-selection contracts, with every device/network/UI boundary mocked.

These checks do not replace a Windows executable build or hardware smoke test.
"""
from contextlib import ExitStack
import os
import struct
import types
import unittest
from unittest.mock import MagicMock, patch

import claim
import mic_sink
import receiver_app
import trackpad_sink


class PlatformSelectionTests(unittest.TestCase):
    def test_windows_never_selects_linux_native_touchpad(self):
        for backend in (None, "native", "legacy"):
            with self.subTest(backend=backend), patch.dict(os.environ), patch.object(trackpad_sink.sys, "platform", "win32"):
                os.environ.pop("USB_LOOM_TP_BACKEND", None)
                if backend is not None:
                    os.environ["USB_LOOM_TP_BACKEND"] = backend
                self.assertFalse(trackpad_sink.native_backend())

    def test_linux_native_default_and_explicit_legacy_fallback(self):
        for backend, expected in ((None, True), ("native", True), ("legacy", False)):
            with self.subTest(backend=backend), patch.dict(os.environ), patch.object(trackpad_sink.sys, "platform", "linux"):
                os.environ.pop("USB_LOOM_TP_BACKEND", None)
                if backend is not None:
                    os.environ["USB_LOOM_TP_BACKEND"] = backend
                self.assertEqual(trackpad_sink.native_backend(), expected)

    def test_claim_mode_matches_selected_backend_and_preserves_destination(self):
        for platform in ("win32", "linux"):
            for backend in (None, "native", "legacy"):
                with self.subTest(platform=platform, backend=backend), patch.dict(os.environ), patch.object(claim.sys, "platform", platform), patch.object(claim, "request", return_value={}) as request:
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
            with self.subTest(adapter=adapter), patch.dict(os.environ, {"USB_LOOM_TP_BACKEND": "native"}), patch.object(claim, "request", return_value={}) as request:
                dest = f"192.0.2.20:{claim.DEFAULT_PORTS[adapter]}"
                claim._register_and_claim("http://hub.invalid", "test-client", adapter, dest)
                self.assertEqual(request.call_args.args[3], {"client_id": "test-client", "dest": dest})

    def test_windows_window_does_not_initialize_linux_tray_or_start_hidden(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(receiver_app.sys, "platform", "win32"))
            stack.enter_context(patch.object(receiver_app.tk, "Tk"))
            stack.enter_context(patch.object(receiver_app.trackpad_config, "load"))
            for method in ("_build_style", "_build", "_ensure_sinks", "_refresh_devices"):
                stack.enter_context(patch.object(receiver_app.ReceiverApp, method))
            tray = stack.enter_context(patch.object(receiver_app.ReceiverApp, "_setup_tray"))
            app = receiver_app.ReceiverApp("http://hub.invalid", "192.0.2.20", "test-client", start_hidden=True)
        tray.assert_not_called()
        self.assertFalse(app._hidden)
        app.root.withdraw.assert_not_called()
        app.root.protocol.assert_not_called()


class AudioPlatformTests(unittest.TestCase):
    def run_windows_audio(self, fallback=False):
        pcm = b"\x01\x00" * 441
        packet = struct.pack("<IIIBBH", mic_sink.MAGIC, 1, 44100, 1, 16, len(pcm)) + pcm
        sock = MagicMock()
        sock.recvfrom.side_effect = [(packet, ("192.0.2.10", 1)), KeyboardInterrupt()]
        wasapi = MagicMock()
        wasapi.name = "CABLE Input (test)"
        constructor = MagicMock(return_value=wasapi)
        if fallback:
            constructor.side_effect = OSError("mock WASAPI unavailable")
        with ExitStack() as stack:
            stack.enter_context(patch.object(mic_sink.sys, "platform", "win32"))
            stack.enter_context(patch.object(mic_sink.socket, "socket", return_value=sock))
            stack.enter_context(patch.object(mic_sink, "wasapi_out", types.SimpleNamespace(WasapiOut=constructor), create=True))
            stack.enter_context(patch.object(mic_sink, "pick_device", return_value=7))
            stack.enter_context(patch.object(mic_sink, "list_wave_devices", return_value=[(7, "CABLE Input (test)")]))
            stack.enter_context(patch.object(mic_sink, "ProbeWriter"))
            stack.enter_context(patch("builtins.print"))
            stack.enter_context(patch.dict(mic_sink.STATS))
            stack.enter_context(patch.dict(mic_sink.MONITOR))
            linux = stack.enter_context(patch.object(mic_sink, "_serve_linux"))
            pulse = stack.enter_context(patch.object(mic_sink, "PulsePaplay"))
            waveout = stack.enter_context(patch.object(mic_sink, "WaveOut"))
            mic_sink.serve(0, "test-cable")
        linux.assert_not_called()
        pulse.assert_not_called()
        constructor.assert_called_once_with(mic_sink.PREFERRED)
        return wasapi, waveout

    def test_windows_keeps_wasapi_native_rate_injection(self):
        wasapi, waveout = self.run_windows_audio()
        wasapi.write_s16_mono.assert_called_once()
        self.assertEqual(wasapi.write_s16_mono.call_args.args[1], 44100)
        waveout.assert_not_called()

    def test_windows_keeps_waveout_fallback(self):
        wasapi, waveout = self.run_windows_audio(fallback=True)
        wasapi.write_s16_mono.assert_not_called()
        waveout.assert_called_once_with(7, mic_sink.INJECT_RATE, 2)
        waveout.return_value.write.assert_called_once()
        waveout.return_value.close.assert_called_once()

    def test_linux_dispatches_only_to_pipewire_path(self):
        with patch.object(mic_sink.sys, "platform", "linux"), patch.object(mic_sink, "_serve_linux") as linux, patch.object(mic_sink, "WaveOut") as waveout:
            mic_sink.serve(0, "test-mic")
        linux.assert_called_once_with(0, "test-mic", False)
        waveout.assert_not_called()


if __name__ == "__main__":
    unittest.main()
