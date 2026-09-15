"""Exercise import isolation in fresh interpreters, with hardware packages denied."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ImportBoundaryTests(unittest.TestCase):
    def probe(self, code):
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_common_modules_are_driver_and_desktop_free(self):
        self.probe('''
import importlib, importlib.abc, pkgutil, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'ctypes', 'tkinter', 'evdev', 'libevdev', 'vgamepad', 'gi', 'pystray'} or fullname.startswith(('client.windows', 'client.linux')):
            raise AssertionError('Forbidden common dependency: ' + fullname)
sys.meta_path.insert(0, Deny())
import client.common
for module in pkgutil.iter_modules(client.common.__path__, 'client.common.'):
    importlib.import_module(module.name)
''')

    def test_platform_probe_never_imports_other_os_or_opens_devices(self):
        self.probe('''
import importlib.abc, json, socket, ssl, subprocess, sys, tempfile, tkinter, urllib.request
from pathlib import Path
wrong = 'client.linux' if sys.platform == 'win32' else 'client.windows'
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(wrong) or fullname.split('.')[0] in {'vgamepad', 'evdev', 'libevdev', 'gi', 'pystray'}:
            raise AssertionError('Forbidden platform import: ' + fullname)
def denied(*args, **kwargs):
    raise AssertionError('Probe attempted device/network/process/window I/O')
sys.meta_path.insert(0, Deny())
socket.socket = subprocess.Popen = tkinter.Tk = urllib.request.urlopen = denied
from client.receiver_app import cli
with tempfile.TemporaryDirectory() as d:
    report = Path(d) / 'report.json'
    assert cli(['--check-platform', str(report)]) == 0
    info = json.loads(report.read_text())
    assert not info['driver_imported'] and info['ui_imported']
    assert not info['linux_backend_imported' if sys.platform == 'win32' else 'windows_backend_imported']
''')

    def test_original_entrypoint_help_commands(self):
        for command in ('receiver_app.py', 'claim.py', 'trackpad_sink.py', 'mic_sink.py', 'xbox_sink.py'):
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, str(ROOT / 'client' / command), '--help'],
                                        cwd=ROOT.parent, text=True, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)
