import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import cleaner

REPO = Path(__file__).resolve().parents[1]
PROBE = r'''
import cleaner
import os
from pathlib import Path
import time
import signal
import shlex
import subprocess
import sys
cleaner.CLEANUP_TIMEOUT_S = 600 if sys.argv[3] != "timeout" else 0.3
original_run = subprocess.run
def short_run(*args, **kwargs):
    if kwargs.get("timeout") == 600 and sys.argv[3] not in ("interrupt", "terminate"):
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
if sys.argv[3].endswith("-launch"):
    original_popen = subprocess.Popen
    def interrupt_launch(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        deadline = time.monotonic() + 3
        while not Path(sys.argv[1]).exists() and time.monotonic() < deadline:
            time.sleep(.005)
        assert Path(sys.argv[1]).exists(), "fixture child must start"
        os.kill(os.getpid(), signal.SIGINT if sys.argv[3] == "interrupt-launch" else signal.SIGTERM)
        return process
    cleaner.subprocess.Popen = interrupt_launch
previous_handler = signal.getsignal(signal.SIGTERM)
freed, error = cleaner.delete_target({"cmd": command})
assert freed == 0 and error, (freed, error)
assert signal.getsignal(signal.SIGTERM) == previous_handler
print("timeout reported", flush=True)
'''


class TestCleanupProcessGroups(unittest.TestCase):
    def test_timeout_stops_descendant_before_later_work(self):
        self.check_descendant("timeout")

    def test_interrupt_stops_descendant_before_later_work(self):
        self.check_descendant("interrupt")

    def test_termination_stops_descendant_before_later_work(self):
        self.check_descendant("terminate")

    def test_interrupt_during_launch_stops_descendant(self):
        self.check_descendant("interrupt-launch")

    def test_termination_during_launch_stops_descendant(self):
        self.check_descendant("terminate-launch")

    def test_restores_signal_handler_after_launch_failure(self):
        previous = signal.getsignal(signal.SIGTERM)
        with mock.patch.object(cleaner.subprocess, "Popen", side_effect=OSError("fixture")):
            self.assertIsNotNone(cleaner.delete_target({"cmd": "unused"})[1])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def check_descendant(self, mode):
        with tempfile.TemporaryDirectory() as td:
            record, marker = Path(td) / 'pid.json', Path(td) / 'late-write'
            process = subprocess.Popen(
                [sys.executable, '-c', PROBE, str(record), str(marker), mode], cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True)
            try:
                if mode in ("interrupt", "terminate"):
                    deadline = time.monotonic() + 3
                    while not record.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(record.exists(), 'fixture child must have started')
                    process.send_signal(signal.SIGINT if mode == "interrupt" else signal.SIGTERM)
                out, err = process.communicate(timeout=4)
                if mode == "timeout":
                    self.assertEqual(process.returncode, 0, err)
                    self.assertEqual(out.strip(), 'timeout reported')
                else:
                    self.assertNotEqual(process.returncode, 0)
                    if mode.startswith('interrupt'):
                        self.assertIn('KeyboardInterrupt', err)
                    else:
                        self.assertEqual(process.returncode, 143)
                self.assertTrue(record.exists(), 'fixture child must have started')
                time.sleep(1)
                self.assertFalse(marker.exists(), 'cleanup continued after timeout or interruption')
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
