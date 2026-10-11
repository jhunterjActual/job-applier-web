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
    def test_runtime_default_has_no_serper_key(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("", searcher.config.get_serper_api_key())

    def test_catalog_contains_exactly_the_fifteen_approved_employers(self):
        expected = {
            ("lever", "tekton"), ("greenhouse", "clear"), ("ashby", "coder"),
            ("greenhouse", "springhealth66"), ("greenhouse", "sothebys"),
            ("greenhouse", "givedirectly"), ("greenhouse", "spacex"),
            ("lever", "rocketship"), ("lever", "weloglobal"),
            ("smartrecruiters", "BoschGroup"), ("smartrecruiters", "AccorHotel"),
            ("smartrecruiters", "Eurofins"), ("smartrecruiters", "HMGroup"),
            ("smartrecruiters", "AECOM2"), ("smartrecruiters", "NorthwesternMedicine"),
        }
        self.assertEqual(expected, set(sources.CATALOG))
        self.assertEqual(15, len(sources.CATALOG))


class DiscoverySourceTests(unittest.TestCase):
    def setUp(self):
        # Preserve offline adapter contract checks for the deferred proposal.
        # Production has no environment switch for this code-level suspension.
        enabled = patch.object(sources, "SERPER_ENABLED", True)
        enabled.start()
        self.addCleanup(enabled.stop)
        catalog = patch.object(sources, "CATALOG", (("lever", "tekton"),
                                                   ("greenhouse", "clear"), ("ashby", "coder")))
        catalog.start()
        self.addCleanup(catalog.stop)
        sources.clear_feed_cache()
        self.addCleanup(sources.clear_feed_cache)

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

    def test_enabled_endpoint_requires_key_before_reservation_or_transport(self):
        reserve = Mock(return_value=True)
        with patch.object(sources.urllib.request, "build_opener") as opener:
            for key in (None, "", " ", False, 1):
                with self.subTest(key=key), self.assertRaisesRegex(sources.SourceError, "quota_unavailable"):
                    sources.fetch_json("https://google.serper.dev/search", sources.Budget(lambda: None),
                                       api_key=key, reserve=reserve)
            reserve.assert_not_called()
            opener.assert_not_called()

    def test_enabled_endpoint_requires_explicit_successful_reservation(self):
        with patch.object(sources.urllib.request, "build_opener") as opener:
            for reserve in (None, True, Mock(return_value=False), Mock(return_value=None),
                            Mock(return_value=1), Mock(return_value="yes")):
                with self.subTest(reserve=reserve), self.assertRaisesRegex(sources.SourceError, "quota_unavailable"):
                    sources.fetch_json("https://google.serper.dev/search", sources.Budget(lambda: None),
                                       api_key="synthetic", reserve=reserve)
            opener.assert_not_called()

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
        def rows(provider, board, budget, **options):
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
        row = {"text": "Engineer", "hostedUrl": "https://jobs.lever.co/tekton/posting",
               "categories": {"location": "Remote"}}
        with patch.object(sources, "fetch_json", return_value=[row] * 20) as fetch:
            result = sources.feed_rows("lever", "tekton", sources.Budget(lambda: None))
        self.assertFalse(result.complete)
        self.assertEqual(60, len(result))
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

    def test_managed_candidates_retain_query_checks_and_duplicate_feed_metadata(self):
        url = "https://jobs.lever.co/tekton/posting"
        row = {"text": "Engineer", "hostedUrl": url, "categories": {"location": "Remote"}}
        with patch.object(sources, "fetch_json", return_value={"organic": [{"link": url}]}), \
                patch.object(sources, "feed_rows", side_effect=lambda provider, *args, **kwargs: [row] if provider == "lever" else []):
            result = sources.collect(["Engineer"], ["Remote"], self.health(), lambda: None,
                                     api_key="synthetic", reserve=lambda: True)
        self.assertEqual(1, len(result))
        self.assertIs(True, result[0]["verify_query"])
        self.assertEqual("Remote", result[0]["feed_metadata"]["location"])

    def test_managed_failure_does_not_mark_unrelated_feed_failed(self):
        health = self.health()
        health["unrelated_feed"] = {"source_attempts": 7}
        with patch.object(sources, "feed_rows", return_value=[]), \
                patch.object(sources, "fetch_json", side_effect=sources.SourceError("quota_unavailable")):
            sources.collect(["Engineer"], ["Remote"], health, lambda: None,
                            api_key="synthetic", reserve=lambda: False)
        self.assertEqual({"source_attempts": 7}, health["unrelated_feed"])

    def test_hybrid_pipeline_keeps_owner_repository_and_match_save_flow(self):
        self.check_hybrid_pipeline(True, True)

    def test_enabled_pipeline_passes_key_and_reservation_without_bypassing_save_flow(self):
        self.check_hybrid_pipeline(True, True, enabled=True)

    def test_enabled_pipeline_falls_back_when_managed_discovery_fails(self):
        self.check_hybrid_pipeline(False, True, enabled=True)

    def test_hybrid_saves_legacy_results_when_feed_fails(self):
        self.check_hybrid_pipeline(False, True)

    def test_hybrid_saves_feed_results_when_legacy_fails(self):
        self.check_hybrid_pipeline(True, False)

    def check_hybrid_pipeline(self, feed_ok, legacy_ok, enabled=False):
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
        def acquire_feeds(kw, loc, health, cancel, **options):
            if enabled and feed_ok:
                options["managed_successes"].add((kw[0], loc[0]))
            return acquire(health, feed_ok)
        with (
            patch.object(searcher.config, "get_discovery_mode", return_value="hybrid_feeds"),
            patch("public_job_feeds.collect", return_value=[]),
            patch.object(searcher, "settings_from_profile", return_value=AIProviderSettings("gemini", "synthetic", "private-ai")),
            patch.object(sources, "collect", side_effect=acquire_feeds) as feeds,
            patch.object(sources, "fetch_json", side_effect=AssertionError("No paid transport")),
            patch.object(searcher, "inspect_job_posting", return_value={"status": "ok", "details": {
                "title": "Engineer", "company": "Synthetic", "description": "Synthetic description",
                "url": url, "source": "lever"}}),
            patch.object(searcher, "analyze_job_matches_batch", return_value={"success": True, "matches": [
                {"index": 0, "match_score": 90, "match_analysis": "Synthetic match"}]}),
            patch.object(searcher, "search_yahoo_jobs", side_effect=lambda kw, loc, health, **options: acquire(health, legacy_ok)) as legacy,
        ):
            reservation = Mock(return_value=True)
            options = {"serper_api_key": "synthetic", "reserve_search_request": reservation} if enabled else {}
            result = searcher.run_job_search_and_matching("Engineer", job_repository=repository,
                        profile_repository=profile, **options)
        self.assertEqual("matches_saved" if feed_ok and legacy_ok else "partial_results", result["outcome"])
        self.assertEqual(1, result["jobs_added"])
        self.assertEqual(url, repository.create_job_if_available.call_args.kwargs["url"])
        if enabled and feed_ok:
            legacy.assert_not_called()
        else:
            legacy.assert_called_once()
        self.assertEqual(not enabled, feeds.call_args.kwargs["feeds_only"])
        if enabled:
            self.assertEqual("synthetic", feeds.call_args.kwargs["api_key"])
            self.assertIs(reservation, feeds.call_args.kwargs["reserve"])
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


class ExpandedCatalogTests(unittest.TestCase):
    def setUp(self):
        sources.clear_feed_cache()
        self.addCleanup(sources.clear_feed_cache)

    def health(self):
        return {provider: {"partial_results": {"candidate_limit_hits": 0}, "errors": []}
                for provider in searcher.PROVIDER_DOMAINS}

    def smart_row(self, **overrides):
        return {"id": "744000000000001", "name": "Laboratory Technician",
                "company": {"identifier": "Eurofins"},
                "ref": "https://api.smartrecruiters.com/v1/companies/Eurofins/postings/744000000000001",
                "location": {"city": "Dayton", "region": "Ohio", "country": "us", "remote": False},
                **overrides}

    def test_smartrecruiters_list_row_becomes_validated_employer_posting(self):
        candidate = sources.feed_candidate("smartrecruiters", "Eurofins", self.smart_row())
        self.assertEqual("https://jobs.smartrecruiters.com/Eurofins/744000000000001", candidate["url"])
        self.assertTrue(searcher.is_specific_job_url(candidate["url"]))
        self.assertTrue(sources.matches(candidate, ["Laboratory Technician"], ["Ohio"]))
        self.assertFalse(sources.matches(candidate, ["Laboratory Technician"], ["remote"]))

    def test_smartrecruiters_rejects_cross_employer_and_unsafe_urls(self):
        invalid = [
            {"company": {"identifier": "OtherEmployer"}},
            {"ref": "https://api.smartrecruiters.com/v1/companies/OtherEmployer/postings/744000000000001"},
            {"ref": "https://api.smartrecruiters.com/v1/companies/Eurofins/postings/744000000000002"},
            {"id": "../../admin"},
            {"postingUrl": "https://jobs.smartrecruiters.com/OtherEmployer/744000000000001"},
            {"postingUrl": "https://jobs.smartrecruiters.com.evil.test/Eurofins/744000000000001"},
            {"postingUrl": "https://user:pass@jobs.smartrecruiters.com/Eurofins/744000000000001"},
            {"postingUrl": "http://jobs.smartrecruiters.com/Eurofins/744000000000001"},
        ]
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(sources.SourceError):
                sources.feed_candidate("smartrecruiters", "Eurofins", self.smart_row(**override))

    def test_smartrecruiters_board_case_is_insensitive_but_not_identity(self):
        row = self.smart_row(company={"identifier": "eurofins"},
                             ref="https://api.smartrecruiters.com/v1/companies/eurofins/postings/744000000000001",
                             postingUrl="https://jobs.smartrecruiters.com/eurofins/744000000000001-lab-tech")
        candidate = sources.feed_candidate("smartrecruiters", "Eurofins", row)
        self.assertTrue(searcher.is_specific_job_url(candidate["url"]))

    def test_all_employers_receive_first_page_before_any_second_page(self):
        calls = []
        def rows(provider, board, budget, **options):
            calls.append((provider, board, options.get("page_index")))
            budget.take()
            return sources.FeedRows([], complete=options.get("page_index", 0) > 0)
        health = self.health()
        with patch.object(sources, "feed_rows", side_effect=rows):
            self.assertEqual([], sources.collect(["Engineer"], ["remote"], health,
                                                lambda: None, feeds_only=True))
        self.assertEqual(set(sources.CATALOG), {(p, b) for p, b, _ in calls[:15]})
        self.assertEqual([0] * 15, [page for _, _, page in calls[:15]])
        self.assertLessEqual(len(calls), sources.MAX_REQUESTS)
        self.assertIn(("smartrecruiters", "NorthwesternMedicine"), {(p, b) for p, b, _ in calls})

    def test_candidate_order_gives_later_employers_a_share_of_provider_limit(self):
        def rows(provider, board, budget, **options):
            if provider != "lever":
                return sources.FeedRows([], complete=True)
            return sources.FeedRows([{"text": "Engineer", "categories": {"location": "Remote"},
                "hostedUrl": f"https://jobs.lever.co/{board}/posting-{index}"}
                for index in range(20)], complete=True)
        with patch.object(sources, "feed_rows", side_effect=rows):
            result = sources.collect(["Engineer"], ["remote"], self.health(), lambda: None,
                                     feeds_only=True)
        from urllib.parse import urlsplit
        self.assertEqual({"tekton", "rocketship", "weloglobal"},
                         {urlsplit(row["url"]).path.split("/")[1] for row in result[:3]})

    def test_request_exhaustion_is_partial_and_does_not_erase_candidates(self):
        def rows(provider, board, budget, **options):
            budget.take()
            if provider == "lever" and board == "tekton":
                return sources.FeedRows([{"text": "Engineer",
                    "hostedUrl": "https://jobs.lever.co/tekton/posting",
                    "categories": {"location": "Remote"}}], complete=False)
            return sources.FeedRows([], complete=False)
        health = self.health()
        with patch.object(sources, "MAX_REQUESTS", 15), patch.object(sources, "feed_rows", side_effect=rows):
            result = sources.collect(["Engineer"], ["remote"], health, lambda: None, feeds_only=True)
        self.assertIn("https://jobs.lever.co/tekton/posting", [row["url"] for row in result])
        self.assertGreater(sum(item["partial_results"].get("catalog_request_limits", 0)
                               + item["partial_results"].get("catalog_page_limits", 0)
                               for item in health.values()), 0)

    def test_partial_empty_pages_do_not_claim_an_exhausted_empty_source(self):
        health = self.health()
        with patch.object(sources, "feed_rows", return_value=sources.FeedRows([], complete=False)):
            self.assertEqual([], sources.collect(["Engineer"], ["remote"], health,
                                                lambda: None, feeds_only=True))
        self.assertEqual(0, sum(item.get("source_empty", 0) for item in health.values()))
        self.assertGreater(sum(item["partial_results"].get("catalog_page_limits", 0)
                               for item in health.values()), 0)

    def test_later_page_failure_keeps_valid_matches_from_earlier_page(self):
        def rows(provider, board, budget, **options):
            if provider != "lever" or board != "tekton":
                return sources.FeedRows([], complete=True)
            if options.get("page_index", 0) > 0:
                raise sources.SourceError("http_error")
            return sources.FeedRows([{"text": "Engineer", "categories": {"location": "Remote"},
                "hostedUrl": "https://jobs.lever.co/tekton/posting"}], complete=False)
        health = self.health()
        with patch.object(sources, "feed_rows", side_effect=rows):
            result = sources.collect(["Engineer"], ["remote"], health, lambda: None, feeds_only=True)
        self.assertEqual(["https://jobs.lever.co/tekton/posting"], [row["url"] for row in result])
        self.assertGreater(health["lever"].get("source_failures", 0), 0)

    def test_catalog_cancellation_prevents_later_employer_requests(self):
        calls = 0
        def checkpoint():
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OperationCancelled()
        with patch.object(sources, "feed_rows", return_value=sources.FeedRows([], complete=True)) as feeds:
            with self.assertRaises(OperationCancelled):
                sources.collect(["Engineer"], ["remote"], self.health(), checkpoint, feeds_only=True)
        self.assertLessEqual(feeds.call_count, 1)

    def test_smartrecruiters_query_is_public_encoded_and_pagination_bounded(self):
        from urllib.parse import parse_qs, urlsplit
        response = {"content": [self.smart_row()] * 100, "totalFound": 10000, "offset": 0, "limit": 100}
        pages = [response, {**response, "offset": 100}]
        with patch.object(sources, "fetch_json", side_effect=pages) as fetch:
            result = sources.feed_rows("smartrecruiters", "Eurofins", sources.Budget(lambda: None),
                                       keywords=["lab & science"])
        self.assertFalse(result.complete)
        self.assertLessEqual(fetch.call_count, 2)
        queries = [parse_qs(urlsplit(call.args[0]).query) for call in fetch.call_args_list]
        self.assertEqual(["0", "100"], [query["offset"][0] for query in queries])
        for query in queries:
            self.assertEqual(["PUBLIC"], query["destination"])
            self.assertEqual(["lab & science"], query["q"])
            self.assertEqual(["100"], query["limit"])

    def test_smartrecruiters_malformed_list_metadata_cannot_claim_success(self):
        invalid = [
            {"content": {}, "totalFound": 0},
            {"content": [], "totalFound": True},
            {"content": [], "totalFound": -1},
            {"content": [self.smart_row()], "totalFound": 0},
            {"content": [self.smart_row()] * 101, "totalFound": 101},
            {"content": [], "totalFound": 0, "offset": 100},
        ]
        for document in invalid:
            with self.subTest(document_size=len(document.get("content", []))), \
                    patch.object(sources, "fetch_json", return_value=document):
                with self.assertRaises(sources.SourceError):
                    sources.feed_rows("smartrecruiters", "Eurofins", sources.Budget(lambda: None),
                                      page_index=0, keywords=["Technician"])

    def test_larger_response_allowance_is_scoped_to_spacex_and_still_bounded(self):
        url = "https://boards-api.greenhouse.io/v1/boards/spacex/jobs"
        payload = json.dumps({"jobs": [], "padding": "x" * sources.MAX_BYTES}).encode()
        opener = Mock()
        opener.open.return_value = io.BytesIO(payload)
        with patch.object(sources.urllib.request, "build_opener", return_value=opener):
            self.assertEqual([], sources.fetch_json(url, sources.Budget(lambda: None),
                max_bytes=sources.LARGE_BOARD_MAX_BYTES)["jobs"])
        opener.open.return_value = io.BytesIO(b"x" * (sources.LARGE_BOARD_MAX_BYTES + 1))
        with patch.object(sources.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(sources.SourceError, "oversized_response"):
                sources.fetch_json(url, sources.Budget(lambda: None), max_bytes=sources.LARGE_BOARD_MAX_BYTES)
        for other_url in ("https://boards-api.greenhouse.io/v1/boards/clear/jobs",
                          "https://google.serper.dev/search"):
            with self.subTest(url=other_url), patch.object(sources.urllib.request, "build_opener") as denied:
                with self.assertRaises(sources.SourceError):
                    sources.fetch_json(other_url, sources.Budget(lambda: None),
                                       max_bytes=sources.LARGE_BOARD_MAX_BYTES)
                denied.assert_not_called()

    def test_public_board_cache_reuses_rows_across_roles_but_expires(self):
        document = {"jobs": [], "meta": {"total": 0}}
        with patch.object(sources.time, "monotonic", return_value=100), \
                patch.object(sources, "fetch_json", return_value=document) as fetch:
            sources.feed_rows("greenhouse", "clear", sources.Budget(lambda: None), keywords=["Engineer"])
            sources.feed_rows("greenhouse", "clear", sources.Budget(lambda: None), keywords=["Lawyer"])
            self.assertEqual(1, fetch.call_count)
        with patch.object(sources.time, "monotonic", return_value=101 + sources.CACHE_TTL_SECONDS), \
                patch.object(sources, "fetch_json", return_value=document) as fetch:
            sources.feed_rows("greenhouse", "clear", sources.Budget(lambda: None), keywords=["Engineer"])
            fetch.assert_called_once()

    def test_smartrecruiters_queries_and_failed_fetches_are_not_cached(self):
        document = {"content": [], "totalFound": 0, "offset": 0, "limit": 100}
        with patch.object(sources, "fetch_json", return_value=document) as fetch:
            for _ in range(2):
                sources.feed_rows("smartrecruiters", "Eurofins", sources.Budget(lambda: None),
                                  keywords=["Laboratory Technician"])
            self.assertEqual(2, fetch.call_count)
        with patch.object(sources, "fetch_json", side_effect=sources.SourceError("http_error")) as fetch:
            for _ in range(2):
                with self.assertRaises(sources.SourceError):
                    sources.feed_rows("greenhouse", "clear", sources.Budget(lambda: None))
            self.assertEqual(2, fetch.call_count)

    def test_cache_hit_still_checks_cancellation_and_does_not_share_mutable_rows(self):
        document = {"jobs": [{"title": "Original",
                    "absolute_url": "https://boards.greenhouse.io/clear/jobs/12345",
                    "location": {"name": "Remote"}}], "meta": {"total": 1}}
        with patch.object(sources, "fetch_json", return_value=document) as fetch:
            first = sources.feed_rows("greenhouse", "clear", sources.Budget(lambda: None))
            first[0]["title"] = "Changed by caller"
            second = sources.feed_rows("greenhouse", "clear", sources.Budget(lambda: None))
            self.assertEqual("Original", second[0]["title"])
            with self.assertRaises(OperationCancelled):
                sources.feed_rows("greenhouse", "clear", sources.Budget(Mock(side_effect=OperationCancelled())))
            self.assertEqual(1, fetch.call_count)

    def test_public_cache_respects_entry_and_byte_bounds(self):
        document = {"jobs": [], "meta": {"total": 0}}
        with patch.object(sources, "CACHE_MAX_ENTRIES", 1), \
                patch.object(sources, "fetch_json", return_value=document) as fetch:
            for board in ("clear", "sothebys", "clear"):
                sources.feed_rows("greenhouse", board, sources.Budget(lambda: None))
            self.assertEqual(3, fetch.call_count)
        sources.clear_feed_cache()
        with patch.object(sources, "CACHE_MAX_BYTES", 1), \
                patch.object(sources, "fetch_json", return_value=document) as fetch:
            for _ in range(2):
                sources.feed_rows("greenhouse", "clear", sources.Budget(lambda: None))
            self.assertEqual(2, fetch.call_count)

    def test_expired_run_budget_cannot_be_extended_by_a_cache_hit(self):
        with patch.object(sources, "fetch_json", return_value={"jobs": [], "meta": {"total": 0}}) as fetch:
            sources.feed_rows("greenhouse", "clear", sources.Budget(lambda: None))
            expired = sources.Budget(lambda: None)
            expired.deadline = sources.time.monotonic() - 1
            with self.assertRaisesRegex(sources.SourceError, "request_limit"):
                sources.feed_rows("greenhouse", "clear", expired)
            self.assertEqual(1, fetch.call_count)


if __name__ == "__main__":
    unittest.main()
