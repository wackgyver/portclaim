"""TP10 data-plane: Magic Trackpad Type-B contacts.

Header (12 bytes, little-endian) + up to 5 contacts (12 bytes each):
  0-3  magic 'TP10'
  4-7  seq
  8    buttons (bit 0 = physical / left click)
  9    n_contacts
  10-11 reserved
  then per contact:
    0    slot
    1    reserved
    2-3  tracking_id (int16, -1 = up)
    4-5  x (int16)
    6-7  y (int16)
    8-9  pressure (uint16)
    10   touch major (uint8, clamped)
    11   touch minor (uint8, clamped)
  trailer (8 bytes, optional):
    int16 dx, dy, wheel, hwheel from hid-magicmouse EV_REL
"""

from __future__ import annotations

import struct

MAGIC = 0x30315054  # ASCII "TP10" little-endian
HEADER_SIZE = 12
CONTACT_SIZE = 12
MAX_CONTACTS = 5
REL_SIZE = 8
PACKET_SIZE = HEADER_SIZE + CONTACT_SIZE * MAX_CONTACTS + REL_SIZE
DEFAULT_TRACKPAD_PORT = 27184
BTN_CLICK = 0x01


def encode_tp10(
    seq: int,
    buttons: int,
    contacts: list[tuple[int, int, int, int, int, int, int]],
    rel: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> bytes:
    n = min(len(contacts), MAX_CONTACTS)
    header = struct.pack("<I I B B H", MAGIC, seq & 0xFFFFFFFF, buttons & 0xFF, n, 0)
    body = bytearray()
    for i in range(n):
        slot, tracking_id, x, y, pressure, major, minor = contacts[i]
        body += struct.pack(
            "<B B h h h H B B",
            slot & 0xFF,
            0,
            int(max(-32768, min(32767, tracking_id))),
            int(max(-32768, min(32767, x))),
            int(max(-32768, min(32767, y))),
            int(max(0, min(65535, pressure))),
            int(max(0, min(255, major))),
            int(max(0, min(255, minor))),
        )
    body += b"\x00" * (CONTACT_SIZE * (MAX_CONTACTS - n))
    dx, dy, wheel, hwheel = rel
    trailer = struct.pack(
        "<h h h h",
        int(max(-32768, min(32767, dx))),
        int(max(-32768, min(32767, dy))),
        int(max(-32768, min(32767, wheel))),
        int(max(-32768, min(32767, hwheel))),
    )
    return header + bytes(body) + trailer


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
    if len(packet) >= rel_at + REL_SIZE:
        rel = struct.unpack("<h h h h", packet[rel_at : rel_at + REL_SIZE])
    return seq, buttons, contacts, rel
