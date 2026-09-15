"""Native Linux touchpad settings belong to the compositor, not gesture sliders."""
from tkinter import ttk


def build_trackpad_panel(app, pane):
    box = app._group(pane, "Native Magic Trackpad")
    ttk.Label(
        box,
        text="Raw multi-touch contacts → Linux uinput touchpad → libinput/Wayland. "
             "Pointer motion, two-finger scrolling, tap/click and gestures are native OS input—not mouse emulation. "
             "Settings belong to your compositor's device profile (Omarchy: ~/.config/hypr/input.lua). "
             "The legacy tracking/flick sliders do not apply. Scroll momentum and pinch actions depend on the application.",
        style="Panel.TLabel", wraplength=680,
    ).pack(anchor="w", pady=8)
    ttk.Label(box, text="Device: PortClaim Magic Trackpad · Transport: TP10/N1", style="Panel.TLabel").pack(anchor="w")
