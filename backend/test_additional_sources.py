"""Synthetic provider contracts and deterministic filtering; no real network/data."""
import io
import json
import unittest
from unittest.mock import Mock, patch

import additional_sources as sources
import candidate_filters as filters
import discovery_sources
import searcher
from operations import OperationCancelled


class FilterTests(unittest.TestCase):
    def test_query_rejections_are_not_format_drift(self):
        alerts = searcher.provider_alerts_from_health({'greenhouse': {
            'new_candidates': 3, 'accepted': 0,
            'rejection_reasons': {'query_mismatch': 1, 'eligibility_unknown': 2}}})
        self.assertEqual([], alerts)

    def test_roles_use_phrases_and_reviewed_aliases(self):
        self.assertTrue(filters.role_matches('Senior Software Developer', ['software engineer']))
        self.assertFalse(filters.role_matches('Forward Deployed Engineer, Internal Data Products', ['data engineer']))
        self.assertFalse(filters.role_matches('Enterprise Solutions Architect', ['enterprise architect']))
        self.assertFalse(filters.role_matches('Engineering Manager', ['engineer']))
        self.assertTrue(filters.role_matches('Senior Software Engineer', ['senior engineer']))

    def test_locations_and_unknowns(self):
        self.assertTrue(filters.location_matches({'location': 'Dayton, Ohio'}, ['Dayton OH']))
        self.assertFalse(filters.location_matches({'location': 'Daytona, OH'}, ['Dayton OH']))
        self.assertFalse(filters.location_matches({'location': 'Beavercreek OH'}, ['Dayton OH']))
        self.assertTrue(filters.needs_location_details({'location': 'Remote'}, ['US remote']))
        self.assertTrue(filters.needs_location_details({'location': 'New York', 'remote': False}, ['remote']))
        self.assertTrue(filters.needs_location_details({'location': 'New York, United States', 'remote': False}, ['US remote']))
        self.assertFalse(filters.detail_location_matches({'location': ''}, ['Dayton OH']))
        self.assertFalse(filters.detail_location_matches({'description': 'Work with us on remote sensing.'}, ['US remote']))
        self.assertTrue(filters.detail_location_matches({'description': 'Work remote anywhere in the United States.'}, ['US remote']))
        self.assertFalse(filters.detail_location_matches({'description': 'Not remote. Our office is in the United States.'}, ['US remote']))
        self.assertTrue(filters.detail_location_matches({'location': 'Dayton, Ohio', 'description': 'This position is not remote.'}, ['Dayton OH', 'remote']))

    def test_inferred_remote_does_not_override_description_evidence(self):
        for description in ['This is not remote.', 'We build remote sensing software.',
                            'Experience managing remote teams required.']:
            details = {'location': 'United States', 'description': description,
                       '_work_arrangement_from_text': True, **searcher._metadata_from_text(description)}
            self.assertFalse(filters.detail_location_matches(details, ['US remote']))
        text = 'This is a fully remote position.'
        details = {'location': 'United States', 'description': text,
                   '_work_arrangement_from_text': True, **searcher._metadata_from_text(text)}
        self.assertTrue(filters.detail_location_matches(details, ['US remote']))

    def test_unknown_queue_is_bounded_and_prioritizes_known(self):
        health = {'greenhouse': {'partial_results': {}}}
        rows = [{'title': 'Software Developer', 'absolute_url': f'https://boards.greenhouse.io/test/jobs/{i}',
                 'location': {'name': 'Remote'}} for i in range(12)]
        with patch.object(discovery_sources, 'CATALOG', (('greenhouse', 'test'),)), patch.object(
                discovery_sources, 'feed_rows', return_value=discovery_sources.FeedRows(rows)):
            result = discovery_sources.collect(['software engineer'], ['US remote'], health, lambda: None, feeds_only=True)
        self.assertEqual(2, len(result))
        self.assertTrue(all(row['verify_query'] for row in result))
        self.assertGreater(health['greenhouse']['partial_results']['unknown_location_limit'], 0)


