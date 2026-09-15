"""Read optional receiver configuration explicitly at application entry points."""
import os
from pathlib import Path
import sys


def load_receiver_env():
    if sys.platform == "win32":
        return
    path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "portclaim/usb-loom.env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
