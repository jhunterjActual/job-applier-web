import json
from pathlib import Path
import unittest

from search_role_sections import affirmative_role, role_evidence
from search_context_quality import digest, measure, compare_context


class SectionRoleTests(unittest.TestCase):
    def test_fresh_holdout_receipt_preserves_no_go(self):
        root = Path(__file__).resolve().parents[1]
        reference_path = root / 'docs/search-quality/section-final-holdout-v1.json'
        receipt = json.loads((root / 'docs/search-quality/sections-final-holdout-20261009.json')
                             .read_text(encoding='utf-8'))
        self.assertEqual(receipt['reference_sha256'], digest(reference_path))
        self.assertEqual(receipt['candidate_sha256'], digest(root / 'scripts/search_role_sections.py'))
        result = measure(json.loads(reference_path.read_text(encoding='utf-8')), affirmative_role)
        self.assertEqual(receipt['after'], result)
        self.assertFalse(compare_context(receipt['before'], result)['accepted'])

    def test_retained_context_and_section_regressions(self):
        root = Path(__file__).resolve().parents[1] / 'docs/search-quality'
        for name, expected_tp, expected_fn in (
                ('context-holdout-v1.json', 14, ['oversized-current-role']),
                ('section-holdout-v1.json', 16, [])):
            reference = json.loads((root / name).read_text(encoding='utf-8'))
            result = measure(reference, affirmative_role)
            self.assertEqual([], result['false_positives'])
            self.assertEqual(expected_fn, result['false_negatives'])
            self.assertEqual(expected_tp, result['true_positives'])

    def test_current_section_resets_history_but_history_cannot_supply_current_role(self):
        self.assertTrue(affirmative_role('Previous opening:\nSales manager.\nCurrent opening:\nThis position is a data engineer role.', 'data engineer'))
        self.assertFalse(affirmative_role('Previous opening:\nThis position is a data engineer role.\nCurrent opening: sales manager.', 'data engineer'))

    def test_local_benign_negation_does_not_cancel_role(self):
        self.assertTrue(affirmative_role('This position is a data engineer role. No degree is required.', 'data engineer'))
        self.assertTrue(affirmative_role('You will serve as a data engineer. If you need accommodation, contact us.', 'data engineer'))
        self.assertFalse(affirmative_role('This position is not a data engineer role.', 'data engineer'))

    def test_third_party_quotes_and_own_role_quotes(self):
        self.assertFalse(affirmative_role('Customer posting:\n"This position is a data engineer role."', 'data engineer'))
        self.assertTrue(affirmative_role('The manager describes this opening: "This position is a data engineer role."', 'data engineer'))

    def test_correction_and_conditional_role_abstain(self):
        self.assertFalse(affirmative_role('This position is a data engineer role. Correction: that describes an old vacancy.', 'data engineer'))
        self.assertFalse(affirmative_role('If promoted, you will work as a data engineer.', 'data engineer'))

    def test_limits_and_reason_codes_do_not_echo_input(self):
        result = role_evidence('private marker ' * 5000, 'data engineer')
        self.assertEqual({'matched': False, 'reason': 'input_limit'}, result)
        self.assertEqual('missing_evidence', role_evidence(None, 'data engineer')['reason'])
        self.assertFalse(affirmative_role('This is fixed-term contract work.', '1099'))


if __name__ == '__main__':
    unittest.main()
