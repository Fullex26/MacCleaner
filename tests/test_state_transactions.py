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

    def test_concurrent_alert_checks_claim_only_once(self):
        self.run_competing('alert')
