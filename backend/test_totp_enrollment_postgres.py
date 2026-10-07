"""Actual restricted PostgreSQL18 store; disposable fixture and synthetic provider."""
import asyncio
from dataclasses import replace, asdict
import json
from uuid import uuid4
import time
import unittest

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import test_provider_session_postgres as provider_tests
import test_identity_session_postgres as identity_tests
from test_totp_enrollment import ProviderFixture, FACTOR, CHALLENGE, SECRET
from signup_http import Coordination
from totp_enrollment import EnrollmentActor, EnrollmentCoordinator, EnrollmentSetup, EnrollmentRejected, EnrollmentRecord
from totp_postgres import PostgreSQLEnrollmentStore, scrub_expired


class TOTPPostgresTests(unittest.IsolatedAsyncioTestCase):
    schema_revision = '20260909_0015'
    query = provider_tests.PostgreSQLCandidateTests.query
    flow = provider_tests.PostgreSQLCandidateTests.flow
    activate = provider_tests.PostgreSQLCandidateTests.activate
    operator = identity_tests.IdentitySessionPostgresTests.operator

    @classmethod
    def setUpClass(cls):
        provider_tests.PostgreSQLCandidateTests.setUpClass.__func__(cls)
        cls.fixture.upgrade_identity_sessions('20260915_0016')

    async def asyncSetUp(self):
        await identity_tests.IdentitySessionPostgresTests.asyncSetUp(self)
        cookie = await self.activate()
        session = self.codec.open(cookie)
        self.actor = EnrollmentActor(self.adapter.authorization(session), session.session_id,
                                     'b' * 64, int(time.time()))
        self.coordination = Coordination(self.store, b'synthetic-coordination-key', session.provider_binding)
        self.enroll_store = PostgreSQLEnrollmentStore(self.runtime, self.coordination)
        self.factor_provider = ProviderFixture()
        async def enroll(subject):
            self.factor_provider.creates += 1
            return EnrollmentSetup(FACTOR, CHALLENGE, SECRET, int(time.time()) + 240)
        self.factor_provider.enroll = enroll
        self.enrollment = EnrollmentCoordinator(self.enroll_store, self.factor_provider, Fernet.generate_key())
        self.migration = create_async_engine(self.fixture.engine('careertrellis_migration').url,
                                             poolclass=NullPool, hide_parameters=True)
        self.addAsyncCleanup(self.migration.dispose)

    async def test_success_and_grant_replay_preserve_private_boundary(self):
        operation = await self.enrollment.start(self.actor)
        self.assertEqual(SECRET, await self.enrollment.setup_secret(self.actor, operation))
        self.assertEqual('complete', await self.enrollment.verify(self.actor, operation, '123456'))
        self.assertIsNone(self.query("SELECT record->>'sealed_secret' FROM careertrellis_admission.totp_attempts"))
        self.fixture.grants()
        for role in ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker'):
            self.assertFalse(self.query("SELECT has_table_privilege(:r,'careertrellis_admission.totp_attempts','SELECT')", r=role))
            self.assertEqual(role=='careertrellis_runtime',self.query("SELECT has_function_privilege(:r,'careertrellis.totp_request(jsonb,text,text,uuid,jsonb,jsonb)','EXECUTE')",r=role))
        with self.assertRaises(DBAPIError):
            with self.fixture.engine('careertrellis_worker').begin() as c:
                c.execute(text("SELECT careertrellis.totp_request('{}','x','get')"))

    async def test_two_database_connections_claim_once(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.factor_provider.enroll
        async def paused(subject):
            entered.set()
            await release.wait()
            return await original(subject)
        self.factor_provider.enroll = paused
        task = asyncio.create_task(self.enrollment.start(self.actor))
        await asyncio.wait_for(entered.wait(), 5)
        try:
            with self.assertRaises(EnrollmentRejected):
                await self.enrollment.start(self.actor)
        finally:
            release.set()
        await task
        self.assertEqual(1, self.factor_provider.creates)

    async def test_fresh_caller_can_close_after_original_session_revoked(self):
        operation = await self.enrollment.start(self.actor)
        oldkey = self.coordination.digest('session', self.actor.authorization.local_id,
            self.actor.authorization.subject, self.actor.session_id)
        self.query("INSERT INTO careertrellis_admission.http_revocations VALUES(:k,clock_timestamp()+interval '1 hour')", k=oldkey)
        self.assertFalse(await self.enroll_store.eligible(self.actor))
        fresh = replace(self.actor, session_id='session_synthetic_fresh')
        self.assertEqual('closed_outcome_unresolved', await self.enrollment.close(fresh, operation))
        self.assertIsNone(self.query("SELECT record->>'sealed_secret' FROM careertrellis_admission.totp_attempts"))

    async def test_scrub_retains_slot_and_defeats_stale_writer(self):
        operation = await self.enrollment.start(self.actor)
        old = await self.enroll_store.get(self.actor, operation)
        # Artificially expire synthetic state without waiting in real time.
        self.query("UPDATE careertrellis_admission.totp_attempts SET record=jsonb_set(record,'{deadline}',to_jsonb(extract(epoch FROM clock_timestamp())::bigint-1))")
        self.assertEqual({'expired_secrets_scrubbed': 1}, await scrub_expired(self.migration))
        self.assertEqual({'expired_secrets_scrubbed': 0}, await scrub_expired(self.migration))
        self.assertFalse(await self.enroll_store.cas(old, replace(old, state='verifying',attempts=1),actor=self.actor))
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis_admission.totp_attempts'))
        with self.assertRaises(EnrollmentRejected):
            await self.enrollment.start(self.actor)
        self.factor_provider.verified = True
        self.assertEqual('complete',await self.enrollment.reconcile(self.actor,operation))

    async def test_policy_identity_and_disabled_denial(self):
        self.assertTrue(await self.enroll_store.eligible(self.actor))
        for update, undo in (("maintenance=true","maintenance=false"),
                             ("minimum_session_version=1","minimum_session_version=2"),
                             ("adapter_ready=false","adapter_ready=true")):
            self.query('UPDATE careertrellis_admission.policy SET '+update)
            self.assertFalse(await self.enroll_store.eligible(self.actor))
            self.query('UPDATE careertrellis_admission.policy SET '+undo)
        for auth in (replace(self.actor.authorization, epoch=2),
                     replace(self.actor.authorization, provider_binding='f'*64),
                     replace(self.actor.authorization, incarnation='00000000-0000-4000-8000-000000000001')):
            self.assertFalse(await self.enroll_store.eligible(replace(self.actor,authorization=auth)))
        self.query('UPDATE careertrellis.app_users SET disabled_at=clock_timestamp() WHERE id=:id',id=self.actor.authorization.local_id)
        self.assertFalse(await self.enroll_store.eligible(self.actor))

    async def test_budget_is_durable_and_freshness_checked(self):
        self.assertFalse(await self.enroll_store.eligible(replace(self.actor,authenticated_at=int(time.time())-301)))
        for _ in range(60):
            self.assertTrue(await self.enroll_store.eligible(self.actor))
        self.assertFalse(await self.enroll_store.eligible(self.actor))
        self.query("UPDATE careertrellis_admission.totp_budgets SET window_start=clock_timestamp()-interval '61 seconds'")
        self.assertTrue(await self.enroll_store.eligible(self.actor))

    async def test_account_erasure_cascades_private_state(self):
        await self.enrollment.start(self.actor)
        self.query('DELETE FROM careertrellis.app_users WHERE id=:id',id=self.actor.authorization.local_id)
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.totp_attempts'))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.totp_budgets'))

    async def test_malformed_record_cannot_claim_permanent_slot(self):
        row = EnrollmentRecord(str(uuid4()),self.actor,'creating',int(time.time())+240)
        key = self.coordination.digest('session',self.actor.authorization.local_id,
            self.actor.authorization.subject,self.actor.session_id)
        for field in ('deadline','attempts'):
            for variant in ('missing', None, '123', True, 1.5):
                value = asdict(row)
                if variant=='missing': value.pop(field)
                else: value[field]=variant
                async with self.runtime.begin() as c:
                    result=await c.scalar(text('SELECT careertrellis.totp_request(CAST(:actor AS jsonb),:key,\'reserve\','
                        'CAST(:op AS uuid),NULL,CAST(:replacement AS jsonb))'),
                        {'actor':json.dumps(asdict(self.actor)),'key':key,'op':row.operation,'replacement':json.dumps(value)})
                self.assertFalse(result)
                self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.totp_attempts'))


if __name__=='__main__':
    unittest.main()
