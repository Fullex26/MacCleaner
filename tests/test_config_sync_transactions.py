import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import cleaner

REPO = Path(__file__).resolve().parent.parent
WORKER = r'''
import sys, time
from pathlib import Path
import cleaner as c
root, action, role, first_kind = Path(sys.argv[1]), *sys.argv[2:]
c.CONFIG_PATH = root / 'config.json'
c._icloud_config_dir = lambda: root / 'cloud'
def pause():
    (root / 'paused').touch()
    deadline = time.monotonic() + 5
    while not (root / 'release').exists() and time.monotonic() < deadline:
        time.sleep(.01)
if role == 'first':
    if first_kind == 'sync':
        original = c.load_config
        def stale_read():
            result = original()
            pause()
            return result
        c.load_config = stale_read
    else:
        original = c._atomic_write_json
        def delayed_write(path, value):
            pause()
            original(path, value)
        c._atomic_write_json = delayed_write
kind = first_kind if role == 'first' else ('setting' if first_kind == 'sync' else 'sync')
(root / (role + '-started')).touch()
if kind == 'sync':
    c.run_config_sync(action)
else:
    c.cmd_config_set_key(c.load_config(), 'delete_mode', '"trash"')
'''


class TestConfigSyncTransactions(unittest.TestCase):
    def run_race(self, action, first_kind):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            local = root / 'config.json'
            cloud = root / 'cloud/config.json'
            cloud.parent.mkdir()
            destination = cloud if action == 'off' else local
            destination.write_text(json.dumps(cleaner.DEFAULT_CONFIG))
            if action == 'off':
                local.symlink_to(cloud)
            processes = []
            def wait_for(name):
                deadline = time.monotonic() + 5
                while not (root / name).exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue((root / name).exists(), name)
            try:
                for role in ('first', 'second'):
                    processes.append(subprocess.Popen(
                        [sys.executable, '-c', WORKER, td, action, role, first_kind], cwd=REPO,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
                    wait_for('paused' if role == 'first' else 'second-started')
                time.sleep(.3)
                (root / 'release').touch()
                for process in processes:
                    out, err = process.communicate(timeout=8)
                    self.assertEqual(process.returncode, 0, out + err)
                self.assertEqual(json.loads(local.read_text())['delete_mode'], 'trash')
                self.assertEqual(local.is_symlink(), action == 'on')
                if action == 'on':
                    self.assertEqual(json.loads(cloud.read_text())['delete_mode'], 'trash')
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()

    def test_sync_off_then_setting_preserves_new_mode(self):
        self.run_race('off', 'sync')

    def test_setting_then_sync_off_preserves_new_mode(self):
        self.run_race('off', 'setting')

    def test_setting_then_sync_on_preserves_binding_and_new_mode(self):
        self.run_race('on', 'setting')
