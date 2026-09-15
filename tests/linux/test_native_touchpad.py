"""Linux input lifecycle with fake uinput and sockets."""
from dataclasses import replace
import unittest
from unittest.mock import MagicMock, patch
from proto import tp10, tp_native as wire
from client.common.stream_guard import StreamGuard
from client import trackpad_sink
from tests.fixtures import descriptor_data, contact, frame, FakeInput

from client.linux import native_touchpad as native

class NativeInputTests(unittest.TestCase):
    def setUp(self):
        self.devices = []
        def factory(*args, **kwargs):
            dev = FakeInput(*args, **kwargs)
            self.devices.append(dev)
            return dev
        self.pad = native.NativeTouchpad(native.Descriptor(descriptor_data()), ui_factory=factory)
        self.ui = self.devices[0]
        self.addCleanup(self.pad.close)

    def test_is_touchpad_not_mouse_or_keyboard(self):
        self.assertNotIn(2, self.ui.caps)  # No EV_REL.
        self.assertEqual(self.ui.options['input_props'], [0, 2])
        self.assertEqual(self.ui.options['vendor'], 0x05AC)
        self.assertTrue(all(key >= 256 for key in self.ui.caps[1]))
        axes = dict(self.ui.caps[3])
        self.assertEqual(axes[native.X][-1], 47)
        self.assertEqual(axes[native.Y][-1], 44)
        self.assertEqual(axes[native.MAJOR][2], 1020)

    def test_one_unit_positions_are_not_rounded_or_scaled(self):
        for x in range(100):
            self.assertTrue(self.pad.feed(frame(contact(x=x))))
        xs = [value for kind, code, value in self.ui.events if kind == 3 and code == native.X]
        self.assertEqual(xs, list(range(100)))

    def test_unchanged_heartbeat_writes_nothing(self):
        self.pad.feed(frame(contact()))
        self.ui.events.clear()
        self.pad.feed(frame(contact(), seq=2))
        self.assertEqual(self.ui.events, [])

    def test_reordering_contacts_keeps_slot_identity(self):
        a, b = contact(100), contact(200, x=100, slot=4)
        self.pad.feed(frame(a, b))
        slots = self.pad.slots.copy()
        self.ui.events.clear()
        self.pad.feed(frame(b, a))
        self.assertEqual(self.pad.slots, slots)
        self.assertEqual(self.ui.events, [])

    def test_replacement_releases_old_tracking_id(self):
        self.pad.feed(frame(contact(10)))
        self.ui.events.clear()
        self.pad.feed(frame(contact(20, x=3000)))
        ids = [value for kind, code, value in self.ui.events if kind == 3 and code == native.TRACKING]
        self.assertEqual(ids, [-1, 20])
        self.assertEqual(self.pad.slots, {20: 0})

    def test_five_fingers_then_full_release(self):
        self.pad.feed(frame(*(contact(i, x=i * 100, slot=i) for i in range(5)), buttons=1))
        self.assertIn((1, native.TOOL_KEYS[4], 1), self.ui.events)
        self.ui.events.clear()
        self.pad.feed(frame())
        self.assertEqual(sum(e == (3, native.TRACKING, -1) for e in self.ui.events), 5)
        self.assertIn((1, native.BTN_TOUCH, 0), self.ui.events)
        self.assertIn((1, native.BTN_LEFT, 0), self.ui.events)
        self.assertFalse(self.pad.active)

    def test_invalid_coordinates_do_not_inject_or_change_state(self):
        self.pad.feed(frame(contact()))
        previous = self.pad.state
        self.ui.events.clear()
        self.assertFalse(self.pad.feed(frame(contact(x=32000))))
        self.assertEqual(self.ui.events, [])
        self.assertEqual(self.pad.state, previous)

    def test_cancel_removes_device_not_a_synthetic_tap(self):
        self.pad.feed(frame(contact(), buttons=1))
        self.ui.events.clear()
        self.pad.cancel()
        self.assertTrue(self.ui.closed)
        self.assertEqual(self.ui.events, [])
        self.pad.feed(frame(contact(50)))
        self.assertEqual(len(self.devices), 2)
        self.assertEqual(self.pad.slots, {50: 0})

    def test_invalid_descriptor_fails_before_uinput(self):
        data = descriptor_data()
        data['axes'][str(native.X)]['resolution'] = 0
        with self.assertRaises(ValueError): native.Descriptor(data)

class NativeServeTests(unittest.TestCase):
    def test_watchdog_cancels_and_reconnect_discards_old_input(self):
        clock = [1.0]
        devices = []
        real_pad = native.NativeTouchpad
        def factory(*args, **kwargs):
            dev = FakeInput(*args, **kwargs); devices.append(dev); return dev
        def make_pad(desc):
            return real_pad(desc, ui_factory=factory)
        sock = MagicMock(); sock.__enter__.return_value = sock
        step = [0]
        def receive(_size):
            step[0] += 1
            if step[0] == 1:
                return wire.encode(frame(contact(), buttons=1)), ('127.0.0.1', 1)
            if step[0] == 2:
                clock[0] = 2.0
                raise native.socket.timeout()
            if step[0] in (3, 4):
                clock[0] += .01
                return wire.encode(frame(contact(99), seq=step[0])), ('127.0.0.1', 1)
            raise KeyboardInterrupt()
        sock.recvfrom.side_effect = receive
        stats = {}
        with patch.dict(native.os.environ, {'USB_LOOM_HUB': 'http://127.0.0.1:27180'}), patch('client.common.claims.request', return_value=descriptor_data()), patch.object(native.socket, 'getaddrinfo', return_value=[(2, 2, 0, '', ('127.0.0.1', 0))]), patch.object(native.socket, 'socket', return_value=sock), patch.object(native, 'NativeTouchpad', side_effect=make_pad), patch.object(native, 'discard_pending', return_value=0), patch.object(native.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(KeyboardInterrupt): native.serve(0, stats)
        self.assertEqual(len(devices), 2)
        self.assertTrue(all(d.closed for d in devices))
        self.assertIn((1, native.BTN_LEFT, 1), devices[0].events)
        # Device removal cancels the held press; no synthetic release/tap frame.
        self.assertNotIn((1, native.BTN_LEFT, 0), devices[0].events)
        self.assertEqual(stats['tp10_packets'], 2)
        self.assertEqual(stats['tp10_startup_discarded'], 1)
        self.assertFalse(stats['listening'])

    def test_startup_drain_is_bounded(self):
        sock = MagicMock()
        sock.recvfrom.return_value = (b'old', ('127.0.0.1', 1))
        self.assertEqual(native.discard_pending(sock), 512)
        sock.settimeout.assert_called_with(.1)
