"""Offline opt-in prompt/cache-price checks; synthetic evidence and fake HTTP only."""
from dataclasses import replace
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

import ai_contracts as contracts
import ai_providers
import claude_adapter as claude
from claude_cache import CachedPrompt, cache_miss_reservation_usd
import test_claude_adapter as baseline
from test_claude_adapter import Answer, reply


def rates():
    # Synthetic reviewed-test rates, not an account balance or live rate card.
    return contracts.RateCard('anthropic', claude.MODEL, 'synthetic-cache-v1',
        Decimal(2), Decimal(10), Decimal('.1'), Decimal('2.5'), Decimal(4))


def usage(base=100, read=20, five=40, hour=0, output=30):
    return {'input_tokens': base, 'cache_read_input_tokens': read,
            'cache_creation_input_tokens': five + hour, 'output_tokens': output,
            'cache_creation': {'ephemeral_5m_input_tokens': five, 'ephemeral_1h_input_tokens': hour}}


class CacheRequestTests(unittest.TestCase):
    def setUp(self):
        self.claude = baseline.ClaudeTests()
        self.claude.setUp()
        for target in ('socket.socket', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('Offline tests cannot open a socket'))
            guard.start()
            self.addCleanup(guard.stop)

    def test_legacy_request_remains_exact_string_and_closed_envelope(self):
        with self.claude.transport() as transport:
            claude.generate_structured(self.claude.settings, 'synthetic legacy', Answer)
        payload = json.loads(transport.return_value.open.call_args.args[0].data)
        self.assertEqual({'model', 'max_tokens', 'thinking', 'messages', 'output_config'}, set(payload))
        self.assertEqual([{'role': 'user', 'content': 'synthetic legacy'}], payload['messages'])

    def test_explicit_blocks_user_role_stable_prefix_and_single_dispatch(self):
        prompt = CachedPrompt('PRIVATE-CANDIDATE stable evidence', 'Synthetic job A')
        document = reply()
        document['usage'] = usage()
        with self.claude.transport(document) as transport, contracts.capture_receipts() as receipts:
            result = ai_providers.generate_structured(self.claude.settings, prompt, Answer, rate_card=rates())
        payload = json.loads(transport.return_value.open.call_args.args[0].data)
        self.assertEqual({'score': 75}, result)
        self.assertEqual(1, transport.return_value.open.call_count)
        self.assertEqual('user', payload['messages'][0]['role'])
        self.assertEqual(prompt.content(), payload['messages'][0]['content'])
        self.assertNotIn('system', payload)
        self.assertNotIn('tools', payload)
        self.assertEqual({'type': 'between_tools'}, payload['thinking'])
        self.assertEqual(32768, payload['max_tokens'])
        self.assertEqual(Decimal('.000602'), receipts[0].estimated_cost_usd)
        self.assertNotIn('PRIVATE', repr(prompt))
        self.assertNotIn('PRIVATE', repr(receipts))
        other = CachedPrompt(prompt.stable_prefix, 'Synthetic job B')
        self.assertEqual(prompt.content()[0], other.content()[0])

    def test_no_padding_and_policy_or_provider_refusal(self):
        prompt = CachedPrompt('short', 'job')
        self.assertEqual('short', prompt.content()[0]['text'])
        for args in (('', 'job'), ('prefix', ''), ('prefix', 'job', '1h'),
                     ('prefix', 'job', None), (b'prefix', 'job')):
            with self.subTest(args=args), self.assertRaises(ValueError):
                CachedPrompt(*args)
        with self.claude.transport() as transport:
            with self.assertRaises(ai_providers.AIProviderError):
                ai_providers.generate_structured(ai_providers.AIProviderSettings('openai', 'gpt-5-mini', 'synthetic'), prompt, Answer)
            transport.assert_not_called()

    def test_combined_input_limit_and_cancellation_apply_before_dispatch(self):
        limit = contracts.OPERATIONS['structured'].max_input_bytes
        prompt = CachedPrompt('x' * limit, 'y')
        with self.claude.transport() as transport, self.assertRaises(ai_providers.AIProviderError):
            claude.generate_structured(self.claude.settings, prompt, Answer)
        transport.assert_not_called()
        from test_ai_contracts import Cancelled
        with self.claude.transport() as transport, contracts.capture_receipts() as receipts:
            with self.assertRaises(Cancelled):
                claude.generate_structured(self.claude.settings, CachedPrompt('a', 'b'), Answer,
                    cancel_check=lambda: (_ for _ in ()).throw(Cancelled()))
        transport.assert_not_called()
        self.assertFalse(receipts[0].dispatched)

    def test_tailoring_opt_in_preserves_facts_and_moves_only_dynamic_fields(self):
        import tailor
        supplied = dict(base_resume_text='SYNTHETIC-RESUME has 4 projects.',
            job_title='SYNTHETIC-TITLE-A', company_name='SYNTHETIC-COMPANY-A', job_description='SYNTHETIC-JOB-A',
            api_key='synthetic-key', ai_provider='anthropic', professional_evidence='SYNTHETIC-EVIDENCE no certification documented.')
        with patch.object(tailor, 'generate_structured', return_value={}) as generate:
            tailor.tailor_resume_and_cover_letter(**supplied)
            legacy = generate.call_args.args[1]
            self.assertIsInstance(legacy, str)
            tailor.tailor_resume_and_cover_letter(**supplied, claude_prompt_cache=True)
            first = generate.call_args.args[1]
            tailor.tailor_resume_and_cover_letter(**dict(supplied, job_title='SYNTHETIC-TITLE-B',
                company_name='SYNTHETIC-COMPANY-B', job_description='SYNTHETIC-JOB-B'), claude_prompt_cache=True)
            second = generate.call_args.args[1]
        self.assertIsInstance(first, CachedPrompt)
        self.assertIn(tailor.FACTUAL_EVIDENCE_CONTRACT, legacy)
        self.assertIn(tailor.FACTUAL_EVIDENCE_CONTRACT, first.stable_prefix)
        formatting = legacy.split('Resume formatting contract:', 1)[1].split('Job Details:', 1)[0].strip()
        self.assertIn(formatting, first.stable_prefix)
        self.assertIn('(under 350 words, 3-4 paragraphs)', first.stable_prefix)
        self.assertEqual(first.stable_prefix, second.stable_prefix)
        self.assertNotEqual(first.suffix, second.suffix)
        for value in (supplied['base_resume_text'], supplied['professional_evidence']):
            self.assertIn(value, first.stable_prefix)
            self.assertNotIn(value, first.suffix)
        for value in (supplied['job_title'], supplied['company_name'], supplied['job_description']):
            self.assertNotIn(value, first.stable_prefix)
            self.assertIn(value, first.suffix)
        self.assertIn(tailor.format_cover_letter_date(tailor.date.today()), first.suffix)
        for clause in ('DO NOT invent jobs, dates, degrees, credentials, technologies, metrics, or companies.',
                       'Use only standard Markdown headings', 'Never emit [Date]',
                       'never infer or invent missing dates, issuers, credential status, project outcomes, or metrics.'):
            self.assertIn(clause, legacy)
            self.assertIn(clause, first.stable_prefix)
        self.assertNotIn('claude_prompt_cache', generate.call_args.kwargs)
        with patch.object(tailor, '_provider_settings') as settings:
            for option in (True, 'true', 1):
                with self.assertRaises(ValueError):
                    tailor.tailor_resume_and_cover_letter('synthetic', 'job', 'company', 'description',
                        ai_provider='gemini', claude_prompt_cache=option)
            settings.assert_not_called()


