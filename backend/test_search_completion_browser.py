"""Exercise the actual completion renderer with synthetic results, offline."""
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright
from search_run_repository import minimized_search_result


class SearchCompletionBrowserTests(unittest.TestCase):
    def test_saved_job_counts_separate_score_and_other_filters(self):
        source = (Path(__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        render = source[source.index("function renderFilteredJobs("):source.index("function buildJobRow(")]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.route("**/*", lambda route: route.abort())
                page.set_content('<select id="score"><option>40</option><option>70</option></select><p id="job-result-count"></p><p id="status"></p><div id="rows"></div>')
                page.add_script_tag(content="""
                    const jobsTableBody = document.getElementById('rows');
                    const jobMinScore = document.getElementById('score');
                    const jobResultsStatus = document.getElementById('status');
                    const jobStatusFilter = {value: 'matched'}, jobSortOrder = {value: 'score_desc'};
                    const loadedJobs = [{match_score: 80, status: 'matched'}, {match_score: 60, status: 'matched'},
                        {match_score: 90, status: 'applied'}, {match_score: null, status: 'matched'}];
                    function currentAdvancedJobFilters() { return {}; }
                    function jobMatchesAdvancedFilters() { return true; }
                    function updateAdvancedJobFilterSummary() { return 0; }
                    function buildJobRow() { return document.createElement('div'); }
                """ + render)
                page.evaluate('renderFilteredJobs()')
                self.assertIn('4 saved jobs before filtering; 4 remain', page.locator('#job-result-count').inner_text())
                self.assertIn('3 shown after all filters', page.locator('#job-result-count').inner_text())
                page.select_option('#score', '70')
                page.evaluate('renderFilteredJobs()')
                self.assertIn('4 saved jobs before filtering; 3 remain', page.locator('#job-result-count').inner_text())
                self.assertIn('2 shown after all filters', page.locator('#job-result-count').inner_text())
                self.assertIn('Includes 1 unscored jobs', page.locator('#job-result-count').inner_text())
                self.assertEqual(page.locator('#job-result-count').inner_text(), page.locator('#status').inner_text())
            finally:
                browser.close()

    def test_search_stop_respects_enqueue_and_operation_boundaries(self):
        source = (Path(__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        stop = source[source.index("function showCancellableLoading("):source.index("function showFullBackupModal(")]
        search = source[source.index("async function searchJobs("):source.index("function renderProviderAlerts(")]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for case in ('early_stop', 'repeated_stop', 'failed_start', 'replaced_operation', 'normal_stop', 'already_active'):
                    with self.subTest(case=case):
                        page = browser.new_page()
                        page.route("**/*", lambda route: route.abort())
                        page.set_content('<div id="overlay" hidden><h2 id="title"></h2><div id="subtitle"></div><button id="stop">Stop</button></div>')
                        page.add_script_tag(content="""
                            const API_URL = '';
                            let searchTitlePlanTimer = null;
                            async function updateSearchTitlePlan() { return true; }
                            function usesTitleAlternatives() { return false; }
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
                            let originalOperation;
                        """)
                        page.wait_for_function('activeLoadingOperation !== null')
                        page.evaluate('originalOperation = activeLoadingOperation')
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
                            if case == 'failed_start' else {'success': False, 'active': True}
                            if case == 'already_active' else {'success': True},
                        )
                        if case == 'normal_stop':
                            page.locator('#stop').click()
                            page.evaluate('stopActiveLoadingOperation()')
                        if case == 'already_active':
                            self.assertEqual(1, page.evaluate('requests.length'))
                            self.assertTrue(page.locator('#stop').is_enabled())
                            self.assertEqual('common.stop', page.locator('#stop').inner_text().strip())
                            self.assertFalse(page.evaluate('activeLoadingOperation.stopRequested'))
                            page.locator('#stop').click()
                            self.assertEqual('/api/jobs/search/cancel', page.evaluate('requests[1].url'))
                        elif case in ('failed_start', 'replaced_operation'):
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
                for result, expected in (
                    ({'success': True, 'message': 'No matching results.',
                      'counts': {'discovered': 0, 'scored': 0, 'meeting_minimum': 0, 'saved': 0},
                      'minimum_match_score': 40, 'source_counts': {'confirmed_empty': 15, 'failed': 0, 'unusable': 0, 'limited': 0}},
                     '15 source checks completed with no matching results'),
                    ({'success': True, 'partial': True, 'message': 'Partial search.',
                      'counts': {'discovered': 12, 'scored': 8, 'meeting_minimum': 5, 'saved': 4},
                      'minimum_match_score': 40, 'source_counts': {'confirmed_empty': 3, 'failed': 2, 'unusable': 2, 'limited': 1}},
                     '12 candidate jobs found; 8 scored; 5 met the minimum match (40%); 4 new jobs saved'),
                    ({'success': False, 'error': 'Sources unavailable.',
                      'counts': {'discovered': 0, 'saved': 0}},
                     'minimum-match counts unavailable for this search'),
                ):
                    page.evaluate('result => renderProviderAlerts(result)', result)
                    self.assertIn(expected, page.locator('#notes').inner_text())
                    if result.get('partial'):
                        self.assertIn('3 source checks completed with no matching results; 2 source checks failed', page.locator('#notes').inner_text())
                        self.assertIn('Counts reflect only completed work', page.locator('#notes').inner_text())
                    if result.get('source_counts', {}).get('failed') == 0:
                        self.assertNotIn('source checks failed', page.locator('#notes').inner_text())
                page.evaluate("renderProviderAlerts({success: true, message: '<img src=x onerror=alert(1)>', provider_alerts: []})")
                self.assertEqual(0, page.locator('#notes img').count())
                self.assertIn('<img', page.locator('#notes').inner_text())
                page.get_by_role('button', name='Dismiss this source notice').click()
                self.assertTrue(page.locator('#notes').is_hidden())
                self.assertEqual('history', page.evaluate('document.activeElement.id'))
            finally:
                browser.close()
