"""The Windows dependency preparer is tested offline and never executes setup."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from deploy.windows import prepare_vgamepad as prep


def archive(extra=None, omit=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name in sorted(prep.REQUIRED | {'setup.py', 'vgamepad/win/driver.msi', 'vgamepad/lin/virtual_gamepad.py'}):
            if name == omit:
                continue
            data = b'# synthetic inert fixture\n'
            source_name = 'LICENSE' if name == 'vgamepad.LICENSE' else name
            entry = tarfile.TarInfo(prep.PREFIX + source_name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
        if extra:
            tar.addfile(extra)
    return buffer.getvalue()


class PreparationTests(unittest.TestCase):
    def test_bad_hash_refused_before_archive_parsing(self):
        with patch.object(prep.tarfile, 'open') as opening:
            with self.assertRaisesRegex(ValueError, 'checksum'):
                prep.files_from_archive(b'not a valid dependency')
        opening.assert_not_called()

    def test_only_allowlisted_files_prepared_and_reused(self):
        data = archive()
        with tempfile.TemporaryDirectory() as d, patch.object(prep, 'SHA256', hashlib.sha256(data).hexdigest()):
            output = Path(d) / 'vendor'
            prep.prepare(output, blob=data)
            self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()},
                             prep.REQUIRED | {'portclaim-vgamepad.json'})
            self.assertEqual(prep.prepare(output, blob=data), output)
            (output / 'unexpected.py').write_text('extra')
            with self.assertRaisesRegex(ValueError, 'modified'):
                prep.prepare(output, blob=data)

    def test_modified_dependency_refused(self):
        data = archive()
        with tempfile.TemporaryDirectory() as d, patch.object(prep, 'SHA256', hashlib.sha256(data).hexdigest()):
            output = prep.prepare(Path(d) / 'vendor', blob=data)
            (output / 'vgamepad/__init__.py').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'modified'):
                prep.prepare(output, blob=data)

    def test_missing_required_member_refused(self):
        data = archive(omit='vgamepad/__init__.py')
        with patch.object(prep, 'SHA256', hashlib.sha256(data).hexdigest()):
            with self.assertRaisesRegex(ValueError, 'missing'):
                prep.files_from_archive(data)

    def test_link_cannot_replace_required_file(self):
        link = tarfile.TarInfo(prep.PREFIX + 'vgamepad/__init__.py')
        link.type = tarfile.SYMTYPE
        link.linkname = '/outside'
        data = archive(extra=link, omit='vgamepad/__init__.py')
        with patch.object(prep, 'SHA256', hashlib.sha256(data).hexdigest()):
            with self.assertRaisesRegex(ValueError, 'invalid'):
                prep.files_from_archive(data)
