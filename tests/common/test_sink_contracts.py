"""No live audio/gamepad output: changed-state and native-rate regression tests."""
from enum import IntFlag
import json
from pathlib import Path
import struct
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from client.common import audio as mic_sink
from client.common import trackpad_config
from client.common import gamepad as xbox_sink


# Pure XInput ABI constants. Importing vgamepad on Windows connects to the real
# ViGEm bus at module import time; unit tests must not open a driver/device.
class XUSB_BUTTON(IntFlag):
    XUSB_GAMEPAD_DPAD_UP = 0x0001
    XUSB_GAMEPAD_DPAD_DOWN = 0x0002
    XUSB_GAMEPAD_DPAD_LEFT = 0x0004
    XUSB_GAMEPAD_DPAD_RIGHT = 0x0008
    XUSB_GAMEPAD_START = 0x0010
    XUSB_GAMEPAD_BACK = 0x0020
    XUSB_GAMEPAD_LEFT_THUMB = 0x0040
    XUSB_GAMEPAD_RIGHT_THUMB = 0x0080
    XUSB_GAMEPAD_LEFT_SHOULDER = 0x0100
    XUSB_GAMEPAD_RIGHT_SHOULDER = 0x0200
    XUSB_GAMEPAD_GUIDE = 0x0400
    XUSB_GAMEPAD_A = 0x1000
    XUSB_GAMEPAD_B = 0x2000
    XUSB_GAMEPAD_X = 0x4000
    XUSB_GAMEPAD_Y = 0x8000


