#!/usr/bin/env python3
"""One tightly scoped root-only unmount request on a DIFFERENT forced SSH key.

No shell, subprocess, file read/export, mount, force/lazy option, or client path.
This helper is inert until separately installed/authorized by an administrator.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import os
from pathlib import Path
import signal
import stat
import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import storage

wire = storage.wire
BASE = Path("/run/portclaim-storage")
LOCK = BASE / "session.lock"
UMOUNT_NOFOLLOW = 8  # Protection only; neither MNT_FORCE nor MNT_DETACH.


def secure_base():
    """The host namespace and root-owned ancestor chain are part of authority."""
    if os.stat("/proc/self/ns/mnt").st_ino != os.stat("/proc/1/ns/mnt").st_ino:
        raise PermissionError("not the host mount namespace")
    if BASE.resolve(strict=True) != BASE:
        raise PermissionError("redirected storage control directory")
    for path in (BASE, *BASE.parents):
        st = path.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise PermissionError("untrusted storage control directory")


@contextmanager
def session_lock():
    """Same inode/lock as the unprivileged SSH reader wrapper; never wait."""
    import fcntl  # Linux only, but importing this module stays harmless elsewhere.
    fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_nlink != 1
                or st.st_mode & 0o007):
            raise PermissionError("untrusted storage session lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = LOCK.lstat()
        if (st.st_dev, st.st_ino) != (current.st_dev, current.st_ino):
            raise PermissionError("storage session lock changed")
        yield
    finally:
        os.close(fd)


def normal_unmount(target):
    """Invoke the Linux syscall directly: no external helper or shell lookup."""
    libc = ctypes.CDLL(None, use_errno=True)
    call = libc.umount2
    call.argtypes = (ctypes.c_char_p, ctypes.c_int)
    call.restype = ctypes.c_int
    if call(os.fsencode(target), UMOUNT_NOFOLLOW) != 0:
        raise OSError(ctypes.get_errno(), "normal unmount failed")


def topology_matches(rows, row):
    # Reject stacked targets, other views of the same filesystem, and submounts.
    # This is the host namespace, not a promise about every container namespace.
    target = row["target"]
    return (sum(r["target"] == target for r in rows) == 1
            and any(r == row for r in rows)
            and not any(r["id"] != row["id"] and (
                r["device"] == row["device"] or r["target"].startswith(target + "/")) for r in rows))


def unmount_selected(volume, expected):
    if not volume.allow_unmount or volume.root != str(BASE / volume.id):
        return "denied"
    try:
        root = storage.Root(volume)
    except (OSError, ValueError):
        return "unavailable"
    try:
        if root.generation != expected:
            return "stale"
        root.check()
        row = dict(root.row)
        if not topology_matches(storage.mount_rows(), row):
            return "busy"
        root.check()
    finally:
        # An O_PATH reference would itself keep a normal unmount busy.
        root.close()
    # Recheck after closing our descriptor. Unprivileged peers cannot alter the
    # protected ancestors. Administrative mount operations must share the lock;
    # no userspace check can serialize an unrelated root remount by itself.
    if not topology_matches(storage.mount_rows(), row):
        return "stale"
    try:
        normal_unmount(volume.root)
    except OSError as exc:
        return "busy" if exc.errno == errno.EBUSY else "failed"
    # Never claim success just because the syscall returned. A replacement or
    # surviving mount produces an uncertain/failed result, not an unplug claim.
    after = storage.mount_rows()
    if any(r["id"] == row["id"] or r["target"] == volume.root or r["device"] == row["device"] for r in after):
        return "failed"
    return "unmounted"


def dispatch(request):
    if (set(request) != {"version", "op", "volume", "generation"}
            or type(request["version"]) is not int or request["version"] != wire.VERSION
            or request["op"] != "unmount"):
        raise ValueError("unsupported storage control request")
    identifier = wire.identifier(request["volume"])
    expected = wire.digest(request["generation"])
    result = {"volume": identifier, "generation": expected, "status": "failed"}
    secure_base()
    try:
        with session_lock():
            volumes = storage.load_config()
            volume = next((v for v in volumes if v.id == identifier), None)
            result["status"] = "denied" if volume is None else unmount_selected(volume, expected)
    except BlockingIOError:
        result["status"] = "busy"
    return result


def main():
    # No command-line paths, environment-based policy, or alternate operations.
    if sys.platform != "linux" or os.geteuid() != 0 or len(sys.argv) != 1:
        return 1
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(10)
    try:
        request = wire.decode(sys.stdin.buffer.readline(wire.MAX_REQUEST + 1), wire.MAX_REQUEST)
        result = dispatch(request)
        sys.stdout.buffer.write(wire.encode({"version": wire.VERSION, "ok": True, "result": result}))
        sys.stdout.buffer.flush()
        return 0
    except (OSError, ValueError, TypeError, KeyError, EOFError):
        print("Storage unmount not confirmed. Refresh to check volume state.", file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
