"""Storage boundary tests using temporary files; never mount or open a block device."""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hub import storage

wire = storage.wire
GENERATION = "a" * 64
UUID = "00000000-0000-0000-0000-000000000000"


class FixtureRoot(storage.Root):
    """Use production descriptor traversal, with only mount identity substituted."""
    def __init__(self, volume):
        self.volume = volume
        self.fd = os.open(volume.root, os.O_RDONLY | os.O_DIRECTORY)
        self.generation = GENERATION
        self.row = {"id": storage.mount_id(self.fd), "super_options": {"norecovery"}}

    def check(self):
        pass


@unittest.skipUnless(sys.platform == "linux", "Linux descriptor traversal")
class StorageServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "folder").mkdir()
        (self.root / "hello.txt").write_bytes(b"private synthetic fixture\n")
        self.volume = storage.Volume("media", "Test volume", str(self.root), UUID, "Recovery may be pending.")
        self.service = storage.Service([self.volume], root_factory=FixtureRoot)

    def request(self, op, **extra):
        return {"version": 1, "op": op, "volume": "media", "generation": GENERATION, "path": [], **extra}

    def call(self, request):
        output = io.BytesIO()
        self.service.serve(request, output)
        output.seek(0)
        return output

    def listing(self):
        return wire.decode(self.call(self.request("list")).readline())["result"]

    def test_volume_inventory_never_leaks_paths_or_uuid_and_keeps_caveat(self):
        rows = self.service.volumes_view()
        self.assertTrue(rows[0]["available"])
        self.assertIn("Recovery may be pending", rows[0]["warning"])
        self.assertIn("Journal replay is disabled", rows[0]["warning"])
        self.assertNotIn(str(self.root), json.dumps(rows))
        self.assertNotIn(UUID, json.dumps(rows))
        self.assertEqual(rows[0]["generation"], GENERATION)
        self.assertIs(rows[0]["can_unmount"], False)
        enabled = storage.Volume("media", "Test", str(self.root), UUID, allow_unmount=True)
        self.assertIs(storage.Service([enabled], root_factory=FixtureRoot).volumes_view()[0]["can_unmount"], False)
        enabled = storage.Volume("media", "Test", "/run/portclaim-storage/media", UUID, allow_unmount=True)
        root = SimpleNamespace(generation=GENERATION, warning="Read only", close=lambda: None)
        self.assertIs(storage.Service([enabled], root_factory=lambda _: root).volumes_view()[0]["can_unmount"], True)

    def test_unavailable_volume_is_not_activated(self):
        service = storage.Service([self.volume], root_factory=lambda _: (_ for _ in ()).throw(OSError()))
        row = service.volumes_view()[0]
        self.assertFalse(row["available"])
        self.assertNotIn("generation", row)
        self.assertIn("Recovery may be pending", row["warning"])

    def test_directory_listing_and_verified_exact_file_stream(self):
        rows = self.listing()["entries"]
        row = next(row for row in rows if row["name"] == "hello.txt")
        self.assertEqual(row["type"], "file")
        stream = self.call(self.request("read", path=[row["name"]], revision=row["revision"]))
        header = wire.decode(stream.readline())["result"]
        data = stream.read(header["bytes"])
        trailer = wire.decode(stream.readline())["result"]
        self.assertEqual(data, b"private synthetic fixture\n")
        self.assertEqual(trailer["sha256"], storage.hashlib.sha256(data).hexdigest())
        self.assertEqual(stream.read(), b"")

    def test_no_write_op_and_no_unknown_fields(self):
        for request in ({"version": 1, "op": "upload"}, {"version": 1, "op": "unmount"}, self.request("list", root="/"),
                        {"version": True, "op": "volumes"}, self.request("read", preview=1)):
            with self.subTest(request=request), self.assertRaises((ValueError, OSError)):
                self.call(request)
        self.assertEqual((self.root / "hello.txt").read_bytes(), b"private synthetic fixture\n")

    def test_generation_file_revision_and_directory_revision_must_match(self):
        for request in (self.request("list", generation="b" * 64),
                        self.request("list", offset=1, revision="b" * 64),
                        self.request("read", path=["hello.txt"], revision="b" * 64)):
            with self.assertRaises(OSError):
                self.call(request)

    def test_traversal_symlinks_fifo_and_directory_reads_refused(self):
        (self.root / "escape").symlink_to("/etc")
        (self.root / "filelink").symlink_to(self.root / "hello.txt")
        os.mkfifo(self.root / "pipe")
        for path in ([".."], ["/etc"], ["folder/.."], ["escape", "passwd"], ["filelink"], ["pipe"], []):
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                self.call(self.request("read", path=path, revision="b" * 64))
        rows = self.listing()["entries"]
        self.assertEqual(next(r for r in rows if r["name"] == "filelink")["type"], "symlink")

    def test_nested_mount_rejected_even_on_same_device(self):
        root = FixtureRoot(self.volume)
        try:
            with patch.object(storage, "mount_id", return_value=root.row["id"] + 1), \
                 patch.object(os, "open", wraps=os.open) as opened:
                with self.assertRaises(OSError), root.open(["hello.txt"]):
                    self.fail("nested mount opened")
                self.assertTrue(opened.call_args_list)
                self.assertTrue(all(call.args[1] & os.O_PATH for call in opened.call_args_list),
                                "outside leaf received a read/driver open before validation")
        finally:
            root.close()

    def test_preview_is_bounded_and_full_size_reported(self):
        (self.root / "large.txt").write_bytes(b"a" * (wire.PREVIEW + 10))
        row = next(r for r in self.listing()["entries"] if r["name"] == "large.txt")
        stream = self.call(self.request("read", path=[row["name"]], revision=row["revision"], preview=True))
        header = wire.decode(stream.readline())["result"]
        self.assertEqual(header["bytes"], wire.PREVIEW)
        self.assertEqual(header["size"], wire.PREVIEW + 10)
        self.assertEqual(len(stream.read(header["bytes"])), wire.PREVIEW)
        self.assertIn("sha256", wire.decode(stream.readline())["result"])

    def test_paging_is_bounded_and_disallows_stale_directory(self):
        for n in range(8):
            (self.root / f"entry-{n}").touch()
        with patch.object(wire, "PAGE_SIZE", 3), patch.object(wire, "MAX_SCAN", 7):
            first = self.listing()
            self.assertEqual(len(first["entries"]), 3)
            self.assertEqual(first["next_offset"], 3)
            last = wire.decode(self.call(self.request("list", offset=6, revision=first["revision"])).readline())["result"]
            self.assertEqual(len(last["entries"]), 1)
            self.assertIsNone(last["next_offset"])
            self.assertTrue(last["limited"])
            (self.root / "new").touch()
            with self.assertRaises(OSError):
                self.call(self.request("list", offset=3, revision=first["revision"]))

    def test_receiver_to_real_helper_service_over_local_pipes(self):
        from client.common.storage import StorageClient
        from client.common import storage as client_storage
        with tempfile.TemporaryDirectory() as other:
            script = Path(other) / "helper.py"
            repo = Path(__file__).resolve().parents[2]
            script.write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(repo)!r})\n"
                "from hub import storage\n"
                "from tests.hub.test_storage import FixtureRoot, UUID\n"
                "volume = storage.Volume('media', 'Test', sys.argv[1], UUID)\n"
                "service = storage.Service([volume], root_factory=FixtureRoot)\n"
                "request = storage.wire.decode(sys.stdin.buffer.readline())\n"
                "service.serve(request, sys.stdout.buffer)\n"
            )
            command = [sys.executable, "-I", str(script), str(self.root)]
            client = StorageClient("synthetic-only")
            with patch.object(client_storage, "ssh_command", return_value=command):
                volume = client.volumes()[0]
                listing = client.list(volume["id"], volume["generation"], [])
                entry = next(e for e in listing["entries"] if e["name"] == "hello.txt")
                preview = client.preview(volume["id"], volume["generation"], [entry["name"]], entry["revision"])
                self.assertEqual(preview["text"], "private synthetic fixture\n")
                destination = Path(other) / "imported.txt"
                result = client.download(volume["id"], volume["generation"], [entry["name"]], entry["revision"], destination)
            self.assertEqual(destination.read_bytes(), (self.root / "hello.txt").read_bytes())
            self.assertEqual(result["bytes"], destination.stat().st_size)

    def test_disconnect_mid_read_never_emits_success_trailer(self):
        row = next(r for r in self.listing()["entries"] if r["name"] == "hello.txt")
        root = FixtureRoot(self.volume)
        service = storage.Service([self.volume], root_factory=lambda _: root)
        output = io.BytesIO()
        with patch.object(root, "check", side_effect=[None, OSError("disconnected")]):
            with self.assertRaises(OSError):
                service.serve(self.request("read", path=[row["name"]], revision=row["revision"]), output)
        self.assertNotIn(b"sha256", output.getvalue())


