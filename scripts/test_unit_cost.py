from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import unittest

from estimate_unit_cost import calculate


class CostTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads((Path(__file__).resolve().parents[1] / 'docs/monetization/cost-inputs-v1.json').read_text())

    def test_missing_costs_prevent_margin_claim(self):
        result = calculate(self.document)['results']['gpt-5-mini']['typical']
        self.assertIsNone(result['contribution_margin'])
        self.assertIsNone(result['total_cost_usd'])
        self.assertEqual(6, len(result['missing_cost_components']))

    def test_stress_doubles_variable_costs(self):
        base = calculate(self.document)['results']['gpt-5-mini']['heavy']
        stress = calculate(self.document, Decimal(2))['results']['gpt-5-mini']['heavy']
        self.assertEqual(2 * Decimal(base['ai_usd']), Decimal(stress['ai_usd']))
        self.assertTrue(stress['ai_exceeds_proposed_three_dollar_cap'])

    def test_complete_inputs_produce_margin_and_cost(self):
        document = deepcopy(self.document)
        document['missing_monthly_costs_usd'] = {'synthetic_all_other_costs': '2'}
        result = calculate(document)['results']['gpt-5-mini']['light']
        self.assertEqual([], result['missing_cost_components'])
        self.assertEqual((Decimal(19) - Decimal(result['total_cost_usd'])) / 19,
                         Decimal(result['contribution_margin']))

    def test_nonfinite_negative_and_invalid_margin_refused(self):
        for invalid in ('NaN', 'Infinity', '-1'):
            with self.assertRaises(ValueError):
                calculate(self.document, invalid)
        self.document['target_contribution_margin'] = '1'
        with self.assertRaises(ValueError):
            calculate(self.document)


if __name__ == '__main__':
    unittest.main()
