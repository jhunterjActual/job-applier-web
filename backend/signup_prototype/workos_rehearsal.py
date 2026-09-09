"""Operator-run, loopback-only WorkOS correlation proof. Not an app endpoint.

No application/database configuration, API key, persistent identity store, or
account provisioning. The operator must first verify a dedicated WorkOS staging
project and enter only its public client ID in their own visible terminal.
"""

from dataclasses import dataclass
import getpass
import hashlib
import html
import hmac
import json
import logging
import re
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from workos import WorkOSClient


ORIGIN = 'http://127.0.0.1:8765'
CALLBACK = ORIGIN + '/callback'
COOKIE = 'ct_workos_rehearsal'
TOKEN = re.compile(r'^[A-Za-z0-9_-]{43,128}$')
HEADERS = {
    'Cache-Control': 'no-store',
    'Pragma': 'no-cache',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    'X-Content-Type-Options': 'nosniff',
}


def public_client(client_id):
    if not re.fullmatch(r'client_[A-Za-z0-9_]{6,120}', client_id):
        raise ValueError('A rehearsal client ID is required; do not enter an API key')
    # Explicit values prevent inherited environment credentials/base URLs/retries.
    return WorkOSClient(client_id=client_id, is_public=True,
                        base_url='https://api.workos.com', request_timeout=10, max_retries=0)


@dataclass(repr=False)
class Pending:
    reservation: str
    browser: str
    verifier: str
    deadline: float
    purpose: str
    code_digest: str = ''
    outcome: str = ''


class CorrelationProof:
    """Volatile protocol evidence only; PostgreSQL admission is a separate gate."""

    def __init__(self, client, clock=time.monotonic):
        self.client = client
        self.clock = clock
        self.pending = {}
        self.lock = threading.Lock()

    def begin(self, browser, purpose, ttl=600):
        if not TOKEN.fullmatch(browser) or purpose not in ('signup', 'login') or ttl not in (30, 600):
            raise ValueError('Invalid rehearsal flow')
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v.deadline > self.clock()}
            if len(self.pending) >= 8:
                raise ValueError('Rehearsal flow limit reached')
            auth = self.client.user_management.get_authorization_url_with_pkce(
                redirect_uri=CALLBACK, provider='authkit', prompt='login',
                screen_hint='sign-up' if purpose == 'signup' else 'sign-in')
            self.pending[auth['state']] = Pending(
                str(uuid4()), browser, auth['code_verifier'], self.clock() + ttl, purpose)
            return auth['url']

    def complete(self, browser, state, code='', provider_error=False):
        # Never expose raw callback values or SDK exception messages to a receipt.
        if not TOKEN.fullmatch(browser) or not TOKEN.fullmatch(state) or len(code) > 2048:
            return 'invalid_flow'
        with self.lock:
            flow = self.pending.get(state)
            if flow is None or not hmac.compare_digest(flow.browser, browser):
                return 'invalid_flow'
            if self.clock() >= flow.deadline:
                self.pending.pop(state, None)
                return 'expired'
            if flow.outcome and provider_error:
                return flow.outcome
            if provider_error:
                flow.outcome = 'provider_cancelled'
                flow.verifier = ''
                return flow.outcome
            if not code:
                return 'invalid_flow'
            digest = hashlib.sha256(code.encode('utf-8')).hexdigest()
            if flow.outcome:
                return flow.outcome if hmac.compare_digest(flow.code_digest, digest) else 'invalid_flow'
            flow.code_digest = digest
            # A transport failure may follow consumption at WorkOS. Do not replay
            # a possibly consumed code automatically or claim a successful proof.
            flow.outcome = 'exchange_unavailable'
            try:
                result = self.client.user_management.authenticate_with_code_pkce(
                    code=code, code_verifier=flow.verifier,
                    request_options={'max_retries': 0, 'timeout': 10})
                if self.clock() >= flow.deadline:
                    flow.outcome = 'expired'
                elif (result.impersonator is not None
                      or result.user.email_verified is not True
                      or not isinstance(result.user.id, str)
                      or not re.fullmatch(r'user_[A-Za-z0-9_]{6,120}', result.user.id)):
                    flow.outcome = 'identity_rejected'
                else:
                    # The verified immutable subject is obtained only from this
                    # exchange. Do not store it or tokens, or bind by email.
                    flow.outcome = 'signup_correlated' if flow.purpose == 'signup' else 'verified_login_only'
            except Exception:
                pass
            finally:
                flow.verifier = ''
            return flow.outcome


