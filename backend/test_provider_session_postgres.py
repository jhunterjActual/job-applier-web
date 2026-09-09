"""Candidate against actual 0014 and restricted roles; disposable exact DB only."""

import asyncio
from contextlib import asynccontextmanager
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError, DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from account_erasure_repository import (PostgreSQLAccountErasureRepository,
    PostgreSQLAccountErasureWorkerRepository, claim_next_account_erasure)
from account_session_candidate import SessionCodec
from admission_adapter_candidate import AdmissionAdapterCandidate, AsyncAdmissionStore
from signup_prototype.integration_fixture import Fixture, ROOT
from test_provider_session_candidate import (SyntheticHTTP, StreamingMockTransport,
    SETTINGS, GENERATION, SUBJECT)
from workos_transport_candidate import WorkOSTransportCandidate


class PostgreSQLCandidateTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        url=os.environ.get('CAREERTRELLIS_ADMISSION_LIFECYCLE_TEST_URL')
        if not url: raise unittest.SkipTest('Exact disposable PostgreSQL URL required')
        if os.name=='nt':
            previous=asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            cls.addClassCleanup(asyncio.set_event_loop_policy,previous)
        cls.fixture=Fixture(url); cls.addClassCleanup(cls.fixture.close)
        cls.fixture.setup(overlay=False)
        with cls.fixture.admin.begin() as c:
            c.execute(text((ROOT.parent/'deploy/postgres/bootstrap-admission-roles.sql').read_text()))
        cls.fixture.roles+=['careertrellis_admission_owner','careertrellis_admission_broker']
        cls.fixture.schemas+=['careertrellis_admission','careertrellis_lifecycle']
        cfg=Config(); cfg.set_main_option('script_location',str(ROOT/'migrations'))
        with patch.dict(os.environ,{'CAREERTRELLIS_MIGRATION_DATABASE_URL':cls.fixture.engine('careertrellis_migration').url.render_as_string(hide_password=False)}):
            command.upgrade(cfg,'head'); command.upgrade(cfg,'head')
        cls.fixture.grants()
        with cls.fixture.admin.begin() as c:
            c.exec_driver_sql("ALTER ROLE careertrellis_admission_broker LOGIN PASSWORD 'synthetic-integration-only'")
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)

    def query(self,sql,**params):
        with self.fixture.admin.begin() as c:
            result=c.execute(text(sql),params)
            return result.scalar() if result.returns_rows else None

    async def asyncSetUp(self):
        self.query('TRUNCATE careertrellis_admission.http_budgets, careertrellis_admission.http_refresh_claims, careertrellis_admission.http_revocations')
        self.query('TRUNCATE careertrellis.app_users CASCADE')
        self.query('TRUNCATE careertrellis_admission.flows')
        self.query("UPDATE careertrellis_admission.policy SET phase='beta',signup_open=true,adapter_ready=true,minimum_session_version=2,maintenance=false,cap=2,generation=0,realm=:realm",realm=GENERATION)
        self.query("INSERT INTO careertrellis.app_users(workos_user_id,role) VALUES('user_synthetic_owner','owner')")
        def engine(role):
            result=create_async_engine(self.fixture.url.set(username=role,password='synthetic-integration-only'),
                poolclass=NullPool,hide_parameters=True,connect_args={'connect_timeout':2})
            self.addAsyncCleanup(result.dispose)
            return result
        self.broker,self.runtime=engine('careertrellis_admission_broker'),engine('careertrellis_runtime')
        self.store=AsyncAdmissionStore(self.broker,self.runtime)
        self.http=SyntheticHTTP(self.key)
        self.provider=WorkOSTransportCandidate(SETTINGS,transport=StreamingMockTransport(self.http),rotation_seconds=0)
        self.addAsyncCleanup(self.provider.aclose)
        self.codec=SessionCodec(Fernet.generate_key(),Fernet.generate_key(),key_id='synthetic-k1',generation=GENERATION,lifetime_seconds=3600,provider_binding=SETTINGS.binding)
        self.adapter=AdmissionAdapterCandidate(self.store,self.provider,self.codec,owner_subject='user_synthetic_owner',
            flow_key=Fernet.generate_key(),redirect_uri='https://app.example.test/auth/callback')
        self.browser='b'*43

    async def flow(self,purpose='signup'):
        result=await self.adapter.begin(self.browser,purpose)
        self.assertEqual('reserved',result.status)
        return result.cookie,parse_qs(urlsplit(result.authorization_url).query)['state'][0]

    async def activate(self):
        cookie,state=await self.flow()
        result=await self.adapter.complete(cookie,self.browser,state,'synthetic-code')
        self.assertEqual('activated',result.status)
        return result.cookie

    async def test_closed_default_context_and_runtime_only_grants(self):
        self.query("UPDATE careertrellis_admission.policy SET phase='pilot',signup_open=false,adapter_ready=false,minimum_session_version=1,realm='unconfigured'")
        result=await self.adapter.begin(self.browser,'signup')
        self.assertEqual('unavailable',result.status); self.assertEqual(0,self.http.posts)
        self.assertEqual({'adapter_ready','minimum_session_version','maintenance','realm'},set(await self.store.context()))
        self.fixture.grants()
        for role in ('careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker'):
            self.assertFalse(self.query("SELECT has_function_privilege(:role,'careertrellis.admission_session_context()','EXECUTE')",role=role))
        for role in ('careertrellis_worker','careertrellis_erasure_worker'):
            with self.assertRaises(DBAPIError),self.fixture.engine(role).begin() as c:
                c.exec_driver_sql('SELECT careertrellis.admission_session_context()')

    async def test_http_budgets_partition_login_callback_and_throttled_browser(self):
        # These assertions intentionally share one real fixed-minute window.
        # Do not begin the sequence at its boundary and mistake a legitimate
        # reset for a failed throttle. No production clock or limit is mocked.
        seconds = float(self.query('SELECT extract(second FROM clock_timestamp())'))
        if seconds >= 50:
            await asyncio.sleep(60 - seconds + .1)
        for _ in range(5):
            self.assertTrue(await self.store.budget('signup', 'a'*64, 'b'*64))
        for _ in range(65):
            self.assertFalse(await self.store.budget('signup', 'a'*64, 'b'*64))
        self.assertEqual(5, self.query("SELECT uses FROM careertrellis_admission.http_budgets WHERE lane='signup' AND bucket='global'"))
        self.assertTrue(await self.store.budget('signup', 'c'*64, 'd'*64))
        for _ in range(10):
            self.assertTrue(await self.store.budget('callback_signup', 'a'*64, 'b'*64))
        self.assertFalse(await self.store.budget('callback_signup', 'a'*64, 'b'*64))
        self.assertTrue(await self.store.budget('callback_login', 'a'*64, 'b'*64))
        self.assertFalse(await self.store.budget(None, 'a'*64, 'b'*64))
        self.fixture.grants()
        for role in ('careertrellis_runtime', 'careertrellis_worker', 'careertrellis_erasure_worker'):
            self.assertFalse(self.query("SELECT has_function_privilege(:role,'careertrellis_admission.http_budget(text,text,text)','EXECUTE')", role=role))

    async def test_http_shared_refresh_claim_once_extension_revocation_and_cleanup(self):
        import time
        expires = int(time.time()) + 120
        results = await asyncio.gather(*(self.store.session_guard('a'*64, expires, 'claim', 'b'*64) for _ in range(8)))
        self.assertEqual(1, sum(results))
        self.assertFalse(await self.store.session_guard('a'*64, expires + 120, 'claim', 'b'*64))
        self.assertGreaterEqual(int(self.query('SELECT extract(epoch FROM expires) FROM careertrellis_admission.http_refresh_claims')), int(time.time()) + 86390)
        self.assertTrue(await self.store.session_guard('a'*64, expires, 'revoke'))
        self.assertGreaterEqual(int(self.query('SELECT extract(epoch FROM expires) FROM careertrellis_admission.http_revocations')), int(time.time()) + 86390)
        self.assertFalse(await self.store.session_guard('a'*64, expires, 'check'))
        self.assertFalse(await self.store.session_guard('a'*64, expires, 'claim', 'c'*64))
        self.query("UPDATE careertrellis_admission.http_revocations SET expires=clock_timestamp()-interval '1 second'")
        self.assertTrue(await self.store.session_guard('d'*64, expires, 'check'))
        self.assertEqual(0, self.query('SELECT count(*) FROM careertrellis_admission.http_revocations'))

    async def test_coordinated_provider_refresh_exactly_once_across_adapter_instances(self):
        import time
        from dataclasses import replace
        from signup_http import Coordination
        cookie = await self.activate()
        session = self.codec.open(cookie)
        data = self.codec.provider_open(session.provider)
        data['access_token'] = self.http.token(exp=int(time.time())-1)
        import json
        session = replace(session, provider=self.codec._provider_seal.encrypt(json.dumps(data).encode()).decode())
        one = self.codec.pack(session)
        two = self.codec.pack(session)
        other = AdmissionAdapterCandidate(self.store,self.provider,self.codec,owner_subject='user_synthetic_owner',
            flow_key=Fernet.generate_key(),redirect_uri='https://app.example.test/auth/callback')
        self.adapter.coordination = Coordination(self.store, b'synthetic-key-only', SETTINGS.binding)
        other.coordination = Coordination(self.store, b'synthetic-key-only', SETTINGS.binding)
        results = await asyncio.gather(self.adapter.authenticate(one,refresh=True),other.authenticate(two,refresh=True))
        self.assertEqual(['authenticated','restart_required'],sorted(r.status for r in results))
        self.assertEqual(2,self.http.posts)

    async def test_actual_closed_default_runtime_construction_and_cleanup(self):
        from signup_http import create_runtime
        from web_auth import WorkOSAuthConfiguration, AuthenticationConfigurationError
        from unittest.mock import AsyncMock
        settings = WorkOSAuthConfiguration(SETTINGS.client_secret, SETTINGS.client_id,
            Fernet.generate_key().decode(), 'https://app.example.test/auth/callback',
            'https://app.example.test/', 'user_synthetic_owner')
        environment = {
            'CAREERTRELLIS_AUTH_V2_ENABLED': '1',
            'CAREERTRELLIS_AUTH_V2_ISSUER': SETTINGS.issuer,
            'CAREERTRELLIS_AUTH_V2_GENERATION': GENERATION,
            'CAREERTRELLIS_AUTH_V2_ENVELOPE_KEY': Fernet.generate_key().decode(),
            'CAREERTRELLIS_AUTH_V2_FLOW_KEY': Fernet.generate_key().decode(),
            'CAREERTRELLIS_AUTH_V2_KEY_ID': 'synthetic-k1',
            'CAREERTRELLIS_AUTH_V2_SESSION_SECONDS': '3600',
            'CAREERTRELLIS_ADMISSION_DATABASE_URL': self.broker.url.render_as_string(hide_password=False),
            'CAREERTRELLIS_DATABASE_URL': self.runtime.url.render_as_string(hide_password=False),
        }
        with patch.dict(os.environ, environment), patch('workos_transport_candidate.httpx.AsyncClient.send', new_callable=AsyncMock) as send:
            runtime = await create_runtime(settings, ROOT/'static')
            self.assertIsNotNone(runtime.adapter.coordination)
            await runtime.aclose()
            self.query('UPDATE careertrellis_admission.policy SET adapter_ready=false,minimum_session_version=1')
            with self.assertRaises(AuthenticationConfigurationError):
                await create_runtime(settings, ROOT/'static')
            send.assert_not_awaited()

    async def test_actual_commit_profile_session_and_durable_duplicate_claim(self):
        cookie,state=await self.flow()
        results=await asyncio.gather(*[self.adapter.complete(cookie,self.browser,state,'synthetic-code') for _ in range(2)])
        self.assertEqual(1,self.http.posts); self.assertEqual(1,sum(bool(r.cookie) for r in results))
        session=next(r.cookie for r in results if r.cookie)
        authorized=await self.adapter.authenticate(session)
        self.assertEqual('authenticated',authorized.status)
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis.profiles WHERE owner_user_id=:uid',uid=authorized.local_id))
        self.assertEqual(2,self.query('SELECT count(*) FROM careertrellis.app_users'))
        repeated=await self.adapter.complete(cookie,self.browser,state,'synthetic-code')
        self.assertFalse(repeated.cookie); self.assertEqual(1,self.http.posts)

    async def test_real_commit_acknowledgement_loss_keeps_account_no_cookie_fresh_login_recovers(self):
        cookie,state=await self.flow()
        original=AsyncEngine.begin
        broker_calls=0
        @asynccontextmanager
        async def ambiguous(engine):
            nonlocal broker_calls
            async with original(engine) as connection:
                yield connection
            if engine is self.broker:
                broker_calls+=1
                if broker_calls==2: raise SQLAlchemyError('synthetic lost COMMIT acknowledgement')
        with patch.object(AsyncEngine,'begin',ambiguous):
            result=await self.adapter.complete(cookie,self.browser,state,'synthetic-code')
        self.assertEqual('unavailable',result.status); self.assertFalse(result.cookie)
        self.assertEqual(2,self.query('SELECT count(*) FROM careertrellis.app_users'))
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis.profiles'))
        cookie,state=await self.flow('login')
        result=await self.adapter.complete(cookie,self.browser,state,'synthetic-new-code')
        self.assertEqual('login',result.status); self.assertTrue(result.cookie)
        self.assertEqual(2,self.query('SELECT count(*) FROM careertrellis.app_users'))

    async def test_lost_claim_acknowledgement_never_exchanges(self):
        cookie,state=await self.flow(); original=AsyncEngine.begin
        @asynccontextmanager
        async def ambiguous(engine):
            async with original(engine) as connection: yield connection
            if engine is self.broker: raise SQLAlchemyError('synthetic lost claim acknowledgement')
        with patch.object(AsyncEngine,'begin',ambiguous):
            result=await self.adapter.complete(cookie,self.browser,state,'synthetic-code')
        self.assertEqual('unavailable',result.status); self.assertEqual(0,self.http.posts)
        self.assertEqual('incomplete',(await self.adapter.complete(cookie,self.browser,state,'synthetic-code')).status)
        self.assertEqual(0,self.http.posts)

    async def test_real_erasure_recreation_rejects_old_incarnation(self):
        cookie=await self.activate(); original=self.codec.open(cookie).local_id
        runtime=self.fixture.engine('careertrellis_runtime'); worker=self.fixture.engine('careertrellis_erasure_worker')
        PostgreSQLAccountErasureRepository(original,engine=runtime).request_erasure()
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)
        claim=claim_next_account_erasure(engine=worker,worker_id=str(uuid4()),lease_seconds=300)
        self.assertTrue(PostgreSQLAccountErasureWorkerRepository(engine=worker).finalize(claim))
        new_cookie=await self.activate()
        self.assertNotEqual(original,self.codec.open(new_cookie).local_id)
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie,refresh=True)).status)
        self.assertEqual('authenticated',(await self.adapter.authenticate(new_cookie)).status)

    async def test_flow_generation_only_preserves_existing_session(self):
        cookie=await self.activate()
        self.query('UPDATE careertrellis_admission.policy SET generation=generation+1,signup_open=false')
        self.assertEqual('authenticated',(await self.adapter.authenticate(cookie)).status)
        self.assertEqual('closed',(await self.adapter.begin(self.browser,'signup')).status)
        flow,state=await self.flow('login')
        self.assertEqual('login',(await self.adapter.complete(flow,self.browser,state,'synthetic-new-code')).status)

    async def test_provider_generation_change_during_exchange_prevents_activation(self):
        flow,state=await self.flow()
        async def change(request):
            if request.method=='POST': self.query("UPDATE careertrellis_admission.policy SET realm='synthetic-next'")
        self.http.hook=change
        result=await self.adapter.complete(flow,self.browser,state,'synthetic-code')
        self.assertEqual('unavailable',result.status); self.assertFalse(result.cookie)
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis.app_users'))

    async def test_known_oversized_provider_session_does_not_allocate_account(self):
        self.http.refresh_token='r'*4000
        flow,state=await self.flow()
        result=await self.adapter.complete(flow,self.browser,state,'synthetic-code')
        self.assertEqual('service_limited',result.status)
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis.app_users'))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis.profiles'))


if __name__=='__main__': unittest.main()
