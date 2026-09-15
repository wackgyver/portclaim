"""Exercise real file transactions in temporary homes; mock every host operation."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from deploy.linux import install as installer


class FakeRunner:
    def __init__(self):
        self.commands = []
        self.active = False
        self.legacy = False
        self.fail_reload = False
        self.fail_pip = False

    def __call__(self, command, *, check=True):
        self.commands.append(command)
        text, code = "", 0
        if command[1:3] == ["-m", "venv"]:
            python = Path(command[3]) / "bin/python"
            python.parent.mkdir(parents=True)
            python.touch(); python.chmod(0o755)
        elif "pip" in command and "install" in command and self.fail_pip:
            raise subprocess.CalledProcessError(1, command)
        elif command[:3] == ["systemctl", "--user", "show"]:
            text = "LoadState=loaded\nActiveState=" + ("active\nMainPID=123\n" if self.active else "inactive\nMainPID=0\n")
        elif command[:3] == ["systemctl", "--user", "is-enabled"]:
            text, code = ("enabled\n", 0) if self.legacy else ("not-found\n", 4)
        elif command[:3] == ["systemctl", "--user", "is-active"]:
            text, code = "inactive\n", 3
        elif command[-1] == "daemon-reload" and self.fail_reload:
            self.fail_reload = False
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, code, text, "")


@unittest.skipUnless(sys.platform == "linux", "Linux installation")
class InstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name) / "home with spaces"
        self.paths = installer.Paths(home, home / ".config", home / ".local/share")
        self.runner = FakeRunner()
        for target, value in (("os.access", True), ("shutil.which", "/test/tool")):
            p = patch(f"deploy.linux.install.{target}", return_value=value)
            p.start(); self.addCleanup(p.stop)

    def install(self, **kwargs):
        return installer.install(installer.SOURCE_ROOT, self.paths, runner=self.runner, **kwargs)

    def test_dry_run_has_no_writes_commands_or_permission_changes(self):
        result = self.install(dry_run=True)
        self.assertFalse(self.paths.home.exists())
        self.assertEqual(self.runner.commands, [])
        self.assertFalse(result["autostart"])

    def test_fresh_install_is_private_stopped_and_isolated(self):
        result = self.install()
        self.assertEqual(self.paths.env.stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.paths.lib / "current/src/client/receiver_app.py").is_file())
        self.assertFalse((self.paths.lib / "current/src/client/wasapi_out.py").exists())
        self.assertFalse((self.paths.lib / "current/src/client/windows").exists())
        self.assertFalse(any(p.name.startswith("test_") for p in (self.paths.lib / "current/src").rglob("*.py")))
        unit = self.paths.unit.read_text()
        self.assertIn("--start-hidden", unit)
        self.assertIn("Restart=no", unit)
        self.assertNotIn("Wants=portclaim-virtmic", unit)
        self.assertIn(f'EnvironmentFile={self.paths.env}\n', unit)
        self.assertIn(f'WorkingDirectory={self.paths.lib}/current/src\n', unit)
        self.assertIn('ExecStart="', unit)
        self.assertIn("environments/", unit)
        self.assertFalse(result["service_start"])
        self.assertFalse(any("enable" in c or "start" in c or "sudo" in c or "usermod" in c for c in self.runner.commands))
        self.assertTrue(any(c[1:3] == ["-m", "venv"] for c in self.runner.commands))

    def test_no_tray_omits_optional_dependencies(self):
        self.install(tray=False)
        self.assertFalse(any(any("requirements-tray.txt" in arg for arg in c) for c in self.runner.commands))

    def test_private_configuration_is_preserved(self):
        self.paths.env.parent.mkdir(parents=True)
        self.paths.env.write_text("USB_LOOM_TOKEN=example-private-value\n")
        self.install()
        self.assertEqual(self.paths.env.read_text(), "USB_LOOM_TOKEN=example-private-value\n")
        self.assertEqual(self.paths.env.stat().st_mode & 0o777, 0o600)

    def test_active_receiver_blocks_before_file_changes(self):
        self.runner.active = True
        with self.assertRaisesRegex(RuntimeError, "active"):
            self.install()
        self.assertFalse(self.paths.lib.exists())

    def test_legacy_microphone_service_blocks_install(self):
        self.runner.legacy = True
        with self.assertRaisesRegex(RuntimeError, "legacy"):
            self.install()
        self.assertFalse(self.paths.lib.exists())

    def test_missing_uinput_access_never_changes_permissions(self):
        with patch.object(installer.os, "access", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "/dev/uinput"):
                self.install()
        self.assertFalse(self.paths.lib.exists())

    def test_unmanaged_installation_and_files_are_not_overwritten(self):
        self.paths.lib.mkdir(parents=True)
        (self.paths.lib / "existing-user-file").write_text("keep")
        with self.assertRaisesRegex(RuntimeError, "unmanaged"):
            self.install()
        self.assertEqual((self.paths.lib / "existing-user-file").read_text(), "keep")
        self.assertEqual(self.runner.commands, [])

    def test_unmanaged_launcher_is_not_overwritten(self):
        self.paths.launcher.parent.mkdir(parents=True)
        self.paths.launcher.write_text("my launcher")
        with self.assertRaisesRegex(RuntimeError, "unmanaged file"):
            self.install()
        self.assertEqual(self.paths.launcher.read_text(), "my launcher")

    def test_modified_managed_file_blocks_upgrade(self):
        self.install()
        self.paths.unit.write_text("custom unit\n")
        with self.assertRaisesRegex(RuntimeError, "locally modified"):
            self.install(upgrade=True)
        self.assertEqual(self.paths.unit.read_text(), "custom unit\n")

    def test_upgrade_requires_explicit_flag(self):
        self.install()
        with self.assertRaisesRegex(RuntimeError, "--upgrade"):
            self.install()

    def test_upgrade_reuses_environment_and_keeps_backup(self):
        first = self.install()
        files = installer.source_files(installer.SOURCE_ROOT)
        files["src/client/receiver_app.py"] += b"\n# test upgrade\n"
        self.runner.commands.clear()
        with patch.object(installer, "source_files", return_value=files):
            second = self.install(upgrade=True)
        self.assertNotEqual(first["release"], second["release"])
        self.assertEqual(first["environment"], second["environment"])
        self.assertTrue(Path(second["backup"]).is_dir())
        self.assertTrue((self.paths.lib / "releases" / first["release"]).is_dir())
        self.assertFalse(any("pip" in c for c in self.runner.commands))

    def test_failed_reload_rolls_back_new_install(self):
        self.runner.fail_reload = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.install()
        for path in (self.paths.unit, self.paths.launcher, self.paths.desktop, self.paths.env, self.paths.manifest):
            self.assertFalse(path.exists(), path)
        self.assertFalse((self.paths.lib / "current").is_symlink())

    def test_failed_upgrade_restores_previous_release_and_files(self):
        self.install()
        original = {p: p.read_bytes() for p in (self.paths.unit, self.paths.launcher, self.paths.desktop, self.paths.manifest, self.paths.env)}
        old_link = os.readlink(self.paths.lib / "current")
        files = installer.source_files(installer.SOURCE_ROOT)
        files["src/client/receiver_app.py"] += b"\n# failed upgrade\n"
        self.runner.fail_reload = True
        with patch.object(installer, "source_files", return_value=files):
            with self.assertRaises(subprocess.CalledProcessError):
                self.install(upgrade=True)
        self.assertEqual(os.readlink(self.paths.lib / "current"), old_link)
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)

    def test_dependency_failure_does_not_touch_live_facing_files(self):
        self.runner.fail_pip = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.install()
        for path in (self.paths.unit, self.paths.launcher, self.paths.desktop, self.paths.env):
            self.assertFalse(path.exists())
        # A corrected retry must not look like an unknown existing installation.
        self.runner.fail_pip = False
        self.install()

    def test_paths_with_percent_are_escaped_not_interpreted_by_systemd(self):
        paths = replace(self.paths, config=self.paths.home / "config%name")
        files = installer.managed_files(installer.SOURCE_ROOT, paths, paths.lib / "venv")
        self.assertIn(b"config%%name", files[paths.unit][0])

    def test_unsafe_quoting_in_paths_fails_closed(self):
        with self.assertRaises(ValueError):
            installer.quoted(Path('/tmp/unsafe"path'))


if __name__ == "__main__":
    unittest.main()
