"""Actual profile UI with synthetic API responses; all external requests blocked."""
import json
from pathlib import Path
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect


class ClaudeBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.profile = dict(name='Synthetic', email='synthetic@example.invalid',
            base_resume_text='', ai_provider='anthropic', ai_model='claude-sonnet-5-5',
            anthropic_available=True, anthropic_api_key_configured=True,
            gemini_api_key_configured=True, openai_api_key_configured=True)
        self.writes = []
        self.secret_failure = False
        self.profile_failure = False
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.on('dialog', lambda dialog: dialog.dismiss())
        self.page.route('**/*', self.route)

    def route(self, route):
        request = route.request
        url = urlsplit(request.url)
        if url.netloc != 'claude.test':
            return route.abort()
        if url.path == '/' or url.path.startswith('/static/'):
            relative = 'index.html' if url.path == '/' else url.path.removeprefix('/static/')
            path = Path(__file__).parent / 'static' / relative
            if not path.is_file():
                return route.fulfill(status=404, body='')
            mime = {'.html':'text/html', '.js':'application/javascript', '.css':'text/css'}.get(path.suffix, 'application/octet-stream')
            return route.fulfill(body=path.read_bytes(), content_type=mime)
        payload = request.post_data_json if request.method in ('PUT', 'POST') else None
        if request.method in ('PUT', 'POST'):
            self.writes.append((url.path, payload))
        response = []
        status = 200
        if url.path == '/api/profile':
            if self.profile_failure:
                return route.fulfill(status=503, content_type='application/json', body='{}')
            if request.method == 'POST':
                self.profile.update(payload)
                response = {'success':True}
            else:
                response = self.profile
        elif url.path == '/api/profile/secrets':
            status = 503 if self.secret_failure else 200
            response = {'detail':'Could not save the key.'} if self.secret_failure else {
                'success':True, 'anthropic_api_key_configured':True,
                'gemini_api_key_configured':True, 'openai_api_key_configured':True}
        elif url.path == '/api/profile/ai-provider/validate':
            response = {'success':True, 'provider':'anthropic', 'model':'claude-sonnet-5-5', 'message':'Configuration works.'}
        elif url.path == '/api/session':
            response = {'authentication':'local', 'authenticated':False}
        elif url.path == '/api/version':
            response = {'build':'synthetic', 'version':'synthetic'}
        elif url.path == '/api/jobs/status':
            response = {'running':False}
        return route.fulfill(status=status, content_type='application/json', body=json.dumps(response))

    def open_profile(self):
        self.page.goto('https://claude.test/')
        self.page.get_by_role('tab', name='Profile & Resume', exact=True).click()
        expect(self.page.locator('#p-ai-provider')).to_have_value(self.profile['ai_provider'])
        expect(self.page.locator('#p-name')).to_have_value('Synthetic')

    def test_hosted_selection_saved_status_and_profile_reload(self):
        self.profile.update(ai_provider='openai', ai_model='gpt-5-mini')
        self.open_profile()
        self.page.locator('#p-ai-provider').select_option('anthropic')
        expect(self.page.locator('#test-ai-provider-btn')).to_be_disabled()
        expect(self.page.locator('#p-anthropic-apikey')).to_have_value('')
        expect(self.page.locator('#p-anthropic-key-status')).to_have_text('Saved')
        self.page.locator('#profile-form button[type=submit]').click()
        expect(self.page.locator('#test-ai-provider-btn')).to_be_enabled()
        profile_writes = [body for path, body in self.writes if path == '/api/profile']
        self.assertEqual('anthropic', profile_writes[-1]['ai_provider'])
        self.assertNotIn('anthropic_api_key', profile_writes[-1])
        self.page.reload()
        expect(self.page.locator('#p-ai-provider')).to_have_value('anthropic')

    def test_secret_save_and_failure_preserve_other_credentials(self):
        self.open_profile()
        key = self.page.locator('#p-anthropic-apikey')
        key.fill('synthetic-new-key')
        expect(self.page.locator('#test-ai-provider-btn')).to_be_disabled()
        self.secret_failure = True
        self.page.locator('#save-provider-secrets-btn').click()
        expect(self.page.locator('#provider-secret-save-status')).to_contain_text('Could not save')
        expect(key).to_have_value('synthetic-new-key')
        self.secret_failure = False
        self.page.locator('#save-provider-secrets-btn').click()
        expect(key).to_have_value('')
        expect(key).to_have_attribute('type', 'password')
        expect(self.page.locator('#p-gemini-key-status')).to_have_text('Saved')
        self.assertEqual({'anthropic_api_key':'synthetic-new-key'}, self.writes[-1][1])
        expect(self.page.locator('#test-ai-provider-btn')).to_be_enabled()
        self.page.locator('#test-ai-provider-btn').click()
        expect(self.page.locator('#ai-provider-test-status')).to_contain_text('Configuration works')
        self.assertNotIn('synthetic-new-key', self.page.locator('body').inner_text())

    def test_switch_to_claude_uses_reviewed_model(self):
        self.profile.update(ai_provider='openai', ai_model='gpt-5-mini')
        self.open_profile()
        self.page.locator('#p-ai-provider').select_option('anthropic')
        expect(self.page.locator('#p-ai-model')).to_have_value('claude-sonnet-5-5')
        expect(self.page.locator('#test-ai-provider-btn')).to_be_disabled()
        link = self.page.locator('[data-credential-provider=anthropic]')
        expect(link).to_be_visible()
        expect(link).to_have_attribute('rel', 'noopener noreferrer')
        expect(link).to_have_attribute('referrerpolicy', 'no-referrer')

    def test_unavailable_claude_is_not_silently_replaced(self):
        self.profile['anthropic_available'] = False
        self.open_profile()
        expect(self.page.locator('#p-anthropic-apikey')).to_be_disabled()
        expect(self.page.locator('#p-ai-provider option[value=anthropic]')).to_be_disabled()
        expect(self.page.locator('#test-ai-provider-btn')).to_be_disabled()
        expect(self.page.locator('#profile-form button[type=submit]')).to_be_disabled()
        self.assertFalse(self.writes)
        expect(self.page.locator('#p-ai-provider')).to_have_value('anthropic')

    def test_unknown_provider_and_profile_failure_fail_closed(self):
        self.profile['ai_provider'] = 'retired-provider'
        self.open_profile()
        expect(self.page.locator('#save-profile-btn')).to_be_disabled()
        self.assertFalse(self.writes)
        self.profile['ai_provider'] = 'anthropic'
        self.page.evaluate('loadProfile()')
        expect(self.page.locator('#p-anthropic-apikey')).to_be_enabled()
        self.profile_failure = True
        self.page.evaluate('loadProfile()')
        expect(self.page.locator('#p-anthropic-apikey')).to_be_disabled()
        expect(self.page.locator('#save-profile-btn')).to_be_disabled()
        expect(self.page.locator('#test-ai-provider-btn')).to_be_disabled()
        expect(self.page.locator('#p-ai-provider')).to_have_value('anthropic')

    def test_mobile_key_controls_fit_and_keyboard_toggle_works(self):
        self.page.set_viewport_size({'width':390, 'height':844})
        self.open_profile()
        key = self.page.locator('#p-anthropic-apikey')
        key.fill('synthetic-key')
        toggle = self.page.locator('#toggle-anthropic-api-visibility')
        toggle.focus()
        toggle.press('Enter')
        expect(key).to_have_attribute('type', 'text')
        toggle.press('Enter')
        expect(key).to_have_attribute('type', 'password')
        self.assertTrue(self.page.locator('#provider-secrets-form').evaluate(
            'el => el.scrollWidth <= el.clientWidth && el.getBoundingClientRect().right <= window.innerWidth'))
