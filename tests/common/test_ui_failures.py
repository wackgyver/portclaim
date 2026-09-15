"""Deferred claim errors must reach the UI without starting real workers."""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from client import ui


class DeferredErrorTests(unittest.TestCase):
    def test_error_message_survives_except_scope_and_clears_busy(self):
        for failure in (OSError('offline'), SystemExit('token is unset')):
            with self.subTest(error=type(failure).__name__):
                app = ui.ReceiverApp.__new__(ui.ReceiverApp)
                app._busy = False
                app.hub_var = MagicMock()
                app.hub_var.get.return_value = 'http://hub.invalid'
                app._dest_host = MagicMock(return_value='192.0.2.20')
                app.root = MagicMock()
                app.status = MagicMock()
                app._ensure_sinks = MagicMock()
                app._refresh_devices = MagicMock()
                with patch.object(ui.claim, 'request', side_effect=failure), patch.object(
                        ui.threading, 'Thread', side_effect=lambda *, target, **kw: SimpleNamespace(start=target)):
                    app._toggle_connect()
                self.assertTrue(app._busy)
                app.root.after.call_args.args[1]()
                self.assertFalse(app._busy)
                app.status.configure.assert_called_once_with(text=str(failure))