class GamepadTests(unittest.TestCase):
    def setUp(self):
        self.pad = MagicMock()
        self.writer = xbox_sink.GamepadWriter(self.pad, types.SimpleNamespace(XUSB_BUTTON=XUSB_BUTTON))
        self.idle = (32768, 32768, 32768, 32768, 0, 65535)

    def test_repeated_unchanged_reports_do_one_update(self):
        for _ in range(1000): self.writer.apply(self.idle)
        self.pad.update.assert_called_once()

    def test_button_edges_both_reach_device(self):
        down = (*self.idle[:4], 1, 65535)
        self.writer.apply(down); self.writer.apply(self.idle)
        self.pad.press_button.assert_any_call(XUSB_BUTTON.XUSB_GAMEPAD_A)
        self.pad.release_button.assert_any_call(XUSB_BUTTON.XUSB_GAMEPAD_A)
        self.assertEqual(self.pad.update.call_count, 2)

    def test_watchdog_neutralizes_once_then_accepts_same_state(self):
        self.writer.apply(self.idle)
        for _ in range(10): self.writer.neutral()
        self.pad.reset.assert_called_once()
        self.assertTrue(self.writer.apply(self.idle))
        self.assertEqual(self.pad.update.call_count, 3)

    def test_axes_triggers_and_diagonal_hat_preserved(self):
        self.writer.apply((65535, 0, 0, 65535, (17 << 16) | (255 << 24), 4500))
        self.pad.left_joystick.assert_called_once_with(32767, 32767)
        self.pad.right_joystick.assert_called_once_with(-32768, -32767)
        self.pad.left_trigger.assert_called_once_with(17)
        self.pad.right_trigger.assert_called_once_with(255)
        self.pad.press_button.assert_any_call(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
        self.pad.press_button.assert_any_call(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)


    def test_both_sticks_follow_explicit_polarity_at_endpoints_and_center(self):
        for invert in (False, True):
            for raw in (0, 16384, 32768, 49152, 65535):
                with self.subTest(invert=invert, raw=raw):
                    pad = MagicMock()
                    writer = xbox_sink.GamepadWriter(pad, types.SimpleNamespace(XUSB_BUTTON=XUSB_BUTTON), invert_y=invert)
                    writer.apply((raw, raw, raw, raw, 0, 65535))
                    x = raw - 32768
                    y = max(-32768, min(32767, -x)) if invert else x
                    pad.left_joystick.assert_called_once_with(x, y)
                    pad.right_joystick.assert_called_once_with(x, y)

    def test_windows_policy_retains_legacy_xinput_values(self):
        from client.windows import gamepad as windows
        self.assertIs(windows.INVERT_Y, True)
        explicit_pad = MagicMock()
        explicit = xbox_sink.GamepadWriter(explicit_pad, types.SimpleNamespace(XUSB_BUTTON=XUSB_BUTTON), invert_y=windows.INVERT_Y)
        for state in ((0, 0, 65535, 65535, 0, 0), self.idle, (65535, 65535, 0, 0, 255 << 24, 18000)):
            self.writer.apply(state); explicit.apply(state)
        self.assertEqual(self.pad.mock_calls, explicit_pad.mock_calls)

    def test_serve_delivers_axis_policy_to_writer_and_still_neutralizes(self):
        packet = xbox_sink.HEADER.pack(xbox_sink.MAGIC, 1, 32768, 0, 32768, 65535, 0, 65535)
        for invert in (False, True):
            with self.subTest(invert=invert), patch.object(xbox_sink.socket, "socket") as socket_factory, patch.dict(xbox_sink.STATS):
                pad = MagicMock(); sock = socket_factory.return_value
                sock.recvfrom.side_effect = [(packet, ("192.0.2.1", 27185)), KeyboardInterrupt]
                xbox_sink.serve(0, lambda: (pad, types.SimpleNamespace(XUSB_BUTTON=XUSB_BUTTON)), invert_y=invert)
                pad.left_joystick.assert_called_once_with(0, 32767 if invert else -32768)
                pad.right_joystick.assert_called_once_with(0, -32767 if invert else 32767)
                pad.reset.assert_called_once(); sock.close.assert_called_once()
                self.assertFalse(xbox_sink.STATS["listening"])
                self.assertEqual(xbox_sink.STATS["error"], "")

    def test_invalid_axis_policy_fails_before_creating_device(self):
        for invalid in (None, "false", 0, 1):
            with self.subTest(value=invalid), patch.dict(xbox_sink.STATS):
                factory = MagicMock()
                xbox_sink.serve(0, factory, invert_y=invalid)
                factory.assert_not_called()
                self.assertFalse(xbox_sink.STATS["listening"])
                self.assertIn("must be a boolean", xbox_sink.STATS["error"])
                with self.assertRaises(ValueError):
                    xbox_sink.GamepadWriter(self.pad, types.SimpleNamespace(XUSB_BUTTON=XUSB_BUTTON), invert_y=invalid)


class AudioTests(unittest.TestCase):
    def packet(self, seq=1, rate=44100, channels=1, pcm=b'\0\0' * 441):
        return struct.pack('<IIIBBH', mic_sink.MAGIC, seq, rate, channels, 16, len(pcm)) + pcm

    def test_rejects_invalid_audio_formats(self):
        for packet in (self.packet(rate=0), self.packet(channels=0), self.packet(pcm=b'1'), self.packet()[:-1]):
            self.assertIsNone(mic_sink.decode_au10(packet))

    def test_agc_reuses_existing_level_sample(self):
        with patch.object(mic_sink, 'pcm_levels', side_effect=AssertionError('duplicate level scan')):
            mic_sink.Agc().apply(b'\x01\0' * 100, (1.0, 1.0))



class ConfigSafetyTests(unittest.TestCase):
    def test_bad_values_do_not_crash_loading(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            path.write_text(json.dumps({'tracking_speed': 'bad', 'scroll_speed': None,
                                        'flick_force': [], 'invert_x': 'false'}))
            cfg = trackpad_config.load(path)
            self.assertEqual(cfg.tracking_speed, 5)
            self.assertEqual(cfg.scroll_speed, 5)
            self.assertFalse(cfg.invert_x)

    def test_atomic_save_keeps_previous_file_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            path.write_text('{"tracking_speed": 4}')
            with patch.object(trackpad_config.os, 'replace', side_effect=OSError('test failure')):
                with self.assertRaises(OSError): trackpad_config.save(trackpad_config.TrackpadConfig(), path)
            self.assertEqual(json.loads(path.read_text()), {'tracking_speed': 4})
            self.assertEqual(list(Path(temp).iterdir()), [path])


if __name__ == '__main__': unittest.main()
