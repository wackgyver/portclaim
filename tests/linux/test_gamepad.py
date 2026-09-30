"""Actual Linux vgamepad report code; all events stay in RAM, never in uinput.

CI also runs this file after installing the pinned runtime dependencies, so a
bare test interpreter's optional skips cannot hide a Linux polarity regression.
"""
import importlib.util
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from client.common.gamepad import GamepadWriter, decode_sb10
from client.linux import gamepad as backend
from hub import hid

HAS_RUNTIME = (sys.platform.startswith("linux")
               and importlib.util.find_spec("libevdev") is not None
               and importlib.util.find_spec("vgamepad") is not None)


@unittest.skipUnless(HAS_RUNTIME, "Linux vgamepad/libevdev runtime dependencies required")
class LinuxGamepadEventsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import libevdev
        cls.ev = libevdev
        # Guard even the dependency import: no test may create a real device.
        guard = patch.object(libevdev.Device, "create_uinput_device",
                             side_effect=AssertionError("real uinput creation forbidden in tests"))
        guard.start(); cls.addClassCleanup(guard.stop)
        import vgamepad
        cls.vg = vgamepad

    def setUp(self):
        self.events = []
        # Skip the constructor which allocates a real uinput device, but retain
        # the installed library's report setters, reset and update methods.
        self.pad = self.vg.VX360Gamepad.__new__(self.vg.VX360Gamepad)
        self.pad.report = self.pad.get_default_report()
        self.pad.uinput = SimpleNamespace(send_events=self.events.extend)
        self.writer = GamepadWriter(self.pad, self.vg, invert_y=backend.INVERT_Y)
        self.sequence = 0

    def forward(self, axes=(0, 0, 0, 0), *, keys=(), hat=(0, 0), triggers=(0, 0)):
        codes = (hid.ABS_X, hid.ABS_Y, hid.ABS_RX, hid.ABS_RY)
        absolute = {code: hid.AbsAxis(code, value, -32768, 32767) for code, value in zip(codes, axes)}
        absolute.update({code: hid.AbsAxis(code, value, 0, 255)
                         for code, value in zip((hid.ABS_Z, hid.ABS_RZ), triggers)})
        device = SimpleNamespace(abs=absolute, key_down=set(keys), hat_x=hat[0], hat_y=hat[1])
        self.sequence += 1
        state = hid.XboxEliteAdapter().to_state(device)
        decoded = decode_sb10(hid.encode_sb10(self.sequence, state))
        return self.writer.apply(decoded[1:])

    def values(self, event_type):
        return {e.code.name: e.value for e in self.events if e.type == event_type}

    def test_linux_policy_is_native_evdev(self):
        self.assertIs(backend.INVERT_Y, False)

    def test_hub_wire_and_library_preserve_all_stick_axes(self):
        names = ("ABS_X", "ABS_Y", "ABS_RX", "ABS_RY")
        for axis in range(4):
            for value in (-32768, -16384, 0, 16384, 32767):
                axes = [0, 0, 0, 0]; axes[axis] = value
                with self.subTest(axis=names[axis], value=value):
                    self.forward(axes)
                    values = self.values(self.ev.EV_ABS)
                    self.assertEqual([values[name] for name in names], axes)

    def test_buttons_triggers_and_dpad_do_not_change(self):
        self.forward(keys=(hid.BTN_A, hid.BTN_TL), triggers=(17, 255), hat=(1, -1))
        absolute = self.values(self.ev.EV_ABS); keys = self.values(self.ev.EV_KEY)
        self.assertEqual((absolute["ABS_Z"], absolute["ABS_RZ"]), (68, 1020))
        self.assertEqual((absolute["ABS_HAT0X"], absolute["ABS_HAT0Y"]), (1, -1))
        self.assertEqual(keys["BTN_SOUTH"], 1)
        self.assertEqual(keys["BTN_TL"], 1)
        self.assertEqual(keys["BTN_EAST"], 0)
        self.forward()
        self.assertTrue(all(value == 0 for value in self.values(self.ev.EV_ABS).values()))
        self.assertTrue(all(value == 0 for value in self.values(self.ev.EV_KEY).values()))

    def test_unchanged_input_is_suppressed_and_neutral_is_emitted_once(self):
        self.forward((1234, -32768, -1234, 32767), keys=(hid.BTN_B,))
        count = len(self.events)
        self.assertFalse(self.forward((1234, -32768, -1234, 32767), keys=(hid.BTN_B,)))
        self.assertEqual(len(self.events), count)
        self.writer.neutral()
        self.assertTrue(all(value == 0 for value in self.values(self.ev.EV_ABS).values()))
        self.assertTrue(all(value == 0 for value in self.values(self.ev.EV_KEY).values()))
        count = len(self.events)
        self.writer.neutral(); self.assertEqual(len(self.events), count)
        self.assertTrue(self.forward((1234, -32768, -1234, 32767), keys=(hid.BTN_B,)))

    def test_factory_stays_lazy_and_returns_the_selected_library(self):
        with patch.object(self.vg, "VX360Gamepad", return_value=self.pad) as create:
            pad, vg = backend.create_pad()
        create.assert_called_once_with()
        self.assertIs(pad, self.pad); self.assertIs(vg, self.vg)
