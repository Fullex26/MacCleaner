import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cleaner


class TestExclusionOverlap(unittest.TestCase):
    def test_parent_targets_cannot_bypass_narrower_exclusions(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            npm = home / '.npm' / '_cacache'
            pip = home / 'Library' / 'Caches' / 'pip'
            for directory in (npm, pip):
                directory.mkdir(parents=True)
                (directory / 'keep').write_bytes(b'x' * 4096)
            config = {**cleaner.DEFAULT_CONFIG, 'skip_paths': [str(npm / 'keep'), str(pip)]}
            with mock.patch.dict(os.environ, {'HOME': str(home)}), mock.patch.object(cleaner, 'HOME', home):
                targets = cleaner.get_targets(config)
                selected = [t for t in targets if t['id'] in ('npm-cache', 'pip-cache', 'general-caches')]
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    cleaner.run_dry_run(selected, json_mode=True)
            self.assertEqual(json.loads(output.getvalue())['items'], [])
            self.assertTrue((npm / 'keep').exists())
            self.assertTrue((pip / 'keep').exists())

    def test_glob_and_multi_paths_exclude_ancestors_at_component_boundaries(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ('cache', 'cache2'):
                (root / name).mkdir()
                (root / name / 'keep').write_text('protected')
            skip = [str(root / 'cache2' / 'keep')]
            for target in ({'glob': str(root / '*'), 'skip': skip},
                           {'paths': [root / 'cache', root / 'cache2'], 'skip': skip}):
                with self.subTest(target=target):
                    self.assertEqual(cleaner._target_paths(target), [root / 'cache'])

    def test_excluded_symlink_entry_protects_parent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache, destination = root / 'cache', root / 'shared'
            cache.mkdir()
            destination.mkdir()
            (cache / 'keep').symlink_to(destination, target_is_directory=True)
            target = {'path': cache, 'skip': [str(cache / 'keep')]}
            self.assertEqual(cleaner._target_paths(target), [])

    def test_dynamic_log_target_respects_excluded_descendant(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            log = home / 'Library/Logs/Protected'
            log.mkdir(parents=True)
            (log / 'keep').write_bytes(b'x' * 4096)
            config = {**cleaner.DEFAULT_CONFIG, 'enabled_categories': ['logs'],
                      'log_threshold_mb': 0, 'skip_paths': [str(log / 'keep')]}
            with mock.patch.object(cleaner, 'HOME', home):
                self.assertEqual(cleaner.get_targets(config), [])
