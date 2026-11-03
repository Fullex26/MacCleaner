"""Clone-aware "unshared" sizing for storage-map --unshared (issue #107).

du bills every APFS clone the full size of the blocks it shares: 81 Xcode
parallel-test simulator clones measured 307 GB and deleting them freed ~0.
The portable tests pin the walk's contract with an injected PRIVATESIZE reader.
The macOS tests build real clones with `cp -c` and read the kernel's answer,
which is also what catches a wrong attribute bit (0x10 is LINKID, an inode
number that looks like a plausible size).
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import cleaner

MiB = 1024 * 1024


def _by_name(values):
    """A fake PRIVATESIZE reader keyed by file name."""
    return lambda path: values.get(os.path.basename(path), 0)


class TestUnsharedWalk(unittest.TestCase):
    def test_sums_private_bytes_of_regular_files(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'a').write_bytes(b'a')
            sub = Path(td) / 'sub'
            sub.mkdir()
            (sub / 'b').write_bytes(b'b')
            got = cleaner._unshared_bytes(td, private_size=_by_name({'a': 100, 'b': 20}))
            self.assertEqual(got, 120)

    def test_symlinks_are_never_followed_or_counted(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            (Path(other) / 'outside').write_bytes(b'x')
            (Path(td) / 'link').symlink_to(Path(other) / 'outside')
            (Path(td) / 'dirlink').symlink_to(other)
            got = cleaner._unshared_bytes(td, private_size=_by_name({'outside': 999}))
            self.assertEqual(got, 0)

    def test_hard_linked_file_counts_zero(self):
        # Another link may survive outside the measured tree, so deleting the
        # tree would free nothing for that inode: counting it would over-promise.
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            (Path(td) / 'shared').write_bytes(b'x')
            os.link(Path(td) / 'shared', Path(other) / 'other-link')
            (Path(td) / 'solo').write_bytes(b'y')
            got = cleaner._unshared_bytes(td, private_size=_by_name({'shared': 500, 'solo': 7}))
            self.assertEqual(got, 7)

    def test_dataless_directory_is_never_entered(self):
        with tempfile.TemporaryDirectory() as td:
            evicted = Path(td) / 'evicted'
            evicted.mkdir()
            (evicted / 'inside').write_bytes(b'x')
            ino = evicted.stat().st_ino
            with mock.patch.object(cleaner, '_is_dataless',
                                   side_effect=lambda st: st.st_ino == ino), \
                 mock.patch.object(cleaner.os, 'scandir', wraps=os.scandir) as scan:
                got = cleaner._unshared_bytes(td, private_size=_by_name({'inside': 50}))
            self.assertEqual(got, 0)
            self.assertNotIn(str(evicted), [c.args[0] for c in scan.call_args_list])

    def test_never_crosses_a_mount_point(self):
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / 'mounted'
            sub.mkdir()
            (sub / 'inside').write_bytes(b'x')
            real = os.lstat(td)
            # The root reports another device, so every subdirectory looks
            # like a mount point. A plain object, not os.stat_result: on macOS
            # a hand-built stat_result has st_flags=None.
            fake = SimpleNamespace(st_mode=real.st_mode, st_dev=real.st_dev + 1,
                                   st_flags=0, st_nlink=real.st_nlink)
            with mock.patch.object(cleaner.os, 'lstat',
                                   side_effect=lambda p, *a, **k: fake
                                   if str(p) == td else os.lstat(p, *a, **k)):
                got = cleaner._unshared_bytes(td, private_size=_by_name({'inside': 50}))
            self.assertEqual(got, 0)

    def test_unanswerable_first_file_means_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'a').write_bytes(b'a')
            self.assertIsNone(cleaner._unshared_bytes(td, private_size=lambda p: None))

    def test_later_unanswerable_file_keeps_the_lower_bound(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'a').write_bytes(b'a')
            (Path(td) / 'b').write_bytes(b'b')
            answers = iter([40, None])
            got = cleaner._unshared_bytes(td, private_size=lambda p: next(answers))
            self.assertEqual(got, 40)

    def test_passed_deadline_means_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'a').write_bytes(b'a')
            got = cleaner._unshared_bytes(td, private_size=_by_name({'a': 1}),
                                          deadline=time.monotonic() - 1)
            self.assertIsNone(got)

    def test_unsupported_platform_means_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'a').write_bytes(b'a')
            with mock.patch.object(cleaner, '_load_private_size_fn', return_value=None):
                self.assertIsNone(cleaner._unshared_bytes(td))

    def test_single_file_and_symlink_rows(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'f'
            f.write_bytes(b'f')
            (Path(td) / 'l').symlink_to(f)
            reader = _by_name({'f': 9})
            self.assertEqual(cleaner._unshared_bytes(f, private_size=reader), 9)
            self.assertEqual(cleaner._unshared_bytes(Path(td) / 'l', private_size=reader), 0)


class TestStorageMapUnsharedFlag(unittest.TestCase):
    def _map(self, td, *extra):
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['cleaner.py', 'storage-map', td, '--json', *extra]), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cleaner.main()
        return json.loads(out.getvalue())

    def _tree(self, td):
        for name in ('one', 'two'):
            d = Path(td) / name
            d.mkdir()
            (d / (name + '.bin')).write_bytes(b'x' * 4096)

    def test_without_flag_contract_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(td)
            r = self._map(td)
            self.assertNotIn('unshared_bytes', r)
            for c in r['children']:
                self.assertNotIn('unshared_bytes', c)

    def test_flag_adds_lower_bounds_and_root_total(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(td)
            reader = _by_name({'one.bin': 4096, 'two.bin': 0})
            with mock.patch.object(cleaner, '_load_private_size_fn', return_value=reader):
                r = self._map(td, '--unshared')
            rows = {c['name']: c for c in r['children']}
            self.assertEqual(rows['one']['unshared_bytes'], 4096)
            self.assertEqual(rows['two']['unshared_bytes'], 0)
            self.assertEqual(rows['two']['unshared_human'], cleaner.fmt_size(0))
            self.assertEqual(r['unshared_bytes'], 4096)

    def test_unmeasurable_row_is_omitted_and_withholds_root_total(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(td)
            reader = lambda p: None if p.endswith('two.bin') else 4096
            with mock.patch.object(cleaner, '_load_private_size_fn', return_value=reader):
                r = self._map(td, '--unshared')
            rows = {c['name']: c for c in r['children']}
            self.assertEqual(rows['one']['unshared_bytes'], 4096)
            self.assertNotIn('unshared_bytes', rows['two'])
            self.assertNotIn('unshared_bytes', r)

    def test_unsupported_platform_omits_every_key(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(td)
            with mock.patch.object(cleaner, '_load_private_size_fn', return_value=None):
                r = self._map(td, '--unshared')
            self.assertNotIn('unshared_bytes', r)
            for c in r['children']:
                self.assertNotIn('unshared_bytes', c)

    def test_nested_depth_rows_are_not_measured(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(td)
            reader = _by_name({'one.bin': 4096, 'two.bin': 4096})
            with mock.patch.object(cleaner, '_load_private_size_fn', return_value=reader):
                r = self._map(td, '--unshared', '--depth', '2')
            for c in r['children']:
                self.assertIn('unshared_bytes', c)
                for nested in c.get('children', []):
                    self.assertNotIn('unshared_bytes', nested)


def _clones_supported(directory):
    # cp -c fails with ENOTSUP on a volume without clonefile; APFS succeeds.
    # (BSD `stat -f %T` reports the FILE type, not the filesystem -- the first
    # version of this check used it and silently skipped every test on CI.)
    probe = os.path.join(directory, '.clone-probe')
    with open(probe, 'wb') as f:
        f.write(b'x')
    r = subprocess.run(['cp', '-c', probe, probe + '-c'], capture_output=True)
    for p in (probe, probe + '-c'):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(p)
    return r.returncode == 0


@unittest.skipUnless(sys.platform == 'darwin', 'APFS clone accounting is macOS-only')
class TestRealApfsClones(unittest.TestCase):
    """Kernel answers on real clones, pinned against the research in #107."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td, True)
        if not _clones_supported(self.td):
            self.skipTest('temporary directory volume does not support clones')
        self.private = cleaner._load_private_size_fn()
        # On APFS the reader must exist and answer; None here means the
        # attribute request itself is wrong, which must fail, not skip.
        self.assertIsNotNone(self.private)

    def _write(self, path, size):
        with open(path, 'wb') as f:
            f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())

    def _clone(self, src, dst):
        subprocess.run(['cp', '-c', src, dst], check=True)

    def test_never_cloned_file_is_fully_private(self):
        path = os.path.join(self.td, 'solo.bin')
        self._write(path, 4 * MiB)
        # Equal to the allocated size: an inode number (LINKID, bit 0x10)
        # would not be, which is how a wrong constant fails here.
        self.assertEqual(self.private(path), os.lstat(path).st_blocks * 512)

    def test_clone_pair_shares_everything(self):
        src = os.path.join(self.td, 'big.bin')
        dst = os.path.join(self.td, 'big-clone.bin')
        self._write(src, 32 * MiB)
        self._clone(src, dst)
        self.assertEqual(self.private(src), 0)
        self.assertEqual(self.private(dst), 0)

    def test_diverged_region_becomes_private_on_both_sides(self):
        src = os.path.join(self.td, 'big.bin')
        dst = os.path.join(self.td, 'big-clone.bin')
        self._write(src, 32 * MiB)
        self._clone(src, dst)
        fd = os.open(dst, os.O_WRONLY)
        try:
            os.write(fd, os.urandom(8 * MiB))
            os.fsync(fd)
        finally:
            os.close(fd)
        self.assertEqual(self.private(dst), 8 * MiB)
        self.assertEqual(self.private(src), 8 * MiB)

    def test_tree_of_clones_reads_zero_while_du_reads_full(self):
        # The XCTestDevices shape: clones inside the tree, owner outside it.
        owner = os.path.join(self.td, 'owner.bin')
        self._write(owner, 16 * MiB)
        clones = os.path.join(self.td, 'clones')
        os.mkdir(clones)
        for i in range(3):
            self._clone(owner, os.path.join(clones, f'c{i}.bin'))
        du, _ = cleaner._du_children(self.td)
        self.assertGreaterEqual(du[clones], 3 * 16 * MiB)  # du bills every clone
        self.assertEqual(cleaner._unshared_bytes(clones), 0)

    def test_storage_map_flag_end_to_end(self):
        solo = os.path.join(self.td, 'solo')
        os.mkdir(solo)
        self._write(os.path.join(solo, 'data.bin'), 4 * MiB)
        r = cleaner._add_unshared(cleaner.scan_storage_map(self.td))
        rows = {c['name']: c for c in r['children']}
        self.assertEqual(rows['solo']['unshared_bytes'], 4 * MiB)


if __name__ == '__main__':
    unittest.main()