@unittest.skipUnless(sys.platform == "linux", "Linux mount identity")
class MountPolicyTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            rootpath = base / "mount"; rootpath.mkdir()
            usb = base / "usb"; usb.mkdir()
            (usb / "idVendor").write_text("1234")
            (usb / "devnum").write_text("7")
            partition = usb / "disk" / "partition"; partition.mkdir(parents=True)
            volume = storage.Volume("media", "Test", str(rootpath), UUID)
            original_stat, original_resolve = os.stat, Path.resolve
            stdev = original_stat(rootpath).st_dev
            fd = os.open(rootpath, os.O_RDONLY | os.O_DIRECTORY)
            identifier = storage.mount_id(fd); os.close(fd)
            row = {"id": identifier, "target": str(rootpath), "root": "/", "fstype": "ext4",
                   "device": f"{os.major(stdev)}:{os.minor(stdev)}", "options": {"ro", "nodev", "nosuid", "noexec"},
                   "super_options": {"ro", "norecovery"}}
            def fake_stat(path, *args, **kwargs):
                if str(path) == "/dev/disk/by-uuid/" + UUID:
                    return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=stdev)
                return original_stat(path, *args, **kwargs)
            def resolve(path, *args, **kwargs):
                if str(path).startswith("/sys/dev/block/"):
                    return partition
                return original_resolve(path, *args, **kwargs)
            with patch.object(storage, "mount_rows", return_value=[row]), patch.object(os, "stat", side_effect=fake_stat), \
                 patch.object(Path, "resolve", resolve), patch.object(os, "fstatvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
                yield volume, row, usb

    def test_real_identity_checks_require_readonly_pinned_usb_mount(self):
        with self.fixture() as (volume, row, usb):
            root = storage.Root(volume)
            try:
                root.check()
                (usb / "devnum").write_text("8")
                with self.assertRaises(OSError):root.check()
            finally:root.close()
            for key, value in (("root", "/subdir"), ("fstype", "btrfs"), ("super_options", {"rw"}),
                               ("options", {"ro"}), ("device", "8:999")):
                old = row[key]; row[key] = value
                try:
                    with self.subTest(key=key), self.assertRaises(OSError):storage.Root(volume)
                finally:row[key] = old

    def test_missing_mount_and_uuid_reuse_fail_closed(self):
        with self.fixture() as (volume, row, usb):
            with patch.object(storage, "mount_rows", return_value=[]), self.assertRaises(OSError):
                storage.Root(volume)
            # Configured UUID now resolves to a different block device.
            original = os.stat
            def moved(path, *args, **kwargs):
                if str(path).startswith("/dev/disk/by-uuid/"):
                    return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=0)
                return original(path, *args, **kwargs)
            with patch.object(os, "stat", side_effect=moved), self.assertRaises(OSError):
                storage.Root(volume)

    def test_main_refuses_root_before_loading_policy(self):
        from contextlib import redirect_stderr
        with patch.object(os, "geteuid", return_value=0), patch.object(storage, "load_config") as config, \
             patch.object(storage.signal, "signal"), patch.object(storage.signal, "alarm"), redirect_stderr(io.StringIO()):
            self.assertEqual(storage.main(), 1)
        config.assert_not_called()

    def test_production_protocol_import_cannot_use_sibling_proto_directory(self):
        import shutil
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            installed = base / "usb-loom"; installed.mkdir()
            sibling = base / "proto"; sibling.mkdir()
            shutil.copyfile(Path(storage.__file__), installed / "storage.py")
            shutil.copyfile(Path(storage.wire.__file__), installed / "storage_wire.py")
            (sibling / "storage_wire.py").write_text("raise RuntimeError('sibling protocol executed')\n")
            script = installed / "probe.py"
            script.write_text("import sys\nfrom pathlib import Path\n"
                              "sys.path.insert(0, str(Path(__file__).parent))\nimport storage\n"
                              "assert storage.PROTOCOL_DIR == Path(__file__).parent\n"
                              "assert Path(storage.wire.__file__).parent == Path(__file__).parent\n")
            subprocess.run([sys.executable, "-I", str(script)], check=True, capture_output=True, timeout=5)
            (installed / "storage_wire.py").unlink()
            # Absence must fail, not fall back to the poisoned sibling directory.
            result = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b"sibling protocol executed", result.stderr)

    def test_policy_must_be_root_owned_nonwritable_regular_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            data = {"volumes": [{"id": "media", "label": "Test", "root": "/run/storage/media", "uuid": UUID}]}
            path.write_text(json.dumps(data)); path.chmod(0o600)
            real = os.fstat
            def metadata(fd):
                st = real(fd)
                return SimpleNamespace(st_uid=0, st_mode=st.st_mode, st_size=st.st_size)
            with patch.object(os, "fstat", side_effect=metadata):
                self.assertEqual(storage.load_config(path)[0].id, "media")
                self.assertFalse(storage.load_config(path)[0].allow_unmount)
                for flag in (True, False):
                    data["volumes"][0]["allow_unmount"] = flag; path.write_text(json.dumps(data))
                    self.assertIs(storage.load_config(path)[0].allow_unmount, flag)
                for flag in (None, 1, "true"):
                    data["volumes"][0]["allow_unmount"] = flag; path.write_text(json.dumps(data))
                    with self.assertRaises(ValueError):storage.load_config(path)
                data["volumes"][0]["allow_unmount"] = False; path.write_text(json.dumps(data))
                path.chmod(0o622)
                with self.assertRaises(ValueError):storage.load_config(path)
                path.chmod(0o644)
                with self.assertRaises(ValueError):storage.load_config(path)
                path.chmod(0o600)
                data["volumes"] *= 2; path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):storage.load_config(path)
            link = Path(temp) / "link"; link.symlink_to(path)
            with self.assertRaises(OSError):storage.load_config(link)
