import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cleaner


class TestEmptyOnlySymlinks(unittest.TestCase):
    def fixture(self, home, target_id):
        docs = home / 'Documents'
        docs.mkdir()
        sentinel = docs / 'personal.txt'
        sentinel.write_text('keep me')
        link = home / ('.Trash' if target_id == 'trash' else 'Library/Caches')
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(docs, target_is_directory=True)
        return {'id': target_id, 'label': 'fixture', 'path': link, 'safe': False,
                'empty_only': True}, sentinel

    def test_empty_only_refuses_symlink_roots_in_both_delete_modes(self):
        for target_id in ('general-caches', 'trash'):
            for mode in ('rm', 'trash'):
                with self.subTest(target=target_id, mode=mode), tempfile.TemporaryDirectory() as td:
                    home = Path(td)
                    target, sentinel = self.fixture(home, target_id)
                    with mock.patch.object(cleaner, 'HOME', home):
                        freed, error = cleaner.delete_target(target, mode=mode)
                    self.assertTrue(sentinel.exists(), 'symlink destination data was removed')
                    self.assertTrue(target['path'].is_symlink())
                    self.assertEqual(freed, 0)
                    self.assertIn('symlink', error or '')

    def test_preview_never_expands_empty_only_symlink_roots(self):
        for target_id in ('general-caches', 'trash'):
            with self.subTest(target=target_id), tempfile.TemporaryDirectory() as td:
                home = Path(td)
                target, sentinel = self.fixture(home, target_id)
                out = io.StringIO()
                with mock.patch.object(cleaner, 'HOME', home), contextlib.redirect_stdout(out):
                    cleaner.run_dry_run([target], json_mode=True)
                result = json.loads(out.getvalue())
                self.assertEqual(result['items'][0]['paths'], [])
                self.assertEqual(result['freed_bytes'], 0)
                self.assertEqual(sentinel.read_text(), 'keep me')
