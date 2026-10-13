"""Identical offline contract cases for Gemini and OpenAI; no provider traffic."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from decimal import Decimal
import json
from types import SimpleNamespace
from threading import Event
import unittest
from unittest.mock import Mock, patch

from pydantic import BaseModel
import ai_contracts as contract
import ai_providers as providers


class Answer(BaseModel):
    ready: bool


class Cancelled(Exception):
    pass


class HTTPResponse:
    def __init__(self, document):
        self.document = document

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return json.dumps(self.document).encode()[:limit]


def response(provider, text='{"ready":true}', usage=True, outcome='success'):
    if provider == 'openai':
        result = {'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': text}]}]}
        if usage:
            result['usage'] = {'input_tokens': 100, 'output_tokens': 30, 'total_tokens': 130,
                               'input_tokens_details': {'cached_tokens': 20},
                               'output_tokens_details': {'reasoning_tokens': 10}}
        if outcome == 'truncated':
            result['status'] = 'incomplete'
        elif outcome == 'refusal':
            result['output'][0]['content'].append({'type': 'refusal', 'refusal': 'PRIVATE'})
        return HTTPResponse(result)
    return SimpleNamespace(parsed=None, text=text,
        prompt_feedback=None,
        candidates=[SimpleNamespace(finish_reason={'truncated': 'MAX_TOKENS', 'refusal': 'SAFETY'}.get(outcome, 'STOP'))],
        usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=20,
            thoughts_token_count=10, total_token_count=130, cached_content_token_count=20) if usage else None)


class ContractTests(unittest.TestCase):
    def settings(self, provider):
        return providers.AIProviderSettings(provider, providers.default_model(provider), 'PRIVATE-KEY')

    def transport(self, provider, reply=None, error=None):
        if provider == 'openai':
            return patch.object(providers.urllib.request, 'urlopen', return_value=reply, side_effect=error)
        client = Mock()
        client.models.generate_content.return_value = reply
        client.models.generate_content.side_effect = error
        return patch.object(providers.genai, 'Client', return_value=client)

    def test_same_structured_usage_cost_and_limits_contract(self):
        for provider in ('gemini', 'openai'):
            with self.subTest(provider=provider), self.transport(provider, response(provider)) as transport:
                settings = self.settings(provider)
                rates = contract.RateCard(provider, settings.model, 'synthetic-v1', Decimal('1'), Decimal('2'), Decimal('.5'))
                with contract.capture_receipts() as receipts:
                    self.assertEqual({'ready': True}, providers.generate_structured(settings, 'PRIVATE-PROMPT', Answer,
                                                                                   rate_card=rates))
                receipt = receipts[0]
                self.assertEqual(contract.Usage(100, 30, 20, 10, 130), receipt.usage)
                self.assertEqual(Decimal('.00015'), receipt.estimated_cost_usd)
                self.assertTrue(receipt.dispatched)
                self.assertEqual('success', receipt.outcome)
                self.assertNotIn('PRIVATE', repr(receipt))
                self.assertNotIn('PRIVATE', repr(settings))
                if provider == 'openai':
                    payload = json.loads(transport.call_args.args[0].data)
                    self.assertEqual(32768, payload['max_output_tokens'])
                else:
                    configuration = transport.return_value.models.generate_content.call_args.kwargs['config']
                    self.assertEqual(32768, configuration.max_output_tokens)

    def test_missing_usage_never_becomes_zero(self):
        for provider in ('gemini', 'openai'):
            with self.subTest(provider=provider), self.transport(provider, response(provider, usage=False)):
                with contract.capture_receipts() as receipts:
                    providers.generate_structured(self.settings(provider), 'test', Answer)
                self.assertFalse(receipts[0].usage.known)
                self.assertIsNone(receipts[0].estimated_cost_usd)

    def test_malformed_refused_truncated_responses_keep_usage(self):
        for provider in ('gemini', 'openai'):
            for outcome, text, expected in [('success', 'not json', 'invalid_response'),
                                             ('success', '', 'empty_response'),
                                             ('refusal', '{"ready":true}', 'refusal'),
                                             ('truncated', '{"ready":true}', 'truncated')]:
                with self.subTest(provider=provider, expected=expected), self.transport(provider, response(provider, text, outcome=outcome)):
                    with contract.capture_receipts() as receipts, self.assertRaises(providers.AIProviderError) as caught:
                        providers.generate_structured(self.settings(provider), 'test', Answer)
                    self.assertEqual(expected, caught.exception.code)
                    self.assertEqual(expected, receipts[0].outcome)
                    self.assertTrue(receipts[0].usage.known)

    def test_transport_failure_unknown_usage_and_no_retry(self):
        for provider in ('gemini', 'openai'):
            error = TimeoutError('PRIVATE')
            with self.subTest(provider=provider), self.transport(provider, error=error) as transport:
                with contract.capture_receipts() as receipts, self.assertRaises(providers.AIProviderError):
                    providers.generate_structured(self.settings(provider), 'test', Answer)
                self.assertTrue(receipts[0].dispatched)
                self.assertFalse(receipts[0].usage.known)
                self.assertNotIn('PRIVATE', repr(receipts))
                self.assertEqual(1, transport.call_count)

    def test_cancellation_before_and_after_dispatch(self):
        for provider in ('gemini', 'openai'):
            for after in (False, True):
                checkpoint = Mock(side_effect=[None, Cancelled()] if after else Cancelled())
                with self.subTest(provider=provider, after=after), self.transport(provider, response(provider)) as transport:
                    with contract.capture_receipts() as receipts, self.assertRaises(Cancelled):
                        providers.generate_structured(self.settings(provider), 'test', Answer, cancel_check=checkpoint)
                    self.assertEqual(after, receipts[0].dispatched)
                    self.assertEqual(after, receipts[0].usage.known)
                    self.assertEqual('cancelled', receipts[0].outcome)
                    self.assertEqual(int(after), transport.call_count)

    def test_ocr_per_page_receipts_and_all_inputs_prevalidated(self):
        for provider in ('gemini', 'openai'):
            with self.subTest(provider=provider), self.transport(provider, response(provider, 'Synthetic resume')) as transport:
                with contract.capture_receipts() as receipts:
                    result = providers.extract_text_from_images(self.settings(provider), [b'image', b'image'])
                self.assertEqual(['Synthetic resume'] * 2, result)
                self.assertEqual(['resume_ocr'] * 2, [receipt.operation for receipt in receipts])
                transport.reset_mock()
                with self.assertRaises(providers.AIProviderError):
                    providers.extract_text_from_images(self.settings(provider), [b'image', b''])
                transport.assert_not_called()

    def test_unknown_provider_cannot_read_or_dispatch_another_key(self):
        with patch.object(providers, 'current_provider_secret') as secrets, patch.object(providers.genai, 'Client') as client:
            for action in (lambda: providers.settings_from_profile({'ai_provider': 'unknown'}),
                           lambda: providers.generate_structured(providers.AIProviderSettings('unknown', 'model', 'key'), 'test', Answer),
                           lambda: providers.extract_text_from_images(providers.AIProviderSettings('unknown', 'model', 'key'), [b'image'])):
                with self.assertRaises(providers.AIProviderError) as caught:
                    action()
                self.assertEqual('unsupported_provider', caught.exception.code)
            secrets.assert_not_called()
            client.assert_not_called()

    def test_invalid_operation_limits_and_rate_fail_before_dispatch(self):
        for provider in ('gemini', 'openai'):
            with self.subTest(provider=provider), self.transport(provider) as transport:
                for kwargs in ({'operation': 'resume_ocr'}, {'operation': 'unsupported'},
                               {'timeout_seconds': 91}, {'timeout_seconds': True}):
                    with self.assertRaises(providers.AIProviderError):
                        providers.generate_structured(self.settings(provider), 'test', Answer, **kwargs)
                with self.assertRaises(providers.AIProviderError):
                    providers.generate_structured(self.settings(provider), 'x' * (512 * 1024 + 1), Answer)
                with self.assertRaises(providers.AIProviderError):
                    providers.extract_text_from_images(self.settings(provider), [b'x'] * 13)
                rates = contract.RateCard('other', 'wrong', 'v1', Decimal(1), Decimal(1), Decimal(1))
                with self.assertRaises(providers.AIProviderError):
                    providers.generate_structured(self.settings(provider), 'test', Answer, rate_card=rates)
                transport.assert_not_called()

    def test_receipt_scopes_are_nested_and_thread_isolated(self):
        def run():
            with contract.capture_receipts() as records:
                providers._request(self.settings('openai'), 'structured', lambda: None)
                return records
        with contract.capture_receipts() as outer:
            inner = run()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: run(), range(2)))
            self.assertEqual([], outer)
        self.assertEqual([1, 1, 1], [len(inner), len(results[0]), len(results[1])])

    def test_receipt_limit_refuses_before_dispatch(self):
        invoke = Mock()
        with contract.capture_receipts() as records, patch.object(contract, 'MAX_RECEIPTS', 1):
            providers._request(self.settings('openai'), 'structured', invoke)
            with self.assertRaises(providers.AIProviderError):
                providers._request(self.settings('openai'), 'structured', invoke)
        self.assertEqual(1, invoke.call_count)

    def test_usage_invalid_counts_and_missing_thoughts(self):
        self.assertIsNone(contract.normalize_usage('gemini', {'prompt_token_count': 100, 'candidates_token_count': 20}).output_tokens)
        for value in (-1, True, '100', 10**12):
            self.assertIsNone(contract.normalize_usage('openai', {'input_tokens': value}).input_tokens)
        self.assertFalse(contract.normalize_usage('openai', {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 5}).known)
        self.assertFalse(contract.normalize_usage('gemini', {'prompt_token_count': 100,
            'candidates_token_count': 20, 'thoughts_token_count': 0, 'total_token_count': 110,
            'cached_content_token_count': 0}).known)

    def test_ocr_cancellation_stops_before_second_page_and_retains_usage(self):
        for provider in ('gemini', 'openai'):
            checkpoint = Mock(side_effect=[None, Cancelled()])
            with self.subTest(provider=provider), self.transport(provider, response(provider, 'text')) as transport:
                with contract.capture_receipts() as records, self.assertRaises(Cancelled):
                    providers.extract_text_from_images(self.settings(provider), [b'page1', b'page2'], checkpoint)
                self.assertEqual(1, transport.call_count)
                self.assertEqual(1, len(records))
                self.assertTrue(records[0].usage.known)
                self.assertEqual('cancelled', records[0].outcome)

    def test_sdk_enum_finish_and_block_reasons(self):
        for reason, expected in ((providers.types.FinishReason.MAX_TOKENS, 'truncated'),
                                 (providers.types.FinishReason.SAFETY, 'refusal')):
            reply = response('gemini')
            reply.candidates[0].finish_reason = reason
            with self.transport('gemini', reply), self.assertRaises(providers.AIProviderError) as caught:
                providers.generate_structured(self.settings('gemini'), 'test', Answer)
            self.assertEqual(expected, caught.exception.code)
        reply = response('gemini')
        reply.prompt_feedback = SimpleNamespace(block_reason=providers.types.BlockedReason.SAFETY)
        with self.transport('gemini', reply), self.assertRaises(providers.AIProviderError) as caught:
            providers.generate_structured(self.settings('gemini'), 'test', Answer)
        self.assertEqual('refusal', caught.exception.code)

    def test_inherited_concurrent_context_reserves_receipt_slot(self):
        entered, release = Event(), Event()
        def invoke():
            entered.set()
            self.assertTrue(release.wait(5))
        with contract.capture_receipts() as records, patch.object(contract, 'MAX_RECEIPTS', 1):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(copy_context().run, providers._request, self.settings('openai'), 'structured', invoke)
                try:
                    self.assertTrue(entered.wait(5))
                    with self.assertRaises(providers.AIProviderError):
                        providers._request(self.settings('openai'), 'structured', lambda: None)
                finally:
                    release.set()
                future.result()
            self.assertEqual(1, len(records))
            self.assertEqual('success', records[0].outcome)

    def test_unknown_cache_split_does_not_invent_discount(self):
        rate = contract.RateCard('openai', 'gpt-5-mini', 'synthetic', Decimal(1), Decimal(2), Decimal('.5'))
        self.assertIsNone(contract.estimated_cost(contract.Usage(100, 30), rate))
        with self.assertRaises(ValueError):
            contract.RateCard('openai', 'model', 'bad', Decimal('NaN'), Decimal(1), Decimal(1))

    def test_real_workflow_callers_preserve_results_and_capture_usage(self):
        import interview_prep
        import searcher
        import tailor

        from base_resumes import MAX_BASE_RESUME_CHARS, MAX_PROFESSIONAL_EVIDENCE_CHARS
        # Exercise accepted character limits using worst-case UTF-8 width.
        resume = '\U0001f600' * MAX_BASE_RESUME_CHARS
        job = {'title': 'Data Engineer', 'company': 'Synthetic Company',
               'description': '\U0001f600' * searcher.MAX_JOB_DESCRIPTION_CHARS,
               'match_analysis': '\U0001f600' * interview_prep.MAX_CONTEXT_CHARS,
               'tailored_resume_text': '\U0001f600' * interview_prep.MAX_CONTEXT_CHARS,
               'notes': '\U0001f600' * interview_prep.MAX_CONTEXT_CHARS}
        interview = {name: ['Prepare a truthful example.', 'Review the role requirements.',
                            'Prepare a question about the team.']
                     for name in interview_prep.InterviewPrepAIResponse.model_fields}
        for provider in ('gemini', 'openai'):
            options = {'api_key': 'PRIVATE-KEY', 'ai_provider': provider}
            cases = [
                ('query_preparation', {'titles': ['Data Engineer']},
                 lambda: searcher.extract_search_keywords_from_resume(resume, **options),
                 lambda result: self.assertEqual(['Data Engineer'], result)),
                ('scoring', {'match_score': 75, 'match_analysis': 'Synthetic match.'},
                 lambda: tailor.analyze_job_match(resume, job['title'], job['company'],
                                                  job['description'], **options),
                 lambda result: self.assertEqual(75, result['match_score'])),
                ('scoring', {'matches': [{'index': 0, 'match_score': 75, 'match_analysis': 'Synthetic match.'}]},
                 lambda: tailor.analyze_job_matches_batch(resume, [job], **options),
                 lambda result: self.assertEqual(75, result['matches'][0]['match_score'])),
                ('tailoring', {'tailored_resume': '# Candidate\n\n## Professional Summary\nSynthetic experience.',
                              'cover_letter': 'Dear hiring team,\nSynthetic cover letter.'},
                 lambda: tailor.tailor_resume_and_cover_letter(resume, job['title'], job['company'],
                                                               job['description'], **options,
                                                               professional_evidence='\U0001f600' * MAX_PROFESSIONAL_EVIDENCE_CHARS),
                 lambda result: self.assertTrue(result['success'])),
                ('interview_help', interview,
                 lambda: interview_prep.generate_interview_prep(self.settings(provider), job),
                 lambda result: self.assertIn('Prepare a truthful example.', result)),
            ]
            for operation, payload, invoke, verify in cases:
                with self.subTest(provider=provider, operation=operation, payload=tuple(payload)):
                    with self.transport(provider, response(provider, json.dumps(payload))):
                        with contract.capture_receipts() as receipts:
                            verify(invoke())
                    self.assertEqual(1, len(receipts))
                    self.assertEqual(operation, receipts[0].operation)
                    self.assertEqual('success', receipts[0].outcome)
                    self.assertEqual(contract.Usage(100, 30, 20, 10, 130), receipts[0].usage)
                    self.assertIsNone(receipts[0].estimated_cost_usd)
                    self.assertNotIn('PRIVATE', repr(receipts))

    def test_handled_workflow_failure_still_records_spent_attempt(self):
        import tailor

        for provider in ('gemini', 'openai'):
            with self.subTest(provider=provider), self.transport(provider, response(provider, 'invalid JSON')):
                with contract.capture_receipts() as receipts:
                    result = tailor.analyze_job_match('Synthetic resume', 'Engineer', 'Example',
                                                      'Synthetic role', api_key='PRIVATE-KEY', ai_provider=provider)
                self.assertFalse(result['success'])
                self.assertEqual('invalid_response', receipts[0].outcome)
                self.assertTrue(receipts[0].dispatched)
                self.assertTrue(receipts[0].usage.known)


if __name__ == '__main__':
    unittest.main()
