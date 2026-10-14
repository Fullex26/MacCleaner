import os
from pathlib import Path
import tempfile
import unittest

import cleaner


class TestBundleHardlinks(unittest.TestCase):
    def test_nested_hardlinks_count_allocated_inode_once(self):
        with tempfile.TemporaryDirectory() as td:
            bundle = Path(td) / 'Example.app'
            nested = bundle / 'Contents' / 'Resources'
            nested.mkdir(parents=True)
            original = bundle / 'original'
            original.write_bytes(b'x' * 8192)
            os.link(original, nested / 'linked')
            independent = nested / 'independent'
            independent.write_bytes(b'x' * 8192)
            expected = sum(p.stat().st_blocks * 512 for p in (original, independent))
            self.assertEqual(cleaner._bundle_size(bundle), expected)

    def test_each_bundle_counts_its_own_shared_inode(self):
        with tempfile.TemporaryDirectory() as td:
            first, second = (Path(td) / name for name in ('First.app', 'Second.app'))
            first.mkdir()
            second.mkdir()
            original = first / 'file'
            original.write_bytes(b'x' * 8192)
            os.link(original, second / 'file')
            expected = original.stat().st_blocks * 512
            self.assertEqual(cleaner._bundle_size(first), expected)
            self.assertEqual(cleaner._bundle_size(second), expected)
