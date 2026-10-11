import datetime as dt
import unittest
from unittest.mock import patch
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/report_completed_alert.py'
spec = importlib.util.spec_from_file_location('report_completed_alert', SCRIPT)
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.start = dt.datetime(2026, 10, 10, 21, 0, tzinfo=dt.timezone.utc)
        self.finish = self.start + dt.timedelta(seconds=2)
        self.now = self.finish + dt.timedelta(seconds=3)
        self.status = {'surface': 'objects', 'result': 'alert',
                       'evaluated_at': (self.start + dt.timedelta(seconds=1)).isoformat(),
                       'failure_codes': ['generated_object_restore_stale']}
        self.props = {'Result': 'exit-code', 'ExecMainStatus': '1',
                      'OnSuccess': 'careertrellis-backup-reporter@' + 'a' * 40 + '.service'}

    def check(self):
        return target.eligible(self.status, self.props, self.start, self.finish, self.now)

    def test_current_completed_alert(self):
        self.assertTrue(self.check())

    def test_previous_healthy_never_reports_recovery(self):
        self.status.update(result='ok', failure_codes=[])
        self.assertFalse(self.check())

    def test_previous_alert_rejected(self):
        self.status['evaluated_at'] = (self.start - dt.timedelta(seconds=1)).isoformat()
        self.assertFalse(self.check())

    def test_same_second_current_write_allowed(self):
        self.status['evaluated_at'] = self.start.isoformat()
        epoch_ns = int(self.start.timestamp()) * 1000000000
        self.assertTrue(target.current_eligible(self.status, self.props,
            epoch_ns + 100000000, epoch_ns + 300000000, epoch_ns + 200000000, self.now))

    def test_same_second_previous_write_rejected(self):
        self.status['evaluated_at'] = self.start.isoformat()
        epoch_ns = int(self.start.timestamp()) * 1000000000
        self.assertFalse(target.current_eligible(self.status, self.props,
            epoch_ns + 100000000, epoch_ns + 300000000, epoch_ns + 99999000, self.now))

    def test_write_after_invocation_rejected(self):
        self.status['evaluated_at'] = self.start.isoformat()
        epoch_ns = int(self.start.timestamp()) * 1000000000
        self.assertFalse(target.current_eligible(self.status, self.props,
            epoch_ns + 100000000, epoch_ns + 300000000, epoch_ns + 300001000, self.now))

    def test_future_and_stale_rejected(self):
        self.status['evaluated_at'] = (self.finish + dt.timedelta(seconds=1)).isoformat()
        self.assertFalse(self.check())
        self.status['evaluated_at'] = self.start.isoformat()
        self.now += dt.timedelta(seconds=301)
        self.assertFalse(self.check())

    def test_real_errors_and_success_not_dispatched(self):
        for result, code in [('exit-code', '2'), ('timeout', '1'), ('success', '0')]:
            self.props.update(Result=result, ExecMainStatus=code)
            self.assertFalse(self.check())

    def test_no_alert_empty_wrong_surface_or_bad_time(self):
        for key, value in [('failure_codes', []), ('surface', 'database'), ('evaluated_at', 'bad')]:
            with patch.dict(self.status, {key: value}):
                self.assertFalse(self.check())

    def test_reporter_destination_is_exactly_one_revision_instance(self):
        for reporter in ['x.service', self.props['OnSuccess'] + ' x.service', 'careertrellis-backup-reporter@database.service']:
            self.props['OnSuccess'] = reporter
            self.assertFalse(self.check())


if __name__ == '__main__':
    unittest.main()
