"""Compatibility import for the Windows WASAPI implementation."""
if not __package__:
    import bootstrap
    bootstrap.setup()
import sys
from client.windows import wasapi_out as _implementation
sys.modules[__name__] = _implementation
