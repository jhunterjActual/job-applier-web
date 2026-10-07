"""Shared security-operation exclusion on a dedicated synthetic PostgreSQL18 server."""
import asyncio
from dataclasses import asdict, replace
import json
import time
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import test_totp_enrollment_postgres as enrollment_tests
from totp_enrollment import EnrollmentRecord, EnrollmentRejected, EnrollmentUncertain


class SecuritySlotTests(enrollment_tests.TOTPPostgresTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fixture.upgrade_identity_sessions('20260928_0017')

    def insert_competitor(self, connection, kind='removal'):
        connection.execute(text('INSERT INTO careertrellis_admission.account_security_slots '
            '(account_id,operation_kind,operation,authorization_binding,actor,stage) '
            "VALUES(:uid,:kind,CAST(:op AS uuid),CAST(:auth AS jsonb),CAST(:actor AS jsonb),'claimed')"),
            dict(uid=self.actor.authorization.local_id,kind=kind,op=str(uuid4()),
                 auth=json.dumps(asdict(self.actor.authorization)),actor=json.dumps(asdict(self.actor))))

    async def test_other_kinds_block_enrollment_without_provider_calls_or_ttl_stealing(self):
        for kind in ('removal','recovery'):
            with self.fixture.admin.begin() as connection:
                self.insert_competitor(connection,kind)
            self.query("UPDATE careertrellis_admission.account_security_slots SET created_at=clock_timestamp()-interval '30 days'")
            with self.assertRaises(EnrollmentRejected):
                await self.enrollment.start(self.actor)
            self.assertEqual(0,self.factor_provider.creates)
            self.assertEqual(kind,self.query('SELECT operation_kind FROM careertrellis_admission.account_security_slots'))
            # Fixture-only reset between independent synthetic cases.
            self.query('DELETE FROM careertrellis_admission.account_security_slots')

    async def test_competing_transaction_serializes_before_enrollment_dispatch(self):
        with self.fixture.admin.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended('careertrellis-admission-lifecycle-v1',0))"))
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-http-session-v1',0))"))
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {'key':'careertrellis-totp-'+str(self.actor.authorization.local_id)})
            self.insert_competitor(connection)
            task=asyncio.create_task(self.enrollment.start(self.actor))
            await asyncio.sleep(.05)
            self.assertEqual(0,self.factor_provider.creates)
        with self.assertRaises(EnrollmentRejected):
            await asyncio.wait_for(task,5)
        self.assertEqual(0,self.factor_provider.creates)

    async def test_slot_and_enrollment_reservation_rollback_together(self):
        row=EnrollmentRecord(str(uuid4()),self.actor,'creating',int(time.time())+240)
        key=self.coordination.digest('session',self.actor.authorization.local_id,
            self.actor.authorization.subject,self.actor.session_id)
        async with self.runtime.connect() as connection:
            transaction=await connection.begin()
            self.assertTrue(await connection.scalar(text("SELECT careertrellis.totp_request("
                "CAST(:actor AS jsonb),:key,'reserve',CAST(:op AS uuid),NULL,CAST(:row AS jsonb))"),
                {'actor':json.dumps(asdict(self.actor)),'key':key,'op':row.operation,'row':json.dumps(asdict(row))}))
            await transaction.rollback()
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.account_security_slots'))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.totp_attempts'))
        await self.enrollment.start(self.actor)
        self.assertEqual(1,self.factor_provider.creates)

    async def test_closed_and_complete_slots_remain_and_keep_original_actor(self):
        operation=await self.enrollment.start(self.actor)
        original=self.query('SELECT actor FROM careertrellis_admission.account_security_slots')
        fresh=replace(self.actor,session_id='session_security_fresh')
        await self.enrollment.close(fresh,operation)
        self.assertEqual('closed',self.query('SELECT stage FROM careertrellis_admission.account_security_slots'))
        self.factor_provider.verified=True
        self.assertEqual('complete',await self.enrollment.reconcile(fresh,operation))
        self.assertEqual(original,self.query('SELECT actor FROM careertrellis_admission.account_security_slots'))
        self.assertEqual('complete',self.query('SELECT stage FROM careertrellis_admission.account_security_slots'))
        with self.assertRaises(EnrollmentRejected): await self.enrollment.start(fresh)

    async def test_missing_or_changed_slot_fails_closed(self):
        operation=await self.enrollment.start(self.actor)
        self.query("UPDATE careertrellis_admission.account_security_slots SET operation=:op",op=uuid4())
        with self.assertRaises(EnrollmentUncertain): await self.enroll_store.eligible(self.actor)
        self.query('DELETE FROM careertrellis_admission.account_security_slots')
        with self.assertRaises(EnrollmentUncertain): await self.enroll_store.eligible(self.actor)
        with self.assertRaises(EnrollmentUncertain): await self.enroll_store.get(self.actor,operation)
        self.assertEqual(1,self.factor_provider.creates)

    async def test_private_slot_and_legacy_entry_stay_inaccessible_after_grant_replay(self):
        self.fixture.grants()
        signature='careertrellis_admission.totp_request_0016_internal(jsonb,text,text,uuid,jsonb,jsonb)'
        for role in ('careertrellis_schema_owner','careertrellis_runtime','careertrellis_worker',
                     'careertrellis_erasure_worker','careertrellis_admission_broker'):
            self.assertFalse(self.query("SELECT has_function_privilege(:role,:fn,'EXECUTE')",role=role,fn=signature))
            self.assertFalse(self.query("SELECT has_table_privilege(:role,'careertrellis_admission.account_security_slots','SELECT')",role=role))
        with self.assertRaises(DBAPIError):
            with self.fixture.engine('careertrellis_runtime').begin() as connection:
                connection.execute(text("SELECT careertrellis_admission.totp_request_0016_internal('{}','x','eligible')"))
        await self.enrollment.start(self.actor)
        self.query('DELETE FROM careertrellis.app_users WHERE id=:uid',uid=self.actor.authorization.local_id)
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.account_security_slots'))


