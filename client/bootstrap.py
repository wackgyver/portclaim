"""Compatibility for direct script execution; frozen/package imports need no shim."""
from pathlib import Path
import sys


def setup():
    if getattr(sys, "frozen", False):
        return
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # Windows source installs prepare the verified binding here, without running
    # its driver-installing setup.py. Normal Linux installs use their own venv.
    vendor = root / "vendor"
    if sys.platform == "win32" and vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
