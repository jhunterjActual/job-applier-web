"""Offline only: no provider accounts, email, credentials or network calls."""

from concurrent.futures import ThreadPoolExecutor
import json
import html
import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import httpx

from signup_prototype.workos_rehearsal import (
    CALLBACK, COOKIE, HEADERS, ORIGIN, CorrelationProof, create_rehearsal_app, public_client,
)


def verified_response(**changes):
    values = {'id': 'user_synthetic000', 'email_verified': True}
    values.update(changes)
    return SimpleNamespace(user=SimpleNamespace(**values), impersonator=None,
                           access_token='synthetic-access', refresh_token='synthetic-refresh')


class ProtocolProofTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.client = Mock()
        self.state = 's' * 43
        self.browser = 'b' * 43
        self.client.user_management.get_authorization_url_with_pkce.return_value = {
            'url': 'https://api.workos.com/user_management/authorize?state=' + self.state,
            'state': self.state, 'code_verifier': 'v' * 43}
        self.client.user_management.authenticate_with_code_pkce.return_value = verified_response()
        self.proof = CorrelationProof(self.client, clock=lambda: self.now)

    def begin(self, purpose='signup', ttl=600):
        return self.proof.begin(self.browser, purpose, ttl)

    def finish(self):
        return self.proof.complete(self.browser, self.state, 'synthetic-code')

    def test_signup_binds_original_verifier_and_does_not_retain_identity_or_tokens(self):
        self.begin()
        self.assertEqual('signup_correlated', self.finish())
        self.client.user_management.authenticate_with_code_pkce.assert_called_once_with(
            code='synthetic-code', code_verifier='v' * 43,
            request_options={'max_retries': 0, 'timeout': 10})
        pending = self.proof.pending[self.state]
        self.assertEqual('', pending.verifier)
        self.assertNotIn('user_synthetic', str(vars(pending)))
        self.assertNotIn('synthetic-access', str(vars(pending)))

    def test_login_is_not_registration_intent(self):
        self.begin('login')
        self.assertEqual('verified_login_only', self.finish())

    def test_wrong_browser_or_state_never_exchanges_code(self):
        self.begin()
        self.assertEqual('invalid_flow', self.proof.complete('x' * 43, self.state, 'code'))
        self.assertEqual('invalid_flow', self.proof.complete(self.browser, 'x' * 43, 'code'))
        self.client.user_management.authenticate_with_code_pkce.assert_not_called()
        self.assertEqual('signup_correlated', self.finish())

    def test_callback_without_admission_never_exchanges(self):
        self.assertEqual('invalid_flow', self.finish())
        self.client.user_management.authenticate_with_code_pkce.assert_not_called()

    def test_expired_flow_cannot_exchange(self):
        self.begin(ttl=30)
        self.now += 30
        self.assertEqual('expired', self.finish())
        self.client.user_management.authenticate_with_code_pkce.assert_not_called()

    def test_expiry_during_exchange_is_not_a_success(self):
        self.begin(ttl=30)
        def exchange(**kwargs):
            self.now += 30
            return verified_response()
        self.client.user_management.authenticate_with_code_pkce.side_effect = exchange
        self.assertEqual('expired', self.finish())

    def test_duplicate_concurrent_callback_exchanges_once(self):
        self.begin()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.finish(), range(4)))
        self.assertEqual(4, results.count('signup_correlated'))
        self.client.user_management.authenticate_with_code_pkce.assert_called_once()
        self.assertEqual('invalid_flow', self.proof.complete(self.browser, self.state, 'changed-code'))

    def test_unverified_and_malformed_subjects_are_rejected(self):
        for response in (verified_response(email_verified=False), verified_response(id='bad'),
                         SimpleNamespace(user=verified_response().user, impersonator=object())):
            self.proof = CorrelationProof(self.client, clock=lambda: self.now)
            self.client.user_management.authenticate_with_code_pkce.return_value = response
            self.begin()
            self.assertEqual('identity_rejected', self.finish())

    def test_provider_exception_is_fixed_and_never_automatically_retried(self):
        self.begin()
        self.client.user_management.authenticate_with_code_pkce.side_effect = RuntimeError('sensitive provider text')
        self.assertEqual('exchange_unavailable', self.finish())
        self.assertEqual('exchange_unavailable', self.finish())
        self.client.user_management.authenticate_with_code_pkce.assert_called_once()

    def test_provider_cancellation_has_no_exchange(self):
        self.begin()
        self.assertEqual('provider_cancelled', self.proof.complete(self.browser, self.state, provider_error=True))
        self.assertEqual('invalid_flow', self.finish())
        self.client.user_management.authenticate_with_code_pkce.assert_not_called()

    def test_late_provider_error_does_not_overwrite_success(self):
        self.begin()
        self.assertEqual('signup_correlated', self.finish())
        self.assertEqual('signup_correlated', self.proof.complete(self.browser, self.state, provider_error=True))
        self.assertEqual('signup_correlated', self.proof.pending[self.state].outcome)
        self.client.user_management.authenticate_with_code_pkce.assert_called_once()


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.sdk = public_client('client_synthetic000')
        self.addCleanup(self.sdk.close)
        self.proof = CorrelationProof(self.sdk)
        self.web = TestClient(create_rehearsal_app(self.proof), base_url=ORIGIN)
        self.addCleanup(self.web.close)
        blocked = patch.object(self.sdk, 'request', side_effect=AssertionError('Provider network forbidden'))
        blocked.start()
        self.addCleanup(blocked.stop)

    def start(self, mode='signup'):
        page = self.web.get('/')
        csrf = re.search(r'name="csrf" value="([A-Za-z0-9_-]+)"', page.text).group(1)
        return self.web.post('/start', data={'csrf': csrf, 'mode': mode}, headers={'Origin': ORIGIN}, follow_redirects=False)

    def test_real_sdk_builds_signup_url_with_pkce_and_exact_callback(self):
        response = self.start()
        self.assertEqual(200, response.status_code)
        url = urlsplit(html.unescape(re.search(r'href="([^"]+)"', response.text).group(1)))
        params = parse_qs(url.query)
        self.assertEqual('api.workos.com', url.hostname)
        self.assertEqual([CALLBACK], params['redirect_uri'])
        self.assertEqual(['S256'], params['code_challenge_method'])
        self.assertEqual(['sign-up'], params['screen_hint'])
        self.assertEqual(['login'], params['prompt'])
        self.assertNotIn('code_verifier', params)
        self.assertNotIn('client_secret', params)

    def test_cookie_no_store_and_secret_free_clean_callback_result(self):
        self.start()
        state = next(iter(self.proof.pending))
        with patch.object(self.sdk.user_management, 'authenticate_with_code_pkce', return_value=verified_response()):
            response = self.web.get('/callback', params={'state': state, 'code': 'private-synthetic-code'}, follow_redirects=False)
        self.assertEqual('/result', response.headers['location'])
        result = self.web.get('/result')
        self.assertEqual('signup_correlated', result.json()['status'])
        for private in ('private-synthetic-code', state, 'user_synthetic000', 'synthetic-access'):
            self.assertNotIn(private, result.text)
        for key, value in HEADERS.items():
            self.assertEqual(value, result.headers[key])
        cookie = self.web.get('/').headers['set-cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=lax', cookie)

    def test_cross_origin_start_and_bad_csrf_are_rejected(self):
        self.web.get('/')
        for origin in ('https://example.test', ORIGIN):
            response = self.web.post('/start', data={'csrf': 'wrong', 'mode': 'signup'}, headers={'Origin': origin})
            self.assertEqual(403, response.status_code)
        self.assertEqual({}, self.proof.pending)

    def test_null_or_missing_origin_is_rejected_even_with_valid_form(self):
        page = self.web.get('/')
        self.assertEqual('same-origin', page.headers['referrer-policy'])
        csrf = re.search(r'name="csrf" value="([A-Za-z0-9_-]+)"', page.text).group(1)
        for headers in ({'Origin': 'null'}, {}):
            response = self.web.post('/start', data={'csrf': csrf, 'mode': 'signup'}, headers=headers)
            self.assertEqual(403, response.status_code)
            self.assertEqual('request_origin_rejected', response.json()['status'])
        self.assertEqual({}, self.proof.pending)

    def test_missing_cookie_error_is_actionable_and_sanitized(self):
        response = self.web.post('/start', data={'csrf': 'private-form', 'mode': 'signup'}, headers={'Origin': ORIGIN})
        self.assertEqual('browser_context_missing', response.json()['status'])
        self.assertNotIn('private-form', response.text)

    def test_wrong_host_and_oversized_requests_are_rejected(self):
        self.assertEqual(400, self.web.get('/', headers={'Host': 'example.test'}).status_code)
        self.assertEqual(400, self.web.get('/callback?state=' + 'x' * 5000).status_code)
        self.web.get('/')
        self.assertEqual(400, self.web.post('/start', content=b'x' * 513, headers={'Origin': ORIGIN}).status_code)

    def test_missing_browser_and_duplicate_query_do_not_exchange(self):
        self.start()
        state = next(iter(self.proof.pending))
        with patch.object(self.sdk.user_management, 'authenticate_with_code_pkce') as exchange:
            self.web.get('/callback', params=[('state', state), ('state', state), ('code', 'test')])
            self.assertEqual('invalid_flow', self.web.get('/result').json()['status'])
            self.web.cookies.clear()
            self.web.get('/callback', params={'state': state, 'code': 'test'})
            exchange.assert_not_called()

    def test_login_url_uses_signin_hint(self):
        response = self.start('login')
        url = html.unescape(re.search(r'href="([^"]+)"', response.text).group(1))
        self.assertEqual(['sign-in'], parse_qs(urlsplit(url).query)['screen_hint'])