class BackfillTests(enrollment_tests.TOTPPostgresTests):
    # Run only this test at0016 before upgrading; inherited tests are deliberately
    # not selected by the standalone native runner.
    async def test_all_historical_states_backfill_without_releasing_uncertainty(self):
        from alembic import command
        from alembic.config import Config
        cfg=Config(); cfg.set_main_option('script_location',str(enrollment_tests.provider_tests.ROOT/'migrations'))
        cfg.attributes['migration_url']=self.fixture.engine('careertrellis_migration').url
        with self.assertRaises(DBAPIError): command.upgrade(cfg,'20260928_0017')
        self.assertEqual('20260915_0016',self.query('SELECT version_num FROM careertrellis.alembic_version'))
        self.assertIsNone(self.query("SELECT to_regclass('careertrellis_admission.account_security_slots')"))
        states=('creating','pending','verifying','closed','complete')
        for number,state in enumerate(states):
            uid=self.query("INSERT INTO careertrellis.app_users(workos_user_id,role) VALUES(:subject,'user') RETURNING id",
                subject='user_slot_history_'+str(number))
            actor=replace(self.actor,authorization=replace(self.actor.authorization,local_id=uid,subject='user_slot_history_'+str(number)))
            row=EnrollmentRecord(str(uuid4()),actor,state,int(time.time())-100)
            self.query('INSERT INTO careertrellis_admission.totp_attempts(account_id,operation,record) '
                'VALUES(:uid,CAST(:op AS uuid),CAST(:row AS jsonb))',uid=uid,op=row.operation,row=json.dumps(asdict(row)))
        await self.runtime.dispose()
        await self.broker.dispose()
        await self.migration.dispose()
        self.fixture.upgrade_identity_sessions('20260928_0017')
        self.assertEqual(5,self.query('SELECT count(*) FROM careertrellis_admission.account_security_slots'))
        self.assertTrue(self.query("SELECT bool_and(s.operation=a.operation AND s.actor=a.record->'actor' "
            "AND s.authorization_binding=a.record->'actor'->'authorization' AND s.stage=a.record->>'state') "
            'FROM careertrellis_admission.account_security_slots s JOIN careertrellis_admission.totp_attempts a USING(account_id)'))
