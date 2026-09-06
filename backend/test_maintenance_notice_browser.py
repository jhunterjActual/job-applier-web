"""Synthetic browser regressions; no application data or external network used."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright


STATIC = Path(__file__).parent / "static"


class MaintenanceNoticeBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception:
            cls.playwright.stop()
            raise unittest.SkipTest("Installed Playwright Chromium is required for browser regressions")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 390, "height": 844})
        self.addCleanup(self.page.close)
        self.now = datetime.now(timezone.utc)
        self.payload = {"server_time": self.now.isoformat(), "notice": {
            "message": "Please save your changes before maintenance.",
            "impact": "Search and tailoring may be unavailable.", "status": "planned",
            "time_zone": "America/New_York",
            "publish_at": (self.now - timedelta(hours=1)).isoformat(),
            "starts_at": (self.now + timedelta(hours=1)).isoformat(),
            "ends_at": (self.now + timedelta(hours=2)).isoformat(),
            "expires_at": (self.now + timedelta(hours=2, minutes=15)).isoformat(),
        }}
        self.failed = False
        self.requests = 0

        def route(request):
            if request.request.url.endswith("/api/maintenance-notice"):
                self.requests += 1
                request.fulfill(status=503 if self.failed else 200,
                                content_type="application/json", body=json.dumps(self.payload))
            elif request.request.url == "http://notice.test/":
                request.fulfill(content_type="text/html", body=(STATIC / "sign-in.html").read_text())
            elif "/static/maintenance-notice." in request.request.url:
                name = request.request.url.split("/static/")[1].split("?")[0]
                request.fulfill(content_type="text/css" if name.endswith("css") else "text/javascript",
                                body=(STATIC / name).read_text())
            else:
                request.abort()

        self.page.route("**/*", route)
        self.page.goto("http://notice.test/")
        self.page.locator("#maintenance-notice:not([hidden])").wait_for()

    def refresh(self):
        previous = self.requests
        with self.page.expect_response("**/api/maintenance-notice"):
            self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        self.page.wait_for_timeout(100)
        self.assertGreater(self.requests, previous)

    def test_mobile_sign_in_renders_window_status_and_plain_text_without_overflow(self):
        banner = self.page.locator("#maintenance-notice")
        self.assertIn("America/New_York", banner.inner_text())
        self.assertIn("Expected impact:", banner.inner_text())
        self.assertEqual("/auth/login", self.page.get_by_role("link", name="Continue to sign in").get_attribute("href"))
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        self.payload["notice"]["message"] = '<img src=x onerror="window.attacked=true">'
        self.refresh()
        self.assertIn("<img", banner.inner_text())
        self.assertEqual(0, banner.locator("img").count())
        self.assertFalse(self.page.evaluate("Boolean(window.attacked)"))

    def test_update_disable_and_failures_preserve_unsaved_form_focus_and_modal(self):
        self.page.evaluate("""() => {
            const form = document.createElement('div');
            form.className = 'app-container';
            form.innerHTML = '<input id="draft" value="unsaved draft"><dialog open id="materials"><textarea id="draft-materials">unsaved materials</textarea></dialog>';
            document.body.append(form);
            document.getElementById('draft-materials').focus();
        }""")
        self.payload["notice"]["status"] = "in_progress"
        self.refresh()
        self.assertEqual("unsaved draft", self.page.locator("#draft").input_value())
        self.assertEqual("unsaved materials", self.page.locator("#draft-materials").input_value())
        self.assertEqual("draft-materials", self.page.evaluate("document.activeElement.id"))
        self.assertTrue(self.page.locator("#materials").evaluate("dialog => dialog.open"))
        self.assertIn("Maintenance in progress", self.page.locator("#maintenance-announcer").inner_text())
        self.failed = True
        self.refresh()
        self.assertTrue(self.page.locator("#maintenance-notice").is_visible())
        self.failed = False
        self.payload["notice"] = None
        self.refresh()
        self.assertTrue(self.page.locator("#maintenance-notice").is_hidden())
        self.assertEqual("unsaved materials", self.page.locator("#draft-materials").input_value())

    def test_unchanged_notice_does_not_repeat_live_announcement(self):
        self.page.evaluate("""() => {
            window.mutations = 0;
            new MutationObserver(records => window.mutations += records.length)
                .observe(document.getElementById('maintenance-announcer'), {childList: true, subtree: true});
        }""")
        self.refresh()
        self.assertEqual(0, self.page.evaluate("window.mutations"))

    def test_known_notice_expires_even_when_refreshes_fail(self):
        self.payload["notice"]["expires_at"] = (self.now + timedelta(seconds=1)).isoformat()
        self.refresh()
        self.failed = True
        self.page.locator("#maintenance-notice[hidden]").wait_for(state="attached", timeout=4000)
        self.assertIn("no longer active", self.page.locator("#maintenance-announcer").inner_text())

    def test_sleeping_device_wall_time_expires_notice_without_network(self):
        self.failed = True
        self.page.evaluate("""() => {
            const afterSleep = Date.now() + 24 * 60 * 60 * 1000;
            Date.now = () => afterSleep;
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        self.assertTrue(self.page.locator("#maintenance-notice").is_hidden())


if __name__ == "__main__":
    unittest.main()
