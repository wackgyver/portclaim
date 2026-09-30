"""Storage client tests: synthetic byte pipes/child processes only, never SSH."""
from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from client.common import storage
from proto import storage_wire as wire

GENERATION = "a" * 64
REVISION = "b" * 64


def response(value):
    return wire.encode({"version": 1, "ok": True, "result": value})


def payload(data, size=None, checksum=None):
    return (response({"bytes": len(data), "size": len(data) if size is None else size, "revision": REVISION})
            + data + response({"sha256": checksum or hashlib.sha256(data).hexdigest()}))


@contextmanager
def exchange(data, cancel=None):
    reader = storage.PipeReader(io.BytesIO(data), cancel or threading.Event(), timeout=.2)
    try:
        yield reader
        reader.finish()
    finally:
        reader.stop.set()
        reader.thread.join(timeout=1)


class StorageWireTests(unittest.TestCase):
    def test_paths_identifiers_and_integer_bounds(self):
        for value in ([".."], ["/tmp"], ["a/b"], ["a\x00b"], ["\udcff"], ["a"] * 33, "file"):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):wire.path_parts(value)
        for value in ("-oProxyCommand=oops", "user@host", "host;echo", "", "/path"):
            with self.assertRaises(ValueError):wire.identifier(value)
        for value in (True, -1, 2**64, 1.5):
            with self.assertRaises(ValueError):wire.integer(value, 0, 100)
        self.assertEqual(wire.path_parts([".hidden", "two words.txt"]), [".hidden", "two words.txt"])

    def test_json_envelope_and_bounds(self):
        for value in (b"{}", b"[]\n", b"{" + b"x" * wire.MAX_RESPONSE + b"}\n"):
            with self.assertRaises(ValueError):wire.decode(value)
        with self.assertRaises(ValueError):wire.encode({"v": "x" * 100}, maximum=20)

    def test_ssh_is_explicit_fixed_command_strict_and_no_forwarding(self):
        with patch.object(storage.shutil, "which", return_value="/usr/bin/ssh"):
            command = storage.ssh_command("portclaim-storage")
        self.assertEqual(command[-2:], ["portclaim-storage", storage.REMOTE])
        for option in ("StrictHostKeyChecking=yes", "BatchMode=yes", "ClearAllForwardings=yes",
                       "ForwardAgent=no", "ForwardX11=no", "PermitLocalCommand=no", "ControlPath=none"):
            self.assertIn(option, command)
        with patch.object(storage.subprocess, "Popen") as popen:
            storage.StorageClient("portclaim-storage")
        popen.assert_not_called()


