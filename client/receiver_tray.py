"""Compatibility import for the optional Linux AppIndicator tray."""
if not __package__:
    import bootstrap
    bootstrap.setup()
import sys
from client.linux import tray as _implementation
sys.modules[__name__] = _implementation
