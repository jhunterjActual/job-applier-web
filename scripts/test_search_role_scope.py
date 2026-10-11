"""Offline SEARCH-003 scope comparison and explicit semantic limitations."""
import copy
import json
from pathlib import Path
import unittest

from search_quality import compare, digest, evaluate, replay

ROOT = Path(__file__).resolve().parents[1]
FILTERS = ROOT / 'docs/search-quality/fixtures/candidate_filters_baseline.py'
REFERENCE = ROOT / 'docs/search-quality/reference-v1.json'


class RoleScopeTests(unittest.TestCase):
    def setUp(self):
        self.reference = json.loads(REFERENCE.read_text(encoding='utf-8'))

    def test_default_preserves_exact_baseline_ranked_ids_and_source(self):
        baseline = json.loads((REFERENCE.parent / 'baseline-20261007.json').read_text(encoding='utf-8'))
        actual = replay(self.reference, FILTERS)
        self.assertEqual('title', actual['role_scope'])
        self.assertEqual(baseline['reference_sha256'], digest(REFERENCE))
        self.assertEqual(baseline['observations']['filter_sha256'], actual['filter_sha256'])
        self.assertEqual(baseline['observations']['cells'], actual['cells'])

    def test_opt_in_gains_fixed_reference_recall_without_geography_widening(self):
        old = evaluate(self.reference, replay(self.reference, FILTERS))
        observations = replay(self.reference, FILTERS, 'title_or_description')
        new = evaluate(self.reference, observations)
        self.assertTrue(compare(old, new, self.reference['thresholds'])['accepted'])
        for cell in observations['cells']:
            self.assertIn('duties', cell['ranked_ids'])
            for excluded in ('wrong-role', 'wrong-location', 'unknown-location'):
                self.assertNotIn(excluded, cell['ranked_ids'])
        self.assertEqual(4, sum(old['cells'][key]['known_set_recall'] < value['known_set_recall']
                                for key, value in new['cells'].items()))

    def test_unknown_scope_rejected(self):
        with self.assertRaises(ValueError):
            replay(self.reference, FILTERS, 'everything')

    def test_missing_description_and_description_bound(self):
        reference = copy.deepcopy(self.reference)
        row = reference['cells'][2]['jobs'][1]
        for description in (None, '', 'x' * 50_000 + ' data engineer'):
            row['description'] = description
            result = replay(reference, FILTERS, 'title_or_description')
            self.assertNotIn('duties', result['cells'][2]['ranked_ids'])

    def test_incidental_and_negated_mentions_are_known_rejection_evidence(self):
        # This candidate is a lexical experiment, not safe production eligibility.
        # Record its false positives rather than silently accepting the small baseline.
        for description in ('Work alongside a data engineer to sell subscriptions.',
                            'This is not a data engineer role.'):
            reference = copy.deepcopy(self.reference)
            reference['cells'][2]['jobs'][2]['description'] = description
            result = replay(reference, FILTERS, 'title_or_description')
            self.assertIn('wrong-role', result['cells'][2]['ranked_ids'])

    def test_contract_is_not_rewritten_as_1099(self):
        reference = copy.deepcopy(self.reference)
        cell = reference['cells'][0]
        cell['role'] = '1099'
        cell['jobs'] = [{**cell['jobs'][0], 'title': 'Contract Software Engineer',
                         'description': 'Fixed-term W-2 contract employment.'}]
        result = replay(reference, FILTERS, 'title_or_description')
        self.assertEqual([], result['cells'][0]['ranked_ids'])


if __name__ == '__main__':
    unittest.main()