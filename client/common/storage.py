"""Explicit, encrypted read-only storage client. No drivers, mounts or auto-connect."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time

from proto import storage_wire as wire

REMOTE = "/usr/bin/python3 -I /usr/local/lib/usb-loom/storage.py"
UNMOUNT_REMOTE = "/usr/bin/python3 -I /usr/local/lib/usb-loom/storage_unmount.py"
UNMOUNT_STATUSES = frozenset({"unmounted", "busy", "stale", "unavailable", "denied", "failed"})


def ssh_command(alias, *, unmount=False):
    wire.identifier(alias)  # An explicitly configured SSH alias, not a URL/shell fragment.
    executable = shutil.which("ssh")
    if not executable:
        raise OSError("OpenSSH client is required for encrypted storage access")
    return [executable, "-T", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2",
            "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "ClearAllForwardings=yes",
            "-o", "PermitLocalCommand=no", "-o", "RemoteCommand=none", "-o", "ControlMaster=no",
            "-o", "ControlPath=none", alias, UNMOUNT_REMOTE if unmount else REMOTE]


class PipeReader:
    """Cross-platform bounded pipe pump; no select() assumption on Windows pipes."""
    def __init__(self, stream, cancel, timeout=20):
        self.stream, self.cancel, self.timeout = stream, cancel, timeout
        self.queue = queue.Queue(maxsize=2)
        self.stop = threading.Event()
        self.buffer = bytearray()
        self.eof = False
        self.deadline = time.monotonic() + 4 * 60 * 60
        self.thread = threading.Thread(target=self._pump, daemon=True, name="storage-ssh-read")
        self.thread.start()

    def _pump(self):
        try:
            while not self.stop.is_set():
                data = self.stream.read(wire.CHUNK)
                while not self.stop.is_set():
                    try:
                        self.queue.put(data, timeout=.1)
                        break
                    except queue.Full:
                        continue
                if not data:
                    return
        except (OSError, ValueError):
            while not self.stop.is_set():
                try:
                    self.queue.put(b"", timeout=.1)
                    return
                except queue.Full:
                    continue

    def check(self):
        if self.cancel.is_set():
            raise OSError("storage operation cancelled")
        if time.monotonic() >= self.deadline:
            raise TimeoutError("storage transfer duration exceeded")

    def _more(self):
        limit = time.monotonic() + self.timeout
        while True:
            self.check()
            try:
                data = self.queue.get(timeout=.1)
                break
            except queue.Empty:
                if time.monotonic() >= limit:
                    raise TimeoutError("storage endpoint stopped responding")
        if not data:
            self.eof = True
        self.buffer.extend(data)

    def line(self):
        while b"\n" not in self.buffer:
            self.check()
            if self.eof:
                raise OSError("SSH storage endpoint unavailable or response incomplete; check key, host and policy")
            if len(self.buffer) > wire.MAX_RESPONSE:
                raise ValueError("oversized storage response")
            self._more()
        end = self.buffer.index(b"\n") + 1
        raw = bytes(self.buffer[:end])
        del self.buffer[:end]
        result = wire.decode(raw)
        if type(result.get("version")) is not int or result["version"] != wire.VERSION or result.get("ok") is not True:
            raise ValueError("invalid storage response envelope")
        return result.get("result")

    def read(self, count):
        self.check()
        while not self.buffer and not self.eof:
            self._more()
        if not self.buffer and self.eof:
            raise OSError("storage file transfer ended early")
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def finish(self):
        while not self.eof:
            self.check()
            if self.buffer:
                raise ValueError("unexpected trailing storage response")
            self._more()
        if self.buffer:
            raise ValueError("unexpected trailing storage response")


class StorageClient:
    def __init__(self, alias, unmount_alias=None):
        self.alias = wire.identifier(alias)
        self.unmount_alias = wire.identifier(unmount_alias) if unmount_alias else None
        if self.unmount_alias == self.alias:
            raise ValueError("unmount requires a separate dedicated SSH alias/key")

    @contextmanager
    def _request(self, request, cancel=None, *, unmount=False):
        cancel = cancel if cancel is not None else threading.Event()
        if cancel.is_set():
            raise OSError("storage operation cancelled")
        raw = wire.encode({"version": wire.VERSION, **request}, wire.MAX_REQUEST)
        if unmount:
            if not self.unmount_alias:
                raise ValueError("no dedicated unmount SSH alias is configured")
            command = ssh_command(self.unmount_alias, unmount=True)
        else:
            command = ssh_command(self.alias)
        kwargs = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL,
                  "bufsize": 0}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(command, **kwargs)
        reader = None
        try:
            reader = PipeReader(proc.stdout, cancel)
            if proc.stdin.write(raw) != len(raw):
                raise OSError("storage request could not be sent completely")
            proc.stdin.close()
            yield reader
            reader.finish()
            if proc.wait(timeout=3) != 0:
                raise OSError("SSH storage helper failed; verify operation state before retrying")
        finally:
            if reader is not None:
                reader.stop.set()
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    stream.close()
            if reader is not None:
                reader.thread.join(timeout=2)

    @staticmethod
    def _selection(volume, generation, path):
        return {"volume": wire.identifier(volume), "generation": wire.digest(generation),
                "path": wire.path_parts(path)}

    def volumes(self, cancel=None):
        with self._request({"op": "volumes"}, cancel) as reader:
            result = reader.line()
            if not isinstance(result, list) or len(result) > 8:
                raise ValueError("invalid storage volume list")
            seen = set()
            for row in result:
                if not isinstance(row, dict):
                    raise ValueError("invalid storage volume")
                identifier = wire.identifier(row.get("id"))
                if identifier in seen:
                    raise ValueError("duplicate storage volume")
                seen.add(identifier)
                for key, limit in (("label", 128), ("warning", 1024)):
                    value = row.get(key)
                    if not isinstance(value, str) or not value or len(value) > limit or not value.isprintable():
                        raise ValueError("invalid storage volume metadata")
                if type(row.get("available")) is not bool:
                    raise ValueError("invalid storage availability")
                if type(row.get("can_unmount", False)) is not bool:
                    raise ValueError("invalid storage unmount capability")
                if row["available"]:
                    wire.digest(row.get("generation"))
            return result

    def unmount(self, volume, generation, cancel=None):
        """Explicit operation on a separate restricted key; no automatic retry.

        Cancellation/lost SSH may occur after the hub acted. Only a complete,
        validated response and successful SSH exit establish the reported result.
        """
        request = {"op": "unmount", "volume": wire.identifier(volume),
                   "generation": wire.digest(generation)}
        with self._request(request, cancel, unmount=True) as reader:
            result = reader.line()
            if (not isinstance(result, dict) or set(result) != {"status", "volume", "generation"}
                    or not isinstance(result["status"], str) or result["status"] not in UNMOUNT_STATUSES
                    or result["volume"] != volume or result["generation"] != generation):
                raise ValueError("invalid unmount result; refresh to check actual volume state")
        return result

    def list(self, volume, generation, path, offset=0, revision=None, cancel=None):
        request = {"op": "list", **self._selection(volume, generation, path),
                   "offset": wire.integer(offset, 0, wire.MAX_SCAN - 1)}
        if offset:
            request["revision"] = wire.digest(revision)
        with self._request(request, cancel) as reader:
            result = reader.line()
            if not isinstance(result, dict) or not isinstance(result.get("entries"), list) or len(result["entries"]) > wire.PAGE_SIZE:
                raise ValueError("invalid storage listing")
            wire.digest(result.get("revision"))
            next_offset = result.get("next_offset")
            if next_offset is not None:
                wire.integer(next_offset, offset + 1, wire.MAX_SCAN - 1)
            if type(result.get("limited")) is not bool:
                raise ValueError("invalid storage listing limit")
            for row in result["entries"]:
                if not isinstance(row, dict):
                    raise ValueError("invalid storage entry")
                wire.component(row.get("name"))
                wire.digest(row.get("revision"))
                wire.integer(row.get("size"), 0, 2**63 - 1)
                if row.get("type") not in {"file", "directory", "symlink", "special"}:
                    raise ValueError("invalid storage entry type")
            return result

    def _read(self, volume, generation, path, revision, sink, preview=False, cancel=None, progress=None):
        request = {"op": "read", **self._selection(volume, generation, path),
                   "revision": wire.digest(revision), "preview": preview}
        if not path:
            raise ValueError("select a file explicitly")
        with self._request(request, cancel) as reader:
            header = reader.line()
            if not isinstance(header, dict) or header.get("revision") != revision:
                raise ValueError("unexpected storage file revision")
            size = wire.integer(header.get("size"), 0, wire.MAX_FILE)
            length = wire.integer(header.get("bytes"), 0, wire.PREVIEW if preview else wire.MAX_FILE)
            if length != (min(size, wire.PREVIEW) if preview else size):
                raise ValueError("unexpected storage transfer size")
            checksum = hashlib.sha256()
            done = 0
            if progress:
                progress(done, length)
            while done < length:
                data = reader.read(min(wire.CHUNK, length - done))
                sink(data)
                checksum.update(data)
                done += len(data)
                if progress:
                    progress(done, length)
            trailer = reader.line()
            if not isinstance(trailer, dict) or wire.digest(trailer.get("sha256")) != checksum.hexdigest():
                raise ValueError("storage checksum mismatch; download not published")
            reader.check()
        return {"bytes": length, "size": size, "sha256": checksum.hexdigest()}

    def preview(self, volume, generation, path, revision, cancel=None):
        data = bytearray()
        result = self._read(volume, generation, path, revision, data.extend, preview=True, cancel=cancel)
        try:
            # A byte-bounded prefix may end in a partial UTF-8 codepoint.
            import codecs
            text = codecs.getincrementaldecoder("utf-8")().decode(data, final=result["size"] <= wire.PREVIEW)
        except UnicodeError:
            raise ValueError("not a UTF-8 text preview; download explicitly for local inspection") from None
        if any(not char.isprintable() and char not in "\r\n\t" for char in text):
            raise ValueError("binary/control content is not shown as text; download explicitly")
        return {"text": text, "truncated": result["size"] > wire.PREVIEW}

    def download(self, volume, generation, path, revision, destination, cancel=None, progress=None):
        # Destination must be explicitly confirmed locally, even when the UI
        # suggests a sanitized basename. No overwrite or partial final file:
        # a same-directory hard link atomically publishes a verified copy.
        destination = Path(destination).absolute()
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("choose a new local filename; storage downloads never overwrite")
        fd, temporary = tempfile.mkstemp(prefix=".portclaim-import-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                result = self._read(volume, generation, path, revision, output.write, cancel=cancel, progress=progress)
                output.flush()
                os.fsync(output.fileno())
            if cancel is not None and cancel.is_set():
                raise OSError("storage operation cancelled")
            try:
                os.link(temporary, destination)
            except FileExistsError:
                raise FileExistsError("destination appeared during download; existing file preserved") from None
            except OSError:
                raise OSError("cannot publish safely: destination filesystem must support same-directory hard links") from None
            return {"bytes": result["bytes"], "sha256": result["sha256"]}
        finally:
            os.unlink(temporary)
