"""Offline source contracts and paid-request isolation."""
import io
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import discovery_sources as sources
import searcher
from operations import OperationCancelled


class SuspensionDefaultTests(unittest.TestCase):
    def test_production_default_is_disabled(self):
        self.assertIs(sources.SERPER_ENABLED, False)


class DiscoverySourceTests(unittest.TestCase):
    def setUp(self):
        # Preserve offline adapter contract checks for the deferred proposal.
        # Production has no environment switch for this code-level suspension.
        enabled = patch.object(sources, "SERPER_ENABLED", True)
        enabled.start()
        self.addCleanup(enabled.stop)

    def test_suspension_rejects_transport_before_reservation_or_network(self):
        reserve = Mock(return_value=True)
        with patch.object(sources, "SERPER_ENABLED", False), patch.object(sources.urllib.request, "build_opener") as opener:
            for key in (None, "synthetic"):
                with self.assertRaisesRegex(sources.SourceError, "serper_disabled"):
                    sources.fetch_json("https://google.serper.dev/search", sources.Budget(lambda: None),
                                       api_key=key, reserve=reserve)
            reserve.assert_not_called()
            opener.assert_not_called()

    def test_environment_cannot_enable_serper(self):
        with patch.dict(os.environ, {"CAREERTRELLIS_DISCOVERY_MODE": "hybrid_serper",
                                   "CAREERTRELLIS_SERPER_API_KEY": "synthetic"}):
            self.assertEqual("", searcher.config.get_serper_api_key())
            with self.assertRaises(searcher.config.RuntimeConfigurationError):
                searcher.config.get_discovery_mode()

    def test_trial_refuses_serper_before_prompt_or_feed_request(self):
        import probe_discovery
        from contextlib import redirect_stderr
        with patch("sys.argv", ["probe_discovery.py", "--serper"]), redirect_stderr(io.StringIO()), \
                patch.object(probe_discovery, "feed_rows") as feeds:
            with self.assertRaises(SystemExit):
                probe_discovery.main()
            feeds.assert_not_called()

    def health(self):
        return {provider: {"partial_results": {"candidate_limit_hits": 0}, "errors": []}
                for provider in searcher.PROVIDER_DOMAINS}

    def test_terms_deduplicate_and_bound_all_sources(self):
        result, limited = sources.bounded_terms(["Data Engineer", "data engineer", "Analyst", "Director", "Extra", "x" * 121], 3)
        self.assertEqual(["Data Engineer", "Analyst", "Director"], result)
        self.assertTrue(limited)

    def test_lever_secondary_locations_are_eligible_and_malformed_lists_refuse(self):
        row = {"text": "Data Engineer", "hostedUrl": "https://jobs.lever.co/tekton/posting",
               "categories": {"location": "New York", "allLocations": ["New York", "Ohio"]}}
        candidate = sources.feed_candidate("lever", "tekton", row)
        self.assertTrue(sources.matches(candidate, ["Data Engineer"], ["Ohio"]))
        self.assertFalse(sources.matches(candidate, ["Data Engineer"], ["California"]))
        for invalid in ("Ohio", [None], {"name": "Ohio"}):
            with self.subTest(invalid=invalid), self.assertRaises(sources.SourceError):
                sources.feed_candidate("lever", "tekton", {**row,
                    "categories": {"location": "New York", "allLocations": invalid}})

    def test_feeds_only_never_attempts_or_reports_managed_search(self):
        health = self.health()
        with patch.object(sources, "SERPER_ENABLED", False), patch.object(sources, "feed_rows", return_value=[]) as feeds, \
                patch.object(sources, "fetch_json", side_effect=AssertionError("Unexpected managed transport")):
            self.assertEqual([], sources.collect(["Engineer"], ["remote"], health, lambda: None, feeds_only=True))
        self.assertEqual(3, feeds.call_count)
        self.assertEqual(3, sum(row.get("source_attempts", 0) for row in health.values()))
        self.assertEqual(0, sum(row.get("source_failures", 0) for row in health.values()))

    def test_feed_failure_preserves_other_feed_results(self):
        health = self.health()
        def rows(provider, board, budget):
            if provider == "lever":
                raise sources.SourceError("http_error")
            if provider == "greenhouse":
                return [{"title": "Engineer", "absolute_url": "https://boards.greenhouse.io/clear/jobs/12345",
                         "location": {"name": "remote"}}]
            return []
        with patch.object(sources, "feed_rows", side_effect=rows):
            found = sources.collect(["Engineer"], ["remote"], health, lambda: None, feeds_only=True)
        self.assertEqual(["https://boards.greenhouse.io/clear/jobs/12345"], [row["url"] for row in found])
        self.assertEqual(1, health["lever"]["source_failures"])
        self.assertEqual(1, health["greenhouse"]["valid_discovered"])
        self.assertEqual(1, health["ashby"]["source_empty"])

    def test_key_header_only_and_reservation_before_transport(self):
        events = []
        opener = Mock()
        def open_request(request, timeout):
            events.append("transport")
            self.assertEqual("private-key", request.get_header("X-api-key"))
            self.assertNotIn("private-key", request.full_url)
            self.assertNotIn("private-key", request.data.decode())
            self.assertLessEqual(timeout, 12)
            return io.BytesIO(b'{"organic": []}')
        opener.open.side_effect = open_request
        with patch.object(sources.urllib.request, "build_opener", return_value=opener):
            result = sources.fetch_json("https://google.serper.dev/search", sources.Budget(lambda: None),
                                        api_key="private-key", payload={"q": "synthetic"},
                                        reserve=lambda: events.append("reserve") or True)
        self.assertEqual(["reserve", "transport"], events)
        self.assertEqual([], result["organic"])

    def test_denied_or_missing_budget_never_transports(self):
        for reserve in (None, lambda: False):
            with patch.object(sources.urllib.request, "build_opener") as opener:
                with self.assertRaises(sources.SourceError):
                    sources.fetch_json("https://google.serper.dev/search", sources.Budget(lambda: None),
                                       api_key="private", payload={}, reserve=reserve)
                opener.assert_not_called()

    def test_errors_are_fixed_and_never_retried(self):
        for code in (401, 403, 429, 500):
            opener = Mock()
            opener.open.side_effect = HTTPError("private-url", code, "private-body", {"Retry-After": "60"}, None)
            with patch.object(sources.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(sources.SourceError) as error:
                    sources.fetch_json("https://google.serper.dev/search", sources.Budget(lambda: None),
                                       api_key="private", payload={}, reserve=lambda: True)
            self.assertNotIn("private", str(error.exception))
            self.assertEqual(1, opener.open.call_count)

    def test_oversized_malformed_and_cancelled_responses(self):
        for payload in (b'x' * (sources.MAX_BYTES + 1), b'not json'):
            opener = Mock(); opener.open.return_value = io.BytesIO(payload)
            with patch.object(sources.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(sources.SourceError):
                    sources.fetch_json("https://api.lever.co", sources.Budget(lambda: None))
        with self.assertRaises(OperationCancelled):
            sources.Budget(Mock(side_effect=OperationCancelled())).take()

    def test_all_redirects_rejected_including_credential_origin_change(self):
        with self.assertRaises(sources.SourceError):
            sources.NoRedirect().redirect_request(None, None, 302, "", {}, "http://127.0.0.1")

    def test_feed_board_and_visibility_contracts(self):
        self.assertIsNone(sources.feed_candidate("ashby", "coder", {"isListed": False}))
        lever = {"text": "Data Engineer", "hostedUrl": "https://jobs.lever.co/tekton/posting", "categories": {"location": "Remote"}}
        self.assertTrue(sources.matches(sources.feed_candidate("lever", "tekton", lever), ["Data Engineer"], ["Remote"]))
        for url in ("https://jobs.lever.co/other/posting", "https://evil.test/tekton/posting", "https://jobs.lever.co"):
            with self.assertRaises(sources.SourceError):
                sources.feed_candidate("lever", "tekton", dict(lever, hostedUrl=url))

    def test_lever_pagination_limit_is_not_an_empty_success(self):
        with patch.object(sources, "fetch_json", return_value=[{}] * 100) as fetch:
            with self.assertRaises(sources.SourceError):
                sources.feed_rows("lever", "tekton", sources.Budget(lambda: None))
        self.assertEqual(3, fetch.call_count)

    def test_feed_once_per_run_and_serper_circuit_breaker(self):
        health = self.health()
        with patch.object(sources, "feed_rows", return_value=[]) as feeds, patch.object(sources, "fetch_json", side_effect=sources.SourceError("rate_limit")) as paid:
            results = sources.collect(["Engineer", "Analyst"], ["Remote", "Ohio"], health, lambda: None,
                                      api_key="private", reserve=lambda: True)
        self.assertEqual([], results)
        self.assertEqual(3, feeds.call_count)
        self.assertEqual(1, paid.call_count)
        self.assertTrue(all(value["source_failures"] == 1 for value in health.values()))

    def test_managed_results_validate_and_deduplicate(self):
        health = self.health()
        url = "https://jobs.lever.co/tekton/posting"
        with patch.object(sources, "feed_rows", return_value=[]), patch.object(sources, "fetch_json", return_value={"organic": [{"link": url}, {"link": url}]}):
            results = sources.collect(["Engineer"], [""], health, lambda: None, api_key="private", reserve=lambda: True)
        self.assertEqual([url], [row["url"] for row in results])

    def test_missing_keyword_key_never_invents_a_role(self):
        with patch.object(searcher.config, "get_gemini_api_key", return_value=""):
            self.assertEqual([], searcher.extract_search_keywords_from_resume("synthetic", ""))

    def test_hybrid_pipeline_keeps_owner_repository_and_match_save_flow(self):
        self.check_hybrid_pipeline(True, True)

    def test_hybrid_saves_legacy_results_when_feed_fails(self):
        self.check_hybrid_pipeline(False, True)

    def test_hybrid_saves_feed_results_when_legacy_fails(self):
        self.check_hybrid_pipeline(True, False)

    def check_hybrid_pipeline(self, feed_ok, legacy_ok):
        from ai_providers import AIProviderSettings
        url = "https://jobs.lever.co/tekton/posting"
        repository = Mock()
        repository.search_catalog_context.return_value = {"known_status_by_url": {}, "suppressed_fingerprints": set()}
        repository.create_job_if_available.return_value = {"outcome": "created", "job": {"id": 1}}
        profile = Mock(get_profile=Mock(return_value={"base_resume_text": "Synthetic resume", "suggested_keywords": ""}))
        def acquire(health, success):
            health["lever"]["source_attempts"] = health["lever"].get("source_attempts", 0) + 1
            if not success:
                health["lever"]["source_failures"] = health["lever"].get("source_failures", 0) + 1
                return []
            return [{"title": "Engineer", "url": url, "snippet": ""}]
        with (
            patch.object(searcher.config, "get_discovery_mode", return_value="hybrid_feeds"),
            patch.object(searcher, "settings_from_profile", return_value=AIProviderSettings("gemini", "synthetic", "private-ai")),
            patch.object(sources, "collect", side_effect=lambda kw, loc, health, cancel, **options: acquire(health, feed_ok)) as feeds,
            patch.object(sources, "fetch_json", side_effect=AssertionError("No paid transport")),
            patch.object(searcher, "inspect_job_posting", return_value={"status": "ok", "details": {
                "title": "Engineer", "company": "Synthetic", "description": "Synthetic description",
                "url": url, "source": "lever"}}),
            patch.object(searcher, "analyze_job_matches_batch", return_value={"success": True, "matches": [
                {"index": 0, "match_score": 90, "match_analysis": "Synthetic match"}]}),
            patch.object(searcher, "search_yahoo_jobs", side_effect=lambda kw, loc, health, **options: acquire(health, legacy_ok)) as legacy,
        ):
            result = searcher.run_job_search_and_matching("Engineer", job_repository=repository,
                        profile_repository=profile)
        self.assertEqual("matches_saved" if feed_ok and legacy_ok else "partial_results", result["outcome"])
        self.assertEqual(1, result["jobs_added"])
        self.assertEqual(url, repository.create_job_if_available.call_args.kwargs["url"])
        legacy.assert_called_once()
        self.assertTrue(feeds.call_args.kwargs["feeds_only"])
        repository.create_job_if_available.assert_called_once()

    def test_reservation_wait_cancellation_never_starts_transport(self):
        cancelled = False
        def checkpoint():
            if cancelled:
                raise OperationCancelled()
        def reserve():
            nonlocal cancelled
            cancelled = True
            return True
        with patch.object(sources.urllib.request, "build_opener") as opener:
            with self.assertRaises(OperationCancelled):
                sources.fetch_json("https://google.serper.dev/search", sources.Budget(checkpoint),
                                   api_key="private", payload={}, reserve=reserve)
            opener.assert_not_called()

    def test_worker_control_exceptions_survive_response_reads(self):
        from search_run_repository import SearchRunLeaseLost
        from search_run_execution import SearchWorkerStopping
        for exception in (SearchRunLeaseLost, SearchWorkerStopping):
            calls = 0
            def checkpoint():
                nonlocal calls
                calls += 1
                if calls > 1:
                    raise exception()
            opener = Mock(); opener.open.return_value = io.BytesIO(b'[]')
            with patch.object(sources.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(exception):
                    sources.fetch_json("https://api.lever.co", sources.Budget(checkpoint))

    def test_malformed_meta_and_urls_are_source_failures(self):
        with patch.object(sources, "fetch_json", return_value={"jobs": [], "meta": None}):
            with self.assertRaises(sources.SourceError):
                sources.feed_rows("greenhouse", "clear", sources.Budget(lambda: None))
        with self.assertRaises(sources.SourceError):
            sources.feed_candidate("ashby", "coder", {"isListed": True, "title": "Engineer", "jobUrl": "https://[", "location": "Remote"})
        health = self.health()
        with patch.object(sources, "feed_rows", return_value=[]), patch.object(sources, "fetch_json", return_value={"organic": [{"link": "https://["}]}):
            self.assertEqual([], sources.collect(["Engineer"], [""], health, lambda: None, api_key="private", reserve=lambda: True))
        self.assertTrue(all(value["source_failures"] == 1 for value in health.values()))


if __name__ == "__main__":
    unittest.main()
