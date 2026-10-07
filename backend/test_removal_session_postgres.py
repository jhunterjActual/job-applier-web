"""Atomic local sign-out at removal completion, using isolated PostgreSQL18."""
from dataclasses import asdict, replace
import json
import time

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from factor_removal import RemovalRejected, SessionEndingRemovalCoordinator
import test_enrollment_transfer_postgres as transfer_tests
import test_totp_enrollment_postgres as enrollment_tests


class SessionRemovalTests(transfer_tests.TransferPostgresTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fixture.upgrade_identity_sessions('20260929_0020')

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.removal=SessionEndingRemovalCoordinator(self.removal_store,self)

    def session_digest(self,actor):
        return self.coordination.digest('session',actor.authorization.local_id,actor.authorization.subject,actor.session_id)

    async def recorded(self):
        await self.completed_receipt(); await self.transfer()
        row=await self.removal_store.get(self.actor)
        busy=replace(row,state='verification_claimed',attempts=1)
        deleting=replace(busy,state='delete_claimed',verified_at=int(time.time()))
        recorded=replace(deleting,state='delete_recorded',delete_acknowledged=True)
        for old,new in ((row,busy),(busy,deleting),(deleting,recorded)):
            self.assertTrue(await self.removal_store.cas(old,new,actor=self.actor))
        self.deleted=True
        return recorded

    async def test_atomic_completion_revokes_initiating_session(self):
        await self.completed_receipt(); await self.transfer()
        self.assertEqual(self.session_digest(self.actor),self.query('SELECT initiating_session_key FROM careertrellis_admission.factor_removal_attempts'))
        self.assertEqual('complete',await self.removal.verify(self.actor,self.operation,'123456'))
        self.assertFalse(await self.removal_store.eligible(self.actor))
        self.assertIsNone(await self.removal_store.get(self.actor))
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis_admission.http_revocations WHERE session_key=:key',key=self.session_digest(self.actor)))
        self.assertTrue(self.query("SELECT expires>clock_timestamp()+interval '23 hours 59 minutes' FROM careertrellis_admission.http_revocations WHERE session_key=:key",key=self.session_digest(self.actor)))
        self.assertEqual(['challenge','verify','delete'],self.effects)

    async def test_fresh_reconcile_revokes_both_then_finalize_ends_only_new_local_session(self):
        await self.recorded()
        fresh=replace(self.actor,session_id='session_removal_reconcile_native')
        self.assertEqual('complete',await self.removal.reconcile(fresh,self.operation))
        for actor in (self.actor,fresh): self.assertFalse(await self.removal_store.eligible(actor))
        third=replace(self.actor,session_id='session_removal_finish_native')
        row=await self.removal_store.get(third)
        self.assertEqual('complete',row.state)
        self.assertTrue(await self.removal_store.finalize(third,self.operation))
        self.assertFalse(await self.removal_store.finalize(third,self.operation))
        self.assertEqual(3,self.query('SELECT count(*) FROM careertrellis_admission.http_revocations'))
        self.assertEqual(['challenge'],self.effects)

    async def test_completion_rollback_preserves_record_and_all_sessions(self):
        row=await self.recorded()
        async with self.runtime.connect() as c:
            tx=await c.begin()
            self.assertTrue(await c.scalar(text('SELECT careertrellis.factor_removal_request('
                "CAST(:actor AS jsonb),:key,'cas',CAST(:op AS uuid),CAST(:old AS jsonb),CAST(:new AS jsonb))"),
                dict(actor=json.dumps(asdict(self.actor)),key=self.session_digest(self.actor),op=self.operation,
                    old=json.dumps(asdict(row)),new=json.dumps(asdict(replace(row,state='complete'))))))
            await tx.rollback()
        self.assertEqual('delete_recorded',(await self.removal_store.get(self.actor)).state)
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.http_revocations'))
        self.assertEqual('complete',await self.removal.reconcile(self.actor,self.operation))

    async def test_finalize_cannot_complete_pending_or_cross_operation(self):
        await self.completed_receipt(); await self.transfer()
        self.assertFalse(await self.removal_store.finalize(self.actor,self.operation))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.http_revocations'))


class ExistingRemovalFenceTests(transfer_tests.TransferPostgresTests):
    async def test_existing_removal_refuses_key_backfill(self):
        await self.start_removal()
        await self.runtime.dispose(); await self.broker.dispose(); await self.migration.dispose()
        with self.assertRaises(DBAPIError): self.fixture.upgrade_identity_sessions('20260929_0020')
        self.assertEqual('20260929_0019',self.query('SELECT version_num FROM careertrellis.alembic_version'))
        self.assertEqual(0,self.query("SELECT count(*) FROM information_schema.columns WHERE table_schema='careertrellis_admission' AND table_name='factor_removal_attempts' AND column_name='initiating_session_key'"))
