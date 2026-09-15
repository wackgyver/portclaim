"""Windows lifecycle stays visible and does not initialize Linux tray/signals."""
from contextlib import ExitStack
import unittest
from unittest.mock import patch
from client import ui
from client.windows import desktop


class WindowsDesktopTests(unittest.TestCase):
    def test_windows_window_does_not_start_hidden_or_register_tray_close(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(ui.sys, "platform", "win32"))
            stack.enter_context(patch.object(ui.tk, "Tk"))
            stack.enter_context(patch.object(ui.trackpad_config, "load"))
            stack.enter_context(patch.object(desktop, "prepare_app"))
            for method in ("_build_style", "_build", "_ensure_sinks", "_refresh_devices"):
                stack.enter_context(patch.object(ui.ReceiverApp, method))
            app = ui.ReceiverApp("http://hub.invalid", "192.0.2.20", "test-client", start_hidden=True)
        self.assertIsInstance(app.desktop, desktop.Controller)
        self.assertFalse(app._hidden)
        app.root.withdraw.assert_not_called()
        app.root.protocol.assert_not_called()
