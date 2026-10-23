import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cleaner


class TestPreviewErrors(unittest.TestCase):
    def preview(self, target):
        out = io.StringIO()
        with mock.patch.object(cleaner, 'measure_targets', side_effect=lambda ts: ts), contextlib.redirect_stdout(out):
            cleaner.run_dry_run([target], json_mode=True)
        return json.loads(out.getvalue())

    def test_unavailable_measurement_is_not_a_successful_empty_preview(self):
        result = self.preview({'id': 'fixture', 'label': 'fixture', 'size': 0,
                               'exists': False, 'measurement_error': 'fixture measurement unavailable'})
        self.assertEqual(result['items'][0].get('preview_error', ''), 'fixture measurement unavailable')
        self.assertEqual(result['items'][0]['paths'], [])
        self.assertTrue(result['dry_run'])

    def test_glob_timeout_is_reported_without_changing_preview_status(self):
        with mock.patch.object(cleaner, '_target_paths', side_effect=TimeoutError('fixture glob timed out')):
            result = self.preview({'id': 'fixture', 'label': 'fixture', 'size': 0, 'exists': True})
        self.assertIn('fixture glob timed out', result['items'][0].get('preview_error', ''))
        self.assertEqual(result['items'][0]['status'], 'would-delete')

    def test_partial_enumeration_preserves_paths_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td).resolve()
            first, second = home / 'first', home / 'second'
            first.mkdir(); second.mkdir()
            kept = first / 'sentinel'
            kept.write_text('not deleted')
            target = {'id': 'fixture', 'label': 'fixture', 'size': 0, 'exists': True,
                      'paths': [first, second], 'empty_only': True}
            with mock.patch.object(cleaner, 'HOME', home), mock.patch.object(
                    cleaner, '_cleanup_children', side_effect=[[kept], PermissionError('fixture denied')]):
                result = self.preview(target)
            self.assertIn('fixture denied', result['items'][0].get('preview_error', ''))
            self.assertEqual([p['path'] for p in result['items'][0]['paths']], [str(kept)])
            self.assertEqual(kept.read_text(), 'not deleted')
