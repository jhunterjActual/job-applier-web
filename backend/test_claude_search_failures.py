"""Real search-to-Claude dispatch with synthetic discovery and HTTP transport."""
import unittest
import urllib.error
from unittest.mock import Mock, patch

import ai_contracts as contracts
import ai_providers as providers
import claude_adapter as claude
import searcher
import search_run_repository as runs
from test_ai_contracts import HTTPResponse
from test_claude_adapter import reply


class ClaudeSearchFailureTests(unittest.TestCase):
    def run_search(self, response=None, error=None):
        repository = Mock()
        repository.search_catalog_context.return_value = {
            'known_status_by_url': {}, 'suppressed_fingerprints': set(),
        }
        opener = Mock()
        opener.open.side_effect = error
        opener.open.return_value = HTTPResponse(response if response is not None else reply())
        url = 'https://jobs.lever.co/synthetic/role'
        with (
            patch.object(searcher.config, 'get_discovery_mode', return_value='legacy'),
            patch.object(searcher, 'settings_from_profile', return_value=providers.AIProviderSettings(
                'anthropic', claude.MODEL, 'synthetic-key')),
            patch.object(searcher, 'search_yahoo_jobs', return_value=[{'url': url}]),
            patch.object(searcher, 'inspect_job_posting', return_value={'status': 'ok', 'details': {
                'url': url, 'title': 'Engineer', 'company': 'Synthetic',
                'description': 'Synthetic description', 'source': 'lever',
            }}),
            patch.object(claude.urllib.request, 'build_opener', return_value=opener),
            patch.object(searcher, '_wait_for_match_retry') as wait,
            patch('builtins.print'),
            contracts.capture_receipts() as receipts,
        ):
            result = searcher.run_job_search_and_matching('engineer', job_repository=repository,
                profile_repository=Mock(get_profile=Mock(return_value={'base_resume_text': 'Synthetic'})))
        repository.create_job_if_available.assert_not_called()
        return result, opener.open.call_count, wait.call_count, receipts

    def test_terminal_provider_failures_dispatch_once_and_survive_minimization(self):
        cases = [
            ('refusal', reply('PRIVATE-RESPONSE', 'refusal'), None),
            ('truncated', reply('{}', 'max_tokens'), None),
            ('invalid_input', None, urllib.error.HTTPError(claude.ENDPOINT, 413, 'PRIVATE', {}, None)),
            ('redirect_rejected', None, lambda *a, **k: claude._NoRedirect().redirect_request(
                None, None, 302, 'PRIVATE', {}, 'https://example.invalid')),
        ]
        for code, response, error in cases:
            with self.subTest(code=code):
                result, attempts, waits, receipts = self.run_search(response, error)
                self.assertEqual(1, attempts)
                self.assertEqual(0, waits)
                self.assertEqual(code, result['failure_code'])
                minimized = runs.minimized_search_result(result)
                self.assertEqual(code, minimized['failure_code'])
                self.assertEqual(runs.MATCH_PROVIDER_FAILURE_MESSAGES[code], minimized['error'])
                self.assertNotIn('PRIVATE', repr(minimized))
                self.assertEqual([code], [record.outcome for record in receipts])
                self.assertTrue(receipts[0].dispatched)

    def test_transient_failures_keep_bounded_retries(self):
        for status, code in ((429, 'rate_limit'), (529, 'overloaded'), (500, 'provider_error')):
            with self.subTest(status=status):
                result, attempts, waits, receipts = self.run_search(error=urllib.error.HTTPError(
                    claude.ENDPOINT, status, 'PRIVATE', {}, None))
                self.assertEqual(searcher.MATCH_PROVIDER_MAX_ATTEMPTS, attempts)
                self.assertEqual(attempts - 1, waits)
                self.assertEqual(code, result['failure_code'])
                self.assertEqual([code] * attempts, [record.outcome for record in receipts])
                self.assertEqual(code, runs.minimized_search_result(result)['failure_code'])

    def test_all_classified_failures_have_safe_persistence_messages(self):
        self.assertLessEqual(searcher.MATCH_PROVIDER_FAILURE_CODES, runs.MATCH_PROVIDER_FAILURE_CODES)
        for code in ('unsupported_provider', 'unsupported_operation', 'unsupported_storage',
                     'invalid_rate', 'receipt_limit', 'refusal', 'truncated', 'invalid_input',
                     'redirect_rejected'):
            self.assertNotIn(code, searcher.MATCH_PROVIDER_RETRYABLE_ERRORS)


if __name__ == '__main__':
    unittest.main()
