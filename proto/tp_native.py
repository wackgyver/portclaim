"""Lossless native-touchpad extension to TP10; legacy receivers ignore the tail.

80-byte TP10 prefix + <4sIQ> (TN01, stream epoch, capture monotonic microseconds)
+ five <iHHh> records (full tracking ID, major, minor, orientation), in the
same order as the prefix contacts. Total: 146 bytes. Geometry is control-plane
metadata, not guessed from the contacts. Source timestamps are NOT local clocks.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

import tp10

EXTENSION = struct.Struct("<4sIQ")
DETAIL = struct.Struct("<iHHh")
TAG = b"TN01"
PACKET_SIZE = tp10.PACKET_SIZE + EXTENSION.size + tp10.MAX_CONTACTS * DETAIL.size


@dataclass(frozen=True)
class Contact:
    slot: int
    tracking_id: int
    x: int
    y: int
    pressure: int
    major: int
    minor: int
    orientation: int = 0


@dataclass(frozen=True)
class Frame:
    seq: int
    buttons: int
    contacts: tuple[Contact, ...]
    epoch: int
    capture_us: int
    rel: tuple[int, int, int, int] = (0, 0, 0, 0)


def encode(frame: Frame) -> bytes:
    if len(frame.contacts) > tp10.MAX_CONTACTS:
        raise ValueError("too many TP10 contacts")
    rows = [(c.slot, c.tracking_id, c.x, c.y, c.pressure, c.major, c.minor) for c in frame.contacts]
    details = b"".join(DETAIL.pack(c.tracking_id, c.major, c.minor, c.orientation) for c in frame.contacts)
    details += b"\0" * (DETAIL.size * (tp10.MAX_CONTACTS - len(rows)))
    return (tp10.encode_tp10(frame.seq, frame.buttons, rows, frame.rel)
            + EXTENSION.pack(TAG, frame.epoch, frame.capture_us) + details)


def decode(packet: bytes) -> Frame | None:
    if len(packet) != PACKET_SIZE or packet[9] > tp10.MAX_CONTACTS:
        return None
    tag, epoch, capture_us = EXTENSION.unpack_from(packet, tp10.PACKET_SIZE)
    if tag != TAG:
        return None
    prefix = tp10.decode_tp10(packet[:tp10.PACKET_SIZE])
    if prefix is None:
        return None
    seq, buttons, rows, rel = prefix
    if len(rows) != packet[9] or buttons & ~tp10.BTN_CLICK:
        return None
    contacts = []
    seen = set()
    for i, row in enumerate(rows):
        tid, major, minor, orientation = DETAIL.unpack_from(packet, tp10.PACKET_SIZE + EXTENSION.size + i * DETAIL.size)
        if tid < 0 or tid in seen:
            return None
        seen.add(tid)
        contacts.append(Contact(row[0], tid, row[2], row[3], row[4], major, minor, orientation))
    return Frame(seq, buttons, tuple(contacts), epoch, capture_us, rel)
