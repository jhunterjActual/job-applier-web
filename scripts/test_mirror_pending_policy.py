from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from mirror_pending_policy import MISMATCH, Observation, classify


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class MirrorPendingTests(unittest.TestCase):
    def setUp(self):
        self.observation = Observation(
            now=utc('2026-10-07T04:01:30'),
            backup_completed=utc('2026-10-07T03:19:14'),
            mirror_completed=utc('2026-10-06T04:44:50'),
            previous_backup_label='synthetic-previous', mirrored_label='synthetic-previous',
            failure_codes=(MISMATCH,), evidence_verified=True,
            schedule_verified=True, alert_active=False)

    def test_recorded_gap_is_pending_but_not_healthy(self):
        result = classify(self.observation)
        self.assertEqual('pending', result.state)
        self.assertEqual(utc('2026-10-07T05:15:00'), result.deadline)
        self.assertEqual((MISMATCH,), result.original_failure_codes)
        self.assertFalse(result.clear_existing_alert)

    def test_deadline_boundary_and_later_are_alerts(self):
        for moment, state in [('05:14:59', 'pending'), ('05:15:00', 'alert'), ('06:01:00', 'alert')]:
            with self.subTest(moment=moment):
                self.assertEqual(state, classify(replace(self.observation,
                    now=utc('2026-10-07T' + moment))).state)

    def test_repeated_evaluations_cannot_extend_deadline(self):
        original = classify(self.observation).deadline
        for minute in (5, 30, 60, 90):
            self.assertEqual(original, classify(replace(self.observation,
                now=self.observation.now + timedelta(minutes=minute))).deadline)

    def test_three_hour_cap_and_no_next_day_grace(self):
        result = classify(replace(self.observation, backup_completed=utc('2026-10-07T00:01:00')))
        self.assertEqual('alert', result.state)
        self.assertEqual(utc('2026-10-07T03:01:00'), result.deadline)
        for completed in ('04:30:00', '06:00:00', '23:59:00'):
            with self.subTest(completed=completed):
                result = classify(replace(self.observation,
                    now=utc('2026-10-08T00:01:00'), backup_completed=utc('2026-10-07T' + completed)))
                self.assertEqual('alert', result.state)

    def test_all_other_failures_are_preserved(self):
        for code in ('pgbackrest_mirror_failed', 'pgbackrest_mirror_unit_failed',
                     'pgbackrest_mirror_unit_missed', 'pgbackrest_mirror_stale',
                     'pgbackrest_mirror_receipt_invalid', 'wal_failed_count_growth',
                     'health_status_stale', 'future_new_failure'):
            with self.subTest(code=code):
                codes = (MISMATCH, code)
                result = classify(replace(self.observation, failure_codes=codes))
                self.assertEqual('unchanged', result.state)
                self.assertEqual(codes, result.original_failure_codes)
                self.assertFalse(result.clear_existing_alert)

    def test_unverified_evidence_and_active_alert_never_pending(self):
        for change in ({'evidence_verified': False}, {'schedule_verified': False},
                       {'alert_active': True}, {'previous_backup_label': ''},
                       {'mirrored_label': 'older-generation'}):
            with self.subTest(change=change):
                self.assertEqual('alert', classify(replace(self.observation, **change)).state)

    def test_bad_order_future_and_stale_receipts(self):
        for change in ({'mirror_completed': self.observation.backup_completed},
                       {'backup_completed': self.observation.now + timedelta(seconds=1)},
                       {'mirror_completed': self.observation.now - timedelta(hours=48)}):
            with self.subTest(change=change):
                self.assertEqual('alert', classify(replace(self.observation, **change)).state)

    def test_success_and_recovery_are_owned_by_existing_evaluator(self):
        result = classify(replace(self.observation, failure_codes=()))
        self.assertEqual('unchanged', result.state)
        self.assertFalse(result.clear_existing_alert)

    def test_invalid_inputs_are_rejected(self):
        for change in ({'now': datetime(2026, 10, 7)}, {'evidence_verified': 1},
                       {'failure_codes': [MISMATCH]}, {'failure_codes': (None,)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                classify(replace(self.observation, **change))


if __name__ == '__main__':
    unittest.main()
