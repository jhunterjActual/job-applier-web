"""Synthetic same-posting metadata and manual-import regressions; no provider calls."""
import copy
from contextlib import closing
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import app
import searcher
import test_p0_regressions as baseline


URL = "https://jobs.smartrecruiters.com/ExampleEmployer/123456789-data-architect"
DESCRIPTION = "Design reliable data services and maintain enterprise architecture. " * 3


def posting(**changes):
    value = {"@type": "JobPosting", "url": URL,
        "jobLocation": {"@type": "Place", "address": {"addressLocality": "Stuttgart",
                        "addressCountry": {"@type": "Country", "name": "Germany"}}},
        "employmentType": "FULL_TIME"}
    value.update(changes)
    return value


class Element:
    def __init__(self, text):
        self.text = text

    def text_content(self):
        return self.text

    def inner_text(self):
        return self.text


class Page:
    def __init__(self, objects, body=None, description=DESCRIPTION):
        self.scripts = [Element(value if isinstance(value, str) else json.dumps(value)) for value in objects]
        self.url = URL
        self.body = body if body is not None else "Data Architect\n" + DESCRIPTION
        self.description = description

    def query_selector_all(self, selector):
        if selector == 'script[type="application/ld+json"]':
            return self.scripts
        if selector == "h1":
            return [Element("Data Architect")]
        if "description" in selector or "main" in selector:
            return [Element(self.description)]
        return []

    def inner_text(self, selector):
        return self.body

    def title(self):
        return "Data Architect at ExampleEmployer"

    def goto(self, url, **kwargs):
        return SimpleNamespace(status=200)

    def wait_for_selector(self, *args, **kwargs):
        pass


