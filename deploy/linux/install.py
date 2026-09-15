#!/usr/bin/env python3
"""User-scoped Linux installation. No sudo, input-group edits, claims, or autostart."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

SOURCE_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 1


@dataclass(frozen=True)
class Paths:
    home: Path
    config: Path
    data: Path

    @classmethod
    def current(cls):
        home = Path.home()
        return cls(home, Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config"),
                   Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share"))

    @property
    def lib(self): return self.home / ".local/lib/portclaim"
    @property
    def launcher(self): return self.home / ".local/bin/portclaim"
    @property
    def unit(self): return self.config / "systemd/user/portclaim.service"
    @property
    def desktop(self): return self.data / "applications/portclaim.desktop"
    @property
    def env(self): return self.config / "portclaim/usb-loom.env"
    @property
    def manifest(self): return self.lib / "install.json"


def run(command, *, check=True):
    return subprocess.run(command, text=True, capture_output=True, check=check, timeout=900)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_files(root):
    """Snapshot only receiver/protocol code, never site files or build outputs."""
    result = {}
    for folder in ("client", "proto"):
        for path in sorted((root / folder).rglob("*.py")):
            relative = path.relative_to(root)
            if ("windows" in relative.parts or "__pycache__" in relative.parts
                    or path.name.startswith("test_") or path.name == "wasapi_out.py"):
                continue
            if path.is_symlink():
                raise ValueError(f"refusing symlinked source: {relative}")
            result[str(Path("src") / relative)] = path.read_bytes()
    for name in ("virtmic.py", "portclaim.py"):
        result[f"helpers/{name}"] = (root / "deploy/linux" / name).read_bytes()
    for name in ("portclaim.service.in", "portclaim.desktop.in", "requirements.txt", "requirements-tray.txt"):
        result[f"packaging/{name}"] = (root / "deploy/linux" / name).read_bytes()
    if "src/client/receiver_app.py" not in result or "src/proto/tp_native.py" not in result:
        raise ValueError("incomplete receiver source tree")
    return result


def fingerprint(files):
    return digest(b"".join(name.encode() + b"\0" + data + b"\0" for name, data in sorted(files.items())))[:20]


def quoted(path):
    text = str(path)
    if not path.is_absolute() or any(c in text for c in '\r\n\x00"`$\\'):
        raise ValueError("installation paths must be absolute and contain no shell/control quoting characters")
    return '"' + text.replace("%", "%%") + '"'


def render(template, values):
    return re.sub(r"@([A-Z_]+)@", lambda m: values[m[1]], template).encode()


def managed_files(root, paths, environment):
    current = paths.lib / "current"
    # Path-only directives consume the whole value, not shell-style words.
    # Quotes become literal filename characters there; Exec/desktop arguments
    # still need quotes. Retain %% escaping for systemd specifiers in both.
    values = {"ENV_FILE": quoted(paths.env)[1:-1], "SOURCE": quoted(current / "src")[1:-1],
              "PYTHON": quoted(environment / "bin/python"),
              "MIC_HELPER": quoted(current / "helpers/virtmic.py"),
              "RECEIVER": quoted(current / "src/client/receiver_app.py"),
              "LAUNCHER": quoted(paths.launcher)}
    return {
        paths.unit: (render((root / "deploy/linux/portclaim.service.in").read_text(), values), 0o644),
        paths.desktop: (render((root / "deploy/linux/portclaim.desktop.in").read_text(), values), 0o644),
        paths.launcher: ((root / "deploy/linux/portclaim.py").read_bytes(), 0o755),
    }


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".portclaim-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def replace_link(path, target):
    temporary = path.parent / (".current-" + str(time.time_ns()))
    try:
        temporary.symlink_to(target)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def service_inactive(runner):
    result = runner(["systemctl", "--user", "show", "portclaim.service", "-p", "LoadState",
                     "-p", "ActiveState", "-p", "MainPID"], check=False)
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if (result.returncode in (0, 4) and fields.get("LoadState") == "not-found"
            and fields.get("ActiveState") == "inactive" and fields.get("MainPID") == "0"):
        return
    if result.returncode or fields.get("LoadState") != "loaded":
        raise RuntimeError("cannot establish an inactive, unmasked user service; no installation performed")
    if fields.get("ActiveState") not in {"inactive", "failed"} or fields.get("MainPID") != "0":
        raise RuntimeError("PortClaim is active; stop it explicitly before installing/upgrading")


def check_owned(paths, targets, upgrade):
    if paths.lib.is_symlink():
        raise RuntimeError("refusing a symlinked installation prefix")
    if paths.manifest.exists():
        previous = json.loads(paths.manifest.read_text())
        if not isinstance(previous, dict) or previous.get("schema") != SCHEMA or set(previous.get("managed", {})) != {str(p) for p in targets}:
            raise RuntimeError("unrecognized installation manifest; manual migration required")
        if not upgrade:
            raise RuntimeError("managed installation exists; use --upgrade after stopping it")
        for path in targets:
            if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != previous["managed"][str(path)]:
                raise RuntimeError(f"locally modified managed file: {path}; preserve/reconcile it before upgrading")
        current = paths.lib / "current"
        if not current.is_symlink() or os.readlink(current) != previous.get("current"):
            raise RuntimeError("current release link differs from the manifest")
        return previous
    if paths.lib.exists() and any(p.name != ".install.lock" for p in paths.lib.iterdir()):
        raise RuntimeError("unmanaged PortClaim installation exists; refusing to overwrite it")
    for path in targets:
        if path.exists() or path.is_symlink():
            raise RuntimeError(f"unmanaged file exists: {path}; manual migration required")
    return None


def prepare_environment(environment, root, tray, runner, env_id):
    marker = environment / "portclaim-environment.json"
    if environment.exists():
        if (not marker.is_file() or json.loads(marker.read_text()).get("id") != env_id
                or not (environment / "bin/python").is_file()):
            raise RuntimeError("unrecognized dependency environment; refusing to reuse/overwrite it")
        return False
    environment.parent.mkdir(parents=True, exist_ok=True)
    try:
        runner([sys.executable, "-m", "venv", str(environment)])
        python = str(environment / "bin/python")
        runner([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(root / "deploy/linux/requirements.txt")])
        if tray:
            runner([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(root / "deploy/linux/requirements-tray.txt")])
        runner([python, "-m", "pip", "check"])
        code = "import tkinter, evdev, libevdev, vgamepad"
        if tray:
            code += "; import os; os.environ['PYSTRAY_BACKEND']='appindicator'; from pystray._appindicator import Icon"
        runner([python, "-c", code])  # Imports only; no virtual input or audio devices.
        atomic_write(marker, json.dumps({"id": env_id}).encode(), 0o600)
    except BaseException:
        if environment.exists():
            shutil.rmtree(environment)  # Only this invocation's incomplete environment.
        raise
    return True


@contextmanager
def installation_lock(prefix):
    import fcntl
    fd = os.open(prefix / ".install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_uid != os.geteuid():
            raise RuntimeError("installation lock is not owned by this user")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another PortClaim installation is running") from None
        yield
    finally:
        os.close(fd)
    # Keep the inode: unlinking a lock can let concurrent installers lock
    # different inodes. An otherwise empty prefix with this lock is retryable.


def install(root, paths, *, upgrade=False, tray=True, dry_run=False, runner=run, _lock_held=False):
    files = source_files(root)
    release_id = fingerprint(files)
    requirements = (root / "deploy/linux/requirements.txt").read_bytes()
    if tray:
        requirements += (root / "deploy/linux/requirements-tray.txt").read_bytes()
    env_id = f"py{sys.version_info.major}.{sys.version_info.minor}-" + digest(requirements)[:16]
    environment = paths.lib / "environments" / env_id
    targets = managed_files(root, paths, environment)
    previous = check_owned(paths, targets, upgrade)
    if paths.env.is_symlink():
        raise RuntimeError("refusing a symlinked private environment file")
    if dry_run:
        return {"release": release_id, "environment": env_id, "files": [str(p) for p in targets],
                "private_config": str(paths.env), "autostart": False, "service_start": False}
    service_inactive(runner)
    if not os.access("/dev/uinput", os.W_OK):
        raise RuntimeError("/dev/uinput is not writable in this session; no permissions/groups were changed")
    if not shutil.which("pactl") or not shutil.which("paplay"):
        raise RuntimeError("install PipeWire/Pulse client utilities (pactl and paplay) first")
    # A separate legacy mic service would own defaults/lifecycle behind our back.
    legacy = runner(["systemctl", "--user", "is-enabled", "portclaim-virtmic.service"], check=False)
    active = runner(["systemctl", "--user", "is-active", "portclaim-virtmic.service"], check=False)
    if legacy.stdout.strip() in {"enabled", "enabled-runtime"} or active.stdout.strip() in {"active", "activating"}:
        raise RuntimeError("legacy portclaim-virtmic.service is enabled/active; manual migration required")

    paths.lib.mkdir(parents=True, exist_ok=True)
    if not _lock_held:
        with installation_lock(paths.lib):
            # Recheck ownership and live state while holding the installation lock.
            return install(root, paths, upgrade=upgrade, tray=tray, runner=runner, _lock_held=True)
    try:
        new_environment = prepare_environment(environment, root, tray, runner, env_id)
    except BaseException:
        # Only remove empty directories left by this failed preparation. Never
        # sweep unknown files or turn a corrected retry into an unmanaged install.
        for directory in (environment.parent, paths.lib):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
    release = paths.lib / "releases" / release_id
    new_release = not release.exists()
    applied = []
    prior = {p: (p.read_bytes(), p.stat().st_mode & 0o777) if p.exists() else None for p in targets}
    old_manifest = paths.manifest.read_bytes() if previous else None
    old_link = previous["current"] if previous else None
    created_env = False
    link_applied = False
    manifest_applied = False
    backup = None
    try:
        if new_release:
            for name, data in files.items():
                atomic_write(release / name, data, 0o644)
        elif any(not (release / name).is_file() or (release / name).read_bytes() != data for name, data in files.items()):
            raise RuntimeError("existing release content differs; refusing to overwrite it")
        # Recheck immediately before replacing any live-facing files.
        service_inactive(runner)
        if previous:
            check_owned(paths, targets, upgrade)
        elif any(p.exists() or p.is_symlink() for p in (*targets, paths.manifest, paths.lib / "current")):
            raise RuntimeError("installation targets changed during preparation; refusing to overwrite them")
        if previous:
            backup = paths.lib / "backups" / str(time.time_ns())
            backup.mkdir(parents=True, mode=0o700)
            atomic_write(backup / "install.json", old_manifest, 0o600)
            for index, (path, content) in enumerate(prior.items()):
                atomic_write(backup / f"managed-{index}", content[0], content[1])
            atomic_write(backup / "paths.json", json.dumps([str(p) for p in prior]).encode(), 0o600)
        if not paths.env.exists():
            data = (root / "deploy/usb-loom-receiver.env.example").read_bytes()
            atomic_write(paths.env, data, 0o600)
            created_env = True
        else:
            paths.env.chmod(0o600)  # Preserve contents; never leave a filled token world-readable.
        for path, (data, mode) in targets.items():
            atomic_write(path, data, mode)
            applied.append(path)
        relative = f"releases/{release_id}"
        replace_link(paths.lib / "current", relative)
        link_applied = True
        record = {"schema": SCHEMA, "current": relative, "environment": env_id,
                  "managed": {str(p): digest(data) for p, (data, _mode) in targets.items()}}
        atomic_write(paths.manifest, json.dumps(record, indent=2).encode() + b"\n", 0o600)
        manifest_applied = True
        runner(["systemctl", "--user", "daemon-reload"])
    except BaseException:
        for path in reversed(applied):
            if prior[path] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, *prior[path])
        if link_applied:
            if old_link is not None:
                replace_link(paths.lib / "current", old_link)
            else:
                (paths.lib / "current").unlink(missing_ok=True)
        if manifest_applied:
            if old_manifest is not None:
                atomic_write(paths.manifest, old_manifest, 0o600)
            else:
                paths.manifest.unlink(missing_ok=True)
        if created_env:
            paths.env.unlink(missing_ok=True)
        if new_release and release.exists():
            shutil.rmtree(release)
        if new_environment:
            shutil.rmtree(environment)
        for directory in (paths.lib / "releases", paths.lib / "environments", paths.lib):
            try:
                directory.rmdir()
            except OSError:
                pass
        # Files are restored even if the manager is unavailable; preserve the
        # original error while making a failed rollback reload visible.
        if applied:
            result = runner(["systemctl", "--user", "daemon-reload"], check=False)
            if result.returncode:
                print("Rollback files restored, but user daemon-reload failed; retry it manually", file=sys.stderr)
        raise
    return {"release": release_id, "environment": env_id, "backup": str(backup) if backup else None,
            "autostart": False, "service_start": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upgrade", action="store_true", help="update only a stopped, recognized installation")
    parser.add_argument("--without-tray", action="store_true", help="omit optional GTK/AppIndicator dependencies; controls stay visible")
    parser.add_argument("--dry-run", action="store_true", help="validate ownership and print paths without writes or subprocesses")
    args = parser.parse_args(argv)
    if sys.platform != "linux" or os.geteuid() == 0:
        parser.exit(1, "Run as the Linux desktop user, never root.\n")
    try:
        result = install(SOURCE_ROOT, Paths.current(), upgrade=args.upgrade, tray=not args.without_tray, dry_run=args.dry_run)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"PortClaim installation failed: {exc}\nNo service was started or enabled.\n")
    print(json.dumps(result, indent=2))
    if not args.dry_run:
        print("Fill the private usb-loom.env, then launch portclaim. Autostart remains opt-in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
