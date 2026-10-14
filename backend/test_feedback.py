"""Feedback stays reachable without authentication, storage, or provider calls."""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.requests import Request

import app
import signup_http
from web_auth import AuthenticationConfigurationError


STATIC = Path(__file__).parent / 'static'
PUBLIC_PATHS = ('/feedback', '/static/feedback.css', '/static/feedback.js')


class FeedbackBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(app.config, 'get_runtime_environment', return_value='staging'))
        self.stack.enter_context(patch.object(app.config, 'get_allowed_public_hosts', return_value={'feedback.test'}))
        self.stack.enter_context(patch.object(app.config, 'get_trusted_proxy_token', return_value='synthetic-proxy-token'))
        # No lifespan startup: never initialize or query the operator's database.
        self.client = TestClient(app.app, base_url='https://feedback.test',
                                 headers={app.TRUSTED_PROXY_TOKEN_HEADER: 'synthetic-proxy-token'})
        self.addCleanup(self.client.close)

    def test_public_files_ignore_unavailable_auth_and_malformed_sessions(self):
        with (
            patch.object(app, 'get_auth_configuration', side_effect=AuthenticationConfigurationError('synthetic unavailable')) as configuration,
            patch.object(signup_http, 'enabled', side_effect=AuthenticationConfigurationError('synthetic unavailable')) as enabled,
            patch.object(app, 'authenticate_session', side_effect=AssertionError('must not authenticate')) as authenticate,
            patch.object(app, 'check_postgres_ready', side_effect=AssertionError('must not query storage')) as database,
        ):
            for path in PUBLIC_PATHS:
                with self.subTest(path=path):
                    response = self.client.get(path, headers={'Cookie': 'ct_session=expired; ct_session=invalid'}, follow_redirects=False)
                    self.assertEqual(200, response.status_code)
                    self.assertNotIn('set-cookie', response.headers)
                    self.assertNotIn('location', response.headers)
                    self.assertEqual('nosniff', response.headers['x-content-type-options'])
                    self.assertEqual('DENY', response.headers['x-frame-options'])
                    self.assertIn("default-src 'none'", response.headers['content-security-policy'])
            configuration.assert_not_called()
            enabled.assert_not_called()
            authenticate.assert_not_called()
            database.assert_not_called()

    def test_only_exact_files_bypass_authentication(self):
        with patch.object(app, 'get_auth_configuration', side_effect=AuthenticationConfigurationError('synthetic unavailable')):
            for path in ('/', '/api/version', '/static/app.js', '/static/feedback.html', '/feedback/private', '/feedback/'):
                with self.subTest(path=path):
                    response = self.client.get(path, follow_redirects=False)
                    self.assertEqual(503, response.status_code)

    def test_public_files_retain_host_proxy_and_origin_checks(self):
        for path in PUBLIC_PATHS:
            with self.subTest(path=path):
                self.assertEqual(400, (self.client.get(path, headers={'Host': 'untrusted.test'})).status_code)
                self.assertEqual(403, (self.client.get(path, headers={app.TRUSTED_PROXY_TOKEN_HEADER: ''})).status_code)
                self.assertEqual(403, (self.client.post(path, headers={'Origin': 'https://untrusted.test'})).status_code)

    def test_head_works_and_mutating_methods_are_rejected(self):
        with patch.object(app, 'get_auth_configuration', side_effect=AssertionError('must not authenticate')):
            for path in PUBLIC_PATHS:
                with self.subTest(path=path):
                    response = self.client.head(path)
                    self.assertEqual(200, response.status_code)
                    self.assertEqual(b'', response.content)
                    for method in ('POST', 'PUT', 'DELETE'):
                        response = self.client.request(method, path)
                        self.assertEqual(405, response.status_code)
                        self.assertEqual({'GET', 'HEAD'}, {value.strip() for value in response.headers['allow'].split(',')})

    def test_broken_configuration_gives_browsers_help_but_keeps_api_errors_json(self):
        with patch.object(app, 'get_auth_configuration', side_effect=AuthenticationConfigurationError('synthetic unavailable')):
            response = self.client.get('/auth/sign-in', headers={'Accept': 'text/html'})
            self.assertEqual(503, response.status_code)
            self.assertIn('href="/feedback?from=sign-in"', response.text)
            self.assertNotIn('set-cookie', response.headers)
            response = self.client.get('/api/version')
            self.assertEqual(503, response.status_code)
            self.assertEqual({'detail': 'Authentication is not configured.'}, response.json())


class FeedbackFailureTests(unittest.IsolatedAsyncioTestCase):
    def request(self, path, method='GET'):
        return Request({'type': 'http', 'method': method, 'path': path,
                        'query_string': b'', 'scheme': 'https', 'server': ('feedback.test', 443),
                        'headers': [(b'accept', b'text/html')], 'app': app.app})

    async def test_legacy_provider_failure_keeps_help_and_clears_auth_cookies(self):
        with (
            patch.object(app, 'get_auth_configuration', return_value=object()),
            patch.object(app, 'run_in_threadpool', new=AsyncMock(side_effect=RuntimeError('synthetic provider unavailable'))),
            patch.object(app.config, 'get_runtime_environment', return_value='local'),
        ):
            response = await app.auth_login(self.request('/auth/login'))
        self.assertEqual(503, response.status_code)
        self.assertIn('href="/feedback?from=sign-in"', response.body.decode())
        self.assertEqual(2, len(response.headers.getlist('set-cookie')))

    async def test_incomplete_provider_logout_keeps_help(self):
        runtime = object.__new__(signup_http.SignupHTTP)
        runtime.adapter = SimpleNamespace(logout_evidence=AsyncMock(side_effect=signup_http.StoreUnavailable()))
        response = await runtime.logout(self.request('/auth/logout', method='POST'))
        self.assertEqual(503, response.status_code)
        self.assertIn('href="/feedback?from=sign-in"', response.body.decode())


class FeedbackEntryTests(unittest.TestCase):
    def test_generated_registration_and_error_pages_keep_help_available(self):
        runtime = object.__new__(signup_http.SignupHTTP)
        runtime.token = lambda *args, **kwargs: 'synthetic-csrf'
        runtime.adapter = SimpleNamespace(codec=SimpleNamespace(lifetime_seconds=3600))
        for status in ('available', 'full', 'closed', 'expired', 'invalid', 'ineligible', 'unavailable'):
            with self.subTest(status=status):
                body = runtime.page('b' * 43, status).body.decode()
                self.assertIn('href="/feedback', body)
                self.assertIn('>Sign in</button>', body)

    def test_static_sign_in_and_dashboard_have_help_links(self):
        for name in ('sign-in.html', 'index.html'):
            with self.subTest(name=name):
                self.assertIn('href="/feedback', (STATIC / name).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
