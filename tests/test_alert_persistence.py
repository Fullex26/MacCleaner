import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cleaner


class TestAlertPersistence(unittest.TestCase):
    def test_app_delivery_is_not_claimed_when_throttle_write_fails(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            err = io.StringIO()
            with mock.patch.object(cleaner, 'ALERTS_PATH', Path(td) / 'alerts.json'), \
                 mock.patch.object(cleaner, 'disk_stats', return_value={'free_bytes': 1}), \
                 mock.patch.object(cleaner, '_trim_cron_log'), \
                 mock.patch.object(cleaner, '_atomic_write_json', side_effect=OSError('unwritable')), \
                 mock.patch.object(cleaner, '_notify') as notify, contextlib.redirect_stderr(err):
                for _ in range(2):
                    result = cleaner.run_disk_check({'low_disk_alerts': True}, json_mode=True, post=False)
                    self.assertFalse(result['should_notify'])
                    self.assertFalse(result['notified'])
                    self.assertTrue(result['below_threshold'])
                notify.assert_not_called()
            self.assertIn('could not write alert state', err.getvalue())

    def test_successful_delivery_remains_reported_if_followup_save_fails(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with mock.patch.object(cleaner, 'ALERTS_PATH', Path(td) / 'alerts.json'), \
                 mock.patch.object(cleaner, 'disk_stats', return_value={'free_bytes': 1}), \
                 mock.patch.object(cleaner, '_trim_cron_log'), \
                 mock.patch.object(cleaner, '_atomic_write_json', side_effect=OSError('unwritable')), \
                 mock.patch.object(cleaner, '_notify', return_value=True):
                result = cleaner.run_disk_check({'low_disk_alerts': True}, json_mode=True)
                self.assertTrue(result['notified'])
