"""Hiding controls must not stop receivers or restart their streams."""
import queue
import unittest
from unittest.mock import MagicMock, patch

import receiver_app


class ReceiverVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.app = receiver_app.ReceiverApp.__new__(receiver_app.ReceiverApp)
        self.app.root = MagicMock()
        self.app._tray = MagicMock()
        self.app._hidden = False
        self.app._closing = False
        self.app._window_actions = queue.SimpleQueue()
        self.app._ensure_sinks = MagicMock()
        self.app._refresh_devices = MagicMock()

    def test_hide_withdraws_without_destroying_or_restarting(self):
        self.app._hide_window()
        self.assertTrue(self.app._hidden)
        self.app.root.withdraw.assert_called_once()
        self.app.root.destroy.assert_not_called()
        self.app._ensure_sinks.assert_not_called()

    def test_no_tray_means_no_hidden_inaccessible_window(self):
        self.app._tray = None
        self.app._hide_window()
        self.assertFalse(self.app._hidden)
        self.app.root.withdraw.assert_not_called()

    def test_show_restores_same_window_without_restarting(self):
        self.app._hidden = True
        self.app._show_window()
        self.assertFalse(self.app._hidden)
        self.app.root.deiconify.assert_called_once()
        self.app._ensure_sinks.assert_not_called()

    def test_hidden_tick_does_not_poll_hub(self):
        self.app._hidden = True
        self.app._tick()
        self.app._refresh_devices.assert_not_called()
        self.app.root.after.assert_called_once_with(2000, self.app._tick)

    def test_menu_and_signal_actions_run_on_tk_poll(self):
        self.app._request_window_action("hide")
        self.app.root.withdraw.assert_not_called()
        self.app._poll_window_actions()
        self.app.root.withdraw.assert_called_once()
        self.app._request_window_action("show")
        self.app._poll_window_actions()
        self.app.root.deiconify.assert_called_once()

    def test_request_flood_is_bounded(self):
        for _ in range(100):
            self.app._request_window_action("show")
        self.assertEqual(self.app._window_actions.qsize(), 16)

    def test_quit_destroys_without_rescheduling_control_poll(self):
        self.app._request_window_action("quit")
        self.app._poll_window_actions()
        self.assertTrue(self.app._closing)
        self.app.root.destroy.assert_called_once()
        self.app.root.after.assert_not_called()

    def test_tray_failure_restores_hidden_controls(self):
        self.app._hidden = True
        self.app._tray.poll.side_effect = RuntimeError("tray unavailable")
        self.app._poll_window_actions()
        self.assertIsNone(self.app._tray)
        self.assertFalse(self.app._hidden)
        self.app.root.deiconify.assert_called_once()

    def test_iconify_hides_only_root_not_child_widget(self):
        self.app.root.state.return_value = "iconic"
        event = MagicMock(widget=self.app.root)
        self.app._on_unmap(event)
        self.app.root.withdraw.assert_called_once()


if __name__ == "__main__":
    unittest.main()
