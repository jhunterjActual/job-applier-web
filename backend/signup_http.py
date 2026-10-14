"""Closed-by-default application HTTP boundary for the admission candidate.

Importing this module never reads credentials, opens connections or enables
signup. Only lifespan construction can install the explicitly configured mode.
"""

import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse

from account_session_candidate import COOKIE_NAME, SessionCodec, SessionInvalid, StoreUnavailable
from admission_adapter_candidate import AdmissionAdapterCandidate, AsyncAdmissionStore
from postgres_database import database_url_from_environment
from web_auth import SESSION_COOKIE_NAME, LOGIN_FLOW_COOKIE_NAME, AuthenticationConfigurationError
from workos_transport_candidate import ProviderSettings, WorkOSTransportCandidate, strict_json
from recovery_callback import is_recovery_callback, recovery_callback_response


ENABLED_ENV = 'CAREERTRELLIS_AUTH_V2_ENABLED'
BROWSER = '__Host-careertrellis_browser_v2'
FLOW = '__Host-careertrellis_flow_v2'
RECEIPT = '__Host-careertrellis_result_v2'
AUTH_COOKIES = (COOKIE_NAME, SESSION_COOKIE_NAME, LOGIN_FLOW_COOKIE_NAME, FLOW, BROWSER, RECEIPT)
PUBLIC_PATHS = frozenset(('/auth/sign-in', '/auth/login', '/auth/initiate', '/auth/start', '/auth/callback',
    '/auth/complete', '/auth/result', '/auth/cancel', '/auth/logout',
    '/static/signup-v2.js', '/static/signup-v2.css', '/static/recovery-callback.js'))
MESSAGES = {
    'available': 'Create an account to join the beta. You will verify your email before activation.',
    'closed': 'New account registration is currently closed. Existing users can still sign in.',
    'full': 'The beta is at its account limit. Existing users can still sign in.',
    'activated': 'Your account is ready. Continue to your workspace.',
    'login': 'You are signed in. Continue to your workspace.',
    'cancelled': 'This sign-in attempt was cancelled.',
    'expired': 'This sign-in attempt expired. Start again when you are ready.',
    'invalid': 'This sign-in attempt could not be verified. Please start again.',
    'ineligible': 'This identity could not activate this attempt. Try signing in if you already have an account.',
    'incomplete': 'Sign-in could not be confirmed. If you completed registration, try signing in. This attempt will not be retried automatically.',
    'service_limited': 'Your session could not be saved safely in this browser. Please contact support.',
    'unavailable': 'Account services are temporarily unavailable. Please try again later.',
    'limited': 'Too many attempts. Wait at least one minute before trying again.',
    'restart_required': 'A session refresh is already in progress or could not be confirmed. Reload once; if needed, sign in again.',
    'pending': 'This sign-in attempt has not completed. You can cancel it or start again.',
}


def enabled():
    value = os.environ.get(ENABLED_ENV, '0')
    if value not in ('0', '1'):
        raise AuthenticationConfigurationError('The v2 authentication switch must be 0 or 1.')
    return value == '1'


def clear_auth(response):
    for name in AUTH_COOKIES:
        response.delete_cookie(name, path='/', secure=True, httponly=True, samesite='lax')


def set_cookie(response, name, value, age=900):
    response.set_cookie(name, value, max_age=age, path='/', secure=True, httponly=True, samesite='lax')


def cookie(request, name, maximum=4096):
    # Starlette's dictionary silently keeps one duplicate. Reject ambiguity.
    pairs = [part.strip().split('=', 1) for header in request.headers.getlist('cookie')
             for part in header.split(';') if '=' in part]
    values = [value for key, value in pairs if key == name]
    if len(values) > 1 or (values and len(values[0]) > maximum):
        raise ValueError()
    return values[0] if values else ''


class Coordination:
    def __init__(self, store, key, binding):
        self.store, self.key, self.binding = store, key, binding

    def digest(self, domain, *values):
        return hmac.new(self.key, json.dumps([domain, self.binding, *values],
            separators=(',', ':')).encode(), hashlib.sha256).hexdigest()

    def session_key(self, session):
        return self.digest('session', session.local_id, session.subject, session.session_id)

    async def check(self, session):
        return await self.store.session_guard(self.session_key(session), session.expires, 'check')

    async def claim(self, session, data):
        snapshot = self.digest('refresh', session.local_id, session.subject, session.session_id,
            data['access_token'], data['refresh_token'])
        return await self.store.session_guard(self.session_key(session), session.expires, 'claim', snapshot)

    async def revoke(self, session):
        return await self.store.session_guard(self.session_key(session), session.expires, 'revoke')


