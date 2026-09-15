#!/usr/bin/env python3
"""Build a credential-free Linux source artifact; never install or run the receiver."""
from __future__ import annotations
import argparse
import gzip
import io
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.linux.install import source_files


def files(root):
    result = {name[4:]: data for name, data in source_files(root).items() if name.startswith('src/')}
    for name in ('README.md', 'deploy/__init__.py', 'deploy/install-receiver.sh', 'deploy/usb-loom-receiver.env.example'):
        result[name] = (root / name).read_bytes()
    for folder in ('deploy/linux', 'docs', 'integrations/omarchy'):
        for p in sorted((root / folder).rglob('*')):
            if p.is_file() and p.suffix in {'.py', '.md', '.txt', '.in', '.lua'} and '__pycache__' not in p.parts:
                if p.is_symlink():
                    raise ValueError(f'refusing symlinked package file: {p}')
                result[p.relative_to(root).as_posix()] = p.read_bytes()
    return result


def build(root, output):
    members = files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Fixed metadata makes equal source produce an equal artifact.
    with output.open('xb') as handle, gzip.GzipFile(filename='', fileobj=handle, mode='wb', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w') as tar:
            for name, data in sorted(members.items()):
                entry = tarfile.TarInfo('portclaim-linux/' + name)
                entry.size = len(data)
                entry.mode = 0o755 if name == 'deploy/install-receiver.sh' else 0o644
                tar.addfile(entry, io.BytesIO(data))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/portclaim-linux-source.tar.gz')
    args = parser.parse_args()
    print(build(ROOT, args.output))


if __name__ == '__main__':
    main()
