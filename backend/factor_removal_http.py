"""Explicitly injected removal route; factory leaves it disabled.

Requires schema0020 store: completion/finalization atomically revokes initiating
and completing local sessions. No provider logout or all-session claim is made.
"""
from dataclasses import asdict
import html
import re
from uuid import uuid4

from cryptography.fernet import InvalidToken
from starlette.responses import HTMLResponse

from account_session_candidate import SessionInvalid, StoreUnavailable
from factor_removal import RemovalRejected, RemovalUncertain, SessionEndingRemovalCoordinator
from signup_http import clear_auth
from totp_enrollment import EnrollmentRejected, EnrollmentUncertain
from totp_http import EnrollmentHTTP
from workos_transport_candidate import ProviderRejected, ProviderUnavailable

BASE='/account/security/removal'
MESSAGES={
    'ready':'Remove your enrolled authenticator only if you intend to stop using it. A fresh authenticator code is required.',
    'code':'Enter the code from this authenticator to confirm removal.',
    'code_not_accepted':'That code was not accepted. Check your authenticator before trying another code.',
    'unresolved':'Removal is not confirmed. Check its status; do not start another removal.',
    'recorded':'The authenticator was removed. Finish to end this local session.',
    'complete':'The authenticator was removed and this local session was ended. Sign in again to continue.',
    'fresh':'Sign in again to manage authenticators.',
    'unavailable':'Authenticator removal is unavailable. No automatic retry was made.',
}

class RemovalHTTP(EnrollmentHTTP):
    def __init__(self,runtime,coordinator,enrollment_store):
        if not isinstance(coordinator,SessionEndingRemovalCoordinator):
            raise ValueError('Session-ending removal coordinator required.')
        super().__init__(runtime,coordinator)
        self.enrollment_store=enrollment_store

    def page(self,status,*,browser=None,actor=None,operation=None,factor=None,code_form=False,http_status=200):
        esc=lambda value:html.escape(value,quote=True)
        content='<p role="status">'+esc(MESSAGES.get(status,MESSAGES['unavailable']))+'</p>'
        if browser and actor and operation and factor:
            csrf=self.runtime.token(browser,'csrf')
            def form(verb,label,fields=''):
                action=self.runtime.token(browser,'removal-'+verb,actor=asdict(actor),operation=operation,factor=factor)
                return ('<form method="post" action="'+BASE+'/'+verb+'">'
                    '<input type="hidden" name="csrf" value="'+esc(csrf)+'">'
                    '<input type="hidden" name="action" value="'+esc(action)+'">'+fields+
                    '<button type="submit" disabled>'+label+'</button></form>')
            if status=='ready': content+=form('start','Remove this authenticator')
            else:
                if code_form:
                    content+=form('verify','Confirm removal','<label>Authenticator code '
                        '<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required></label>')
                content+=form('check','Finish and sign in again' if status=='recorded' else 'Check removal status')
        content+='<p><a href="/auth/initiate">Sign in</a> · <a href="/">Return to workspace</a></p>'
        return HTMLResponse('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Remove authenticator · CareerTrellis</title><link rel="stylesheet" href="/static/signup-v2.css">'
            '<script src="/static/factor-removal.js" defer></script></head><body><main>'
            '<h1 tabindex="-1">Remove authenticator</h1><noscript>JavaScript is required for protected removal.</noscript>'
            +content+'</main></body></html>',status_code=http_status,headers={
                'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff',
                'Content-Security-Policy':"default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})

    async def handle(self,request):
        if request.url.query: return self.page('unavailable',http_status=400)
        try:
            browser,actor=await self.actor(request)
            if request.method=='GET' and request.url.path==BASE:
                row=await self.coordinator.store.get(actor)
                if row is None:
                    enrolled=await self.enrollment_store.get(actor)
                    if (enrolled is None or enrolled.actor.authorization!=actor.authorization
                            or enrolled.state!='complete' or type(enrolled.factor) is not str
                            or re.fullmatch(r'auth_factor_[A-Za-z0-9_]{6,160}',enrolled.factor) is None):
                        raise RemovalRejected()
                    return self.page('ready',browser=browser,actor=actor,operation=str(uuid4()),factor=enrolled.factor)
                ready=row.state=='challenge_ready' and row.actor==actor and self.coordinator.now()<row.deadline
                return self.page('recorded' if row.state=='complete' else 'code' if ready else 'unresolved',
                    browser=browser,actor=actor,operation=row.operation,factor=row.factor,code_form=ready)
            verb=request.url.path.removeprefix(BASE+'/')
            if request.method!='POST' or verb not in ('start','verify','check'):
                return self.page('unavailable',http_status=404)
            _,fields=await self.runtime.body(request,{'action','code'} if verb=='verify' else {'action'})
            action=self.runtime.open_token(fields['action'],browser,'removal-'+verb)
            if action.get('actor')!=asdict(actor): raise RemovalRejected()
            operation,factor=action.get('operation'),action.get('factor')
            if not isinstance(operation,str) or not isinstance(factor,str): raise RemovalRejected()
            if verb=='start':
                await self.coordinator.start(actor,operation,factor)
                return self.page('code',browser=browser,actor=actor,operation=operation,factor=factor,code_form=True)
            row=await self.coordinator.store.get(actor,operation)
            if row is None or row.factor!=factor: raise RemovalRejected()
            if verb=='verify': result=await self.coordinator.verify(actor,operation,fields['code'])
            else: result=await self.coordinator.reconcile(actor,operation)
            if result=='complete':
                request.state.suppress_refreshed_session=True
                response=self.page('complete'); clear_auth(response)
                return response
            return self.page(result,browser=browser,actor=actor,operation=operation,factor=factor,
                code_form=result=='code_not_accepted')
        except (RemovalRejected,EnrollmentRejected,SessionInvalid,ProviderRejected):
            return self.page('fresh',http_status=403)
        except (RemovalUncertain,EnrollmentUncertain,StoreUnavailable,ProviderUnavailable):
            return self.page('unavailable',http_status=503)
        except (ValueError,InvalidToken,TypeError):
            return self.page('unavailable',http_status=400)
