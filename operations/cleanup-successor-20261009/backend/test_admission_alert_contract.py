import json
import unittest

import admission_alert_contract as alert


class AlertContractTests(unittest.TestCase):
    source = 'a'*40
    now = 200000

    def status(self, **changes):
        value = dict(schema=alert.SCHEMA, source=self.source, checked_at=self.now, result='ok', issues=[])
        value.update(changes)
        return json.dumps(value).encode()

    def check(self, raw, code=0):
        return alert.classify(code, raw, self.source, self.now)

    def test_healthy_and_each_actual_status_issue(self):
        self.assertEqual((), self.check(self.status()))
        for issue in alert.ISSUES:
            self.assertEqual((issue,), self.check(self.status(result='alert', issues=[issue]), 1))

    def test_unreachable_and_invalid_exit_codes(self):
        for code in (None, 2, 255, -1, True, '0'):
            self.assertEqual(alert.UNAVAILABLE, self.check(self.status(), code))

    def test_malformed_oversized_and_duplicate_json(self):
        for raw in (b'', b'\xff', b'null', b'[]', b'x'*4097,
            self.status()+b'\nextra', self.status().replace(b'"result": "ok"', b'"result":"alert","result":"ok"')):
            self.assertEqual(alert.UNAVAILABLE, self.check(raw))

    def test_wrong_revision_schema_unknown_fields(self):
        for changes in ({'source':'b'*40}, {'schema':'unknown'}, {'extra':'sensitive'}):
            self.assertEqual(alert.UNAVAILABLE, self.check(self.status(**changes)))

    def test_freshness_boundary_future_and_wrong_types(self):
        self.assertEqual((), self.check(self.status(checked_at=self.now-420)))
        for checked in (self.now-421, self.now+1, True, str(self.now), -1):
            self.assertEqual(alert.UNAVAILABLE, self.check(self.status(checked_at=checked)))

    def test_mismatched_result_exit_and_issues(self):
        for changes, code in (({'result':'alert'},0), ({},1),
            ({'issues':['last_run_failed']},0), ({'result':'ok','issues':['last_run_failed']},1),
            ({'result':'alert','issues':['unknown']},1),
            ({'result':'alert','issues':['last_run_failed']*2},1)):
            self.assertEqual(alert.UNAVAILABLE, self.check(self.status(**changes), code))

    def test_raw_output_never_enters_message(self):
        raw = b'private-token-and-profile-data'
        classification = self.check(raw)
        self.assertEqual(alert.UNAVAILABLE, classification)
        self.assertNotIn(raw.decode(), alert.message(classification))

    def test_recovery_requires_healthy_and_idle_is_quiet(self):
        self.assertIsNone(alert.message(()))
        self.assertIn('recovered', alert.message((), recovery=True))
        for value in (alert.UNAVAILABLE, ('last_run_failed',)):
            with self.assertRaises(ValueError): alert.message(value, recovery=True)
        with self.assertRaises(ValueError): alert.message(('untrusted text',))


if __name__ == '__main__':
    unittest.main()
