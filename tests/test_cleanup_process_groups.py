import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
PROBE = r'''
import cleaner
import shlex
import subprocess
import sys
cleaner.CLEANUP_TIMEOUT_S = 0.3
original_run = subprocess.run
def short_run(*args, **kwargs):
    if kwargs.get("timeout") == 600:
        kwargs["timeout"] = 0.3
    return original_run(*args, **kwargs)
cleaner.subprocess.run = short_run
child = (
    "import json,os,time; from pathlib import Path; "
    f"record=Path({sys.argv[1]!r}); marker=Path({sys.argv[2]!r}); "
    "temporary=record.with_suffix('.ready'); "
    "temporary.write_text(json.dumps([os.getpid(),os.getpgrp()])); temporary.replace(record); "
    "time.sleep(0.8); marker.write_text('continued after timeout'); time.sleep(10)"
)
command = shlex.quote(sys.executable) + " -c " + shlex.quote(child) + " & wait"
freed, error = cleaner.delete_target({"cmd": command})
assert freed == 0 and error, (freed, error)
print("timeout reported", flush=True)
'''


class TestCleanupProcessGroups(unittest.TestCase):
    def test_timeout_stops_descendant_before_later_work(self):
        with tempfile.TemporaryDirectory() as td:
            record, marker = Path(td) / 'pid.json', Path(td) / 'late-write'
            process = subprocess.Popen(
                [sys.executable, '-c', PROBE, str(record), str(marker)], cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True)
            try:
                out, err = process.communicate(timeout=4)
                self.assertEqual(process.returncode, 0, err)
                self.assertEqual(out.strip(), 'timeout reported')
                self.assertTrue(record.exists(), 'fixture child must have started')
                time.sleep(1)
                self.assertFalse(marker.exists(), 'cleanup continued after reporting timeout')
                pid, _ = json.loads(record.read_text())
                state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                                       capture_output=True, text=True, timeout=2)
                self.assertEqual(state.stderr, '')
                self.assertTrue(not state.stdout.strip() or state.stdout.strip().startswith('Z'))
            finally:
                groups = {process.pid}
                try:
                    groups.add(json.loads(record.read_text())[1])
                except (OSError, ValueError, IndexError):
                    pass
                for group in groups:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.communicate(timeout=3)
