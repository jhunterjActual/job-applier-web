"""Real encrypted session boundary with synthetic epoch-authority responses."""
from dataclasses import replace
import time
import unittest
from uuid import uuid4

from account_session_candidate import SessionInvalid, StoreUnavailable
from test_provider_session_candidate import CandidateTests, SUBJECT
from workos_transport_candidate import VerifiedTokens


class SessionEpochTests(unittest.IsolatedAsyncioTestCase):
    setUpClass = classmethod(CandidateTests.setUpClass.__func__)
    flow = CandidateTests.flow
    activate = CandidateTests.activate

    async def asyncSetUp(self):
        await CandidateTests.asyncSetUp(self)
        self.codec.require_epoch=True
        self.epoch=1; self.incarnation=str(uuid4()); self.held=False
        original=self.store.finish
        async def finish(flow,attempt,identity):
            result=await original(flow,attempt,identity)
            return {**result,'epoch':self.epoch,'incarnation':self.incarnation}
        async def mapping(session):
            if self.store.fail_mapping: raise StoreUnavailable()
            if self.held or session.epoch!=self.epoch or session.incarnation!=self.incarnation: return None
            return await self.store.mapping(session.subject,session.local_id)
        self.store.finish=finish; self.store.session_mapping=mapping

    async def test_old_cookie_stays_invalid_after_hold_is_released(self):
        cookie=await self.activate()
        auth=await self.adapter.authenticate(cookie)
        self.assertEqual(1,auth.authorization.epoch)
        self.epoch+=1; self.held=True
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)
        self.held=False
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)
        flow,state=await self.flow('login')
        fresh=await self.adapter.complete(flow,self.browser,state,'synthetic-fresh')
        self.assertEqual(2,(await self.adapter.authenticate(fresh.cookie)).authorization.epoch)

    async def test_missing_malformed_or_reincarnated_evidence_fails_closed(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        for changes in ({'epoch':None},{'epoch':True},{'epoch':0},{'incarnation':'bad'},{'incarnation':None}):
            with self.subTest(changes=changes),self.assertRaises(SessionInvalid):
                self.codec.open(self.codec.pack(replace(session,**changes)))
        self.incarnation=str(uuid4())
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)

    async def test_refresh_preserves_epoch_and_original_session_lifetime(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        identity=VerifiedTokens(SUBJECT,session.session_id,session.generation,self.http.token(exp=int(time.time())-1),
            'synthetic-refresh-only',int(time.time())-1,session.provider_binding)
        expired=self.codec.pack(replace(session,provider=self.codec.provider_seal(identity)))
        result=await self.adapter.authenticate(expired,refresh=True)
        self.assertEqual('authenticated',result.status)
        refreshed=self.codec.open(result.cookie)
        self.assertEqual((session.issued,session.expires,session.epoch,session.incarnation),
            (refreshed.issued,refreshed.expires,refreshed.epoch,refreshed.incarnation))
        self.assertEqual(3600,refreshed.expires-refreshed.issued)

    async def test_refresh_race_cannot_upgrade_old_epoch(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        identity=VerifiedTokens(SUBJECT,session.session_id,session.generation,self.http.token(exp=int(time.time())-1),
            'synthetic-refresh-only',int(time.time())-1,session.provider_binding)
        expired=self.codec.pack(replace(session,provider=self.codec.provider_seal(identity)))
        async def containment(request):
            if request.method=='POST': self.epoch+=1
        self.http.hook=containment
        result=await self.adapter.authenticate(expired,refresh=True)
        self.assertEqual('unauthenticated',result.status); self.assertFalse(result.cookie)

    async def test_completion_cannot_recapture_epoch_after_finish(self):
        original=self.store.finish
        async def racing(flow,attempt,identity):
            receipt=await original(flow,attempt,identity)
            self.epoch+=1
            return receipt
        self.store.finish=racing
        flow,state=await self.flow()
        result=await self.adapter.complete(flow,self.browser,state,'synthetic-code')
        self.assertEqual('ineligible',result.status); self.assertFalse(result.cookie)

    async def test_store_outage_returns_no_authorization_and_keeps_cookie(self):
        cookie=await self.activate(); self.store.fail_mapping=True
        result=await self.adapter.authenticate(cookie)
        self.assertEqual('unavailable',result.status)
        self.assertFalse(result.clear_cookie); self.assertIsNone(result.authorization)
