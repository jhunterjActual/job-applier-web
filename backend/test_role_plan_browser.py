"""Exercise actual title-preview and submit functions with deferred offline fetches."""
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright


class RolePlanBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        cls.script = source[source.index("let searchTitlePlanGeneration ="):
                            source.index("function renderProviderAlerts(")]
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.route("**/*", lambda route: route.abort())
        self.page.set_content('''
            <input id="s-keywords" value="Data Engineer">
            <input id="s-title-alternatives" type="checkbox" checked>
            <input id="s-location" value="Dayton OH">
            <small id="search-title-plan" role="status"></small>
        ''')
        self.page.add_script_tag(content="""
            const API_URL = '';
            const sKeywords = document.getElementById('s-keywords');
            const sLocation = document.getElementById('s-location');
            const saveSearchCheckbox = {checked: false}, savedSearchName = {value: ''};
            const savedSearchSelect = {value: ''}, savedSearchFrequency = {value: 'none'};
            let activeLoadingOperation = null;
            let loadingCalls = 0, pollingCalls = 0;
            const requests = [];
            function t(key) { return key; }
            function logActivity() {}
            function alert() {}
            function showCancellableLoading(title, subtitle, options) {
                loadingCalls += 1;
                activeLoadingOperation = {...options, id: 'synthetic-operation', stopRequested: false};
                return activeLoadingOperation;
            }
            function hideLoading() { activeLoadingOperation = null; }
            function operationHeaders(operation, headers) { return headers; }
            async function checkDueSavedSearches() {}
            async function loadSavedSearches() {}
            async function sendStopRequest() { throw new Error('Unexpected cancellation'); }
            function startSearchStatusPolling() { pollingCalls += 1; }
            window.fetch = (url, options) => new Promise((resolve, reject) => {
                requests.push({url, body: JSON.parse(options.body), resolve, reject});
            });
            function settle(index, body, ok = true) {
                requests[index].resolve({ok, json: async () => body});
            }
        """ + self.script + """
            sKeywords.addEventListener('input', scheduleSearchTitlePlan);
            document.getElementById('s-title-alternatives')
                .addEventListener('change', scheduleSearchTitlePlan);
        """)

    def tearDown(self):
        self.page.close()

    @staticmethod
    def plan(*titles):
        return {'mode': 'manual', 'titles': list(titles), 'software_alias': False}

    def settle(self, index, body, ok=True):
        self.page.evaluate("args => settle(args.index, args.body, args.ok)",
                           {'index': index, 'body': body, 'ok': ok})

    def submit(self):
        self.page.evaluate("() => { window.submission = searchJobs({preventDefault() {}}); }")
        self.assertEqual('/api/jobs/search/plan', self.page.evaluate('requests.at(-1).url'))

    def assert_no_enqueue(self):
        self.assertEqual(0, self.page.evaluate(
            "requests.filter(request => request.url === '/api/jobs/search').length"))
        self.assertEqual(0, self.page.evaluate('loadingCalls'))

    def test_out_of_order_preview_keeps_newest_text(self):
        self.page.evaluate("() => { window.firstPlan = updateSearchTitlePlan(); }")
        self.page.evaluate("""() => {
            sKeywords.value = 'Enterprise Architect';
            window.secondPlan = updateSearchTitlePlan();
        }""")
        self.settle(1, self.plan('Enterprise Architect'))
        self.assertTrue(self.page.evaluate('secondPlan'))
        newest = self.page.locator('#search-title-plan').inner_text()
        self.settle(0, self.plan('Data Engineer'))
        self.assertFalse(self.page.evaluate('firstPlan'))
        self.assertEqual(newest, self.page.locator('#search-title-plan').inner_text())
        self.assertIn('Enterprise Architect', newest)
        self.assert_no_enqueue()

    def test_resume_preview_renders_every_core_and_adjacent_title(self):
        titles = ['Data Architect', 'Chief Data Officer', 'Enterprise Architect',
                  'AI Architect', 'Data Governance Manager', 'AI Governance Manager']
        self.page.evaluate("() => { sKeywords.value = ''; window.plan = updateSearchTitlePlan(); }")
        self.settle(0, {'mode': 'resume_suggested', 'titles': titles, 'max_titles': 6, 'software_alias': False})
        self.assertTrue(self.page.evaluate('plan'))
        visible = self.page.locator('#search-title-plan').inner_text()
        for title in titles:
            self.assertIn(title, visible)
        self.assert_no_enqueue()

    def test_http_preview_failure_blocks_enqueue(self):
        self.submit()
        self.settle(0, {'detail': [{'msg': 'Use at most three different title alternatives.'}]}, False)
        self.page.evaluate('submission')
        self.assertIn('at most three', self.page.locator('#search-title-plan').inner_text())
        self.assert_no_enqueue()

    def test_transport_preview_failure_blocks_enqueue(self):
        self.submit()
        self.page.evaluate("requests[0].reject(new Error('Synthetic unavailable preview'))")
        self.page.evaluate('submission')
        self.assertIn('unavailable', self.page.locator('#search-title-plan').inner_text())
        self.assert_no_enqueue()

    def test_keyword_edit_during_submit_requires_fresh_submit(self):
        self.submit()
        self.page.evaluate("""() => {
            sKeywords.value = 'Platform Engineer';
            sKeywords.dispatchEvent(new Event('input'));
            clearTimeout(searchTitlePlanTimer);
        }""")
        self.settle(0, self.plan('Data Engineer'))
        self.page.evaluate('submission')
        self.assert_no_enqueue()
        self.assertNotIn('Data Engineer', self.page.locator('#search-title-plan').inner_text())
        self.submit()
        self.assertEqual('Platform Engineer', self.page.evaluate('requests[1].body.keywords'))
        self.settle(1, self.plan('Platform Engineer'))
        self.page.wait_for_function('requests.length === 3')
        self.assertEqual('/api/jobs/search', self.page.evaluate('requests[2].url'))
        self.assertEqual('Platform Engineer', self.page.evaluate('requests[2].body.keywords'))
        self.assertTrue(self.page.evaluate('requests[2].body.title_alternatives'))
        self.settle(2, {'success': True})
        self.page.evaluate('submission')
        self.assertEqual(1, self.page.evaluate('pollingCalls'))

    def test_mode_edit_during_submit_blocks_old_plan(self):
        self.submit()
        self.page.evaluate("""() => {
            const toggle = document.getElementById('s-title-alternatives');
            toggle.checked = false;
            toggle.dispatchEvent(new Event('change'));
            clearTimeout(searchTitlePlanTimer);
        }""")
        self.settle(0, self.plan('Data Engineer'))
        self.page.evaluate('submission')
        self.assert_no_enqueue()

    def test_latest_preview_displays_titles_as_plain_text(self):
        title = '<img src=x onerror="window.injected=true">'
        self.page.evaluate("value => { sKeywords.value = value; window.preview = updateSearchTitlePlan(); }", title)
        self.settle(0, self.plan(title))
        self.assertTrue(self.page.evaluate('preview'))
        self.assertIn(title, self.page.locator('#search-title-plan').inner_text())
        self.assertEqual(0, self.page.locator('#search-title-plan img').count())
        self.assertFalse(self.page.evaluate('window.injected === true'))
        self.assert_no_enqueue()

    def test_resume_suggested_preview_displays_actual_titles_safely(self):
        self.page.evaluate("() => { sKeywords.value = ''; window.preview = updateSearchTitlePlan(); }")
        self.settle(0, {'mode': 'resume_suggested', 'titles': ['Data Engineer', '<img src=x>'], 'software_alias': False})
        self.assertTrue(self.page.evaluate('preview'))
        text = self.page.locator('#search-title-plan').inner_text()
        self.assertIn('Resume-suggested role filter: Data Engineer OR <img src=x>', text)
        self.assertIn('before AI scoring', text)
        self.assertEqual(0, self.page.locator('#search-title-plan img').count())
        self.assert_no_enqueue()


if __name__ == '__main__':
    unittest.main()
