"""Exercise the actual completion renderer with synthetic results, offline."""
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright
from search_run_repository import minimized_search_result


class SearchCompletionBrowserTests(unittest.TestCase):
    def test_completion_survives_overlay_close_and_uses_plain_text(self):
        source = (Path(__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        finish = source[source.index("async function finishObservedSearch("):source.index("async function pollSearchStatus(")]
        render = source[source.index("function renderProviderAlerts("):source.index("const DIAGNOSTIC_CODE_LABELS")]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.route("**/*", lambda route: route.abort())
                page.set_content('<div id="overlay">Searching</div><section id="notes" hidden></section><button id="history">History</button>')
                page.add_script_tag(content="""
                    let searchStatusPollGeneration = 0, searchWasObservedActive = true;
                    let lastAnnouncedSearchPhase = null, searchStatusPollTimer = null;
                    let activeLoadingOperation = {kind: 'search'};
                    const providerAlerts = document.getElementById('notes');
                    const openSourceDiagnosticsBtn = document.getElementById('history');
                    function hideLoading() { document.getElementById('overlay').hidden = true; }
                    async function loadJobs() {} async function updateDashboardStats() {}
                    async function refreshSourceDiagnosticCount() {}
                    function logActivity() {} function alert() {} function announce() {}
                    function showSourceDiagnostics() {} function t() { return 'Search failed'; }
                    function createElement(tag, classes, text) {
                        const node = document.createElement(tag); node.className = classes;
                        node.textContent = text || ''; return node;
                    }
                    function createActionButton(label, icon, classes, action) {
                        const button = createElement('button', classes, label);
                        button.onclick = action; return button;
                    }
                """ + finish + render)
                for outcome in ('source_unavailable', 'no_results', 'no_new_matches', 'partial_results', 'matches_saved'):
                    result = minimized_search_result({
                        'success': outcome != 'source_unavailable', 'outcome': outcome,
                        'jobs_added': 2 if outcome == 'matches_saved' else 0,
                    })
                    page.evaluate("result => finishObservedSearch({last_result: result}, searchStatusPollGeneration)", result)
                    self.assertTrue(page.locator('#overlay').is_hidden())
                    self.assertTrue(page.locator('#notes').is_visible())
                    self.assertIn(result.get('message') or result['error'], page.locator('#notes').inner_text())
                page.evaluate("renderProviderAlerts({success: true, message: '<img src=x onerror=alert(1)>', provider_alerts: []})")
                self.assertEqual(0, page.locator('#notes img').count())
                self.assertIn('<img', page.locator('#notes').inner_text())
                page.get_by_role('button', name='Dismiss this source notice').click()
                self.assertTrue(page.locator('#notes').is_hidden())
                self.assertEqual('history', page.evaluate('document.activeElement.id'))
            finally:
                browser.close()
