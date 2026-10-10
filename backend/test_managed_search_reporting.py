"""Full shared search pages must not impersonate per-provider detail caps."""
import unittest
import sqlite3
from unittest.mock import Mock, patch

import discovery_sources
import searcher
from source_diagnostics import safe_provider_counters, persist_source_diagnostics, list_source_diagnostics


class ManagedSearchReportingTests(unittest.TestCase):
    def test_full_shared_pages_preserve_partial_without_false_detail_cap(self):
        health = {provider: {"partial_results": {"candidate_limit_hits": 0}}
                  for provider in searcher.PROVIDER_DOMAINS}
        rows = [{"link": f"https://jobs.lever.co/example/posting-{index}"}
                for index in range(10)]
        reserve = Mock(return_value=True)
        successes = set()
        def record(provider, counter):
            health[provider][counter] = health[provider].get(counter, 0) + 1
        with patch.object(discovery_sources, "fetch_json", return_value={"organic": rows}) as fetch:
            discovery_sources._collect_managed(
                ["Data Engineer", "Software Engineer - Data"], ["US remote"],
                health, lambda: None, object(), "synthetic", reserve,
                lambda url: True, record, successes)
        self.assertEqual({("Data Engineer", "US remote"),
                          ("Software Engineer - Data", "US remote")}, successes)
        self.assertEqual(2, fetch.call_count)
        for call in fetch.call_args_list:
            self.assertEqual(10, call.kwargs["payload"]["num"])
            self.assertIs(reserve, call.kwargs["reserve"])
        for provider, item in health.items():
            with self.subTest(provider=provider):
                self.assertEqual(0, item["partial_results"]["candidate_limit_hits"])
                self.assertEqual(2, item["partial_results"]["managed_page_limits"])
                counters = safe_provider_counters(item)
                self.assertEqual(2, counters["managed_page_limits"])
        alerts = searcher.provider_alerts_from_health(health)
        self.assertEqual(4, len(alerts))
        self.assertTrue(all("shared search" in alert["message"] and
                            "10-result" in alert["message"] and
                            "25-posting" not in alert["message"] for alert in alerts))
        with sqlite3.connect(":memory:") as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("CREATE TABLE source_diagnostics (id INTEGER PRIMARY KEY, "
                               "recorded_at TEXT, provider TEXT, diagnostic_code TEXT, counters_json TEXT)")
            persist_source_diagnostics(connection, {"provider_health": health,
                                                   "provider_alerts": alerts}, "2026-10-10T00:00:00Z")
            stored = list_source_diagnostics(connection)
            self.assertEqual(4, stored["count"])
            self.assertTrue(all(item["counters"]["managed_page_limits"] == 2 and
                                item["counters"]["candidate_limit_hits"] == 0
                                for item in stored["items"]))
        health["lever"]["accepted"] = 1
        result = searcher._search_completion(health, 1, 1, 1, 1)
        self.assertTrue(result["partial"])
        self.assertEqual(4, result["source_counts"]["limited"])

    def test_actual_provider_cap_retains_its_distinct_explanation(self):
        alerts = searcher.provider_alerts_from_health({"lever": {
            "candidate_budget_exhausted": True,
            "partial_results": {"managed_page_limits": 1}}})
        self.assertIn("25-posting processing limit", alerts[0]["message"])
        self.assertIn("10-result page limit", alerts[0]["message"])

    def test_short_managed_page_does_not_report_full_page(self):
        for rows in ([], [{"link": "https://jobs.lever.co/example/posting"}]):
            with self.subTest(rows=len(rows)):
                health = {provider: {"partial_results": {"candidate_limit_hits": 0}}
                          for provider in searcher.PROVIDER_DOMAINS}
                successes = set()
                with patch.object(discovery_sources, "fetch_json", return_value={"organic": rows}):
                    discovery_sources._collect_managed(
                        ["Engineer"], ["Remote"], health, lambda: None, object(),
                        "synthetic", Mock(), lambda url: True, lambda *args: None, successes)
                self.assertEqual({("Engineer", "Remote")}, successes)
                self.assertTrue(all("managed_page_limits" not in item["partial_results"]
                                    for item in health.values()))

    def test_failure_preserves_prior_success_and_stops_paid_requests(self):
        health = {provider: {"partial_results": {"candidate_limit_hits": 0}}
                  for provider in searcher.PROVIDER_DOMAINS}
        successes = set()
        with patch.object(discovery_sources, "fetch_json", side_effect=[
                {"organic": []}, discovery_sources.SourceError("rate_limit")]) as fetch:
            discovery_sources._collect_managed(
                ["Engineer", "Analyst"], ["Remote", "Ohio"], health,
                lambda: None, object(), "synthetic", Mock(), lambda url: True,
                lambda *args: None, successes)
        self.assertEqual(2, fetch.call_count)
        self.assertEqual({("Engineer", "Remote")}, successes)

    def test_malformed_managed_page_never_suppresses_fallback(self):
        health = {provider: {"partial_results": {"candidate_limit_hits": 0}}
                  for provider in searcher.PROVIDER_DOMAINS}
        successes = set()
        with patch.object(discovery_sources, "fetch_json", return_value={"organic": [None]}):
            discovery_sources._collect_managed(
                ["Engineer"], ["Remote"], health, lambda: None, object(),
                "synthetic", Mock(), lambda url: True, lambda *args: None, successes)
        self.assertEqual(set(), successes)

    def test_pipeline_falls_back_only_for_failed_and_unattempted_pairs(self):
        from ai_providers import AIProviderSettings
        repository = Mock()
        repository.search_catalog_context.return_value = {
            "known_status_by_url": {}, "suppressed_fingerprints": set()}
        profile = Mock(get_profile=Mock(return_value={
            "base_resume_text": "Synthetic resume", "suggested_keywords": ""}))
        with (
            patch.object(searcher.config, "get_discovery_mode", return_value="hybrid_feeds"),
            patch.object(searcher, "settings_from_profile", return_value=AIProviderSettings("gemini", "synthetic", "synthetic")),
            patch("role_queries.execution_titles", return_value=["Engineer", "Analyst"]),
            patch("public_job_feeds.collect", return_value=[]),
            patch("additional_sources.collect", return_value=[]),
            patch.object(discovery_sources, "feed_rows", return_value=[]) as feeds,
            patch.object(discovery_sources, "fetch_json", side_effect=[
                {"organic": []}, discovery_sources.SourceError("rate_limit")]) as managed,
            patch.object(searcher, "search_yahoo_jobs", return_value=[]) as legacy,
        ):
            result = searcher.run_job_search_and_matching(
                "Synthetic", "Remote; Ohio", job_repository=repository,
                profile_repository=profile, serper_api_key="synthetic",
                reserve_search_request=Mock(return_value=True))
        self.assertEqual(2, managed.call_count)
        self.assertEqual([("Engineer", "Ohio"), ("Analyst", "Remote"), ("Analyst", "Ohio")],
                         [call.args[:2] for call in legacy.call_args_list])
        self.assertEqual(len(discovery_sources.CATALOG) + 6, feeds.call_count)
        self.assertEqual(4, result["source_counts"]["failed"])


if __name__ == "__main__":
    unittest.main()
