import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cleaner


class TestMinFreePreview(unittest.TestCase):
    def preview(self, free_bytes, *flags):
        targets = [{'id': tid, 'label': tid, 'safe': safe, 'cmd': 'never-run',
                    'size': 4096, 'exists': True} for tid, safe in [('safe', True), ('review', False)]]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, 'argv', ['cleaner.py', 'clean', '--dry-run', '--json', *flags]))
            stack.enter_context(mock.patch.object(cleaner, 'load_config', return_value=cleaner.DEFAULT_CONFIG.copy()))
            stack.enter_context(mock.patch.object(cleaner, 'collect_targets', return_value=targets))
            stack.enter_context(mock.patch.object(cleaner, 'measure_targets', side_effect=lambda ts: ts))
            stack.enter_context(mock.patch.object(cleaner, 'disk_stats', return_value={'free_bytes': free_bytes}))
            stack.enter_context(mock.patch.object(cleaner, 'disk_free', return_value='test disk'))
            effects = [stack.enter_context(mock.patch.object(cleaner, name)) for name in
                       ('delete_target', 'write_log', 'record_snapshot', '_notify')]
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            cleaner.main()
            for effect in effects:
                effect.assert_not_called()
        return json.loads(out.getvalue()), err.getvalue()

    def test_named_review_target_is_excluded_in_minimum_free_preview(self):
        result, _ = self.preview(0, '--min-free', '1', '--targets', 'review', '--notify')
        self.assertEqual(result['items'], [])

    def test_already_met_floor_previews_nothing(self):
        result, _ = self.preview(2 * 1024 ** 3, '--min-free', '1')
        self.assertEqual(result['items'], [])

    def test_plain_preview_still_accepts_named_review_target(self):
        result, _ = self.preview(0, '--targets', 'review')
        self.assertEqual([item['id'] for item in result['items']], ['review'])

    def test_invalid_threshold_is_rejected_before_discovery(self):
        for value in ('nan', 'inf', '-1', '1e308'):
            for mode in ('--dry-run', '--yes'):
                with self.subTest(value=value, mode=mode), contextlib.redirect_stderr(io.StringIO()):
                    with mock.patch.object(sys, 'argv', ['cleaner.py', 'clean', '--min-free', value, mode]), \
                         mock.patch.object(cleaner, 'collect_targets') as collect:
                        with self.assertRaises(SystemExit) as error:
                            cleaner.main()
                        self.assertEqual(error.exception.code, 2)
                        collect.assert_not_called()

    def test_unmet_preview_marks_candidates_without_predicting_reclamation(self):
        result, warning = self.preview(123, '--min-free', '1', '--targets', 'safe,review', '--trash')
        self.assertEqual([item['id'] for item in result['items']], ['safe'])
        self.assertTrue(result['selection_is_upper_bound'])
        self.assertFalse(result['target_met'])
        self.assertEqual(result['free_bytes'], 123)
        self.assertEqual(result['min_free_gb'], 1)
        self.assertIn('execution may stop earlier', warning)

    def test_empty_selection_stays_empty(self):
        result, _ = self.preview(0, '--min-free', '1', '--targets', '')
        self.assertEqual(result['items'], [])

    def test_unknown_target_still_fails_when_floor_is_met(self):
        with self.assertRaises(SystemExit) as error:
            self.preview(2 * 1024 ** 3, '--min-free', '1', '--targets', 'missing')
        self.assertEqual(error.exception.code, 1)
