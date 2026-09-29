"""Actual application boundary with synthetic signed sessions and removal effects."""
from dataclasses import asdict, replace
import html
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from factor_removal import (SessionEndingRemovalCoordinator,FactorObservation,RemovalChallenge,
                           RemovalVerification,RemovalDeletion)
from factor_removal_http import BASE,RemovalHTTP
import signup_http
import test_totp_http as enrollment_http_tests
from test_provider_session_candidate import VerifiedTokens
from totp_enrollment import EnrollmentRecord
import unittest


FACTOR='auth_factor_removal_synthetic'
CHALLENGE='auth_challenge_removal_synthetic'


class RemovalStore:
    def __init__(self,runtime,auth_store):
        self.runtime=runtime; self.auth_store=auth_store; self.row=None
        self.reserves=0; self.completions=0; self.finalizations=0

    def key(self,actor):
        a=actor.authorization
        return self.runtime.coordination.digest('session',a.local_id,a.subject,actor.session_id)

    async def eligible(self,actor): return self.key(actor) not in self.auth_store.revoked

    async def get(self,actor,operation=None):
        row=self.row
        return row if row and row.actor.authorization==actor.authorization and (operation is None or operation==row.operation) else None

    async def reserve(self,row):
        if self.row is not None: return False
        self.row=row; self.reserves+=1
        return True

    async def cas(self,old,new,*,actor):
        if old!=self.row or actor.authorization!=old.actor.authorization: return False
        self.row=new
        if new.state=='complete':
            self.completions+=1
            self.auth_store.revoked.update((self.key(old.actor),self.key(actor)))
        return True

    async def finalize(self,actor,operation):
        if not self.row or self.row.operation!=operation or self.row.state!='complete': return False
        self.finalizations+=1
        self.auth_store.revoked.update((self.key(self.row.actor),self.key(actor)))
        return True


class RemovalProvider:
    def __init__(self,binding):
        self.binding=binding; self.effects=[]; self.observations=0; self.absent=False
        self.valid=True; self.lost_verify=False; self.lost_delete=False

    async def observe(self,subject,factor):
        self.observations+=1
        return FactorObservation(self.binding,subject,factor,None if self.absent else 'totp',
                                 'absent' if self.absent else 'present',True)

    async def challenge(self,subject,factor):
        self.effects.append('challenge')
        return RemovalChallenge(self.binding,subject,factor,CHALLENGE,int(time.time())+240)

    async def verify(self,subject,factor,challenge,code):
        self.effects.append('verify')
        if self.lost_verify: raise OSError('private code '+code)
        return RemovalVerification(self.binding,subject,factor,challenge,self.valid)

    async def delete(self,subject,factor,operation):
        self.effects.append('delete'); self.absent=True
        if self.lost_delete: raise OSError('private provider response')
        return RemovalDeletion(self.binding,subject,factor,operation,'deleted')