@unittest.skipUnless(os.name == 'nt', 'Native Edge harness regression on Windows')
class NativeFormBrowserTests(unittest.TestCase):
    def test_edge_native_post_reproduces_legacy_403_and_passes_current_policy(self):
        # A fresh headless browser context exercises native form/cookie/referrer
        # behavior. Every request is intercepted into TestClient; no listener,
        # running operator process, real WorkOS account or external request is used.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            try:
                for legacy in (True, False):
                    with self.subTest(legacy=legacy):
                        sdk = public_client('client_synthetic000')
                        proof = CorrelationProof(sdk)
                        web = TestClient(create_rehearsal_app(proof), base_url=ORIGIN)
                        context = browser.new_context()
                        observed = []
                        def intercept(route):
                            request = route.request
                            parsed = urlsplit(request.url)
                            if parsed.scheme != 'http' or parsed.netloc != '127.0.0.1:8765':
                                route.abort()
                                return
                            response = web.request(request.method, request.url,
                                headers=request.headers, content=request.post_data_buffer,
                                follow_redirects=False)
                            headers = dict(response.headers)
                            if legacy and request.method == 'GET' and parsed.path == '/':
                                headers['referrer-policy'] = 'no-referrer'
                            if request.method == 'POST':
                                observed.append((request.headers.get('origin'), response.status_code))
                            route.fulfill(status=response.status_code, headers=headers, body=response.content)
                        try:
                            with patch.object(sdk, 'request', side_effect=AssertionError('Provider network forbidden')):
                                context.route('**/*', intercept)
                                page = context.new_page()
                                page.goto(ORIGIN + '/')
                                page.get_by_role('button', name='Start signup proof (10 minutes)', exact=True).click()
                                page.wait_for_url(ORIGIN + '/start')
                                if legacy:
                                    self.assertEqual([('null', 403)], observed)
                                    self.assertIn('request_origin_rejected', page.locator('body').inner_text())
                                    self.assertEqual({}, proof.pending)
                                else:
                                    self.assertEqual([(ORIGIN, 200)], observed)
                                    self.assertTrue(page.get_by_role('link', name='Continue to isolated AuthKit').is_visible())
                                    self.assertEqual(1, len(proof.pending))
                        finally:
                            context.close()
                            web.close()
                            sdk.close()
            finally:
                browser.close()

