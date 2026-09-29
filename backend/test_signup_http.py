"""Application boundary tests with synthetic provider/identity fixtures only."""

import asyncio
import html
from dataclasses import replace
import os
import re
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx

import app
import config
import signup_http as boundary
from account_session_candidate import SessionCodec, StoreUnavailable
from admission_adapter_candidate import AdmissionAdapterCandidate
import test_p0_regressions as regressions
from test_provider_session_candidate import MemoryStore, SyntheticHTTP, StreamingMockTransport, SETTINGS, GENERATION, SUBJECT
from workos_transport_candidate import WorkOSTransportCandidate


ORIGIN = 'https://staging-app.jhunterj.com'


class HTTPStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.availability_status = 'available'
        self.limited = False
        self.lanes = []
        self.claims = set()
        self.revoked = set()

    async def availability(self):
        return {'status': self.availability_status}

    async def reserve(self, flow):
        if flow.purpose == 'signup' and self.availability_status != 'available':
            return {'status': self.availability_status}
        return await super().reserve(flow)

    async def budget(self, lane, browser, network):
        self.lanes.append(lane)
        return not self.limited

    async def session_guard(self, key, expires, operation, snapshot=None):
        if operation == 'revoke':
            self.revoked.add(key)
            return True
        if key in self.revoked:
            return False
        if operation == 'check':
            return True
        if snapshot in self.claims:
            return False
        self.claims.add(snapshot)
        return True


class SignupHTTPTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    async def asyncSetUp(self):
        environment = regressions.WebAuthenticationTests().staging_environment()
        environment.update({boundary.ENABLED_ENV: '1', 'CAREERTRELLIS_DATA_STORE': 'postgres'})
        patcher = patch.dict(os.environ, environment)
        patcher.start(); self.addCleanup(patcher.stop)
        self.http = SyntheticHTTP(self.signing_key)
        self.provider = WorkOSTransportCandidate(SETTINGS, transport=StreamingMockTransport(self.http), rotation_seconds=0)
        self.addAsyncCleanup(self.provider.aclose)
        self.store = HTTPStore()
        self.key = Fernet.generate_key()
        self.codec = SessionCodec(self.key, Fernet.generate_key(), key_id='synthetic-k1',
            generation=GENERATION, lifetime_seconds=3600, provider_binding=SETTINGS.binding)
        self.adapter = AdmissionAdapterCandidate(self.store, self.provider, self.codec,
            owner_subject='user_synthetic_owner', flow_key=Fernet.generate_key(), redirect_uri=ORIGIN + '/auth/callback')
        self.runtime = boundary.SignupHTTP(self.adapter, key=self.key, logout_return_to=ORIGIN + '/', static_dir=config.BASE_DIR / 'static')
        previous = getattr(app.app.state, 'signup_runtime', None)
        app.app.state.signup_runtime = self.runtime
        self.addCleanup(setattr, app.app.state, 'signup_runtime', previous)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app.app), base_url=ORIGIN,
            headers={app.TRUSTED_PROXY_TOKEN_HEADER: environment['CAREERTRELLIS_TRUSTED_PROXY_TOKEN'], 'Origin': ORIGIN})
        self.addAsyncCleanup(self.client.aclose)

    async def landing(self):
        response = await self.client.get('/auth/sign-in')
        self.assertEqual(200, response.status_code)
        self.csrf = re.search('name="csrf-token" content="([^"]+)"', response.text)[1]
        return response

    async def start(self, purpose='signup', choice='same'):
        await self.landing()
        response = await self.client.post('/auth/start', data={'csrf': self.csrf, 'purpose': purpose, 'choice': choice})
        self.assertIn(response.status_code, (200, 303))
        return response

    def authorization_url(self, response):
        return html.unescape(re.search('name="authorization-url" content="([^"]+)"', response.text)[1])

    async def activate(self):
        response = await self.start()
        state = parse_qs(urlsplit(self.authorization_url(response)).query)['state'][0]
        result = await self.client.post('/auth/complete', json={'csrf': self.csrf, 'state': state, 'code': 'synthetic-code', 'error': ''})
        self.assertEqual(200, result.status_code)
        self.assertEqual({'redirect': '/auth/result'}, result.json())
        return state, result

    async def test_default_switch_has_no_factory_or_new_routes(self):
        with patch.dict(os.environ, {boundary.ENABLED_ENV: '0'}), patch.object(boundary, 'create_async_engine') as engine:
            self.assertIsNone(await boundary.create_runtime(None, config.BASE_DIR))
            self.assertEqual(200, (await self.client.get('/auth/sign-in')).status_code)
            self.assertNotIn('Create account', (await self.client.get('/auth/sign-in')).text)
            with patch.object(app, 'check_postgres_ready', side_effect=RuntimeError('synthetic unavailable')):
                self.assertEqual(503, (await self.client.post('/auth/start')).status_code)
            engine.assert_not_called()
        with patch.dict(os.environ, {boundary.ENABLED_ENV: 'true'}):
            self.assertEqual(503, (await self.client.get('/auth/sign-in')).status_code)

    async def test_enabled_without_runtime_never_falls_back(self):
        app.app.state.signup_runtime = None
        with patch.object(app, 'authenticate_session') as legacy:
            self.assertEqual(503, (await self.client.get('/api/version')).status_code)
            self.assertEqual(503, (await self.client.get('/auth/login')).status_code)
            legacy.assert_not_called()

    async def test_provider_initiation_is_login_only_with_fresh_pkce_and_closed_signup(self):
        self.store.availability_status = 'closed'
        result = await self.client.get('/auth/initiate')
        self.assertEqual(303, result.status_code)
        url = urlsplit(result.headers['location'])
        self.assertEqual(('https', 'api.workos.com', '/user_management/authorize'),
            (url.scheme, url.netloc, url.path))
        query = parse_qs(url.query)
        self.assertEqual(['sign-in'], query['screen_hint'])
        self.assertEqual(['login'], query['prompt'])
        self.assertEqual(['0'], query['max_age'])
        self.assertEqual([ORIGIN + '/auth/callback'], query['redirect_uri'])
        self.assertEqual(['S256'], query['code_challenge_method'])
        self.assertEqual(43, len(query['state'][0]))
        self.assertNotEqual(query['state'], query['code_challenge'])
        self.assertEqual(1, len(self.store.flows))
        flow = self.adapter._open_flow(self.client.cookies.get(boundary.FLOW),
            self.client.cookies.get(boundary.BROWSER))
        self.assertEqual('login', flow.purpose)
        self.assertEqual(0, self.http.posts)
        self.assertIn('no-store', result.headers['cache-control'])
        self.assertEqual('no-referrer', result.headers['referrer-policy'])
        self.assertNotIn('invitation_token', query)

    async def test_provider_initiation_rejects_untrusted_parameters_before_reservation(self):
        for query in ('?token=synthetic', '?invitation_token=synthetic',
                '?redirect_uri=https://evil.test', '?state=attacker', '?x=a&x=b'):
            response = await self.client.get('/auth/initiate' + query)
            self.assertEqual(400, response.status_code)
            self.assertNotIn('synthetic', response.text)
        self.assertEqual(405, (await self.client.post('/auth/initiate')).status_code)
        self.assertEqual(0, len(self.store.flows))
        self.assertEqual(0, self.http.posts)

    async def test_provider_initiation_preserves_session_and_pending_attempt(self):
        await self.activate()
        original = self.client.cookies.get(boundary.COOKIE_NAME)
        result = await self.client.get('/auth/initiate')
        self.assertEqual(303, result.status_code)
        self.assertEqual(original, self.client.cookies.get(boundary.COOKIE_NAME))
        self.assertFalse(self.store.revoked)
        flow = self.client.cookies.get(boundary.FLOW)
        count = len(self.store.flows)
        repeated = await self.client.get('/auth/initiate')
        self.assertEqual(409, repeated.status_code)
        self.assertEqual('same-origin', repeated.headers['referrer-policy'])
        self.assertEqual(flow, self.client.cookies.get(boundary.FLOW))
        self.assertEqual(count, len(self.store.flows))

    async def test_provider_initiation_is_rate_limited_and_store_failure_is_closed(self):
        self.store.limited = True
        self.assertEqual(429, (await self.client.get('/auth/initiate')).status_code)
        self.assertEqual(0, len(self.store.flows))
        self.store.limited = False
        with patch.object(self.store, 'reserve', side_effect=StoreUnavailable()):
            self.assertEqual(503, (await self.client.get('/auth/initiate')).status_code)
        self.assertEqual(0, self.http.posts)

    async def test_failed_initiated_callback_preserves_existing_account_session(self):
        await self.activate()
        original = self.client.cookies.get(boundary.COOKIE_NAME)
        result = await self.client.get('/auth/initiate')
        state = parse_qs(urlsplit(result.headers['location']).query)['state'][0]
        wrong = await self.client.post('/auth/complete', json={'csrf': self.csrf,
            'state': 'x' * 43, 'code': 'synthetic-code', 'error': ''})
        self.assertEqual(200, wrong.status_code)
        self.assertEqual(original, self.client.cookies.get(boundary.COOKIE_NAME))
        cancelled = await self.client.post('/auth/complete', json={'csrf': self.csrf,
            'state': state, 'code': '', 'error': 'access_denied'})
        self.assertEqual(200, cancelled.status_code)
        self.assertEqual(original, self.client.cookies.get(boundary.COOKIE_NAME))
        self.assertEqual(1, self.http.posts)
        self.assertFalse(self.store.revoked)

    async def test_closed_full_allow_login_but_allocate_no_signup(self):
        for status in ('closed', 'full'):
            self.store.availability_status = status
            page = await self.landing()
            self.assertIn('disabled>Create account', page.text)
            self.assertIn('Existing users can still sign in.', page.text)
            result = await self.start()
            self.assertEqual('/auth/result', result.headers['location'])
            self.assertEqual(0, len(self.store.flows))
        result = await self.start('login', 'different')
        query = parse_qs(urlsplit(self.authorization_url(result)).query)
        self.assertEqual(['select_account'], query['provider_query_params[prompt]'])
        self.assertEqual(['sign-in'], query['screen_hint'])
        self.assertEqual(1, len(self.store.flows))
        self.assertEqual(0, self.http.posts)

    async def test_csrf_origin_duplicates_size_and_rate_precede_reservation(self):
        await self.landing()
        valid = {'csrf': self.csrf, 'purpose': 'signup', 'choice': 'same'}
        for headers in ({'Origin': 'null'}, {'Origin': 'https://evil.test'}, {'Origin': ''}):
            response = await self.client.post('/auth/start', data=valid, headers=headers)
            self.assertIn(response.status_code, (400, 403))
        for body in ('csrf=x&csrf=y&purpose=signup&choice=same', 'a=' + 'x' * 9000):
            self.assertEqual(400, (await self.client.post('/auth/start', content=body,
                headers={'content-type': 'application/x-www-form-urlencoded'})).status_code)
        self.assertEqual(400, (await self.client.post('/auth/start', json={**valid, 'csrf': 'wrong'})).status_code)
        self.assertEqual(0, len(self.store.flows))
        self.store.limited = True
        self.assertEqual(303, (await self.client.post('/auth/start', data=valid)).status_code)
        self.assertEqual(0, len(self.store.flows)); self.assertEqual(0, self.http.posts)

    async def test_callback_clean_result_cookie_and_account_bound_private_route(self):
        response = await self.start()
        state = parse_qs(urlsplit(self.authorization_url(response)).query)['state'][0]
        callback = await self.client.get('/auth/callback', params={'code': 'synthetic-private-code', 'state': state})
        self.assertEqual(200, callback.status_code)
        self.assertNotIn('synthetic-private-code', callback.text)
        self.assertNotIn(state, callback.text)
        self.assertEqual('no-referrer', callback.headers['referrer-policy'])
        self.assertIn("form-action 'self'", callback.headers['content-security-policy'])
        self.assertEqual(0, self.http.posts)
        result = await self.client.post('/auth/complete', json={'csrf': self.csrf, 'state': state, 'code': 'synthetic-private-code', 'error': ''})
        self.assertEqual(200, result.status_code)
        cookies = result.headers.get_list('set-cookie')
        session = next(header for header in cookies if header.startswith(boundary.COOKIE_NAME + '='))
        for attribute in ('Secure', 'HttpOnly', 'SameSite=lax', 'Path=/', 'Max-Age=3600'):
            self.assertIn(attribute, session)
        self.assertLess(len(session), 4096)
        self.assertNotIn('Domain=', session)
        self.assertIn('Your account is ready', (await self.client.get('/auth/result')).text)
        self.assertEqual(200, (await self.client.get('/api/version')).status_code)
        summary = (await self.client.get('/api/session')).json()
        self.assertTrue(summary['authenticated']); self.assertIn('logout_csrf', summary)
        replay = await self.client.post('/auth/complete', json={'csrf': self.csrf, 'state': state, 'code': 'synthetic-private-code', 'error': ''})
        self.assertEqual(1, self.http.posts)
        self.assertFalse(any(h.startswith(boundary.COOKIE_NAME + '=') for h in replay.headers.get_list('set-cookie')))

    async def test_callback_rejects_duplicate_query_and_browser_cookie(self):
        await self.start()
        self.assertEqual(400, (await self.client.get('/auth/callback?code=a&code=b&state=' + 's' * 43)).status_code)
        self.assertEqual(400, (await self.client.get('/auth/sign-in', headers={'Cookie': f'{boundary.BROWSER}=a; {boundary.BROWSER}=b'})).status_code)
        self.assertEqual(0, self.http.posts)

    async def test_cancel_and_failed_provider_have_fixed_messages_no_cookie(self):
        response = await self.start()
        state = parse_qs(urlsplit(self.authorization_url(response)).query)['state'][0]
        self.assertEqual(303, (await self.client.post('/auth/cancel', data={'csrf': self.csrf})).status_code)
        await self.client.post('/auth/complete', json={'csrf': self.csrf, 'state': state, 'code': 'synthetic-code', 'error': ''})
        self.assertEqual(0, self.http.posts)
        self.assertEqual(0, self.store.finishes)

    async def test_legacy_downgrade_is_blocked_before_sdk(self):
        with patch.dict(os.environ, {boundary.ENABLED_ENV: '0'}), patch.object(app, 'check_postgres_ready', side_effect=RuntimeError()), patch.object(app, 'authenticate_session') as legacy, patch.object(app, 'complete_login') as exchange:
            for path in ('/api/version', '/auth/login', '/auth/callback?code=a&state=b'):
                self.assertEqual(503, (await self.client.get(path)).status_code)
            legacy.assert_not_called(); exchange.assert_not_called()

    async def test_logout_clears_all_cookies_and_revokes_late_refresh(self):
        await self.activate()
        old = self.client.cookies.get(boundary.COOKIE_NAME)
        response = await self.client.post('/auth/logout', data={'csrf': self.csrf})
        self.assertEqual(303, response.status_code)
        self.assertEqual([ORIGIN + '/'], parse_qs(urlsplit(response.headers['location']).query)['return_to'])
        for name in boundary.AUTH_COOKIES:
            self.assertTrue(any(h.startswith(name + '=') and 'Max-Age=0' in h for h in response.headers.get_list('set-cookie')))
        self.assertEqual('unauthenticated', (await self.adapter.authenticate(old)).status)
        self.assertEqual(1, self.http.posts)

    async def test_refresh_one_snapshot_once_and_no_expiry_extension(self):
        await self.activate()
        original = self.codec.open(self.client.cookies.get(boundary.COOKIE_NAME))
        data = self.codec.provider_open(original.provider)
        data['access_token'] = self.http.token(exp=int(time.time()) - 1)
        provider_cookie = self.codec._provider_seal.encrypt(__import__('json').dumps(data).encode()).decode()
        expired = replace(original, provider=provider_cookie)
        one, two = self.codec.pack(expired), self.codec.pack(expired)
        results = await asyncio.gather(self.adapter.authenticate(one, refresh=True), self.adapter.authenticate(two, refresh=True))
        self.assertEqual(2, self.http.posts)
        self.assertEqual(['authenticated', 'restart_required'], sorted(r.status for r in results))
        winner = next(r for r in results if r.cookie)
        self.assertEqual(original.expires, self.codec.open(winner.cookie).expires)
        self.assertEqual(1, len(self.store.claims))

    async def test_budget_ignores_forged_headers_from_untrusted_connection(self):
        from starlette.requests import Request
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        seen = []
        async def budget(lane, browser, network):
            seen.append(network)
            return True
        self.store.budget = budget
        async def endpoint(scope, receive, send):
            await self.runtime.budget(Request(scope), 'login', 'synthetic-browser')
        middleware = ProxyHeadersMiddleware(endpoint, trusted_hosts='127.0.0.1')
        for claimed in (b'192.0.2.11', b'192.0.2.12'):
            await middleware({'type':'http', 'scheme':'http', 'client':('198.51.100.1',1234),
                'headers':[(b'x-forwarded-for',claimed),(b'cf-connecting-ip',claimed)]}, None, None)
        expected = self.runtime.coordination.digest('network', '198.51.100.1')
        self.assertEqual([expected, expected], seen)

    async def test_trusted_proxy_shared_address_shares_network_budget(self):
        from starlette.requests import Request
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        seen = []
        async def budget(lane, browser, network):
            seen.append((browser, network))
            return True
        self.store.budget = budget
        async def endpoint(scope, receive, send):
            await self.runtime.budget(Request(scope), 'login', 'synthetic-browser-' + str(len(seen)))
        middleware = ProxyHeadersMiddleware(endpoint, trusted_hosts='127.0.0.1')
        for claimed in (b'192.0.2.11', b'192.0.2.12'):
            await middleware({'type':'http', 'scheme':'http', 'client':('127.0.0.1',1234),
                'headers':[(b'x-forwarded-for',b'198.51.100.8'),(b'cf-connecting-ip',claimed)]}, None, None)
        expected = self.runtime.coordination.digest('network', '198.51.100.8')
        self.assertEqual([expected, expected], [network for _, network in seen])
        self.assertNotEqual(seen[0][0], seen[1][0])

    async def test_lost_refresh_acknowledgement_consumes_snapshot_without_retry(self):
        await self.activate()
        original = self.codec.open(self.client.cookies.get(boundary.COOKIE_NAME))
        data = self.codec.provider_open(original.provider)
        data['access_token'] = self.http.token(exp=int(time.time()) - 1)
        provider_cookie = self.codec._provider_seal.encrypt(__import__('json').dumps(data).encode()).decode()
        expired = replace(original, provider=provider_cookie)
        consumed = []
        async def lose_response(request):
            if request.method == 'POST':
                consumed.append(True)
                self.http.refresh_token = 'synthetic-rotated-token'
                raise httpx.ReadError('synthetic lost acknowledgement', request=request)
        self.http.hook = lose_response
        first = await self.adapter.authenticate(self.codec.pack(expired), refresh=True)
        self.assertEqual('unavailable', first.status)
        self.assertFalse(first.cookie); self.assertFalse(first.clear_cookie)
        self.assertIsNone(first.authorization)
        self.http.hook = None
        second = await self.adapter.authenticate(self.codec.pack(expired), refresh=True)
        self.assertEqual('restart_required', second.status)
        self.assertFalse(second.cookie); self.assertFalse(second.clear_cookie)
        self.assertEqual([True], consumed)
        self.assertEqual(2, self.http.posts)  # Initial exchange and the lost refresh only.
        self.assertEqual(1, len(self.store.claims))
        self.assertEqual(original.expires, expired.expires)
        self.assertEqual('authenticated', (await self.adapter.authenticate(
            self.client.cookies.get(boundary.COOKIE_NAME))).status)

    async def test_logout_during_refresh_prevents_late_cookie_publication(self):
        await self.activate()
        original_cookie = self.client.cookies.get(boundary.COOKIE_NAME)
        original = self.codec.open(original_cookie)
        data = self.codec.provider_open(original.provider)
        data['access_token'] = self.http.token(exp=int(time.time()) - 1)
        provider_cookie = self.codec._provider_seal.encrypt(__import__('json').dumps(data).encode()).decode()
        expired = replace(original, provider=provider_cookie)
        entered, release = asyncio.Event(), asyncio.Event()
        async def pause_response(request):
            if request.method == 'POST':
                entered.set()
                await release.wait()
        self.http.hook = pause_response
        task = asyncio.create_task(self.adapter.authenticate(self.codec.pack(expired), refresh=True))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await self.runtime.coordination.revoke(expired)
            release.set()
            result = await asyncio.wait_for(task, 3)
        finally:
            release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual('restart_required', result.status)
        self.assertFalse(result.cookie); self.assertIsNone(result.authorization)
        self.assertEqual('unauthenticated', (await self.adapter.authenticate(original_cookie)).status)
        self.assertEqual(2, self.http.posts)

    async def test_replacement_account_clears_and_revokes_old_cookie_even_when_closed(self):
        await self.activate()
        old = self.client.cookies.get(boundary.COOKIE_NAME)
        self.store.availability_status = 'closed'
        response = await self.start('signup', 'different')
        self.assertEqual('/auth/result', response.headers['location'])
        self.assertIsNone(self.client.cookies.get(boundary.COOKIE_NAME))
        self.assertEqual('unauthenticated', (await self.adapter.authenticate(old)).status)

    async def test_all_owned_resources_close_when_one_fails(self):
        from unittest.mock import AsyncMock
        first, second = AsyncMock(), AsyncMock()
        first.dispose.side_effect = RuntimeError('synthetic cleanup failure')
        self.runtime.engines = (first, second)
        with self.assertRaises(StoreUnavailable):
            await self.runtime.aclose()
        first.dispose.assert_awaited_once(); second.dispose.assert_awaited_once()

    async def test_analytics_initialization_failure_closes_constructed_runtime(self):
        from unittest.mock import AsyncMock, Mock
        owned = Mock(aclose=AsyncMock())
        with patch.object(app.config, 'validate_runtime_configuration'), patch.object(app, 'validate_data_store_configuration', return_value='postgres'), patch.object(app, 'validate_identity_store_configuration'), patch.object(app, 'validate_provider_secret_store_configuration'), patch.object(app, 'validate_object_store_configuration'), patch.object(boundary, 'create_runtime', new=AsyncMock(return_value=owned)), patch.object(app, 'initialize_analytics', side_effect=RuntimeError('synthetic failure')), patch.object(app, 'shutdown_analytics'), patch.object(app, 'dispose_postgres_engines') as dispose:
            with self.assertRaises(RuntimeError):
                async with app.lifespan(app.app):
                    self.fail('Failed initialization must not yield')
            owned.aclose.assert_awaited_once()
            dispose.assert_called_once()
            self.assertIsNone(app.app.state.signup_runtime)

    async def test_unavailable_landing_preserves_session_and_shows_actionable_page(self):
        await self.activate()
        session = self.client.cookies.get(boundary.COOKIE_NAME)
        self.store.fail_context = True
        response = await self.client.get('/auth/sign-in')
        self.assertEqual(503, response.status_code)
        self.assertIn('Account services are temporarily unavailable', response.text)
        self.assertIn('disabled>Create account', response.text)
        self.assertEqual(session, self.client.cookies.get(boundary.COOKIE_NAME))

    async def test_runtime_rejects_dsn_redirection_before_connections_and_cleans_cancelled_construction(self):
        from unittest.mock import AsyncMock, Mock
        import web_auth
        settings = web_auth.get_auth_configuration()
        environment = {
            'CAREERTRELLIS_AUTH_V2_ISSUER': 'https://api.workos.com/user_management/' + settings.client_id,
            'CAREERTRELLIS_AUTH_V2_GENERATION': GENERATION,
            'CAREERTRELLIS_AUTH_V2_ENVELOPE_KEY': Fernet.generate_key().decode(),
            'CAREERTRELLIS_AUTH_V2_FLOW_KEY': Fernet.generate_key().decode(),
            'CAREERTRELLIS_AUTH_V2_KEY_ID': 'synthetic-k1',
            'CAREERTRELLIS_AUTH_V2_SESSION_SECONDS': '3600',
            'CAREERTRELLIS_ADMISSION_DATABASE_URL': 'postgresql+psycopg://careertrellis_admission_broker:synthetic@127.0.0.1/ct_signup_integration',
            'CAREERTRELLIS_DATABASE_URL': 'postgresql+psycopg://careertrellis_runtime:synthetic@127.0.0.1/ct_signup_integration',
        }
        with patch.dict(os.environ, environment):
            for suffix in ('?host=evil.test', '?options=unsafe', '?service=alternate'):
                with patch.dict(os.environ, {'CAREERTRELLIS_ADMISSION_DATABASE_URL': environment['CAREERTRELLIS_ADMISSION_DATABASE_URL'] + suffix}), patch.object(boundary, 'create_async_engine') as engine:
                    with self.assertRaises(web_auth.AuthenticationConfigurationError):
                        await boundary.create_runtime(settings, config.BASE_DIR / 'static')
                    engine.assert_not_called()
            engines = [Mock(dispose=AsyncMock()), Mock(dispose=AsyncMock())]
            with patch.object(boundary, 'create_async_engine', side_effect=engines), patch.object(boundary, 'AsyncAdmissionStore', side_effect=asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await boundary.create_runtime(settings, config.BASE_DIR / 'static')
                for engine in engines:
                    engine.dispose.assert_awaited_once()

    async def test_native_browser_forms_secure_cookie_clean_callback_and_mobile_layout(self):
        import ipaddress
        from datetime import datetime, timedelta, timezone
        from pathlib import Path
        import socket
        import tempfile
        import threading
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from playwright.async_api import async_playwright
        import uvicorn
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        origin = f'https://127.0.0.1:{port}'
        # Test-only ephemeral ingress; production construction rejects this URI.
        self.runtime.origin = origin
        self.runtime.logout_return_to = origin + '/'
        self.adapter.redirect_uri = origin + '/auth/callback'
        with tempfile.TemporaryDirectory(prefix='ct-http-native-') as temporary:
            private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic loopback')])
            now = datetime.now(timezone.utc)
            certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(private.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
                .sign(private, hashes.SHA256()))
            key_path, cert_path = Path(temporary) / 'synthetic.key', Path(temporary) / 'synthetic.crt'
            key_path.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            server = uvicorn.Server(uvicorn.Config(app.app, lifespan='off', access_log=False, log_config=None,
                timeout_keep_alive=1, timeout_graceful_shutdown=2,
                ssl_keyfile=str(key_path), ssl_certfile=str(cert_path)))
            def serve():
                # TLS sockets use Selector on Windows; Playwright retains its
                # own Proactor loop for subprocess support in the test thread.
                with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                    runner.run(server.serve(sockets=[listener]))
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            external, observed_origins = [], []
            async def guard(route):
                url = route.request.url
                if url.startswith('https://api.workos.com/user_management/authorize?'):
                    state = parse_qs(urlsplit(url).query)['state'][0]
                    await route.fulfill(status=303, headers={'location': origin + '/auth/callback?' + __import__('urllib.parse', fromlist=['urlencode']).urlencode({'state': state, 'code': 'synthetic-browser-code'})})
                elif url.startswith(origin + '/'):
                    if urlsplit(url).path == '/auth/initiate':
                        # Playwright routes only the first request of a server
                        # redirect chain. Fetch this local response without
                        # following it; simulate the provider return while
                        # retaining the actual browser/flow Set-Cookie headers.
                        response = await route.fetch(max_redirects=0)
                        if response.status == 303:
                            target = urlsplit(response.headers['location'])
                            self.assertEqual(('https', 'api.workos.com', '/user_management/authorize'),
                                (target.scheme, target.netloc, target.path))
                            state = parse_qs(target.query)['state'][0]
                            headers = {**response.headers, 'location': origin + '/auth/callback?' +
                                __import__('urllib.parse', fromlist=['urlencode']).urlencode(
                                    {'state': state, 'code': 'synthetic-initiated-code'})}
                            await route.fulfill(response=response, headers=headers)
                        else:
                            await route.fulfill(response=response)
                        return
                    if route.request.method == 'POST':
                        observed_origins.append((urlsplit(url).path, (await route.request.all_headers()).get('origin')))
                    if urlsplit(url).path.startswith('/api/') and urlsplit(url).path not in ('/api/version', '/api/session', '/api/maintenance-notice'):
                        await route.abort()
                    else:
                        await route.continue_()
                else:
                    external.append(urlsplit(url).hostname)
                    await route.abort()
            try:
                for _ in range(100):
                    if server.started: break
                    await asyncio.sleep(.05)
                self.assertTrue(server.started)
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(channel='msedge' if os.name == 'nt' else None, headless=True,
                        args=['--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost'])
                    try:
                        context = await browser.new_context(ignore_https_errors=True, service_workers='block',
                            viewport={'width': 320, 'height': 700})
                        await context.route('**/*', guard)
                        page = await context.new_page()
                        responses = []
                        page.on('response', lambda response: responses.append((urlsplit(response.url).path, response.status)))
                        await page.goto(origin + '/auth/sign-in')
                        self.assertFalse(await page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                        self.assertEqual('Create account', await page.get_by_role('button', name='Create account', exact=True).inner_text())
                        await page.get_by_role('button', name='Create account', exact=True).focus()
                        await page.keyboard.press('Enter')
                        try:
                            await page.wait_for_url(origin + '/auth/result', timeout=7000)
                        except Exception:
                            self.fail(f'Browser path={urlsplit(page.url).path}; responses={responses}; post_origins={observed_origins}; text={(await page.locator("body").inner_text())[:300]}')
                        self.assertIn('Your account is ready', await page.locator('body').inner_text())
                        self.assertNotIn('code=', page.url)
                        cookies = await context.cookies()
                        session = next(c for c in cookies if c['name'] == boundary.COOKIE_NAME)
                        self.assertTrue(session['secure']); self.assertTrue(session['httpOnly'])
                        self.assertEqual('Lax', session['sameSite'])
                        await page.goto(origin + '/api/version')
                        self.assertIn(app.APP_BUILD, await page.locator('body').inner_text())
                        self.assertEqual([('/auth/start', origin), ('/auth/complete', origin)], observed_origins)
                        self.assertEqual([], external)
                        self.assertEqual(1, self.http.posts)
                        await page.goto(origin + '/')
                        self.assertIn('CareerTrellis', await page.title())
                        self.assertCountEqual(['fonts.googleapis.com', 'cdnjs.cloudflare.com'], external)
                        blocked_dashboard_assets = list(external)
                        self.store.availability_status = 'closed'
                        await page.goto(origin + '/auth/initiate')
                        try:
                            await page.wait_for_url(origin + '/auth/result', timeout=7000)
                        except Exception:
                            self.fail(f'Initiation path={urlsplit(page.url).path}; responses={responses}; '
                                f'text={(await page.locator("body").inner_text())[:300]}')
                        self.assertIn('You are signed in', await page.locator('body').inner_text())
                        self.assertNotIn('code=', page.url)
                        self.assertEqual(2, self.http.posts)
                        self.assertEqual(blocked_dashboard_assets, external)
                    finally:
                        await browser.close()
            finally:
                server.should_exit = True
                await asyncio.to_thread(thread.join, 10)
                listener.close()
                self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
