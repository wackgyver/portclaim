#!/usr/bin/env python3
"""PortClaim Receiver — claim bar + device mapping.

Drivers and capture live on the hub. This window claims a device and injects
on the local OS. SidestickBridge stays the T.A320 ViGEm map.
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("portclaim.Receiver")
    except OSError:
        pass

import claim
import mic_sink
import trackpad_config
import trackpad_sink
import xbox_sink

BG = "#181a1e"
PANEL = "#1c1e24"
FG = "#e6e6e6"
MUTED = "#b4d2ff"
ACCENT = "#385a82"
ROW = "#24262e"

SUPPORTED = (
    ("ta320", "T.A320 Copilot"),
    ("xboxelite", "Xbox Elite"),
    ("magictrackpad", "Magic Trackpad"),
    ("mic", "Microphone"),
)

SIDESTICK = os.environ.get("USB_LOOM_SIDESTICK", "").strip()


class ReceiverApp:
    def __init__(self, hub: str, dest_host: str, client_id: str) -> None:
        self.hub = hub
        self.dest_host = dest_host
        self.client_id = client_id
        self.cfg = trackpad_config.load()
        self._busy = False
        self._last_devices: list[dict] = []
        self.root = tk.Tk()
        self.root.title("PortClaim")
        self.root.configure(bg=BG)
        self.root.minsize(720, 720)
        self.root.geometry("780x820")
        self._build_style()
        self._build()
        self._ensure_sinks()
        self._refresh_devices()
        self.root.after(2000, self._tick)

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Head.TLabel", background=BG, foreground=FG, font=("Segoe UI", 14, "bold"))
        style.configure("Group.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9, "bold"))
        style.configure("Panel.TLabel", background=PANEL, foreground=FG, font=("Segoe UI", 10))
        style.configure("TButton", background=ACCENT, foreground="white", font=("Segoe UI", 10))
        style.configure("TCheckbutton", background=PANEL, foreground=FG, font=("Segoe UI", 10))
        style.configure("TRadiobutton", background=PANEL, foreground=FG, font=("Segoe UI", 10))
        style.configure("TCombobox", fieldbackground=ROW, background=ROW, foreground=FG)
        style.configure("Horizontal.TScale", background=PANEL)

    def _build(self) -> None:
        pad = {"padx": 16, "pady": 6}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="PortClaim", style="Head.TLabel").pack(anchor="w")
        ttk.Label(
            top,
            text="Claim a hub device here. Drivers live on the hub; this window injects.",
            style="Muted.TLabel",
        ).pack(anchor="w")

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=16, pady=8)
        ttk.Label(bar, text="Hub").grid(row=0, column=0, sticky="w")
        self.hub_var = tk.StringVar(value=self.hub)
        ttk.Entry(bar, textvariable=self.hub_var, width=36).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(bar, text="Dest").grid(row=0, column=2, sticky="w")
        self.dest_var = tk.StringVar(value=self.dest_host)
        ttk.Entry(bar, textvariable=self.dest_var, width=16).grid(row=0, column=3, sticky="ew", padx=8)
        self.connect_btn = ttk.Button(bar, text="Connect", command=self._toggle_connect)
        self.connect_btn.grid(row=0, column=4, padx=8)
        bar.columnconfigure(1, weight=1)

        map_row = ttk.Frame(self.root)
        map_row.pack(fill="x", padx=16, pady=(12, 4))
        ttk.Label(map_row, text="Mapping", style="Head.TLabel").pack(side="left")
        ttk.Label(map_row, text="Device", style="Muted.TLabel").pack(side="left", padx=(24, 8))
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(map_row, textvariable=self.device_var, state="readonly", width=36)
        self.device_combo.pack(side="left")
        self.device_combo.bind("<<ComboboxSelected>>", lambda _e: self._show_pane())
        self.claim_btn = ttk.Button(map_row, text="Claim", command=self._claim_selected)
        self.claim_btn.pack(side="left", padx=8)

        self.pane_host = ttk.Frame(self.root)
        self.pane_host.pack(fill="both", expand=True, padx=16, pady=8)
        self.panes: dict[str, ttk.Frame] = {}
        self._build_ta320_pane()
        self._build_xbox_pane()
        self._build_trackpad_pane()
        self._build_mic_pane()

        self.status = ttk.Label(self.root, text="Idle.", style="Muted.TLabel")
        self.status.pack(fill="x", padx=16, pady=(0, 12))

    def _group(self, parent: ttk.Frame, title: str) -> ttk.Frame:
        box = ttk.Frame(parent, style="Panel.TFrame")
        box.pack(fill="x", pady=8)
        ttk.Label(box, text=title.upper(), style="Group.TLabel").pack(anchor="w", padx=12, pady=(10, 4))
        inner = ttk.Frame(box, style="Panel.TFrame")
        inner.pack(fill="x", padx=12, pady=(0, 12))
        return inner

    def _build_ta320_pane(self) -> None:
        pane = ttk.Frame(self.pane_host)
        self.panes["ta320"] = pane
        box = self._group(pane, "T.A320 Copilot")
        ttk.Label(
            box,
            text=(
                "SidestickBridge is Windows-only. This pane still claims ta320 on the hub. Map the stick on a Windows Receiver, or add a later uinput X360 sidecar."
                if sys.platform != "win32"
                else "Xbox mapping stays in SidestickBridge. This pane only claims the stick and opens that map."
            ),
            style="Panel.TLabel",
            wraplength=680,
        ).pack(anchor="w", pady=4)
        self.ta320_status = ttk.Label(box, text=self._claim_dest_text(27182), style="Panel.TLabel")
        self.ta320_status.pack(anchor="w", pady=4)
        ttk.Button(box, text="Open SidestickBridge mapping", command=self._open_sidestick).pack(anchor="w", pady=8)

    def _build_xbox_pane(self) -> None:
        pane = ttk.Frame(self.pane_host)
        self.panes["xboxelite"] = pane
        box = self._group(pane, "Xbox Elite")
        ttk.Label(
            box,
            text="Microsoft pad on the hub (xpad). This Receiver injects an identity ViGEm Xbox 360 controller on UDP :27185. SidestickBridge stays the T.A320 map — the Elite does not share that window. Use an Ultrabase USB 2.0 jack (same hub as the condenser), not a USB 1.1 companion port.",
            style="Panel.TLabel",
            wraplength=680,
        ).pack(anchor="w", pady=4)
        self.xbox_status = ttk.Label(box, text=self._claim_dest_text(27185), style="Panel.TLabel")
        self.xbox_status.pack(anchor="w", pady=4)
        self.xbox_frames = ttk.Label(box, text="XB10 idle", style="Panel.TLabel")
        self.xbox_frames.pack(anchor="w", pady=2)

    def _build_mic_pane(self) -> None:
        pane = ttk.Frame(self.pane_host)
        self.panes["mic"] = pane
        box = self._group(pane, "USB microphone")
        ttk.Label(
            box,
            text=(
                "The hub captures the USB condenser (AU10). This Receiver injects PCM into PipeWire PortClaim Mic. Handy records portclaim_mic.monitor (or Default after the virtmic unit). Not a Bluetooth HFP pin, not the laptop built-in."
                if sys.platform != "win32"
                else "The hub captures the USB condenser (AU10). This Receiver injects PCM into CABLE Input (VB-CABLE). Handy records CABLE Output — not Steam Streaming Microphone, and not a Bluetooth hands-free mic."
            ),
            style="Panel.TLabel",
            wraplength=680,
        ).pack(anchor="w", pady=4)
        self.mic_source = ttk.Label(box, text="Hub source: (poll the fabric)", style="Panel.TLabel")
        self.mic_source.pack(anchor="w", pady=2)
        self.mic_dest = ttk.Label(box, text=self._claim_dest_text(27183), style="Panel.TLabel")
        self.mic_dest.pack(anchor="w", pady=2)
        self.mic_inject = ttk.Label(box, text="Inject into: (sink not started)", style="Panel.TLabel")
        self.mic_inject.pack(anchor="w", pady=2)
        self.mic_frames = ttk.Label(box, text="AU10 idle", style="Panel.TLabel")
        self.mic_frames.pack(anchor="w", pady=2)
        self.mic_level = ttk.Label(box, text="Level: —", style="Panel.TLabel")
        self.mic_level.pack(anchor="w", pady=2)
        self.monitor_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            box,
            text="Hear the stream on these speakers (howls if the condenser is in the same room)",
            variable=self.monitor_var,
            command=self._on_monitor,
        ).pack(anchor="w", pady=6)
        ttk.Label(
            box,
            text=(
                "Handy (AUR) records portclaim_mic.monitor. Bind Hyprland to handy --toggle-transcription. VAD off for the first proof. Leave monitor off — speakers into that dock mic howl. If Handy only lists Default, the virtmic unit already set that source."
                if sys.platform != "win32"
                else "Handy records CABLE Output (VB-Audio Virtual Cable). Hold Ctrl+Space for the whole sentence. Turn Handy VAD off for the first proof. Leave monitor off — speakers into that dock mic howl."
            ),
            style="Panel.TLabel",
            wraplength=680,
        ).pack(anchor="w", pady=8)

    def _build_trackpad_pane(self) -> None:
        pane = ttk.Frame(self.pane_host)
        self.panes["magictrackpad"] = pane
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

    def _on_monitor(self) -> None:
        mic_sink.MONITOR["enabled"] = bool(self.monitor_var.get())

    def _inventory(self) -> tuple[list[str], dict]:
        hub = self.hub_var.get().strip()
        if hub and not hub.startswith("http"):
            hub = f"http://{hub}:27180"
        try:
            payload = claim.request(hub, "GET", "/v1/devices")
        except SystemExit:
            return [], {}
        except Exception:
            return [], {}
        present: set[str] = set()
        for row in payload.get("devices") or []:
            for name in row.get("adapters") or []:
                present.add(name)
        routes = payload.get("routes") or {}
        labels = []
        meta = {}
        for adapter, title in SUPPORTED:
            claimed = adapter in routes
            on_hub = adapter in present
            if on_hub and claimed:
                suffix = "claimed"
            elif on_hub:
                suffix = "not claimed"
            elif claimed:
                suffix = "claimed — not on hub"
            else:
                suffix = "not on hub"
            label = f"{title}  ({suffix})"
            labels.append(label)
            meta[label] = {
                "adapter": adapter,
                "claimed": claimed,
                "present": on_hub,
            }
        self._last_devices = payload.get("devices") or []
        return labels, meta

    def _refresh_devices(self) -> None:
        labels, meta = self._inventory()
        self._device_meta = meta
        current = self.device_var.get()
        self.device_combo["values"] = labels
        if labels and current not in labels:
            prefer = next((l for l in labels if "Magic Trackpad" in l), labels[0])
            self.device_var.set(prefer)
        self._show_pane()

    def _dest_host(self) -> str:
        return self.dest_var.get().strip() if hasattr(self, "dest_var") else (self.dest_host or "")

    def _claim_dest_text(self, port: int, extra: str = "") -> str:
        dest = self._dest_host()
        if not dest:
            return "Claim dest (set Dest)"
        suffix = f"    {extra}" if extra else ""
        return f"Claim dest {dest}:{port}{suffix}"

    def _selected_adapter(self) -> str | None:
        meta = getattr(self, "_device_meta", {}).get(self.device_var.get())
        if not meta:
            return None
        return str(meta["adapter"])

    def _show_pane(self) -> None:
        adapter = self._selected_adapter()
        for name, pane in self.panes.items():
            pane.pack_forget()
        if adapter in self.panes:
            self.panes[adapter].pack(fill="both", expand=True)
        meta = getattr(self, "_device_meta", {}).get(self.device_var.get()) or {}
        self.claim_btn.configure(state=("normal" if meta and not meta.get("claimed") else "disabled"))

    def _toggle_connect(self) -> None:
        if self._busy:
            return
        hub = self.hub_var.get().strip()
        if not hub:
            self.status.configure(text="set Hub")
            return
        if hub and not hub.startswith("http"):
            hub = f"http://{hub}:27180"
        dest = self._dest_host()
        if not dest:
            self.status.configure(text="set Dest")
            return
        self._busy = True

        def work() -> None:
            try:
                routes = claim.request(hub, "GET", "/v1/routes").get("routes") or {}
                if all(name in routes for name in claim.CONNECT):
                    for adapter in claim.CONNECT:
                        claim.request(hub, "POST", f"/v1/devices/{adapter}/release", {})
                    msg = "Released stick, Elite, trackpad, and mic claims."
                else:
                    claim._start_sidestick()
                    claim._start_xbox_sink()
                    for adapter in claim.CONNECT:
                        dest_full = f"{dest}:{claim.DEFAULT_PORTS[adapter]}"
                        claim._register_and_claim(hub, self.client_id, adapter, dest_full)
                    msg = f"Claimed ta320 + xboxelite + magictrackpad + mic → {dest}"
                self.root.after(0, lambda: self._after_connect(msg))
            except Exception as exc:
                self.root.after(0, lambda: self._after_connect(str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _ensure_sinks(self) -> None:
        if not claim._port_open(27183):
            threading.Thread(
                target=mic_sink.serve,
                args=(27183, "", False),
                name="au10-sink",
                daemon=True,
            ).start()
        if not claim._port_open(27184):
            threading.Thread(target=trackpad_sink.serve, args=(27184,), name="tp10-sink", daemon=True).start()
        else:
            print("TP10 :27184 already bound — this Receiver will not start a second sink", flush=True)
        if not claim._port_open(27185):
            def _xbox() -> None:
                try:
                    xbox_sink.serve(27185)
                except BaseException as exc:
                    xbox_sink.STATS["error"] = str(exc) or type(exc).__name__
                    xbox_sink.STATS["listening"] = False
                    print(f"xbox_sink failed: {xbox_sink.STATS['error']}", flush=True)

            threading.Thread(target=_xbox, name="xb10-sink", daemon=True).start()

    def _after_connect(self, msg: str) -> None:
        self._busy = False
        self._ensure_sinks()
        self.status.configure(text=msg)
        self._refresh_devices()

    def _claim_selected(self) -> None:
        adapter = self._selected_adapter()
        if not adapter:
            return
        hub = self.hub_var.get().strip()
        if not hub:
            self.status.configure(text="set Hub")
            return
        if hub and not hub.startswith("http"):
            hub = f"http://{hub}:27180"
        dest_host = self._dest_host()
        if not dest_host:
            self.status.configure(text="set Dest")
            return
        dest = f"{dest_host}:{claim.DEFAULT_PORTS.get(adapter, 27182)}"
        try:
            claim._register_and_claim(hub, self.client_id, adapter, dest)
            self.status.configure(text=f"Claimed {adapter} → {dest}")
        except SystemExit as exc:
            self.status.configure(text=str(exc))
        self._refresh_devices()

    def _open_sidestick(self) -> None:
        if sys.platform != "win32":
            self.status.configure(text="SidestickBridge is Windows-only.")
            return
        claim._start_sidestick()
        if os.path.isfile(SIDESTICK):
            creation = getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen([SIDESTICK], creationflags=creation)
        self.status.configure(text="SidestickBridge: click Mapping for the Xbox pad map.")

    def _tick(self) -> None:
        last = trackpad_sink.STATS.get("tp10_last") or 0.0
        packets = trackpad_sink.STATS.get("tp10_packets") or 0
        mode = trackpad_sink.STATS.get("tp10_mode") or "-"
        if last:
            age = time.time() - last
            sticky = " sticky" if trackpad_sink.STATS.get("tp10_sticky") else ""
            tp = f"TP10 {age:.1f}s ago  {packets} frames  mode={mode}{sticky}"
        else:
            tp = "TP10 idle"
        au_last = mic_sink.STATS.get("au10_last") or 0.0
        au_pkts = mic_sink.STATS.get("au10_packets") or 0
        au_dev = mic_sink.STATS.get("au10_device") or "-"
        au_rms = float(mic_sink.STATS.get("au10_rms") or 0.0)
        au_peak = float(mic_sink.STATS.get("au10_peak") or 0.0)
        au_err = (mic_sink.STATS.get("error") or "").strip()
        if au_err:
            au = f"AU10 failed: {au_err}"
        elif au_last:
            au = f"AU10 {time.time() - au_last:.1f}s ago  {au_pkts} frames"
        else:
            au = "AU10 idle"
        sb = "SB10 listening" if claim._port_open(27182) else "SB10 not listening"
        xb_last = xbox_sink.STATS.get("xb10_last") or 0.0
        xb_pkts = xbox_sink.STATS.get("xb10_packets") or 0
        xb_err = (xbox_sink.STATS.get("error") or "").strip()
        if xb_err:
            xb = f"XB10 failed: {xb_err}"
        elif xb_last:
            xb = f"XB10 {time.time() - xb_last:.1f}s ago  {xb_pkts} frames"
        else:
            xb = "XB10 idle" if claim._port_open(27185) else "XB10 not listening"
        self.status.configure(text=f"{tp}   ·   {sb}   ·   {au}   ·   {xb}")
        dest = self._dest_host()
        self.ta320_status.configure(text=self._claim_dest_text(27182, sb))
        self.xbox_status.configure(text=self._claim_dest_text(27185, xb))
        self.xbox_frames.configure(text=xb)
        self.mic_dest.configure(text=self._claim_dest_text(27183))
        self.mic_inject.configure(text=f"Inject into: {au_dev}")
        self.mic_frames.configure(text=au)
        self.mic_level.configure(text=f"Level: rms {au_rms:.0f}  peak {au_peak:.0f} / 32767")
        source = next(
            (
                f"{row.get('name')}  {row.get('path')}  {row.get('vid')}:{row.get('pid')}"
                for row in getattr(self, "_last_devices", [])
                if "mic" in (row.get("adapters") or [])
            ),
            "no USB mic on hub",
        )
        self.mic_source.configure(text=f"Hub source: {source}")
        self._refresh_devices()
        self.root.after(2000, self._tick)

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main(hub: str | None = None, dest_host: str | None = None, client_id: str | None = None) -> int:
    app = ReceiverApp(
        hub=hub or os.environ.get("USB_LOOM_HUB", "").strip(),
        dest_host=dest_host or os.environ.get("USB_LOOM_SELF", "").strip() or claim._self_host(),
        client_id=client_id or os.environ.get("USB_LOOM_CLIENT_ID", socket.gethostname()),
    )
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
