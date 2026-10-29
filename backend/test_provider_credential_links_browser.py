"""Existing profile UI, synthetic responses, no outbound provider navigation."""
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
import test_claude_browser as harness


class ProviderCredentialLinkBrowserTests(unittest.TestCase):
    setUpClass = classmethod(harness.ClaudeBrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(harness.ClaudeBrowserTests.tearDownClass.__func__)
    open_profile = harness.ClaudeBrowserTests.open_profile

    LINKS = {
        "gemini": ("p-apikey", "https://aistudio.google.com/app/apikey"),
        "openai": ("p-openai-apikey", "https://platform.openai.com/api-keys"),
        "anthropic": ("p-anthropic-apikey", "https://platform.claude.com/settings/keys"),
        "google_maps": ("p-google-apikey", "https://console.cloud.google.com/apis/credentials"),
    }

    def setUp(self):
        harness.ClaudeBrowserTests.setUp(self)
        self.profile.update(maps_provider="google", google_maps_api_key_configured=True)
        self.navigations = []
        self.mutations = []
        # Popup first navigation is routed at context scope. No real destination
        # or account is contacted, including on failed assertions.
        self.page.context.route("**/*", self.route_context)

    def route(self, route):
        request = route.request
        cached_title_preview = (request.method == "POST"
            and urlsplit(request.url).path == "/api/jobs/search/plan")
        if request.method not in {"GET", "HEAD"} and not cached_title_preview:
            self.mutations.append((request.method, urlsplit(request.url).path))
        return harness.ClaudeBrowserTests.route(self, route)

    def route_context(self, route):
        if urlsplit(route.request.url).netloc == "claude.test":
            return self.route(route)
        if route.request.url not in {row[1] for row in self.LINKS.values()}:
            return route.abort()
        if route.request.method != "GET":
            self.mutations.append((route.request.method, "external_management_navigation"))
            return route.abort()
        self.navigations.append({"url": route.request.url, "headers": route.request.headers})
        return route.fulfill(content_type="text/html", body="<title>Synthetic management destination</title>")

    def assert_links(self, activate=False):
        for provider, (_, destination) in self.LINKS.items():
            with self.subTest(provider=provider, activate=activate):
                link = self.page.locator(f'[data-credential-provider="{provider}"]')
                expect(link).to_be_visible()
                expect(link).to_have_attribute("href", destination)
                expect(link).to_have_attribute("target", "_blank")
                expect(link).to_have_attribute("rel", "noopener noreferrer")
                expect(link).to_have_attribute("referrerpolicy", "no-referrer")
                self.assertIn("opens in a new tab", link.get_attribute("aria-label"))
                self.assertNotIn("SENTINEL", link.get_attribute("href"))
                if activate:
                    link.focus()
                    with self.page.expect_popup() as popup_event:
                        link.press("Enter")
                    popup = popup_event.value
                    try:
                        popup.wait_for_load_state("domcontentloaded")
                        self.assertEqual(destination, popup.url)
                        self.assertTrue(popup.evaluate("window.opener === null"))
                        self.assertEqual(destination, self.navigations[-1]["url"])
                        self.assertNotIn("referer", self.navigations[-1]["headers"])
                    finally:
                        popup.close()

    def test_empty_saved_pending_and_cleared_keys_keep_private_keyboard_links(self):
        for configured in (False, True):
            with self.subTest(configured=configured):
                for provider in self.LINKS:
                    self.profile[provider + "_api_key_configured"] = configured
                self.open_profile()
                self.page.locator("#p-name").fill("PROFILE_SENTINEL")
                self.page.locator("#p-email").fill("PROFILE_SENTINEL@example.invalid")
                self.assert_links(activate=True)
        for input_id, _ in self.LINKS.values():
            self.page.locator("#" + input_id).fill("PRIVATE_KEY_SENTINEL")
        self.assert_links(activate=True)
        for input_id, _ in self.LINKS.values():
            self.page.locator("#" + input_id).fill("")
        self.assert_links(activate=True)
        self.assertEqual(16, len(self.navigations))
        self.assertFalse(self.writes)
        self.assertFalse(self.mutations)

    def test_provider_switch_unavailability_and_keyless_maps(self):
        self.open_profile()
        for provider in ("gemini", "openai", "anthropic"):
            self.page.locator("#p-ai-provider").select_option(provider)
            self.assert_links()
        self.profile["anthropic_available"] = False
        self.page.reload()
        self.page.get_by_role("tab", name="Profile & Resume", exact=True).click()
        expect(self.page.locator("#p-anthropic-apikey")).to_be_disabled()
        self.assert_links()
        self.page.locator("#p-maps-provider").select_option("openstreetmap")
        expect(self.page.locator('[data-credential-provider="google_maps"]')).to_be_hidden()
        self.page.locator("#p-maps-provider").select_option("google")
        self.assert_links()
        self.assertFalse(self.writes)
        self.assertFalse(self.mutations)
        self.assertFalse(self.navigations)


if __name__ == "__main__":
    unittest.main()
