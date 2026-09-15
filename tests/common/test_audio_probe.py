"""Debug recording is optional; disabling it must not buffer or write audio."""
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import wave

from client.common.audio import ProbeWriter


class AudioProbeTests(unittest.TestCase):
    def test_disabled_probe_neither_buffers_nor_writes(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"USB_LOOM_AUDIO_PROBE": "0"}
        ):
            path = pathlib.Path(directory) / "probe.wav"
            probe = ProbeWriter(path)
            probe.push(b"\x00\x00" * 100, 48000)
            probe.flush()
            self.assertFalse(probe._buf)
            self.assertFalse(path.exists())

    def test_enabled_probe_retains_wav_functionality(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"USB_LOOM_AUDIO_PROBE": "1"}
        ):
            path = pathlib.Path(directory) / "probe.wav"
            probe = ProbeWriter(path)
            pcm = b"\x01\x00" * 100
            probe.push(pcm, 48000)
            probe.flush()
            with wave.open(str(path), "rb") as handle:
                self.assertEqual(handle.getframerate(), 48000)
                self.assertEqual(handle.readframes(100), pcm)


if __name__ == "__main__":
    unittest.main()
