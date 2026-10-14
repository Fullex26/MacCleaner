import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ENGINE = Path(__file__).resolve().parent.parent / 'cleaner.py'


class TestProjectExclusions(unittest.TestCase):
    def run_case(self, exclusion, clean=False, alias=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / 'home'
            protected = home / 'projects' / 'protected'
            allowed = home / 'projects' / 'allowed'
            for project in (protected, allowed):
                (project / 'node_modules').mkdir(parents=True)
                (project / 'package.json').write_text('{}')
                (project / 'node_modules' / 'data').write_bytes(b'x' * 4096)
            excluded = protected if exclusion == 'project' else protected / 'node_modules' / 'data'
            config = root / 'config.json'
            config.write_text(json.dumps({'skip_paths': [str(excluded)], 'project_git_check': False}))
            scan_root = home / 'projects'
            if alias:
                scan_root = home / 'alias'
                scan_root.symlink_to(home / 'projects', target_is_directory=True)
            args = [sys.executable, str(ENGINE), 'projects', '--roots', str(scan_root),
                    '--min-age-days', '0', '--json']
            if clean:
                args += ['--clean', '--yes']
            env = {**os.environ, 'HOME': str(home), 'MACCLEANER_CONFIG': str(config),
                   'MACCLEANER_LOG': str(root / 'report.log'),
                   'MACCLEANER_SNAPSHOTS': str(root / 'snapshots.log')}
            result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            if clean:
                self.assertTrue((protected / 'node_modules' / 'data').exists())
                self.assertFalse((allowed / 'node_modules').exists())
                self.assertEqual(len(data['items']), 1)
            else:
                self.assertEqual(len(data['artifacts']), 1)
                self.assertEqual(Path(data['artifacts'][0]['project']).name, 'allowed')

    def test_excluded_project_is_not_offered(self):
        self.run_case('project')

    def test_excluded_descendant_protects_whole_artifact_during_cleanup(self):
        self.run_case('descendant', clean=True)

    def test_root_alias_cannot_bypass_project_exclusion(self):
        self.run_case('project', alias=True)
