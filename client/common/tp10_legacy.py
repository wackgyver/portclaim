"""The existing Windows/legacy TP10 decoder, retained without wire changes."""
from __future__ import annotations
import struct
MAGIC = 0x30315054
HEADER_SIZE = 12
CONTACT_SIZE = 12
MAX_CONTACTS = 5
BTN_CLICK = 0x01

def decode_tp10(
    packet: bytes,
) -> tuple[int, int, list[tuple[int, int, int, int, int, int, int]], tuple[int, int, int, int]] | None:
    if len(packet) < HEADER_SIZE:
        return None
    magic, seq, buttons, n, _res = struct.unpack("<I I B B H", packet[:HEADER_SIZE])
    if magic != MAGIC:
        return None
    n = min(n, MAX_CONTACTS)
    contacts: list[tuple[int, int, int, int, int, int, int]] = []
    for i in range(n):
        start = HEADER_SIZE + i * CONTACT_SIZE
        end = start + CONTACT_SIZE
        if len(packet) < end:
            return None
        slot, _pad, tracking_id, x, y, pressure, major, minor = struct.unpack("<B B h h h H B B", packet[start:end])
        if tracking_id < 0:
            continue
        contacts.append((slot, tracking_id, x, y, pressure, major, minor))
    rel = (0, 0, 0, 0)
    rel_at = HEADER_SIZE + CONTACT_SIZE * MAX_CONTACTS
    if len(packet) >= rel_at + 8:
        rel = struct.unpack("<h h h h", packet[rel_at : rel_at + 8])
    return seq, buttons, contacts, rel
