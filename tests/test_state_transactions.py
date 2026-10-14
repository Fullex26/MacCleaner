"""Exercise real competing processes, not only atomic file replacement."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
WORKER = r'''
import datetime, json, sys, time
from pathlib import Path
import cleaner
root, kind, index = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
cleaner.LOG_PATH = root / 'report.log'
cleaner.SNAPSHOTS_PATH = root / 'snapshots.log'
cleaner.ALERTS_PATH = root / 'alerts.json'
cleaner.CONFIG_PATH = root / 'config.json'
cfg = cleaner.load_config()
cleaner.disk_free = lambda: 'test disk'
cleaner.disk_stats = lambda: {'free_bytes': 1, 'total_bytes': 100}
cleaner._trim_cron_log = lambda: None
original = cleaner._atomic_write_json
def slow_write(path, data):
    if index == 0:
        (root / 'first-writing').touch()
        deadline = time.monotonic() + 3
        while not (root / 'release').exists() and time.monotonic() < deadline:
            time.sleep(.01)
    original(path, data)
cleaner._atomic_write_json = slow_write
(root / ('started-' + str(index))).touch()
if kind == 'report':
    cleaner.write_log(index, [])
elif kind == 'snapshot':
    class Clock(datetime.datetime):
        @classmethod
        def now(cls): return cls(2026, 1, index + 1)
    cleaner.datetime.datetime = Clock
    cleaner.record_snapshot(index, {})
elif kind == 'config':
    cleaner.cmd_config_set_key(cfg, 'delete_mode' if index == 0 else 'project_min_age_days',
                               '"trash"' if index == 0 else '60')
else:
    result = cleaner.run_disk_check({'low_disk_alerts': True}, json_mode=True, post=False)
    (root / ('result-' + str(index))).write_text(json.dumps(result))
'''


class TestStateTransactions(unittest.TestCase):
    def run_competing(self, kind):
        import time
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            def wait_for(path):
                deadline = time.monotonic() + 5
                while not path.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(path.exists(), str(path))
            processes = []
            try:
                for index in range(2):
                    processes.append(subprocess.Popen(
                        [sys.executable, '-c', WORKER, td, kind, str(index)],
                        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
                    wait_for(root / ('first-writing' if index == 0 else 'started-1'))
                # The first writer is paused after its read. Give its competitor
                # time to attempt the transaction before releasing that writer.
                time.sleep(.3)
                (root / 'release').touch()
                for process in processes:
                    out, err = process.communicate(timeout=8)
                    self.assertEqual(process.returncode, 0, out + err)
                if kind == 'report':
                    self.assertEqual({r['total_freed_bytes'] for r in json.loads((root / 'report.log').read_text())}, {0, 1})
                elif kind == 'snapshot':
                    self.assertEqual({r['reclaimable_bytes'] for r in json.loads((root / 'snapshots.log').read_text())}, {0, 1})
                elif kind == 'config':
                    cfg = json.loads((root / 'config.json').read_text())
                    self.assertEqual(cfg['delete_mode'], 'trash')
                    self.assertEqual(cfg['project_min_age_days'], 60)
                else:
                    claims = [json.loads((root / ('result-' + str(i))).read_text())['should_notify'] for i in range(2)]
                    self.assertEqual(sum(claims), 1)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()

    def test_concurrent_report_appends_are_preserved(self):
        self.run_competing('report')

    def test_concurrent_snapshot_days_are_preserved(self):
        self.run_competing('snapshot')

    def test_concurrent_config_changes_are_preserved(self):
        self.run_competing('config')

    def test_concurrent_alert_checks_claim_only_once(self):
        self.run_competing('alert')

    def test_lock_wait_is_bounded_and_process_exit_releases_it(self):
        import time
        from unittest import mock
        sys.path.insert(0, str(REPO))
        import cleaner
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'state.json'
            code = "import fcntl,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); time.sleep(30)"
            holder = subprocess.Popen([sys.executable, '-c', code, str(path) + '.lock'],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), 'locked')
                started = time.monotonic()
                with mock.patch.object(cleaner, 'STATE_LOCK_TIMEOUT_S', .05):
                    with self.assertRaises(TimeoutError):
                        with cleaner._state_transaction(path):
                            self.fail('entered a locked transaction')
                self.assertLess(time.monotonic() - started, 1)
                holder.kill()
                holder.communicate(timeout=3)
                with cleaner._state_transaction(path):
                    pass
                self.assertTrue(Path(str(path) + '.lock').exists())
            finally:
                if holder.poll() is None:
                    holder.kill()
                holder.communicate()

    def test_disk_check_lock_timeout_returns_json_without_notification_claim(self):
        import contextlib
        import io
        from unittest import mock
        import cleaner
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(cleaner, '_trim_cron_log'), mock.patch.object(cleaner, 'disk_stats', return_value={'free_bytes': 1}), mock.patch.object(cleaner, '_state_transaction', side_effect=TimeoutError('busy')), mock.patch.object(cleaner, '_notify') as notify, contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = cleaner.run_disk_check({'low_disk_alerts': True}, json_mode=True)
        self.assertTrue(result['below_threshold'])
        self.assertFalse(result['should_notify'])
        self.assertFalse(result['notified'])
        self.assertEqual(json.loads(output.getvalue())['should_notify'], False)
        self.assertIn('could not update alert state', errors.getvalue())
        notify.assert_not_called()
