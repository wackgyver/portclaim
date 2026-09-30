"""Read-only storage v1 bounds. JSON control; exact-length binary file payloads."""
from __future__ import annotations

import hashlib
import json
import re

VERSION = 1
MAX_REQUEST = 4096
MAX_RESPONSE = 512 * 1024
CHUNK = 64 * 1024
PREVIEW = 64 * 1024
MAX_FILE = 128 * 1024**3
PAGE_SIZE = 200
MAX_SCAN = 10000
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("invalid storage identifier")
    return value


def digest(value):
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ValueError("invalid storage generation or revision")
    return value


def component(value):
    if (not isinstance(value, str) or value in {"", ".", ".."}
            or "/" in value or not value.isprintable()):
        raise ValueError("unsupported storage path component")
    try:
        if len(value.encode("utf-8")) > 255:
            raise ValueError("storage name is too long")
    except UnicodeError:
        raise ValueError("unsupported storage filename encoding") from None
    return value


def path_parts(value):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("storage path must contain at most 32 components")
    return [component(part) for part in value]


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("storage integer outside allowed range")
    return value


def fingerprint(*values):
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def encode(value, maximum=MAX_RESPONSE):
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(raw) > maximum:
        raise ValueError("storage message is too large")
    return raw


def decode(raw, maximum=MAX_RESPONSE):
    if not raw or len(raw) > maximum or not raw.endswith(b"\n"):
        raise ValueError("invalid or oversized storage message")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("storage message must be an object")
    return result
