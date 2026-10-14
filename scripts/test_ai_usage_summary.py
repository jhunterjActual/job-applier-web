from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from summarize_ai_usage import receipt_document, summarize, VERSION, MAX_RECEIPTS

BACKEND = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(BACKEND))
from ai_contracts import Receipt, Usage


def receipt(outcome='success', dispatched=True, estimate=Decimal('0.01'), **overrides):
    values = dict(provider='openai', model='synthetic-model', operation='scoring',
                  outcome=outcome, dispatched=dispatched,
                  usage=Usage(100, 20, 0, 0, 120), estimated_cost_usd=estimate,
                  rate_version='synthetic-v1')
    values.update(overrides)
    return Receipt(**values)


def document(records):
    return receipt_document(records, provenance='synthetic', capture_complete=True)


class ReceiptSummaryTests(unittest.TestCase):
    def test_real_contract_shape_export_and_failed_attempt_costs(self):
        report = summarize(document([receipt('rate_limit'), receipt(), receipt('cancelled')]))
        self.assertEqual('0.03', report['estimated_ai_cost_total_usd'])
        self.assertEqual(3, report['groups'][0]['dispatched_attempts'])
        self.assertEqual(1, report['groups'][0]['successful_attempts'])
        self.assertEqual(300, report['groups'][0]['known_input_token_subtotal'])
        self.assertIsNone(report['completed_workflows'])
        self.assertIsNone(report['monthly_cost_usd'])
        self.assertIsNone(report['contribution_margin'])

    def test_unknown_cost_does_not_become_zero(self):
        report = summarize(document([receipt(), receipt('timeout', estimate=None, usage=Usage())]))
        self.assertEqual('0.01', report['known_estimated_cost_subtotal_usd'])
        self.assertIsNone(report['estimated_ai_cost_total_usd'])
        self.assertIsNone(report['groups'][0]['estimated_cost_total_usd'])
        self.assertEqual(1, report['unknown_cost_attempts'])
        self.assertEqual(1, report['groups'][0]['unknown_usage_attempts'])

    def test_known_usage_without_rate_remains_unknown_cost(self):
        report = summarize(document([receipt(estimate=None, rate_version=None)]))
        self.assertEqual(1, report['unknown_cost_attempts'])
        self.assertEqual(0, report['groups'][0]['unknown_usage_attempts'])
        self.assertIsNone(report['estimated_ai_cost_total_usd'])

    def test_claude_transport_failures_preserve_unknown_dispatched_cost(self):
        for outcome in ('redirect_rejected', 'overloaded'):
            report = summarize(document([receipt(outcome, provider='anthropic',
                estimate=None, usage=Usage(), rate_version=None)]))
            self.assertEqual({outcome: 1}, report['groups'][0]['outcomes'])
            self.assertEqual(1, report['groups'][0]['dispatched_attempts'])
            self.assertEqual(1, report['unknown_cost_attempts'])
            self.assertIsNone(report['estimated_ai_cost_total_usd'])

    def test_cancelled_before_dispatch_does_not_claim_spend(self):
        report = summarize(document([receipt('cancelled', False, None, usage=Usage(), rate_version=None)]))
        self.assertEqual('0', report['estimated_ai_cost_total_usd'])
        self.assertEqual(0, report['unknown_cost_attempts'])
        self.assertEqual(0, report['groups'][0]['dispatched_attempts'])

    def test_in_progress_placeholder_and_incomplete_capture_block_totals(self):
        report = summarize(document([receipt('in_progress', False, None, usage=Usage(), rate_version=None)]))
        self.assertFalse(report['capture_complete'])
        self.assertEqual(1, report['in_progress_attempts'])
        self.assertIsNone(report['estimated_ai_cost_total_usd'])
        incomplete = document([receipt()])
        incomplete['capture_complete'] = False
        self.assertIsNone(summarize(incomplete)['estimated_ai_cost_total_usd'])

    def test_provider_model_operation_groups_and_rates_stay_distinct(self):
        records = [receipt(), receipt(provider='gemini'), receipt(operation='tailoring'),
                   receipt(model='synthetic-other'), receipt(rate_version='synthetic-v2')]
        report = summarize(document(records))
        self.assertEqual(4, len(report['groups']))
        group = next(g for g in report['groups'] if g['provider'] == 'openai'
                     and g['model'] == 'synthetic-model' and g['operation'] == 'scoring')
        self.assertEqual(['synthetic-v1', 'synthetic-v2'], group['rate_versions'])
        self.assertEqual('0.05', report['estimated_ai_cost_total_usd'])

    def test_empty_capture_is_not_an_invented_monthly_workload(self):
        report = summarize(document([]))
        self.assertEqual(0, report['attempts'])
        self.assertEqual('0', report['estimated_ai_cost_total_usd'])
        self.assertIsNone(report['monthly_cost_usd'])

    def test_unknown_fields_and_free_text_are_rejected(self):
        base = document([receipt()])
        for field, value in [('prompt', 'private'), ('model', 'private model text'),
                             ('operation', 'private search'), ('outcome', 'raw provider error'),
                             ('rate_version', 'private rate text')]:
            changed = deepcopy(base)
            changed['receipts'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                summarize(changed)
        base['user_id'] = 'private'
        with self.assertRaises(ValueError):
            summarize(base)

    def test_invalid_numbers_and_inconsistent_usage_are_rejected(self):
        base = document([receipt()])
        for invalid in ('NaN', 'Infinity', '-1', '1e100', '0.0000000000001', 0.01, True):
            changed = deepcopy(base)
            changed['receipts'][0]['estimated_cost_usd'] = invalid
            with self.subTest(cost=invalid), self.assertRaises(ValueError):
                summarize(changed)
        for field, invalid in [('input_tokens', True), ('output_tokens', -1),
                               ('total_tokens', 1), ('cached_input_tokens', 101),
                               ('reasoning_tokens', 21), ('input_tokens', 1_000_000_001)]:
            changed = deepcopy(base)
            changed['receipts'][0]['usage'][field] = invalid
            with self.subTest(field=field), self.assertRaises(ValueError):
                summarize(changed)

    def test_cost_requires_rate_and_usage_and_dispatch(self):
        for changes in ({'rate_version': None}, {'usage': Usage()}, {'dispatched': False},
                        {'outcome': 'cancelled', 'dispatched': False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                document([receipt(**changes)])

    def test_collection_limit_and_boolean_completeness_are_enforced(self):
        with self.assertRaises(ValueError):
            document([receipt()] * (MAX_RECEIPTS + 1))
        valid = document([receipt()])
        valid['capture_complete'] = 1
        with self.assertRaises(ValueError):
            summarize(valid)

    def test_cli_refuses_overwrite_duplicate_fields_and_oversized_input(self):
        script = Path(__file__).with_name('summarize_ai_usage.py')
        with tempfile.TemporaryDirectory(prefix='ct-usage-summary-') as directory:
            source, output = Path(directory) / 'input.json', Path(directory) / 'output.json'
            command = [sys.executable, '-B', str(script), '--input', str(source), '--output', str(output)]
            source.write_text(json.dumps(document([receipt()])), encoding='utf-8')
            first = subprocess.run(command, capture_output=True, timeout=10)
            self.assertEqual(0, first.returncode)
            original = output.read_bytes()
            second = subprocess.run(command, capture_output=True, timeout=10)
            self.assertEqual(1, second.returncode)
            self.assertEqual(original, output.read_bytes())
            for payload in ('{"version":"private","version":"duplicate"}', ' ' * (1024 * 1024 + 1)):
                source.write_text(payload, encoding='utf-8')
                result = subprocess.run(command, capture_output=True, timeout=10)
                self.assertEqual(1, result.returncode)
                self.assertNotIn(b'private', result.stderr)
                self.assertEqual(original, output.read_bytes())


if __name__ == '__main__':
    unittest.main()
