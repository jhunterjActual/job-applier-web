"""Synthetic search runs: resolved identities are checked before AI scoring."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

import searcher
from job_suppressions import job_url_fingerprint


class PostRedirectSearchTests(unittest.TestCase):
    first = 'https://jobs.lever.co/synthetic/alias-one'
    second = 'https://jobs.lever.co/synthetic/alias-two'
    resolved = 'https://jobs.lever.co/synthetic/posting'

    def run_search(self, targets, *, known=None, suppressed=None, batch_size=10,
                   statuses=None, verify_query=False, role_results=None):
        repository = Mock()
        context = {'known_status_by_url': known or {},
                   'suppressed_fingerprints': suppressed or set()}
        original = deepcopy(context)
        repository.search_catalog_context.return_value = context
        repository.create_job_if_available.return_value = {'outcome': 'created'}
        candidates = [{'url': url, 'verify_query': verify_query}
                      for url in (self.first, self.second)[:len(targets)]]
        details = [{'url': target, 'title': 'Architect', 'company': 'Synthetic',
                    'description': 'Synthetic architecture role.', 'location': 'Dayton, OH',
                    'employment_type': 'full_time', 'source': 'lever'} for target in targets]
        statuses = statuses or ['ok'] * len(targets)
        outcomes = [{'status': status, 'details': row if status == 'ok' else None}
                    for status, row in zip(statuses, details)]
        originals = deepcopy(outcomes)
        score_batches = []

        def score(resume, batch, *args, **kwargs):
            score_batches.append(deepcopy(batch))
            return {'success': True, 'matches': [
                {'index': index, 'match_score': 90, 'analysis': 'Synthetic match'}
                for index in range(len(batch))]}

        with (
            patch.object(searcher.config, 'get_discovery_mode', return_value='legacy'),
            patch.object(searcher, 'search_yahoo_jobs', return_value=candidates),
            patch.object(searcher, 'settings_from_profile', return_value=Mock(
                api_key='synthetic', provider='gemini', model='synthetic')),
            patch.object(searcher, 'inspect_job_posting', side_effect=outcomes) as inspect,
            patch.object(searcher, 'analyze_job_matches_batch', side_effect=score),
            patch.object(searcher, 'MATCH_BATCH_SIZE', batch_size),
            patch('candidate_filters.role_matches', side_effect=role_results or [True] * len(targets)),
            patch('candidate_filters.detail_location_matches', return_value=True),
            patch('builtins.print'),
        ):
            result = searcher.run_job_search_and_matching('Architect', job_repository=repository,
                profile_repository=Mock(get_profile=Mock(return_value={'base_resume_text': 'Synthetic'})))
        self.assertEqual(original, context)
        self.assertEqual(originals, outcomes)
        return result, score_batches, repository, inspect

    def test_aliases_resolving_to_one_posting_are_scored_and_saved_once(self):
        for batch_size in (1, 10):
            with self.subTest(batch_size=batch_size):
                result, batches, repository, inspect = self.run_search(
                    [self.resolved, self.resolved + '?utm_source=other'], batch_size=batch_size)
                self.assertEqual(2, inspect.call_count)
                self.assertEqual(1, sum(len(batch) for batch in batches))
                repository.create_job_if_available.assert_called_once()
                self.assertEqual(1, result['jobs_added'])
                self.assertEqual(1, result['provider_health']['lever']['coverage_duplicate_candidates'])

    def test_resolved_saved_and_archived_jobs_never_reach_scoring(self):
        for status, counter in [('new', 'skipped_active'), ('archived', 'skipped_archived')]:
            with self.subTest(status=status):
                result, batches, repository, inspect = self.run_search(
                    [self.resolved], known={self.resolved: status})
                self.assertEqual(1, inspect.call_count)
                self.assertEqual([], batches)
                repository.create_job_if_available.assert_not_called()
                self.assertEqual(1, result['provider_health']['lever'][counter])

    def test_resolved_deleted_job_never_reaches_scoring(self):
        result, batches, repository, _ = self.run_search(
            [self.resolved + '?utm_source=other'], suppressed={job_url_fingerprint(self.resolved)})
        self.assertEqual([], batches)
        repository.create_job_if_available.assert_not_called()
        self.assertEqual(1, result['provider_health']['lever']['skipped_suppressed'])

    def test_different_postings_with_same_title_and_employer_remain_distinct(self):
        result, batches, repository, _ = self.run_search([self.resolved, self.resolved + '-reposted'])
        self.assertEqual(2, sum(len(batch) for batch in batches))
        self.assertEqual(2, repository.create_job_if_available.call_count)
        self.assertEqual(2, result['jobs_added'])
        self.assertEqual('Dayton, OH', batches[0][0]['location'])
        self.assertEqual('full_time', batches[0][0]['employment_type'])

    def test_role_rejection_does_not_reserve_scoring_identity(self):
        result, batches, _, _ = self.run_search([self.resolved, self.resolved],
            verify_query=True, role_results=[False, True])
        self.assertEqual(1, sum(len(batch) for batch in batches))
        self.assertEqual(1, result['jobs_added'])
        self.assertEqual(1, result['provider_health']['lever']['rejection_reasons']['query_mismatch'])

    def test_timeout_does_not_suppress_a_later_valid_posting(self):
        result, batches, _, _ = self.run_search([self.resolved, self.resolved], statuses=['timeout', 'ok'])
        self.assertEqual(1, sum(len(batch) for batch in batches))
        self.assertEqual(1, result['jobs_added'])

    def test_stale_posting_never_reaches_scoring_or_saved_history(self):
        result, batches, repository, _ = self.run_search([self.resolved], statuses=['stale'])
        self.assertEqual([], batches)
        repository.create_job_if_available.assert_not_called()
        self.assertEqual(0, result['jobs_added'])
        self.assertEqual(1, result['provider_health']['lever']['rejection_reasons']['stale'])

    def test_scoring_identity_is_not_shared_across_runs_or_owners(self):
        for _ in range(2):
            result, batches, _, _ = self.run_search([self.resolved])
            self.assertEqual(1, sum(len(batch) for batch in batches))
            self.assertEqual(1, result['jobs_added'])

    def test_original_known_url_still_skips_detail_fetch(self):
        result, batches, repository, inspect = self.run_search([self.resolved], known={self.first: 'archived'})
        inspect.assert_not_called()
        self.assertEqual([], batches)
        repository.create_job_if_available.assert_not_called()
        self.assertEqual(1, result['provider_health']['lever']['skipped_archived'])


if __name__ == '__main__':
    unittest.main()
