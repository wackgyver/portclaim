"""Hub capture tests use fake evdev data; no physical devices or network claims."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hub'))
import hid
import trackpad


class CaptureTests(unittest.TestCase):
    def reader(self):
        d = hid.EvdevDevice.__new__(hid.EvdevDevice)
        d.fd = -1
        d.abs = {}
        d.key_down = set()
        d.mt_slots = {}
        d.mt_slot = 0
        d.rel_x = d.rel_y = d.rel_wheel = d.rel_hwheel = 0
        d._dropped = False
        d._monotonic_events = True
        return d

    def event(self, kind, code, value):
        return struct.pack(hid.EVENT_FORMAT, 1, 100, kind, code, value)

    def test_preserves_down_and_up_in_same_read(self):
        d = self.reader()
        data = b''.join(self.event(*e) for e in [(1, hid.BTN_LEFT, 1), (0, 0, 0), (1, hid.BTN_LEFT, 0), (0, 0, 0)])
        frames = []
        with patch.object(hid.os, 'read', side_effect=[data, BlockingIOError()]):
            d.pump(lambda dev, stamp, reset: frames.append((bool(dev.key_down), stamp, reset)))
        self.assertEqual(frames, [(True, 1000100, False), (False, 1000100, False)])

    def test_never_emits_partial_report(self):
        d = self.reader()
        frames = []
        with patch.object(hid.os, 'read', side_effect=[self.event(1, hid.BTN_LEFT, 1), BlockingIOError()]):
            d.pump(lambda *args: frames.append(args))
        self.assertEqual(frames, [])
        with patch.object(hid.os, 'read', side_effect=[self.event(0, 0, 0), BlockingIOError()]):
            d.pump(lambda dev, stamp, reset: frames.append(bool(dev.key_down)))
        self.assertEqual(frames, [True])

    def test_syn_dropped_ignores_deltas_until_resync(self):
        d = self.reader()
        d.sync_state = MagicMock(side_effect=d.key_down.clear)
        data = b''.join(self.event(*e) for e in [(0, 3, 0), (1, hid.BTN_LEFT, 1), (0, 0, 0)])
        frames = []
        with patch.object(hid.os, 'read', side_effect=[data, BlockingIOError()]):
            d.pump(lambda dev, stamp, reset: frames.append((bool(dev.key_down), reset)))
        d.sync_state.assert_called_once()
        self.assertEqual(frames, [(False, True)])

    def test_eof_is_disconnect_not_busy_loop(self):
        d = self.reader()
        with patch.object(hid.os, 'read', return_value=b''):
            with self.assertRaises(OSError): d.pump()

    def test_sync_state_rebuilds_slots_and_keys(self):
        d = self.reader()
        d.abs = {code: hid.AbsAxis(code, 0, 0, high) for code, high in
                 [(hid.ABS_MT_SLOT, 1), (hid.ABS_MT_TRACKING_ID, 65535),
                  (hid.ABS_MT_POSITION_X, 1000), (hid.ABS_MT_POSITION_Y, 1000)]}
        def ioctl_in(request, size):
            if size != 24:
                keys = bytearray(size); keys[hid.BTN_LEFT // 8] |= 1 << (hid.BTN_LEFT % 8)
                return bytes(keys)
            return struct.pack('iiiiii', 0, 0, 1000, 0, 0, 0)
        def slots_ioctl(fd, request, buf, mutate):
            values = {hid.ABS_MT_TRACKING_ID: [-1, 60000],
                      hid.ABS_MT_POSITION_X: [0, 123], hid.ABS_MT_POSITION_Y: [0, 456]}
            buf[1], buf[2] = values[buf[0]]
        d._ioctl_in = ioctl_in
        with patch.object(hid, 'fcntl', SimpleNamespace(ioctl=slots_ioctl)): d.sync_state()
        self.assertEqual(d.key_down, {hid.BTN_LEFT})
        self.assertEqual(d.mt_slots[1][hid.ABS_MT_TRACKING_ID], 60000)
        self.assertEqual(d.mt_slots[1][hid.ABS_MT_POSITION_X], 123)
        self.assertNotIn(0, d.mt_slots)

    def test_late_first_interface_gets_full_sibling_wait(self):
        clock = [0.0]
        made = []
        def sleep(delay): clock[0] += delay
        def scan():
            count = 0 if clock[0] < 2.8 else 1 if clock[0] < 3.6 else 2
            result = []
            for _ in range(count):
                d = MagicMock(); d.id.bustype = 3; result.append(d); made.append(d)
            return result
        with patch.object(trackpad.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(trackpad.time, 'sleep', side_effect=sleep), patch.object(trackpad, 'open_trackpads', side_effect=scan):
            result = trackpad.wait_for_trackpads(ready=3)
        self.assertEqual(len(result), 2)
        self.assertGreaterEqual(clock[0], 3.6)
        for d in made[:-2]: d.close.assert_called_once()

    def test_incomplete_usb_pair_fails_closed(self):
        clock = [0.0]
        d = MagicMock(); d.id.bustype = 3
        with patch.object(trackpad.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(trackpad.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), patch.object(trackpad, 'open_trackpads', return_value=[d]):
            self.assertEqual(trackpad.wait_for_trackpads(ready=.4), [])
        d.grab.assert_not_called()

    def test_native_snapshot_preserves_full_contact_fields(self):
        d = self.reader()
        d.mt_slots = {0: {hid.ABS_MT_TRACKING_ID: 60000, hid.ABS_MT_POSITION_X: 10,
                         hid.ABS_MT_POSITION_Y: 20, hid.ABS_MT_TOUCH_MAJOR: 950,
                         hid.ABS_MT_TOUCH_MINOR: 600, hid.ABS_MT_ORIENTATION: -3}}
        c, = trackpad.native_contacts([d])
        self.assertEqual((c.tracking_id, c.major, c.minor, c.orientation), (60000, 950, 600, -3))


class DescriptorApiTests(unittest.TestCase):
    def test_native_flag_is_explicit_and_boolean(self):
        import server
        registry = server.Registry()
        legacy = registry.claim('magictrackpad', {'dest': '127.0.0.1:27184'})
        self.assertFalse(legacy['native_touchpad'])
        native = registry.claim('magictrackpad', {'dest': '127.0.0.1:27184', 'native_touchpad': True})
        self.assertTrue(native['native_touchpad'])
        with self.assertRaises(ValueError):
            registry.claim('magictrackpad', {'dest': '127.0.0.1:27184', 'native_touchpad': 'true'})

    def test_descriptor_endpoint_is_authenticated(self):
        import server
        handler = server.Handler.__new__(server.Handler)
        handler._auth = lambda: False
        handler._json = MagicMock()
        handler.path = '/v1/devices/magictrackpad/descriptor'
        with patch.object(server.trackpad_hub, 'descriptor') as desc:
            handler.do_GET()
        desc.assert_not_called()
        handler._json.assert_called_once_with(401, {'error': 'token'})

    def test_descriptor_unavailable_returns_service_unavailable(self):
        import server
        handler = server.Handler.__new__(server.Handler)
        handler._auth = lambda: True
        handler._json = MagicMock()
        handler.path = '/v1/devices/magictrackpad/descriptor'
        with patch.object(server.trackpad_hub, 'descriptor', side_effect=OSError('no device')):
            handler.do_GET()
        handler._json.assert_called_once_with(503, {'error': 'no device'})


if __name__ == '__main__': unittest.main()
