#!/bin/sh
set +e
pkill -9 arecord 2>/dev/null
sleep 0.3
timeout 1 arecord -D hw:1,0 --dump-hw-params /dev/null >/tmp/hw-out.txt 2>/tmp/hw-params.txt
echo "=== hw:1,0 params ==="
head -40 /tmp/hw-params.txt
echo "=== capture tests ==="
timeout 2 arecord -D hw:1,0 -f S16_LE -r 48000 -c 1 -t raw /tmp/mic-hw-48k-1.raw >/tmp/c1.txt 2>&1
timeout 2 arecord -D hw:1,0 -f S16_LE -r 44100 -c 1 -t raw /tmp/mic-hw-44k-1.raw >/tmp/c2.txt 2>&1
timeout 2 arecord -D hw:1,0 -f S16_LE -r 16000 -c 1 -t raw /tmp/mic-hw-16k-1.raw >/tmp/c3.txt 2>&1
timeout 2 arecord -D hw:1,0 -f S16_LE -r 48000 -c 2 -t raw /tmp/mic-hw-48k-2.raw >/tmp/c4.txt 2>&1
timeout 2 arecord -D plughw:1,0 -f S16_LE -r 48000 -c 1 -t raw /tmp/mic-plug-48k-1.raw >/tmp/c5.txt 2>&1
echo "=== capture logs ==="
for f in /tmp/c1.txt /tmp/c2.txt /tmp/c3.txt /tmp/c4.txt /tmp/c5.txt; do
  echo "-- $f"
  cat "$f"
done
python3 - <<'PY'
import os, struct
for path in [
    "/tmp/mic-hw-48k-1.raw","/tmp/mic-hw-44k-1.raw","/tmp/mic-hw-16k-1.raw",
    "/tmp/mic-hw-48k-2.raw","/tmp/mic-plug-48k-1.raw",
]:
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        print(f"{os.path.basename(path)}: missing/empty")
        continue
    data = open(path,"rb").read()
    n = len(data)//2
    samples = struct.unpack("<" + "h"*n, data[:n*2])
    peak = max(abs(s) for s in samples) if samples else 0
    rms = (sum(s*s for s in samples)/n)**0.5 if n else 0
    loud = sum(1 for s in samples if abs(s) > 8)
    print(f"{os.path.basename(path)}: bytes={len(data)} rms={rms:.1f} peak={peak} loud={loud}")
PY
