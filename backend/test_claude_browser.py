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
        self.title_previews = []
        self.secret_failure = False
        self.profile_failure = False
        self.validation_status = 200
        self.validation_result = {'success':True, 'provider':'anthropic',
            'model':'claude-sonnet-5-5', 'message':'Configuration works.'}
        self.validation_deferred = False
        self.validation_routes = []
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
        if request.method == 'POST' and url.path == '/api/jobs/search/plan':
            # This endpoint reads cached titles; it neither saves nor starts a search.
            self.title_previews.append(payload)
            response = {'mode':'resume_suggested', 'titles':[],
                'max_titles':3, 'software_alias':False}
            return route.fulfill(content_type='application/json', body=json.dumps(response))
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
            if self.validation_deferred:
                self.validation_routes.append(route)
                return
            status = self.validation_status
            response = self.validation_result
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
        expect(self.page.locator('#search-title-plan')).to_contain_text('Resume-suggested titles will be prepared')
        self.assertEqual([{'keywords':'', 'title_alternatives':False}], self.title_previews)
        self.assertFalse(self.writes)
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

    def test_cost_disclosure_and_provider_specific_model_suggestions(self):
        self.open_profile()
        expect(self.page.locator('#ai-provider-cost-help')).to_contain_text('may charge your API account')
        expect(self.page.locator('#test-ai-provider-btn')).to_have_attribute(
            'aria-describedby', 'ai-provider-cost-help')
        expect(self.page.locator('#ai-model-capability-help')).to_contain_text('separate image support')
        self.assertFalse(self.writes)
        for provider, model in [('gemini', 'gemini-2.5-flash'), ('openai', 'gpt-5-mini'),
                                ('anthropic', 'claude-sonnet-5-5')]:
            self.page.locator('#p-ai-provider').select_option(provider)
            self.assertEqual([model], self.page.locator('#ai-model-suggestions option').evaluate_all(
                'options => options.map(option => option.value)'))
            expect(self.page.locator('#p-ai-model')).to_have_value(model)
            for key in ('gemini', 'openai', 'anthropic'):
                expect(self.page.locator(f'#p-{key}-key-status')).to_have_text('Saved')
        self.assertFalse(self.writes)

    def test_validation_guard_blocks_unsaved_missing_and_pending_keys(self):
        self.open_profile()
        self.page.locator('#p-ai-provider').select_option('openai')
        self.page.evaluate('testSavedAIProvider()')
        self.assertFalse(self.writes)
        self.page.reload()
        self.page.get_by_role('tab', name='Profile & Resume', exact=True).click()
        expect(self.page.locator('#test-ai-provider-btn')).to_be_enabled()
        self.page.locator('#p-anthropic-apikey').fill('synthetic-pending-key')
        self.page.evaluate('testSavedAIProvider()')
        self.assertFalse(self.writes)
        self.profile['anthropic_api_key_configured'] = False
        self.page.reload()
        self.page.get_by_role('tab', name='Profile & Resume', exact=True).click()
        expect(self.page.locator('#test-ai-provider-btn')).to_be_disabled()
        self.page.evaluate('testSavedAIProvider()')
        self.assertFalse(self.writes)

    def test_duplicate_validation_dispatch_is_blocked_and_button_recovers(self):
        self.open_profile()
        self.page.evaluate('Promise.all([testSavedAIProvider(), testSavedAIProvider()])')
        requests = [path for path, _ in self.writes if path == '/api/profile/ai-provider/validate']
        self.assertEqual(1, len(requests))
        expect(self.page.locator('#test-ai-provider-btn')).to_be_enabled()
        expect(self.page.locator('#ai-provider-test-status')).to_contain_text('Configuration works')

    def test_keyboard_focus_returns_after_provider_test(self):
        for result, status in [({'success': True, 'provider': 'anthropic',
                                'model': 'synthetic', 'message': 'Configuration works.'}, 200),
                               ({'detail': 'Synthetic provider failure.'}, 422),
                               ({'cancelled': True, 'message': 'AI provider test stopped.'}, 200)]:
            with self.subTest(status=status, result=result):
                self.validation_result = result
                self.validation_status = status
                self.open_profile()
                button = self.page.locator('#test-ai-provider-btn')
                button.focus()
                button.press('Enter')
                expect(self.page.locator('#loading-modal')).not_to_have_class('modal modal-sm active')
                expect(button).to_be_enabled()
                expect(button).to_be_focused()

    def test_model_guidance_is_accessible_on_desktop_and_mobile(self):
        for width, height in [(1280, 900), (900, 900), (390, 844), (320, 800)]:
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.open_profile()
                model = self.page.locator('#p-ai-model')
                expect(model).to_have_attribute('aria-describedby',
                    'ai-model-general-help ai-model-capability-help')
                expect(model).to_have_accessible_description(
                    'Use a model available to your key that supports structured JSON output. '
                    'Claude uses the fixed model shown above. The test checks structured output; '
                    'scanned-PDF OCR needs separate image support and consent.')
                model.focus()
                expect(model).to_be_focused()
                self.assertTrue(model.evaluate(
                    'el => el.getBoundingClientRect().left >= 0 && el.getBoundingClientRect().right <= window.innerWidth'))
                if width <= 900:
                    self.assertGreaterEqual(self.page.locator('#profile-form').evaluate(
                        'el => el.getBoundingClientRect().width'), width - 120)
                    cards = self.page.locator('#tab-profile .settings-grid > .card')
                    first, second = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
                    self.assertGreaterEqual(second['y'], first['y'] + first['height'])
                self.assertFalse(self.writes)

    def test_keyboard_stays_in_provider_test_dialog_until_completion(self):
        self.validation_deferred = True
        self.open_profile()
        button = self.page.locator('#test-ai-provider-btn')
        button.focus()
        button.press('Enter')
        stop = self.page.locator('#stop-loading-btn')
        expect(stop).to_be_focused()
        self.page.keyboard.press('Tab')
        expect(stop).to_be_focused()
        self.page.keyboard.press('Shift+Tab')
        expect(stop).to_be_focused()
        expect(button).to_be_disabled()
        self.assertEqual(1, len(self.validation_routes))
        self.validation_routes[0].fulfill(status=200, content_type='application/json',
            body=json.dumps(self.validation_result))
        expect(button).to_be_enabled()
        expect(button).to_be_focused()
