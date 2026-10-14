import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
PROBE = r'''
import os, sys, threading, time
from pathlib import Path
import cleaner as c
root, case = Path(sys.argv[1]), sys.argv[2]
c.READ_ONLY_SCAN_BUDGET_S = .05
c.STORAGE_MAP_SCAN_BUDGET_S = .05
def blocked(*args, **kwargs):
    print('ENTERED_BLOCKED_ENUMERATION', flush=True)
    threading.Event().wait()
real_scandir = c.os.scandir
c.os.scandir = blocked
if case == 'tmp':
    c.TMP_SCAN_ROOT = root
    assert c.scan_tmp_artifacts({}) == []
elif case == 'apps':
    c._installed_apps_dirs = lambda: [root]
    assert c.installed_bundle_ids() is None
elif case == 'leftovers':
    c.installed_bundle_ids = lambda: set()
    c._leftover_library_root = lambda: root
    assert c.scan_app_leftovers({}) == []
elif case == 'inventory_safety':
    apps, lib = root / 'apps', root / 'Library'
    apps.mkdir()
    candidate = lib / 'Caches/com.keep.app'
    candidate.mkdir(parents=True)
    (candidate / 'blob').write_bytes(b'x')
    old = time.time() - 10 * 86400
    os.utime(candidate, (old, old))
    c.os.scandir = lambda path: blocked() if Path(path) == apps else real_scandir(path)
    c._installed_apps_dirs = lambda: [apps]
    c._leftover_library_root = lambda: lib
    c._mdfind_confirms_installed = lambda ids: set()
    assert c.scan_app_leftovers({}) == []
elif case == 'storage_map':
    c._du_children = lambda path: ({}, 0)
    result = c.scan_storage_map(root)
    assert result['children'] == [] and result['incomplete'] is True
elif case == 'logs':
    c.HOME = root
    (root / 'Library/Logs').mkdir(parents=True)
    c.Path.iterdir = blocked
    c.get_size = lambda path: 1
    rows = c.get_targets({'enabled_categories': ['logs', 'node']})
    assert any(t['id'] == 'npm-cache' for t in rows)
    assert not any(t['category'] == 'logs' for t in rows)
elif case == 'system_temp':
    temp_root = root / 'var/folders/test/T'
    temp_root.mkdir(parents=True)
    os.environ['TMPDIR'] = str(temp_root)
    c.subprocess.run = lambda *a, **kw: type('Result', (), {'stdout': '1 test'})()
    assert c._system_temp_usage() is None
elif case in ('glob_measure', 'glob_delete', 'empty_preview', 'empty_delete'):
    c.HOME = root
    directory = root / 'cache'
    directory.mkdir()
    sentinel = directory / 'keep.txt'
    sentinel.write_text('keep')
    target = {'id': 'fixture', 'label': 'fixture', 'category': 'node',
              'description': 'fixture', 'safe': True, 'path': directory}
    c.get_size = lambda path: 1
    def mutation(path):
        raise AssertionError('mutation must not run after failed discovery')
    c._remove = mutation
    if case.startswith('glob'):
        target.update(path=None, glob=str(directory / '*'))
    else:
        target.update(empty_only=True, size=1, exists=True)
        c.Path.iterdir = blocked
    if case == 'glob_measure':
        c.measure_targets([target])
        assert target['size'] == 0 and target['measurement_error']
        assert c.reclaimable_total([target]) == 0
        import contextlib, io, json
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            c.scan_json([target])
        assert json.loads(output.getvalue())['targets'][0]['measurement_error']
        total, items = c.run_dry_run([target], json_mode=True)
        assert total == 0 and items[0]['paths'] == []
    elif case.endswith('delete'):
        freed, error = c.delete_target(target)
        assert freed == 0 and error
        if case == 'glob_delete':
            c.LOG_PATH, c.SNAPSHOTS_PATH = root / 'report.log', root / 'snapshots.log'
            freed, items = c.run_clean([target], auto_approve=True, explicit=True, json_mode=True)
            assert freed == 0 and items[0]['status'] == 'error'
    else:
        total, items = c.run_dry_run([target], json_mode=True)
        assert total == 0 and items[0]['paths'] == []
    assert sentinel.read_text() == 'keep'
print('RETURNED', flush=True)
'''