class CacheAccountingTests(unittest.TestCase):
    def test_ttl_writes_reads_and_regular_input_count_once(self):
        normalized = contracts.normalize_usage('anthropic', usage(five=40, hour=10))
        self.assertEqual(170, normalized.input_tokens)
        self.assertEqual(200, normalized.total_tokens)
        self.assertEqual(Decimal('.000642'), contracts.estimated_cost(normalized, rates()))
        self.assertEqual(Decimal('.000602'), contracts.estimated_cost(
            contracts.normalize_usage('anthropic', usage()), rates()))
        self.assertEqual(Decimal('.000502'), contracts.estimated_cost(
            contracts.normalize_usage('anthropic', usage(five=0)), rates()))

    def test_unsplit_writes_and_missing_rate_remain_unknown(self):
        value = usage()
        value.pop('cache_creation')
        normalized = contracts.normalize_usage('anthropic', value)
        self.assertTrue(normalized.known)
        self.assertIsNone(contracts.estimated_cost(normalized, rates()))
        normalized = contracts.normalize_usage('anthropic', usage())
        self.assertIsNone(contracts.estimated_cost(normalized, replace(rates(), cache_creation_5m_per_million_usd=None)))
        mixed = contracts.normalize_usage('anthropic', usage(hour=10))
        self.assertIsNone(contracts.estimated_cost(mixed, replace(rates(), cache_creation_1h_per_million_usd=None)))

    def test_missing_claude_aggregates_are_unknown_without_breaking_other_providers(self):
        self.assertIsNone(contracts.estimated_cost(contracts.Usage(100, 30, 20, None, 130), rates()))
        self.assertIsNone(contracts.estimated_cost(contracts.Usage(100, 30, None, None, 130, 0), rates()))
        other = contracts.RateCard('openai', 'synthetic', 'synthetic-v1', Decimal(2), Decimal(10), Decimal('.1'))
        self.assertEqual(Decimal('.000462'), contracts.estimated_cost(contracts.Usage(100, 30, 20, None, 130), other))

    def test_bad_ttl_evidence_and_rates_fail_closed(self):
        for split in (None, {}, {'ephemeral_5m_input_tokens': 40},
                {'ephemeral_5m_input_tokens': True, 'ephemeral_1h_input_tokens': 39},
                {'ephemeral_5m_input_tokens': 41, 'ephemeral_1h_input_tokens': 0},
                {'ephemeral_5m_input_tokens': 40, 'ephemeral_1h_input_tokens': 0, 'other': 0}):
            self.assertFalse(contracts.normalize_usage('anthropic', dict(usage(), cache_creation=split)).known)
        for rate in (Decimal('NaN'), Decimal('Infinity'), Decimal('-1'), 2.5, True):
            with self.assertRaises(ValueError):
                replace(rates(), cache_creation_5m_per_million_usd=rate)
        self.assertIsNone(contracts.estimated_cost(contracts.Usage(100, 30, 0, None, 130, False), rates()))

    def test_reservation_prices_full_miss_without_assuming_hits(self):
        self.assertEqual(Decimal('.0127'), cache_miss_reservation_usd(5000, 20, rates()))
        for incoming, outgoing in ((True, 1), (-1, 1), (1, None), (1, 1_000_000_001)):
            with self.assertRaises(ValueError):
                cache_miss_reservation_usd(incoming, outgoing, rates())
        with self.assertRaises(ValueError):
            cache_miss_reservation_usd(1, 1, replace(rates(), cache_creation_5m_per_million_usd=None))
        for invalid in (None, {}, True):
            with self.assertRaises(ValueError):
                cache_miss_reservation_usd(1, 1, invalid)
        self.assertEqual(Decimal('.0352'), cache_miss_reservation_usd(5000, 20,
            replace(rates(), cached_input_per_million_usd=Decimal(7))))


if __name__ == '__main__':
    unittest.main()