class AdditionalSourceTests(unittest.TestCase):
    ud = 'https://employment.udayton.edu/en-us/job/123456/software-engineer'
    zr = 'https://www.ziprecruiter.com/c/Synthetic/Job/Engineer?jid=abc123'

    def document(self, status='OK'):
        rows = [] if status == 'NO_RESULTS' else [{
            'title': 'Software Engineer', 'company': 'Synthetic', 'location': 'Dayton OH',
            'is_remote': False, 'job_redirect_url': self.zr}]
        return {'jsonrpc': '2.0', 'id': 1, 'result': {'structuredContent': {
            'status': status, 'results': rows, 'warnings': [],
            'meta': {'count': len(rows), 'limit': 5, 'total': len(rows)}}}}

    def test_disabled_before_network(self):
        self.assertEqual((), sources.ENABLED)
        with patch.object(sources, '_request') as request:
            self.assertEqual([], sources.collect(['software engineer'], ['Dayton OH'], {}, lambda: None))
        request.assert_not_called()

    def test_identity_and_unsafe_urls(self):
        self.assertEqual(self.zr, searcher.canonicalize_job_url(self.zr + '&utm_source=test'))
        self.assertNotEqual(searcher.canonicalize_job_url(self.zr), searcher.canonicalize_job_url(self.zr.replace('abc123', 'def456')))
        for url in [self.zr + '&jid=other', self.ud.replace('https:', 'http:'),
                    self.ud.replace('employment.udayton.edu', 'employment.udayton.edu.evil.test'),
                    self.zr.replace('www.', 'user@www.'), self.zr.replace('abc123', ''),
                    'https://www.ziprecruiter.com/jobs']:
            self.assertIsNone(sources.canonical_source_url(url))
        self.assertEqual('', searcher._canonical_redirected_job_url(self.zr, self.zr.replace('abc123', 'def456')))
        self.assertEqual('', searcher._canonical_redirected_job_url(self.ud, self.ud.replace('123456', '123457')))

    def test_ud_repeated_rows_and_pagination(self):
        link = '<a class="job-link" href="/en-us/job/123456/software-engineer">Software Engineer</a>'
        rows, complete = sources.parse_ud_listing(link + link + '<a class="more-link" href="?page=2">More</a>')
        self.assertEqual(1, len(rows))
        self.assertFalse(complete)
        self.assertNotIn('feed_metadata', rows[0])  # Never use university address as work location.
        self.assertEqual(self.ud, rows[0]['url'])

    def test_ud_template_drift_or_injected_link_fails(self):
        for raw in ['<h1>Access denied</h1>', '<a class="job-link" href="https://evil.test/job">Engineer</a>']:
            with self.assertRaises(sources.SourceError):
                sources.parse_ud_listing(raw)

    def test_zip_json_and_sse(self):
        raw = json.dumps(self.document())
        for body in [raw, 'event: message\ndata: ' + raw + '\n\n']:
            rows, limited = sources.parse_zip_response(body, 1)
            self.assertEqual(self.zr, rows[0]['url'])
            self.assertFalse(limited)
            self.assertNotIn('description', rows[0])

    def test_zip_failures_are_not_empty_success(self):
        for status in ['ERROR', 'UNSUPPORTED_COUNTRY', 'UNSUPPORTED_CONSTRAINTS']:
            with self.assertRaises(sources.SourceError):
                sources.parse_zip_response(json.dumps(self.document(status)), 1)
        data = self.document()
        data['result']['structuredContent']['meta']['count'] = 2
        with self.assertRaises(sources.SourceError):
            sources.parse_zip_response(json.dumps(data), 1)
        with self.assertRaises(sources.SourceError):
            sources.parse_zip_response(json.dumps(self.document()), 2)
        self.assertEqual(([], False), sources.parse_zip_response(json.dumps(self.document('NO_RESULTS')), 1))

    def test_zip_cap_and_warning(self):
        data = self.document()
        data['result']['structuredContent']['meta']['total'] = 100
        self.assertTrue(sources.parse_zip_response(json.dumps(data), 1)[1])

    def test_arguments_never_invent_country_radius_or_skills(self):
        self.assertEqual({'job_role': 'engineer', 'location': 'Dayton OH', 'offset': 0}, sources.zip_arguments('engineer', 'Dayton OH'))
        self.assertEqual(['REMOTE'], sources.zip_arguments('engineer', 'remote')['location_types'])

    def test_transport_bounds_and_no_retry(self):
        budget = discovery_sources.Budget(lambda: None)
        with patch.object(sources.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value = io.BytesIO(b'x' * (sources.MAX_BYTES + 1))
            with self.assertRaisesRegex(sources.SourceError, 'oversized_response'):
                sources._request(sources.ZIP_ENDPOINT, budget, {})
            self.assertEqual(1, opener.return_value.open.call_count)
            self.assertEqual(1, budget.requests)
        with self.assertRaisesRegex(sources.SourceError, 'invalid_endpoint'):
            sources._request('http://127.0.0.1/', budget)

    def test_cancellation_propagates(self):
        def cancelled():
            raise OperationCancelled()
        with self.assertRaises(OperationCancelled):
            sources._request(sources.ZIP_ENDPOINT, discovery_sources.Budget(cancelled), {})

    def test_partial_ud_pages_preserve_candidates_on_later_failure(self):
        html = '<a class="job-link" href="/en-us/job/123456/software-engineer">Software Engineer</a><a class="more-link">More</a>'
        health = {'udayton': {}}
        with patch.object(sources, 'ENABLED', ('udayton',)), patch.object(sources, '_request', side_effect=[html, sources.SourceError('network')]):
            found = sources.collect(['software engineer'], ['Dayton OH'], health, lambda: None)
        self.assertEqual(1, len(found))
        self.assertEqual(1, health['udayton']['source_failures'])
        self.assertNotIn('source_empty', health['udayton'])

    def test_detail_gate_precedes_scoring_and_save(self):
        url = 'https://jobs.lever.co/synthetic/id'
        for title, location, expected in [('Software Developer', 'Dayton, Ohio', 1),
                                          ('Software Engineer', 'Beavercreek OH', 0),
                                          ('Sales Engineer', 'Dayton OH', 0),
                                          ('Software Engineer', '', 0)]:
            repository = Mock()
            repository.search_catalog_context.return_value = {'known_status_by_url': {}, 'suppressed_fingerprints': set()}
            repository.create_job_if_available.return_value = {'outcome': 'created'}
            with patch.object(searcher.config, 'get_discovery_mode', return_value='legacy'), patch.object(
                    searcher, 'settings_from_profile', return_value=Mock(api_key='synthetic', provider='gemini', model='synthetic')), patch.object(
                    searcher, 'search_yahoo_jobs', return_value=[{'url': url, 'verify_query': True}]), patch.object(
                    searcher, 'inspect_job_posting', return_value={'status': 'ok', 'details': {
                        'url': url, 'title': title, 'location': location, 'company': 'Synthetic', 'description': 'Synthetic duties'}}), patch.object(
                    searcher, 'analyze_job_matches_batch', return_value={'success': True, 'matches': [{'index': 0, 'match_score': 90}]}) as score, patch('builtins.print'):
                result = searcher.run_job_search_and_matching('software engineer', location='Dayton OH',
                    job_repository=repository, profile_repository=Mock(get_profile=Mock(return_value={'base_resume_text': 'Synthetic'})))
            self.assertEqual(expected, score.call_count)
            self.assertEqual(expected, result['jobs_added'])
            self.assertEqual(1 - expected, result['counts']['filtered'])


if __name__ == '__main__':
    unittest.main()
