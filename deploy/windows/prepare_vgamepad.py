#!/usr/bin/env python3
"""Prepare verified Windows binding files without executing vgamepad's MSI setup."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import urllib.request

VERSION = "0.1.0"
URL = "https://files.pythonhosted.org/packages/8a/54/0eaddc33f84247963af078f364b37153d09fcd6cdc398f243ec3e8842c56/vgamepad-0.1.0.tar.gz"
SHA256 = "57f6bd01aec0c172947517fb782d150ef9b285f7f4d524c317374fa5c24a89de"
LIMIT = 4 * 1024 * 1024
PREFIX = f"vgamepad-{VERSION}/"
REQUIRED = {
    "vgamepad/__init__.py", "vgamepad/win/__init__.py",
    "vgamepad/win/virtual_gamepad.py", "vgamepad/win/vigem_client.py", "vgamepad/win/vigem_commons.py",
    "vgamepad/win/vigem/client/x64/ViGEmClient.dll", "vgamepad/win/vigem/client/x86/ViGEmClient.dll",
    "vgamepad.LICENSE",
}


def files_from_archive(blob):
    if len(blob) > LIMIT or hashlib.sha256(blob).hexdigest() != SHA256:
        raise ValueError("vgamepad archive checksum/size mismatch; nothing prepared")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.name.startswith(PREFIX):
                continue
            name = member.name[len(PREFIX):]
            if name == "LICENSE":
                name = "vgamepad.LICENSE"
            if name not in REQUIRED:
                continue  # Never setup.py, MSI installers, tests, or Linux code.
            if not member.isfile() or ".." in PurePosixPath(name).parts or name in files or member.size > LIMIT:
                raise ValueError("invalid dependency archive member")
            files[name] = archive.extractfile(member).read()
    if set(files) != REQUIRED:
        raise ValueError("verified archive is missing required binding files")
    return files


def fetch():
    with urllib.request.urlopen(URL, timeout=30) as response:
        return response.read(LIMIT + 1)


def prepare(output, *, blob=None):
    output = Path(output).absolute()
    files = files_from_archive(fetch() if blob is None else blob)
    marker = {"version": VERSION, "archive_sha256": SHA256,
              "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}}
    marker_data = (json.dumps(marker, indent=2) + "\n").encode()
    if output.is_symlink():
        raise ValueError("refusing a symlinked vendor directory")
    if output.exists():
        actual = {p.relative_to(output).as_posix() for p in output.rglob("*")
                  if p.is_file() and "__pycache__" not in p.relative_to(output).parts}
        if (any(p.is_symlink() for p in output.rglob("*"))
                or actual != REQUIRED | {"portclaim-vgamepad.json"}
                or not (output / "portclaim-vgamepad.json").is_file()
                or (output / "portclaim-vgamepad.json").read_bytes() != marker_data
                or any(not (output / name).is_file() or (output / name).read_bytes() != data for name, data in files.items())):
            raise ValueError("existing vendor directory is unrecognized or modified; refusing overwrite")
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".vgamepad-", dir=output.parent))
    try:
        for name, data in files.items():
            path = temporary / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (temporary / "portclaim-vgamepad.json").write_bytes(marker_data)
        temporary.rename(output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, help="optional offline archive; checksum is still mandatory")
    args = parser.parse_args(argv)
    path = prepare(args.output, blob=args.archive.read_bytes() if args.archive else None)
    print(f"Prepared vgamepad {VERSION} binding/DLL files at {path}; no setup or driver installer executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
