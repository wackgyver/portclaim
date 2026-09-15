"""Mocked Windows audio contracts; never open a sound device or socket."""
from contextlib import ExitStack
import struct
import types
import unittest
from unittest.mock import MagicMock, patch
from client.windows import audio as mic_sink


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
            waveout = stack.enter_context(patch.object(mic_sink, "WaveOut"))
            mic_sink.serve(0, "test-cable")
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
