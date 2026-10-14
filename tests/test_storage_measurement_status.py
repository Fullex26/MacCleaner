import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import cleaner


class TestStorageMeasurementStatus(unittest.TestCase):
    def test_timeout_keeps_loose_rows_but_marks_total_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            file = Path(td) / 'file'
            file.write_bytes(b'x' * 4096)
            with mock.patch.object(cleaner.subprocess, 'run',
                                   side_effect=subprocess.TimeoutExpired('du', 600)), \
                 contextlib.redirect_stderr(io.StringIO()):
                result = cleaner.scan_storage_map(td)
            self.assertTrue(result.get('incomplete'))
            self.assertEqual(result['children'][0]['size_bytes'], file.stat().st_blocks * 512)

    def test_partial_du_output_preserves_rows_and_incomplete_status(self):
        with tempfile.TemporaryDirectory() as td:
            child = Path(td) / 'child'
            child.mkdir()
            reply = subprocess.CompletedProcess([], 1, f'4\t{child}\n4\t{td}\n', 'permission denied')
            with mock.patch.object(cleaner.subprocess, 'run', return_value=reply):
                result = cleaner.scan_storage_map(td)
            self.assertTrue(result.get('incomplete'))
            self.assertEqual(result['total_bytes'], 4096)
            self.assertEqual(result['children'][0]['size_bytes'], 4096)

    def test_missing_root_total_is_incomplete_even_on_success_exit(self):
        with tempfile.TemporaryDirectory() as td:
            reply = subprocess.CompletedProcess([], 0, 'unparseable\n', '')
            with mock.patch.object(cleaner.subprocess, 'run', return_value=reply):
                self.assertTrue(cleaner.scan_storage_map(td).get('incomplete'))

    def test_nested_failure_marks_parent_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            child = Path(td) / 'child'
            child.mkdir()
            top = subprocess.CompletedProcess([], 0, f'4\t{child}\n4\t{td}\n', '')
            with mock.patch.object(cleaner.subprocess, 'run',
                                   side_effect=[top, subprocess.TimeoutExpired('du', 600)]), \
                 contextlib.redirect_stderr(io.StringIO()):
                result = cleaner.scan_storage_map(td, depth=2)
            self.assertTrue(result.get('incomplete'))
            self.assertEqual(result['children'][0]['children'], [])

    def test_complete_zero_total_remains_complete(self):
        with tempfile.TemporaryDirectory() as td:
            reply = subprocess.CompletedProcess([], 0, f'0\t{td}\n', '')
            with mock.patch.object(cleaner.subprocess, 'run', return_value=reply):
                result = cleaner.scan_storage_map(td)
            self.assertNotIn('incomplete', result)
            self.assertEqual(result['total_bytes'], 0)
