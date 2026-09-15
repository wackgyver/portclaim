"""Linux audio route tests with fake player/socket."""
import struct
import unittest
from unittest.mock import MagicMock, patch
from client.linux import audio as mic_sink
from client import mic_sink as entrypoint, platforms


class LinuxAudioTests(unittest.TestCase):
    def test_linux_dispatches_only_to_pipewire_path(self):
        with patch.object(platforms.sys, "platform", "linux"), patch.object(mic_sink, "serve") as serve:
            entrypoint.serve(0, "test-mic")
        serve.assert_called_once_with(0, "test-mic", False)

    def packet(self, seq=1, rate=44100, channels=1, pcm=b'\0\0' * 441):
        return struct.pack('<IIIBBH', mic_sink.MAGIC, seq, rate, channels, 16, len(pcm)) + pcm

    def test_linux_streams_native_rate_without_python_resampling(self):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.recvfrom.side_effect = [(self.packet(), ('127.0.0.1', 1)), KeyboardInterrupt()]
        player = MagicMock(); player.rate = 44100
        with patch.dict('os.environ', {'USB_LOOM_AUDIO_PROBE': '0'}), patch.object(mic_sink, 'pick_linux_sink', return_value='test-mic'), patch.object(mic_sink.socket, 'socket', return_value=sock), patch.object(mic_sink, 'PulsePaplay', return_value=player) as constructor, patch.object(mic_sink, 'resample_s16', side_effect=AssertionError('Python resampling used')):
            mic_sink.serve(0, 'test-mic')
        constructor.assert_called_once_with('test-mic', 44100)
        player.write.assert_called_once()
        player.close.assert_called_once()
