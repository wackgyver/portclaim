"""Optional Linux AppIndicator tray, integrated into Tk's existing main thread."""
from __future__ import annotations


def create_icon():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, 62, 62), fill="#385a82")
    # A USB trident, legible at the bar's small icon size.
    draw.line((32, 48, 32, 15), fill="white", width=5)
    draw.polygon(((25, 19), (32, 9), (39, 19)), fill="white")
    draw.line((32, 39, 19, 32, 19, 25), fill="white", width=4)
    draw.ellipse((14, 19, 24, 29), fill="white")
    draw.line((32, 32, 45, 25, 45, 20), fill="white", width=4)
    draw.rectangle((40, 14, 50, 24), fill="white")
    draw.ellipse((26, 45, 38, 57), fill="white")
    return image


class ReceiverTray:
    def __init__(self, request):
        # Explicit AppIndicator backend: no invisible legacy XEmbed fallback.
        from pystray._appindicator import Icon
        from pystray import Menu, MenuItem
        from gi.repository import GLib

        self.context = GLib.MainContext.default()
        self.icon = Icon(
            "portclaim", create_icon(), "PortClaim Receiver",
            Menu(
                MenuItem("PortClaim receiver running", None, enabled=False),
                MenuItem("Show controls", lambda _icon, _item: request("show")),
                MenuItem("Hide controls", lambda _icon, _item: request("hide")),
                Menu.SEPARATOR,
                MenuItem("Quit receiver (stops devices)", lambda _icon, _item: request("quit")),
            ),
        )
        self.icon.run_detached()

    def poll(self):
        # GTK/AppIndicator and Tk stay on one thread. Bound dispatch work so a
        # busy DBus queue cannot starve Tk. No extra GLib thread/service needed.
        for _ in range(32):
            if not self.context.pending():
                break
            self.context.iteration(False)

    def close(self):
        self.icon.visible = False
        self.icon.stop()
        self.poll()
