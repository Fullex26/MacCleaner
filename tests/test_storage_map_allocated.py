import tempfile
from pathlib import Path
import unittest

import cleaner


class TestStorageMapAllocatedBytes(unittest.TestCase):
    def test_sparse_file_reports_allocated_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'sparse.raw'
            with path.open('wb') as f:
                f.truncate(1024 ** 3)
            allocated = path.stat().st_blocks * 512
            self.assertLess(allocated, path.stat().st_size)
            result = cleaner.scan_storage_map(td)
            entry = next(row for row in result['children'] if row['name'] == path.name)
            self.assertEqual(entry['size_bytes'], allocated)
            self.assertEqual(entry['size_human'], cleaner.fmt_size(allocated))

    def test_minimum_size_uses_sparse_allocation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'sparse.raw'
            with path.open('wb') as f:
                f.truncate(1024 ** 3)
            threshold = path.stat().st_blocks * 512 + 1
            result = cleaner.scan_storage_map(td, min_bytes=threshold)
            self.assertNotIn(path.name, [row['name'] for row in result['children']])
