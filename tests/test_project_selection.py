import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent / "cleaner.py"


class TestProjectEmptySelection(unittest.TestCase):
    def check_empty_selection(self, dry_run):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            project = home / "project"
            artifact = project / "node_modules"
            artifact.mkdir(parents=True)
            (project / "package.json").write_text("{}")
            (artifact / "data").write_bytes(b"x" * 4096)
            env = {**os.environ, "HOME": str(home),
                   "MACCLEANER_CONFIG": str(root / "config.json"),
                   "MACCLEANER_LOG": str(root / "report.log"),
                   "MACCLEANER_SNAPSHOTS": str(root / "snapshots.log")}
            args = [sys.executable, str(ENGINE), "projects", "--roots", str(home),
                    "--min-age-days", "0", "--targets", "", "--yes", "--json",
                    "--dry-run" if dry_run else "--clean"]
            result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["items"], [])
            self.assertTrue((artifact / "data").exists())
            if dry_run:
                self.assertFalse((root / "report.log").exists())
                self.assertFalse((root / "snapshots.log").exists())

    def test_explicit_empty_selection_does_not_clean_projects(self):
        self.check_empty_selection(False)

    def test_explicit_empty_selection_does_not_preview_projects(self):
        self.check_empty_selection(True)
