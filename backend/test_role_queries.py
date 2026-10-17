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
    def test_generated_core_families_survive_adjacent_roles(self):
        evidence = 'Led enterprise data governance and responsible AI governance programs.'
        core = ['Enterprise Data Architect', 'Chief Data Officer', 'Enterprise Architect', 'AI Architect']
        response = {'titles': core, 'adjacent_titles': [
            {'title': 'Data Governance Manager', 'resume_evidence': evidence},
            {'title': 'AI Governance Manager', 'resume_evidence': evidence}]}
        with patch.object(searcher, 'generate_structured', return_value=response):
            titles = searcher.extract_search_keywords_from_resume(evidence, 'synthetic')
        self.assertEqual(['Data Architect', *core[1:], 'Data Governance Manager', 'AI Governance Manager'], titles)
        self.assertTrue(role_matches('Senior Manager - Data & AI Governance', titles))
        self.assertTrue(role_matches('Senior Data Architect', titles))
        self.assertFalse(role_matches('Chief Digital Officer', titles))
        self.assertFalse(role_matches('Marketing Manager', titles))

    def test_adjacent_roles_require_resume_evidence_across_occupations(self):
        for core, evidence, adjacent in (
            (['Registered Nurse', 'Nurse Educator'], 'Developed clinical training programs and supervised nursing staff.', 'Clinical Education Manager'),
            (['Accountant', 'Financial Analyst'], 'Led internal audit reviews and implemented financial controls.', 'Internal Auditor'),
        ):
            with self.subTest(core=core), patch.object(searcher, 'generate_structured', return_value={
                'titles': core, 'adjacent_titles': [
                    {'title': adjacent, 'resume_evidence': evidence},
                    {'title': 'Security Architect', 'resume_evidence': 'Designed secure cloud architecture for regulated systems.'}]}):
                self.assertEqual(core + [adjacent], searcher.extract_search_keywords_from_resume(evidence, 'synthetic'))
        with patch.object(searcher, 'generate_structured', return_value={
            'titles': ['Registered Nurse'], 'adjacent_titles': [{'title': 'Surgeon', 'resume_evidence': 'Nurse'}]}):
            self.assertEqual(['Registered Nurse'], searcher.extract_search_keywords_from_resume('Nurse', 'synthetic'))

    def test_core_priority_dedup_and_six_title_bound(self):
        core = ['Enterprise Data Architect', 'Data Architect', 'Chief Data Officer',
                'Enterprise Architect', 'AI Architect', 'Data Engineer', 'Analytics Engineer']
        evidence = 'Led enterprise data governance and responsible AI governance programs.'
        with patch.object(searcher, 'generate_structured', return_value={
            'titles': core, 'adjacent_titles': [{'title': 'Data Governance Manager', 'resume_evidence': evidence}]}):
            titles = searcher.extract_search_keywords_from_resume(evidence, 'synthetic')
        self.assertEqual(['Data Architect', *core[2:]], titles)
        self.assertEqual(6, len(titles))
        with self.assertRaises(ValueError):
            parse_manual_titles(';'.join(titles))

    def test_generated_plan_round_trips_within_cache_character_bound(self):
        from candidate_filters import resume_search_titles, MAX_SUGGESTED_KEYWORDS_CHARS
        from profile_repository import _suggested_keywords, MAX_SUGGESTED_KEYWORDS_CHARS as cache_limit
        self.assertEqual(cache_limit, MAX_SUGGESTED_KEYWORDS_CHARS)
        long_titles = ['Role ' + str(i) + ' ' + 'a' * 100 for i in range(6)]
        with patch.object(searcher, 'generate_structured', return_value={
            'titles': long_titles, 'adjacent_titles': []}):
            titles = searcher.extract_search_keywords_from_resume('Synthetic', 'synthetic')
        self.assertEqual(long_titles[:4], titles)
        cached = _suggested_keywords(','.join(titles))
        self.assertEqual(titles, resume_search_titles(cached.split(',')))
        self.assertEqual(['Data Architect'], resume_search_titles(['Lead, Data Architect', 'Data Architect']))

    def test_six_roles_survive_cache_preview_execution_and_report(self):
        from search_review import role_filter, sanitize_review
        titles = ['Data Architect', 'Chief Data Officer', 'Enterprise Architect',
                  'AI Architect', 'Data Governance Manager', 'AI Governance Manager']
        class StopAfterPlan(Exception):
            pass
        profile = Mock()
        profile.get_profile.return_value = {'base_resume_text': 'Synthetic resume', 'suggested_keywords': ','.join(titles)}
        with patch.object(app_module, 'runtime_profile_repository', return_value=profile):
            plan = app_module.preview_search_titles(app_module.SearchRequest(keywords=''))
        self.assertEqual(titles, plan['titles'])
        self.assertEqual(6, plan['max_titles'])
        review = sanitize_review({'role_filter': role_filter(titles, 'resume_suggested')})
        self.assertEqual(titles, review['role_filter']['titles'])
        with patch.object(searcher, 'settings_from_profile', return_value=AIProviderSettings('gemini', 'synthetic', '')), \
             patch.object(searcher.config, 'get_discovery_mode', return_value='hybrid_feeds'), \
             patch('public_job_feeds.collect', return_value=[]) as public, \
             patch('additional_sources.collect', return_value=[]) as additional, \
             patch('discovery_sources.collect', side_effect=StopAfterPlan) as collect:
            with self.assertRaises(StopAfterPlan):
                searcher.run_job_search_and_matching('', 'dayton, oh; remote', profile_repository=profile, job_repository=Mock())
        for call in (public, additional, collect):
            self.assertEqual(titles, call.call_args.args[0])

    def test_catalog_and_snapshot_filters_include_the_sixth_role(self):
        import discovery_sources as sources
        import public_job_feeds as feeds
        import additional_sources as additional
        titles = ['Data Architect', 'Chief Data Officer', 'Enterprise Architect',
                  'AI Architect', 'Data Governance Manager', 'AI Governance Manager']
        row = {'title': 'Senior AI Governance Manager', 'location': 'Remote',
               'work_arrangement': 'remote', 'source': 'jobicy',
               'url': 'https://jobicy.com/jobs/123-synthetic'}
        with patch.object(feeds, 'PROVIDERS', ('jobicy',)), patch.object(feeds, '_snapshot', return_value=[row]):
            self.assertEqual(1, len(feeds.collect(titles, ['remote'], {}, lambda: None)))
        with patch.object(sources, 'CATALOG', (('lever', 'synthetic'),)), \
             patch.object(sources, 'feed_rows', return_value=sources.FeedRows([
                 {**row, 'url': 'https://jobs.lever.co/synthetic/12345678-1234-1234-1234-123456789abc',
                  'employment_type': 'Full-time'}], complete=True)), \
             patch.object(sources, 'feed_candidate', side_effect=lambda provider, board, value: value):
            self.assertEqual(1, len(sources.collect(titles, ['remote'], {'lever': {}}, lambda: None, feeds_only=True)))
        with patch.object(additional, 'ENABLED', ('udayton',)), \
             patch.object(additional, '_request', return_value=b'synthetic'), \
             patch.object(additional, 'parse_ud_listing', return_value=([row], True)):
            self.assertEqual(1, len(additional.collect(titles, ['remote'], {'udayton': {}}, lambda: None)))

    def test_resume_families_keep_related_titles_and_reject_other_occupations(self):
        from candidate_filters import resume_search_titles
        titles = resume_search_titles(['Enterprise Data Architect', 'Chief Data Officer', 'Generative AI Architect'])
        self.assertEqual(['Data Architect', 'Chief Data Officer', 'AI Architect'], titles)
        for title in ('Lead, Data Architect', 'Senior Data Architect (Req #1427)',
                      'Data Architect', 'AI Architect', 'AI Technical Architect',
                      'Lead AI Architect/Strategist', 'Architect, GenAI',
                      'Artificial Intelligence Architect'):
            with self.subTest(title=title):
                self.assertTrue(role_matches(title, titles))
        for title in ('Chief Digital Officer', 'Chief Development Officer',
                      'Deputy Chief Compliance Officer', 'Solutions Architect',
                      'Enterprise Architect', 'AI Trainer', 'Data Engineer',
                      'Senior Product Marketing Manager, Data Intelligence'):
            with self.subTest(title=title):
                self.assertFalse(role_matches(title, titles))
        self.assertFalse(role_matches('Data Architect', ['Enterprise Data Architect']))
        self.assertFalse(role_matches('AI Architect', ['Generative AI Architect']))
        self.assertFalse(detail_location_matches({'title': 'AI Architect', 'location': 'Canada',
                                                  'description': 'No remote work.'}, ['Dayton OH', 'remote']))

    def test_suggested_normalization_is_bounded_idempotent_and_preserves_scope(self):
        from candidate_filters import resume_search_titles
        self.assertEqual(['Data Architect', 'AI Architect', 'Chief Data Officer'], resume_search_titles([
            'Senior Enterprise Data Architect', 'Data Architect', 'GenAI Architect', 'Chief Data Officer']))
        unchanged = ['Chief Data Officer', 'Director of Data Architecture', 'Security Architect']
        self.assertEqual(unchanged, resume_search_titles(unchanged))
        self.assertEqual([], resume_search_titles([None, '', '!', 'x' * 1000]))
        titles = resume_search_titles(['Principal Data Architect', 'Artificial Intelligence Architect'])
        self.assertEqual(titles, resume_search_titles(titles))

    def test_cached_and_new_suggestions_use_same_preview_and_execution_plan(self):
        class StopAfterPlan(Exception):
            pass
        suggested = ['Enterprise Data Architect', 'Chief Data Officer', 'Generative AI Architect']
        expected = ['Data Architect', 'Chief Data Officer', 'AI Architect']
        profile = Mock()
        for cached in (','.join(suggested), ''):
            profile.get_profile.return_value = {'base_resume_text': 'Synthetic resume', 'suggested_keywords': cached}
            with patch.object(searcher, 'settings_from_profile', return_value=AIProviderSettings('gemini', 'synthetic', '')), \
                 patch.object(searcher.config, 'get_discovery_mode', return_value='hybrid_feeds'), \
                 patch('additional_sources.collect', return_value=[]), \
                 patch('public_job_feeds.collect', return_value=[]), \
                 patch('discovery_sources.collect', side_effect=StopAfterPlan) as collect, \
                 patch.object(searcher, 'extract_search_keywords_from_resume', return_value=suggested) as generate:
                with self.assertRaises(StopAfterPlan):
                    searcher.run_job_search_and_matching('', 'dayton, oh; remote',
                                                        profile_repository=profile, job_repository=Mock())
                self.assertEqual(expected, collect.call_args.args[0])
                self.assertEqual(['dayton, oh', 'remote'], collect.call_args.args[1])
                self.assertEqual(0 if cached else 1, generate.call_count)
        profile.get_profile.return_value['suggested_keywords'] = ','.join(suggested)
        with patch.object(app_module, 'runtime_profile_repository', return_value=profile):
            self.assertEqual(expected, app_module.preview_search_titles(app_module.SearchRequest(keywords=''))['titles'])

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
        self.assertEqual(3, MAX_TITLE_ALTERNATIVES)
        self.assertEqual(6, discovery_sources.MAX_KEYWORDS)
        self.assertEqual(discovery_sources.MAX_TERM_CHARS, MAX_TITLE_CHARS)


if __name__ == '__main__':
    unittest.main()
