"""Unmount authority tests: synthetic identity/locks only, NEVER a real unmount."""
from contextlib import contextmanager, redirect_stderr
import errno
import io
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from hub import storage_unmount as control

GENERATION = "a" * 64
UUID = "00000000-0000-0000-0000-000000000000"


@unittest.skipUnless(sys.platform == "linux", "Linux unmount control")
class UnmountTests(unittest.TestCase):
    def setUp(self):
        self.syscall = Mock(side_effect=AssertionError("a test attempted a real unmount"))
        self.patch = patch.object(control, "normal_unmount", self.syscall)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.volume = control.storage.Volume("media", "Test", str(control.BASE / "media"), UUID,
                                             allow_unmount=True)
        self.row = {"id": 123, "target": self.volume.root, "device": "8:17", "root": "/",
                    "fstype": "ext4", "options": {"ro", "nodev", "nosuid", "noexec"},
                    "super_options": {"ro", "norecovery"}}
        self.root = SimpleNamespace(generation=GENERATION, row=self.row, check=Mock(), close=Mock())
        self.request = {"version": 1, "op": "unmount", "volume": "media", "generation": GENERATION}

    @contextmanager
    def mounted(self, snapshots=None):
        with patch.object(control.storage, "Root", return_value=self.root), \
             patch.object(control.storage, "mount_rows", side_effect=snapshots or [[self.row], [self.row], []]):
            yield

    def test_success_closes_own_descriptor_before_syscall_and_verifies_absence(self):
        def action(path):
            self.assertEqual(path, self.volume.root)
            self.root.close.assert_called_once_with()
            self.assertEqual(self.root.check.call_count, 2)
        self.syscall.side_effect = action
        with self.mounted():
            self.assertEqual(control.unmount_selected(self.volume, GENERATION), "unmounted")
        self.syscall.assert_called_once_with(self.volume.root)

    def test_policy_default_off_and_outside_fixed_control_root_are_denied(self):
        for volume in (control.storage.Volume("media", "Test", self.volume.root, UUID),
                       control.storage.Volume("media", "Test", "/mnt/other", UUID, allow_unmount=True),
                       control.storage.Volume("media", "Test", "/run/portclaim-storage/other", UUID, allow_unmount=True)):
            with patch.object(control.storage, "Root") as opened:
                self.assertEqual(control.unmount_selected(volume, GENERATION), "denied")
            opened.assert_not_called()
        self.syscall.assert_not_called()

    def test_stale_generation_cannot_unmount_and_still_closes_descriptor(self):
        with self.mounted():
            self.assertEqual(control.unmount_selected(self.volume, "b" * 64), "stale")
        self.root.close.assert_called_once_with(); self.syscall.assert_not_called()

    def test_missing_or_invalid_readonly_usb_identity_is_unavailable(self):
        for exc in (OSError("missing"), ValueError("invalid policy")):
            with patch.object(control.storage, "Root", side_effect=exc):
                self.assertEqual(control.unmount_selected(self.volume, GENERATION), "unavailable")
        self.syscall.assert_not_called()

    def test_stacked_same_device_views_and_nested_mounts_are_busy(self):
        for row in ({**self.row, "id": 124},
                    {**self.row, "id": 124, "target": "/mnt/other"},
                    {**self.row, "id": 124, "device": "9:1", "target": self.volume.root + "/nested"}):
            with self.subTest(row=row), self.mounted([[self.row, row]]):
                self.assertEqual(control.unmount_selected(self.volume, GENERATION), "busy")
        self.syscall.assert_not_called()

    def test_disappearance_or_replacement_before_syscall_fails_closed(self):
        for rows in ([], [{**self.row, "id": 124}], [{**self.row, "device": "8:18"}]):
            with self.mounted([[self.row], rows]):
                self.assertEqual(control.unmount_selected(self.volume, GENERATION), "stale")
        self.syscall.assert_not_called()

    def test_identity_recheck_failure_closes_descriptor_and_never_calls_syscall(self):
        for checks in ([OSError("identity changed")], [None, OSError("identity changed")]):
            self.root.close.reset_mock(); self.root.check = Mock(side_effect=checks)
            with self.mounted(), self.assertRaises(OSError):
                control.unmount_selected(self.volume, GENERATION)
            self.root.close.assert_called_once_with()
        self.syscall.assert_not_called()

    def test_kernel_busy_and_other_failure_do_not_force_or_retry(self):
        for number, status in ((errno.EBUSY, "busy"), (errno.EPERM, "failed"), (errno.EINVAL, "failed")):
            self.syscall.reset_mock(); self.syscall.side_effect = OSError(number, "private detail")
            with self.mounted():
                self.assertEqual(control.unmount_selected(self.volume, GENERATION), status)
            self.syscall.assert_called_once_with(self.volume.root)

    def test_syscall_return_alone_never_proves_success(self):
        self.syscall.side_effect = None
        for remaining in (self.row, {**self.row, "id": 124},
                          {**self.row, "id": 124, "target": "/mnt/new-view"}):
            with self.mounted([[self.row], [self.row], [remaining]]):
                self.assertEqual(control.unmount_selected(self.volume, GENERATION), "failed")

    def test_invalid_request_fields_and_operations_never_reach_policy_or_lock(self):
        for request in ({**self.request, "version": True}, {**self.request, "op": "volumes"},
                        {**self.request, "op": "mount"}, {**self.request, "op": "read"},
                        {**self.request, "path": "/"}, {**self.request, "force": True},
                        {**self.request, "generation": "bad"}, {**self.request, "volume": "../media"}):
            with patch.object(control, "secure_base") as base, \
                 patch.object(control, "session_lock") as lock, self.assertRaises(ValueError):
                control.dispatch(request)
            base.assert_not_called(); lock.assert_not_called()
        self.syscall.assert_not_called()

    def test_unknown_volume_denied_and_busy_lock_never_loads_policy(self):
        with patch.object(control, "secure_base"), patch.object(control, "session_lock"), \
             patch.object(control.storage, "load_config", return_value=[]):
            self.assertEqual(control.dispatch(self.request)["status"], "denied")
        with patch.object(control, "secure_base"), \
             patch.object(control, "session_lock", side_effect=BlockingIOError()), \
             patch.object(control.storage, "load_config") as policy:
            self.assertEqual(control.dispatch(self.request)["status"], "busy")
        policy.assert_not_called(); self.syscall.assert_not_called()

    def test_dispatch_binds_result_to_request_and_holds_lock_during_action(self):
        active = []
        @contextmanager
        def lock():
            active.append(True)
            try:yield
            finally:active.pop()
        def action(volume, generation):
            self.assertEqual(active, [True]); self.assertEqual(volume, self.volume)
            self.assertEqual(generation, GENERATION); return "unmounted"
        with patch.object(control, "secure_base"), patch.object(control, "session_lock", lock), \
             patch.object(control.storage, "load_config", return_value=[self.volume]), \
             patch.object(control, "unmount_selected", side_effect=action):
            self.assertEqual(control.dispatch(self.request), {"volume": "media", "generation": GENERATION,
                                                            "status": "unmounted"})
        self.assertEqual(active, [])

    def test_real_flock_blocks_independent_session_and_releases(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.lock"; path.touch(mode=0o600)
            real_fstat = os.fstat
            def metadata(fd):
                st = real_fstat(fd)
                return SimpleNamespace(st_mode=st.st_mode, st_uid=0, st_nlink=st.st_nlink,
                                       st_dev=st.st_dev, st_ino=st.st_ino)
            with patch.object(control, "LOCK", path), patch.object(os, "fstat", side_effect=metadata):
                fd = os.open(path, os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with self.assertRaises(BlockingIOError), control.session_lock():self.fail("lock bypass")
                finally:os.close(fd)
                with control.session_lock():pass
            link = Path(directory) / "link"; link.symlink_to(path)
            with patch.object(control, "LOCK", link), self.assertRaises(OSError), control.session_lock():
                self.fail("symlink lock opened")

    def test_real_client_and_control_main_over_local_child_pipes(self):
        from client.common import storage as client_storage
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "control.py"
            repo = Path(__file__).resolve().parents[2]
            script.write_text(
                "import sys\nfrom types import SimpleNamespace\nfrom unittest.mock import Mock, patch\n"
                f"sys.path.insert(0, {str(repo)!r})\n"
                "from hub import storage_unmount as control\n"
                f"volume = control.storage.Volume('media', 'Test', {self.volume.root!r}, {UUID!r}, allow_unmount=True)\n"
                f"row = {self.row!r}\n"
                f"root = SimpleNamespace(generation={GENERATION!r}, row=row, check=Mock(), close=Mock())\n"
                "def fake_unmount(path):\n    root.close.assert_called_once_with()\n"
                "with patch.object(control.os, 'geteuid', return_value=0), "
                "patch.object(control, 'secure_base'), patch.object(control, 'session_lock'), "
                "patch.object(control.storage, 'load_config', return_value=[volume]), "
                "patch.object(control.storage, 'Root', return_value=root), "
                "patch.object(control.storage, 'mount_rows', side_effect=[[row], [row], []]), "
                "patch.object(control, 'normal_unmount', side_effect=fake_unmount):\n"
                "    raise SystemExit(control.main())\n")
            with patch.object(client_storage, "ssh_command", return_value=[sys.executable, "-I", str(script)]):
                result = client_storage.StorageClient("read", "control").unmount("media", GENERATION)
            self.assertEqual(result, {"status": "unmounted", "volume": "media", "generation": GENERATION})
        self.syscall.assert_not_called()

    def test_untrusted_lock_metadata_and_replacement_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.lock"; path.touch(mode=0o600)
            real_fstat = os.fstat
            def metadata(fd, **changes):
                st = real_fstat(fd)
                return SimpleNamespace(**{**dict(st_mode=st.st_mode, st_uid=0, st_nlink=1,
                                                st_dev=st.st_dev, st_ino=st.st_ino), **changes})
            for changes in ({"st_uid": 1000}, {"st_nlink": 2}, {"st_mode": 0o100644}, {"st_mode": 0o10600}):
                with patch.object(control, "LOCK", path), \
                     patch.object(os, "fstat", side_effect=lambda fd: metadata(fd, **changes)), \
                     self.assertRaises(PermissionError), control.session_lock():self.fail("untrusted lock")
            with patch.object(control, "LOCK", path), patch.object(os, "fstat", side_effect=metadata), \
                 patch.object(Path, "lstat", return_value=SimpleNamespace(st_dev=0, st_ino=0)), \
                 self.assertRaises(PermissionError), control.session_lock():self.fail("replaced lock")

    def test_main_refuses_nonroot_extra_args_and_never_echoes_exception_details(self):
        with patch.object(os, "geteuid", return_value=1000), patch.object(control, "dispatch") as dispatch:
            self.assertEqual(control.main(), 1)
        dispatch.assert_not_called()
        with patch.object(os, "geteuid", return_value=0), patch.object(sys, "argv", ["helper", "--root=/"]), \
             patch.object(control, "dispatch") as dispatch:
            self.assertEqual(control.main(), 1)
        dispatch.assert_not_called()
        error = io.StringIO()
        with patch.object(os, "geteuid", return_value=0), patch.object(sys, "argv", ["helper"]), \
             patch.object(control.signal, "signal"), patch.object(control.signal, "alarm"), \
             patch.object(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(control.wire.encode(self.request)))), \
             patch.object(control, "dispatch", side_effect=ValueError("SECRET path/token")), redirect_stderr(error):
            self.assertEqual(control.main(), 1)
        self.assertNotIn("SECRET", error.getvalue()); self.syscall.assert_not_called()


@unittest.skipUnless(sys.platform == "linux", "Linux syscall contract")
class SyscallTests(unittest.TestCase):
    def test_only_umount_nofollow_passed_to_mocked_libc(self):
        libc = Mock(); libc.umount2.return_value = 0
        with patch.object(control.ctypes, "CDLL", return_value=libc):
            control.normal_unmount("/run/portclaim-storage/media")
        libc.umount2.assert_called_once_with(b"/run/portclaim-storage/media", 8)
        libc.umount2.return_value = -1
        with patch.object(control.ctypes, "CDLL", return_value=libc), \
             patch.object(control.ctypes, "get_errno", return_value=errno.EBUSY), self.assertRaises(OSError) as caught:
            control.normal_unmount("/run/portclaim-storage/media")
        self.assertEqual(caught.exception.errno, errno.EBUSY)

    def test_secure_base_checks_entire_ancestor_chain_and_no_symlink(self):
        base = control.BASE
        with patch.object(os, "stat", return_value=SimpleNamespace(st_ino=1)), \
             patch.object(Path, "resolve", return_value=base), \
             patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0o40755, st_uid=0)):
            control.secure_base()
        for target in (base, Path("/run"), Path("/")):
            for bad in (SimpleNamespace(st_mode=0o40775, st_uid=0),
                        SimpleNamespace(st_mode=0o40755, st_uid=1000),
                        SimpleNamespace(st_mode=0o120777, st_uid=0)):
                def lstat(path):
                    return bad if path == target else SimpleNamespace(st_mode=0o40755, st_uid=0)
                with patch.object(os, "stat", return_value=SimpleNamespace(st_ino=1)), \
                     patch.object(Path, "resolve", return_value=base), patch.object(Path, "lstat", lstat), \
                     self.assertRaises(PermissionError):control.secure_base()
        with patch.object(os, "stat", return_value=SimpleNamespace(st_ino=1)), \
             patch.object(Path, "resolve", return_value=Path("/elsewhere")), self.assertRaises(PermissionError):
            control.secure_base()

    def test_secure_base_rejects_other_namespace_before_directory_checks(self):
        with patch.object(os, "stat", side_effect=[SimpleNamespace(st_ino=1), SimpleNamespace(st_ino=2)]), \
             self.assertRaises(PermissionError):
            control.secure_base()
