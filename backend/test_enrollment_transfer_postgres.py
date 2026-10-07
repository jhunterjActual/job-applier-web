"""Future enrollment receipts and atomic removal transfer on isolated PostgreSQL18."""
import asyncio
from dataclasses import asdict, replace
import json
import time
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from enrollment_disposition import AttributableEnrollmentCoordinator
from enrollment_disposition_postgres import PostgreSQLEnrollmentDispositionStore
from factor_removal import RemovalRecord, RemovalRejected
from totp_enrollment import EnrollmentRejected, EnrollmentUncertain
import test_factor_removal_postgres as removal_tests
import test_totp_enrollment_postgres as enrollment_tests


class TransferPostgresTests(removal_tests.RemovalPostgresTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fixture.upgrade_identity_sessions('20260929_0019')

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.disposition_store=PostgreSQLEnrollmentDispositionStore(self.runtime,self.coordination)
        self.attributable=AttributableEnrollmentCoordinator(self.disposition_store,self.factor_provider,Fernet.generate_key())

    async def completed_receipt(self):
        operation=await self.attributable.start(self.actor)
        self.assertEqual('complete',await self.attributable.verify(self.actor,operation,'123456'))
        return operation

    async def transfer(self):
        return await self.removal.start(self.actor,self.operation,enrollment_tests.FACTOR)

    async def test_receipt_transfers_exact_factor_and_retains_predecessor(self):
        predecessor=await self.completed_receipt()
        self.assertTrue(await self.removal_store.eligible(self.actor))
        with self.assertRaises(RemovalRejected):
            await self.removal.start(self.actor,self.operation,'auth_factor_wrong_transfer')
        self.assertIsNone(self.query('SELECT consumed_by FROM careertrellis_admission.enrollment_dispositions'))
        await self.transfer()
        self.assertEqual(self.operation,str(self.query('SELECT consumed_by FROM careertrellis_admission.enrollment_dispositions')))
        self.assertEqual(predecessor,str(self.query('SELECT operation FROM careertrellis_admission.totp_attempts')))
        self.assertEqual('complete',self.query("SELECT record->>'state' FROM careertrellis_admission.totp_attempts"))
        self.assertEqual('removal',self.query('SELECT operation_kind FROM careertrellis_admission.account_security_slots'))
        self.assertEqual('complete',await self.removal.verify(self.actor,self.operation,'123456'))
        with self.assertRaises(EnrollmentRejected): await self.attributable.start(self.actor)

    async def test_legacy_complete_cannot_mint_or_transfer_receipt(self):
        operation=await self.enrollment.start(self.actor)
        self.factor_provider.verified=True
        self.assertEqual('complete',await self.enrollment.reconcile(self.actor,operation))
        self.assertEqual('complete',await self.attributable.reconcile(self.actor,operation))
        row=await self.enroll_store.get(self.actor,operation)
        with self.assertRaises(EnrollmentUncertain):
            await self.disposition_store.finish_verified(row,row,actor=self.actor)
        key=self.coordination.digest('session',self.actor.authorization.local_id,self.actor.authorization.subject,self.actor.session_id)
        async with self.runtime.begin() as c:
            self.assertFalse(await c.scalar(text('SELECT careertrellis.totp_finish_verified('
                'CAST(:actor AS jsonb),:key,CAST(:row AS jsonb),CAST(:row AS jsonb))'),
                dict(actor=json.dumps(asdict(self.actor)),key=key,row=json.dumps(asdict(row)))))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.enrollment_dispositions'))
        self.assertFalse(await self.removal_store.eligible(self.actor))

    async def test_lost_verify_response_and_membership_never_mint(self):
        operation=await self.attributable.start(self.actor)
        self.factor_provider.lost_verify=True
        with self.assertRaises(EnrollmentUncertain): await self.attributable.verify(self.actor,operation,'123456')
        self.assertTrue(self.factor_provider.verified)
        self.assertEqual('unresolved',await self.attributable.reconcile(self.actor,operation))
        with self.assertRaises(EnrollmentRejected): await self.attributable.verify(self.actor,operation,'123456')
        self.assertEqual(1,self.factor_provider.verifications)
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.enrollment_dispositions'))
        self.assertFalse(await self.removal_store.eligible(self.actor))

    async def test_receipt_mismatch_or_consumption_blocks_transfer(self):
        await self.completed_receipt()
        original=self.query('SELECT record FROM careertrellis_admission.enrollment_dispositions')
        self.query("UPDATE careertrellis_admission.enrollment_dispositions SET record=jsonb_set(record,'{factor}','\"auth_factor_wrong_transfer\"')")
        self.assertFalse(await self.removal_store.eligible(self.actor))
        self.query('UPDATE careertrellis_admission.enrollment_dispositions SET record=CAST(:row AS jsonb),consumed_by=:op',
            row=json.dumps(original),op=uuid4())
        self.assertFalse(await self.removal_store.eligible(self.actor))
        self.assertEqual([],self.effects)

    async def test_transfer_reservation_rollback_preserves_unconsumed_enrollment(self):
        predecessor=await self.completed_receipt()
        row=RemovalRecord(self.operation,self.actor,enrollment_tests.FACTOR,'challenge_claimed',int(time.time())+240)
        key=self.coordination.digest('session',self.actor.authorization.local_id,self.actor.authorization.subject,self.actor.session_id)
        async with self.runtime.connect() as c:
            tx=await c.begin()
            self.assertTrue(await c.scalar(text("SELECT careertrellis.factor_removal_request("
                "CAST(:actor AS jsonb),:key,'reserve',CAST(:op AS uuid),NULL,CAST(:row AS jsonb))"),
                dict(actor=json.dumps(asdict(self.actor)),key=key,op=self.operation,row=json.dumps(asdict(row)))))
            await tx.rollback()
        self.assertEqual(predecessor,str(self.query('SELECT operation FROM careertrellis_admission.account_security_slots')))
        self.assertIsNone(self.query('SELECT consumed_by FROM careertrellis_admission.enrollment_dispositions'))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.factor_removal_attempts'))
        await self.transfer()

    async def test_transfer_concurrent_claims_dispatch_once(self):
        await self.completed_receipt()
        entered,release=asyncio.Event(),asyncio.Event()
        self.pause=entered,release
        task=asyncio.create_task(self.transfer())
        await asyncio.wait_for(entered.wait(),5)
        try:
            with self.assertRaises(RemovalRejected): await self.transfer()
            with self.assertRaises(EnrollmentRejected): await self.attributable.start(self.actor)
        finally:
            release.set()
            await task
        self.assertEqual(['challenge'],self.effects)
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis_admission.enrollment_dispositions WHERE consumed_by IS NOT NULL'))

    async def test_finish_receipt_and_complete_rollback_together(self):
        operation=await self.attributable.start(self.actor)
        row=await self.enroll_store.get(self.actor,operation)
        busy=replace(row,state='verifying',attempts=1)
        self.assertTrue(await self.enroll_store.cas(row,busy,actor=self.actor))
        complete=replace(busy,state='complete',sealed_secret=None)
        key=self.coordination.digest('session',self.actor.authorization.local_id,self.actor.authorization.subject,self.actor.session_id)
        async with self.runtime.connect() as c:
            tx=await c.begin()
            self.assertTrue(await c.scalar(text('SELECT careertrellis.totp_finish_verified('
                'CAST(:actor AS jsonb),:key,CAST(:old AS jsonb),CAST(:new AS jsonb))'),
                dict(actor=json.dumps(asdict(self.actor)),key=key,old=json.dumps(asdict(busy)),new=json.dumps(asdict(complete)))))
            await tx.rollback()
        self.assertEqual('verifying',self.query("SELECT record->>'state' FROM careertrellis_admission.totp_attempts"))
        self.assertEqual('verifying',self.query('SELECT stage FROM careertrellis_admission.account_security_slots'))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.enrollment_dispositions'))

    async def test_finish_requires_exact_verifying_actor_and_payload(self):
        operation=await self.attributable.start(self.actor)
        row=await self.enroll_store.get(self.actor,operation)
        busy=replace(row,state='verifying',attempts=1)
        self.assertTrue(await self.enroll_store.cas(row,busy,actor=self.actor))
        complete=replace(busy,state='complete',sealed_secret=None)
        fresh=replace(self.actor,session_id='session_transfer_fresh_native')
        key=self.coordination.digest('session',fresh.authorization.local_id,fresh.authorization.subject,fresh.session_id)
        async with self.runtime.begin() as c:
            self.assertFalse(await c.scalar(text('SELECT careertrellis.totp_finish_verified('
                'CAST(:actor AS jsonb),:key,CAST(:old AS jsonb),CAST(:new AS jsonb))'),
                dict(actor=json.dumps(asdict(fresh)),key=key,old=json.dumps(asdict(busy)),new=json.dumps(asdict(complete)))))
        original_key=self.coordination.digest('session',self.actor.authorization.local_id,self.actor.authorization.subject,self.actor.session_id)
        for changed in (replace(complete,factor='auth_factor_wrong_transfer'),
                        replace(complete,challenge='auth_challenge_wrong_transfer'),
                        replace(complete,attempts=2),replace(complete,deadline=complete.deadline+1)):
            async with self.runtime.begin() as c:
                self.assertFalse(await c.scalar(text('SELECT careertrellis.totp_finish_verified('
                    'CAST(:actor AS jsonb),:key,CAST(:old AS jsonb),CAST(:new AS jsonb))'),
                    dict(actor=json.dumps(asdict(self.actor)),key=original_key,
                        old=json.dumps(asdict(busy)),new=json.dumps(asdict(changed)))))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.enrollment_dispositions'))

    async def test_disposition_grants_and_history_erasure(self):
        await self.completed_receipt()
        self.fixture.grants()
        for role in ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_migration'):
            self.assertEqual(role=='careertrellis_runtime',self.query("SELECT has_function_privilege(:r,'careertrellis.totp_finish_verified(jsonb,text,jsonb,jsonb)','EXECUTE')",r=role))
            self.assertFalse(self.query("SELECT has_table_privilege(:r,'careertrellis_admission.enrollment_dispositions','SELECT,INSERT,UPDATE,DELETE')",r=role))
        self.query('DELETE FROM careertrellis.app_users WHERE id=:uid',uid=self.actor.authorization.local_id)
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.enrollment_dispositions'))


class TransferFenceTests(removal_tests.RemovalPostgresTests):
    async def test_unfenced_transfer_migration_leaves0018_unchanged(self):
        from alembic import command
        from alembic.config import Config
        cfg=Config(); cfg.set_main_option('script_location',str(enrollment_tests.provider_tests.ROOT/'migrations'))
        cfg.attributes['migration_url']=self.fixture.engine('careertrellis_migration').url
        with self.assertRaises(DBAPIError): command.upgrade(cfg,'20260929_0019')
        self.assertEqual('20260928_0018',self.query('SELECT version_num FROM careertrellis.alembic_version'))
        self.assertIsNone(self.query("SELECT to_regclass('careertrellis_admission.enrollment_dispositions')"))