class TestReadOnlyDeadlines(unittest.TestCase):
    def check_probe(self, case):
        with tempfile.TemporaryDirectory() as td:
            process = subprocess.Popen([sys.executable, '-c', PROBE, td, case], cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                try:
                    out, err = process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    out, err = process.communicate()
                    self.fail(f'{case} enumeration exceeded external deadline: {out}{err}')
                self.assertEqual(process.returncode, 0, out + err)
                self.assertIn('ENTERED_BLOCKED_ENUMERATION', out)
                self.assertIn('RETURNED', out)
                self.assertIn('Warning:', err)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate()

    def test_tmp_discovery_returns_after_blocked_read(self): self.check_probe('tmp')
    def test_inventory_returns_unknown_after_blocked_read(self): self.check_probe('apps')
    def test_leftover_discovery_returns_after_blocked_read(self): self.check_probe('leftovers')
    def test_incomplete_inventory_cannot_offer_app_leftovers(self): self.check_probe('inventory_safety')
    def test_storage_map_returns_after_blocked_read(self): self.check_probe('storage_map')
    def test_log_timeout_preserves_static_targets(self): self.check_probe('logs')
    def test_system_temp_returns_after_blocked_read(self): self.check_probe('system_temp')

    def test_glob_measurement_returns_after_blocked_read(self): self.check_probe('glob_measure')
    def test_glob_cleanup_reports_discovery_failure(self): self.check_probe('glob_delete')
    def test_empty_only_preview_returns_after_blocked_read(self): self.check_probe('empty_preview')
    def test_empty_only_cleanup_reports_discovery_failure(self): self.check_probe('empty_delete')

    def test_sizing_subprocess_is_reaped_before_cli_exit(self):
        probe = r'''
import os, subprocess, sys
import cleaner as c
c.STORAGE_MAP_SCAN_BUDGET_S = .05
original_run = subprocess.run
announce = os.dup(1)
def fake_du(args, **kwargs):
    child = ('import os,time; '
             f'os.write({announce}, ("DU_PID=%d\\n" % os.getpid()).encode()); '
             'time.sleep(10)')
    kwargs['timeout'] = .2
    return original_run([sys.executable, '-c', child], pass_fds=(announce,), **kwargs)
c.subprocess.run = fake_du
c.scan_storage_map(sys.argv[1])
print('RETURNED', flush=True)
'''
        with tempfile.TemporaryDirectory() as td:
            process = subprocess.Popen([sys.executable, '-c', probe, td], cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                try:
                    out, err = process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    self.fail('sizing child survived the CLI or exceeded its timeout')
                self.assertEqual(process.returncode, 0, out + err)
                self.assertIn('RETURNED', out)
                pid = int(next(line.split('=')[1] for line in out.splitlines()
                               if line.startswith('DU_PID=')))
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            finally:
                # A daemon regression can leave children after the CLI exits.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()

    def test_minimum_free_reports_failed_measurements(self):
        import contextlib
        import io
        import json
        from unittest import mock
        import cleaner
        target = {'id': 'fixture', 'label': 'fixture', 'safe': True, 'exists': False,
                  'size': 0, 'measurement_error': 'glob enumeration timed out'}
        output = io.StringIO()
        with mock.patch.object(cleaner, 'disk_stats', return_value={'free_bytes': 0}), \
                mock.patch.object(cleaner, 'disk_free', return_value='fixture disk'), \
                mock.patch.object(cleaner, 'write_log'), mock.patch.object(cleaner, 'record_snapshot'), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            cleaner.run_clean_min_free([target], 1, json_mode=True)
        result = json.loads(output.getvalue())
        self.assertFalse(result['target_met'])
        self.assertEqual(result['freed_bytes'], 0)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['status'], 'error')