class RemovalHTTPTests(unittest.IsolatedAsyncioTestCase):
    setUpClass=classmethod(enrollment_http_tests.EnrollmentHTTPTests.setUpClass.__func__)
    headers=enrollment_http_tests.EnrollmentHTTPTests.headers

    async def asyncSetUp(self):
        await enrollment_http_tests.EnrollmentHTTPTests.asyncSetUp(self)
        self.runtime.enrollment_http=None
        self.removal_store=RemovalStore(self.runtime,self.store)
        self.removal_provider=RemovalProvider(self.codec.provider_binding)
        self.removal=SessionEndingRemovalCoordinator(self.removal_store,self.removal_provider)
        class Enrolled:
            async def get(_self,actor,operation=None):
                return EnrollmentRecord(str(uuid4()),actor,'complete',int(time.time())+240,
                                        FACTOR,CHALLENGE,None,1)
        self.handler=RemovalHTTP(self.runtime,self.removal,Enrolled())
        self.runtime.removal_http=self.handler

    def fields(self,response,verb):
        form=re.search(r'<form method="post" action="'+re.escape(BASE+'/'+verb)+r'">(.*?)</form>',response.text,re.S)
        self.assertIsNotNone(form,response.text)
        return {name:html.unescape(re.search('name="'+name+'" value="([^"]+)"',form[1])[1])
                for name in ('csrf','action')}

    async def pending(self):
        ready=await self.client.get(BASE,headers=self.headers())
        self.assertEqual(200,ready.status_code)
        fields=self.fields(ready,'start')
        pending=await self.client.post(BASE+'/start',headers=self.headers(),data=fields)
        self.assertEqual(200,pending.status_code)
        return pending,fields

    def fresh_session(self):
        session=self.codec.open(self.fresh_cookies[0])
        sid='session_removal_fresh_synthetic'
        identity=VerifiedTokens(session.subject,sid,session.generation,
            self.http.token(sub=session.subject,sid=sid,auth_time=int(time.time())-5),
            'synthetic-refresh',int(time.time())+300,session.provider_binding)
        self.fresh_cookies[0]=self.codec.pack(replace(session,session_id=sid,provider=self.codec.provider_seal(identity)))

    async def test_absent_handler_is404_and_routing_does_not_fall_into_enrollment(self):
        self.runtime.removal_http=None
        for method,path in (('GET',BASE),('POST',BASE+'/start')):
            response=await self.client.request(method,path,headers=self.headers())
            self.assertEqual(404,response.status_code)
        self.runtime.removal_http=self.handler
        self.assertEqual(200,(await self.client.get(BASE,headers=self.headers())).status_code)
        self.assertEqual([],self.removal_provider.effects)

    async def test_get_is_readonly_and_recovers_latest_operation(self):
        for _ in range(2):
            response=await self.client.get(BASE,headers=self.headers())
            self.assertEqual(200,response.status_code)
        self.assertIsNone(self.removal_store.row); self.assertEqual(0,self.removal_provider.observations)
        pending,_=await self.pending()
        operation=self.removal_store.row.operation
        response=await self.client.get(BASE,headers=self.headers())
        token=self.runtime.open_token(self.fields(response,'verify')['action'],'b'*43,'removal-verify')
        self.assertEqual(operation,token['operation'])
        self.assertEqual(['challenge'],self.removal_provider.effects)
        self.assertNotIn(FACTOR,response.text); self.assertNotIn(CHALLENGE,response.text)
        self.assertIn('no-store',response.headers['cache-control'])
        self.assertEqual('no-referrer',response.headers['referrer-policy'])

    async def test_origin_csrf_actor_and_browser_binding(self):
        ready=await self.client.get(BASE,headers=self.headers()); fields=self.fields(ready,'start')
        self.assertEqual(403,(await self.client.post(BASE+'/start',headers={**self.headers(),'Origin':'https://wrong.invalid'},data=fields)).status_code)
        self.assertEqual(400,(await self.client.post(BASE+'/start',headers=self.headers(),data={'action':fields['action']})).status_code)
        self.assertEqual(403,(await self.client.post(BASE+'/start',headers=self.headers(1),data=fields)).status_code)
        changed=self.headers(); changed['Cookie']=changed['Cookie'].replace('b'*43,'c'*43)
        self.assertEqual(400,(await self.client.post(BASE+'/start',headers=changed,data=fields)).status_code)
        self.assertEqual([],self.removal_provider.effects)

    async def test_action_purpose_and_factor_binding(self):
        pending,_=await self.pending()
        fields=self.fields(pending,'verify')
        self.assertEqual(400,(await self.client.post(BASE+'/check',headers=self.headers(),data=fields)).status_code)
        action=self.runtime.open_token(fields['action'],'b'*43,'removal-verify')
        fields['action']=self.runtime.token('b'*43,'removal-verify',actor=action['actor'],
            operation=action['operation'],factor='auth_factor_other_synthetic')
        self.assertEqual(403,(await self.client.post(BASE+'/verify',headers=self.headers(),data={**fields,'code':'123456'})).status_code)
        self.assertEqual(['challenge'],self.removal_provider.effects)

    async def test_duplicate_start_and_verify_after_uncertainty_do_not_replay(self):
        pending,start=await self.pending()
        self.assertEqual(403,(await self.client.post(BASE+'/start',headers=self.headers(),data=start)).status_code)
        self.removal_provider.lost_verify=True
        fields={**self.fields(pending,'verify'),'code':'123456'}
        uncertain=await self.client.post(BASE+'/verify',headers=self.headers(),data=fields)
        self.assertEqual(503,uncertain.status_code)
        self.assertNotIn('123456',uncertain.text); self.assertNotIn('private',uncertain.text)
        self.assertEqual(403,(await self.client.post(BASE+'/verify',headers=self.headers(),data=fields)).status_code)
        revisit=await self.client.get(BASE,headers=self.headers())
        checked=await self.client.post(BASE+'/check',headers=self.headers(),data=self.fields(revisit,'check'))
        self.assertEqual(200,checked.status_code); self.assertIn('not confirmed',checked.text)
        self.assertEqual(['challenge','verify'],self.removal_provider.effects)

    async def test_success_clears_all_cookies_suppresses_refresh_and_revokes(self):
        pending,_=await self.pending()
        seen=[]; original=self.handler.handle
        async def capture(request):
            request.state.refreshed_v2_session='synthetic-forbidden-refresh'
            result=await original(request)
            seen.append(getattr(request.state,'suppress_refreshed_session',False))
            return result
        self.handler.handle=capture
        done=await self.client.post(BASE+'/verify',headers=self.headers(),data={**self.fields(pending,'verify'),'code':'123456'})
        self.assertEqual(200,done.status_code); self.assertIn('local session was ended',done.text)
        self.assertEqual([True],seen)
        cookies=done.headers.get_list('set-cookie')
        for name in signup_http.AUTH_COOKIES:
            self.assertTrue(any(value.startswith(name+'=') and 'Max-Age=0' in value for value in cookies),name)
        self.assertFalse(any('synthetic-forbidden-refresh' in value for value in cookies))
        self.assertEqual(['challenge','verify','delete'],self.removal_provider.effects)
        self.assertEqual(1,self.removal_store.completions)
        self.assertEqual(403,(await self.client.get(BASE,headers=self.headers())).status_code)

    async def test_complete_get_is_readonly_and_fresh_check_finalizes_session(self):
        pending,_=await self.pending()
        await self.client.post(BASE+'/verify',headers=self.headers(),data={**self.fields(pending,'verify'),'code':'123456'})
        self.fresh_session()
        latest=await self.client.get(BASE,headers=self.headers())
        self.assertEqual(200,latest.status_code); self.assertIn('Finish to end',latest.text)
        self.assertEqual(0,self.removal_store.finalizations)
        done=await self.client.post(BASE+'/check',headers=self.headers(),data=self.fields(latest,'check'))
        self.assertEqual(200,done.status_code); self.assertEqual(1,self.removal_store.finalizations)
        self.assertEqual(['challenge','verify','delete'],self.removal_provider.effects)

    async def test_absence_after_lost_delete_ack_stays_unresolved(self):
        pending,_=await self.pending(); self.removal_provider.lost_delete=True
        result=await self.client.post(BASE+'/verify',headers=self.headers(),data={**self.fields(pending,'verify'),'code':'123456'})
        self.assertEqual(503,result.status_code)
        latest=await self.client.get(BASE,headers=self.headers())
        checked=await self.client.post(BASE+'/check',headers=self.headers(),data=self.fields(latest,'check'))
        self.assertIn('not confirmed',checked.text); self.assertEqual(0,self.removal_store.completions)
        self.assertEqual(['challenge','verify','delete'],self.removal_provider.effects)

    async def test_completion_race_finalizes_before_clearing_cookies(self):
        await self.pending()
        self.removal_store.row=replace(self.removal_store.row,state='delete_recorded',
            attempts=1,verified_at=int(time.time()),delete_acknowledged=True)
        latest=await self.client.get(BASE,headers=self.headers())
        original=self.removal_store.get; calls=0
        async def racing_get(actor,operation=None):
            nonlocal calls
            calls+=1
            if calls==2: self.removal_store.row=replace(self.removal_store.row,state='complete')
            return await original(actor,operation)
        self.removal_store.get=racing_get
        done=await self.client.post(BASE+'/check',headers=self.headers(),data=self.fields(latest,'check'))
        self.assertEqual(200,done.status_code); self.assertEqual(1,self.removal_store.finalizations)
        self.assertTrue(any('Max-Age=0' in value for value in done.headers.get_list('set-cookie')))
        self.assertEqual(['challenge'],self.removal_provider.effects)

    async def test_finalize_denial_does_not_claim_success_or_clear_cookies(self):
        await self.pending()
        self.removal_store.row=replace(self.removal_store.row,state='complete',
            attempts=1,verified_at=int(time.time()),delete_acknowledged=True)
        latest=await self.client.get(BASE,headers=self.headers())
        async def denied(actor,operation): return False
        self.removal_store.finalize=denied
        rejected=await self.client.post(BASE+'/check',headers=self.headers(),data=self.fields(latest,'check'))
        self.assertEqual(503,rejected.status_code)
        self.assertNotIn('session was ended',rejected.text)
        self.assertFalse(any('Max-Age=0' in value for value in rejected.headers.get_list('set-cookie')))

    async def test_missing_signed_freshness_and_revocation_block_before_mutation(self):
        self.assertEqual(403,(await self.client.get(BASE,headers=self.headers(fresh=False))).status_code)
        ready=await self.client.get(BASE,headers=self.headers())
        session=self.codec.open(self.fresh_cookies[0]); await self.runtime.coordination.revoke(session)
        self.assertEqual(403,(await self.client.post(BASE+'/start',headers=self.headers(),data=self.fields(ready,'start'))).status_code)
        self.assertEqual([],self.removal_provider.effects)

    async def test_invalid_code_can_retry_and_query_is_rejected(self):
        pending,_=await self.pending(); self.removal_provider.valid=False
        rejected=await self.client.post(BASE+'/verify',headers=self.headers(),data={**self.fields(pending,'verify'),'code':'123456'})
        self.assertEqual(200,rejected.status_code); self.assertIn('not accepted',rejected.text)
        self.assertEqual('challenge_ready',self.removal_store.row.state)
        self.assertEqual(400,(await self.client.get(BASE+'?operation=anything',headers=self.headers())).status_code)
        self.assertEqual(['challenge','verify'],self.removal_provider.effects)

    async def test_browser_lost_response_clears_code_and_never_retries(self):
        from playwright.async_api import async_playwright
        pending,_=await self.pending()
        posts=[]; disabled=[]
        async with async_playwright() as p:
            browser=await p.chromium.launch(channel='msedge' if os.name=='nt' else None,
                headless=True,args=['--host-resolver-rules=MAP * ~NOTFOUND'])
            try:
                page=await browser.new_page(service_workers='block',viewport={'width':375,'height':760})
                async def intercept(route):
                    if route.request.method=='POST':
                        posts.append(route.request.url)
                        disabled.append(await page.locator('button').evaluate_all('(buttons)=>buttons.every(button=>button.disabled)'))
                        await route.abort()
                    elif route.request.url.endswith('/static/factor-removal.js'):
                        await route.fulfill(content_type='text/javascript',
                            body=(Path(__file__).parent/'static/factor-removal.js').read_bytes())
                    elif route.request.url.endswith(BASE):
                        await route.fulfill(content_type='text/html',body=pending.content)
                    else: await route.abort()
                await page.route('**/*',intercept)
                await page.goto('https://synthetic.invalid'+BASE)
                await page.get_by_label('Authenticator code').fill('123456')
                await page.get_by_role('button',name='Confirm removal',exact=True).focus()
                await page.keyboard.press('Enter')
                link=page.get_by_role('link',name='Reopen removal',exact=True)
                await link.wait_for()
                self.assertEqual(BASE,await link.get_attribute('href'))
                self.assertEqual([True],disabled)
                self.assertEqual(['https://synthetic.invalid'+BASE+'/verify'],posts)
                self.assertEqual(0,await page.locator('input').count())
                self.assertNotIn('123456',await page.content())
                self.assertEqual('status',await page.evaluate('document.activeElement.getAttribute("role")'))
                self.assertEqual(['challenge'],self.removal_provider.effects)
            finally:
                await browser.close()


if __name__=='__main__': unittest.main()