class SmartRecruitersMetadataTests(unittest.TestCase):
    def extract(self, objects, url=URL):
        return searcher._smartrecruiters_structured_metadata(Page(objects), url)

    def scrape(self, objects, body=None, description=DESCRIPTION):
        page = Page(objects, body, description)
        browser = Mock()
        browser.new_context.return_value.new_page.return_value = page
        playwright = MagicMock()
        playwright.__enter__.return_value.chromium.launch.return_value = browser
        with (
            patch.object(searcher, "sync_playwright", return_value=playwright),
            patch.object(searcher, "validate_public_http_url", return_value=(True, "")),
            patch.object(searcher, "is_public_http_url", return_value=True),
        ):
            details = searcher._scrape_job_details_browser(URL)
        browser.close.assert_called_once()
        return details

    def test_matching_graph_preserves_city_country_and_employment(self):
        result = self.extract([{"@graph": [{"@type": "Organization"}, posting()]}])
        self.assertEqual("Stuttgart, Germany", result["location"])
        self.assertEqual("full_time", result["employment_type"])
        self.assertEqual("", result["work_arrangement"])

    def test_unrelated_first_posting_is_not_selected(self):
        result = self.extract([[posting(url=URL.replace("123456789", "999999999")), posting()]])
        self.assertEqual("Stuttgart, Germany", result["location"])

    def test_identity_allows_same_employer_id_slug_but_not_other_origins(self):
        result = self.extract([posting(url="https://jobs.smartrecruiters.com/exampleemployer/123456789")])
        self.assertEqual("Stuttgart, Germany", result["location"])
        for url in (URL.replace("123456789", "999999999"), URL.replace("ExampleEmployer", "Other"),
                    URL.replace("jobs.smartrecruiters.com", "jobs.smartrecruiters.com.evil.test"),
                    URL.replace("https://", "http://"), "https://jobs.smartrecruiters.com/ExampleEmployer"):
            with self.subTest(url=url):
                self.assertEqual({}, self.extract([posting(url=url)]))

    def test_missing_url_malformed_json_and_ambiguous_matches_refuse(self):
        no_url = posting()
        no_url.pop("url")
        for objects in ([no_url], ["{"], [posting(), posting(employmentType="CONTRACTOR")],
                        ['{"@type":"JobPosting","url":"' + URL + '","url":"' + URL + '"}'],
                        ['{"@type":"JobPosting","url":"' + URL + '","value":NaN}']):
            with self.subTest(objects=objects):
                self.assertEqual({}, self.extract(objects))

    def test_exact_telecommute_only_and_no_geographic_inference(self):
        for value, expected in (("TELECOMMUTE", "remote"), ("telecommute", "remote"),
                                ("NOT_REMOTE", ""), ("REMOTE", ""), ("", ""), (["TELECOMMUTE"], "")):
            with self.subTest(value=value):
                self.assertEqual(expected, self.extract([posting(jobLocationType=value)])["work_arrangement"])

    def test_malformed_addresses_do_not_destroy_other_valid_fields(self):
        for value in (5, [None], [{"address": 5}], True, [False]):
            with self.subTest(value=value):
                result = self.extract([posting(jobLocation=value)])
                self.assertEqual("", result["location"])
                self.assertEqual("full_time", result["employment_type"])
        result = self.extract([posting(jobLocation=None, applicantLocationRequirements={"name": "United States"})])
        self.assertEqual("", result["location"])

    def test_employment_ambiguity_and_unknowns_are_not_asserted(self):
        for value in (["FULL_TIME", "CONTRACTOR"], ["FULL_TIME", "UNKNOWN"], "NOT_CONTRACT", None, {"name": "FULL_TIME"}):
            with self.subTest(value=value):
                self.assertEqual("", self.extract([posting(employmentType=value)])["employment_type"])
        self.assertEqual("contract", self.extract([posting(employmentType="CONTRACTOR")])["employment_type"])

    def test_parser_bounds_refuse_partial_selection(self):
        nested = posting()
        for _ in range(18):
            nested = {"@graph": nested}
        for objects in ([{}] * 33, [posting(), " " * (256 * 1024 + 1)], [nested], [[{}] * 1025]):
            with self.subTest(kind=len(objects)):
                self.assertEqual({}, self.extract(objects))

    def test_browser_fallback_fills_missing_fields_without_remote_guess(self):
        details = self.scrape([posting(jobLocationType="NOT_REMOTE")])
        self.assertEqual("Stuttgart, Germany", details["location"])
        self.assertEqual("full_time", details["employment_type"])
        self.assertEqual("", details["work_arrangement"])
        self.assertEqual("Data Architect", details["title"])

    def test_visible_fields_win_and_structured_city_does_not_imply_remote(self):
        details = self.scrape([posting(jobLocationType="TELECOMMUTE", employmentType="CONTRACTOR")],
                              "Data Architect\nLocation\nDayton, OH\nFull-time on-site role. " + DESCRIPTION,
                              "Full-time on-site role. " + DESCRIPTION)
        # Description is the visible job description, not unrelated page chrome.
        self.assertEqual("Dayton, OH", details["location"])
        self.assertEqual("full_time", details["employment_type"])
        self.assertEqual("on_site", details["work_arrangement"])
        city = posting(jobLocation="Remote", jobLocationType="NOT_REMOTE")
        unknown = self.scrape([city])
        self.assertEqual("Remote", unknown["location"])
        self.assertEqual("", unknown["work_arrangement"])

    def test_preview_and_save_persist_metadata_without_feed_candidate(self):
        fixture = baseline.ManualJobImportTests("test_preview_extracts_editable_fields_without_saving")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        details = self.scrape([posting()])
        with (
            patch.object(app, "get_db_connection", side_effect=fixture.connection_factory),
            patch.object(app, "validate_public_http_url", return_value=(True, "")),
            patch.object(app, "inspect_job_posting", return_value={"status": "ok", "details": details}),
            patch.object(app, "settings_from_profile", return_value=SimpleNamespace(api_key="")),
            patch.object(app, "analyze_job_match", side_effect=AssertionError("No provider call permitted")) as ai,
        ):
            preview = app.preview_manual_job(app.ManualJobPreviewRequest(url=URL))
            self.assertTrue(preview["extraction_succeeded"])
            fields = copy.deepcopy(preview["job"])
            fields.pop("source")
            saved = app.save_manual_job(app.ManualJobSaveRequest(**fields))
            self.assertTrue(saved["success"])
            ai.assert_not_called()
        with closing(fixture.connection_factory()) as connection:
            row = connection.execute("SELECT location, employment_type, work_arrangement FROM jobs").fetchone()
        self.assertEqual(("Stuttgart, Germany", "full_time", ""), tuple(row))


if __name__ == "__main__":
    unittest.main()
