import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import cleaner


class TestProjectIDs(unittest.TestCase):
    def test_colliding_slugs_have_distinct_order_independent_ids(self):
        hits = []
        for name in ('foo-bar', 'foo_bar'):
            project = cleaner.HOME / 'Developer' / name
            hits.append({'path': str(project / 'node_modules'), 'project': str(project),
                         'kind': 'node_modules', 'age_days': 90, 'size_bytes': 1,
                         'git': {'dirty': True, 'unpushed': True}})
        forward = {str(t['path']): t['id'] for t in cleaner.projects_to_targets(hits)}
        reverse = {str(t['path']): t['id'] for t in cleaner.projects_to_targets(list(reversed(hits)))}
        self.assertEqual(len(set(forward.values())), 2)
        self.assertEqual(forward, reverse)

    def run_cleanup(self, legacy=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / 'home'
            projects = home / 'projects'
            for name in ('foo-bar', 'foo_bar'):
                project = projects / name
                (project / 'node_modules').mkdir(parents=True)
                (project / 'package.json').write_text('{}')
                (project / 'node_modules' / 'data').write_bytes(b'x' * 4096)
            config = root / 'config.json'
            config.write_text(json.dumps({'project_git_check': False}))
            env = {**os.environ, 'HOME': str(home), 'MACCLEANER_CONFIG': str(config),
                   'MACCLEANER_LOG': str(root / 'report.log'),
                   'MACCLEANER_SNAPSHOTS': str(root / 'snapshots.log')}
            command = [sys.executable, str(REPO / 'cleaner.py'), 'projects', '--roots', str(projects), '--min-age-days', '0', '--json']
            scanned = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(scanned.returncode, 0, scanned.stderr)
            selected = next(a['id'] for a in json.loads(scanned.stdout)['artifacts']
                            if Path(a['project']).name == 'foo-bar')
            if legacy:
                selected = 'project-projects-foo-bar-node-modules'
            result = subprocess.run(command + ['--clean', '--yes', '--targets', selected],
                                    env=env, capture_output=True, text=True, timeout=15)
            if legacy:
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertTrue((projects / 'foo-bar' / 'node_modules' / 'data').exists())
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(json.loads(result.stdout)['items']), 1)
                self.assertFalse((projects / 'foo-bar' / 'node_modules').exists())
            self.assertTrue((projects / 'foo_bar' / 'node_modules' / 'data').exists())

    def test_selecting_one_colliding_project_cleans_only_that_project(self):
        self.run_cleanup()

    def test_ambiguous_legacy_id_is_rejected(self):
        self.run_cleanup(legacy=True)
