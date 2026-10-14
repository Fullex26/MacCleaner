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
