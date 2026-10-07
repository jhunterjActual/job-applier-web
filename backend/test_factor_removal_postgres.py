"""Restricted PostgreSQL removal protocol; dedicated fixture, synthetic provider."""
import asyncio
from dataclasses import asdict, replace
import json
import time
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import test_totp_enrollment_postgres as enrollment_tests
from factor_removal import (FactorRemovalCoordinator, FactorObservation, RemovalChallenge,
    RemovalVerification, RemovalDeletion, RemovalRecord, RemovalRejected, RemovalUncertain)
from factor_removal_postgres import PostgreSQLRemovalStore


FACTOR = 'auth_factor_removal_native'
CHALLENGE = 'auth_challenge_removal_native'


class RemovalPostgresTests(enrollment_tests.TOTPPostgresTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fixture.upgrade_identity_sessions('20260928_0018')

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.removal_store = PostgreSQLRemovalStore(self.runtime, self.coordination)
        self.operation = str(uuid4())
        self.effects = []
        self.deleted = False
        self.valid = True
        self.pause = None
        self.fail_delete = False
        self.removal = FactorRemovalCoordinator(self.removal_store, self)

    async def observe(self, subject, factor):
        return FactorObservation(self.actor.authorization.provider_binding, subject, factor,
            None if self.deleted else 'totp', 'absent' if self.deleted else 'present', True)

    def claimed(self, stage):
        self.assertEqual(stage, self.query("SELECT record->>'state' FROM careertrellis_admission.factor_removal_attempts"))
        self.assertEqual(stage, self.query('SELECT stage FROM careertrellis_admission.account_security_slots'))

    async def challenge(self, subject, factor):
        self.claimed('challenge_claimed')
        self.effects.append('challenge')
        if self.pause:
            entered, release = self.pause
            entered.set()
            await release.wait()
        return RemovalChallenge(self.actor.authorization.provider_binding, subject, factor,
            CHALLENGE, int(time.time()) + 240)

    async def verify(self, subject, factor, challenge, code):
        self.claimed('verification_claimed')
        self.effects.append('verify')
        return RemovalVerification(self.actor.authorization.provider_binding, subject, factor, challenge, self.valid)

    async def delete(self, subject, factor, operation):
        self.claimed('delete_claimed')
        self.effects.append('delete')
        self.deleted = True
        if self.fail_delete:
            raise OSError('Synthetic response loss')
        return RemovalDeletion(self.actor.authorization.provider_binding, subject, factor, operation, 'deleted')

    async def start_removal(self):
        return await self.removal.start(self.actor, self.operation, FACTOR)

    async def raw(self, action, *, old=None, new=None, actor=None):
        actor = actor or self.actor
        key = self.coordination.digest('session',actor.authorization.local_id,
            actor.authorization.subject,actor.session_id)
        async with self.runtime.begin() as c:
            return await c.scalar(text('SELECT careertrellis.factor_removal_request('
                'CAST(:actor AS jsonb),:key,:action,CAST(:op AS uuid),'
                'CAST(:old AS jsonb),CAST(:new AS jsonb))'), dict(actor=json.dumps(asdict(actor)),
                key=key,action=action,op=self.operation,old=json.dumps(old) if old else None,
                new=json.dumps(new) if new else None))

    async def test_removal_success_retains_slot_and_blocks_enrollment(self):
        await self.start_removal()
        self.assertEqual('complete', await self.removal.verify(self.actor, self.operation, '123456'))
        self.claimed('complete')
        self.assertEqual(['challenge','verify','delete'], self.effects)
        with self.assertRaises(enrollment_tests.EnrollmentRejected):
            await self.enrollment.start(self.actor)
        self.assertEqual(0, self.factor_provider.creates)
        with self.assertRaises(RemovalRejected): await self.start_removal()

    async def test_removal_grants_survive_replay_and_private_state_is_denied(self):
        self.fixture.grants()
        public = 'careertrellis.factor_removal_request(jsonb,text,text,uuid,jsonb,jsonb)'
        private = 'careertrellis_admission.factor_removal_request(jsonb,text,text,uuid,jsonb,jsonb)'
        for role in ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker',
                     'careertrellis_admission_broker','careertrellis_migration'):
            self.assertEqual(role == 'careertrellis_runtime', self.query(
                "SELECT has_function_privilege(:role,:fn,'EXECUTE')", role=role, fn=public))
            self.assertFalse(self.query("SELECT has_function_privilege(:role,:fn,'EXECUTE')", role=role, fn=private))
            for table in ('factor_removal_attempts','factor_removal_budgets','account_security_slots'):
                self.assertFalse(self.query("SELECT has_table_privilege(:role,:tbl,'SELECT,INSERT,UPDATE,DELETE')",
                    role=role, tbl='careertrellis_admission.'+table))
        with self.assertRaises(DBAPIError):
            with self.fixture.engine('careertrellis_worker').begin() as c:
                c.execute(text("SELECT careertrellis.factor_removal_request('{}','x','eligible')"))
        self.assertTrue(await self.removal_store.eligible(self.actor))

    async def test_removal_never_transfers_any_enrollment_state(self):
        operation = await self.enrollment.start(self.actor)
        for state in ('creating','pending','verifying','closed','complete'):
            # Administrative fixture construction only; no production transition.
            self.query("UPDATE careertrellis_admission.totp_attempts SET record=jsonb_set(record,'{state}',to_jsonb(CAST(:state AS text)))", state=state)
            self.query('UPDATE careertrellis_admission.account_security_slots SET stage=:state', state=state)
            self.assertFalse(await self.removal_store.eligible(self.actor))
            with self.assertRaises(RemovalRejected): await self.start_removal()
        self.assertEqual([], self.effects)
        self.assertEqual(operation, str(self.query('SELECT operation FROM careertrellis_admission.account_security_slots')))

    async def test_removal_missing_or_corrupt_slot_fails_closed(self):
        await self.start_removal()
        self.query("UPDATE careertrellis_admission.account_security_slots SET stage='wrong'")
        with self.assertRaises(RemovalUncertain): await self.removal_store.get(self.actor, self.operation)
        self.query('DELETE FROM careertrellis_admission.account_security_slots')
        with self.assertRaises(RemovalUncertain): await self.removal_store.eligible(self.actor)
        with self.assertRaises(RemovalUncertain): await self.start_removal()
        self.assertEqual(['challenge'], self.effects)

    async def test_removal_absence_after_lost_response_cannot_release_or_replay(self):
        await self.start_removal()
        self.fail_delete = True
        with self.assertRaises(RemovalUncertain):
            await self.removal.verify(self.actor, self.operation, '123456')
        coordinator = FactorRemovalCoordinator(PostgreSQLRemovalStore(self.runtime, self.coordination), self)
        self.assertEqual('unresolved', await coordinator.reconcile(self.actor, self.operation))
        with self.assertRaises(RemovalRejected):
            await coordinator.verify(self.actor, self.operation, '123456')
        with self.assertRaises(RemovalRejected):
            await coordinator.start(self.actor, str(uuid4()), FACTOR)
        self.assertEqual(['challenge','verify','delete'], self.effects)
        self.claimed('delete_claimed')

    async def test_removal_two_connections_claim_once_and_exclude_enrollment(self):
        entered, release = asyncio.Event(), asyncio.Event()
        self.pause = entered, release
        task = asyncio.create_task(self.start_removal())
        await asyncio.wait_for(entered.wait(), 5)
        try:
            with self.assertRaises(RemovalRejected): await self.start_removal()
            with self.assertRaises(enrollment_tests.EnrollmentRejected): await self.enrollment.start(self.actor)
        finally:
            release.set()
            await task
        self.assertEqual(['challenge'], self.effects)
        self.assertEqual(0, self.factor_provider.creates)

    async def test_removal_reserve_and_slot_rollback_together(self):
        row = RemovalRecord(self.operation, self.actor, FACTOR, 'challenge_claimed', int(time.time())+240)
        key = self.coordination.digest('session',self.actor.authorization.local_id,
            self.actor.authorization.subject,self.actor.session_id)
        async with self.runtime.connect() as c:
            tx = await c.begin()
            self.assertTrue(await c.scalar(text("SELECT careertrellis.factor_removal_request("
                "CAST(:actor AS jsonb),:key,'reserve',CAST(:op AS uuid),NULL,CAST(:row AS jsonb))"),
                dict(actor=json.dumps(asdict(self.actor)),key=key,op=row.operation,row=json.dumps(asdict(row)))))
            await tx.rollback()
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.account_security_slots'))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.factor_removal_attempts'))
        await self.start_removal()

    async def test_removal_illegal_cas_cannot_change_factor_budget_or_proof(self):
        await self.start_removal()
        row = await self.removal_store.get(self.actor,self.operation)
        for new in (replace(row,factor='auth_factor_wrong_native'),
                    replace(row,attempts=3), replace(row,deadline=row.deadline+100),
                    replace(row,state='delete_claimed',attempts=1,verified_at=int(time.time()))):
            self.assertFalse(await self.raw('cas',old=asdict(row),new=asdict(new)))
        self.assertEqual(row, await self.removal_store.get(self.actor,self.operation))

    async def test_removal_signed_freshness_revocation_and_epoch_denial(self):
        for actor in (replace(self.actor,authenticated_at=int(time.time())-301),
                      replace(self.actor,authenticated_at=int(time.time())+10),
                      replace(self.actor,authorization=replace(self.actor.authorization,epoch=999))):
            self.assertFalse(await self.removal_store.eligible(actor))
        key=self.coordination.digest('session',self.actor.authorization.local_id,
            self.actor.authorization.subject,self.actor.session_id)
        self.query("INSERT INTO careertrellis_admission.http_revocations VALUES(:key,clock_timestamp()+interval '1 hour')",key=key)
        self.assertFalse(await self.removal_store.eligible(self.actor))

    async def test_removal_invalid_code_budget_is_durable(self):
        await self.start_removal()
        self.valid=False
        for _ in range(5):
            self.assertEqual('code_not_accepted', await self.removal.verify(self.actor,self.operation,'000000'))
        with self.assertRaises(RemovalRejected): await self.removal.verify(self.actor,self.operation,'123456')
        self.assertEqual(5,self.effects.count('verify'))
        self.assertNotIn('delete',self.effects)

    async def test_removal_erasure_cascades_only_account_state(self):
        await self.start_removal()
        self.query('DELETE FROM careertrellis.app_users WHERE id=:uid',uid=self.actor.authorization.local_id)
        for table in ('factor_removal_attempts','factor_removal_budgets','account_security_slots'):
            self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.'+table))

    async def test_removal_raw_malformed_records_do_not_reserve(self):
        row=asdict(RemovalRecord(self.operation,self.actor,FACTOR,'challenge_claimed',int(time.time())+240))
        for field in ('deadline','attempts','delete_acknowledged','factor','verified_at'):
            for value in ('invalid', {}, [], 1.5):
                changed=dict(row); changed[field]=value
                self.assertFalse(await self.raw('reserve',new=changed))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.factor_removal_attempts'))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.account_security_slots'))

    async def test_removal_sql_proof_age_and_original_actor_are_enforced(self):
        await self.start_removal()
        row=await self.removal_store.get(self.actor,self.operation)
        busy=replace(row,state='verification_claimed',attempts=1)
        self.assertTrue(await self.removal_store.cas(row,busy,actor=self.actor))
        for proof in (int(time.time())-30,int(time.time())+10):
            deleting=replace(busy,state='delete_claimed',verified_at=proof)
            self.assertFalse(await self.raw('cas',old=asdict(busy),new=asdict(deleting)))
        deleting=replace(busy,state='delete_claimed',verified_at=int(time.time()))
        fresh=replace(self.actor,session_id='session_removal_fresh_native',browser='d'*64)
        self.assertFalse(await self.raw('cas',old=asdict(busy),new=asdict(deleting),actor=fresh))
        self.assertTrue(await self.raw('cas',old=asdict(busy),new=asdict(deleting)))
        recorded=replace(deleting,state='delete_recorded',delete_acknowledged=True)
        self.assertTrue(await self.raw('cas',old=asdict(deleting),new=asdict(recorded)))
        self.assertTrue(await self.raw('cas',old=asdict(recorded),new=asdict(replace(recorded,state='complete')),actor=fresh))
        self.assertEqual(self.actor, (await self.removal_store.get(fresh,self.operation)).actor)

    async def test_removal_request_budget_does_not_disappear_on_reconnect(self):
        for _ in range(60): self.assertTrue(await self.removal_store.eligible(self.actor))
        second=PostgreSQLRemovalStore(self.runtime,self.coordination)
        self.assertFalse(await second.eligible(self.actor))
        self.query("UPDATE careertrellis_admission.factor_removal_budgets SET window_start=clock_timestamp()-interval '61 seconds'")
        self.assertTrue(await second.eligible(self.actor))


class RemovalMigrationFenceTests(enrollment_tests.TOTPPostgresTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fixture.upgrade_identity_sessions('20260928_0017')

    async def test_unfenced_removal_migration_leaves0017_unchanged(self):
        from alembic import command
        from alembic.config import Config
        cfg=Config(); cfg.set_main_option('script_location',str(enrollment_tests.provider_tests.ROOT/'migrations'))
        cfg.attributes['migration_url']=self.fixture.engine('careertrellis_migration').url
        with self.assertRaises(DBAPIError): command.upgrade(cfg,'20260928_0018')
        self.assertEqual('20260928_0017',self.query('SELECT version_num FROM careertrellis.alembic_version'))
        self.assertIsNone(self.query("SELECT to_regclass('careertrellis_admission.factor_removal_attempts')"))
