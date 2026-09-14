"""No real uinput in unit tests: verify the protocol and exact Linux event contract."""
from dataclasses import replace
import struct
import unittest
from unittest.mock import MagicMock, patch

import native_touchpad as native
import trackpad_sink
import tp10
import tp_native as wire
from stream_guard import StreamGuard


def descriptor_data():
    geometry = {native.X: (-3678, 3934, 47), native.Y: (-2478, 2587, 44),
                native.PRESSURE: (0, 253, 0), native.MAJOR: (0, 1020, 0),
                native.MINOR: (0, 1020, 0), native.ORIENTATION: (-3, 4, 0)}
    return {"schema": 1, "transport": "TP10/N1", "vendor": 0x05AC, "product": 0x0265,
            "bustype": 3, "version": 1, "max_contacts": 5,
            "axes": {str(k): dict(zip(("minimum", "maximum", "resolution"), v)) for k, v in geometry.items()}}


def contact(tid=1, x=0, y=0, slot=0):
    return wire.Contact(slot, tid, x, y, 60, 80, 60, 0)


def frame(*contacts, seq=1, buttons=0, epoch=123):
    return wire.Frame(seq, buttons, tuple(contacts), epoch, 1000000)


class FakeInput:
    def __init__(self, caps, **kwargs):
        self.caps, self.options = caps, kwargs
        self.events = []
        self.closed = False
    def write(self, *event):
        self.events.append(event)
    def syn(self):
        self.events.append((0, 0, 0))
    def close(self):
        self.closed = True


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


class NativeProtocolTests(unittest.TestCase):
    def test_full_identity_size_orientation_roundtrip(self):
        c = replace(contact(60000), major=950, minor=600, orientation=-3)
        original = frame(c, contact(60001), seq=0xFFFFFFFF)
        packet = wire.encode(original)
        self.assertEqual(len(packet), 146)
        self.assertEqual(wire.decode(packet), original)
        legacy = tp10.decode_tp10(packet)
        self.assertIsNotNone(legacy)
        # Check the actual Windows/legacy receiver decoder, not only the codec.
        self.assertEqual(trackpad_sink.decode_tp10(packet), legacy)
        self.assertEqual(legacy[2][0][5], 255)
        self.assertEqual(legacy[2][0][1], 32767)

    def test_rel_trailer_preserved_for_legacy_receiver(self):
        original = replace(frame(contact()), rel=(1, -2, 3, 4))
        packet = wire.encode(original)
        self.assertEqual(wire.decode(packet), original)
        self.assertEqual(tp10.decode_tp10(packet)[3], original.rel)
        self.assertEqual(trackpad_sink.decode_tp10(packet)[3], original.rel)

    def test_malformed_or_legacy_packets_rejected_by_native(self):
        packet = wire.encode(frame(contact()))
        for length in (0, 12, 79, 80, 145):
            self.assertIsNone(wire.decode(packet[:length]))
        self.assertIsNone(wire.decode(packet + b'\0'))
        bad = bytearray(packet); bad[80:84] = b'BAD!'
        self.assertIsNone(wire.decode(bytes(bad)))
        bad = bytearray(packet); bad[9] = 6
        self.assertIsNone(wire.decode(bytes(bad)))

    def test_duplicate_full_tracking_ids_rejected(self):
        self.assertIsNone(wire.decode(wire.encode(frame(contact(5), contact(5, slot=1)))))


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
        with patch.dict(native.os.environ, {'USB_LOOM_HUB': 'http://127.0.0.1:27180'}), patch('claim.request', return_value=descriptor_data()), patch.object(native.socket, 'getaddrinfo', return_value=[(2, 2, 0, '', ('127.0.0.1', 0))]), patch.object(native.socket, 'socket', return_value=sock), patch.object(native, 'NativeTouchpad', side_effect=make_pad), patch.object(native, 'discard_pending', return_value=0), patch.object(native.time, 'monotonic', side_effect=lambda: clock[0]):
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


class StreamGuardTests(unittest.TestCase):
    def test_wrap_duplicate_reordering_and_loss(self):
        g = StreamGuard()
        self.assertTrue(g.accept(0xFFFFFFFF, 1))
        self.assertTrue(g.accept(0, 1.01))
        self.assertFalse(g.accept(0, 1.02))
        self.assertFalse(g.accept(0xFFFFFFFF, 1.03))
        self.assertTrue(g.accept(3, 1.04))
        self.assertEqual(g.missing, 2)
        self.assertEqual(g.rejected, 2)

    def test_restart_epoch_rejects_old_stream(self):
        g = StreamGuard()
        g.accept(100, 1, 10)
        self.assertTrue(g.accept(1, 1.01, 20))
        self.assertTrue(g.resync)
        self.assertFalse(g.accept(101, 1.02, 10))

    def test_timeout_allows_counter_restart_and_requires_resync(self):
        g = StreamGuard()
        g.accept(100, 1)
        self.assertTrue(g.expired(1.6))
        self.assertTrue(g.accept(1, 1.6))
        self.assertTrue(g.resync)

    def test_duplicates_cannot_keep_watchdog_alive(self):
        g = StreamGuard()
        g.accept(100, 1)
        self.assertFalse(g.accept(100, 1.4))
        self.assertTrue(g.expired(1.5))


if __name__ == '__main__':
    unittest.main()
