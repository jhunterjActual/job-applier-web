"""Actual import dialog HTML/JavaScript with synthetic, intercepted API traffic."""
import json
from pathlib import Path
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect


class JobImportBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except BaseException:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.writes = []
        self.preview_title = "Synthetic Software Engineer"
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.on("dialog", lambda dialog: dialog.dismiss())
        self.page.route("**/*", self.route)

    def route(self, route):
        request = route.request
        url = urlsplit(request.url)
        if url.netloc != "import.test":
            return route.abort()
        if url.path == "/" or url.path.startswith("/static/"):
            relative = "index.html" if url.path == "/" else url.path.removeprefix("/static/")
            path = Path(__file__).parent / "static" / relative
            if not path.is_file() or not path.resolve().is_relative_to((Path(__file__).parent / "static").resolve()):
                return route.fulfill(status=404, body="")
            mime = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}.get(path.suffix, "application/octet-stream")
            return route.fulfill(body=path.read_bytes(), content_type=mime)
        payload = request.post_data_json if request.method in ("POST", "PUT") else None
        if payload is not None:
            self.writes.append((url.path, payload))
        response = []
        if url.path == "/api/profile":
            response = {"name": "Synthetic", "email": "synthetic@example.invalid", "base_resume_text": "", "ai_provider": "openai", "ai_model": "gpt-5-mini"}
        elif url.path == "/api/session":
            response = {"authentication": "local", "authenticated": False}
        elif url.path == "/api/version":
            response = {"build": "synthetic", "version": "synthetic"}
        elif url.path == "/api/jobs/status":
            response = {"running": False}
        elif url.path == "/api/jobs/import/preview":
            response = {"extraction_succeeded": True, "message": "Review synthetic details.", "job": {
                "url": "https://jobs.example.invalid/role", "company": "Example Cedar", "title": self.preview_title,
                "description": "Synthetic responsibilities and requirements for a software engineering position.",
                "location": "Dayton, Ohio", "work_arrangement": "hybrid", "employment_type": "full_time"}}
        elif url.path == "/api/jobs/import":
            response = {"success": True, "saved": True, "job_id": 1, "message": "Synthetic posting saved."}
        return route.fulfill(content_type="application/json", body=json.dumps(response))

    def open_import(self):
        self.page.goto("https://import.test/")
        self.page.get_by_role("tab", name="Search & Match", exact=True).click()
        self.page.locator("#open-job-import-btn").click()
        dialog = self.page.get_by_role("dialog", name="Add Job Posting by URL", exact=True)
        expect(dialog).to_be_visible()
        expect(self.page.get_by_label("Job posting URL", exact=True)).to_be_focused()
        return dialog

    def preview(self):
        self.page.get_by_label("Job posting URL", exact=True).fill("https://jobs.example.invalid/role")
        self.page.locator("#preview-job-import-btn").click()
        expect(self.page.locator("#save-job-import-btn")).to_be_enabled()

    def test_preview_autofills_position_and_normal_save_submits_it(self):
        dialog = self.open_import()
        self.assertEqual(1, self.page.locator("#job-import-title").count())
        self.assertEqual(1, self.page.locator("#job-import-dialog-title").count())
        self.assertEqual("INPUT", self.page.get_by_label("Position", exact=True).evaluate("el => el.tagName"))
        self.preview()
        expect(self.page.get_by_label("Position", exact=True)).to_have_value(self.preview_title)
        self.page.locator("#save-job-import-btn").click()
        expect(dialog).to_be_hidden()
        saved = [body for path, body in self.writes if path == "/api/jobs/import"]
        self.assertEqual(1, len(saved))
        self.assertEqual(self.preview_title, saved[0]["title"])
        self.assertEqual("Example Cedar", saved[0]["company"])
        self.assertEqual("Dayton, Ohio", saved[0]["location"])
        self.assertEqual("hybrid", saved[0]["work_arrangement"])
        self.assertEqual("full_time", saved[0]["employment_type"])

    def test_missing_position_blocks_save_and_close_restores_opener(self):
        self.preview_title = ""
        dialog = self.open_import()
        self.preview()
        expect(self.page.get_by_label("Position", exact=True)).to_have_value("")
        self.page.locator("#save-job-import-btn").click()
        expect(self.page.get_by_label("Position", exact=True)).to_be_focused()
        self.assertFalse([body for path, body in self.writes if path == "/api/jobs/import"])
        self.page.locator("#close-job-import-modal").click()
        expect(dialog).to_be_hidden()
        expect(self.page.locator("#open-job-import-btn")).to_be_focused()


if __name__ == "__main__":
    unittest.main()
