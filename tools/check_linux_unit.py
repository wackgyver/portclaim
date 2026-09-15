#!/usr/bin/env python3
"""Offline systemd syntax check of the rendered user unit; never reload/start it."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.linux.install import Paths, managed_files


def main():
    with tempfile.TemporaryDirectory(prefix='portclaim-unit-check-') as directory:
        base = Path(directory)
        home = base / 'home with spaces%literal'
        home.mkdir()
        runtime = base / 'runtime'
        runtime.mkdir(mode=0o700)
        paths = Paths(home, home / '.config', home / '.local/share')
        unit = base / 'portclaim.service'
        unit.write_bytes(managed_files(ROOT, paths, Path(sys.prefix))[paths.unit][0])
        env = {**os.environ, 'HOME': str(home), 'XDG_CONFIG_HOME': str(paths.config),
               'XDG_DATA_HOME': str(paths.data), 'XDG_RUNTIME_DIR': str(runtime)}
        env.pop('DBUS_SESSION_BUS_ADDRESS', None)
        result = subprocess.run(['systemd-analyze', '--user', 'verify', str(unit)],
                                env=env, capture_output=True, text=True, timeout=30)
        if result.returncode:
            print(result.stdout + result.stderr, file=sys.stderr)
            return result.returncode
        print('Rendered user unit verified offline (spaces and literal percent paths); no service operations')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
