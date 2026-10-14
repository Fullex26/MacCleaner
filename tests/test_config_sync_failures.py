import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cleaner


class TestConfigSyncFailures(unittest.TestCase):
    def test_failed_sync_off_preserves_trash_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            local, cloud = root / 'config.json', root / 'cloud/config.json'
            cloud.parent.mkdir()
            cloud.write_text(json.dumps({'delete_mode': 'trash'}))
            local.symlink_to(cloud)
            with patch.object(cleaner, 'CONFIG_PATH', local), \
                 patch.object(cleaner, '_icloud_config_dir', return_value=cloud.parent), \
                 patch.object(cleaner, '_atomic_write_json', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    cleaner.run_config_sync('off')
                self.assertTrue(local.is_symlink())
                self.assertEqual(cleaner.load_config()['delete_mode'], 'trash')
            self.assertEqual(json.loads(cloud.read_text())['delete_mode'], 'trash')

    def test_failed_sync_on_preserves_local_config(self):
        for existing_cloud in (False, True):
            with self.subTest(existing_cloud=existing_cloud), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                local, cloud = root / 'config.json', root / 'cloud/config.json'
                local.write_text(json.dumps({'delete_mode': 'trash'}))
                cloud.parent.mkdir()
                if existing_cloud:
                    cloud.write_text(json.dumps({'delete_mode': 'rm'}))
                with patch.object(cleaner, 'CONFIG_PATH', local), \
                     patch.object(cleaner, '_icloud_config_dir', return_value=cloud.parent), \
                     patch.object(cleaner.os, 'symlink', side_effect=OSError('link failed')):
                    with self.assertRaises(OSError):
                        cleaner.run_config_sync('on')
                    self.assertTrue(local.is_file())
                    self.assertFalse(local.is_symlink())
                    self.assertEqual(cleaner.load_config()['delete_mode'], 'trash')

    def test_successful_sync_off_replaces_link_without_changing_cloud(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            local, cloud = root / 'config.json', root / 'cloud/config.json'
            cloud.parent.mkdir()
            cloud.write_text(json.dumps({'delete_mode': 'trash'}))
            original = cloud.read_bytes()
            local.symlink_to(cloud)
            with patch.object(cleaner, 'CONFIG_PATH', local), \
                 patch.object(cleaner, '_icloud_config_dir', return_value=cloud.parent), \
                 contextlib.redirect_stdout(io.StringIO()):
                cleaner.run_config_sync('off')
                self.assertEqual(cleaner.load_config()['delete_mode'], 'trash')
            self.assertFalse(local.is_symlink())
            self.assertEqual(cloud.read_bytes(), original)
