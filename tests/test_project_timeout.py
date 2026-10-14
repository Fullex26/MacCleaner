import io
from contextlib import redirect_stderr
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cleaner


class TestProjectWalkDeadline(unittest.TestCase):
    def test_blocked_scandir_returns_completed_artifacts_within_budget(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            good, blocked = root / 'good', root / 'blocked'
            (good / 'node_modules').mkdir(parents=True)
            (good / 'package.json').write_text('{}')
            blocked.mkdir()
            release = threading.Event()
            original = cleaner.os.scandir
            def scandir(path):
                if Path(path) == blocked:
                    release.wait(1)
                return original(path)
            errors = io.StringIO()
            try:
                with mock.patch.object(cleaner, 'PROJECT_SCAN_TIME_BUDGET_S', .05, create=True), mock.patch.object(cleaner.os, 'scandir', scandir), mock.patch.object(cleaner, 'get_size', return_value=4096), redirect_stderr(errors):
                    started = time.monotonic()
                    hits, roots, age = cleaner.scan_projects({'project_git_check': False}, roots=[str(good), str(blocked)], min_age_days=0)
                    elapsed = time.monotonic() - started
                self.assertLess(elapsed, .5, 'blocked readdir must not hold up the caller')
                self.assertEqual([h['path'] for h in hits], [str(good / 'node_modules')])
                self.assertIn('partial', errors.getvalue())
            finally:
                release.set()

    def test_blocked_discovery_does_not_keep_cli_process_alive(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            code = '''
import sys, threading
import cleaner
cleaner.PROJECT_SCAN_TIME_BUDGET_S = .05
cleaner.os.scandir = lambda path: threading.Event().wait()
hits, roots, age = cleaner.scan_projects({'project_git_check': False}, roots=[sys.argv[1]], min_age_days=0)
assert hits == []
'''
            result = subprocess.run([sys.executable, '-c', code, td],
                                    cwd=Path(__file__).resolve().parent.parent,
                                    capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('partial', result.stderr)
