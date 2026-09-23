"""Incomplete catalog coverage must never read as a definitive empty market."""
import unittest
import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

from searcher import provider_alerts_from_health, _search_completion


class CatalogReportingTests(unittest.TestCase):
    def test_catalog_limits_have_actionable_explanations(self):
        for key, text in (
            ("catalog_page_limits", "more postings"),
            ("catalog_request_limits", "request or time limit"),
            ("catalog_term_limits", "search term limit"),
        ):
            with self.subTest(key=key):
                health = {"smartrecruiters": {"partial_results": {key: 1}}}
                alerts = provider_alerts_from_health(health)
                self.assertEqual("partial_results", alerts[0]["code"])
                self.assertIn(text, alerts[0]["message"])
                result = _search_completion(health, 0, 0, 0, 0)
                self.assertNotEqual("no_results", result.get("outcome"))
                self.assertFalse(result["success"])

    def test_unknown_limit_still_has_a_reason(self):
        alerts = provider_alerts_from_health({"lever": {"partial_results": {"future_limit": 1}}})
        self.assertIn("some sources could not be fully checked", alerts[0]["message"])

    def test_operator_probe_reports_each_employer_and_page_scope(self):
        import probe_discovery
        import discovery_sources
        class Page(list):
            complete = False
        with patch.dict(os.environ), patch("sys.argv", ["probe_discovery.py", "--details"]), \
                patch.object(probe_discovery, "CATALOG", discovery_sources.CATALOG), \
                patch.object(probe_discovery, "feed_rows", return_value=Page([{}])) as feeds, \
                patch.object(probe_discovery, "feed_candidate", return_value={"url": "https://jobs.lever.co/example/posting"}), \
                patch("searcher.inspect_job_posting", return_value={"status": "ok", "details": {
                    "title": "Synthetic", "company": "Synthetic", "description": "Synthetic"}}) as details:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, probe_discovery.main())
        receipt = json.loads(output.getvalue())
        self.assertEqual(15, feeds.call_count)
        self.assertEqual(15, details.call_count)
        self.assertEqual(15, receipt["detail_sample_limit"])
        self.assertEqual(15, len({r["board"] for r in receipt["feeds"]}))
        self.assertTrue(all(not r["page_scope_complete"] for r in receipt["feeds"]))
        self.assertFalse(receipt["beta_ready"])
        self.assertTrue(all(c.kwargs["page_index"] == 0 for c in feeds.call_args_list))


if __name__ == "__main__":
    unittest.main()