class StorageClientTests(unittest.TestCase):
    def setUp(self):
        self.client = storage.StorageClient("portclaim-storage")

    def mocked(self, data):
        return patch.object(self.client, "_request", side_effect=lambda _request, cancel=None: exchange(data, cancel))

    def test_volume_warning_and_explicit_selection(self):
        row = {"id": "media", "label": "Test", "available": True,
               "generation": GENERATION, "warning": "Read-only; pending recovery."}
        with self.mocked(response([row])):
            self.assertEqual(self.client.volumes()[0], row)
        for bad in ([row, row], [{**row, "generation": "invalid"}], [{**row, "warning": ""}], [{**row, "available": 1}]):
            with self.mocked(response(bad)), self.assertRaises(ValueError):self.client.volumes()

    def test_unmount_requires_separate_explicit_alias_and_validated_selection(self):
        with patch.object(storage.subprocess, "Popen") as popen:
            with self.assertRaises(ValueError):self.client.unmount("media", GENERATION)
            with self.assertRaises(ValueError):storage.StorageClient("read", "read")
            with self.assertRaises(ValueError):storage.StorageClient("read", "root@host")
            configured = storage.StorageClient("read", "unmount-only")
            with self.assertRaises(ValueError):configured.unmount("../disk", GENERATION)
            with self.assertRaises(ValueError):configured.unmount("media", "not-a-generation")
            cancel = threading.Event(); cancel.set()
            with self.assertRaises(OSError):configured.unmount("media", GENERATION, cancel=cancel)
        popen.assert_not_called()
        with patch.object(storage.shutil, "which", return_value="/usr/bin/ssh"):
            command = storage.ssh_command("unmount-only", unmount=True)
        self.assertEqual(command[-2:], ["unmount-only", storage.UNMOUNT_REMOTE])
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("ClearAllForwardings=yes", command)

    def test_unmount_result_must_be_complete_recognized_and_bound_to_selection(self):
        self.client = storage.StorageClient("read", "unmount-only")
        good = {"status": "unmounted", "volume": "media", "generation": GENERATION}
        for status in storage.UNMOUNT_STATUSES:
            result = {**good, "status": status}
            with patch.object(self.client, "_request", return_value=exchange(response(result))) as request:
                self.assertEqual(self.client.unmount("media", GENERATION), result)
            self.assertEqual(request.call_args.args[0], {"op": "unmount", "volume": "media", "generation": GENERATION})
            self.assertTrue(request.call_args.kwargs["unmount"])
        for result in ({**good, "status": True}, {**good, "status": "success"},
                       {**good, "volume": "other"}, {**good, "generation": "c" * 64},
                       {**good, "path": "/etc"}, {}, None):
            with patch.object(self.client, "_request", return_value=exchange(response(result))), self.assertRaises(ValueError):
                self.client.unmount("media", GENERATION)
        with patch.object(self.client, "_request", return_value=exchange(response(good) + b"unexpected")), self.assertRaises(ValueError):
            self.client.unmount("media", GENERATION)

    def test_volume_unmount_capability_is_optional_but_must_be_boolean(self):
        row = {"id": "media", "label": "Test", "available": True,
               "generation": GENERATION, "warning": "Read only"}
        for flag in (True, False):
            with self.mocked(response([{**row, "can_unmount": flag}])):
                self.assertIs(self.client.volumes()[0]["can_unmount"], flag)
        for flag in (None, 1, "true"):
            with self.mocked(response([{**row, "can_unmount": flag}])), self.assertRaises(ValueError):self.client.volumes()

    def test_listing_rejects_remote_path_escape_and_invalid_offsets(self):
        entry = {"name": "../oops", "type": "file", "size": 10, "revision": REVISION}
        result = {"entries": [entry], "revision": REVISION, "next_offset": None, "limited": False}
        with self.mocked(response(result)), self.assertRaises(ValueError):
            self.client.list("media", GENERATION, [])
        result["entries"] = []
        result["next_offset"] = 0
        with self.mocked(response(result)), self.assertRaises(ValueError):
            self.client.list("media", GENERATION, [])
        result["next_offset"] = None
        with self.mocked(response(result)) as request:
            self.client.list("media", GENERATION, ["folder"], 1, REVISION)
        self.assertEqual(request.call_args.args[0]["path"], ["folder"])
        self.assertEqual(request.call_args.args[0]["generation"], GENERATION)
        self.assertEqual(request.call_args.args[0]["revision"], REVISION)

    def test_text_preview_is_bounded_and_never_executes_or_decodes_binary(self):
        with self.mocked(payload(b"hello\n")):
            self.assertEqual(self.client.preview("media", GENERATION, ["test"], REVISION),
                             {"text": "hello\n", "truncated": False})
        for data in (b"\x00binary", b"\xff\xd8image", b"\x1b[2J"):
            with self.mocked(payload(data)), self.assertRaises(ValueError):
                self.client.preview("media", GENERATION, ["test"], REVISION)
        data = b"a" * (wire.PREVIEW - 1) + b"\xc3"
        with self.mocked(payload(data, size=wire.PREVIEW + 1)):
            result = self.client.preview("media", GENERATION, ["test"], REVISION)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["text"]), wire.PREVIEW - 1)

    def test_download_verifies_checksum_and_publishes_new_private_file(self):
        data = b"synthetic content\n" * 10000
        with tempfile.TemporaryDirectory() as temp, self.mocked(payload(data)):
            dest = Path(temp) / "copy.bin"
            result = self.client.download("media", GENERATION, ["test"], REVISION, dest)
            self.assertEqual(dest.read_bytes(), data)
            self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(list(Path(temp).iterdir()), [dest])
            if os.name != "nt":self.assertEqual(dest.stat().st_mode & 0o777, 0o600)

    def test_existing_destination_and_late_collision_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "existing"; dest.write_text("keep")
            with patch.object(self.client, "_read") as read, self.assertRaises(FileExistsError):
                self.client.download("media", GENERATION, ["test"], REVISION, dest)
            read.assert_not_called()
            dest.unlink()
            def collision(done, total):
                if done == total:dest.write_text("someone else's file")
            with self.mocked(payload(b"new")), self.assertRaises(FileExistsError):
                self.client.download("media", GENERATION, ["test"], REVISION, dest, progress=collision)
            self.assertEqual(dest.read_text(), "someone else's file")
            self.assertEqual(list(Path(temp).iterdir()), [dest])

    def test_malformed_hash_truncation_cancel_and_unsupported_publish_clean_partials(self):
        bad = [payload(b"data", checksum="c" * 64), payload(b"data")[:-12],
               response({"bytes": wire.MAX_FILE + 1, "size": wire.MAX_FILE + 1, "revision": REVISION}),
               payload(b"data") + b"unexpected"]
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "copy"
            for data in bad:
                with self.mocked(data), self.assertRaises((ValueError, OSError)):
                    self.client.download("media", GENERATION, ["test"], REVISION, dest)
                self.assertEqual(list(Path(temp).iterdir()), [])
            cancel = threading.Event()
            with self.mocked(payload(b"a" * wire.CHUNK * 3)), self.assertRaises(OSError):
                self.client.download("media", GENERATION, ["test"], REVISION, dest,
                                     cancel=cancel, progress=lambda done, total: cancel.set() if done else None)
            self.assertEqual(list(Path(temp).iterdir()), [])
            with self.mocked(payload(b"data")), patch.object(os, "link", side_effect=OSError()), self.assertRaises(OSError):
                self.client.download("media", GENERATION, ["test"], REVISION, dest)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_pipe_timeout_and_cancel_are_bounded(self):
        reader = storage.PipeReader(io.BytesIO(b""), threading.Event(), timeout=.05)
        try:
            with self.assertRaises(OSError):reader.line()
        finally:
            reader.stop.set(); reader.thread.join(timeout=1)
        cancel = threading.Event(); cancel.set()
        with patch.object(storage.subprocess, "Popen") as popen, self.assertRaises(OSError):
            self.client.volumes(cancel)
        popen.assert_not_called()


