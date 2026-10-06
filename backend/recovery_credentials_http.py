"""Protected, once-only recovery credential display and custody confirmation."""
from dataclasses import asdict
import html
import re
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from starlette.responses import HTMLResponse

from account_session_candidate import SessionInvalid, StoreUnavailable
from recovery_credentials import CredentialDisplay, RecoveryCredentialCoordinator
from recovery_factor_proof import (PURPOSES, RecoveryFactorProofCoordinator,
    RecoveryProofRejected, RecoveryProofUncertain)
from totp_enrollment import EnrollmentRejected, EnrollmentUncertain
from totp_http import EnrollmentHTTP
from workos_transport_candidate import ProviderRejected, ProviderUnavailable


BASE = '/account/security/recovery'
MESSAGES = {
    'ready': 'Save recovery codes before you lose your authenticator. Your current authenticator code is required.',
    'code': 'Enter a current code from your enrolled authenticator.',
    'code_not_accepted': 'That authenticator code was not accepted. Check your authenticator and enter another code.',
    'display': 'Save these eight recovery codes somewhere private. They appear only once. Paste one saved code below to activate the set.',
    'credentials_activated': 'Your recovery codes are active. Keep your saved copy private. Any previous set has been replaced.',
    'fresh': 'This action could not be verified. Sign in again before managing recovery codes.',
    'unavailable': 'Recovery codes could not be confirmed. Do not repeat this action. Contact support to check its outcome.',
}


class RecoveryCredentialsHTTP(EnrollmentHTTP):
    def __init__(self, runtime, coordinator, enrollment_store):
        if not isinstance(coordinator, RecoveryCredentialCoordinator):
            raise ValueError('Recovery credential coordinator required.')
        # Reuse the established signed-session/recent-authentication actor path.
        super().__init__(runtime, RecoveryFactorProofCoordinator(
            coordinator.store, coordinator.provider, now=coordinator.now))
        self.credentials, self.enrollment_store = coordinator, enrollment_store

    def page(self, status, *, browser=None, actor=None, operation=None, factor=None,
             purpose=None, codes=None, http_status=200):
        esc = lambda value: html.escape(value, quote=True)
        content = '<p role="status">' + esc(MESSAGES.get(status, MESSAGES['unavailable'])) + '</p>'
        if browser and actor and operation and factor:
            csrf = self.runtime.token(browser, 'csrf')
            def form(verb, label, selected, fields=''):
                action = self.runtime.token(browser, 'recovery-codes-' + verb,
                    actor=asdict(actor), operation=operation, factor=factor, purpose=selected)
                return ('<form method="post" action="' + BASE + '/' + verb + '">'
                    '<input type="hidden" name="csrf" value="' + esc(csrf) + '">'
                    '<input type="hidden" name="action" value="' + esc(action) + '">'
                    + fields + '<button type="submit" disabled>' + label + '</button></form>')
            if status == 'ready':
                content += form('start', 'Create my first recovery codes', 'issue_recovery_credentials')
                content += form('start', 'Replace my existing recovery codes', 'replace_recovery_credentials')
            elif status in ('code', 'code_not_accepted'):
                content += form('verify', 'Verify authenticator', purpose,
                    '<label>Authenticator code <input name="code" inputmode="numeric" '
                    'autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required></label>')
            elif status == 'display':
                if (type(codes) is not tuple or len(codes) != 8 or len(set(codes)) != 8
                        or any(type(code) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{43}', code) for code in codes)):
                    raise RecoveryProofUncertain()
                content += ('<label for="recovery-codes">Recovery codes</label>'
                    '<textarea id="recovery-codes" data-sensitive readonly rows="8" cols="48" '
                    'spellcheck="false" autocomplete="off">' + esc('\n'.join(codes)) + '</textarea>')
                content += form('confirm', 'Activate my saved recovery codes', purpose,
                    '<label>One saved recovery code <input data-sensitive type="password" name="code" '
                    'autocomplete="off" pattern="[A-Za-z0-9_-]{43}" maxlength="43" required></label>')
        content += '<p><a href="/account/security">Account security</a> · <a href="/auth/initiate">Sign in again</a></p>'
        return HTMLResponse('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Recovery codes · CareerTrellis</title><link rel="stylesheet" href="/static/signup-v2.css">'
            '<script src="/static/recovery-credentials.js" defer></script></head><body><main>'
            '<h1 tabindex="-1">Recovery codes</h1><noscript>JavaScript is required for protected recovery codes.</noscript>'
            + content + '</main></body></html>', status_code=http_status, headers={
                'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff',
                'Content-Security-Policy': "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})

    async def handle(self, request):
        if request.url.query:
            return self.page('unavailable', http_status=400)
        try:
            browser, actor = await self.actor(request)
            if request.method == 'GET' and request.url.path == BASE:
                factor = await self.credentials.store.enrolled_factor(actor)
                if (type(factor) is not str
                        or re.fullmatch(r'auth_factor_[A-Za-z0-9_]{6,160}', factor) is None):
                    raise RecoveryProofRejected()
                return self.page('ready', browser=browser, actor=actor, operation=str(uuid4()), factor=factor)
            verb = request.url.path.removeprefix(BASE + '/')
            if request.method != 'POST' or verb not in ('start', 'verify', 'confirm'):
                return self.page('unavailable', http_status=404)
            _, fields = await self.runtime.body(request, {'action'} if verb == 'start' else {'action', 'code'})
            action = self.runtime.open_token(fields['action'], browser, 'recovery-codes-' + verb)
            if action.get('actor') != asdict(actor):
                raise RecoveryProofRejected()
            operation, factor, purpose = (action.get(k) for k in ('operation', 'factor', 'purpose'))
            if (type(operation) is not str or str(UUID(operation)) != operation or purpose not in PURPOSES
                    or type(factor) is not str or re.fullmatch(r'auth_factor_[A-Za-z0-9_]{6,160}', factor) is None):
                raise RecoveryProofRejected()
            if verb == 'start':
                await self.credentials.start(actor, operation, factor, purpose)
                status, codes = 'code', None
            elif verb == 'verify':
                result = await self.credentials.verify(actor, operation, factor, purpose, fields['code'])
                if isinstance(result, CredentialDisplay):
                    if result.operation != operation:
                        raise RecoveryProofUncertain()
                    status, codes = 'display', result.codes
                elif result == 'code_not_accepted':
                    status, codes = result, None
                else:
                    raise RecoveryProofUncertain()
            else:
                status = await self.credentials.confirm_custody(actor, operation, purpose, fields['code'])
                if status != 'credentials_activated':
                    raise RecoveryProofUncertain()
                return self.page(status)
            return self.page(status, browser=browser, actor=actor, operation=operation,
                             factor=factor, purpose=purpose, codes=codes)
        except (RecoveryProofRejected, EnrollmentRejected, SessionInvalid, ProviderRejected):
            return self.page('fresh', http_status=403)
        except (RecoveryProofUncertain, EnrollmentUncertain, StoreUnavailable, ProviderUnavailable):
            return self.page('unavailable', http_status=503)
        except (ValueError, InvalidToken, TypeError, KeyError):
            return self.page('unavailable', http_status=400)
