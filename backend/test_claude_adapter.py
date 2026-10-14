"""Offline Claude transport/contract acceptance; never loads a real credential."""
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch
from decimal import Decimal

from pydantic import BaseModel, Field
import ai_contracts as contracts
import ai_providers
import claude_adapter as claude
from test_ai_contracts import HTTPResponse, Cancelled


class Answer(BaseModel):
    score: int = Field(ge=0, le=100)


def reply(text='{"score":75}', reason='end_turn'):
    return {'content': [{'type': 'text', 'text': text}], 'stop_reason': reason,
            'usage': {'input_tokens': 100, 'output_tokens': 30,
                      'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 0}}


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.settings = ai_providers.AIProviderSettings('anthropic', claude.MODEL, 'PRIVATE-KEY')

    def transport(self, document=None, error=None):
        opener = Mock()
        opener.open.return_value = HTTPResponse(document if document is not None else reply())
        opener.open.side_effect = error
        return patch.object(claude.urllib.request, 'build_opener', return_value=opener)

    def test_payload_schema_local_validation_and_usage(self):
        with self.transport() as transport, contracts.capture_receipts() as records:
            self.assertEqual({'score': 75}, claude.generate_structured(self.settings, 'PRIVATE-PROMPT', Answer))
        request = transport.return_value.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(claude.ENDPOINT, request.full_url)
        self.assertEqual('PRIVATE-KEY', request.get_header('X-api-key'))
        self.assertEqual('2023-06-01', request.get_header('Anthropic-version'))
        self.assertEqual(32768, payload['max_tokens'])
        self.assertEqual({'type': 'between_tools'}, payload['thinking'])
        schema = payload['output_config']['format']['schema']
        self.assertNotIn('maximum', schema['properties']['score'])
        self.assertIn('100', schema['properties']['score']['description'])
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(contracts.Usage(120, 30, 20, None, 150, 0), records[0].usage)
        self.assertIsNone(records[0].estimated_cost_usd)
        self.assertNotIn('PRIVATE', repr(records))
        self.assertNotIn('PRIVATE', repr(self.settings))
        with self.transport(reply('{"score":101}')), self.assertRaises(ai_providers.AIProviderError) as caught:
            claude.generate_structured(self.settings, 'test', Answer)
        self.assertEqual('invalid_response', caught.exception.code)

    def test_refusal_truncation_malformed_and_unknown_completion_keep_usage(self):
        for reason, text, code in [('refusal', 'PRIVATE', 'refusal'), ('max_tokens', '{"score":75}', 'truncated'),
                ('model_context_window_exceeded', '{}', 'truncated'), ('end_turn', 'bad JSON', 'invalid_response'),
                ('end_turn', '', 'empty_response'), ('tool_use', '{}', 'invalid_response')]:
            with self.subTest(reason=reason, code=code), self.transport(reply(text, reason)):
                with contracts.capture_receipts() as records, self.assertRaises(ai_providers.AIProviderError) as caught:
                    claude.generate_structured(self.settings, 'test', Answer)
                self.assertEqual(code, caught.exception.code)
                self.assertTrue(records[0].dispatched)
                self.assertTrue(records[0].usage.known)
                self.assertNotIn('PRIVATE', str(caught.exception))

    def test_http_and_timeout_errors_are_sanitized_without_retry(self):
        cases = [(urllib.error.HTTPError(claude.ENDPOINT, status, 'PRIVATE', {}, None), code)
                 for status, code in [(400, 'unsupported_request'), (401, 'authentication'),
                 (403, 'permission'), (429, 'rate_limit'), (529, 'overloaded')]]
        cases += [(TimeoutError('PRIVATE'), 'timeout'),
                  (urllib.error.URLError(TimeoutError('PRIVATE')), 'timeout')]
        for error, code in cases:
            with self.subTest(code=code), self.transport(error=error) as transport:
                with contracts.capture_receipts() as records, self.assertRaises(ai_providers.AIProviderError) as caught:
                    claude.generate_structured(self.settings, 'test', Answer)
                self.assertEqual(code, caught.exception.code)
                self.assertNotIn('PRIVATE', str(caught.exception))
                self.assertEqual(1, transport.return_value.open.call_count)
                self.assertFalse(records[0].usage.known)

    def test_cancellation_and_ocr_stop_before_next_page(self):
        for after in (False, True):
            with self.subTest(after=after), self.transport(reply('resume')) as transport:
                check = Mock(side_effect=[None, Cancelled()] if after else Cancelled())
                with contracts.capture_receipts() as records, self.assertRaises(Cancelled):
                    claude.extract_text_from_images(self.settings, [b'page1', b'page2'], check)
                self.assertEqual(int(after), transport.return_value.open.call_count)
                self.assertEqual(after, records[0].dispatched)
                self.assertEqual('cancelled', records[0].outcome)
        with self.transport(reply('resume')), contracts.capture_receipts() as records:
            self.assertEqual(['resume', 'resume'], claude.extract_text_from_images(self.settings, [b'a', b'b']))
        self.assertEqual(['resume_ocr'] * 2, [r.operation for r in records])

    def test_preflight_and_registration_boundary(self):
        with self.transport() as transport:
            for settings in [ai_providers.AIProviderSettings('gemini', claude.MODEL, 'key'),
                             ai_providers.AIProviderSettings('anthropic', 'unreviewed', 'key'),
                             ai_providers.AIProviderSettings('anthropic', claude.MODEL, '')]:
                with self.assertRaises(ai_providers.AIProviderError):
                    claude.generate_structured(settings, 'test', Answer)
            for images in ([b'good', b''], [b'x'] * 13,
                           [b'x' * (claude.MAX_ENCODED_IMAGE_BYTES * 3 // 4 + 1)]):
                with self.assertRaises(ai_providers.AIProviderError):
                    claude.extract_text_from_images(self.settings, images)
            for kwargs in ({'operation': 'resume_ocr'}, {'timeout_seconds': True}, {'timeout_seconds': 91}):
                with self.assertRaises(ai_providers.AIProviderError):
                    claude.generate_structured(self.settings, 'test', Answer, **kwargs)
            transport.assert_not_called()
        self.assertIn('anthropic', ai_providers.AI_PROVIDERS)
        with patch.object(ai_providers, 'provider_secret_store_kind', return_value='local'), \
                self.assertRaises(ai_providers.AIProviderError):
            ai_providers.settings_from_profile({'ai_provider': 'anthropic'})

    def test_cache_usage_and_unknowns_never_invent_free_tokens(self):
        metadata = reply()['usage']
        metadata['cache_creation_input_tokens'] = 40
        usage = contracts.normalize_usage('anthropic', metadata)
        self.assertEqual(160, usage.input_tokens)
        rates = contracts.RateCard('anthropic', claude.MODEL, 'synthetic', Decimal(2), Decimal(10), Decimal('.1'))
        self.assertIsNone(contracts.estimated_cost(usage, rates))
        for field in metadata:
            for bad in (None, -1, True, '2'):
                self.assertFalse(contracts.normalize_usage('anthropic', dict(metadata, **{field: bad})).known)

    def test_redirect_and_response_caps(self):
        with self.assertRaises(ai_providers.AIProviderError):
            claude._NoRedirect().redirect_request(None, None, 302, 'PRIVATE', {}, 'https://example.invalid')
        with self.transport(reply('x' * (ai_providers.MAX_PROVIDER_RESPONSE_BYTES + 1))):
            with self.assertRaises(ai_providers.AIProviderError) as caught:
                claude.generate_structured(self.settings, 'test', Answer)
            self.assertEqual('oversized_response', caught.exception.code)

    def test_existing_workflow_schemas(self):
        from tailor import JobMatchResponse, BatchMatchResponse, TailoringResponse
        from searcher import SearchKeywordResponse
        from interview_prep import InterviewPrepAIResponse
        from maps_providers import HeadquartersAIResponse
        cases = [(SearchKeywordResponse, {'titles': ['Data Engineer']}, 'query_preparation'),
                 (JobMatchResponse, {'match_score': 70, 'match_analysis': 'Synthetic'}, 'scoring'),
                 (BatchMatchResponse, {'matches': [{'index': 0, 'match_score': 70,
                                                   'match_analysis': 'Synthetic'}]}, 'scoring'),
                 (TailoringResponse, {'tailored_resume': 'Synthetic', 'cover_letter': 'Synthetic'}, 'tailoring'),
                 (InterviewPrepAIResponse, {field: ['Synthetic'] for field in InterviewPrepAIResponse.model_fields},
                  'interview_help'),
                 (ai_providers.CapabilityResponse, {'ready': True}, 'capability_check')]
        # Headquarters has its own optional fields; exercise the declared schema
        # transformation even when this slice does not invoke the maps workflow.
        self.assertIsInstance(claude._schema(HeadquartersAIResponse), dict)
        for schema, payload, operation in cases:
            with self.subTest(schema=schema.__name__), self.transport(reply(json.dumps(payload))):
                with contracts.capture_receipts() as records:
                    self.assertEqual(payload, claude.generate_structured(self.settings, 'Synthetic', schema))
                self.assertEqual(operation, records[0].operation)

    def test_malformed_envelopes_and_missing_usage(self):
        for document in ([], {'stop_reason': 'end_turn', 'content': {}},
                         {'stop_reason': 'end_turn', 'content': [None]}):
            with self.subTest(document=document), self.transport(document):
                with self.assertRaises(ai_providers.AIProviderError) as caught:
                    claude.generate_structured(self.settings, 'test', Answer)
                self.assertEqual('invalid_response', caught.exception.code)
        document = reply()
        document.pop('usage')
        with self.transport(document), contracts.capture_receipts() as records:
            claude.generate_structured(self.settings, 'test', Answer)
        self.assertFalse(records[0].usage.known)


if __name__ == '__main__':
    unittest.main()
