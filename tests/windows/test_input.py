"""SendInput payload/ABI checks; all output calls are intercepted."""
import ctypes
import sys
import unittest
from unittest.mock import patch
from client.windows import input as backend


class InputTests(unittest.TestCase):
    def test_relative_motion_payload(self):
        with patch.object(backend, '_send') as send:
            backend.mouse_move(3, -2)
        packet = send.call_args.args[0][0]
        self.assertEqual((packet.union.mi.dx, packet.union.mi.dy), (3, -2))
        self.assertEqual(packet.union.mi.dwFlags, backend.MOUSEEVENTF_MOVE)

    def test_wheel_retains_windows_units(self):
        with patch.object(backend, '_send') as send:
            backend.mouse_wheel(vertical=120, horizontal=240)
        packets = send.call_args.args[0]
        self.assertEqual([p.union.mi.mouseData for p in packets], [120, 240])

    @unittest.skipUnless(sys.platform == 'win32', 'real Windows ctypes ABI')
    def test_input_structure_matches_windows_abi(self):
        self.assertEqual(ctypes.sizeof(backend.INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
