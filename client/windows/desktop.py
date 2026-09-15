"""Windows keeps its normal visible-window lifecycle; no Linux tray dependencies."""
import ctypes


def prepare_app():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("portclaim.Receiver")
    except (AttributeError, OSError):
        pass


class Controller:
    def __init__(self, root, start_hidden=False):
        self.root = root
        self._hidden = False  # --start-hidden is a Linux feature only.

    def _hide_window(self):
        pass

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()

    def close(self):
        pass
