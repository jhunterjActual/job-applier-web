import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from evaluate_ai_facts import evaluate, unique_fields


FIXTURE = Path(__file__).resolve().parents[1] / 'docs/ai-facts-synthetic-v1.json'


class FactEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_reference_and_adversarial_results(self):
        good, bad = evaluate(self.data)['candidates']
        self.assertTrue(good['all_cases_passed'])
        self.assertEqual(2, good['passed_cases'])
        self.assertEqual(0, bad['passed_cases'])
        self.assertEqual(1, bad['cases'][0]['unsupported_fact_count'])
        self.assertEqual(1, bad['cases'][1]['missing_required_fact_count'])

    def test_retry_metrics(self):
        good = evaluate(self.data)['candidates'][0]
        self.assertEqual(3, good['attempts'])
        self.assertEqual('0.006', good['estimated_cost_total_usd'])
        self.assertEqual('0.003', good['estimated_cost_per_passed_case_usd'])
        self.assertEqual(600, good['total_latency_ms'])

    def test_unknown_retry_metrics(self):
        self.data['candidates'][0]['runs'][0]['attempts'][0].update(
            estimated_cost_usd=None, latency_ms=None)
        good = evaluate(self.data)['candidates'][0]
        self.assertIsNone(good['estimated_cost_total_usd'])
        self.assertIsNone(good['estimated_cost_per_passed_case_usd'])
        self.assertIsNone(good['total_latency_ms'])
        self.assertEqual('0.005', good['known_cost_subtotal_usd'])

    def test_invalid_output(self):
        for output in (None, [], {'facts': ['SQL', 'SQL']}, {'facts': [], 'extra': True},
                       {'facts': [True]}, {'facts': 'SQL'}):
            with self.subTest(output=output):
                data = copy.deepcopy(self.data)
                data['candidates'][0]['runs'][0]['attempts'][-1]['output'] = output
                case = evaluate(data)['candidates'][0]['cases'][0]
                self.assertFalse(case['schema_valid'])
                self.assertFalse(case['passed'])

    def test_terminal_failure(self):
        self.data['candidates'][0]['runs'][0]['attempts'][-1]['outcome'] = 'cancelled'
        self.assertFalse(evaluate(self.data)['candidates'][0]['cases'][0]['passed'])

    def test_incomplete_or_duplicate_cases(self):
        for runs in ([], [self.data['candidates'][0]['runs'][0]] * 2):
            self.data['candidates'][0]['runs'] = runs
            with self.assertRaises(ValueError):
                evaluate(self.data)

    def test_invalid_reference(self):
        self.data['cases'][0]['required_facts'] = ['invented']
        with self.assertRaises(ValueError):
            evaluate(self.data)

    def test_invalid_cost_or_latency(self):
        for field, value in [('estimated_cost_usd', '-1'), ('estimated_cost_usd', 'NaN'),
                             ('latency_ms', True), ('latency_ms', -1)]:
            with self.subTest(field=field, value=value):
                data = copy.deepcopy(self.data)
                data['candidates'][0]['runs'][0]['attempts'][0][field] = value
                with self.assertRaises(ValueError):
                    evaluate(data)

    def test_provenance_and_identity(self):
        self.data['candidates'].append(copy.deepcopy(self.data['candidates'][0]))
        with self.assertRaises(ValueError):
            evaluate(self.data)
        self.data['candidates'].pop()
        self.data['provenance'] = 'live'
        with self.assertRaises(ValueError):
            evaluate(self.data)

    def test_no_routing_or_zero_success_cost_claim(self):
        report = evaluate(self.data)
        self.assertFalse(report['runtime_routing_approved'])
        self.assertIsNone(report['candidates'][1]['estimated_cost_per_passed_case_usd'])

    def test_duplicate_json_fields_refused(self):
        with self.assertRaises(ValueError):
            json.loads('{"facts": [], "facts": ["SQL"]}', object_pairs_hook=unique_fields)

    def test_cli_preserves_existing_output(self):
        before = FIXTURE.read_bytes()
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('evaluate_ai_facts.py')),
                                 '--input', str(FIXTURE), '--output', str(FIXTURE)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(2, result.returncode)
        self.assertIn('Benchmark refused', result.stderr)
        self.assertEqual(before, FIXTURE.read_bytes())


if __name__ == '__main__':
    unittest.main()
