"""Test-only ASGI browser facade; no launcher, deployment route or real provider.

Tests use TestClient and a disposable loopback HTTPS server with a synthetic
certificate. This module never starts a listener or calls the main application.
"""

import hmac
import html
import secrets
from urllib.parse import parse_qs, urlencode

from cryptography.fernet import InvalidToken
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from .integration import IntegrationAdapter, TOKEN, digest


ORIGIN = 'https://127.0.0.1:8766'
BROWSER = '__Host-ct_test_browser'
FLOW = '__Host-ct_test_flow'
SESSION = '__Host-ct_test_session'
RECEIPT = '__Host-ct_test_receipt'
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
           'Content-Security-Policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
           'X-Content-Type-Options': 'nosniff'}


def cookie(response, name, value, age=900):
    response.set_cookie(name, value, secure=True, httponly=True, samesite='lax', path='/', max_age=age)


def create_app(adapter):
    if type(adapter) is not IntegrationAdapter:
        raise ValueError('An isolated integration adapter is required')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def browser(request):
        value = request.cookies.get(BROWSER, '')
        return value if TOKEN.fullmatch(value) else ''

    def receipt(request, status):
        response = RedirectResponse('/result', status_code=303)
        cookie(response, RECEIPT, adapter._encrypt('receipt', {'browser': digest(browser(request)), 'status': status}))
        return response

    @app.middleware('http')
    async def boundary(request, call_next):
        if (request.headers.get('host') != '127.0.0.1:8766' or request.url.scheme != 'https'
                or len(request.scope.get('query_string', b'')) > 4096
                or len(request.headers.get('cookie', '')) > 14000):
            response = JSONResponse({'status': 'invalid'}, status_code=400)
        else:
            response = await call_next(request)
        response.headers.update(HEADERS)
        if request.method == 'GET' and request.url.path == '/' and response.status_code == 200:
            response.headers['Referrer-Policy'] = 'same-origin'
        return response

    @app.get('/api/version')
    def version():
        return {'build': 'isolated-synthetic-integration', 'application_release': False}

    @app.get('/')
    def home(request: Request):
        context = browser(request) or secrets.token_urlsafe(32)
        csrf = adapter._encrypt('form', {'browser': digest(context)})
        status = adapter.store.availability()['status']
        signup = '<button name="mode" value="signup">Create test account</button>' if status == 'available' else ''
        response = HTMLResponse('<!doctype html><html lang="en"><title>Isolated signup integration</title>'
            '<h1>Isolated synthetic integration</h1><p>No real accounts or provider calls.</p>'
            f'<p id="availability">{html.escape(status)}</p><form method="post" action="/start">'
            f'<input type="hidden" name="csrf" value="{csrf}">{signup}'
            '<button name="mode" value="login">Sign in to test account</button></form></html>')
        cookie(response, BROWSER, context)
        return response

    @app.post('/start')
    async def start(request: Request):
        if not browser(request) or request.headers.get('origin') != ORIGIN:
            return JSONResponse({'status': 'invalid_origin_or_browser'}, status_code=403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024:
                return JSONResponse({'status': 'invalid_body'}, status_code=400)
        try:
            form = parse_qs(body.decode('ascii'), strict_parsing=True, keep_blank_values=True)
            if set(form) != {'mode', 'csrf'} or any(len(v) != 1 for v in form.values()):
                raise ValueError('invalid form')
            csrf = adapter._decrypt(form['csrf'][0], 'form', 600)
            if set(csrf) != {'browser'} or not hmac.compare_digest(csrf['browser'], digest(browser(request))):
                raise ValueError('invalid form')
            outcome = await run_in_threadpool(adapter.begin, browser(request), form['mode'][0])
        except (ValueError, InvalidToken, KeyError, TypeError, UnicodeError):
            return JSONResponse({'status': 'invalid_form'}, status_code=403)
        if outcome.status != 'reserved':
            return receipt(request, outcome.status)
        response = RedirectResponse('/provider?' + urlencode({'state': outcome.state}), status_code=303)
        cookie(response, FLOW, outcome.cookie)
        response.delete_cookie(SESSION, path='/', secure=True, httponly=True, samesite='lax')
        return response

    @app.get('/provider')
    def provider(request: Request):
        try:
            flow = adapter.open_flow(request.cookies.get(FLOW, ''), browser(request))
            states = request.query_params.getlist('state')
            if states != [flow.state]:
                raise ValueError('invalid flow')
            # Fixed synthetic identity only; no identity assertions come from HTTP.
            code = adapter.provider.issue(flow.state)
            return RedirectResponse('/callback?' + urlencode({'state': flow.state, 'code': code}), status_code=303)
        except (ValueError, InvalidToken, KeyError, TypeError):
            return receipt(request, 'invalid')

    @app.get('/callback')
    def callback(request: Request):
        params = request.query_params
        keys = set(params)
        if keys not in ({'code', 'state'}, {'error', 'state'}) or any(len(params.getlist(k)) != 1 for k in keys):
            return receipt(request, 'invalid')
        outcome = adapter.complete(request.cookies.get(FLOW, ''), browser(request), params.get('state', ''),
                                   params.get('code', ''), cancelled='error' in params)
        response = receipt(request, outcome.status)
        if outcome.cookie:
            cookie(response, SESSION, outcome.cookie, age=86400)
        return response

    @app.get('/result')
    def result(request: Request):
        status = 'no_browser_context'
        if browser(request):
            try:
                value = adapter._decrypt(request.cookies.get(RECEIPT, ''), 'receipt', 900)
                if set(value) != {'browser', 'status'} or value['browser'] != digest(browser(request)):
                    raise ValueError('invalid receipt')
                status = value['status']
                if status in ('activated', 'login'):
                    status = adapter.result(request.cookies.get(FLOW, ''), browser(request)).status
            except (ValueError, InvalidToken, KeyError, TypeError):
                status = 'no_result'
        return {'scope': 'isolated_real_schema_synthetic_only', 'status': status,
                'provider_tokens_persisted_in_admission_store': False}

    @app.get('/api/session')
    def session(request: Request):
        result = adapter.authenticate(request.cookies.get(SESSION, ''))
        return JSONResponse({'status': result.status}, status_code=503 if result.status == 'unavailable' else 200)

    return app
