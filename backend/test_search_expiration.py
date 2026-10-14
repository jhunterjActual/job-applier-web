"""Synthetic expiration checks; no network, providers or persistent user data."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch
import unittest

import searcher


class StructuredExpirationTests(unittest.TestCase):
    url = 'https://careers.example.invalid/jobs/architect?id=42'
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)

    def expired(self, value, url=None):
        return searcher._structured_posting_expired(
            {'url': self.url if url is None else url, 'validThrough': value}, self.url, self.now)

    def test_expiration_boundary_and_offsets(self):
        self.assertTrue(self.expired('2026-10-14T11:59:59Z'))
        self.assertFalse(self.expired('2026-10-14T12:00:00Z'))
        self.assertFalse(self.expired('2026-10-14T12:00:01Z'))
        self.assertTrue(self.expired('2026-10-14T13:59:59+02:00'))
        self.assertFalse(self.expired('2026-10-14T08:00:01-04:00'))

    def test_ambiguous_and_invalid_values_stay_eligible(self):
        for value in (None, '', 0, [], {}, '2020-01-01', '2020-01-01T00:00:00',
                      '2020-02-30T00:00:00Z', '2020-01-01T00:00:00+00:99',
                      '2020-01-01T00:00:00+24:00', 'x' * 100):
            with self.subTest(value=value):
                self.assertFalse(self.expired(value))

    def test_identity_must_match(self):
        for url in ('', '/jobs/architect', self.url.replace('42', '43'),
                    'https://other.example.invalid/jobs/architect?id=42'):
            with self.subTest(url=url):
                self.assertFalse(self.expired('2020-01-01T00:00:00Z', url))
        self.assertTrue(self.expired('2020-01-01T00:00:00Z', self.url + '&utm_source=test'))
        self.assertFalse(searcher._structured_posting_expired(
            {'url': 'bad', 'validThrough': '2020-01-01T00:00:00Z'}, 'bad', self.now))

    def scrape(self, expiry, *, status=200, failure=None, posting_url=None, partial=False):
        page = Mock()
        page.url = self.url
        page.goto.return_value = Mock(status=status)
        page.goto.side_effect = failure
        page.title.return_value = 'Architect - Synthetic Employer'
        page.inner_text.return_value = 'Design reporting systems. ' * 30
        page.query_selector.return_value = None
        browser = Mock()
        browser.new_context.return_value.new_page.return_value = page
        playwright = MagicMock()
        playwright.return_value.__enter__.return_value.chromium.launch.return_value = browser
        posting = {'url': posting_url or self.url, 'validThrough': expiry,
                   'title': 'Architect', 'hiringOrganization': {'name': 'Synthetic Employer'},
                   'description': 'Design reporting systems. ' * 30}
        diagnostic = {}
        with (patch.object(searcher, 'sync_playwright', playwright),
              patch.object(searcher, 'validate_public_http_url', return_value=(True, '')),
              patch.object(searcher, 'is_public_http_url', return_value=True),
              patch.object(searcher, '_protect_browser_network'),
              patch.object(searcher, '_select_job_content_scope', return_value=(page, posting, False)),
              patch('builtins.print')):
            details = searcher._scrape_job_details_browser(self.url, allow_partial=partial, diagnostic=diagnostic)
        browser.close.assert_called_once()
        return details, diagnostic

    def test_expired_page_rejected_even_for_partial_import(self):
        for partial in (False, True):
            details, diagnostic = self.scrape('2000-01-01T00:00:00Z', partial=partial)
            self.assertEqual({}, details)
            self.assertEqual('stale', diagnostic['status'])

    def test_future_ambiguous_and_other_job_expiry_do_not_reject(self):
        for expiry, url in [('9999-01-01T00:00:00Z', self.url),
                            ('2000-01-01', self.url),
                            ('2000-01-01T00:00:00Z', self.url.replace('42', '43'))]:
            details, diagnostic = self.scrape(expiry, posting_url=url)
            self.assertEqual('ok', diagnostic['status'])
            self.assertEqual('Architect', details['title'])

    def test_timeout_access_and_server_errors_are_not_closure(self):
        for status, failure, expected in [(200, TimeoutError(), 'provider_error'),
                                          (403, None, 'access_challenge'),
                                          (429, None, 'access_challenge'),
                                          (503, None, 'provider_error')]:
            details, diagnostic = self.scrape('2000-01-01T00:00:00Z', status=status, failure=failure)
            self.assertEqual({}, details)
            self.assertEqual(expected, diagnostic['status'])


if __name__ == '__main__':
    unittest.main()
