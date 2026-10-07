"""Offline coverage accounting: overlapping pages are not unique inventory."""
import json
import sqlite3
import unittest
from unittest.mock import patch

import discovery_sources as sources
import public_job_feeds
import searcher
import source_diagnostics as diagnostics
from candidate_filters import role_matches, detail_location_matches


class CoverageDiagnosticsTests(unittest.TestCase):
    def test_pages_failures_exclusions_duplicates_and_totals(self):
        health = {'lever': {}}
        def page(provider, board, budget, *, page_index, **kwargs):
            budget.take()
            if board == 'broken':
                raise sources.SourceError('network')
            return sources.FeedRows([
                {'title': 'Software Engineer', 'location': 'Dayton OH', 'url': 'https://jobs.lever.co/test/1',
                 'work_arrangement': '', 'employment_type': ''},
                {'title': 'Sales Engineer', 'location': 'Dayton OH'},
                {'title': 'Software Engineer', 'location': 'Daytona FL'},
                {'title': 'Software Engineer', 'location': '', 'url': 'https://jobs.lever.co/test/2',
                 'work_arrangement': '', 'employment_type': ''}, None,
            ], complete=False, advertised_total=100)
        with patch.object(sources, 'CATALOG', (('lever', 'test'), ('lever', 'broken'))), \
                patch.object(sources, 'feed_rows', side_effect=page), \
                patch.object(sources, 'feed_candidate', side_effect=lambda p, b, row: row):
            found = sources.collect(['software engineer'], ['Dayton OH'], health, lambda: None, feeds_only=True)
        counts = health['lever']
        self.assertEqual(2, len(found))
        self.assertEqual(4, counts['coverage_http_attempts'])
        self.assertEqual(3, counts['coverage_pages_read'])
        self.assertEqual(15, counts['coverage_rows_read'])
        self.assertEqual(100, counts['coverage_advertised_rows'])
        self.assertEqual(1, counts['coverage_queries_with_total'])
        for key in ('role_excluded', 'location_excluded', 'location_unknown', 'metadata_matches', 'unlisted_rows'):
            self.assertEqual(3, counts['coverage_' + key])
        self.assertEqual(3, counts['coverage_duplicate_candidates'])
        self.assertEqual(1, counts['coverage_failed_boards'])
        self.assertEqual(1, counts['partial_results']['catalog_page_limits'])
        self.assertEqual(1, counts['partial_results']['unknown_location_limit'])

    def test_cache_hits_do_not_count_as_http_attempts(self):
        sources.clear_feed_cache()
        self.addCleanup(sources.clear_feed_cache)
        health = {'lever': {}}
        with patch.object(sources, 'CATALOG', (('lever', 'test'),)), \
                patch.object(sources, 'fetch_json', return_value=[]):
            sources.feed_rows('lever', 'test', sources.Budget(lambda: None), page_index=0)
            sources.collect(['engineer'], ['remote'], health, lambda: None, feeds_only=True)
        self.assertEqual(1, health['lever']['coverage_cached_pages'])
        self.assertEqual(0, health['lever']['coverage_http_attempts'])
        self.assertEqual(1, health['lever']['coverage_complete_boards'])

    def test_downstream_cap_and_duplicates_are_separate(self):
        health = {'lever': {}}
        results, seen, counts = [], set(), {'lever': 0}
        with patch.object(searcher, 'MAX_PROVIDER_CANDIDATES', 1):
            for suffix in ('one', 'one', 'two'):
                searcher._append_candidate_with_budget({'url': 'https://jobs.lever.co/test/' + suffix},
                                                       results, seen, counts, health)
        self.assertEqual(1, len(results))
        self.assertEqual(1, health['lever']['coverage_duplicate_candidates'])
        self.assertEqual(1, health['lever']['coverage_detail_cap_excluded'])

    def test_public_snapshot_is_not_counted_as_inventory_or_http(self):
        health = {'jobicy': {}}
        rows = [{'title': title, 'location': location, 'url': 'https://jobicy.com/jobs/123-test', 'source': 'jobicy'}
                for title, location in [('Software Engineer', 'Dayton OH'), ('Sales Engineer', 'Dayton OH'),
                                        ('Software Engineer', 'Daytona FL')]]
        with patch.object(public_job_feeds, 'PROVIDERS', ('jobicy',)), \
                patch.object(public_job_feeds, '_snapshot', return_value=rows):
            found = public_job_feeds.collect(['software engineer'], ['Dayton OH'], health, lambda: None)
        self.assertEqual(1, len(found))
        self.assertEqual(1, health['jobicy']['coverage_snapshots_read'])
        self.assertEqual(3, health['jobicy']['coverage_rows_read'])
        self.assertNotIn('coverage_advertised_rows', health['jobicy'])
        self.assertNotIn('coverage_http_attempts', health['jobicy'])

    def test_safe_persistence_roundtrip_excludes_content(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE source_diagnostics (id INTEGER PRIMARY KEY, recorded_at TEXT, '
                           'provider TEXT, diagnostic_code TEXT, counters_json TEXT)')
        health = {'coverage_rows_read': 15, 'query': 'private search', 'url': 'private URL',
                  'coverage_advertised_rows': 10**12, 'rejection_reasons': {'query_mismatch': 2, 'eligibility_unknown': 1},
                  'partial_results': {'unknown_location_limit': 3}}
        diagnostics.persist_source_diagnostics(connection, {'provider_health': {'lever': health},
            'provider_alerts': [{'provider': 'lever', 'code': 'partial_results'}]}, '2026-09-28T00:00:00Z')
        history = diagnostics.list_source_diagnostics(connection)
        counts = history['items'][0]['counters']
        self.assertEqual(15, counts['coverage_rows_read'])
        self.assertEqual(1_000_000, counts['coverage_advertised_rows'])
        self.assertEqual(2, counts['query_mismatch'])
        self.assertEqual(1, counts['eligibility_unknown'])
        self.assertEqual(3, counts['unknown_location_limit'])
        self.assertNotIn('private', json.dumps(history))

    def test_six_cell_synthetic_controls_are_not_live_coverage(self):
        for role in ('software engineer', 'data engineer', 'enterprise architect'):
            for location in ('Dayton OH', 'US remote'):
                with self.subTest(role=role, location=location):
                    row = {'title': role.title(), 'location': 'Dayton, Ohio' if location == 'Dayton OH' else 'United States',
                           'remote': location == 'US remote'}
                    self.assertTrue(role_matches(row['title'], [role]))
                    self.assertTrue(detail_location_matches(row, [location]))
                    self.assertFalse(role_matches('Sales Manager', [role]))
                    self.assertFalse(detail_location_matches({'location': 'Daytona FL'}, [location]))
                    self.assertFalse(detail_location_matches({'location': ''}, [location]))


if __name__ == '__main__':
    unittest.main()
