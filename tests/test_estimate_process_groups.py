import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import cleaner

REPO = Path(__file__).resolve().parents[1]
PROBE = r'''
import cleaner
import shlex
import sys
cleaner.ESTIMATE_TIMEOUT_S = 0.5
child = (
    "import json,os,time; from pathlib import Path; "
    f"target=Path({sys.argv[1]!r}); temporary=target.with_suffix('.ready'); "
    "temporary.write_text(json.dumps([os.getpid(), os.getpgrp()])); temporary.replace(target); "
    "time.sleep(10)"
)
command = shlex.quote(sys.executable) + " -c " + shlex.quote(child) + " & "
if sys.argv[2] == "timeout":
    command += "wait"
else:
    command += "while [ ! -f " + shlex.quote(sys.argv[1]) + " ]; do sleep 0.01; done; printf '7\\n'"
print(cleaner._run_estimate(command, "du_path"), flush=True)
'''


class TestEstimateProcessGroups(unittest.TestCase):
    def check_descendant(self, mode, expected):
        with tempfile.TemporaryDirectory() as td:
            record = Path(td) / 'child.json'
            process = subprocess.Popen(
                [sys.executable, '-c', PROBE, str(record), mode], cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True)
            try:
                out, err = process.communicate(timeout=4)
                self.assertEqual(process.returncode, 0, err)
                self.assertEqual(out.strip(), str(expected))
                self.assertTrue(record.exists(), 'fixture child must have started')
                pid, _ = json.loads(record.read_text())
                # A killed child can briefly remain a zombie until init reaps it.
                deadline = time.monotonic() + 1
                while True:
                    result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                                            capture_output=True, text=True, timeout=2)
                    self.assertEqual(result.stderr, "", "process-state inspection must succeed")
                    state = result.stdout.strip()
                    if not state or state.startswith('Z'):
                        break
                    if time.monotonic() >= deadline:
                        self.fail(f'estimate descendant {pid} still running: {state}')
                    time.sleep(0.02)
            finally:
                groups = {process.pid}
                if record.exists():
                    groups.add(json.loads(record.read_text())[1])
                for group in groups:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.communicate(timeout=3)

    def test_timeout_terminates_shell_descendant(self):
        self.check_descendant('timeout', 0)

    def test_shell_exit_terminates_remaining_descendant(self):
        self.check_descendant('success', 7 * 1024)

    def test_nonzero_exit_discards_output(self):
        self.assertEqual(cleaner._run_estimate("printf '7\\n'; exit 1", 'du_path'), 0)
