"""Synthetic role-filter and rejection export regressions; no provider calls."""
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

import app as app_module
import searcher
from ai_providers import AIProviderSettings
from discovery_repository import _diagnostic_history
from search_run_repository import minimized_search_result
from search_review import record_rejection, role_filter, sanitize_review
from source_diagnostics import persist_source_diagnostics, list_source_diagnostics, build_maintainer_report


class SearchReviewTests(unittest.TestCase):
    def test_worker_saves_review_even_without_source_alerts(self):
        import search_run_execution
        result = {'provider_alerts': [], 'role_filter': role_filter(['Engineer'], 'resume_suggested')}
        with patch.object(search_run_execution, 'runtime_discovery_repository') as repository:
            search_run_execution._save_source_diagnostic_history(result)
        repository.return_value.persist_source_diagnostics.assert_called_once()
        self.assertEqual(result, repository.return_value.persist_source_diagnostics.call_args.args[0])

    def test_catalog_rejections_keep_the_stage_and_reason(self):
        import discovery_sources as sources
        health = {'lever': {'_role_filter': role_filter(['Data Engineer'], 'resume_suggested')}}
        rows = sources.FeedRows([{'title': 'Sales Manager', 'location': 'Dayton OH'},
                                {'title': 'Data Engineer', 'location': 'Canada', 'work_arrangement': 'onsite'}], complete=True)
        with patch.object(sources, 'CATALOG', (('lever', 'synthetic'),)), \
             patch.object(sources, 'feed_rows', return_value=rows), \
             patch.object(sources, 'feed_candidate', side_effect=lambda provider, board, row: row):
            self.assertEqual([], sources.collect(['Data Engineer'], ['Dayton OH'], health, lambda: None, feeds_only=True))
        rejected = health['lever']['_search_review']['rejections']
        self.assertEqual(['query_mismatch', 'location_mismatch'], [row['reason'] for row in rejected])
        self.assertTrue(all(row['stage'] == 'discovery' for row in rejected))

    def test_cached_preview_is_owner_scoped_and_does_not_generate_keywords(self):
        repository = Mock()
        repository.get_profile.return_value = {'suggested_keywords': 'Data Engineer, Software Developer'}
        with patch.object(app_module, 'runtime_profile_repository', return_value=repository), \
             patch.object(searcher, 'extract_search_keywords_from_resume', side_effect=AssertionError('No paid preview')):
            plan = app_module.preview_search_titles(app_module.SearchRequest(keywords=''))
            self.assertEqual(['Data Engineer', 'Software Developer'], plan['titles'])
            self.assertEqual('resume_suggested', plan['mode'])
            self.assertTrue(plan['software_alias'])
            repository.get_profile.return_value = {'suggested_keywords': ''}
            self.assertEqual([], app_module.preview_search_titles(app_module.SearchRequest(keywords=''))['titles'])
        with patch.object(app_module, 'runtime_profile_repository', side_effect=AssertionError('Manual preview needs no profile')):
            self.assertEqual(['Engineer'], app_module.preview_search_titles(app_module.SearchRequest(keywords='Engineer'))['titles'])

    def test_blank_keyword_pipeline_retains_actual_filter_and_detail_rejection(self):
        profile, jobs = Mock(), Mock()
        profile.get_profile.return_value = {'base_resume_text': 'Synthetic resume', 'suggested_keywords': 'Data Engineer'}
        jobs.search_catalog_context.return_value = {'known_status_by_url': {}, 'suppressed_fingerprints': set()}
        candidate = {'url': 'https://jobs.lever.co/synthetic/one', 'title': 'Discovery title', 'verify_query': True}
        with patch.object(searcher, 'settings_from_profile', return_value=AIProviderSettings('gemini', 'synthetic', '')), \
             patch.object(searcher.config, 'get_discovery_mode', return_value='legacy'), \
             patch.object(searcher, 'search_yahoo_jobs', return_value=[candidate]), \
             patch.object(searcher, 'inspect_job_posting', return_value={'details': {'title': 'Sales Manager', 'url': candidate['url'], 'location': 'Dayton OH'}}), \
             patch.object(searcher, 'analyze_job_matches_batch', side_effect=AssertionError('Rejected before scoring')):
            result = searcher.run_job_search_and_matching('', 'dayton, oh; remote', profile_repository=profile, job_repository=jobs)
        self.assertEqual(0, result['counts']['scored'])
        self.assertEqual(['Data Engineer'], minimized_search_result(result)['role_filter']['titles'])
        review = result['provider_health']['lever']['_search_review']
        self.assertEqual([{'title': 'Sales Manager', 'reason': 'query_mismatch', 'stage': 'inspection'}], review['rejections'])

    def test_sqlite_and_postgres_readback_preserve_bounded_reviews_without_report_leak(self):
        health = {'_role_filter': role_filter(['Synthetic Role'], 'resume_suggested')}
        for i in range(55):
            record_rejection(health, f'Synthetic title {i}', 'query_mismatch', 'discovery')
        record_rejection(health, '<img src=x> Synthetic', 'query_mismatch')
        record_rejection(health, None, 'stale')
        result = {'provider_alerts': [], 'provider_health': {'lever': health}}
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE source_diagnostics (id INTEGER PRIMARY KEY, recorded_at TEXT, provider TEXT, diagnostic_code TEXT, counters_json TEXT)')
        self.assertEqual(1, persist_source_diagnostics(connection, result, '2026-10-16T12:00:00'))
        history = list_source_diagnostics(connection)
        review = history['items'][0]['search_review']
        self.assertEqual(52, len(review['rejections']))
        self.assertEqual(5, review['omitted']['discovery'])
        self.assertEqual('Synthetic Role', review['role_filter']['titles'][0])
        row = dict(connection.execute('SELECT * FROM source_diagnostics').fetchone())
        row['counters'] = json.loads(row.pop('counters_json'))
        self.assertEqual(review, _diagnostic_history(1, [row])['items'][0]['search_review'])
        report = build_maintainer_report(history, 'synthetic', '2026-10-16')
        self.assertEqual(0, report['reportable_event_count'])
        self.assertNotIn('Synthetic Role', json.dumps(report))
        with patch.object(app_module, 'runtime_discovery_repository') as factory:
            factory.return_value.list_source_diagnostics.return_value = history
            exported = app_module.export_source_diagnostic_history()
        self.assertEqual(2, json.loads(exported.body)['schema_version'])
        self.assertIn('Synthetic Role', exported.body.decode())
        self.assertEqual('no-store', exported.headers['cache-control'])

    def test_malformed_review_and_unrelated_fields_are_not_retained(self):
        self.assertIsNone(sanitize_review({'raw_error': 'private'}))
        review = sanitize_review({'role_filter': {'titles': ['Role'], 'mode': 'resume_suggested'},
                                  'rejections': [{'reason': [], 'stage': 'inspection'},
                                                 {'title': 'Engineer https://private.example/path user@example.com', 'reason': 'query_mismatch', 'stage': 'inspection', 'description': 'private'}],
                                  'omitted': {'inspection': -1}, 'resume': 'private'})
        self.assertEqual(1, len(review['rejections']))
        self.assertNotIn('private', json.dumps(review))
        self.assertNotIn('user@example', json.dumps(review))
        self.assertEqual(0, review['omitted']['inspection'])


if __name__ == '__main__':
    unittest.main()
