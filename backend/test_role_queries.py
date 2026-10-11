"""Synthetic title planning, admission and worker contract tests; no paid calls."""
import unittest
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from pydantic import ValidationError
import app as app_module
import searcher
from ai_providers import AIProviderSettings
from candidate_filters import role_matches, detail_location_matches
from role_queries import (parse_manual_titles, title_plan, encode_titles, execution_titles,
                          display_saved_search, TITLE_LIST_PREFIX, MAX_TITLE_ALTERNATIVES, MAX_TITLE_CHARS)


class RoleQueryTests(unittest.TestCase):
    def test_explicit_list_normalization_and_legacy_fallback(self):
        self.assertEqual(['Data Engineer', 'Software Engineer - Data'],
                         parse_manual_titles(' Data  Engineer ; Software Engineer - Data; data engineer '))
        self.assertEqual(['C++ Engineer, Platform'], parse_manual_titles('C++ Engineer, Platform'))
        for text in ('', '  ', 'undefined', 'UNDEFINED'):
            self.assertEqual([], parse_manual_titles(text))
        self.assertEqual(['a' * 120], parse_manual_titles('a' * 120))

    def test_invalid_lists_never_silently_truncate_or_infer(self):
        for text in (';', 'role;', ';role', 'a;;b', 'a;b;c;d', 'a' * 121, 'a\nb', 'a\x00b', '!!!'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_manual_titles(text)
            with self.assertRaises(ValidationError):
                app_module.SearchRequest(keywords=text, title_alternatives=True)

    def test_preview_is_pure_and_admission_preserves_saved_and_queued_text(self):
        text = 'Data Engineer; Software Engineer - Data'
        encoded = encode_titles(text, True)
        req = app_module.SearchRequest(keywords=text, location='Dayton OH', title_alternatives=True)
        with patch.object(app_module, 'runtime_search_run_repository', side_effect=AssertionError('No queue in preview')):
            self.assertEqual(title_plan(text, True), app_module.preview_search_titles(req))
        for options in ({}, {'save_search': True}, {'saved_search_id': 7}):
            repository, saved, background = Mock(), Mock(), Mock()
            repository.enqueue.return_value = 'synthetic-run'
            saved.upsert_saved_search.return_value = {'keywords': encoded, 'location': 'Dayton OH'}
            saved.advance_saved_search_run.return_value = {'id': 7}
            with patch.object(app_module, 'data_store_kind', return_value=app_module.LOCAL_DATA_STORE), \
                 patch.object(app_module, 'runtime_search_run_repository', return_value=repository), \
                 patch.object(app_module, 'runtime_discovery_repository', return_value=saved), \
                 patch.object(app_module, 'capture_event'):
                result = app_module.search_jobs(app_module.SearchRequest(keywords=text, location='Dayton OH', title_alternatives=True, **options),
                                               background, Mock(), operation_id=None)
            self.assertTrue(result['success'])
            repository.enqueue.assert_called_once_with(None, encoded, 'Dayton OH')
            repository.publish_prepared.assert_called_once_with('synthetic-run', encoded, 'Dayton OH')

    def test_legacy_and_versioned_saved_keywords_are_distinct(self):
        for text in ('A;B', 'A;B;C;D', 'A;', '', 'undefined'):
            self.assertEqual(text, encode_titles(text))
            self.assertEqual([] if text in ('', 'undefined') else [text], execution_titles(text))
            self.assertFalse(display_saved_search({'keywords': text})['title_alternatives'])
            app_module.SearchRequest(keywords=text)
        encoded = encode_titles('Data Engineer; Software-Engineer', True)
        display = display_saved_search({'keywords': encoded, 'id': 7})
        self.assertTrue(display['title_alternatives'])
        self.assertEqual(encoded, encode_titles(display['keywords'], display['title_alternatives']))
        self.assertTrue(title_plan('Software-Engineer', True)['software_alias'])
        self.assertEqual('invalid', title_plan('!!!')['mode'])
        with self.assertRaises(ValueError):
            encode_titles(' ' + encoded)
        with self.assertRaises(ValueError):
            encode_titles(';'.join(letter + '\\' * 119 for letter in ('a', 'b', 'c')), True)
        for corrupt in ('{}', '[]', '["a;b"]', '[1]', '["a","b","c","d"]'):
            with self.assertRaises(ValueError):
                execution_titles(TITLE_LIST_PREFIX + corrupt)

    def test_worker_uses_one_bounded_collection_with_all_alternatives(self):
        class StopAfterPlan(Exception):
            pass
        profile = Mock()
        profile.get_profile.return_value = {'base_resume_text': 'Synthetic resume', 'suggested_keywords': ''}
        with patch.object(searcher, 'settings_from_profile', return_value=AIProviderSettings('gemini', 'synthetic', '')), \
             patch.object(searcher.config, 'get_discovery_mode', return_value='hybrid_feeds'), \
             patch('additional_sources.collect', return_value=[]), patch('public_job_feeds.collect', return_value=[]), \
             patch('discovery_sources.collect', side_effect=StopAfterPlan) as collect, \
             patch.object(searcher, 'extract_search_keywords_from_resume', side_effect=AssertionError('No AI inference')):
            with self.assertRaises(StopAfterPlan):
                searcher.run_job_search_and_matching(encode_titles('Data Engineer; Software Engineer - Data', True), 'Dayton OH',
                                                    profile_repository=profile, job_repository=Mock())
            self.assertEqual(['Data Engineer', 'Software Engineer - Data'], collect.call_args.args[0])
            self.assertEqual(['Dayton OH'], collect.call_args.args[1])
            self.assertEqual(1, collect.call_count)
            collect.reset_mock()
            result = searcher.run_job_search_and_matching(TITLE_LIST_PREFIX + '["a","b","c","d"]', 'Dayton OH',
                                                         profile_repository=profile, job_repository=Mock())
            self.assertEqual('keywords_unavailable', result['failure_code'])
            collect.assert_not_called()

    def test_saved_due_queue_claim_round_trip_keeps_explicit_mode(self):
        import database
        from discovery_repository import SQLiteDiscoveryRepository
        from search_run_repository import SQLiteSearchRunRepository
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / 'synthetic.db'
            with patch.object(database, 'DB_PATH', database_path):
                database.init_db()
            def connection():
                result = sqlite3.connect(database_path)
                result.row_factory = sqlite3.Row
                return result
            saved = SQLiteDiscoveryRepository(connection)
            queue = SQLiteSearchRunRepository(1, connection)
            now = datetime.now(timezone.utc)
            for index, encoded in enumerate(('A;B', encode_titles('Data Engineer; Software Engineer', True))):
                saved.upsert_saved_search(name=f'Synthetic {index}', keywords=encoded, location='Dayton OH',
                                          schedule_frequency='daily', run_at=now - timedelta(days=2))
            due = saved.list_due_saved_searches(now)
            self.assertEqual(2, len(due))
            for row in due:
                run = queue.enqueue(None, row['keywords'], row['location'])
                queue.publish_prepared(run, row['keywords'], row['location'])
                claim = queue.claim(run, '10000000-0000-4000-8000-000000000011')
                self.assertEqual(execution_titles(row['keywords']), execution_titles(claim.keywords))
                view = display_saved_search(row)
                self.assertEqual(claim.keywords, encode_titles(view['keywords'], view['title_alternatives']))
                queue.succeed(claim, {'success': True, 'jobs_added': 0})

    def test_title_or_geography_and_budget_contract(self):
        import discovery_sources
        titles = parse_manual_titles('Data Engineer; Software Engineer - Data')
        self.assertTrue(role_matches('Senior Software Engineer - Data', titles))
        self.assertFalse(role_matches('Sales Manager', titles))
        self.assertFalse(detail_location_matches({'location': 'Canada', 'remote': True}, ['US remote']))
        self.assertEqual(discovery_sources.MAX_KEYWORDS, MAX_TITLE_ALTERNATIVES)
        self.assertEqual(discovery_sources.MAX_TERM_CHARS, MAX_TITLE_CHARS)


if __name__ == '__main__':
    unittest.main()
