"""Compatibility import; freshness tracking lives in client.common.stream_guard."""
if not __package__:
    import bootstrap
    bootstrap.setup()
import sys
from client.common import stream_guard as _implementation
sys.modules[__name__] = _implementation