def create_rehearsal_app(proof):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    browsers = {}

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        if (request.headers.get('host') != '127.0.0.1:8765'
                or len(request.scope.get('query_string', b'')) > 4096
                or len(request.headers.get('cookie', '')) > 4096):
            return Response(status_code=400, headers=HEADERS)
        try:
            response = await call_next(request)
        except Exception:
            response = JSONResponse({'status': 'rehearsal_unavailable'}, status_code=503)
        response.headers.update(HEADERS)
        if request.method == 'GET' and request.url.path == '/' and response.status_code == 200:
            # no-referrer turns native form POST Origin into "null" in browsers.
            # Only this non-sensitive local form page permits same-origin referrers;
            # callbacks/results/provider links retain no-referrer protection.
            response.headers['Referrer-Policy'] = 'same-origin'
        return response

    def known_browser(request):
        browser = request.cookies.get(COOKIE, '')
        return browser if TOKEN.fullmatch(browser) and browser in browsers else ''

    @app.get('/')
    def index(request: Request):
        browser = known_browser(request)
        if not browser:
            if len(browsers) >= 8:
                return Response('Rehearsal browser limit reached. Restart the harness.', status_code=429)
            browser = secrets.token_urlsafe(32)
            browsers[browser] = {'csrf': secrets.token_urlsafe(32), 'result': 'not_started'}
        csrf = browsers[browser]['csrf']
        response = HTMLResponse('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Isolated WorkOS rehearsal</title><h1>Isolated WorkOS rehearsal</h1>'
            '<p>This is not CareerTrellis. No application account is created.</p>'
            '<p>Use only the approved rehearsal environment and a test identity you control. '
            'Do not paste callback URLs, codes, credentials, or screenshots of them into chat.</p>'
            f'<form method="post" action="/start"><input type="hidden" name="csrf" value="{csrf}">'
            '<button name="mode" value="signup">Start signup proof (10 minutes)</button> '
            '<button name="mode" value="expiry">Start expiry proof (30 seconds)</button> '
            '<button name="mode" value="login">Start login-only control</button></form>'
            '<p><a href="/result">Read sanitized result</a></p></html>')
        # HTTP loopback is the documented development exception, not a hosted
        # cookie policy. No provider tokens are placed in this cookie.
        response.set_cookie(COOKIE, browser, httponly=True, samesite='lax', max_age=1800)
        return response

    @app.post('/start')
    async def start(request: Request):
        browser = known_browser(request)
        if not browser:
            return JSONResponse({'status': 'browser_context_missing',
                'next_step': 'Open the rehearsal home page again in this browser.'}, status_code=403)
        if request.headers.get('origin') != ORIGIN:
            return JSONResponse({'status': 'request_origin_rejected',
                'next_step': 'Restart the updated harness and reopen its home page.'}, status_code=403)
        body = b''
        async for chunk in request.stream():
            body += chunk
            if len(body) > 512:
                return Response(status_code=400)
        try:
            form = parse_qs(body.decode('ascii'), strict_parsing=True, max_num_fields=2)
            if (set(form) != {'csrf', 'mode'} or any(len(v) != 1 for v in form.values())
                    or not hmac.compare_digest(form['csrf'][0], browsers[browser]['csrf'])):
                return JSONResponse({'status': 'form_context_invalid',
                    'next_step': 'Open the rehearsal home page again; do not resubmit an old form.'}, status_code=403)
            mode = form['mode'][0]
            if mode not in ('signup', 'expiry', 'login'):
                return Response(status_code=400)
            url = proof.begin(browser, 'login' if mode == 'login' else 'signup',
                              ttl=30 if mode == 'expiry' else 600)
        except Exception:
            return JSONResponse({'status': 'start_unavailable'}, status_code=503)
        browsers[browser]['result'] = 'awaiting_provider'
        # A form POST followed by cross-origin redirects is blocked by some
        # browsers' form-action enforcement. Use a normal link instead; keep
        # the restrictive form policy and no-referrer header intact.
        return HTMLResponse('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<title>Continue isolated rehearsal</title><h1>Continue isolated rehearsal</h1>'
            '<p>Complete authentication yourself using only your approved test identity.</p>'
            f'<a href="{html.escape(url, quote=True)}" rel="noreferrer">Continue to isolated AuthKit</a></html>')

    @app.get('/callback')
    def callback(request: Request):
        browser = known_browser(request)
        values = request.query_params
        if (not browser or any(len(values.getlist(k)) > 1 for k in values)
                or set(values) - {'state', 'code', 'error', 'error_description'}
                or ('code' in values and 'error' in values)):
            outcome = 'invalid_flow'
        else:
            outcome = proof.complete(browser, values.get('state', ''), values.get('code', ''),
                                     provider_error='error' in values)
        if browser:
            browsers[browser]['result'] = outcome
        # Remove the sensitive query from the displayed URL immediately. Never
        # include it in HTML, redirects, diagnostics, or application logs.
        return RedirectResponse('/result', status_code=303)

    @app.get('/result')
    def result(request: Request):
        browser = known_browser(request)
        return JSONResponse({'scope': 'isolated_workos_protocol_only',
                             'status': browsers[browser]['result'] if browser else 'no_browser_context',
                             'application_accounts_created': 0,
                             'provider_tokens_retained': False})

    return app


def main():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('Run this helper in your own visible terminal; redirected input is refused.')
        return 2
    logging.disable(logging.CRITICAL)
    try:
        confirmation = input('Confirm the separate CareerTrellis Auth Rehearsal / Staging project (type ISOLATED STAGING): ')
        if confirmation != 'ISOLATED STAGING':
            print('Not started.')
            return 2
        client_id = getpass.getpass('Rehearsal public Client ID (hidden; NOT an API key): ')
        client = public_client(client_id.strip())
        proof = CorrelationProof(client)
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(create_rehearsal_app(proof), host='127.0.0.1',
            port=8765, access_log=False, log_config=None, log_level='critical', proxy_headers=False))
        timer = threading.Timer(1800, lambda: setattr(server, 'should_exit', True))
        timer.daemon = True
        timer.start()
        print('Starting at ' + ORIGIN + '/. Open it in your browser to confirm readiness. Stop with Ctrl+C; automatic stop is 30 minutes.')
        try:
            server.run()
        except KeyboardInterrupt:
            pass
        finally:
            timer.cancel()
            proof.pending.clear()
            client.close()
        print(json.dumps({'operation': 'isolated_workos_rehearsal', 'result': 'stopped'}))
        return 0
    except (Exception, SystemExit, KeyboardInterrupt):
        print('{"operation":"isolated_workos_rehearsal","result":"not_completed"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
