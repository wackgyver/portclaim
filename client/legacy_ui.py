"""Shared legacy gesture controls; native Linux never displays these settings."""
import tkinter as tk
from tkinter import ttk
from client.common import trackpad_config
BG = "#181a1e"


class LegacyControlsMixin:
    def _build_legacy_trackpad_controls(self, pane):
        canvas = tk.Canvas(pane, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(pane, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        point = self._group(inner, "Point & Click")
        self.speed_var = tk.IntVar(value=self.cfg.tracking_speed)
        self._slider(point, "Tracking speed", self.speed_var, self._on_speed)
        self.tap_var = tk.BooleanVar(value=self.cfg.tap_to_click)
        self._check(point, "Tap to click", self.tap_var, "tap_to_click")
        ttk.Label(point, text="Secondary click  —  Two-finger click (right)", style="Panel.TLabel").pack(anchor="w", pady=2)
        self.invert_x_var = tk.BooleanVar(value=self.cfg.invert_x)
        self._check(point, "Invert horizontal axis", self.invert_x_var, "invert_x")
        self.invert_y_var = tk.BooleanVar(value=self.cfg.invert_y)
        self._check(point, "Invert vertical axis", self.invert_y_var, "invert_y")

        scroll_z = self._group(inner, "Scroll & Zoom")
        self.natural_var = tk.BooleanVar(value=self.cfg.natural_scroll)
        self._check(scroll_z, "Scroll direction: Natural", self.natural_var, "natural_scroll")
        self.scroll_speed_var = tk.IntVar(value=self.cfg.scroll_speed)
        self._slider(scroll_z, "Scroll speed", self.scroll_speed_var, self._on_scroll_speed)
        ttk.Label(scroll_z, text="Zoom in or out  —  Off (Ctrl+ / Ctrl-)", style="Panel.TLabel").pack(anchor="w", pady=2)
        ttk.Label(scroll_z, text="Smart zoom  —  Off (coming)", style="Panel.TLabel").pack(anchor="w", pady=2)
        ttk.Label(scroll_z, text="Rotate  —  Off (coming)", style="Panel.TLabel").pack(anchor="w", pady=2)

        flick = self._group(inner, "Flick coast")
        self.flick_force_var = tk.IntVar(value=self.cfg.flick_force)
        self._slider(flick, "Flick force", self.flick_force_var, self._on_flick_force)
        self.flick_friction_var = tk.IntVar(value=self.cfg.flick_friction)
        self._slider(flick, "Flick friction", self.flick_friction_var, self._on_flick_friction)
        ttk.Label(
            flick,
            text="Decay is not a setting — it follows force vs friction.",
            style="Panel.TLabel",
        ).pack(anchor="w", pady=2)

        more = self._group(inner, "More Gestures")
        ttk.Label(more, text="Three finger drag  —  On (sticky mark)", style="Panel.TLabel").pack(anchor="w", pady=2)
        self._choice(
            more,
            "Swipe between pages",
            "swipe_pages",
            [("Off", "off"), ("Back / Forward (Alt+Left / Alt+Right)", "back-forward")],
        )
        self._choice(
            more,
            "Swipe between full-screen apps",
            "swipe_fullscreen",
            [("Off", "off"), ("Virtual desktops (Win+Ctrl+Left / Right)", "desktops")],
        )
        self._choice(
            more,
            "Mission Control",
            "mission_control",
            [("Off", "off"), ("Task View (Win+Tab)", "task-view")],
        )
        self._choice(
            more,
            "Notification Center",
            "notification_center",
            [("Off", "off"), ("Notifications (Win+N)", "win-n")],
        )
        self._choice(
            more,
            "Show Desktop",
            "show_desktop",
            [("Off", "off"), ("Show desktop (Win+D)", "win-d")],
        )
        self._choice(
            more,
            "Launchpad",
            "launchpad",
            [("Off", "off"), ("Start (Win)", "start")],
        )

    def _slider(self, parent: ttk.Frame, label: str, var: tk.IntVar, command) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").pack(anchor="w", pady=(6, 0))
        ttk.Scale(parent, from_=0, to=10, variable=var, command=command).pack(fill="x")

    def _check(self, parent: ttk.Frame, label: str, var: tk.BooleanVar, field: str) -> None:
        ttk.Checkbutton(
            parent,
            text=label,
            variable=var,
            command=lambda: self._set_bool(field, var.get()),
        ).pack(anchor="w", pady=2)

    def _radio(self, parent: ttk.Frame, var: tk.StringVar, choices: list[tuple[str, str]], field: str) -> None:
        for label, value in choices:
            ttk.Radiobutton(
                parent,
                text=label,
                value=value,
                variable=var,
                command=lambda f=field, v=var: self._set_str(f, v.get()),
            ).pack(anchor="w")

    def _choice(self, parent: ttk.Frame, title: str, field: str, choices: list[tuple[str, str]]) -> None:
        ttk.Label(parent, text=title, style="Panel.TLabel").pack(anchor="w", pady=(8, 0))
        current = getattr(self.cfg, field)
        var = tk.StringVar(value=current)
        setattr(self, f"{field}_var", var)
        combo = ttk.Combobox(parent, textvariable=var, state="readonly", width=52, values=[c[0] for c in choices])
        labels = {value: label for label, value in choices}
        reverse = {label: value for label, value in choices}
        var.set(labels.get(current, choices[0][0]))

        def on_pick(_event=None, field=field, reverse=reverse, var=var) -> None:
            self._set_str(field, reverse.get(var.get(), "off"))

        combo.bind("<<ComboboxSelected>>", on_pick)
        combo.pack(anchor="w", pady=2)

    def _set_bool(self, field: str, value: bool) -> None:
        setattr(self.cfg, field, bool(value))
        trackpad_config.save(self.cfg)

    def _set_str(self, field: str, value: str) -> None:
        setattr(self.cfg, field, value)
        trackpad_config.save(self.cfg)

    def _on_speed(self, _raw=None) -> None:
        self.cfg.tracking_speed = int(self.speed_var.get())
        trackpad_config.save(self.cfg)

    def _on_scroll_speed(self, _raw=None) -> None:
        self.cfg.scroll_speed = int(self.scroll_speed_var.get())
        trackpad_config.save(self.cfg)

    def _on_flick_force(self, _raw=None) -> None:
        self.cfg.flick_force = int(self.flick_force_var.get())
        trackpad_config.save(self.cfg)

    def _on_flick_friction(self, _raw=None) -> None:
        self.cfg.flick_friction = int(self.flick_friction_var.get())
        trackpad_config.save(self.cfg)
