import copy
import unittest

from search_quality import compare, evaluate


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.reference = {'version': 1, 'cells': [{'id': 'remote-data', 'jobs': [
            {'id': 'a', 'identity': 'a', 'employer': 'Alpha', 'status': 'open', 'relevant': True},
            {'id': 'a-copy', 'identity': 'a', 'employer': 'Alpha', 'status': 'open', 'relevant': True},
            {'id': 'b', 'identity': 'b', 'employer': 'Beta', 'status': 'open', 'relevant': True},
            {'id': 'closed', 'identity': 'c', 'employer': 'Gamma', 'status': 'closed', 'relevant': False},
        ]}]}
        self.observed = {'reference_version': 1, 'kind': 'synthetic', 'cells': [
            {'id': 'remote-data', 'ranked_ids': ['a', 'a-copy', 'closed'],
             'http_attempts': 6, 'detail_attempts': 3, 'detail_successes': 2}]}
        self.thresholds = {'max_precision_at_10_drop': 0, 'max_known_set_recall_drop': 0,
                           'min_one_cell_recall_gain': .1}

    def test_duplicates_do_not_inflate_precision_recall_or_employers(self):
        report = evaluate(self.reference, self.observed)['cells']['remote-data']
        self.assertEqual(.1, report['precision_at_10'])
        self.assertEqual(.5, report['known_set_recall'])
        self.assertEqual(1, report['unique_eligible_jobs'])
        self.assertEqual(1, report['unique_eligible_employers'])
        self.assertEqual(1 / 3, report['duplicate_rate'])
        self.assertEqual(1 / 3, report['stale_rate'])
        self.assertEqual(2 / 3, report['detail_success_rate'])
        self.assertEqual(6, report['requests_per_usable_result'])

    def test_empty_results_and_unknown_measurements_are_distinct(self):
        self.observed['cells'][0] = {'id': 'remote-data', 'ranked_ids': []}
        report = evaluate(self.reference, self.observed)['cells']['remote-data']
        self.assertEqual(0, report['precision_at_10'])
        self.assertEqual(0, report['known_set_recall'])
        self.assertIsNone(report['requests_per_usable_result'])
        self.assertIsNone(report['detail_success_rate'])
        self.assertIsNone(report['stale_rate'])

    def test_missing_cells_are_not_zero_result_cells(self):
        self.observed['cells'] = []
        self.assertEqual(['remote-data'], evaluate(self.reference, self.observed)['missing_cells'])

    def test_unknown_and_duplicate_cells_and_unjudged_jobs_fail(self):
        for mutation in ('cell', 'duplicate', 'job', 'version'):
            document = copy.deepcopy(self.observed)
            if mutation == 'cell':
                document['cells'][0]['id'] = 'unknown'
            elif mutation == 'duplicate':
                document['cells'] *= 2
            elif mutation == 'job':
                document['cells'][0]['ranked_ids'].append('unknown')
            else:
                document['reference_version'] = 2
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                evaluate(self.reference, document)

    def test_top_ten_and_recall_have_different_denominators(self):
        self.observed['cells'][0]['ranked_ids'] = ['closed'] * 10 + ['b']
        report = evaluate(self.reference, self.observed)['cells']['remote-data']
        self.assertEqual(0, report['precision_at_10'])
        self.assertEqual(.5, report['known_set_recall'])

    def test_no_positives_is_unknown_recall(self):
        for row in self.reference['cells'][0]['jobs']:
            row['relevant'] = False
        self.assertIsNone(evaluate(self.reference, self.observed)['cells']['remote-data']['known_set_recall'])

    def test_compare_requires_gain_and_no_regression(self):
        before = evaluate(self.reference, self.observed)
        self.assertFalse(compare(before, before, self.thresholds)['accepted'])
        self.observed['cells'][0]['ranked_ids'] = ['a', 'b']
        after = evaluate(self.reference, self.observed)
        self.assertTrue(compare(before, after, self.thresholds)['accepted'])
        self.assertFalse(compare(after, before, self.thresholds)['non_regression_pass'])

    def test_invalid_measurements_rejected(self):
        for field, value in [('http_attempts', -1), ('http_attempts', True), ('detail_successes', 4),
                             ('latency_ms', float('nan')), ('http_attempts', 1.5)]:
            document = copy.deepcopy(self.observed)
            document['cells'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                evaluate(self.reference, document)

    def test_conflicting_identity_judgments_rejected(self):
        self.reference['cells'][0]['jobs'][1]['relevant'] = False
        with self.assertRaises(ValueError):
            evaluate(self.reference, self.observed)

    def test_diagnostics_accept_only_known_numeric_counters(self):
        self.observed['cells'][0]['diagnostics'] = {'coverage_role_excluded': 3}
        result = evaluate(self.reference, self.observed)
        self.assertEqual({'coverage_role_excluded': 3}, result['cells']['remote-data']['diagnostics'])
        self.observed['cells'][0]['diagnostics']['query'] = 'private content'
        with self.assertRaises(ValueError):
            evaluate(self.reference, self.observed)


if __name__ == '__main__':
    unittest.main()