class RealPipeTests(unittest.TestCase):
    def test_local_child_binary_protocol_and_cleanup_without_ssh(self):
        data = b"bytes, not a media recording\n" * 5000
        raw = payload(data)
        # Use an actual child/pipe lifecycle, replacing only the SSH executable.
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / "fixture"; fixture.write_bytes(raw)
            code = "import pathlib,sys; sys.stdin.buffer.readline(); sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes()); sys.stdout.buffer.flush()"
            command = [sys.executable, "-u", "-c", code, str(fixture)]
            children = []
            original = subprocess.Popen
            def spawn(*args, **kwargs):
                proc = original(*args, **kwargs); children.append(proc); return proc
            dest = Path(temp) / "result"
            with patch.object(storage, "ssh_command", return_value=command), patch.object(storage.subprocess, "Popen", side_effect=spawn):
                storage.StorageClient("test").download("media", GENERATION, ["test"], REVISION, dest)
            self.assertEqual(dest.read_bytes(), data)
            self.assertEqual(len(children), 1)
            self.assertEqual(children[0].returncode, 0)
            self.assertTrue(children[0].stdout.closed)

    def test_unmount_checks_real_child_exit_not_just_success_json(self):
        result = {"status": "unmounted", "volume": "media", "generation": GENERATION}
        for code in (0, 1):
            command = [sys.executable, "-u", "-c", "import sys; sys.stdin.buffer.readline(); "
                       f"sys.stdout.buffer.write({response(result)!r}); sys.stdout.buffer.flush(); sys.exit({code})"]
            with patch.object(storage, "ssh_command", return_value=command) as ssh:
                client = storage.StorageClient("read", "unmount-only")
                if code:
                    with self.assertRaises(OSError):client.unmount("media", GENERATION)
                else:self.assertEqual(client.unmount("media", GENERATION), result)
            ssh.assert_called_once_with("unmount-only", unmount=True)

    def test_stalled_child_is_terminated_and_partial_removed(self):
        command = [sys.executable, "-u", "-c", "import time;time.sleep(60)"]
        original_reader = storage.PipeReader
        def quick_reader(stream, cancel):return original_reader(stream, cancel, timeout=.15)
        children = []; original_popen = subprocess.Popen
        def spawn(*args, **kwargs):
            proc = original_popen(*args, **kwargs); children.append(proc); return proc
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(storage, "ssh_command", return_value=command), patch.object(storage, "PipeReader", side_effect=quick_reader), \
                 patch.object(storage.subprocess, "Popen", side_effect=spawn), self.assertRaises(TimeoutError):
                storage.StorageClient("test").download("media", GENERATION, ["test"], REVISION, Path(temp) / "result")
            self.assertEqual(list(Path(temp).iterdir()), [])
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue(children[0].stdout.closed)
