"""Synthetic public-feed discovery through matching and persistence contracts."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import public_job_feeds as feeds
import searcher
from operations import OperationCancelled


class PublicFeedIntegrationTests(unittest.TestCase):
    url = 'https://jobicy.com/jobs/123-software-engineer'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = patch.object(feeds, '_directory', return_value=Path(temporary.name))
        directory.start()
        self.addCleanup(directory.stop)
        self.payload = json.dumps({'jobs': [{
            'jobTitle': 'Software Engineer', 'url': self.url, 'companyName': 'Synthetic',
            'jobGeo': 'USA', 'jobType': ['contract'],
            'jobDescription': 'Build and maintain reliable software for a synthetic organization. ' * 5,
        }]})
        with patch.object(feeds, '_fetch', return_value=self.payload):
            feeds._snapshot('jobicy', lambda: None)
        with patch.object(feeds, '_fetch', return_value='<rss><channel/></rss>'):
            feeds._snapshot('weworkremotely', lambda: None)

    def test_feed_to_saved_match_preserves_source_and_geography(self):
        repository = Mock()
        repository.search_catalog_context.return_value = {
            'known_status_by_url': {}, 'suppressed_fingerprints': set()}
        repository.create_job_if_available.return_value = {'outcome': 'created'}
        with (
            patch.object(searcher.config, 'get_discovery_mode', return_value='hybrid_feeds'),
            patch.object(searcher, 'settings_from_profile', return_value=Mock(
                api_key='synthetic', provider='gemini', model='synthetic')),
            patch('discovery_sources.collect', return_value=[]),
            patch.object(searcher, 'search_yahoo_jobs', return_value=[]),
            patch.object(searcher, 'validate_public_http_url', return_value=(True, '')),
            patch.object(feeds, '_fetch', side_effect=AssertionError('Cache should be reused')),
            patch.object(searcher, '_scrape_job_details_browser', side_effect=AssertionError('No scrape')),
            patch.object(searcher, 'analyze_job_matches_batch', return_value={
                'success': True, 'matches': [{'index': 0, 'match_score': 75}]}),
            patch('builtins.print'),
        ):
            result = searcher.run_job_search_and_matching('software engineer', 'remote',
                job_repository=repository,
                profile_repository=Mock(get_profile=Mock(return_value={'base_resume_text': 'Synthetic'})))
        self.assertEqual(1, result['counts']['discovered'])
        self.assertEqual(1, result['counts']['scored'])
        self.assertEqual(1, result['jobs_added'])
        saved = repository.create_job_if_available.call_args.kwargs
        self.assertEqual(('USA', 'remote', 'contract', 'jobicy', self.url),
            tuple(saved[key] for key in ('location', 'work_arrangement', 'employment_type', 'source', 'url')))
        self.assertEqual(1, result['provider_health']['weworkremotely']['source_empty'])
        self.assertEqual(4, len(searcher.PROVIDER_DOMAINS))

    def test_cache_miss_and_invalid_listing_do_not_scrape(self):
        with patch.object(searcher, 'validate_public_http_url', return_value=(True, '')), \
             patch.object(searcher.urllib.request, 'build_opener') as transport:
            missing = searcher.inspect_job_posting('https://jobicy.com/jobs/999-missing')
            invalid = searcher.inspect_job_posting('https://jobicy.com/jobs', require_live=True)
        self.assertEqual('provider_error', missing['status'])
        self.assertEqual('unsafe_url', invalid['status'])
        transport.assert_not_called()

    def test_explicit_verification_requires_live_response(self):
        with patch.object(searcher, 'validate_public_http_url', return_value=(True, '')), \
             patch.object(searcher.urllib.request, 'build_opener') as transport:
            response = transport.return_value.open.return_value.__enter__.return_value
            response.status = 200
            self.assertEqual('ok', searcher.inspect_job_posting(self.url, require_live=True)['status'])
            self.assertEqual('HEAD', transport.return_value.open.call_args.args[0].method)
            for code, status in ((404, 'stale'), (410, 'stale'), (503, 'provider_error'), (302, 'provider_error')):
                transport.return_value.open.side_effect = HTTPError(self.url, code, '', {}, None)
                self.assertEqual(status, searcher.inspect_job_posting(self.url, require_live=True)['status'])

    def test_cancellation_propagates(self):
        with self.assertRaises(OperationCancelled):
            searcher.inspect_job_posting(self.url, cancel_check=Mock(side_effect=OperationCancelled()))


if __name__ == '__main__':
    unittest.main()
