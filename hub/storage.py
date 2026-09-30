#!/usr/bin/env python3
"""One read-only storage request over an independently restricted SSH connection.

No listener, mount, authentication fallback, decoder or write operation. Linux
only. An administrator must configure and mount each allowed USB ext4 volume.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time

ROOT = Path(__file__).resolve().parent
# Source checkout layout is explicit. Production searches ONLY its installed
# directory, never an adjacent /usr/local/lib/proto that could shadow the wire
# module when this code is imported by the privileged unmount endpoint.
PROTOCOL_DIR = ROOT.parent / "proto" if ROOT.name == "hub" else ROOT
if str(PROTOCOL_DIR) not in sys.path:
    sys.path.insert(0, str(PROTOCOL_DIR))
import storage_wire as wire

CONFIG = "/etc/portclaim-storage.json"
WARNING = "Read-only inspection; filesystem consistency is not verified. Exports are disabled."


class Unavailable(OSError):
    pass


def mount_rows():
    def unescape(value):
        return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)
    rows = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, right = line.split(" - ", 1)
        before, after = left.split(), right.split()
        rows.append({"id": int(before[0]), "device": before[2], "root": unescape(before[3]),
                     "target": unescape(before[4]), "options": set(before[5].split(",")),
                     "fstype": after[0], "super_options": set(after[2].split(","))})
    return rows


def mount_id(fd):
    for line in Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines():
        if line.startswith("mnt_id:"):
            return int(line.split()[1])
    raise Unavailable("cannot verify storage mount identity")


@dataclass(frozen=True)
class Volume:
    id: str
    label: str
    root: str
    uuid: str
    caveat: str = ""
    allow_unmount: bool = False


def load_config(path=CONFIG):
    # Only host-administrator-owned policy, never a client-selected file/root.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o027 or st.st_size > 32768:
            raise ValueError("storage policy must be a bounded private root-owned non-writable regular file")
        raw = os.read(fd, 32769)
        config = json.loads(raw)
    finally:
        os.close(fd)
    if not isinstance(config, dict) or set(config) != {"volumes"}:
        raise ValueError("invalid storage policy")
    rows = config["volumes"]
    if not isinstance(rows, list) or len(rows) > 8:
        raise ValueError("storage policy permits at most eight volumes")
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"id", "label", "root", "uuid", "caveat", "allow_unmount"}:
            raise ValueError("invalid volume policy")
        volume = Volume(**row)
        wire.identifier(volume.id)
        if type(volume.allow_unmount) is not bool:
            raise ValueError("invalid unmount policy")
        for value, limit in ((volume.label, 128), (volume.caveat, 512)):
            if not isinstance(value, str) or len(value) > limit or (value and not value.isprintable()):
                raise ValueError("invalid storage label or caveat")
        if (not volume.label or not isinstance(volume.root, str) or volume.root == "/"
                or not Path(volume.root).is_absolute()):
            raise ValueError("storage root must be absolute")
        if not isinstance(volume.uuid, str) or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", volume.uuid):
            raise ValueError("storage volume requires an ext4 UUID")
        result.append(volume)
    if len({v.id for v in result}) != len(result):
        raise ValueError("duplicate storage volume identifiers")
    return result


class Root:
    def __init__(self, volume):
        self.volume = volume
        path = Path(volume.root)
        if str(path) != volume.root or path.resolve(strict=True) != path:
            raise Unavailable("storage root must be a real mountpoint without symlinks")
        self.fd = os.open(path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            self.row, self.generation = self.identity()
        except BaseException:
            os.close(self.fd)
            raise

    def identity(self):
        st = os.fstat(self.fd)
        identifier = mount_id(self.fd)
        rows = [row for row in mount_rows() if row["id"] == identifier]
        if len(rows) != 1:
            raise Unavailable("storage mount is no longer available")
        row = rows[0]
        required = {"ro", "nodev", "nosuid", "noexec"}
        if (row["target"] != self.volume.root or row["root"] != "/" or row["fstype"] != "ext4"
                or not required <= row["options"] or "ro" not in row["super_options"]
                or not os.fstatvfs(self.fd).f_flag & os.ST_RDONLY):
            raise Unavailable("storage requires a dedicated read-only ext4 mount with nodev,nosuid,noexec")
        device = os.stat("/dev/disk/by-uuid/" + self.volume.uuid)
        if not stat.S_ISBLK(device.st_mode) or device.st_rdev != st.st_dev:
            raise Unavailable("storage volume identity changed")
        if row["device"] != f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}":
            raise Unavailable("storage mount device changed")
        node = Path("/sys/dev/block") / row["device"]
        resolved = node.resolve(strict=True)
        usb = next((p for p in resolved.parents if (p / "idVendor").is_file()), None)
        if usb is None:
            raise Unavailable("only explicitly selected USB storage is supported")
        devnum = (usb / "devnum").read_text().strip()
        if not devnum.isascii() or not devnum.isdecimal() or int(devnum) < 1:
            raise Unavailable("cannot verify USB connection generation")
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        incarnation = (str(resolved), devnum, boot)
        generation = wire.fingerprint(row["id"], st.st_dev, st.st_ino, self.volume.uuid, incarnation)
        return row, generation

    def check(self):
        _, generation = self.identity()
        if generation != self.generation:
            raise Unavailable("storage changed; select the volume again")

    @property
    def warning(self):
        caveat = self.volume.caveat
        if self.row["super_options"] & {"noload", "norecovery"}:
            caveat = "Journal replay is disabled; pending recovery may make this view incomplete. " + caveat
        return (WARNING + " " + caveat).strip()

    @contextmanager
    def open(self, parts, directory=False):
        parts = wire.path_parts(parts)
        self.check()
        fd = os.dup(self.fd)
        try:
            for i, part in enumerate(parts):
                # O_PATH acquires metadata only: even a device bind-mounted
                # onto a leaf must be rejected before any driver open callback.
                flags = os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW
                if directory or i < len(parts) - 1:
                    flags |= os.O_DIRECTORY
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
                if os.fstat(fd).st_dev != os.fstat(self.fd).st_dev or mount_id(fd) != self.row["id"]:
                    raise Unavailable("nested mounts are not accessible through storage")
            st = os.fstat(fd)
            if directory and not stat.S_ISDIR(st.st_mode):
                raise Unavailable("not a storage directory")
            if not directory and not stat.S_ISREG(st.st_mode):
                raise Unavailable("only regular files may be read")
            # Reopen the already pinned inode, not its mutable directory name.
            readable = os.open(f"/proc/self/fd/{fd}", os.O_RDONLY | os.O_CLOEXEC
                               | (os.O_DIRECTORY if directory else 0))
            os.close(fd)
            fd = readable
            if os.fstat(fd).st_dev != st.st_dev or os.fstat(fd).st_ino != st.st_ino or mount_id(fd) != self.row["id"]:
                raise Unavailable("storage descriptor changed during open")
            yield fd
            self.check()
        finally:
            os.close(fd)

    def close(self):
        os.close(self.fd)


def revision(fd):
    st = os.fstat(fd)
    return wire.fingerprint(st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def entry_revision(st):
    return wire.fingerprint(st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


class Service:
    def __init__(self, volumes, root_factory=Root):
        self.volumes = {volume.id: volume for volume in volumes}
        self.root_factory = root_factory

    def volumes_view(self):
        result = []
        for volume in self.volumes.values():
            row = {"id": volume.id, "label": volume.label, "available": False,
                   "can_unmount": (volume.allow_unmount
                                   and volume.root == f"/run/portclaim-storage/{volume.id}"),
                   "warning": (WARNING + " " + volume.caveat).strip()}
            try:
                root = self.root_factory(volume)
                try:
                    row.update(available=True, generation=root.generation, warning=root.warning)
                finally:
                    root.close()
            except (OSError, ValueError):
                row["warning"] += " Unavailable: verify the approved volume and read-only mount."
            result.append(row)
        return result

    @contextmanager
    def selected(self, request):
        volume = self.volumes.get(wire.identifier(request.get("volume")))
        if volume is None:
            raise ValueError("unknown storage volume")
        expected = wire.digest(request.get("generation"))
        root = self.root_factory(volume)
        try:
            if expected != root.generation:
                raise Unavailable("volume changed; refresh and select it again")
            yield root
        finally:
            root.close()

    def listing(self, root, request):
        offset = wire.integer(request.get("offset", 0), 0, wire.MAX_SCAN - 1)
        with root.open(request.get("path"), directory=True) as fd:
            version = revision(fd)
            if offset and wire.digest(request.get("revision")) != version:
                raise Unavailable("directory changed; browse it again")
            entries = []
            more = False
            with os.scandir(fd) as scan:
                for index, entry in enumerate(scan):
                    if index < offset:
                        continue
                    if len(entries) == min(wire.PAGE_SIZE, wire.MAX_SCAN - offset):
                        more = True
                        break
                    try:
                        wire.component(entry.name)
                        metadata = os.open(entry.name, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=fd)
                        try:
                            st = os.fstat(metadata)
                            if st.st_dev != os.fstat(root.fd).st_dev or mount_id(metadata) != root.row["id"]:
                                raise Unavailable("nested mount entry omitted")
                        finally:
                            os.close(metadata)
                    except (OSError, ValueError):
                        # Still advance the cursor for unsupported/vanished entries.
                        entries.append({"name": "", "type": "unavailable", "size": 0, "revision": ""})
                        continue
                    kind = ("directory" if stat.S_ISDIR(st.st_mode) else "file" if stat.S_ISREG(st.st_mode)
                            else "symlink" if stat.S_ISLNK(st.st_mode) else "special")
                    entries.append({"name": entry.name, "type": kind, "size": st.st_size,
                                    "revision": entry_revision(st)})
            if revision(fd) != version:
                raise Unavailable("directory changed during listing")
            next_offset = offset + len(entries)
            limited = more and next_offset >= wire.MAX_SCAN
            return {"entries": [e for e in entries if e["type"] != "unavailable"], "revision": version,
                    "next_offset": next_offset if more and not limited else None, "limited": limited}

    def serve(self, request, output):
        if type(request.get("version")) is not int or request["version"] != wire.VERSION:
            raise ValueError("unsupported storage protocol version")
        op = request.get("op")
        allowed = {"version", "op"}
        if op in {"list", "read"}:
            allowed |= {"volume", "generation", "path", "revision"}
            allowed |= {"offset"} if op == "list" else {"preview"}
        if set(request) - allowed:
            raise ValueError("unsupported storage request fields")
        def send(result):
            output.write(wire.encode({"version": wire.VERSION, "ok": True, "result": result}))
            output.flush()
        if op == "volumes":
            send(self.volumes_view())
        elif op in {"list", "read"}:
            with self.selected(request) as root:
                if op == "list":
                    send(self.listing(root, request))
                    return
                expected = wire.digest(request.get("revision"))
                preview = request.get("preview", False)
                if type(preview) is not bool:
                    raise ValueError("invalid preview flag")
                with root.open(request.get("path")) as fd:
                    if revision(fd) != expected:
                        raise Unavailable("file changed; browse it again")
                    size = wire.integer(os.fstat(fd).st_size, 0, wire.MAX_FILE)
                    length = min(size, wire.PREVIEW) if preview else size
                    send({"bytes": length, "size": size, "revision": expected})
                    checksum = hashlib.sha256()
                    remaining = length
                    deadline = time.monotonic() + 4 * 60 * 60
                    next_identity_check = 0.0
                    while remaining:
                        now = time.monotonic()
                        if now >= deadline:
                            raise TimeoutError("storage transfer duration exceeded")
                        if now >= next_identity_check:
                            root.check()
                            next_identity_check = now + .25
                        if revision(fd) != expected:
                            raise Unavailable("file changed during read")
                        data = os.read(fd, min(wire.CHUNK, remaining))
                        if not data:
                            raise EOFError("storage file ended early")
                        output.write(data)
                        output.flush()
                        checksum.update(data)
                        remaining -= len(data)
                    root.check()
                    if revision(fd) != expected:
                        raise Unavailable("file changed during read")
                    send({"sha256": checksum.hexdigest()})
        else:
            raise ValueError("unsupported storage operation; writes are not implemented")


def main():
    # Installed as a fixed forced command for a dedicated unprivileged SSH key.
    # No command-line/client override of the root policy; no inherited Python path
    # is needed when invoked with /usr/bin/python3 -I /.../storage.py.
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(10)
    try:
        if os.geteuid() == 0:
            raise ValueError("storage helper must run as a dedicated unprivileged account")
        service = Service(load_config())
        request = wire.decode(sys.stdin.buffer.readline(wire.MAX_REQUEST + 1), wire.MAX_REQUEST)
        # SSH disconnect/backpressure must not hold a helper forever. Reset only
        # on successful output progress; the outer duration also stays bounded.
        class Output:
            def write(self, data):
                sys.stdout.buffer.write(data)
            def flush(self):
                sys.stdout.buffer.flush()
                signal.alarm(30)
        service.serve(request, Output())
        return 0
    except (OSError, ValueError, TypeError, KeyError, EOFError):
        # Never echo paths, file bytes, policy, UUIDs or arbitrary exception text.
        print("Storage unavailable: check the selected volume, policy and read-only mount.", file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
