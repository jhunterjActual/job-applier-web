"""Synthetic intent controls and full-returned-precision acceptance guard."""
import copy
import json
from pathlib import Path
import unittest

from search_quality import compare, evaluate, replay
from search_role_intent import affirmative_role

ROOT = Path(__file__).resolve().parents[1]
FILTERS = ROOT / 'docs/search-quality/fixtures/candidate_filters_baseline.py'


class IntentTests(unittest.TestCase):
    def test_contextual_example_remains_a_known_false_positive(self):
        # Independent review counterexample: sentence grammar is not document intent.
        self.assertTrue(affirmative_role(
            'Historical role example:\nThis position is a data engineer role.',
            'data engineer'))

    def test_affirmative_statements_and_non_role_text(self):
        for phrase in ('Duties include data engineer responsibilities.',
                       'This position is a data engineer role.',
                       'You will serve as a data engineer.'):
            self.assertTrue(affirmative_role(phrase, 'data engineer'))
        for phrase in ('Work with a data engineer.', 'Previous data engineer experience.',
                       'If promoted, you will work as a data engineer.',
                       'This position is not a data engineer role.',
                       'This position is a data engineer role. You will not perform that work.',
                       'Customer says: "This position is a data engineer role."',
                       "This position is a data engineer role. It isn't your responsibility.",
                       'Duties include data engineering responsibilities.'):
            self.assertFalse(affirmative_role(phrase, 'data engineer'), phrase)

    def test_bounds_and_missing_values_abstain(self):
        for value in (None, '', 'Duties include data engineer work.' + ' ' * 50_000):
            self.assertFalse(affirmative_role(value, 'data engineer'))
        self.assertFalse(affirmative_role('This position is a data engineer role.', ''))
        self.assertFalse(affirmative_role('Fixed-term contract employment.', '1099'))

    def test_adversarial_fixed_thresholds_reject_naive_accept_conservative(self):
        reference = json.loads((ROOT / 'docs/search-quality/reference-adversarial-v1.json').read_text())
        old = evaluate(reference, replay(reference, FILTERS))
        naive = evaluate(reference, replay(reference, FILTERS, 'title_or_description'))
        observations = replay(reference, FILTERS, 'title_or_affirmative_description')
        candidate = evaluate(reference, observations)
        self.assertFalse(compare(old, naive, reference['thresholds'])['accepted'])
        self.assertTrue(compare(old, candidate, reference['thresholds'])['accepted'])
        for cell in observations['cells']:
            self.assertEqual(['title', 'duties', 'position'], cell['ranked_ids'])
            self.assertEqual(.75, candidate['cells'][cell['id']]['known_set_recall'])
            self.assertEqual(1, candidate['cells'][cell['id']]['precision_all_returned'])

    def test_precision_guard_catches_extra_false_positive_outside_top_ten(self):
        reference = {'version': 1, 'cells': [{'id': 'one', 'jobs': [
            dict(id=str(i), identity=str(i), relevant=i < 10, status='open', employer=str(i))
            for i in range(11)]}]}
        observations = {'reference_version': 1, 'kind': 'synthetic',
                        'cells': [{'id': 'one', 'ranked_ids': [str(i) for i in range(10)]}]}
        old = evaluate(reference, observations)
        observations['cells'][0]['ranked_ids'].append('10')
        new = evaluate(reference, observations)
        thresholds = dict(max_precision_at_10_drop=0, max_known_set_recall_drop=0,
                          max_precision_all_returned_drop=0, min_one_cell_recall_gain=.1)
        result = compare(old, new, thresholds)
        self.assertEqual(['one:precision_all_returned'], result['regressions'])
        self.assertEqual(old['cells']['one']['precision_at_returned'],
                         new['cells']['one']['precision_at_returned'])

    def test_unknown_full_precision_cannot_pass_guard(self):
        report = dict(reference_version=1, kind='synthetic', missing_cells=[],
                      cells={'one': dict(precision_at_10=0, known_set_recall=0,
                                         precision_all_returned=None)})
        with self.assertRaises(ValueError):
            compare(report, copy.deepcopy(report), dict(max_precision_at_10_drop=0,
                    max_known_set_recall_drop=0, max_precision_all_returned_drop=0,
                    min_one_cell_recall_gain=.1))

    def test_original_reference_gain_preserved(self):
        reference = json.loads((ROOT / 'docs/search-quality/reference-v1.json').read_text())
        old = evaluate(reference, replay(reference, FILTERS))
        candidate = evaluate(reference, replay(reference, FILTERS, 'title_or_affirmative_description'))
        self.assertTrue(compare(old, candidate, reference['thresholds'])['accepted'])


if __name__ == '__main__':
    unittest.main()
