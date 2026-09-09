"""Synthetic-only provider/crypto/session tests. No credentials or external I/O."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import io
import json
import logging
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import ssl
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
from workos.session import unseal_data

from account_session_candidate import (BoundSession, MAX_LOCAL_ID, SessionCodec,
    SessionCapacity, StoreUnavailable)
from admission_adapter_candidate import AdmissionAdapterCandidate, AsyncAdmissionStore, build_candidate
from workos_transport_candidate import (API, ProviderSettings, WorkOSTransportCandidate,
    ProviderRejected, ProviderUnavailable, VerifiedTokens)


GENERATION = 'synthetic-provider-v2'
SUBJECT = 'user_synthetic_admitted'
SID = 'session_synthetic_original'
SETTINGS = ProviderSettings('client_synthetic_test', 'synthetic-secret-only',
    API + '/user_management/client_synthetic_test', GENERATION)


def synthetic_cookie_size_receipt():
    """Aggregate lengths only. These are SDK-shaped fixtures, not live measurements."""
    receipts=[]
    for bits in (2048,4096):
        http=SyntheticHTTP(rsa.generate_private_key(public_exponent=65537,key_size=bits))
        codec=SessionCodec(Fernet.generate_key(),Fernet.generate_key(),key_id='synthetic-k1',
            generation=GENERATION,lifetime_seconds=3600,provider_binding=SETTINGS.binding)
        for refresh_size in (128,512,2048):
            now=int(time.time()); access=http.token()
            identity=VerifiedTokens(SUBJECT,SID,GENERATION,access,'r'*refresh_size,now+300,SETTINGS.binding)
            session=BoundSession(MAX_LOCAL_ID,GENERATION,SUBJECT,SID,codec.provider_seal(identity),
                                 now,now+3600,SETTINGS.binding)
            # Mirror exact packing to measure rejected values, without emitting one.
            value=codec._seal.encrypt(json.dumps({'v':2,'kind':'account_session','kid':codec.key_id,
                'data':session.__dict__},separators=(',',':')).encode()).decode()
            header='__Host-careertrellis_session_v2='+value+'; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=3600'
            receipts.append({'rsa_bits':bits,'refresh_bytes':refresh_size,'access_bytes':len(access),
                'cookie_value_bytes':len(value),'set_cookie_bytes':len(header),
                'fits_candidate_budget':len(value)<=3500 and len(header)<=4096})
    return receipts


class StreamingMockTransport(httpx.MockTransport):
    async def handle_async_request(self, request):
        response = await super().handle_async_request(request)
        # HTTPX's json= fixture is pre-consumed; emulate an unread network stream.
        return httpx.Response(response.status_code, headers=response.headers,
                              stream=httpx.ByteStream(response.content))


class SyntheticHTTP:
    def __init__(self, key):
        self.key = key
        self.kid = 'synthetic-key-1'
        self.claims = {}
        self.extra = {}
        self.user = {'id': SUBJECT, 'email_verified': True}
        self.refresh_token = 'synthetic-refresh-only'
        self.status = 200
        self.calls = []
        self.hook = None

    def token(self, **claims):
        now = int(time.time())
        return jwt.encode({'iss': SETTINGS.issuer, 'sub': SUBJECT, 'sid': SID, 'iat': now-5,
            'exp': now+300, **self.claims, **claims}, self.key, algorithm='RS256', headers={'kid': self.kid})

    def jwks(self):
        key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {'keys': [{**key, 'kid': self.kid, 'alg': 'RS256', 'use': 'sig'}]}

    async def __call__(self, request):
        self.calls.append((request.method, str(request.url)))
        if self.hook is not None:
            response = await self.hook(request)
            if response is not None:
                return response
        if request.method == 'GET':
            return httpx.Response(200, json=self.jwks())
        self.body = json.loads(request.content)
        return httpx.Response(self.status, json={'access_token': self.token(),
            'refresh_token': self.refresh_token, 'user': self.user, **self.extra})

    @property
    def posts(self):
        return sum(method == 'POST' for method, _ in self.calls)


class MemoryStore:
    """Injectable contract fake. Separate PostgreSQL suite proves SQL/commit behavior."""
    def __init__(self):
        self.policy = {'adapter_ready': True, 'minimum_session_version': 2,
                       'maintenance': False, 'realm': GENERATION}
        self.accounts = {}
        self.flows = {}
        self.finishes = 0
        self.fail_context = False
        self.fail_mapping = False
        self.before_finish = None
        self.after_finish = None
        self.next_id = 10

    async def context(self):
        if self.fail_context: raise StoreUnavailable()
        return dict(self.policy)

    async def reserve(self, flow):
        self.flows[flow.id] = 'reserved'
        return {'status': 'reserved'}

    async def claim(self, flow, code, attempt):
        state = self.flows[flow.id]
        if state != 'reserved': return {'status': 'incomplete' if state == 'claimed' else state}
        self.flows[flow.id] = 'claimed'
        return {'status': 'exchange'}

    async def result(self, flow, cancel=False):
        if cancel: self.flows[flow.id] = 'cancelled'
        return {'status': self.flows[flow.id]}

    async def finish(self, flow, attempt, identity):
        if self.before_finish: self.before_finish()
        self.finishes += 1
        if flow.purpose == 'login':
            if identity.subject not in self.accounts: return {'status': 'ineligible'}
            status = 'login'
        elif identity.subject in self.accounts:
            return {'status': 'existing'}
        else:
            self.accounts[identity.subject] = {'id': self.next_id, 'role': 'user'}
            self.next_id += 1
            status = 'activated'
        self.flows[flow.id] = status
        if self.after_finish: self.after_finish()
        return {'status': status, 'local_id': self.accounts[identity.subject]['id']}

    async def mapping(self, subject, local_id):
        if self.fail_mapping: raise StoreUnavailable()
        row = self.accounts.get(subject)
        return row if row and row['id'] == local_id else None


class CandidateTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    async def asyncSetUp(self):
        self.http = SyntheticHTTP(self.key)
        self.provider = WorkOSTransportCandidate(SETTINGS, transport=StreamingMockTransport(self.http), rotation_seconds=0)
        self.addAsyncCleanup(self.provider.aclose)
        self.store = MemoryStore()
        self.codec = SessionCodec(Fernet.generate_key(), Fernet.generate_key(),
            key_id='synthetic-k1', generation=GENERATION, lifetime_seconds=3600,provider_binding=SETTINGS.binding)
        self.adapter = AdmissionAdapterCandidate(self.store, self.provider, self.codec,
            owner_subject='user_synthetic_owner', flow_key=Fernet.generate_key(),
            redirect_uri='https://app.example.test/auth/callback')
        self.browser = 'b'*43

    async def flow(self, purpose='signup'):
        outcome = await self.adapter.begin(self.browser, purpose)
        self.assertEqual('reserved', outcome.status)
        return outcome.cookie, parse_qs(urlsplit(outcome.authorization_url).query)['state'][0]

    async def activate(self):
        cookie, state = await self.flow()
        result = await self.adapter.complete(cookie, self.browser, state, 'synthetic-code')
        self.assertEqual('activated', result.status)
        return result.cookie

    async def test_disabled_factory_does_not_read_settings_create_client_or_connect(self):
        factory = Mock(side_effect=AssertionError('must stay closed'))
        self.assertIsNone(build_candidate(factory=factory))
        factory.assert_not_called()
        with self.assertRaises(ValueError): build_candidate(enabled='false', factory=factory)

    async def test_client_scoped_issuer_configuration_and_signed_claims(self):
        self.assertEqual(API + '/user_management/' + SETTINGS.client_id, SETTINGS.issuer)
        invalid = (API, API + '/', API + '/user_management/client_synthetic_other',
            SETTINGS.issuer + '/', SETTINGS.issuer + '?x=1', SETTINGS.issuer + '#x',
            SETTINGS.issuer.replace('https:', 'http:'),
            SETTINGS.issuer.replace('api.workos.com', 'other.example.test'),
            SETTINGS.issuer.replace('api.workos.com', 'api.workos.com:443'),
            SETTINGS.issuer.replace('api.workos.com', 'user@api.workos.com'), None)
        for issuer in invalid:
            with self.subTest(issuer=issuer):
                with self.assertRaises(ValueError): replace(SETTINGS, issuer=issuer)
                if issuer is None:
                    continue  # PyJWT's signing API rejects non-string issuers.
                # A valid signature cannot compensate for an incorrect issuer.
                with self.assertRaises(ProviderRejected):
                    await self.provider.verify(self.http.token(iss=issuer), SUBJECT)
        self.assertEqual(SUBJECT, (await self.provider.verify(self.http.token(), SUBJECT))['sub'])

    async def test_fixed_endpoints_exact_pkce_body_no_metadata_retention(self):
        self.http.extra = {'oauth_tokens': {'secret': 'discard-me'}, 'authentication_method': 'Password'}
        result = await self.provider.exchange('synthetic-code', 'v'*43)
        self.assertEqual({'grant_type': 'authorization_code', 'code': 'synthetic-code', 'code_verifier': 'v'*43,
            'client_id': SETTINGS.client_id, 'client_secret': SETTINGS.client_secret}, self.http.body)
        self.assertEqual([('POST', API+'/user_management/authenticate'),
            ('GET', API+'/sso/jwks/'+SETTINGS.client_id)], self.http.calls)
        self.assertEqual(SUBJECT, result.subject)
        self.assertNotIn('discard-me', repr(result))
        sealed = self.codec.provider_seal(result)
        self.assertEqual({'access_token','refresh_token','user'}, set(unseal_data(sealed,self.codec._provider_key)))
        self.assertEqual({'id': SUBJECT,'email_verified':True}, self.codec.provider_open(sealed)['user'])

    async def test_refresh_body_is_one_request(self):
        await self.provider.refresh('synthetic-refresh-only')
        self.assertEqual({'grant_type':'refresh_token','refresh_token':'synthetic-refresh-only',
            'client_id':SETTINGS.client_id,'client_secret':SETTINGS.client_secret},self.http.body)
        self.assertEqual(1,self.http.posts)

    async def test_http_failures_never_retry_or_follow_redirect(self):
        for status in (301,302,307,308,400,401,403,429,500,502,503):
            with self.subTest(status=status):
                self.http.status=status; self.http.calls=[]
                with self.assertRaises((ProviderRejected,ProviderUnavailable)):
                    await self.provider.exchange('synthetic-code','v'*43)
                self.assertEqual(1,self.http.posts)
                self.assertEqual(1,len(self.http.calls))

    async def test_connection_loss_is_sanitized_no_retry(self):
        async def fail(request):
            raise httpx.ReadError('private token must not escape',request=request)
        self.http.hook=fail
        with self.assertRaises(ProviderUnavailable) as raised:
            await self.provider.refresh('synthetic-refresh-only')
        self.assertNotIn('private',str(raised.exception)); self.assertEqual(1,self.http.posts)

    async def test_total_deadline_cancels_delayed_post(self):
        stopped=asyncio.Event()
        async def slow(request):
            try: await asyncio.sleep(10)
            finally: stopped.set()
        self.http.hook=slow; self.provider.total_seconds=.05
        before=time.monotonic()
        with self.assertRaises(ProviderUnavailable): await self.provider.exchange('synthetic-code','v'*43)
        self.assertLess(time.monotonic()-before,1); self.assertTrue(stopped.is_set())
        self.assertEqual(0,self.provider._inflight); self.assertEqual(1,self.http.posts)

    async def test_cancelled_exchange_cannot_finish_in_background(self):
        entered=asyncio.Event(); stopped=asyncio.Event()
        async def slow(request):
            entered.set()
            try: await asyncio.sleep(10)
            finally: stopped.set()
        self.http.hook=slow
        cookie,state=await self.flow()
        task=asyncio.create_task(self.adapter.complete(cookie,self.browser,state,'synthetic-code'))
        await entered.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(stopped.is_set()); self.assertEqual(0,self.store.finishes)
        self.assertEqual('incomplete',(await self.adapter.complete(cookie,self.browser,state,'synthetic-code')).status)
        self.assertEqual(1,self.http.posts)

    async def test_bounded_concurrency_rejects_excess_without_queue_or_request(self):
        entered=asyncio.Event(); release=asyncio.Event()
        async def slow(request): entered.set(); await release.wait()
        self.http.hook=slow; self.provider.max_inflight=1
        task=asyncio.create_task(self.provider.exchange('synthetic-code','v'*43))
        await entered.wait()
        with self.assertRaises(ProviderUnavailable): await self.provider.exchange('synthetic-code-2','v'*43)
        release.set(); await task; self.assertEqual(1,self.http.posts)

    async def test_strict_identity_and_jwt_contract(self):
        cases=[({'sub':'user_synthetic_other'},{}),({'iss':'https://wrong.example.test/'},{}),
            ({'sid':None},{}),({'sid':[]},{}),({'exp':int(time.time())-1},{}),
            ({'exp':'12345'},{}),({'iat':True},{}),({'iat':int(time.time())+60},{}),
            ({'aud':'unexpected-audience'},{}),({}, {'impersonator':{'id':'synthetic'}})]
        for claims,extra in cases:
            with self.subTest(claims=claims,extra=extra):
                self.http.claims=claims; self.http.extra=extra
                with self.assertRaises(ProviderRejected): await self.provider.exchange('synthetic-code','v'*43)
        self.http.claims={}; self.http.extra={}
        for verified in (False,'true',1,None):
            self.http.user={'id':SUBJECT,'email_verified':verified}
            with self.assertRaises(ProviderRejected): await self.provider.exchange('synthetic-code','v'*43)

    async def test_missing_claims_wrong_algorithm_and_signature(self):
        now=int(time.time()); claims={'iss':SETTINGS.issuer,'sub':SUBJECT,'sid':SID,'iat':now-5,'exp':now+300}
        for missing in claims:
            token=jwt.encode({k:v for k,v in claims.items() if k!=missing},self.key,algorithm='RS256',headers={'kid':self.http.kid})
            with self.assertRaises(ProviderRejected): await self.provider.verify(token,SUBJECT)
        token=jwt.encode(claims,'synthetic-hmac-key-not-a-secret-32-bytes',algorithm='HS256',headers={'kid':self.http.kid})
        with self.assertRaises(ProviderRejected): await self.provider.verify(token,SUBJECT)
        other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        token=jwt.encode(claims,other,algorithm='RS256',headers={'kid':self.http.kid})
        with self.assertRaises(ProviderRejected): await self.provider.verify(token,SUBJECT)

    async def test_jwks_rotation_and_duplicate_kid_fail_closed(self):
        await self.provider.verify(self.http.token(),SUBJECT)
        self.http.key=rsa.generate_private_key(public_exponent=65537,key_size=2048); self.http.kid='synthetic-key-2'
        await self.provider.verify(self.http.token(),SUBJECT)
        self.assertEqual(2,len(self.http.calls))
        async def duplicate(request):
            doc=self.http.jwks(); doc['keys']*=2
            return httpx.Response(200,json=doc)
        self.http.hook=duplicate; self.provider._fetched=None
        with self.assertRaises(ProviderUnavailable): await self.provider.verify(self.http.token(),SUBJECT)

    async def test_jwks_outage_and_kid_flood_are_bounded(self):
        self.provider.rotation_seconds=5
        async def outage(request): return httpx.Response(503,json={})
        self.http.hook=outage
        for _ in range(3):
            with self.assertRaises(ProviderUnavailable): await self.provider.verify(self.http.token(),SUBJECT)
        self.assertEqual(1,len(self.http.calls))

    async def test_malformed_large_or_compressed_json_is_unavailable(self):
        for content,headers in [(b'{"keys":[],"keys":[]}',{}),(b'x'*65537,{}),
                (b'[]',{}),(b'{}',{'content-encoding':'gzip'}),(b'{',{})]:
            async def malformed(request):
                return httpx.Response(200,content=content,headers={'content-type':'application/json',**headers})
            self.http.hook=malformed
            with self.assertRaises(ProviderUnavailable): await self.provider.exchange('synthetic-code','v'*43)

    async def test_browser_state_pkce_cancel_and_no_login_provisioning(self):
        cookie,state=await self.flow()
        for browser,given in [('x'*43,state),(self.browser,'wrong'),(self.browser,state+'x')]:
            self.assertEqual('invalid',(await self.adapter.complete(cookie,browser,given,'synthetic-code')).status)
        self.assertEqual(0,self.http.posts)
        self.assertEqual('cancelled',(await self.adapter.complete(cookie,self.browser,state,cancelled=True)).status)
        self.assertEqual('cancelled',(await self.adapter.complete(cookie,self.browser,state,'synthetic-code')).status)
        cookie,state=await self.flow('login')
        self.assertEqual('ineligible',(await self.adapter.complete(cookie,self.browser,state,'synthetic-code')).status)
        self.assertFalse(self.store.accounts)

    async def test_duplicate_callbacks_exchange_once_and_replay_has_no_cookie(self):
        cookie,state=await self.flow()
        results=await asyncio.gather(*[self.adapter.complete(cookie,self.browser,state,'synthetic-code') for _ in range(3)])
        self.assertEqual(1,sum(bool(result.cookie) for result in results)); self.assertEqual(1,self.http.posts)
        self.assertEqual(1,len(self.store.accounts)); self.assertEqual(1,self.store.finishes)

    async def test_active_session_exact_mapping_and_owner_consistency(self):
        cookie=await self.activate()
        self.assertEqual('authenticated',(await self.adapter.authenticate(cookie)).status)
        self.store.accounts[SUBJECT]['role']='owner'
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)
        self.store.accounts[SUBJECT]={'id':99,'role':'user'}
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie,refresh=True)).status)

    async def test_outages_preserve_cookie_without_authorization(self):
        cookie=await self.activate()
        self.store.fail_mapping=True
        outcome=await self.adapter.authenticate(cookie)
        self.assertEqual('unavailable',outcome.status); self.assertFalse(outcome.clear_cookie)
        self.store.fail_mapping=False; self.provider._fetched=None
        async def outage(request): return httpx.Response(503,json={})
        self.http.hook=outage
        outcome=await self.adapter.authenticate(cookie)
        self.assertEqual('unavailable',outcome.status); self.assertFalse(outcome.clear_cookie)

    async def test_provider_generation_and_mode_change_block_old_session(self):
        cookie=await self.activate()
        for update in ({'realm':'synthetic-next'}, {'minimum_session_version':1}, {'maintenance':True}, {'adapter_ready':False}):
            old=dict(self.store.policy); self.store.policy.update(update)
            self.assertEqual('unavailable',(await self.adapter.authenticate(cookie)).status)
            self.store.policy=old

    async def expired_cookie(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        data=self.codec.provider_open(session.provider)
        expired=self.http.token(iat=int(time.time())-30,exp=int(time.time())-1)
        from workos.session import seal_session_from_auth_response
        provider=seal_session_from_auth_response(access_token=expired,refresh_token=data['refresh_token'],
            user=data['user'],cookie_password=self.codec._provider_key)
        return self.codec.pack(replace(session,provider=provider))

    async def test_explicit_refresh_preserves_absolute_lifetime_and_binding(self):
        cookie=await self.expired_cookie(); before=self.codec.open(cookie)
        self.assertEqual('refresh_required',(await self.adapter.authenticate(cookie)).status)
        outcome=await self.adapter.authenticate(cookie,refresh=True)
        self.assertEqual('authenticated',outcome.status)
        after=self.codec.open(outcome.cookie)
        self.assertEqual((before.local_id,before.generation,before.issued,before.expires),
                         (after.local_id,after.generation,after.issued,after.expires))
        self.assertEqual(2,self.http.posts)

    async def test_refresh_subject_or_session_change_is_rejected(self):
        cookie=await self.expired_cookie()
        self.http.claims={'sid':'session_synthetic_changed'}
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie,refresh=True)).status)
        self.http.claims={'sub':'user_synthetic_changed'}; self.http.user={'id':'user_synthetic_changed','email_verified':True}
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie,refresh=True)).status)

    async def test_refresh_rechecks_deleted_account_and_policy_after_provider_io(self):
        cookie=await self.expired_cookie()
        async def disable(request):
            if request.method=='POST': self.store.accounts.clear()
        self.http.hook=disable
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie,refresh=True)).status)

    async def test_known_oversize_stops_before_activation_and_post_commit_failure_keeps_account(self):
        self.http.refresh_token='r'*4000
        cookie,state=await self.flow()
        outcome=await self.adapter.complete(cookie,self.browser,state,'synthetic-code')
        self.assertEqual('service_limited',outcome.status); self.assertFalse(outcome.cookie)
        self.assertEqual(0,self.store.finishes); self.assertFalse(self.store.accounts)
        self.http.refresh_token='synthetic-refresh-only'
        cookie,state=await self.flow()
        real_issue=self.codec.issue
        def issue(identity,local_id):
            if local_id!=MAX_LOCAL_ID: raise SessionCapacity()
            return real_issue(identity,local_id)
        with patch.object(self.codec,'issue',side_effect=issue):
            result=await self.adapter.complete(cookie,self.browser,state,'synthetic-code')
        self.assertEqual('service_limited',result.status); self.assertEqual(1,len(self.store.accounts)); self.assertFalse(result.cookie)

    async def test_refresh_oversize_returns_service_limit_not_bare_cookie(self):
        cookie=await self.expired_cookie(); self.http.refresh_token='r'*4000
        result=await self.adapter.authenticate(cookie,refresh=True)
        self.assertEqual('service_limited',result.status); self.assertFalse(result.cookie); self.assertFalse(result.clear_cookie)

    async def test_commit_or_mapping_outage_cannot_emit_cookie_or_delete_account(self):
        def unavailable(): raise StoreUnavailable()
        self.store.after_finish=unavailable
        cookie,state=await self.flow()
        result=await self.adapter.complete(cookie,self.browser,state,'synthetic-code')
        self.assertEqual('unavailable',result.status); self.assertFalse(result.cookie)
        self.assertEqual(1,len(self.store.accounts))
        self.store.after_finish=None
        cookie,state=await self.flow('login')
        result=await self.adapter.complete(cookie,self.browser,state,'new-synthetic-code')
        self.assertEqual('login',result.status); self.assertEqual(1,len(self.store.accounts))

    async def test_legacy_tampering_expiry_and_future_versions_never_authorize(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        for value in (session.provider,'garbage',cookie[:-5]+'abcde',self.codec.pack(replace(session,expires=int(time.time())-1))):
            result=await self.adapter.authenticate(value)
            self.assertEqual('unauthenticated',result.status); self.assertFalse(result.cookie)
        for data in ({'v':3,'kind':'account_session','kid':self.codec.key_id,'data':session.__dict__},
                     {'v':2,'kind':'account_session','kid':'other','data':session.__dict__},
                     {'v':2,'kind':'account_session','kid':self.codec.key_id,'data':{**session.__dict__,'local_id':True}}):
            token=self.codec._seal.encrypt(json.dumps(data).encode()).decode()
            self.assertEqual('unauthenticated',(await self.adapter.authenticate(token)).status)

    async def test_verified_logout_without_mapping_and_explicit_legacy_logout_only(self):
        cookie=await self.activate(); legacy=self.codec.open(cookie).provider; self.store.accounts.clear()
        result=await self.adapter.logout_evidence(cookie)
        self.assertEqual('logout_ready',result.status); self.assertIn('/user_management/sessions/logout?',result.logout_url)
        self.assertEqual('logout_ready',(await self.adapter.logout_evidence(legacy,legacy=True)).status)
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(legacy)).status)

    async def test_no_credentials_in_library_diagnostics_or_repr(self):
        output=io.StringIO(); handler=logging.StreamHandler(output)
        logger=logging.getLogger('httpx'); previous=logger.level; logger.setLevel(logging.INFO); logger.addHandler(handler)
        try: result=await self.provider.exchange('synthetic-code','v'*43)
        finally: logger.removeHandler(handler); logger.setLevel(previous)
        text=output.getvalue()+repr(result)+repr(SETTINGS)
        for secret in ('synthetic-code','synthetic-secret-only','synthetic-refresh-only',result.access_token):
            self.assertNotIn(secret,text)

    async def test_transient_configuration_and_terminal_refresh_errors_are_distinct(self):
        cookie=await self.expired_cookie()
        for status,body,expected in ((400,{'error':'invalid_grant'},'unauthenticated'),
                (400,{'error':'invalid_client'},'unavailable'),(400,{'code':'unknown'},'unavailable'),
                (401,{'error':'invalid_client'},'unavailable'),(403,{},'unavailable'),(503,{},'unavailable')):
            async def error(request): return httpx.Response(status,json=body)
            self.http.hook=error
            result=await self.adapter.authenticate(cookie,refresh=True)
            self.assertEqual(expected,result.status)
            self.assertEqual(expected=='unauthenticated',result.clear_cookie)

    async def test_key_argument_encoding_cannot_bypass_distinct_key_requirement(self):
        key=Fernet.generate_key()
        with self.assertRaises(ValueError):
            SessionCodec(key,key.decode(),key_id='synthetic-k1',generation=GENERATION,lifetime_seconds=3600,provider_binding=SETTINGS.binding)

    async def test_authorization_rechecks_outer_and_token_expiry_after_mapping_wait(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        original=self.store.mapping
        async def expire(subject,local_id):
            row=await original(subject,local_id)
            # Advance only after crypto checks; no actual minute-long wait needed.
            clock.return_value=session.expires+1
            return row
        with patch('time.time',return_value=time.time()) as clock,patch.object(self.store,'mapping',side_effect=expire):
            self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)
        token_exp=self.codec.provider_open(session.provider)['access_token']
        expiry=jwt.decode(token_exp,options={'verify_signature':False})['exp']
        async def expire_token(subject,local_id):
            row=await original(subject,local_id); clock.return_value=expiry+1; return row
        with patch('time.time',return_value=time.time()) as clock,patch.object(self.store,'mapping',side_effect=expire_token):
            self.assertEqual('refresh_required',(await self.adapter.authenticate(cookie)).status)

    async def test_audience_is_optional_unless_explicitly_configured(self):
        self.provider.settings=replace(SETTINGS,audience='synthetic-audience')
        with self.assertRaises(ProviderRejected): await self.provider.verify(self.http.token(),SUBJECT)
        await self.provider.verify(self.http.token(aud='synthetic-audience'),SUBJECT)
        with self.assertRaises(ProviderRejected): await self.provider.verify(self.http.token(aud='wrong'),SUBJECT)

    async def test_hostile_headers_and_duplicate_claim_members(self):
        claims={'iss':SETTINGS.issuer,'sub':SUBJECT,'sid':SID,'iat':int(time.time())-5,'exp':int(time.time())+300}
        for header in ({'jku':'https://attacker.example.test/'},{'jwk':{}},{'crit':[]},{'x5u':'https://attacker.example.test/'}):
            token=jwt.encode(claims,self.key,algorithm='RS256',headers={'kid':self.http.kid,**header})
            with self.assertRaises(ProviderRejected): await self.provider.verify(token,SUBJECT)
        duplicate=json.dumps(claims)[:-1]+',"sub":"user_synthetic_other"}'
        token=jwt.api_jws.encode(duplicate.encode(),self.key,algorithm='RS256',headers={'kid':self.http.kid})
        with self.assertRaises(ProviderRejected): await self.provider.verify(token,SUBJECT)
        self.assertEqual(0,len(self.http.calls))

    async def test_jwks_private_material_and_key_size_are_rejected(self):
        for size in (1024,2048):
            key=rsa.generate_private_key(public_exponent=65537,key_size=size)
            entry=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key if size==2048 else key.public_key()))
            async def bad_key(request): return httpx.Response(200,json={'keys':[{**entry,'kid':self.http.kid}]})
            self.http.hook=bad_key
            with self.assertRaises(ProviderUnavailable): await self.provider.verify(self.http.token(),SUBJECT)

    async def test_jwks_timeout_cancels_request_and_leaves_activation_unfinished(self):
        cancelled=asyncio.Event()
        async def slow(request):
            if request.method=='GET':
                try: await asyncio.sleep(10)
                finally: cancelled.set()
        self.http.hook=slow; self.provider.total_seconds=.05
        flow,state=await self.flow()
        result=await self.adapter.complete(flow,self.browser,state,'synthetic-code')
        self.assertEqual('incomplete',result.status); self.assertTrue(cancelled.is_set())
        self.assertEqual(0,self.store.finishes); self.assertEqual(1,self.http.posts)

    async def test_provider_configuration_change_cannot_reuse_same_generation(self):
        cookie=await self.activate()
        self.provider.settings=replace(SETTINGS,client_id='client_synthetic_changed',
            issuer=API+'/user_management/client_synthetic_changed')
        self.assertEqual('unavailable',(await self.adapter.authenticate(cookie)).status)
        self.assertNotEqual(SETTINGS.binding,self.provider.settings.binding)

    async def test_refreshed_token_expiring_during_mapping_is_not_authorized(self):
        cookie=await self.expired_cookie(); original=self.store.mapping; count=0
        expiry=int(time.time())+10; self.http.claims={'exp':expiry}
        async def delay(subject,local_id):
            nonlocal count
            row=await original(subject,local_id); count+=1
            if count==2: clock.return_value=expiry+1
            return row
        with patch('time.time',return_value=time.time()) as clock,patch.object(self.store,'mapping',side_effect=delay):
            result=await self.adapter.authenticate(cookie,refresh=True)
            self.assertEqual('unavailable',result.status); self.assertFalse(result.cookie)

    async def test_all_httpx_timeout_classes_are_sanitized_without_retry(self):
        for exception in (httpx.ConnectTimeout,httpx.ReadTimeout,httpx.WriteTimeout,httpx.PoolTimeout):
            async def timeout(request): raise exception('synthetic-private-detail',request=request)
            self.http.hook=timeout; self.http.calls=[]
            with self.assertRaises(ProviderUnavailable) as raised: await self.provider.exchange('synthetic-code','v'*43)
            self.assertEqual(1,self.http.posts); self.assertNotIn('private',str(raised.exception))

    async def test_cookie_measurements_enforce_value_and_complete_header_budget(self):
        result=await self.provider.exchange('synthetic-code','v'*43)
        cookie=self.codec.issue(result,MAX_LOCAL_ID)
        self.assertLessEqual(len(cookie),3500)
        self.assertLessEqual(len('__Host-careertrellis_session_v2='+cookie+'; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=3600'),4096)
        for length in (2048,4096,8192):
            with self.assertRaises(SessionCapacity): self.codec.issue(replace(result,refresh_token='r'*length),MAX_LOCAL_ID)

    async def test_native_loopback_tls_stream_total_deadline_closes_socket(self):
        # Real HTTPX/httpcore TLS and socket cancellation, not a sleeping thread.
        with tempfile.TemporaryDirectory(prefix='ct-provider-synthetic-tls-') as temporary:
            path=Path(temporary); now=datetime.now(timezone.utc)
            name=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,'synthetic-loopback')])
            cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(self.key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
                .not_valid_after(now+timedelta(hours=1)).add_extension(x509.SubjectAlternativeName([
                    x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).sign(self.key,hashes.SHA256()))
            cert_path=path/'certificate.pem'; key_path=path/'key.pem'
            cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            server_ssl=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); server_ssl.load_cert_chain(cert_path,key_path)
            client_ssl=ssl.create_default_context(cafile=str(cert_path))
            disconnected=asyncio.Event(); headers_sent=asyncio.Event(); calls=[]
            async def serve(reader,writer):
                try:
                    header=await reader.readuntil(b'\r\n\r\n'); calls.append(1)
                    length=next(int(line.split(b':')[1]) for line in header.split(b'\r\n') if line.lower().startswith(b'content-length:'))
                    await reader.readexactly(length)
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 10000\r\n\r\n{')
                    await writer.drain()
                    headers_sent.set()
                    await reader.read(1)
                finally:
                    writer.close()
                    try: await asyncio.wait_for(writer.wait_closed(),2)
                    except (OSError,TimeoutError): writer.transport.abort()
                    disconnected.set()
            server=await asyncio.start_server(serve,'127.0.0.1',0,ssl=server_ssl,ssl_shutdown_timeout=1)
            port=server.sockets[0].getsockname()[1]
            class LoopbackTLS(httpx.AsyncBaseTransport):
                def __init__(inner): inner.transport=httpx.AsyncHTTPTransport(verify=client_ssl,retries=0,trust_env=False)
                async def handle_async_request(inner,request):
                    local=httpx.Request(request.method,f'https://127.0.0.1:{port}/synthetic',
                        content=request.content,extensions=request.extensions)
                    return await inner.transport.handle_async_request(local)
                async def aclose(inner): await inner.transport.aclose()
            provider=WorkOSTransportCandidate(SETTINGS,transport=LoopbackTLS(),total_seconds=2)
            try:
                task=asyncio.create_task(provider.exchange('synthetic-code','v'*43))
                await asyncio.wait_for(headers_sent.wait(),3)
                with self.assertRaises(ProviderUnavailable): await task
                await asyncio.wait_for(disconnected.wait(),3)
                self.assertEqual(1,len(calls)); self.assertEqual(0,provider._inflight)
            finally:
                await provider.aclose(); server.close(); await server.wait_closed()


if __name__=='__main__': unittest.main()
