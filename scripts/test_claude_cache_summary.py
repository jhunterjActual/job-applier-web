"""Compatibility between legacy usage receipts and explicit cache-write TTL evidence."""
from copy import deepcopy
from decimal import Decimal
import unittest

from test_ai_usage_summary import document, receipt
from ai_contracts import Usage
from summarize_ai_usage import summarize, USAGE_FIELDS, CACHE_TTL_FIELDS


class CacheSummaryTests(unittest.TestCase):
    def cached(self):
        return receipt(provider='anthropic', model='claude-sonnet-5-5',
            usage=Usage(160, 30, 20, None, 190, 40, 40, 0),
            estimate=Decimal('.000602'), rate_version='synthetic-cache-v1')

    def test_old_six_field_export_and_reader_remain_compatible(self):
        old = document([receipt()])
        self.assertEqual(USAGE_FIELDS, set(old['receipts'][0]['usage']))
        self.assertEqual('0.01', summarize(old)['estimated_ai_cost_total_usd'])

    def test_ttl_fields_survive_export_and_known_total(self):
        current = document([self.cached()])
        self.assertEqual(USAGE_FIELDS | CACHE_TTL_FIELDS, set(current['receipts'][0]['usage']))
        self.assertEqual(40, current['receipts'][0]['usage']['cache_creation_5m_input_tokens'])
        self.assertEqual(0, current['receipts'][0]['usage']['cache_creation_1h_input_tokens'])
        self.assertEqual('0.000602', summarize(current)['estimated_ai_cost_total_usd'])

    def test_aggregate_only_write_usage_is_not_a_known_price(self):
        old = self.cached()
        from dataclasses import replace
        old = replace(old, usage=Usage(160, 30, 20, None, 190, 40), estimated_cost_usd=None)
        report = summarize(document([old]))
        self.assertIsNone(report['estimated_ai_cost_total_usd'])
        self.assertEqual(1, report['unknown_cost_attempts'])
        with self.assertRaises(ValueError):
            document([replace(old, estimated_cost_usd=Decimal('.000602'))])

    def test_conflicting_partial_or_other_provider_ttl_fields_refuse(self):
        source = document([self.cached()])
        mutations = [lambda r: r['usage'].__setitem__('cache_creation_5m_input_tokens', 41),
                     lambda r: r['usage'].__setitem__('cache_creation_1h_input_tokens', None),
                     lambda r: r['usage'].__setitem__('cache_creation_5m_input_tokens', True),
                     lambda r: r.__setitem__('provider', 'openai'),
                     lambda r: r['usage'].__delitem__('cache_creation_1h_input_tokens')]
        for mutate in mutations:
            changed = deepcopy(source)
            mutate(changed['receipts'][0])
            with self.assertRaises(ValueError):
                summarize(changed)

    def test_known_claude_price_cannot_omit_cache_aggregates(self):
        for field in ('cached_input_tokens', 'cache_creation_input_tokens'):
            changed = document([self.cached()])
            for name in CACHE_TTL_FIELDS:
                changed['receipts'][0]['usage'].pop(name)
            changed['receipts'][0]['usage'][field] = None
            with self.assertRaises(ValueError):
                summarize(changed)


if __name__ == '__main__':
    unittest.main()