class SdkCredentialBoundaryTests(unittest.TestCase):
    def test_public_pkce_exchange_ignores_environment_api_key_and_destination(self):
        user = dict(object='user', id='user_synthetic000', email='synthetic@example.test',
                    email_verified=True, first_name=None, last_name=None, profile_picture_url=None,
                    external_id=None, last_sign_in_at=None, created_at='2026-09-07T00:00:00Z',
                    updated_at='2026-09-07T00:00:00Z')
        observed = []
        def send(client, request, **kwargs):
            observed.append(request)
            return httpx.Response(200, json={'user': user, 'access_token': 'test', 'refresh_token': 'test'}, request=request)
        with patch.dict(os.environ, {'WORKOS_API_KEY': 'synthetic-forbidden-key', 'WORKOS_BASE_URL': 'https://example.test'}):
            client = public_client('client_synthetic000')
            try:
                with patch('httpx.Client.send', new=send):
                    result = client.user_management.authenticate_with_code_pkce(code='test', code_verifier='v' * 43,
                        request_options={'max_retries': 0, 'timeout': 10})
                self.assertTrue(result.user.email_verified)
                self.assertEqual(1, len(observed))
                self.assertEqual('api.workos.com', observed[0].url.host)
                self.assertNotIn('authorization', observed[0].headers)
                self.assertNotIn('client_secret', json.loads(observed[0].content))
            finally:
                client.close()


if __name__ == '__main__':
    unittest.main()
