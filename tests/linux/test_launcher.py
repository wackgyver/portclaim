"""Show/hide signals cannot kill an initializing receiver or spawn a second one."""
import signal
import sys
import unittest
from unittest.mock import patch

from deploy.linux import portclaim


@unittest.skipUnless(sys.platform == "linux", "Linux /proc and signals")
class LauncherTests(unittest.TestCase):
    def test_signal_waits_for_handler_readiness(self):
        mask = 1 << (signal.SIGUSR1 - 1)
        with patch.object(portclaim.subprocess, "check_output", return_value="123\n"), patch.object(portclaim.Path, "read_text", side_effect=["SigCgt: 0\n", f"SigCgt: {mask:x}\n"]), patch.object(portclaim.time, "sleep") as sleep, patch.object(portclaim.os, "kill") as kill:
            portclaim.window_signal(signal.SIGUSR1)
        sleep.assert_called_once_with(.1)
        kill.assert_called_once_with(123, signal.SIGUSR1)

    def test_missing_handler_never_sends_terminating_default_signal(self):
        with patch.object(portclaim.subprocess, "check_output", return_value="123\n"), patch.object(portclaim.Path, "read_text", return_value="SigCgt: 0\n"), patch.object(portclaim.time, "monotonic", side_effect=[0, 0, 11]), patch.object(portclaim.time, "sleep"), patch.object(portclaim.os, "kill") as kill:
            with self.assertRaises(SystemExit):
                portclaim.window_signal(signal.SIGUSR1)
        kill.assert_not_called()

    def test_no_receiver_never_signals_pid_zero(self):
        with patch.object(portclaim.subprocess, "check_output", return_value="0\n"), patch.object(portclaim.os, "kill") as kill:
            with self.assertRaises(SystemExit):
                portclaim.window_signal(signal.SIGUSR2)
        kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
