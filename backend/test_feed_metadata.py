"""Synthetic end-to-end coverage for metadata lost at the detail boundary."""
import unittest
from unittest.mock import Mock, patch

import discovery_sources as sources
import searcher


class FeedMetadataTests(unittest.TestCase):
    url = 'https://jobs.smartrecruiters.com/BoschGroup/744000000000001'

    def row(self):
        return {'id': '744000000000001', 'name': 'Enterprise Architect',
                'company': {'identifier': 'BoschGroup'},
                'location': {'city': 'Stuttgart', 'region': 'BW', 'country': 'Germany',
                             'remote': False, 'hybrid': True},
                'typeOfEmployment': {'label': 'Full-time'}}

    def test_validated_feed_metadata_reaches_scoring_and_save(self):
        for details_mode in ('missing', 'existing', 'rejected', 'different_url', 'slug_redirect', 'different_employer'):
            with self.subTest(details_mode=details_mode):
                repository = Mock()
                repository.search_catalog_context.return_value = {
                    'known_status_by_url': {}, 'suppressed_fingerprints': set()}
                repository.create_job_if_available.return_value = {'outcome': 'created'}
                details = {'url': self.url, 'title': 'Enterprise Architect',
                           'company': 'Synthetic', 'source': 'smartrecruiters',
                           'description': 'Synthetic independently validated posting.'}
                if details_mode == 'existing':
                    details.update(location='Berlin', work_arrangement='on_site', employment_type='contract')
                if details_mode == 'different_url':
                    details['url'] += '2'
                if details_mode == 'slug_redirect':
                    details['url'] += '-enterprise-architect'
                if details_mode == 'different_employer':
                    details['url'] = details['url'].replace('BoschGroup', 'OtherEmployer')
                with (
                    patch.object(sources, 'CATALOG', (('smartrecruiters', 'BoschGroup'),)),
                    patch.object(sources, 'feed_rows', return_value=sources.FeedRows([self.row()])),
                    patch.object(sources, 'fetch_json', side_effect=AssertionError('No provider calls')),
                    patch('public_job_feeds.collect', return_value=[]),
                    patch.object(searcher.config, 'get_discovery_mode', return_value='hybrid_feeds'),
                    patch.object(searcher, 'search_yahoo_jobs', return_value=[]),
                    patch.object(searcher, 'settings_from_profile', return_value=Mock(
                        api_key='synthetic', provider='gemini', model='synthetic')),
                    patch.object(searcher, 'inspect_job_posting', return_value={
                        'status': 'not_a_job' if details_mode == 'rejected' else 'ok',
                        'details': None if details_mode == 'rejected' else details}),
                    patch.object(searcher, 'analyze_job_matches_batch', return_value={
                        'success': True, 'matches': [{'index': 0, 'match_score': 90}]}) as score,
                    patch('builtins.print'),
                ):
                    result = searcher.run_job_search_and_matching('Architect', job_repository=repository,
                        profile_repository=Mock(get_profile=Mock(return_value={'base_resume_text': 'Synthetic'})))
                if details_mode == 'rejected':
                    score.assert_not_called()
                    repository.create_job_if_available.assert_not_called()
                    self.assertEqual(0, result['jobs_added'])
                    continue
                saved = repository.create_job_if_available.call_args.kwargs
                expected = {'missing': ('Stuttgart, BW, Germany', 'hybrid', 'full_time'),
                            'existing': ('Berlin', 'on_site', 'contract'),
                            'different_url': ('', '', ''),
                            'different_employer': ('', '', ''),
                            'slug_redirect': ('Stuttgart, BW, Germany', 'hybrid', 'full_time')}[details_mode]
                self.assertEqual(expected, tuple(saved[key] for key in
                    ('location', 'work_arrangement', 'employment_type')))
                scored = score.call_args.args[1][0]
                self.assertEqual(expected, tuple(scored.get(key, '') for key in
                    ('location', 'work_arrangement', 'employment_type')))
                self.assertEqual(1, result['jobs_added'])
                # Supplementation must not mutate the detail-parser result.
                if details_mode == 'missing':
                    self.assertNotIn('location', details)

    def test_false_remote_does_not_invent_on_site(self):
        row = self.row()
        row['location'].pop('hybrid')
        row['typeOfEmployment'] = {'label': {'unexpected': 'value'}}
        candidate = sources.feed_candidate('smartrecruiters', 'BoschGroup', row)
        self.assertEqual('', candidate['work_arrangement'])
        self.assertEqual('', candidate['employment_type'])
        self.assertFalse(sources.matches(candidate, ['Architect'], ['remote']))

    def test_metadata_url_must_match_and_values_are_bounded(self):
        details = {'url': self.url, 'title': 'Architect'}
        candidate = {'url': self.url, 'feed_metadata': {
            'url': self.url + '2', 'location': 'Wrong location'}}
        self.assertEqual(details, searcher._supplement_feed_metadata(details, candidate))
        candidate['feed_metadata'].update(url=self.url, location=' x\n' * 500)
        supplemented = searcher._supplement_feed_metadata(details, candidate)
        self.assertLessEqual(len(supplemented['location']), 240)
        self.assertNotIn('\n', supplemented['location'])


if __name__ == '__main__':
    unittest.main()
