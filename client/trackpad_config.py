"""Magic Trackpad mapping contract. Receiver writes; sink reloads on mtime.

Path: %LOCALAPPDATA%/portclaim/trackpad.json
First run copies %LOCALAPPDATA%/usb-loom/trackpad.json if present.
Env USB_LOOM_TP_* still overrides for one-off debug.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

SECONDARY_CHOICES = ("two-finger", "off")
SWIPE_PAGES_CHOICES = ("off", "back-forward")
SWIPE_FULLSCREEN_CHOICES = ("off", "desktops")
MISSION_CHOICES = ("off", "task-view")
NOTIFY_CHOICES = ("off", "win-n")
DESKTOP_CHOICES = ("off", "win-d")
LAUNCHPAD_CHOICES = ("off", "start")


def config_path() -> Path:
    override = os.environ.get("USB_LOOM_TP_CONFIG")
    if override:
        return Path(override)
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or "."
    new = Path(root) / "portclaim" / "trackpad.json"
    old = Path(root) / "usb-loom" / "trackpad.json"
    if not new.is_file() and old.is_file():
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            new.write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            return old
    return new


@dataclass
class TrackpadConfig:
    tracking_speed: int = 5
    tap_to_click: bool = True
    secondary: str = "off"  # retired: two-finger never clicks; scroll/flick only
    click_drag: bool = False  # retired: one-finger never marks; three-finger only
    invert_x: bool = False
    invert_y: bool = False
    natural_scroll: bool = True
    pinch_zoom: bool = False  # retired: two-finger never zooms; Ctrl+/- instead
    scroll_speed: int = 5
    flick_force: int = 5
    flick_friction: int = 5
    three_finger_drag: bool = True
    drag_idle_ms: int = 350
    swipe_pages: str = "off"
    swipe_fullscreen: str = "off"
    mission_control: str = "off"
    notification_center: str = "off"
    show_desktop: str = "off"
    launchpad: str = "off"

    def scale(self) -> float:
        raw = os.environ.get("USB_LOOM_TP_SCALE")
        if raw:
            return float(raw)
        speed = max(0, min(10, int(self.tracking_speed)))
        return 0.06 + speed * 0.039

    def scroll_scale(self) -> float:
        raw = os.environ.get("USB_LOOM_TP_SCROLL")
        if raw:
            return float(raw)
        speed = max(0, min(10, int(self.scroll_speed)))
        return 0.06 + speed * 0.024

    def firm_v(self) -> float:
        raw = os.environ.get("USB_LOOM_TP_INERTIA_FIRM_V")
        if raw:
            return float(raw)
        n = max(0, min(10, int(self.flick_force)))
        return 40.0 + n * 6.0

    def table_friction(self) -> float:
        raw = os.environ.get("USB_LOOM_TP_INERTIA_FRICTION")
        if raw:
            return float(raw)
        n = max(0, min(10, int(self.flick_friction)))
        return 10.0 + n * 3.2

    def natural(self) -> bool:
        return _env_bool("USB_LOOM_TP_NATURAL", bool(self.natural_scroll))

    def invert_horizontal(self) -> bool:
        return _env_bool("USB_LOOM_TP_INVERT_X", bool(self.invert_x))

    def invert_vertical(self) -> bool:
        return _env_bool("USB_LOOM_TP_INVERT_Y", bool(self.invert_y))

    def idle_ms(self) -> float:
        raw = os.environ.get("USB_LOOM_TP_DRAG_IDLE_MS")
        if raw:
            try:
                return float(_clamp_idle(int(float(raw))))
            except ValueError:
                pass
        return float(self.drag_idle_ms)


def _env_bool(name: str, default: bool) -> bool:
    env = os.environ.get(name)
    if env is None:
        return default
    return env.strip().lower() not in ("0", "false", "no", "off", "")


def _clamp_idle(ms: int) -> int:
    return max(150, min(2500, int(ms)))


def _clamp(cfg: TrackpadConfig) -> TrackpadConfig:
    cfg.tracking_speed = max(0, min(10, int(cfg.tracking_speed)))
    cfg.scroll_speed = max(0, min(10, int(cfg.scroll_speed)))
    try:
        cfg.flick_force = max(0, min(10, int(cfg.flick_force)))
    except (TypeError, ValueError):
        cfg.flick_force = 5
    try:
        cfg.flick_friction = max(0, min(10, int(cfg.flick_friction)))
    except (TypeError, ValueError):
        cfg.flick_friction = 5
    try:
        cfg.drag_idle_ms = _clamp_idle(int(cfg.drag_idle_ms))
    except (TypeError, ValueError):
        cfg.drag_idle_ms = 350
    cfg.invert_x = bool(cfg.invert_x)
    cfg.invert_y = bool(cfg.invert_y)
    cfg.click_drag = False
    cfg.secondary = "off"
    cfg.pinch_zoom = False
    cfg.three_finger_drag = True
    if cfg.swipe_pages not in SWIPE_PAGES_CHOICES:
        cfg.swipe_pages = "off"
    if cfg.swipe_fullscreen not in SWIPE_FULLSCREEN_CHOICES:
        cfg.swipe_fullscreen = "off"
    if cfg.mission_control not in MISSION_CHOICES:
        cfg.mission_control = "off"
    if cfg.notification_center not in NOTIFY_CHOICES:
        cfg.notification_center = "off"
    if cfg.show_desktop not in DESKTOP_CHOICES:
        cfg.show_desktop = "off"
    if cfg.launchpad not in LAUNCHPAD_CHOICES:
        cfg.launchpad = "off"
    return cfg


def load(path: Path | None = None) -> TrackpadConfig:
    target = path or config_path()
    if not target.is_file():
        return _clamp(TrackpadConfig())
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _clamp(TrackpadConfig())
    if not isinstance(raw, dict):
        return _clamp(TrackpadConfig())
    known = {f.name for f in fields(TrackpadConfig)}
    kwargs = {k: raw[k] for k in known if k in raw}
    return _clamp(TrackpadConfig(**kwargs))


def save(cfg: TrackpadConfig, path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    clean = _clamp(cfg)
    target.write_text(json.dumps(asdict(clean), indent=2) + "\n", encoding="utf-8")
    return target


def mtime(path: Path | None = None) -> float:
    target = path or config_path()
    try:
        return target.stat().st_mtime
    except OSError:
        return 0.0
