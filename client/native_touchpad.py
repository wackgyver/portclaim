"""Compatibility import for the Linux native touchpad backend."""
if not __package__:
    import bootstrap
    bootstrap.setup()
import sys
from client.linux import native_touchpad as _implementation
sys.modules[__name__] = _implementation
