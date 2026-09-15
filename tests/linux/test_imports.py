"""Regression: the Linux receiver must not import Windows-only WASAPI."""
import pathlib
import subprocess
import sys
import unittest


@unittest.skipIf(sys.platform == "win32", "Linux receiver import contract")
class LinuxImportTests(unittest.TestCase):
    def test_receiver_import_does_not_load_wasapi(self):
        result = subprocess.run(
            [sys.executable, "-c",
             "import client.platforms, sys; client.platforms.diagnostics(); assert not any(n.startswith('client.windows') for n in sys.modules); assert 'vgamepad' not in sys.modules"],
            cwd=pathlib.Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
