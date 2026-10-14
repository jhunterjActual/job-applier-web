"""Feedback journeys using real assets, synthetic responses, and blocked networking."""
import json
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright, expect


STATIC = Path(__file__).parent / 'static'
ORIGIN = 'https://feedback.test'
REPOSITORY = 'https://github.com/jhunterjActual/job-applier-web'


class FeedbackBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 320, 'height': 720})
        self.addCleanup(self.page.close)
        self.page.route('**/*', self.route)

    def route(self, route):
        url = urlsplit(route.request.url)
        if url.netloc != 'feedback.test':
            return route.abort()
        relative = {'/': 'index.html', '/feedback': 'feedback.html',
                    '/auth/sign-in': 'sign-in.html'}.get(url.path)
        if url.path.startswith('/static/'):
            relative = url.path.removeprefix('/static/')
        if relative:
            path = STATIC / relative
            if not path.is_file():
                return route.fulfill(status=404, body='')
            mime = {'.html': 'text/html', '.js': 'application/javascript',
                    '.css': 'text/css'}.get(path.suffix, 'application/octet-stream')
            return route.fulfill(body=path.read_bytes(), content_type=mime)
        response = []
        if url.path == '/api/session':
            response = {'authentication': 'local', 'authenticated': False}
        elif url.path == '/api/version':
            response = {'build': 'synthetic', 'version': 'synthetic'}
        elif url.path == '/api/jobs/status':
            response = {'running': False}
        elif url.path == '/api/maintenance-notice':
            response = {'notice': None}
        elif url.path == '/api/profile':
            response = {'name': 'Synthetic', 'ai_provider': 'openai'}
        return route.fulfill(content_type='application/json', body=json.dumps(response))

    def open_feedback(self, query=''):
        self.page.goto(ORIGIN + '/feedback' + query)
        expect(self.page.get_by_role('heading', name='Help & feedback', exact=True)).to_be_visible()

    def test_contact_destinations_and_public_notice(self):
        self.open_feedback()
        draft = urlsplit(self.page.locator('#email-draft').get_attribute('href'))
        self.assertEqual('mailto', draft.scheme)
        self.assertEqual('feedback@jhunterj.com', draft.path)
        self.assertIn('What happened?', parse_qs(draft.query)['body'][0])
        issue = self.page.get_by_role('link', name='Create GitHub issue')
        expect(issue).to_have_attribute('href', REPOSITORY + '/issues/new/choose')
        expect(issue).to_have_attribute('target', '_blank')
        expect(issue).to_have_attribute('rel', 'noopener noreferrer')
        expect(self.page.get_by_role('link', name='Browse existing issues')).to_have_attribute('href', REPOSITORY + '/issues')
        expect(self.page.get_by_text('Public · GitHub account required', exact=True)).to_be_visible()

    def test_only_allowlisted_context_changes_subject_without_reflecting_query(self):
        for source, suffix in [('sign-in', 'Sign-in help'), ('access', 'Access question'),
                               ('dashboard', 'Feedback'), ('unknown-private-value', 'Feedback'),
                               ('__proto__', 'Feedback')]:
            with self.subTest(source=source):
                self.open_feedback('?from=' + source + '&token=private-canary&email=private-canary')
                href = self.page.locator('#email-draft').get_attribute('href')
                self.assertEqual('CareerTrellis — ' + suffix, parse_qs(urlsplit(href).query)['subject'][0])
                self.assertNotIn('private-canary', href)
                self.assertNotIn('private-canary', self.page.locator('body').inner_text())
                self.assertNotIn('unknown-private-value', href)

    def test_clipboard_success_announces_exact_value(self):
        self.page.add_init_script("""Object.defineProperty(navigator, 'clipboard', {value: {
            writeText: async value => { window.copiedFeedback = value; }
        }});""")
        self.open_feedback()
        self.page.get_by_role('button', name='Copy email address').click()
        expect(self.page.get_by_role('status')).to_have_text('Email address copied.')
        self.assertEqual('feedback@jhunterj.com', self.page.evaluate('window.copiedFeedback'))
        self.page.locator('summary').click()
        self.page.get_by_role('button', name='Copy suggested message').click()
        expect(self.page.get_by_role('status')).to_contain_text('Suggested message copied.')
        self.assertEqual(self.page.locator('#suggested-message').input_value(), self.page.evaluate('window.copiedFeedback'))
        expect(self.page.get_by_role('button', name='Copy suggested message')).to_be_enabled()

    def test_clipboard_denied_retains_selectable_text_and_reenabled_controls(self):
        self.page.add_init_script("""Object.defineProperty(navigator, 'clipboard', {value: {
            writeText: async () => { throw new Error('Synthetic denied permission'); }
        }});""")
        self.open_feedback()
        self.page.get_by_role('button', name='Copy email address').click()
        expect(self.page.get_by_role('status')).to_contain_text('Could not copy automatically.')
        expect(self.page.get_by_role('button', name='Copy email address')).to_be_enabled()
        self.page.locator('summary').click()
        self.page.get_by_role('button', name='Copy suggested message').click()
        expect(self.page.get_by_role('status')).to_contain_text('Select the suggested message')
        message = self.page.locator('#suggested-message')
        message.focus()
        self.page.keyboard.press('ControlOrMeta+A')
        self.assertEqual(message.input_value(), message.evaluate('element => element.value.slice(element.selectionStart, element.selectionEnd)'))
        expect(self.page.get_by_role('button', name='Copy suggested message')).to_be_enabled()

    def test_no_javascript_or_clipboard_keeps_contact_options_usable(self):
        context = self.browser.new_context(java_script_enabled=False, viewport={'width': 320, 'height': 720})
        self.addCleanup(context.close)
        page = context.new_page()
        page.route('**/*', self.route)
        page.goto(ORIGIN + '/feedback')
        expect(page.get_by_role('link', name='Open email draft')).to_be_visible()
        expect(page.get_by_role('link', name='Create GitHub issue')).to_be_visible()
        expect(page.get_by_role('link', name='feedback@jhunterj.com', exact=True)).to_be_visible()
        expect(page.locator('#copy-address')).to_be_hidden()
        page.locator('summary').click()
        expect(page.locator('#suggested-message')).to_be_visible()
        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: undefined});")
        self.open_feedback()
        expect(self.page.locator('#copy-address')).to_be_hidden()
        expect(self.page.locator('#email-draft')).to_be_visible()

    def test_mobile_layout_keyboard_skip_and_focus(self):
        self.open_feedback()
        for width in (320, 390, 768, 1280):
            self.page.set_viewport_size({'width': width, 'height': 720})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            for link in (self.page.locator('#email-draft'), self.page.get_by_role('link', name='Create GitHub issue')):
                bounds = link.bounding_box()
                self.assertGreaterEqual(bounds['height'], 44)
                self.assertGreaterEqual(bounds['x'], 0)
                self.assertLessEqual(bounds['x'] + bounds['width'], width)
        self.page.reload()
        self.page.keyboard.press('Tab')
        expect(self.page.get_by_role('link', name='Skip to content')).to_be_focused()
        self.page.keyboard.press('Enter')
        expect(self.page.locator('#main')).to_be_focused()
        self.page.keyboard.press('Tab')
        expect(self.page.locator('#email-draft')).to_be_focused()
        self.assertNotEqual('none', self.page.locator('#email-draft').evaluate('element => getComputedStyle(element).outlineStyle'))

    def test_dashboard_help_link_navigates_without_becoming_a_workspace_tab(self):
        self.page.goto(ORIGIN + '/')
        link = self.page.get_by_role('link', name='Help & feedback', exact=True)
        expect(link).to_be_visible()
        self.assertFalse(link.evaluate("element => Boolean(element.closest('[role=tablist]'))"))
        expect(link).to_have_attribute('href', '/feedback?from=dashboard')
        link.click()
        expect(self.page.get_by_role('heading', name='Help & feedback', exact=True)).to_be_visible()
        self.assertEqual('/feedback', urlsplit(self.page.url).path)


if __name__ == '__main__':
    unittest.main()
