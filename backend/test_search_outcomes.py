"""Synthetic discovery response and completion outcome regressions."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import HTTPError

import searcher
from operations import OperationCancelled


class SearchOutcomeTests(unittest.TestCase):
    @staticmethod
    def health():
        return {"lever": {
            "raw_candidates": 0, "valid_discovered": 0, "new_candidates": 0,
            "accepted": 0, "rejected": 0, "errors": [],
            "skipped_active": 0, "skipped_archived": 0, "skipped_suppressed": 0,
            "partial_results": {"oversized_responses": 0, "candidate_limit_hits": 0, "timeouts": 0},
            "rejection_reasons": {"stale": 0, "format_drift": 0, "access_challenge": 0,
                                  "embedded_content": 0, "provider_error": 0},
        }}

    def discover(self, responses):
        health = self.health()
        output = io.StringIO()
        with (
            patch.object(searcher, "PROVIDER_DOMAINS", {"lever": "lever.co"}),
            patch.object(searcher.urllib.request, "urlopen", side_effect=responses),
            redirect_stdout(output),
        ):
            results = searcher.search_yahoo_jobs("private query", diagnostics=health)
        self.assertNotIn("private query", output.getvalue())
        return results, health

    @staticmethod
    def response(markup):
        return io.BytesIO(markup.encode())

    def test_unrelated_malformed_and_challenge_pages_are_not_zero_results(self):
        for markup in (
            '<li class="b_algo"><a href="https://example.org/">Reference</a></li>',
            '<html><p>Unexpected response',
            '<html><p>Verify you are human</p></html>',
            '<script>no results found</script>',
        ):
            with self.subTest(markup=markup):
                results, health = self.discover([self.response(markup), self.response(markup)])
                self.assertEqual([], results)
                self.assertEqual(2, health["lever"]["source_unusable"])
                result = searcher._search_completion(health, 0, 0, 0, 0)
                self.assertFalse(result["success"])
                self.assertEqual("source_unavailable", result["outcome"])

    def test_explicit_zero_is_valid_without_fallback(self):
        results, health = self.discover([self.response('<div class="compNoResults">No results found</div>')])
        self.assertEqual([], results)
        self.assertEqual(1, health["lever"]["source_empty"])
        result = searcher._search_completion(health, 0, 0, 0, 0)
        self.assertTrue(result["success"])
        self.assertEqual("no_results", result["outcome"])

    def test_no_results_marker_does_not_hide_unrelated_result_blocks(self):
        markup = '<div class="b_no">No results found</div><li class="b_algo"><a href="https://example.org">Other</a></li>'
        _, health = self.discover([self.response(markup), self.response(markup)])
        self.assertEqual(1, health["lever"]["source_failures"])

    def test_fallback_recovery_preserves_notes_and_accepted_domain(self):
        error = HTTPError("https://search.yahoo.com", 500, "private response", None, None)
        markup = "<a href='https://jobs.lever.co/acme/posting-123'>Role</a><a href='https://jobs.lever.co.evil.test/acme/123'>Bad</a>"
        results, health = self.discover([error, self.response(markup)])
        self.assertEqual(["https://jobs.lever.co/acme/posting-123"], [row["url"] for row in results])
        self.assertEqual(1, health["lever"]["source_fallbacks"])
        self.assertEqual(2, health["lever"]["source_attempts"])
        self.assertIn("partial_results", [a["code"] for a in searcher.provider_alerts_from_health(health)])

    def test_complete_outage_and_timeout_are_failures(self):
        for error in (TimeoutError(), HTTPError("https://search.yahoo.com", 500, "error", None, None)):
            with self.subTest(error=type(error).__name__):
                _, health = self.discover([error, error])
                result = searcher._search_completion(health, 0, 0, 0, 0)
                self.assertEqual("source_unavailable", result["failure_code"])

    def test_cancellation_is_not_retried_as_source_failure(self):
        with self.assertRaises(OperationCancelled):
            self.discover([OperationCancelled()])

    def test_known_stale_suppressed_and_low_scored_are_no_new_matches(self):
        health = self.health()
        health["lever"].update(skipped_active=1, skipped_archived=1, skipped_suppressed=1, accepted=1)
        health["lever"]["rejection_reasons"]["stale"] = 1
        result = searcher._search_completion(health, 5, 5, 1, 0)
        self.assertEqual("no_new_matches", result["outcome"])
        self.assertEqual(2, result["counts"]["known"])
        self.assertEqual(3, result["counts"]["filtered"])

    def test_all_detail_failures_are_unavailable_not_filtered(self):
        health = self.health()
        health["lever"]["rejection_reasons"]["provider_error"] = 1
        result = searcher._search_completion(health, 1, 1, 0, 0)
        self.assertEqual("source_unavailable", result["outcome"])
        self.assertEqual(0, result["counts"]["filtered"])

    def test_partial_search_includes_zero_saved_with_valid_source_zero(self):
        health = self.health()
        health["lever"].update(source_failures=1, source_empty=1)
        result = searcher._search_completion(health, 0, 0, 0, 0)
        self.assertTrue(result["success"])
        self.assertTrue(result["partial"])
        self.assertEqual("partial_results", result["outcome"])

    def test_saved_matches_and_partial_ai_failure(self):
        health = self.health()
        health["lever"]["accepted"] = 2
        result = searcher._search_completion(health, 2, 2, 2, 2)
        self.assertEqual("matches_saved", result["outcome"])
        failed = searcher._search_completion(health, 2, 2, 1, 1, "Provider unavailable", "timeout")
        self.assertTrue(failed["success"])
        self.assertEqual("partial_results", failed["outcome"])
        self.assertEqual(1, failed["jobs_added"])

    def test_counters_are_bounded(self):
        health = self.health()
        health["lever"]["source_attempts"] = 100_000
        searcher._source_counter(health["lever"], "source_attempts")
        self.assertEqual(100_000, health["lever"]["source_attempts"])
        result = searcher._search_completion(health, 1_000_000, 0, 0, 0)
        self.assertEqual(100_000, result["counts"]["discovered"])

    def test_candidate_budget_is_partial_even_without_response_truncation(self):
        health = self.health()
        health["lever"].update(candidate_budget_exhausted=True, accepted=1)
        result = searcher._search_completion(health, 1, 1, 1, 1)
        self.assertEqual("partial_results", result["outcome"])

    def test_failed_saves_are_failures_not_filtered_postings(self):
        health = self.health()
        health["lever"]["accepted"] = 2
        result = searcher._search_completion(health, 2, 2, 2, 0, save_failures=2)
        self.assertFalse(result["success"])
        self.assertEqual("storage_error", result["failure_code"])
        self.assertEqual(0, result["counts"]["filtered"])
        partial = searcher._search_completion(health, 2, 2, 2, 1, save_failures=1)
        self.assertTrue(partial["success"])
        self.assertEqual("partial_results", partial["outcome"])
        self.assertEqual(0, partial["counts"]["filtered"])


if __name__ == "__main__":
    unittest.main()
