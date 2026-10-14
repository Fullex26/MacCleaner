import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cleaner


class TestDeletionReporting(unittest.TestCase):
    def test_permission_denied_directory_is_not_reported_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            parent = home / "readonly"
            parent.mkdir()
            target = parent / "cache"
            target.mkdir()
            (target / "payload").write_bytes(b"x" * 8192)
            parent.chmod(0o500)
            target.chmod(0o500)
            try:
                with mock.patch.object(cleaner, "HOME", home):
                    freed, error = cleaner.delete_target({"id": "fixture", "path": target})
                self.assertTrue(target.exists())
                self.assertIsNotNone(error)
                self.assertEqual(freed, 0)
            finally:
                target.chmod(0o700)
                parent.chmod(0o700)

    def test_empty_only_counts_successful_children_before_failure(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            cache = home / "cache"
            cache.mkdir()
            first, second = cache / "first", cache / "second"
            first.write_bytes(b"a" * 4096)
            second.write_bytes(b"b" * 8192)
            remove = cleaner._remove
            def fail_second(path):
                if path == second:
                    raise PermissionError("fixture denial")
                remove(path)
            with mock.patch.object(cleaner, "HOME", home), \
                 mock.patch.object(cleaner, "_target_paths", return_value=[cache]), \
                 mock.patch.object(Path, "iterdir", return_value=iter([first, second])), \
                 mock.patch.object(cleaner, "_remove", side_effect=fail_second):
                freed, error = cleaner.delete_target({"id": "fixture", "empty_only": True})
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertIsNotNone(error)
            self.assertEqual(freed, 4096)
