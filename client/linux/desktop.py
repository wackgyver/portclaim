"""Linux tray/signal/window lifecycle; no Windows dependency."""
import queue
import signal


def prepare_app():
    pass


class Controller:
    def __init__(self, root, start_hidden=False):
        self.root = root
        self._tray = None
        self._hidden = False
        self._closing = False
        self._window_actions = queue.SimpleQueue()
        self._setup_tray(start_hidden)

    def _setup_tray(self, start_hidden: bool) -> None:
        try:
            from client.linux.tray import ReceiverTray
            self._tray = ReceiverTray(self._request_window_action)
        except Exception as exc:
            # Never hide an app that has no usable way back to its controls.
            print(f"PortClaim tray unavailable; keeping controls visible: {exc}", flush=True)
        signal.signal(signal.SIGUSR1, lambda *_: self._request_window_action("show"))
        signal.signal(signal.SIGUSR2, lambda *_: self._request_window_action("hide"))
        if self._tray is not None:
            self.root.protocol("WM_DELETE_WINDOW", self._hide_window)
            self.root.bind("<Unmap>", self._on_unmap, add="+")
        self.root.after(100, self._poll_window_actions)
        if start_hidden:
            self._hide_window()

    def _request_window_action(self, action: str) -> None:
        # SimpleQueue.put is reentrant, including from Python signal handlers.
        if self._window_actions.qsize() < 16:
            self._window_actions.put(action)

    def _poll_window_actions(self) -> None:
        if self._closing:
            return
        if self._tray is not None:
            try:
                self._tray.poll()
            except Exception as exc:
                print(f"PortClaim tray failed; restoring controls: {exc}", flush=True)
                self._tray = None
                self._show_window()
        for _ in range(16):
            try:
                action = self._window_actions.get_nowait()
            except queue.Empty:
                break
            if action == "show":
                self._show_window()
            elif action == "hide":
                self._hide_window()
            elif action == "quit":
                self._closing = True
                self.root.destroy()
                return
        self.root.after(100, self._poll_window_actions)

    def _show_window(self) -> None:
        self._hidden = False
        self.root.deiconify()
        self.root.lift()

    def _hide_window(self) -> None:
        if self._tray is not None:
            self._hidden = True
            self.root.withdraw()

    def _on_unmap(self, event) -> None:
        if event.widget == self.root and self.root.state() == "iconic":
            self._hide_window()

    def close(self):
        self._closing = True
        if self._tray is not None:
            self._tray.close()