class SignupHTTP:
    def __init__(self, adapter, *, key, logout_return_to, static_dir, engines=()):
        self.adapter, self.seal = adapter, Fernet(key)
        self.origin = adapter.redirect_uri.removesuffix('/auth/callback')
        if logout_return_to != self.origin + '/':
            raise ValueError('Exact same-origin logout return required')
        self.logout_return_to, self.static_dir, self.engines = logout_return_to, static_dir, engines
        # Rotating the envelope key invalidates old cookies AND coordination
        # identities together. Re-encrypting a token does not change its claim.
        self.coordination = Coordination(adapter.store,
            hmac.new(base64.urlsafe_b64decode(key), b'http-coordination-v1', hashlib.sha256).digest(),
            adapter.codec.provider_binding)
        adapter.coordination = self.coordination
        # Explicitly injected only after enrollment storage/keys/provider acceptance.
        # Default construction leaves this disabled; factory opt-in is explicit.
        self.enrollment_http = None
        self.enrollment_provider = None
        self.removal_http = None
        self.removal_provider = None
        self.recovery_http = None

    async def aclose(self):
        results = await asyncio.gather(self.adapter.provider.aclose(),
            *([self.enrollment_provider.aclose()] if self.enrollment_provider else []),
            *([self.removal_provider.aclose()] if self.removal_provider else []),
            *(engine.dispose() for engine in self.engines), return_exceptions=True)
        if any(isinstance(result, BaseException) for result in results):
            raise StoreUnavailable()

    def token(self, browser, kind, **values):
        return self.seal.encrypt(json.dumps({'kind': kind, 'browser': browser,
            'binding': self.adapter.codec.provider_binding, **values}, separators=(',', ':')).encode()).decode()

    def open_token(self, token, browser, kind):
        if not isinstance(token, str) or len(token) > 2000 or not browser:
            raise ValueError()
        data = strict_json(self.seal.decrypt(token.encode(), ttl=self.adapter.codec.lifetime_seconds if kind == 'csrf' else 900))
        if (data.get('kind') != kind or data.get('binding') != self.adapter.codec.provider_binding
                or not hmac.compare_digest(data.get('browser', ''), browser)):
            raise ValueError()
        return data

    async def budget(self, request, lane, browser):
        # Do not accept arbitrary forwarded/client-address headers. Until the
        # trusted ingress contract is accepted, a proxy peer may share a bucket.
        peer = request.client.host if request.client else 'unknown'
        return await self.adapter.store.budget(lane,
            self.coordination.digest('browser', browser), self.coordination.digest('network', peer))

    async def body(self, request, fields):
        if request.headers.getlist('origin') != [self.origin]:
            raise ValueError()
        raw = bytearray()
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 8192:
                    raise ValueError()
        content_type = request.headers.get('content-type', '').split(';')[0]
        if content_type == 'application/json':
            values = strict_json(raw)
        elif content_type == 'application/x-www-form-urlencoded':
            pairs = parse_qsl(raw.decode('utf-8'), keep_blank_values=True, max_num_fields=8)
            if len({key for key, _ in pairs}) != len(pairs):
                raise ValueError()
            values = dict(pairs)
        else:
            raise ValueError()
        if not isinstance(values, dict) or set(values) != fields | {'csrf'}:
            raise ValueError()
        if any(not isinstance(value, str) or len(value) > 2048 for value in values.values()):
            raise ValueError()
        browser = cookie(request, BROWSER, 43)
        self.open_token(values.pop('csrf'), browser, 'csrf')
        return browser, values

    def page(self, browser, status, *, complete=False):
        status = status if status in MESSAGES else 'incomplete'
        csrf = html.escape(self.token(browser, 'csrf'), quote=True)
        form = lambda purpose, label, choice='same', disabled=False: (
            '<form action="/auth/start" method="post">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<input type="hidden" name="purpose" value="{purpose}">'
            f'<input type="hidden" name="choice" value="{choice}">'
            f'<button type="submit"{" disabled" if disabled else ""}>{label}</button></form>')
        actions = (f'<p role="status" id="auth-progress">Completing sign-in…</p>'
            '<p class="timer">Elapsed: <span id="auth-elapsed">00:00</span></p>'
            '<noscript><p>JavaScript is required to finish this attempt. Return to sign in if it is disabled.</p></noscript>'
            if complete else '')
        if complete:
            actions += '<button type="button" id="auth-cancel">Cancel this attempt</button>'
        else:
            actions += form('login', 'Sign in')
            actions += form('signup', 'Create account', disabled=status != 'available')
        if not complete and status == 'pending':
            actions += ('<form action="/auth/cancel" method="post">'
                f'<input type="hidden" name="csrf" value="{csrf}"><button type="submit">Cancel this attempt</button></form>')
        feedback = ('<p><a href="/feedback?from=access">Questions about access? Contact us.</a></p>'
            if status in {'full', 'closed'} else
            '<p><a href="/feedback?from=sign-in">Help &amp; feedback</a><br>'
            'Trouble signing in? You can contact us without an account.</p>')
        response = HTMLResponse('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="csrf-token" content="{csrf}">'
            '<title>Sign in or create account | CareerTrellis</title>'
            '<link rel="stylesheet" href="/static/signup-v2.css">'
            '<link rel="stylesheet" href="/static/maintenance-notice.css?v=2">'
            '<script src="/static/maintenance-notice.js?v=1" defer></script>'
            '<script src="/static/signup-v2.js" defer></script></head>'
            f'<body data-complete="{str(complete).lower()}"><main><h1>CareerTrellis</h1>'
            '<h2>Sign in or create account</h2>'
            '<div id="maintenance-announcer" role="status" aria-live="polite" aria-atomic="true"></div>'
            '<section id="maintenance-notice" class="maintenance-notice" aria-label="Maintenance announcement" hidden></section>'
            f'<p id="auth-message" role="status">{MESSAGES[status]}</p>{actions}{feedback}'
            '<p>Signing out here does not sign you out of Google or other identity providers.</p>'
            '<footer>CareerTrellis is owned by J. Hunter Johnson LLC.</footer></main></body></html>',
            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' if complete else 'same-origin',
                'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
        set_cookie(response, BROWSER, browser, self.adapter.codec.lifetime_seconds)
        return response

    def result(self, browser, status, *, json_response=False, session=''):
        status = status if status in MESSAGES else 'incomplete'
        response = JSONResponse({'redirect': '/auth/result'}) if json_response else RedirectResponse('/auth/result', 303)
        set_cookie(response, RECEIPT, self.token(browser, 'receipt', status=status))
        if session:
            self.set_session(response, session)
        return response

    def set_session(self, response, value):
        session = self.adapter.codec.open(value)
        set_cookie(response, COOKIE_NAME, value, max(1, session.expires - int(time.time())))
        response.delete_cookie(SESSION_COOKIE_NAME, path='/', secure=True, httponly=True, samesite='lax')

    def authorize_page(self, destination):
        # End the native POST on our own origin. A 303 provider redirect remains
        # part of the form-action CSP chain in Chromium. This clean intermediary
        # uses a navigation instead and also offers a no-JavaScript link.
        destination = html.escape(destination, quote=True)
        return HTMLResponse('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="authorization-url" content="{destination}">'
            '<title>Continue to sign in | CareerTrellis</title>'
            '<link rel="stylesheet" href="/static/signup-v2.css">'
            '<script src="/static/signup-v2.js" defer></script></head><body><main>'
            '<h1>Continue to secure sign-in</h1>'
            f'<p><a href="{destination}" rel="noreferrer">Continue to sign in</a></p>'
            '</main></body></html>', headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})

    async def logout(self, request):
        request.state.suppress_refreshed_session = True
        response = HTMLResponse('<p>The browser session was cleared, but provider sign-out could not be confirmed.</p><a href="/auth/sign-in">Sign in</a><p><a href="/feedback?from=sign-in">Get help</a> — no CareerTrellis account needed.</p>', 503)
        try:
            value = cookie(request, COOKIE_NAME, 3500)
            legacy = not bool(value)
            if value:
                session = self.adapter.codec.open(value)
                if not await self.coordination.revoke(session):
                    raise StoreUnavailable()
            else:
                value = cookie(request, SESSION_COOKIE_NAME)
            outcome = await self.adapter.logout_evidence(value, legacy=legacy)
            if outcome.status == 'logout_ready':
                response = RedirectResponse(outcome.logout_url + '&' + urlencode({'return_to': self.logout_return_to}), 303)
        except (ValueError, SessionInvalid, StoreUnavailable):
            pass
        clear_auth(response)
        return response

    async def initiate(self, request):
        # Hosted WorkOS recovery/invitation screens own their tokens. This entry
        # point only starts an ordinary login with fresh state and PKCE. Never
        # forward arbitrary query parameters, trust an invitation as admission,
        # revoke an existing session on GET, or create an account in this lane.
        if request.query_params:
            raise ValueError()
        browser = cookie(request, BROWSER, 43) or secrets.token_urlsafe(32)
        if not await self.budget(request, 'login', browser):
            response = self.page(browser, 'limited')
            response.status_code = 429
            return response
        previous = cookie(request, FLOW, 2000)
        if previous:
            status = await self.adapter.status(previous, browser)
            if status.status not in ('invalid', 'expired', 'cancelled', 'login', 'activated', 'ineligible', 'incomplete'):
                response = self.page(browser, 'unavailable' if status.status == 'unavailable' else 'pending')
                response.status_code = 503 if status.status == 'unavailable' else 409
                return response
        outcome = await self.adapter.begin(browser, 'login')
        if outcome.status != 'reserved':
            response = self.page(browser, outcome.status)
            response.status_code = 503
            return response
        response = RedirectResponse(outcome.authorization_url, 303,
            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                'Content-Security-Policy': "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
        set_cookie(response, BROWSER, browser, self.adapter.codec.lifetime_seconds)
        set_cookie(response, FLOW, outcome.cookie)
        return response

    async def handle(self, request):
        path = request.url.path
        try:
            if path == '/account/security/recovery' or path.startswith('/account/security/recovery/'):
                if self.recovery_http is None:
                    return JSONResponse({'detail': 'Recovery codes are not enabled.'}, 404,
                                        headers={'Cache-Control': 'no-store'})
                return await self.recovery_http.handle(request)
            if path == '/account/security/removal' or path.startswith('/account/security/removal/'):
                if self.removal_http is None:
                    return JSONResponse({'detail': 'Authenticator removal is not enabled.'},404,
                                        headers={'Cache-Control': 'no-store'})
                return await self.removal_http.handle(request)
            if path == '/account/security' or path.startswith('/account/security/'):
                if self.enrollment_http is None:
                    return JSONResponse({'detail': 'Account security setup is not enabled.'}, 404,
                                        headers={'Cache-Control': 'no-store'})
                return await self.enrollment_http.handle(request)
            if path in ('/static/signup-v2.js', '/static/signup-v2.css', '/static/totp-enrollment.js', '/static/factor-removal.js', '/static/recovery-callback.js', '/static/recovery-credentials.js') and request.method == 'GET':
                return FileResponse(self.static_dir / path.rsplit('/', 1)[1])
            if path not in PUBLIC_PATHS:
                return None
            if path == '/auth/initiate':
                if request.method != 'GET':
                    return JSONResponse({'detail': 'Method not allowed.'}, 405)
                return await self.initiate(request)
            if request.method == 'GET' and path == '/auth/callback':
                pairs = list(request.query_params.multi_items())
                if is_recovery_callback(pairs):
                    return recovery_callback_response(pairs)
            browser = cookie(request, BROWSER, 43)
            if request.method == 'GET' and path in ('/auth/sign-in', '/auth/login', '/auth/result', '/auth/callback'):
                if path in ('/auth/sign-in', '/auth/result') and request.query_params:
                    raise ValueError()
                if path == '/auth/login':
                    if list(request.query_params.multi_items()) not in ([], [('account', 'different')]):
                        raise ValueError()
                    return RedirectResponse('/auth/sign-in', 303)
                if path == '/auth/callback':
                    pairs = list(request.query_params.multi_items())
                    query = dict(pairs)
                    if (len(pairs) != len(query) or set(query) not in ({'code', 'state'}, {'error', 'state'})
                            or not all(query.values()) or len(query.get('state', '')) != 43
                            or any(len(value) > 2048 for value in query.values()) or not browser):
                        raise ValueError()
                    return self.page(browser, 'pending', complete=True)
                browser = browser or secrets.token_urlsafe(32)
                if not await self.budget(request, 'status', browser):
                    return self.page(browser, 'limited')
                if path == '/auth/result':
                    status = self.open_token(cookie(request, RECEIPT), browser, 'receipt').get('status')
                    if status in ('activated', 'login'):
                        # The protected root authenticates the session again. A receipt
                        # never grants access or supplies a redirect destination.
                        return RedirectResponse('/', 303, headers={
                            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
                else:
                    await self.adapter.context()
                    status = (await self.adapter.store.availability())['status']
                return self.page(browser, status)
            if request.method != 'POST':
                return JSONResponse({'detail': 'Not found.'}, 404)
            fields = {'purpose', 'choice'} if path == '/auth/start' else {'state', 'code', 'error'} if path == '/auth/complete' else set()
            browser, values = await self.body(request, fields)
            flow_cookie = cookie(request, FLOW, 2000)
            lane = values.get('purpose') if path == '/auth/start' else 'logout' if path == '/auth/logout' else 'status'
            if path == '/auth/complete':
                lane = 'callback_' + self.adapter._open_flow(flow_cookie, browser).purpose
            if lane not in ('signup', 'login', 'callback_signup', 'callback_login', 'logout', 'status'):
                raise ValueError()
            if not await self.budget(request, lane, browser):
                return self.result(browser, 'limited', json_response=path == '/auth/complete')
            if path == '/auth/start':
                if values['choice'] not in ('same', 'different'):
                    raise ValueError()
                outcome = None
                try:
                    old = cookie(request, COOKIE_NAME, 3500)
                    if old:
                        try:
                            session = self.adapter.codec.open(old)
                        except SessionInvalid:
                            session = None
                        if session and not await self.coordination.revoke(session):
                            raise StoreUnavailable()
                    if flow_cookie:
                        previous = await self.adapter.status(flow_cookie, browser, cancel=True)
                        if previous.status == 'unavailable':
                            raise StoreUnavailable()
                    outcome = await self.adapter.begin(browser, values['purpose'], different_account=values['choice'] == 'different')
                    response = self.authorize_page(outcome.authorization_url) if outcome.status == 'reserved' else self.result(browser, outcome.status)
                except StoreUnavailable:
                    response = self.result(browser, 'unavailable')
                for name in (COOKIE_NAME, SESSION_COOKIE_NAME, LOGIN_FLOW_COOKIE_NAME):
                    response.delete_cookie(name, path='/', secure=True, httponly=True, samesite='lax')
                if outcome and outcome.status == 'reserved':
                    set_cookie(response, FLOW, outcome.cookie)
                return response
            if path == '/auth/complete':
                if bool(values['code']) == bool(values['error']):
                    raise ValueError()
                outcome = await self.adapter.complete(flow_cookie, browser, values['state'], values['code'], cancelled=bool(values['error']))
                return self.result(browser, outcome.status, json_response=True, session=outcome.cookie)
            if path == '/auth/cancel':
                outcome = await self.adapter.status(flow_cookie, browser, cancel=True)
                return self.result(browser, outcome.status)
            if path == '/auth/logout':
                return await self.logout(request)
            return JSONResponse({'detail': 'Not found.'}, 404)
        except (InvalidToken, SessionInvalid, ValueError, TypeError, KeyError, UnicodeError):
            return JSONResponse({'detail': MESSAGES['invalid']}, 400)
        except (StoreUnavailable, TimeoutError):
            if request.method == 'GET' and path in ('/auth/sign-in', '/auth/result'):
                response = self.page(browser or secrets.token_urlsafe(32), 'unavailable')
                response.status_code = 503
                return response
            return JSONResponse({'detail': MESSAGES['unavailable']}, 503)


def enrollment_key(existing_keys):
    """Explicit opt-in; disabled construction never reads an enrollment key."""
    flag = os.environ.get('CAREERTRELLIS_AUTH_V2_TOTP_ENABLED', '0')
    if flag not in ('0','1'):
        raise ValueError('Invalid enrollment configuration.')
    if flag == '0':
        return None
    key = os.environ.get('CAREERTRELLIS_AUTH_V2_TOTP_KEY', '')
    # Fernet also checks encoding/length; compare decoded bytes, not spelling.
    Fernet(key)
    raw = base64.b64decode(key, altchars=b'-_', validate=True)
    if len(raw)!=32 or raw in {base64.urlsafe_b64decode(value) for value in existing_keys}:
        raise ValueError('Distinct enrollment key required.')
    return key


from factor_runtime import (factor_readiness, removal_enabled, recovery_enabled,
                            recovery_readiness, build_recovery_credentials)


async def create_runtime(settings, static_dir):
    if not enabled():
        return None
    engines = []
    provider = None
    factor_provider = None
    removal_provider = None
    try:
        if settings is None:
            raise ValueError()
        def required(name):
            value = os.environ.get('CAREERTRELLIS_AUTH_V2_' + name, '')
            if not value:
                raise ValueError()
            return value
        provider_settings = ProviderSettings(settings.client_id, settings.api_key,
            required('ISSUER'), required('GENERATION'), os.environ.get('CAREERTRELLIS_AUTH_V2_AUDIENCE') or None)
        key, flow_key = required('ENVELOPE_KEY'), required('FLOW_KEY')
        if len({base64.urlsafe_b64decode(value) for value in (key, flow_key, settings.cookie_password)}) != 3:
            raise ValueError()
        factor_key = enrollment_key((key, flow_key, settings.cookie_password))
        enable_removal = removal_enabled(factor_key is not None)
        enable_recovery = recovery_enabled(factor_key is not None)
        codec = SessionCodec(key, settings.cookie_password, key_id=required('KEY_ID'),
            generation=provider_settings.generation, lifetime_seconds=int(required('SESSION_SECONDS')),
            provider_binding=provider_settings.binding, require_epoch=True)
        urls = [database_url_from_environment('CAREERTRELLIS_ADMISSION_DATABASE_URL'), database_url_from_environment()]
        for url, principal in zip(urls, ('careertrellis_admission_broker', 'careertrellis_runtime')):
            if (url.username != principal or not url.password or not url.host or ',' in url.host
                    or any(name not in ('sslmode', 'sslrootcert') for name in url.query)
                    or any(not isinstance(value, str) for value in url.query.values())):
                raise ValueError()
        if any((url.host, url.port or 5432, url.database, url.query) !=
               (urls[0].host, urls[0].port or 5432, urls[0].database, urls[0].query) for url in urls):
            raise ValueError()
        for url in urls:
            engines.append(create_async_engine(url, hide_parameters=True, pool_size=2,
                max_overflow=0, pool_timeout=2, connect_args={'connect_timeout': 3}, pool_pre_ping=True))
        store = AsyncAdmissionStore(*engines, provider_binding=provider_settings.binding)
        for engine, principal in zip(engines, ('careertrellis_admission_broker', 'careertrellis_runtime')):
            if await store._query(engine, 'SELECT session_user') != principal:
                raise ValueError()
        provider = WorkOSTransportCandidate(provider_settings)
        adapter = AdmissionAdapterCandidate(store, provider, codec, owner_subject=settings.owner_workos_user_id,
            flow_key=flow_key, redirect_uri=settings.redirect_uri)
        await adapter.context()
        runtime = SignupHTTP(adapter, key=key, logout_return_to=settings.logout_return_to,
            static_dir=static_dir, engines=engines)
        if factor_key is not None:
            from enrollment_disposition_postgres import PostgreSQLEnrollmentDispositionStore
            from enrollment_disposition import AttributableEnrollmentCoordinator
            from totp_http import EnrollmentHTTP
            from workos_totp import WorkOSTOTP
            await factor_readiness(store,engines[1])
            factor_provider = WorkOSTOTP(settings.api_key)
            enrollment_store = PostgreSQLEnrollmentDispositionStore(engines[1],runtime.coordination)
            coordinator = AttributableEnrollmentCoordinator(enrollment_store,factor_provider,factor_key)
            runtime.enrollment_provider = factor_provider
            runtime.enrollment_http = EnrollmentHTTP(runtime,coordinator)
            if enable_removal or enable_recovery:
                from workos_removal import WorkOSRemoval
                removal_provider = WorkOSRemoval(provider_settings)
                runtime.removal_provider = removal_provider
            if enable_removal:
                from factor_removal import SessionEndingRemovalCoordinator
                from factor_removal_postgres import PostgreSQLRemovalStore
                from factor_removal_http import RemovalHTTP
                removal = SessionEndingRemovalCoordinator(PostgreSQLRemovalStore(engines[1],runtime.coordination),removal_provider)
                runtime.removal_http = RemovalHTTP(runtime,removal,enrollment_store)
            if enable_recovery:
                from recovery_credentials_http import RecoveryCredentialsHTTP
                await recovery_readiness(store, engines[1])
                credentials = build_recovery_credentials(engines[1], runtime.coordination, removal_provider)
                runtime.recovery_http = RecoveryCredentialsHTTP(runtime, credentials, enrollment_store)
        return runtime
    except BaseException as exc:
        await asyncio.gather(*([provider.aclose()] if provider else []),
            *([factor_provider.aclose()] if factor_provider else []),
            *([removal_provider.aclose()] if removal_provider else []),
            *(engine.dispose() for engine in engines), return_exceptions=True)
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        raise AuthenticationConfigurationError('The v2 account boundary is not ready.') from None
