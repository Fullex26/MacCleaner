import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
PROBE = r'''
import cleaner
import shlex
import sys
cleaner.ESTIMATE_TIMEOUT_S = 0.5
child = (
    "import json,os,time; from pathlib import Path; "
    f"Path({sys.argv[1]!r}).write_text(json.dumps([os.getpid(), os.getpgrp()])); "
    "time.sleep(10)"
)
command = shlex.quote(sys.executable) + " -c " + shlex.quote(child) + " & wait"
print(cleaner._run_estimate(command, "du_path"), flush=True)
'''


class TestEstimateProcessGroups(unittest.TestCase):
    def test_timeout_terminates_shell_descendant(self):
        with tempfile.TemporaryDirectory() as td:
            record = Path(td) / 'child.json'
            process = subprocess.Popen(
                [sys.executable, '-c', PROBE, str(record)], cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True)
            try:
                out, err = process.communicate(timeout=4)
                self.assertEqual(process.returncode, 0, err)
                self.assertEqual(out.strip(), '0')
                self.assertTrue(record.exists(), 'fixture child must have started')
                pid, _ = json.loads(record.read_text())
                # A killed child can briefly remain a zombie until init reaps it.
                result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                                        capture_output=True, text=True, timeout=2)
                self.assertEqual(result.stderr, "", "process-state inspection must succeed")
                self.assertTrue(not result.stdout.strip() or
                                result.stdout.lstrip().startswith('Z'),
                                f'estimate descendant {pid} still running: {result.stdout}')
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
