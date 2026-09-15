"""Legacy TP10 reception shared by explicit OS input adapters."""
from __future__ import annotations
import os
import socket
import time
from client.common.legacy_gestures import GestureEngine
from client.common.tp10_legacy import decode_tp10

def serve(port: int, output, STATS) -> None:
    STATS.update(backend="legacy", error="")
    output.open()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as exc:
        raise SystemExit(f"TP10 sink cannot bind UDP {port}: {exc}") from exc
    log_dir = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"
    log_path = os.environ.get("USB_LOOM_TP_LOG", os.path.join(log_dir, "usb-loom-tp10.log"))
    backend = output.NAME
    print(f"TP10 sink listening UDP {port}  (Mac-like gestures, {backend})", flush=True)
    output.restore_ole_drag_defaults()
    engine = GestureEngine(output=output)
    packets = 0
    last_fingers = -1
    STATS["listening"] = True
    try:
        while True:
            data, _addr = sock.recvfrom(2048)
            decoded = decode_tp10(data)
            if decoded is None:
                continue
            _seq, buttons, contacts, rel = decoded
            engine.feed(buttons, contacts, rel)
            packets += 1
            STATS["tp10_last"] = time.time()
            STATS["tp10_packets"] = packets
            STATS["tp10_mode"] = engine.mode or "-"
            STATS["tp10_sticky"] = bool(engine.sticky)
            fingers = len(contacts)
            if packets == 1 or packets % 400 == 0 or fingers != last_fingers or any(rel):
                line = (
                    f"frames {packets}  fingers={fingers}  buttons={buttons}  "
                    f"rel={rel}  mode={engine.mode or '-'}  sticky={int(engine.sticky)}"
                )
                print(line, flush=True)
                try:
                    with open(log_path, "a", encoding="utf-8") as log:
                        log.write(line + "\n")
                except OSError:
                    pass
                last_fingers = fingers
    except KeyboardInterrupt:
        print("stopped")
    finally:
        STATS["listening"] = False
        engine.close()
        output.close()
        sock.close()
