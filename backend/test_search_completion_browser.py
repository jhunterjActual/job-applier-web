"""Exercise the actual completion renderer with synthetic results, offline."""
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright
from search_run_repository import minimized_search_result


class SearchCompletionBrowserTests(unittest.TestCase):
    def test_search_stop_respects_enqueue_and_operation_boundaries(self):
        source = (Path(__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        stop = source[source.index("function showCancellableLoading("):source.index("function showFullBackupModal(")]
        search = source[source.index("async function searchJobs("):source.index("function renderProviderAlerts(")]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for case in ('early_stop', 'repeated_stop', 'failed_start', 'replaced_operation', 'normal_stop'):
                    with self.subTest(case=case):
                        page = browser.new_page()
                        page.route("**/*", lambda route: route.abort())
                        page.set_content('<div id="overlay" hidden><h2 id="title"></h2><div id="subtitle"></div><button id="stop">Stop</button></div>')
                        page.add_script_tag(content="""
                            const API_URL = '';
                            let activeLoadingOperation = null;
                            const stopLoadingBtn = document.getElementById('stop');
                            const loadingTitle = document.getElementById('title');
                            const loadingSubtitle = document.getElementById('subtitle');
                            const sKeywords = {value: 'Synthetic Engineer'}, sLocation = {value: 'remote'};
                            const saveSearchCheckbox = {checked: false}, savedSearchName = {value: ''};
                            const savedSearchSelect = {value: ''}, savedSearchFrequency = {value: 'none'};
                            const requests = [], alerts = [];
                            let pollingCalls = 0, resolveStart;
                            if (!crypto.randomUUID) crypto.randomUUID = () => '00000000-0000-4000-8000-000000000001';
                            function showLoading(title, subtitle, operation) {
                                activeLoadingOperation = operation;
                                document.getElementById('overlay').hidden = false;
                                loadingTitle.textContent = title; loadingSubtitle.textContent = subtitle;
                            }
                            function hideLoading() {
                                activeLoadingOperation = null;
                                document.getElementById('overlay').hidden = true;
                            }
                            function t(key) { return key; }
                            function createElement(tag, classes, text) {
                                const node = document.createElement(tag); node.className = classes;
                                node.textContent = text || ''; return node;
                            }
                            function logActivity() {} function alert(message) { alerts.push(message); }
                            async function loadSavedSearches() {} async function checkDueSavedSearches() {}
                            function startSearchStatusPolling() { pollingCalls += 1; }
                            window.fetch = async (url, options) => {
                                requests.push({url, method: options.method});
                                if (url === '/api/jobs/search') {
                                    return await new Promise(resolve => { resolveStart = resolve; });
                                }
                                return {ok: true, json: async () => ({success: true, active: true})};
                            };
                            function settleStart(result, ok = true) {
                                resolveStart({ok, json: async () => result});
                                return searchPromise;
                            }
                        """ + stop + search + """
                            stopLoadingBtn.addEventListener('click', () => void stopActiveLoadingOperation());
                            const searchPromise = searchJobs({preventDefault() {}});
                            const originalOperation = activeLoadingOperation;
                        """)
                        if case != 'normal_stop':
                            page.locator('#stop').click()
                            self.assertEqual(1, page.evaluate('requests.length'))
                            self.assertTrue(page.locator('#stop').is_disabled())
                        if case == 'repeated_stop':
                            page.evaluate('stopActiveLoadingOperation()')
                            self.assertEqual(1, page.evaluate('requests.length'))
                        if case == 'replaced_operation':
                            page.evaluate("""() => {
                                activeLoadingOperation = {kind: 'search', id: 'new-operation', stopRequested: false};
                                loadingTitle.textContent = 'New operation';
                                stopLoadingBtn.disabled = false;
                            }""")
                        page.evaluate(
                            "result => settleStart(result, result.success)",
                            {'success': False, 'message': 'Synthetic queue failure'}
                            if case == 'failed_start' else {'success': True},
                        )
                        if case == 'normal_stop':
                            page.locator('#stop').click()
                            page.evaluate('stopActiveLoadingOperation()')
                        if case in ('failed_start', 'replaced_operation'):
                            self.assertEqual(1, page.evaluate('requests.length'))
                            self.assertEqual(0, page.evaluate('pollingCalls'))
                            if case == 'failed_start':
                                self.assertTrue(page.locator('#overlay').is_hidden())
                            else:
                                self.assertEqual('new-operation', page.evaluate('activeLoadingOperation.id'))
                                self.assertEqual('New operation', page.locator('#title').inner_text())
                                self.assertTrue(page.locator('#stop').is_enabled())
                        else:
                            page.wait_for_function('requests.length === 2')
                            self.assertEqual(
                                page.evaluate("`/api/operations/${originalOperation.id}/cancel`"),
                                page.evaluate('requests[1].url'),
                            )
                            self.assertEqual('POST', page.evaluate('requests[1].method'))
                            self.assertEqual(1, page.evaluate('pollingCalls'))
                        page.close()
            finally:
                browser.close()

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
                page.evaluate("finishObservedSearch({last_result: {success: true, cancelled: true, message: 'Search stopped; previously saved matches are retained.', provider_alerts: []}}, searchStatusPollGeneration)")
                self.assertTrue(page.locator('#overlay').is_hidden())
                self.assertIn('Search stopped', page.locator('#notes').inner_text())
                page.evaluate("renderProviderAlerts({success: true, message: '<img src=x onerror=alert(1)>', provider_alerts: []})")
                self.assertEqual(0, page.locator('#notes img').count())
                self.assertIn('<img', page.locator('#notes').inner_text())
                page.get_by_role('button', name='Dismiss this source notice').click()
                self.assertTrue(page.locator('#notes').is_hidden())
                self.assertEqual('history', page.evaluate('document.activeElement.id'))
            finally:
                browser.close()
