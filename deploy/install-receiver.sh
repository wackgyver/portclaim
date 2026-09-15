#!/bin/sh
# Compatibility entry point. Linux packaging is owned by deploy/linux/.
set -eu
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$HERE/linux/install.py" "$@"
