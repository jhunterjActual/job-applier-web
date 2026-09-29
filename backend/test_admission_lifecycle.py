"""Actual admission migration and lifecycle paths; disposable PostgreSQL only."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from datetime import datetime, timedelta, timezone
import os
import time
import tempfile
import subprocess
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text, create_engine
from sqlalchemy.exc import DBAPIError

from signup_prototype.integration_fixture import Fixture, ROOT
from pilot_users import perform_operation, PilotUserOperationError
from account_erasure_repository import (PostgreSQLAccountErasureRepository,
    PostgreSQLAccountErasureWorkerRepository, claim_next_account_erasure, AccountErasureLeaseLost)
from postgres_database import check_postgres_ready
from postgres_database import tenant_transaction
from search_run_repository import PostgreSQLSearchRunRepository, claim_next_postgres_search_run, SearchRunLeaseLost
from discovery_repository import acquire_owner_job_catalog_lock


class AdmissionLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get('CAREERTRELLIS_ADMISSION_LIFECYCLE_TEST_URL')
        if not url:
            raise unittest.SkipTest('Dedicated admission lifecycle URL required')
        cls.fixture = Fixture(url)  # Exact loopback DB and fresh-server collision guards.
        cls.addClassCleanup(cls.fixture.close)
        cls.fixture.setup(overlay=False)
        cls.admin = cls.fixture.admin
        cls.migration = cls.fixture.engine('careertrellis_migration')
        cls.runtime = cls.fixture.engine('careertrellis_runtime')
        cls.worker = cls.fixture.engine('careertrellis_erasure_worker')
        cls.config = Config()
        cls.config.set_main_option('script_location', str(ROOT / 'migrations'))
        with cls.admin.begin() as connection:
            connection.execute(text((ROOT.parent / 'deploy/postgres/bootstrap-admission-roles.sql').read_text()))
        cls.fixture.roles += ['careertrellis_admission_owner','careertrellis_admission_broker']
        cls.fixture.schemas += ['careertrellis_admission','careertrellis_lifecycle']
        cls.upgrade()
        cls.upgrade()
        cls.fixture.grants()
        with cls.admin.begin() as connection:
            cls.preserved = connection.exec_driver_sql("SELECT p.name FROM careertrellis.profiles p JOIN careertrellis.app_users u ON u.id=p.owner_user_id WHERE u.workos_user_id='user_overlay_preserved'").scalar_one()
            # Synthetic test login only. The shipped bootstrap installs no credential.
            connection.exec_driver_sql("ALTER ROLE careertrellis_admission_broker LOGIN PASSWORD 'synthetic-integration-only'")
        cls.broker = create_engine(cls.fixture.url.set(username='careertrellis_admission_broker', password='synthetic-integration-only'), hide_parameters=True)
        cls.addClassCleanup(cls.broker.dispose)

    @classmethod
    def upgrade(cls):
        with patch.dict(os.environ, {'CAREERTRELLIS_MIGRATION_DATABASE_URL': cls.migration.url.render_as_string(hide_password=False), 'CAREERTRELLIS_CONFIG_READ_ONLY':'1'}):
            command.upgrade(cls.config, '20260907_0014')

    def query(self, sql, engine=None, **params):
        with (engine or self.admin).begin() as connection:
            result = connection.execute(text(sql), params)
            return result.scalar() if result.returns_rows else None

    def setUp(self):
        self.query('TRUNCATE careertrellis.app_users CASCADE')
        self.query("UPDATE careertrellis_admission.policy SET phase='pilot',signup_open=false,adapter_ready=false,minimum_session_version=1,maintenance=false,cap=2,generation=0,realm='synthetic-realm-v1'")
        self.owner=self.query("INSERT INTO careertrellis.app_users(workos_user_id,role) VALUES ('user_synthetic_owner','owner') RETURNING id")
        self.query('INSERT INTO careertrellis.profiles(owner_user_id) VALUES (:uid)',uid=self.owner)

    def operator(self, operation='seed', subject='user_synthetic_target'):
        return perform_operation(self.migration,operation,'user_synthetic_owner',subject)

    def target(self):
        self.operator()
        return self.query("SELECT id FROM careertrellis.app_users WHERE workos_user_id='user_synthetic_target'")

    def beta(self, cap=2, opened=False):
        self.query("UPDATE careertrellis_admission.policy SET phase='beta',cap=:cap,signup_open=:opened,adapter_ready=:opened,minimum_session_version=2",cap=cap,opened=opened)

    def reserve(self):
        rid=uuid4()
        result=self.query('SELECT careertrellis_admission.reserve(:rid,:proof,:deadline,:purpose,:realm)',self.broker,
            rid=rid,proof='a'*64,deadline=datetime.now(timezone.utc)+timedelta(minutes=10),purpose='signup',realm='synthetic-realm-v1')
        return rid,result

    def claim(self):
        return claim_next_account_erasure(engine=self.worker,worker_id=str(uuid4()),lease_seconds=300)

    def test_additive_upgrade_preserves_tenant_and_closed_default(self):
        self.assertEqual('Synthetic preservation fixture',self.preserved)
        self.assertEqual('closed',self.query('SELECT careertrellis_admission.availability()',self.broker)['status'])
        self.assertEqual('closed',self.reserve()[1]['status'])
        self.assertEqual('created',self.operator().result)
        self.assertEqual('already_active',self.operator().result)

    def test_beta_closed_enforces_cap_and_reinvite_keeps_seat(self):
        self.beta()
        self.target()
        with self.assertRaises(PilotUserOperationError): self.operator(subject='user_synthetic_second')
        self.operator('disable')
        with self.assertRaises(PilotUserOperationError): self.operator(subject='user_synthetic_second')
        self.assertEqual('reactivated',self.operator('reinvite').result)
        self.assertEqual(2,self.query('SELECT count(*) FROM careertrellis.app_users'))

    def test_actual_seed_and_signup_reservation_race_one_slot(self):
        self.beta(opened=True)
        barrier=Barrier(2)
        def seed():
            barrier.wait()
            try: self.operator(); return True
            except PilotUserOperationError: return False
        def reserve():
            barrier.wait(); return self.reserve()[1]['status']=='reserved'
        with ThreadPoolExecutor(2) as pool:
            futures=[pool.submit(seed),pool.submit(reserve)]
            self.assertEqual(1,sum(f.result(timeout=10) for f in futures))

    def test_closed_seed_counts_prior_live_reservation(self):
        self.beta(opened=True)
        self.assertEqual('reserved',self.reserve()[1]['status'])
        self.query('UPDATE careertrellis_admission.policy SET signup_open=false')
        with self.assertRaises(PilotUserOperationError): self.operator()

    def test_canonical_erasure_keeps_capacity_until_real_finalization(self):
        uid=self.target(); self.beta()
        repo=PostgreSQLAccountErasureRepository(uid,engine=self.runtime)
        first=repo.request_erasure(); self.assertEqual(first,repo.request_erasure())
        self.assertEqual(1,self.query('SELECT generation FROM careertrellis_admission.policy'))
        self.assertEqual('erasure_pending',self.operator('reinvite').result)
        with self.assertRaises(PilotUserOperationError): self.operator(subject='user_synthetic_second')
        claim=self.claim(); self.assertIsNotNone(claim)
        worker=PostgreSQLAccountErasureWorkerRepository(engine=self.worker)
        self.assertTrue(worker.finalize(claim))
        self.assertEqual(2,self.query('SELECT generation FROM careertrellis_admission.policy'))
        self.assertEqual('created',self.operator(subject='user_synthetic_second').result)

    def test_private_implementations_and_ledger_not_callable_after_grants(self):
        self.fixture.grants()
        for role in ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker'):
            engine=self.fixture.engine(role)
            for sql in ('SELECT * FROM careertrellis_admission.policy',
                        'SELECT careertrellis_lifecycle.request_account_erasure()',
                        'SELECT careertrellis_lifecycle.purge_expired_account_erasures(1)',
                        'SET ROLE careertrellis_admission_owner'):
                with self.subTest(role=role,sql=sql), self.assertRaises(DBAPIError): self.query(sql,engine)
        with self.assertRaises(DBAPIError): self.query('SELECT * FROM careertrellis_admission.flows',self.broker)

    def test_direct_null_bounds_rejected_by_canonical_sql(self):
        for sql in ('SELECT careertrellis.claim_next_account_erasure(gen_random_uuid(),NULL)',
                    'SELECT careertrellis.account_erasure_object_batch(gen_random_uuid(),1,gen_random_uuid(),NULL)',
                    'SELECT careertrellis.purge_expired_account_erasures(NULL)'):
            with self.subTest(sql=sql),self.assertRaises(DBAPIError): self.query(sql,self.worker)
        self.assertFalse(self.query('SELECT careertrellis.renew_account_erasure_lease(gen_random_uuid(),1,gen_random_uuid(),NULL)',self.worker))
        self.assertFalse(self.query('SELECT careertrellis.retry_account_erasure(gen_random_uuid(),1,gen_random_uuid(),NULL)',self.worker))

    def maintenance(self, enabled):
        with self.migration.begin() as connection:
            connection.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
            connection.execute(text('SELECT careertrellis_admission.set_import_maintenance(:enabled)'),{'enabled':enabled})

    def test_import_requires_durable_closed_gate_and_blocks_reopening(self):
        with self.migration.begin() as connection:
            connection.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
            with self.assertRaises(DBAPIError): connection.exec_driver_sql('SELECT careertrellis_admission.assert_import_maintenance()')
        self.maintenance(True)
        with self.migration.begin() as connection:
            connection.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
            connection.exec_driver_sql('SELECT careertrellis_admission.assert_import_maintenance()')
            with self.assertRaises(DBAPIError): self.maintenance(False)
            with self.assertRaises(DBAPIError): self.operator()
        self.maintenance(False)
        self.assertEqual('created',self.operator().result)

    def test_no_destructive_downgrade(self):
        with patch.dict(os.environ, {'CAREERTRELLIS_MIGRATION_DATABASE_URL':self.migration.url.render_as_string(hide_password=False)}):
            with self.assertRaisesRegex(RuntimeError,'forward correction'):
                command.downgrade(self.config,'20260903_0013')
        self.assertEqual('20260907_0014',self.query('SELECT version_num FROM careertrellis.alembic_version'))

    def test_actual_activation_creates_one_ordinary_account_and_empty_profile(self):
        self.beta(cap=3,opened=True)
        rid,result=self.reserve(); self.assertEqual('reserved',result['status'])
        attempt=uuid4()
        args={'rid':rid,'attempt':attempt,'subject':'user_synthetic_admitted'}
        self.assertEqual('exchange',self.query("SELECT careertrellis_admission.claim(:rid,repeat('a',64),repeat('b',64),:attempt)",self.broker,**args)['status'])
        result=self.query("SELECT careertrellis_admission.finish(:rid,repeat('a',64),:attempt,:subject,'synthetic-realm-v1')",self.broker,**args)
        self.assertEqual('activated',result['status'])
        self.assertEqual('user',result['role'])
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis.profiles WHERE owner_user_id=:uid',uid=result['local_id']))
        repeated=self.query("SELECT careertrellis_admission.finish(:rid,repeat('a',64),:attempt,:subject,'synthetic-realm-v1')",self.broker,**args)
        self.assertEqual(result,repeated)
        self.assertEqual(2,self.query('SELECT count(*) FROM careertrellis.app_users'))

    def test_failed_profile_insert_rolls_back_actual_admission(self):
        self.beta(opened=True); rid,_=self.reserve(); attempt=uuid4()
        self.query("SELECT careertrellis_admission.claim(:rid,repeat('a',64),repeat('b',64),:attempt)",self.broker,rid=rid,attempt=attempt)
        self.query("ALTER TABLE careertrellis.profiles ADD CONSTRAINT synthetic_reject_new CHECK(owner_user_id="+str(self.owner)+')')
        try:
            with self.assertRaises(DBAPIError):
                self.query("SELECT careertrellis_admission.finish(:rid,repeat('a',64),:attempt,'user_synthetic_rejected','synthetic-realm-v1')",self.broker,rid=rid,attempt=attempt)
            self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis.app_users'))
        finally: self.query('ALTER TABLE careertrellis.profiles DROP CONSTRAINT synthetic_reject_new')

    def test_erasure_invalidates_preexisting_claim_and_unlinks_deleted_account(self):
        uid=self.target(); self.beta(cap=4,opened=True)
        rid,_=self.reserve(); attempt=uuid4()
        self.query("SELECT careertrellis_admission.claim(:rid,repeat('a',64),repeat('b',64),:attempt)",self.broker,rid=rid,attempt=attempt)
        PostgreSQLAccountErasureRepository(uid,engine=self.runtime).request_erasure()
        self.assertEqual('expired',self.query("SELECT careertrellis_admission.finish(:rid,repeat('a',64),:attempt,'user_synthetic_late','synthetic-realm-v1')",self.broker,rid=rid,attempt=attempt)['status'])
        self.query('UPDATE careertrellis_admission.flows SET local_id=:uid WHERE id=:rid',uid=uid,rid=rid)
        self.assertTrue(PostgreSQLAccountErasureWorkerRepository(engine=self.worker).finalize(self.claim()))
        self.assertIsNone(self.query('SELECT local_id FROM careertrellis_admission.flows WHERE id=:rid',rid=rid))

    def test_actual_object_acknowledgement_and_other_tenant_preservation(self):
        uid=self.target(); oid=uuid4(); other=uuid4()
        for account,obj in ((uid,oid),(self.owner,other)):
            self.query("INSERT INTO careertrellis.stored_objects(id,owner_user_id,object_key,content_type,plaintext_size,sha256,wrapped_data_key) VALUES(:oid,:uid,:key,'application/pdf',1,:sha,:wrapped)",
                oid=obj,uid=account,key='v1/'+obj.hex,sha=b'a'*32,wrapped=b'synthetic')
        PostgreSQLAccountErasureRepository(uid,engine=self.runtime).request_erasure()
        worker=PostgreSQLAccountErasureWorkerRepository(engine=self.worker)
        self.assertFalse(worker.finalize(self.claim()))
        self.assertEqual(1,self.query('SELECT generation FROM careertrellis_admission.policy'))
        claim=self.claim(); worker.renew(claim)
        objects=worker.object_batch(claim,limit=20)
        self.assertEqual([str(oid)],[obj.object_id for obj in objects])
        worker.confirm_object_deleted(claim,objects[0])
        self.assertTrue(worker.finalize(claim))
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis.stored_objects WHERE owner_user_id=:uid',uid=self.owner))
        self.assertEqual(timedelta(days=30),self.query('SELECT receipt_expires_at-completed_at FROM careertrellis.account_erasure_requests'))

    def wait_for_lock(self,role='careertrellis_erasure_worker'):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if self.query("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE usename=:role AND wait_event_type='Lock')",role=role): return
            time.sleep(.01)
        self.fail('Expected real PostgreSQL lock wait was not observed')

    def test_finalize_catalog_wait_crossing_lease_rolls_back_all_deletion(self):
        uid=self.target(); PostgreSQLAccountErasureRepository(uid,engine=self.runtime).request_erasure()
        claim=self.claim(); worker=PostgreSQLAccountErasureWorkerRepository(engine=self.worker)
        expires=self.query("UPDATE careertrellis.account_erasure_requests SET lease_expires_at=clock_timestamp()+interval '1 second' WHERE id=:rid RETURNING lease_expires_at",rid=claim.request_id)
        with self.admin.begin() as holder,ThreadPoolExecutor(1) as pool:
            holder.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'careertrellis-job-catalog:'+str(uid)})
            future=pool.submit(worker.finalize,claim)
            try:
                self.wait_for_lock()
                until=time.monotonic()+3
                while self.query('SELECT clock_timestamp()')<=expires and time.monotonic()<until: time.sleep(.02)
            finally: holder.commit()
            with self.assertRaises(AccountErasureLeaseLost): future.result(5)
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis.app_users WHERE id=:uid',uid=uid))
        self.assertEqual(1,self.query('SELECT generation FROM careertrellis_admission.policy'))

    def test_claim_after_outer_lock_wait_uses_current_time(self):
        uid=self.target(); PostgreSQLAccountErasureRepository(uid,engine=self.runtime).request_erasure()
        with self.admin.begin() as holder,ThreadPoolExecutor(1) as pool:
            holder.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-admission-lifecycle-v1',0))")
            future=pool.submit(self.claim)
            try:
                self.wait_for_lock()
                release_time=self.query('SELECT clock_timestamp()')
            finally: holder.commit()
            claim=future.result(5)
        self.assertGreaterEqual(claim.lease_expires_at,release_time+timedelta(seconds=300))

    def test_erasure_waits_for_actual_search_owner_lock_then_denies_heartbeat(self):
        uid=self.target(); search=self.fixture.engine('careertrellis_worker')
        repository=PostgreSQLSearchRunRepository(uid,engine=self.runtime)
        run=repository.enqueue(str(uuid4()),'synthetic keyword','Remote')
        repository.publish_prepared(run,'synthetic keyword','Remote')
        claim=claim_next_postgres_search_run(str(uuid4()),engine=search)
        with tenant_transaction(search,uid) as holder,ThreadPoolExecutor(1) as pool:
            holder.execute(text('SELECT * FROM careertrellis.lock_search_run_claim(:rid,:uid,:token)'),
                {'rid':claim.run_id,'uid':uid,'token':claim.lease_token}).all()
            future=pool.submit(PostgreSQLAccountErasureRepository(uid,engine=self.runtime).request_erasure)
            try: self.wait_for_lock('careertrellis_runtime')
            finally: holder.commit()
            self.assertEqual('accepted',future.result(5)['status'])
        with self.assertRaises(SearchRunLeaseLost): PostgreSQLSearchRunRepository(uid,engine=search).heartbeat(claim)
        self.assertTrue(PostgreSQLAccountErasureWorkerRepository(engine=self.worker).finalize(self.claim()))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis.search_runs WHERE owner_user_id=:uid',uid=uid))

    def test_catalog_inflight_write_is_cascaded_by_waiting_finalizer(self):
        uid=self.target()
        PostgreSQLAccountErasureRepository(uid,engine=self.runtime).request_erasure()
        claim=self.claim(); worker=PostgreSQLAccountErasureWorkerRepository(engine=self.worker)
        with tenant_transaction(self.runtime,uid) as holder,ThreadPoolExecutor(1) as pool:
            acquire_owner_job_catalog_lock(holder,uid)
            future=pool.submit(worker.finalize,claim)
            try:
                self.wait_for_lock()
                holder.execute(text("INSERT INTO careertrellis.jobs(owner_user_id,title,url) VALUES(:uid,'Synthetic inflight','https://example.test/inflight')"),{'uid':uid})
            finally: holder.commit()
            self.assertTrue(future.result(5))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis.jobs WHERE owner_user_id=:uid',uid=uid))

    def test_expired_renew_and_retry_do_not_mutate(self):
        uid=self.target(); PostgreSQLAccountErasureRepository(uid,engine=self.runtime).request_erasure(); claim=self.claim()
        self.query('UPDATE careertrellis.account_erasure_requests SET lease_expires_at=clock_timestamp() WHERE id=:rid',rid=claim.request_id)
        worker=PostgreSQLAccountErasureWorkerRepository(engine=self.worker)
        with self.assertRaises(AccountErasureLeaseLost): worker.renew(claim)
        with self.assertRaises(AccountErasureLeaseLost): worker.retry(claim,'object_delete_failed')
        self.assertEqual('deleting_objects',self.query('SELECT status FROM careertrellis.account_erasure_requests'))

    def test_runtime_readiness_never_accepts_future_session_mode(self):
        check_postgres_ready(self.runtime)
        self.query('UPDATE careertrellis_admission.policy SET minimum_session_version=2')
        with self.assertRaises(RuntimeError): check_postgres_ready(self.runtime)

    def test_direct_import_refuses_before_provider_construction_or_inventory(self):
        import sqlite_postgres_import as importer
        from unittest.mock import MagicMock
        source=MagicMock()
        with tempfile.TemporaryDirectory() as temp,patch.object(importer,'_open_source',return_value=source),patch.object(importer,'inspect_sqlite_workspace'),patch.object(importer,'runtime_object_backend') as provider,patch.object(importer,'require_empty_object_inventory') as inventory:
            with self.assertRaises(DBAPIError):
                importer.import_sqlite_workspace(Path(temp)/'synthetic.db','user_synthetic_owner',engine=self.migration)
            provider.assert_not_called(); inventory.assert_not_called()

    def test_reservation_physical_purge_cannot_renew_old_request(self):
        self.beta(opened=True); rid,_=self.reserve()
        self.query('UPDATE careertrellis_admission.flows SET expires=clock_timestamp() WHERE id=:rid',rid=rid)
        with self.migration.begin() as connection:
            connection.exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
            self.assertEqual(0,connection.exec_driver_sql('SELECT careertrellis_admission.purge()').scalar_one())
        self.query('UPDATE careertrellis_admission.flows SET deadline=clock_timestamp() WHERE id=:rid',rid=rid)
        with self.migration.begin() as connection:
            connection.exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
            self.assertEqual(1,connection.exec_driver_sql('SELECT careertrellis_admission.purge()').scalar_one())


class BootstrapContaminationTests(unittest.TestCase):
    def setUp(self):
        url=os.environ.get('CAREERTRELLIS_ADMISSION_LIFECYCLE_TEST_URL')
        if not url: self.skipTest('Dedicated admission lifecycle URL required')
        self.fixture=Fixture(url); self.addCleanup(self.fixture.close); self.fixture.setup(overlay=False)
        self.sql=(ROOT.parent/'deploy/postgres/bootstrap-admission-roles.sql').read_text()
        with self.fixture.admin.begin() as c: c.execute(text(self.sql))
        self.fixture.roles+=['careertrellis_admission_owner','careertrellis_admission_broker']
        self.fixture.schemas+=['careertrellis_admission','careertrellis_lifecycle']

    def contaminate(self,statement):
        with self.fixture.admin.begin() as c: c.exec_driver_sql(statement)
        with self.assertRaises(DBAPIError),self.fixture.admin.begin() as c: c.execute(text(self.sql))
        migration=self.fixture.engine('careertrellis_migration')
        cfg=Config(); cfg.set_main_option('script_location',str(ROOT/'migrations'))
        with patch.dict(os.environ,{'CAREERTRELLIS_MIGRATION_DATABASE_URL':migration.url.render_as_string(hide_password=False)}),self.assertRaises(DBAPIError): command.upgrade(cfg,'head')
        with self.fixture.admin.begin() as c:
            self.assertEqual('20260903_0013',c.exec_driver_sql('SELECT version_num FROM careertrellis.alembic_version').scalar_one())

    def test_incoming_runtime_owner_membership_refused(self):
        self.contaminate('GRANT careertrellis_admission_owner TO careertrellis_runtime')

    def test_unexpected_schema_acl_refused(self):
        self.contaminate('GRANT USAGE ON SCHEMA careertrellis_admission TO careertrellis_runtime')

    def test_unexpected_global_default_grants_refused(self):
        self.contaminate('ALTER DEFAULT PRIVILEGES FOR ROLE careertrellis_admission_owner GRANT SELECT ON TABLES TO careertrellis_runtime')

    def test_root_wrapper_streams_sql_from_release_inaccessible_to_postgres(self):
        container=os.environ.get('CAREERTRELLIS_ADMISSION_TEST_CONTAINER')
        if container != 'careertrellis-admission-lifecycle-20260907':
            self.skipTest('Exact disposable container required for native permission gate')
        def run(*args,ok=True):
            result=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if ok and result.returncode: self.fail('Disposable native permission command failed')
            return result
        label=run('inspect','--format','{{index .Config.Labels "careertrellis.scope"}}',container).stdout.strip()
        self.assertEqual('isolated-admission-lifecycle',label)
        ports=json.loads(run('inspect','--format','{{json .NetworkSettings.Ports}}',container).stdout)['5432/tcp']
        self.assertEqual([{'HostIp':'127.0.0.1','HostPort':str(self.fixture.url.port)}],ports)
        root='/tmp/ct-admission-permission-'+uuid4().hex
        run('exec',container,'install','-d','-m','0700',root,root+'/scripts',root+'/postgres')
        run('cp',str(ROOT.parent/'deploy/scripts/bootstrap-admission-roles.sh'),container+':'+root+'/scripts/bootstrap-admission-roles.sh')
        run('cp',str(ROOT.parent/'deploy/postgres/bootstrap-admission-roles.sql'),container+':'+root+'/postgres/bootstrap-admission-roles.sql')
        self.assertNotEqual(0,run('exec',container,'runuser','-u','postgres','--','test','-r',root+'/postgres/bootstrap-admission-roles.sql',ok=False).returncode)
        run('exec','--env','CAREERTRELLIS_POSTGRES_DATABASE=ct_signup_integration',container,'bash',root+'/scripts/bootstrap-admission-roles.sh')
        for script in ('bootstrap-admission-roles.sh','migrate-postgres.sh','deploy-release.sh','provision-postgres-host.sh'):
            run('cp',str(ROOT.parent/'deploy/scripts'/script),container+':'+root+'/scripts/'+script)
            run('exec',container,'bash','-n',root+'/scripts/'+script)


if __name__=='__main__': unittest.main()
