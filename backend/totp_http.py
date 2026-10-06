"""Opt-in protected enrollment screen. Runtime factory does not enable it yet."""
import html
import re
from dataclasses import asdict

from cryptography.fernet import InvalidToken
from starlette.responses import HTMLResponse

from account_session_candidate import SessionInvalid, StoreUnavailable
from signup_http import BROWSER, COOKIE_NAME, cookie
from totp_enrollment import EnrollmentActor, EnrollmentRejected, EnrollmentUncertain
from workos_transport_candidate import ProviderRejected, ProviderUnavailable


BASE = '/account/security'
MESSAGES = {
    'ready': 'Adding an authenticator is optional. You can keep using CareerTrellis without setting one up.',
    'pending': 'Add the setup key to your authenticator app, then enter its six-digit code.',
    'complete': 'Your authenticator enrollment is verified.',
    'code_not_accepted': 'That code was not accepted. Check your authenticator app before trying another code.',
    'unresolved': 'We cannot yet confirm the outcome. Check completion before starting any new setup.',
    'closed_outcome_unresolved': 'Setup is closed. A code already submitted may still complete enrollment. Closing does not remove an authenticator.',
    'fresh': 'Sign in again to manage authenticators. We need recent authentication for this action.',
    'unavailable': 'Authenticator setup is unavailable. No automatic retry will be made.',
}


class EnrollmentHTTP:
    def __init__(self, runtime, coordinator):
        self.runtime, self.coordinator = runtime, coordinator

    async def actor(self, request):
        runtime = self.runtime
        if not runtime.adapter.codec.require_epoch:
            raise EnrollmentRejected()
        browser = cookie(request, BROWSER, 43)
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', browser):
            raise EnrollmentRejected()
        value = cookie(request, COOKIE_NAME, 3500)
        outcome = await runtime.adapter.authenticate(value)
        if outcome.status != 'authenticated' or outcome.authorization is None:
            raise EnrollmentRejected()
        session = runtime.adapter.codec.open(value)
        data = runtime.adapter.codec.provider_open(session.provider)
        claims = await runtime.adapter.provider.verify_recent(data['access_token'], session.subject, session.session_id)
        actor = EnrollmentActor(outcome.authorization, session.session_id,
            runtime.coordination.digest('browser', browser), claims['auth_time'])
        await self.coordinator._eligible(actor)
        return browser, actor

    def page(self, status, *, browser=None, actor=None, operation=None, secret=None, code_form=False, http_status=200):
        esc = lambda value: html.escape(value, quote=True)
        message = MESSAGES.get(status, MESSAGES['unavailable'])
        content = '<p role="status">' + esc(message) + '</p>'
        if status == 'fresh':
            content += '<p><a href="/auth/initiate">Sign in again</a></p>'
        if browser and actor:
            # Both the generic origin/browser CSRF and this account/session/action
            # token are required. An account switch cannot reuse the old form.
            csrf = self.runtime.token(browser, 'csrf')
            action = self.runtime.token(browser, 'totp-action', actor=asdict(actor), operation=operation)
            def form(path, label, fields=''):
                return ('<form method="post" action="' + BASE + '/' + path + '">'
                    '<input type="hidden" name="csrf" value="' + esc(csrf) + '">'
                    '<input type="hidden" name="action" value="' + esc(action) + '">'
                    + fields + '<button type="submit" disabled>' + label + '</button></form>')
            if status == 'ready':
                content += form('start', 'Add authenticator')
            if secret:
                content += '<p>Setup key: <code>' + esc(secret) + '</code></p>'
            if code_form:
                content += form('verify', 'Verify code', '<label>Authenticator code '
                    '<input name="code" inputmode="numeric" autocomplete="one-time-code" '
                    'pattern="[0-9]{6}" maxlength="6" required></label>')
            if operation and status != 'complete':
                content += form('check', 'Check completion') + form('close', 'Close setup')
        if getattr(self.runtime, 'recovery_http', None) is not None:
            content += '<p><a href="/account/security/recovery">Manage recovery codes</a></p>'
        content += '<p><a href="/">Return to workspace</a></p>'
        return HTMLResponse('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Account security · CareerTrellis</title>'
            '<link rel="stylesheet" href="/static/signup-v2.css">'
            '<script src="/static/totp-enrollment.js" defer></script></head>'
            '<body><main><h1 tabindex="-1">Account security</h1>'
            '<noscript>JavaScript is required for protected authenticator setup.</noscript>'
            + content + '</main></body></html>',
            status_code=http_status, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                'X-Content-Type-Options': 'nosniff',
                'Content-Security-Policy': "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})

    async def handle(self, request):
        if request.url.query:
            return self.page('unavailable', http_status=400)
        try:
            browser, actor = await self.actor(request)
            if request.method == 'GET' and request.url.path == BASE:
                row = await self.coordinator.store.get(actor)
                if row is None:
                    return self.page('ready', browser=browser, actor=actor)
                if row.actor.authorization != actor.authorization:
                    raise EnrollmentRejected()
                if row.state == 'complete':
                    return self.page('complete')
                if row.state == 'pending' and row.actor == actor and row.deadline > self.coordinator.now():
                    secret = await self.coordinator.setup_secret(actor, row.operation)
                    return self.page('pending', browser=browser, actor=actor, operation=row.operation, secret=secret, code_form=True)
                return self.page('unresolved', browser=browser, actor=actor, operation=row.operation)
            verb = request.url.path.removeprefix(BASE + '/')
            if request.method != 'POST' or verb not in ('start', 'verify', 'check', 'close'):
                return self.page('unavailable', http_status=404)
            _, fields = await self.runtime.body(request, {'action', 'code'} if verb == 'verify' else {'action'})
            action = self.runtime.open_token(fields['action'], browser, 'totp-action')
            if action.get('actor') != asdict(actor):
                raise EnrollmentRejected()
            operation = action.get('operation')
            if verb == 'start':
                if operation is not None:
                    raise EnrollmentRejected()
                operation = await self.coordinator.start(actor)
                secret = await self.coordinator.setup_secret(actor, operation)
                return self.page('pending', browser=browser, actor=actor, operation=operation, secret=secret, code_form=True)
            if not isinstance(operation, str):
                raise EnrollmentRejected()
            if verb == 'verify':
                result = await self.coordinator.verify(actor, operation, fields['code'])
            elif verb == 'check':
                result = await self.coordinator.reconcile(actor, operation)
            else:
                result = await self.coordinator.close(actor, operation)
            return self.page(result, browser=browser, actor=actor, operation=operation,
                             code_form=result == 'code_not_accepted')
        except (EnrollmentRejected, SessionInvalid, ProviderRejected):
            return self.page('fresh', http_status=403)
        except (EnrollmentUncertain, StoreUnavailable, ProviderUnavailable):
            return self.page('unavailable', http_status=503)
        except (ValueError, InvalidToken, TypeError):
            return self.page('unavailable', http_status=400)
