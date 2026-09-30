"""Validate the unpacked, OS-isolated artifact without installing it."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from tools import package_linux


class PackageTests(unittest.TestCase):
    def test_reproducible_archive_and_isolated_entrypoint(self):
        self.assertTrue(os.access(package_linux.ROOT / 'deploy/install-receiver.sh', os.X_OK))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            first = package_linux.build(package_linux.ROOT, root / 'one.tar.gz')
            second = package_linux.build(package_linux.ROOT, root / 'two.tar.gz')
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as archive:
                names = archive.getnames()
                for expected in ('client/linux/camera.py', 'client/common/camera.py', 'client/webcam_sink.py',
                                 'proto/mjpeg.py', 'docs/webcam.md', 'deploy/linux/packages-camera.arch.txt',
                                 'client/storage_ui.py', 'client/common/storage.py',
                                 'proto/storage_wire.py', 'docs/storage.md'):
                    self.assertIn('portclaim-linux/' + expected, names)
                self.assertFalse(any('/windows/' in n or n.endswith('.dll') or '/test_' in n for n in names))
                self.assertFalse(any('/hub/' in n or n.endswith('/storage-unmount-command') for n in names),
                                 'privileged hub helpers must not enter the receiver artifact')
                # Extract only our own verified, relative regular-file members.
                for member in archive.getmembers():
                    self.assertTrue(member.isfile())
                    self.assertNotIn('..', Path(member.name).parts)
                    target = root / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(member).read())
            script = root / 'portclaim-linux/client/receiver_app.py'
            report = root / 'report.json'
            env = {**os.environ, 'USB_LOOM_TP_BACKEND': 'native', 'PYTHONDONTWRITEBYTECODE': '1',
                   'PYTHONPATH': '', 'PYTHONNOUSERSITE': '1'}
            run = subprocess.run([sys.executable, str(script), '--check-platform', str(report)],
                                 cwd=root, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            result = json.loads(report.read_text())
            self.assertTrue(result['native_touchpad'])
            self.assertFalse(result['windows_backend_imported'])
            self.assertFalse(result['driver_imported'])
            self.assertEqual(result['camera_backend'], 'client.linux.camera')
