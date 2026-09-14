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
             "import receiver_app, sys; assert 'wasapi_out' not in sys.modules"],
            cwd=pathlib.Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
