"""Shared native/legacy wire and freshness compatibility."""
from dataclasses import replace
import unittest
from unittest.mock import MagicMock, patch
from proto import tp10, tp_native as wire
from client.common.stream_guard import StreamGuard
from client import trackpad_sink
from tests.fixtures import descriptor_data, contact, frame, FakeInput

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
