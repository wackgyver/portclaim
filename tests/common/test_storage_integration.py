"""Storage integration with receiver activation/quit, no Tk display or I/O."""
import io
from contextlib import redirect_stderr
import unittest
from unittest.mock import Mock, patch

from client import ui


class StorageIntegrationTests(unittest.TestCase):
    def app(self):
        app = ui.ReceiverApp.__new__(ui.ReceiverApp)
        app.root = Mock()
        app.desktop = Mock()
        app.camera = Mock()
        app.storage_window = None
        return app

    def test_button_opens_only_explicitly_and_reuses_existing_window(self):
        app = self.app()
        with patch("client.storage_ui.StorageWindow") as window:
            window.assert_not_called()
            app._open_storage()
            window.assert_called_once_with(app.root)
            app._open_storage()
            self.assertEqual(window.call_count, 1)
            window.return_value.window.deiconify.assert_called_once()
            window.return_value.window.lift.assert_called_once()

    def test_quit_cancels_and_allows_bounded_cleanup_after_mainloop(self):
        app = self.app()
        app.storage_window = Mock()
        app.storage_window._thread.is_alive.return_value = False
        events = []
        app.root.mainloop.side_effect = lambda: events.append("loop ended")
        app.storage_window.close.side_effect = lambda: events.append("cancel")
        app.storage_window._thread.join.side_effect = lambda **kwargs: events.append("join")
        self.assertEqual(app.run(), 0)
        self.assertEqual(events, ["loop ended", "cancel", "join"])
        app.storage_window._thread.join.assert_called_once_with(timeout=4)
        app.camera.close.assert_called_once()
        app.desktop.close.assert_called_once()

    def test_pending_cleanup_is_reported_not_claimed_successfully_stopped(self):
        app = self.app()
        app.storage_window = Mock()
        app.storage_window._thread.is_alive.return_value = True
        output = io.StringIO()
        with redirect_stderr(output):app.run()
        self.assertIn("cancellation still pending", output.getvalue())
        app.camera.close.assert_called_once()
        app.desktop.close.assert_called_once()

    def test_storage_cleanup_failure_still_closes_camera_and_desktop(self):
        app = self.app()
        app.storage_window = Mock()
        app.storage_window.close.side_effect = RuntimeError("test-only cleanup failure")
        with self.assertRaises(RuntimeError):app.run()
        app.camera.close.assert_called_once()
        app.desktop.close.assert_called_once()
