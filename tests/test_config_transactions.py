import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cleaner


class TestConfigTransactions(unittest.TestCase):
    def test_stale_setting_writer_preserves_newer_delete_mode(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(cleaner, 'CONFIG_PATH', Path(td) / 'config.json'):
                first, second = cleaner.load_config(), cleaner.load_config()
                cleaner.cmd_config_set_key(first, 'delete_mode', '"trash"')
                cleaner.cmd_config_set_key(second, 'project_min_age_days', '60')
                actual = cleaner.load_config()
                self.assertEqual(actual['delete_mode'], 'trash')
                self.assertEqual(actual['project_min_age_days'], 60)

    def test_stale_category_writer_preserves_newer_settings_through_symlink(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            destination, link = root / 'cloud.json', root / 'config.json'
            destination.write_text(json.dumps(cleaner.DEFAULT_CONFIG))
            link.symlink_to(destination)
            with mock.patch.object(cleaner, 'CONFIG_PATH', link):
                first, second = cleaner.load_config(), cleaner.load_config()
                cleaner.cmd_config_set_key(first, 'delete_mode', '"trash"')
                cleaner.cmd_config_set_category(second, 'disable', 'node')
                actual = cleaner.load_config()
                self.assertEqual(actual['delete_mode'], 'trash')
                self.assertNotIn('node', actual['enabled_categories'])
                self.assertTrue(link.is_symlink())
