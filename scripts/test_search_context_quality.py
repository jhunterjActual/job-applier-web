import json
from pathlib import Path
import unittest

from search_context_quality import compare_context, digest, load_matcher, measure


class ContextQualityTests(unittest.TestCase):
    def test_frozen_holdout_reproduces_rejected_candidate_and_restored_source(self):
        root = Path(__file__).resolve().parents[1]
        folder = root / 'docs/search-quality/fixtures'
        reference_path = root / 'docs/search-quality/context-holdout-v1.json'
        reference = json.loads(reference_path.read_text(encoding='utf-8'))
        receipt = json.loads((root / 'docs/search-quality/context-comparison-20261009.json')
                             .read_text(encoding='utf-8'))
        self.assertEqual(receipt['reference_sha256'], digest(reference_path))
        self.assertEqual(receipt['before_sha256'], digest(root / 'scripts/search_role_intent.py'))
        for name, path, hash_key in (
                ('before', root / 'scripts/search_role_intent.py', 'before_sha256'),
                ('after', folder / 'intent-context-candidate.py', 'candidate_sha256')):
            self.assertEqual(receipt[hash_key], digest(path))
            self.assertEqual(receipt[name], measure(reference, load_matcher(path)))
        self.assertFalse(compare_context(receipt['before'], receipt['after'])['accepted'])

    def test_reports_false_positives_and_false_negatives_by_id(self):
        reference = {'cases': [dict(id='positive', expected=True, role='engineer', description='duties'),
                               dict(id='negative', expected=False, role='engineer', description='history')]}
        result = measure(reference, lambda description, role: description == 'history')
        self.assertEqual(['negative'], result['false_positives'])
        self.assertEqual(['positive'], result['false_negatives'])
        self.assertEqual(0, result['precision'])
        self.assertEqual(0, result['recall'])

    def test_no_predictions_is_unknown_precision(self):
        result = measure({'cases': []}, lambda description, role: False)
        self.assertIsNone(result['precision'])
        self.assertIsNone(result['recall'])
        self.assertFalse(compare_context(result, result)['accepted'])

    def test_recall_loss_cannot_be_hidden_by_precision_gain(self):
        self.assertFalse(compare_context(dict(recall=1),
                         dict(recall=.9, false_positives=[]))['accepted'])
        self.assertTrue(compare_context(dict(recall=.8),
                        dict(recall=.9, false_positives=[]))['accepted'])
        self.assertFalse(compare_context(dict(recall=.8),
                         dict(recall=.9, false_positives=['one']))['accepted'])
        self.assertFalse(compare_context(dict(recall=.8),
                         dict(recall=.9, false_positives=[]))['runtime_approved'])

    def test_invalid_judgments_and_duplicate_ids_rejected(self):
        case = dict(id='one', expected=True, role='engineer', description=None)
        for cases in ([case, case], [{**case, 'expected': 1}], [{**case, 'description': 1}]):
            with self.assertRaises(ValueError):
                measure({'cases': cases}, lambda description, role: False)


if __name__ == '__main__':
    unittest.main()
