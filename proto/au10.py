"""AU10 data-plane: 16-bit PCM microphone frames.

Header (16 bytes, little-endian) + PCM:
  0-3  magic 'AU10'
  4-7  seq
  8-11 sample_rate
  12   channels
  13   bits (16)
  14-15 payload_bytes
  16+  signed PCM
"""

MAGIC = 0x30315541  # ASCII "AU10" little-endian
HEADER_SIZE = 16
DEFAULT_AUDIO_PORT = 27183
SAMPLE_RATE = 44100
CHANNELS = 1
BITS = 16
FRAME_MS = 10
