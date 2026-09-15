"""Compatibility import; settings live in client.common.trackpad_config."""
if not __package__:
    import bootstrap
    bootstrap.setup()
import sys
from client.common import trackpad_config as _implementation
sys.modules[__name__] = _implementation
