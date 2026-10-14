import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cleaner


class TestDynamicIDs(unittest.TestCase):
    def assert_stable(self, hits, convert, identity):
        before = {identity(h): t['id'] for h, t in zip(hits, convert(hits))}
        reverse = {identity(h): t['id'] for h, t in zip(reversed(hits), convert(list(reversed(hits))))}
        self.assertEqual(before, reverse)
        self.assertEqual(len(set(before.values())), len(hits))
        remaining = convert(hits[1:])[0]['id']
        self.assertNotEqual(before[identity(hits[0])], remaining)

    def test_temporary_ids_do_not_redirect_when_candidates_change(self):
        hits = [{'path': Path('/private/tmp') / name, 'kind': 'repo-clone', 'mtime': 0}
                for name in ('foo-bar', 'foo_bar')]
        self.assert_stable(hits, cleaner.tmp_to_targets, lambda h: h['path'])

    def test_leftover_ids_do_not_redirect_when_candidates_change(self):
        hits = [{'bundle_id': name, 'paths': [Path('/unused') / name],
                 'locations': ['Caches'], 'mtime': 0} for name in ('com.foo-bar', 'com.foo.bar')]
        self.assert_stable(hits, cleaner.app_leftovers_to_targets, lambda h: h['bundle_id'])

    def test_log_targets_have_distinct_ids(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            for name in ('foo-bar', 'foo_bar'):
                directory = home / 'Library/Logs' / name
                directory.mkdir(parents=True)
                (directory / 'log').write_bytes(b'x' * 4096)
            with mock.patch.object(cleaner, 'HOME', home):
                rows = cleaner.get_targets({**cleaner.DEFAULT_CONFIG,
                    'enabled_categories': ['logs'], 'log_threshold_mb': 0})
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({t['id'] for t in rows}), 2)

    def test_generated_project_id_cannot_collide_with_a_literal_slug(self):
        root = Path('/fixture')
        paths = [root / 'foo-bar', root / 'foo_bar']
        suffix = hashlib.sha256(b'foo-bar').hexdigest()
        paths.append(root / ('foo-bar-' + suffix))
        hits = [{'path': str(p), 'project': str(root), 'kind': 'build',
                 'age_days': 100, 'size_bytes': 1, 'git': None} for p in paths]
        with mock.patch.object(cleaner, 'HOME', root):
            ids = [t['id'] for t in cleaner.projects_to_targets(hits)]
        self.assertEqual(len(set(ids)), 3)
