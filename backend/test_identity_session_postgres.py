"""Real PostgreSQL18 roles/RLS and production session code, synthetic providers."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import time
import unittest
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from account_session_candidate import SessionInvalid
from postgres_database import tenant_transaction
from session_authorization import current
from test_provider_session_postgres import PostgreSQLCandidateTests
from test_provider_session_candidate import SETTINGS, SUBJECT


class IdentityMigrationGateTests(unittest.IsolatedAsyncioTestCase):
    setUpClass=classmethod(PostgreSQLCandidateTests.setUpClass.__func__)
    asyncSetUp=PostgreSQLCandidateTests.asyncSetUp
    query=PostgreSQLCandidateTests.query

    def attempt(self):
        from alembic import command
        from alembic.config import Config
        from signup_prototype.integration_fixture import ROOT
        cfg=Config(); cfg.set_main_option('script_location',str(ROOT/'migrations'))
        cfg.attributes['migration_url']=self.fixture.engine('careertrellis_migration').url
        command.upgrade(cfg,'20260909_0015')

    def unchanged(self):
        self.assertEqual('20260907_0014',self.query('SELECT version_num FROM careertrellis.alembic_version'))
        self.assertIsNone(self.query("SELECT to_regclass('careertrellis_admission.identity_accounts')"))

    async def test_unfenced_migration_refuses_without_schema_change(self):
        with self.assertRaises(DBAPIError): self.attempt()
        self.unchanged()

    async def test_existing_connection_blocks_even_after_login_fence(self):
        runtime=self.fixture.engine('careertrellis_runtime')
        callers=('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker')
        try:
            with runtime.connect() as existing:
                existing.execute(text('SELECT 1'))
                self.query('UPDATE careertrellis_admission.policy SET maintenance=true,signup_open=false')
                for role in callers: self.query('ALTER ROLE '+role+' NOLOGIN')
                with self.assertRaises(DBAPIError): self.attempt()
                self.unchanged()
        finally:
            runtime.dispose()
            for role in callers: self.query('ALTER ROLE '+role+' LOGIN')


class LegacyPilotMigrationTests(unittest.IsolatedAsyncioTestCase):
    setUpClass=classmethod(PostgreSQLCandidateTests.setUpClass.__func__)
    asyncSetUp=PostgreSQLCandidateTests.asyncSetUp
    query=PostgreSQLCandidateTests.query

    async def test_real_disable_before_0015_preserves_legacy_accounts_and_profiles(self):
        from pilot_users import perform_operation
        engine=self.fixture.engine('careertrellis_migration')
        self.query('UPDATE careertrellis_admission.policy SET cap=25,signup_open=false')
        subjects=('user_synthetic_before_migration_one','user_synthetic_before_migration_two')
        for subject in subjects:
            self.assertEqual('created',perform_operation(engine,'seed','user_synthetic_owner',subject).result)
        before=self.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY owner_user_id) FROM careertrellis.profiles p')
        generation=self.query('SELECT generation FROM careertrellis_admission.policy')
        for subject in subjects:
            self.assertEqual('disabled',perform_operation(engine,'disable','user_synthetic_owner',subject).result)
        self.assertEqual(generation+2,self.query('SELECT generation FROM careertrellis_admission.policy'))
        accounts=self.query('SELECT jsonb_agg(to_jsonb(u) ORDER BY id) FROM careertrellis.app_users u')
        self.assertEqual('20260907_0014',self.query('SELECT version_num FROM careertrellis.alembic_version'))
        self.fixture.upgrade_identity_sessions()
        self.assertEqual('20260909_0015',self.query('SELECT version_num FROM careertrellis.alembic_version'))
        self.assertEqual(accounts,self.query('SELECT jsonb_agg(to_jsonb(u) ORDER BY id) FROM careertrellis.app_users u'))
        self.assertEqual(before,self.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY owner_user_id) FROM careertrellis.profiles p'))
        self.assertEqual(2,self.query("SELECT count(*) FROM careertrellis_admission.identity_accounts WHERE role='user' AND disabled"))
        self.assertEqual(1,self.query("SELECT count(*) FROM careertrellis_admission.identity_accounts WHERE role='owner' AND NOT disabled"))
        self.assertTrue(self.query('SELECT bool_and(a.subject=u.workos_user_id AND a.disabled=(u.disabled_at IS NOT NULL)) '
            'FROM careertrellis_admission.identity_accounts a JOIN careertrellis.app_users u ON u.id=a.id'))


class IdentitySessionPostgresTests(unittest.IsolatedAsyncioTestCase):
    schema_revision = '20260909_0015'
    setUpClass = classmethod(PostgreSQLCandidateTests.setUpClass.__func__)
    query = PostgreSQLCandidateTests.query
    flow = PostgreSQLCandidateTests.flow
    activate = PostgreSQLCandidateTests.activate

    async def asyncSetUp(self):
        # Reset only this disposable fixture's provider before parent setup
        # creates the next case's owner; never inherit another case's target.
        # These two histories are not account-owned and survive app_users CASCADE.
        # Each independent case starts a new synthetic provider/configuration history.
        self.query('TRUNCATE careertrellis_admission.identity_provider_history, '
            'careertrellis_admission.identity_configuration_receipts')
        self.query('UPDATE careertrellis_admission.identity_provider SET binding=:b',b=SETTINGS.binding)
        await PostgreSQLCandidateTests.asyncSetUp(self)
        self.query('UPDATE careertrellis_admission.policy SET maintenance=true,signup_open=false')
        previous = self.query('SELECT binding FROM careertrellis_admission.identity_provider')
        self.operator('SELECT careertrellis_admission.configure_identity_provider(:old,:target)',old=previous,target=SETTINGS.binding)
        self.query("UPDATE careertrellis_admission.policy SET maintenance=false,signup_open=true")
        self.store.provider_binding = SETTINGS.binding
        self.codec.require_epoch = True

    def operator(self,sql,**params):
        with self.fixture.engine('careertrellis_migration').begin() as c:
            c.exec_driver_sql("SET LOCAL lock_timeout='200ms'")
            result = c.execute(text(sql),params)
            return result.scalar() if result.returns_rows else None

    def prepare(self,session,*,target=None,operation=None,seconds=300,owner=False):
        op = operation or str(uuid4())
        self.operator('SELECT careertrellis_admission.identity_prepare(CAST(:op AS uuid),:uid,:b,:s,:e,CAST(:i AS uuid),:tb,:ts,:deadline,:owner)',
            op=op,uid=session.local_id,b=session.provider_binding,s=session.subject,e=session.epoch,i=session.incarnation,
            tb=target[0] if target else session.provider_binding,ts=target[1] if target else session.subject,
            deadline=datetime.now(timezone.utc)+timedelta(seconds=seconds),owner=owner)
        return op

    def attest_activate(self,op,epoch):
        self.operator("SELECT careertrellis_admission.identity_attest(CAST(:op AS uuid),:e,repeat('a',64),clock_timestamp(),clock_timestamp()+interval '30 seconds')",op=op,e=epoch)
        return self.operator('SELECT careertrellis_admission.identity_activate(CAST(:op AS uuid),:e)',op=op,e=epoch)

    async def test_real_cookie_hold_recovery_and_old_cookie_never_returns(self):
        cookie = await self.activate()
        session = self.codec.open(cookie)
        self.assertEqual(3600,session.expires-session.issued)
        before = self.query('SELECT row_to_json(p) FROM careertrellis.profiles p WHERE owner_user_id=:uid',uid=session.local_id)
        auth = await self.adapter.authenticate(cookie)
        self.assertEqual('authenticated',auth.status)
        op = self.prepare(session)
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)
        self.attest_activate(op,session.epoch+1)
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie,refresh=True)).status)
        flow,state = await self.flow('login')
        fresh = await self.adapter.complete(flow,self.browser,state,'synthetic-fresh-code')
        self.assertEqual('login',fresh.status)
        self.assertEqual(session.epoch+1,self.codec.open(fresh.cookie).epoch)
        self.assertEqual(before,self.query('SELECT row_to_json(p) FROM careertrellis.profiles p WHERE owner_user_id=:uid',uid=session.local_id))
        self.assertEqual(2,self.query('SELECT count(*) FROM careertrellis.app_users'))

    async def test_google_evidence_intake_exact_binding_replay_and_hold(self):
        await self.test_operator_evidence_intake_exact_binding_replay_and_hold(version=2)

    async def test_operator_evidence_intake_exact_binding_replay_and_hold(self, version=1):
        from admission_operations import encoded
        from identity_evidence import EvidenceRejected, submit
        from test_identity_evidence import packet
        cookie = await self.activate(); session = self.codec.open(cookie)
        op = self.prepare(session)
        context = self.operator('SELECT careertrellis_admission.identity_evidence_context(CAST(:op AS uuid))', op=op)
        proof = packet(context)
        if version == 2:
            proof['version'] = 2
            proof['target'] = {'method': 'fresh_authentication_under_optional_mfa',
                'completed_at': proof['verified_at'], 'authentication_method': 'google',
                'email_verified': True, 'applicable_factor_status': 'none_enrolled'}
        engine = self.fixture.engine('careertrellis_migration')
        import copy
        for field, value in (('old_subject','user_wrong'), ('target_binding','c'*64),
                             ('account_id',999), ('incarnation',str(uuid4())),
                             ('predecessor',str(uuid4())), ('epoch',999)):
            bad = copy.deepcopy(proof); bad['context'][field] = value
            with self.assertRaises(EvidenceRejected): submit(encoded(bad),engine)
        self.assertEqual('held',self.query('SELECT state FROM careertrellis_admission.identity_transitions WHERE operation=CAST(:op AS uuid)',op=op))
        raw = encoded(proof)
        from contextlib import contextmanager
        from unittest.mock import patch
        original_begin = engine.begin
        @contextmanager
        def lost_ack():
            with original_begin() as connection: yield connection
            raise RuntimeError('synthetic lost commit acknowledgement')
        with patch.object(engine,'begin',lost_ack):
            with self.assertRaises(EvidenceRejected): submit(raw,engine)
        self.assertEqual({'version':1,'outcome':'attested_account_held'},submit(raw,engine))
        self.assertEqual(submit(raw,engine),submit(raw,engine))
        self.assertTrue(self.query('SELECT held FROM careertrellis_admission.identity_accounts WHERE id=:uid',uid=session.local_id))
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)
        changed = copy.deepcopy(proof)
        if version == 2: changed['target']['authentication_method'] = 'password'
        else: changed['target']['factor'] = 'password_totp'
        with self.assertRaises(EvidenceRejected): submit(encoded(changed),engine)
        for role in ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker'):
            with self.assertRaises(EvidenceRejected): submit(raw,self.fixture.engine(role))
        from sqlalchemy import create_engine
        broker = create_engine(self.fixture.url.set(username='careertrellis_admission_broker',password='synthetic-integration-only'),hide_parameters=True)
        try:
            with self.assertRaises(EvidenceRejected): submit(raw,broker)
        finally: broker.dispose()

    async def test_operator_intake_disabled_and_reenabled_fail_closed(self):
        from admission_operations import encoded
        from identity_evidence import EvidenceRejected, submit
        from test_identity_evidence import packet
        cookie = await self.activate(); session = self.codec.open(cookie)
        op = self.prepare(session)
        context = self.operator('SELECT careertrellis_admission.identity_evidence_context(CAST(:op AS uuid))', op=op)
        raw = encoded(packet(context)); engine = self.fixture.engine('careertrellis_migration')
        self.query('UPDATE careertrellis.app_users SET disabled_at=clock_timestamp() WHERE id=:uid',uid=session.local_id)
        with self.assertRaises(EvidenceRejected): submit(raw,engine)
        self.query('UPDATE careertrellis.app_users SET disabled_at=NULL WHERE id=:uid',uid=session.local_id)
        with self.assertRaises(EvidenceRejected): submit(raw,engine)
        self.assertTrue(self.query('SELECT held FROM careertrellis_admission.identity_accounts WHERE id=:uid',uid=session.local_id))

    async def test_operator_historical_reconciliation_after_activation(self):
        from admission_operations import encoded
        import identity_operator as operator
        import identity_evidence as evidence
        from test_identity_evidence import packet
        from unittest.mock import patch
        import hashlib
        cookie=await self.activate(); session=self.codec.open(cookie); op=self.prepare(session)
        context=self.operator('SELECT careertrellis_admission.identity_evidence_context(CAST(:op AS uuid))',op=op)
        proof=packet(context); raw=encoded(proof); digest=hashlib.sha256(raw).hexdigest()
        engine=self.fixture.engine('careertrellis_migration')
        with patch.object(operator,'DATABASE','ct_signup_integration'):
            operator.preflight(engine)
            evidence.submit(raw,engine)
            self.operator('SELECT careertrellis_admission.identity_activate(CAST(:op AS uuid),:e)',op=op,e=session.epoch+1)
            self.assertEqual({'state':'committed','recorded':True},operator.inspect(engine,op,digest,proof['verified_at'],proof['expires_at']))
            self.assertFalse(operator.inspect(engine,op,digest,proof['verified_at'],proof['verified_at'])['recorded'])
            for role in ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker'):
                with self.assertRaises(Exception): operator.inspect(self.fixture.engine(role),op,digest,proof['verified_at'],proof['expires_at'])

    async def test_operator_historical_reconciliation_after_expired_resume(self):
        from admission_operations import encoded
        import identity_operator as operator
        import identity_evidence as evidence
        from test_identity_evidence import packet
        from unittest.mock import patch
        import hashlib
        cookie=await self.activate(); session=self.codec.open(cookie); op=self.prepare(session,seconds=.5)
        context=self.operator('SELECT careertrellis_admission.identity_evidence_context(CAST(:op AS uuid))',op=op)
        proof=packet(context); proof['expires_at']=context['deadline']; raw=encoded(proof)
        engine=self.fixture.engine('careertrellis_migration'); evidence.submit(raw,engine)
        await asyncio.sleep(.55)
        next_op=str(uuid4())
        self.operator('SELECT careertrellis_admission.identity_resume(CAST(:old AS uuid),CAST(:new AS uuid),:e,:deadline)',
            old=op,new=next_op,e=session.epoch+1,deadline=datetime.now(timezone.utc)+timedelta(seconds=300))
        with patch.object(operator,'DATABASE','ct_signup_integration'):
            self.assertEqual({'state':'superseded','recorded':True},operator.inspect(engine,op,hashlib.sha256(raw).hexdigest(),proof['verified_at'],proof['expires_at']))
        self.assertEqual(session.epoch+2,self.query('SELECT epoch FROM careertrellis_admission.identity_accounts WHERE id=:uid',uid=session.local_id))
        self.assertTrue(self.query('SELECT held FROM careertrellis_admission.identity_accounts WHERE id=:uid',uid=session.local_id))

    async def test_operator_handoff_real_commit_loss_activation_and_cleanup(self):
        from contextlib import ExitStack
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        import admission_operations as ops
        import identity_operator as operator
        import identity_evidence as evidence
        from test_identity_evidence import packet
        cookie=await self.activate(); session=self.codec.open(cookie); op=self.prepare(session)
        context=self.operator('SELECT careertrellis_admission.identity_evidence_context(CAST(:op AS uuid))',op=op)
        raw=ops.encoded(packet(context)); engine=self.fixture.engine('careertrellis_migration')
        real_submit=evidence.submit
        class LostAcknowledgement(RuntimeError): pass
        def lost(*args): real_submit(*args); raise LostAcknowledgement('synthetic lost acknowledgement')
        def write(path,value,previous=None,**kw):
            if previous is None: self.assertFalse(path.exists())
            else: self.assertEqual(previous,path.read_bytes())
            path.write_bytes(value)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path=Path(directory)/'input.json'; receipt=Path(directory)/'journal.json'; path.write_bytes(raw)
            stack.enter_context(patch.object(operator,'DATABASE','ct_signup_integration'))
            # Native DB semantics are under test, not this machine's root-owned
            # /etc selection barrier. An unrelated OperationError is also a
            # RuntimeError and previously masqueraded as our lost acknowledgement.
            stack.enter_context(patch.object(operator.barrier,'inspect',return_value=None))
            stack.enter_context(patch.object(operator,'locations',return_value=(path,receipt)))
            stack.enter_context(patch.object(ops,'read_protected',side_effect=lambda p,**kw:p.read_bytes()))
            stack.enter_context(patch.object(ops,'write_protected',side_effect=write))
            stack.enter_context(patch.object(operator,'remove_exact',side_effect=lambda p,*args:p.unlink()))
            with patch.object(evidence,'submit',side_effect=lost) as submitted,self.assertRaises(LostAcknowledgement):
                operator.process('attest',op,'c'*40,engine)
            submitted.assert_called_once()
            self.assertEqual('verified',self.query('SELECT state FROM careertrellis_admission.identity_transitions WHERE operation=CAST(:op AS uuid)',op=op))
            self.assertTrue(path.exists())
            self.operator('SELECT careertrellis_admission.identity_activate(CAST(:op AS uuid),:e)',op=op,e=session.epoch+1)
            with patch.object(evidence,'submit',side_effect=AssertionError('reconcile must not submit')):
                self.assertEqual({'outcome':'recorded_input_removed'},operator.process('reconcile',op,'c'*40,engine))
            self.assertFalse(path.exists())
            self.assertEqual({'state':'committed','recorded':True},operator.process('status',op,'c'*40,engine))

    async def test_guided_entry_rechecks_current_account_before_collecting_evidence(self):
        import identity_entry as entry
        import identity_operator as operator
        from unittest.mock import patch
        cookie=await self.activate(); session=self.codec.open(cookie); op=self.prepare(session)
        engine=self.fixture.engine('careertrellis_migration')
        with patch.object(operator,'DATABASE','ct_signup_integration'):
            with operator.secure_connection(engine) as connection:
                operator.setup(connection)
                self.assertEqual(session.epoch+1,entry.context(connection,op)['epoch'])
            self.query('UPDATE careertrellis.app_users SET disabled_at=clock_timestamp() WHERE id=:uid',uid=session.local_id)
            with self.assertRaises(Exception),operator.secure_connection(engine) as connection:
                operator.setup(connection); entry.context(connection,op)

    def configuration_fence(self):
        self.query('UPDATE careertrellis_admission.policy SET maintenance=true,signup_open=false')
        for role in ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker'):
            self.query('ALTER ROLE '+role+' NOLOGIN')

    def configuration_unfence_fixture(self):
        for role in ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker'):
            self.query('ALTER ROLE '+role+' LOGIN')

    async def test_owner_only_switch_requires_disabled_pilots_and_exact_quarantine(self):
        import admission_operations as ops
        import identity_configuration as configuration
        import identity_operator as operator
        import session_v2_transition as transition
        from pilot_users import perform_operation
        from test_identity_configuration import plan
        from unittest.mock import patch
        engine=self.fixture.engine('careertrellis_migration')
        self.addCleanup(engine.dispose)
        self.query('UPDATE careertrellis_admission.policy SET cap=25,signup_open=false')
        subjects=('user_synthetic_legacy_one','user_synthetic_legacy_two')
        for subject in subjects:
            self.assertEqual('created',perform_operation(engine,'seed','user_synthetic_owner',subject).result)
        pilots=self.query("SELECT jsonb_agg(to_jsonb(a) ORDER BY id) FROM careertrellis_admission.identity_accounts a WHERE role='user'")
        before_profiles=self.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY owner_user_id) FROM careertrellis.profiles p')
        generation=self.query('SELECT generation FROM careertrellis_admission.policy')
        self.assertEqual('disabled',perform_operation(engine,'disable','user_synthetic_owner',subjects[0]).result)
        # The 0015 sync trigger and existing helper each invalidate flows.
        self.assertEqual(generation+2,self.query('SELECT generation FROM careertrellis_admission.policy'))
        self.assertEqual('already_disabled',perform_operation(engine,'disable','user_synthetic_owner',subjects[0]).result)
        self.assertEqual(generation+2,self.query('SELECT generation FROM careertrellis_admission.policy'))
        owner=self.query("SELECT row_to_json(a) FROM careertrellis_admission.identity_accounts a WHERE role='owner'")
        target='e'*64; owner_op=str(uuid4())
        self.operator('SELECT careertrellis_admission.identity_prepare(CAST(:op AS uuid),:uid,:b,:s,:e,CAST(:i AS uuid),:tb,:ts,:deadline,true)',
            op=owner_op,uid=owner['id'],b=owner['binding'],s=owner['subject'],e=owner['epoch'],i=owner['incarnation'],
            tb=target,ts='user_synthetic_production_owner',deadline=datetime.now(timezone.utc)+timedelta(seconds=300))
        self.operator("SELECT careertrellis_admission.identity_attest(CAST(:op AS uuid),:e,repeat('a',64),clock_timestamp(),clock_timestamp()+interval '120 seconds',true)",op=owner_op,e=owner['epoch']+1)
        p=plan(SETTINGS.binding)
        p.update(mode='switch_environment',target_binding=target,transitions=[owner_op],
            quarantined_accounts=[pilots[0]['id']],expected_generation=self.query('SELECT generation FROM careertrellis_admission.policy'))
        p['owner_proof']=transition.owner_proof(p['operation'],target,'user_synthetic_production_owner')
        with patch.object(operator,'DATABASE','ct_signup_integration'):
            self.configuration_fence()
            try:
                # The second pilot is active: owner-only coverage is incomplete.
                with self.assertRaises(ops.OperationError):
                    configuration.apply(ops.encoded(p),engine,verified_current_binding=SETTINGS.binding)
                self.assertEqual(SETTINGS.binding,self.query('SELECT binding FROM careertrellis_admission.identity_provider'))
                self.assertEqual('verified',self.query('SELECT state FROM careertrellis_admission.identity_transitions WHERE operation=CAST(:op AS uuid)',op=owner_op))
            finally: self.configuration_unfence_fixture()
            # Only this disposable fixture is reopened to complete its planned disable.
            self.query('UPDATE careertrellis_admission.policy SET maintenance=false')
            self.assertEqual('disabled',perform_operation(engine,'disable','user_synthetic_owner',subjects[1]).result)
            p['expected_generation']=self.query('SELECT generation FROM careertrellis_admission.policy')
            self.configuration_fence()
            try:
                with self.assertRaises(ops.OperationError):
                    configuration.apply(ops.encoded(p),engine,verified_current_binding=SETTINGS.binding)
                p['quarantined_accounts']=[row['id'] for row in pilots]
                self.assertEqual({'outcome':'configuration_committed_maintenance_retained'},
                    configuration.apply(ops.encoded(p),engine,verified_current_binding=SETTINGS.binding))
                self.assertEqual(p['expected_generation']+3,self.query('SELECT generation FROM careertrellis_admission.policy'))
                for original in pilots:
                    row=self.query('SELECT row_to_json(a) FROM careertrellis_admission.identity_accounts a WHERE id=:uid',uid=original['id'])
                    self.assertEqual({**original,'disabled':True,'epoch':original['epoch']+1},row)
                    self.assertEqual(original['subject'],self.query('SELECT workos_user_id FROM careertrellis.app_users WHERE id=:uid',uid=original['id']))
                    self.assertEqual(1,self.query("SELECT count(*) FROM careertrellis_admission.identity_history WHERE account_id=:uid AND binding=:b AND subject=:s AND state='active'",uid=original['id'],b=original['binding'],s=original['subject']))
                self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.identity_history WHERE binding=:b AND account_id<>:uid',b=target,uid=owner['id']))
                self.assertEqual(before_profiles,self.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY owner_user_id) FROM careertrellis.profiles p'))
                self.assertEqual(3,self.query('SELECT count(*) FROM careertrellis.app_users'))
                self.assertEqual('user_synthetic_production_owner',self.query('SELECT workos_user_id FROM careertrellis.app_users WHERE id=:uid',uid=owner['id']))
            finally: self.configuration_unfence_fixture()

    async def test_configuration_switch_is_atomic_and_exact_retry_is_historical(self):
        import admission_operations as ops
        import identity_configuration as configuration
        import identity_operator as operator
        import session_v2_transition as transition
        from test_identity_configuration import plan
        from unittest.mock import patch
        cookie=await self.activate(); session=self.codec.open(cookie)
        owner=self.query("SELECT row_to_json(a) FROM careertrellis_admission.identity_accounts a WHERE role='owner'")
        p=plan(SETTINGS.binding); target='d'*64
        p.update(mode='switch_environment',target_binding=target)
        ops_list=[]
        user_op=self.prepare(session,target=(target,'user_target_member')); ops_list.append(user_op)
        owner_op=str(uuid4()); ops_list.append(owner_op)
        self.operator('SELECT careertrellis_admission.identity_prepare(CAST(:op AS uuid),:uid,:b,:s,:e,CAST(:i AS uuid),:tb,:ts,:deadline,true)',
            op=owner_op,uid=owner['id'],b=owner['binding'],s=owner['subject'],e=owner['epoch'],i=owner['incarnation'],
            tb=target,ts='user_target_owner',deadline=datetime.now(timezone.utc)+timedelta(seconds=300))
        for op,e in ((user_op,session.epoch+1),(owner_op,owner['epoch']+1)):
            self.operator("SELECT careertrellis_admission.identity_attest(CAST(:op AS uuid),:e,repeat('a',64),clock_timestamp(),clock_timestamp()+interval '120 seconds',true)",op=op,e=e)
        p['transitions']=sorted(ops_list)
        excluded=self.query("INSERT INTO careertrellis.app_users(workos_user_id,role,disabled_at) VALUES('user_synthetic_quarantined','user',clock_timestamp()) RETURNING id")
        p['quarantined_accounts']=[excluded]
        p['expected_generation']=self.query('SELECT generation FROM careertrellis_admission.policy')
        p['owner_proof']=transition.owner_proof(p['operation'],target,'user_target_owner')
        before=self.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY owner_user_id) FROM careertrellis.profiles p')
        self.configuration_fence()
        try:
            with patch.object(operator,'DATABASE','ct_signup_integration'):
                engine=self.fixture.engine('careertrellis_migration')
                incomplete={**p,'transitions':[user_op]}
                with self.assertRaises(ops.OperationError): configuration.apply(ops.encoded(incomplete),engine,verified_current_binding=SETTINGS.binding)
                with self.assertRaises(ops.OperationError): configuration.apply(ops.encoded({**p,'quarantined_accounts':[]}),engine,verified_current_binding=SETTINGS.binding)
                self.assertEqual(SETTINGS.binding,self.query('SELECT binding FROM careertrellis_admission.identity_provider'))
                # Force the final receipt write to fail after all mapping changes;
                # the transaction must restore every original mapping and hold.
                self.query("CREATE FUNCTION careertrellis_admission.synthetic_receipt_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic failure'; END $$; "
                    "CREATE TRIGGER synthetic_failure BEFORE INSERT ON careertrellis_admission.identity_configuration_receipts FOR EACH ROW EXECUTE FUNCTION careertrellis_admission.synthetic_receipt_failure()")
                with self.assertRaises(ops.OperationError): configuration.apply(ops.encoded(p),engine,verified_current_binding=SETTINGS.binding)
                self.assertEqual(SETTINGS.binding,self.query('SELECT binding FROM careertrellis_admission.identity_provider'))
                self.assertTrue(self.query('SELECT bool_and(held) FROM careertrellis_admission.identity_accounts WHERE NOT disabled'))
                self.query('DROP TRIGGER synthetic_failure ON careertrellis_admission.identity_configuration_receipts; DROP FUNCTION careertrellis_admission.synthetic_receipt_failure()')
                # Rechecking the exact schema must retain logging safeguards
                # after setup has switched to the restricted admission role.
                self.assertEqual({'outcome':'configuration_committed_maintenance_retained'},configuration.apply(ops.encoded(p),engine,verified_current_binding=SETTINGS.binding,expected_schema=self.schema_revision))
                generation=self.query('SELECT generation FROM careertrellis_admission.policy')
                # Provider configuration + activation + changed-subject trigger
                # for each of the two mapped accounts; quarantine adds none.
                self.assertEqual(p['expected_generation']+1+2*len(ops_list),generation)
                import identity_web_handoff as handoff
                activation={'operation':p['operation'],'provider_binding':target,'owner_proof':p['owner_proof'],'cap':25,'schema':self.schema_revision}
                with handoff.committed_guard(p,activation,engine): pass
                with self.assertRaises(ops.OperationError),handoff.committed_guard({**p,'application':'f'*40},activation,engine): pass
                self.query('UPDATE careertrellis_admission.policy SET generation=generation+1')
                with self.assertRaises(ops.OperationError),handoff.committed_guard(p,activation,engine): pass
                # Undo only the synthetic injected policy drift for subsequent
                # historical coordinator checks; no production recovery does this.
                self.query('UPDATE careertrellis_admission.policy SET generation=:g',g=generation)
                self.assertEqual({'outcome':'historical_configuration_committed'},configuration.apply(ops.encoded(p),engine,verified_current_binding=target))
                self.assertEqual(generation,self.query('SELECT generation FROM careertrellis_admission.policy'))
                with self.assertRaises(ops.OperationError): configuration.apply(ops.encoded({**p,'application':'e'*40}),engine,verified_current_binding=target)
            self.assertEqual(target,self.query('SELECT binding FROM careertrellis_admission.identity_provider'))
            self.assertTrue(self.query('SELECT disabled AND binding=:old FROM careertrellis_admission.identity_accounts WHERE id=:uid',old=SETTINGS.binding,uid=excluded))
            self.assertEqual(before,self.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY owner_user_id) FROM careertrellis.profiles p'))
            self.assertTrue(self.query('SELECT maintenance AND NOT signup_open AND minimum_session_version=2 FROM careertrellis_admission.policy'))
            # Account erasure removes scoped identity history, but must not make
            # the retired provider environment available for reuse.
            self.query('DELETE FROM careertrellis.app_users')
            self.query("INSERT INTO careertrellis.app_users(workos_user_id,role) VALUES('user_new_owner','owner')")
            owner=self.query("SELECT row_to_json(a) FROM careertrellis_admission.identity_accounts a WHERE role='owner'")
            back_op=str(uuid4())
            self.operator('SELECT careertrellis_admission.identity_prepare(CAST(:op AS uuid),:uid,:b,:s,:e,CAST(:i AS uuid),:tb,:ts,:deadline,true)',
                op=back_op,uid=owner['id'],b=target,s=owner['subject'],e=owner['epoch'],i=owner['incarnation'],
                tb=SETTINGS.binding,ts='user_old_environment_new_subject',deadline=datetime.now(timezone.utc)+timedelta(seconds=300))
            self.operator("SELECT careertrellis_admission.identity_attest(CAST(:op AS uuid),:e,repeat('b',64),clock_timestamp(),clock_timestamp()+interval '120 seconds',true)",op=back_op,e=owner['epoch']+1)
            back=plan(target); back.update(mode='switch_environment',target_binding=SETTINGS.binding,transitions=[back_op],
                expected_generation=self.query('SELECT generation FROM careertrellis_admission.policy'))
            back['owner_proof']=transition.owner_proof(back['operation'],SETTINGS.binding,'user_old_environment_new_subject')
            with patch.object(operator,'DATABASE','ct_signup_integration'),self.assertRaises(ops.OperationError):
                configuration.apply(ops.encoded(back),engine,verified_current_binding=target)
            self.assertEqual(target,self.query('SELECT binding FROM careertrellis_admission.identity_provider'))
        finally: self.configuration_unfence_fixture()

    async def test_configuration_current_binding_requires_verified_baseline_and_empty_history(self):
        import admission_operations as ops
        import identity_configuration as configuration
        import identity_operator as operator
        from test_identity_configuration import plan
        from unittest.mock import patch
        # Synthetic pre-binding state only. No production reset path exists.
        self.query('DELETE FROM careertrellis_admission.identity_history; DELETE FROM careertrellis_admission.identity_provider_history; UPDATE careertrellis_admission.identity_accounts SET binding=NULL; UPDATE careertrellis_admission.identity_provider SET binding=NULL')
        p=plan(SETTINGS.binding); p['expected_generation']=self.query('SELECT generation FROM careertrellis_admission.policy')
        self.configuration_fence()
        try:
            with patch.object(operator,'DATABASE','ct_signup_integration'):
                engine=self.fixture.engine('careertrellis_migration')
                with self.assertRaises(ops.OperationError): configuration.apply(ops.encoded(p),engine,verified_current_binding='f'*64)
                self.assertIsNone(self.query('SELECT binding FROM careertrellis_admission.identity_provider'))
                self.assertEqual({'outcome':'configuration_committed_maintenance_retained'},configuration.apply(ops.encoded(p),engine,verified_current_binding=SETTINGS.binding))
                self.assertEqual(SETTINGS.binding,self.query('SELECT binding FROM careertrellis_admission.identity_provider'))
                p['operation']=str(uuid4())
                with self.assertRaises(ops.OperationError): configuration.apply(ops.encoded(p),engine,verified_current_binding=SETTINGS.binding)
        finally: self.configuration_unfence_fixture()

    async def test_runtime_rls_missing_stale_and_cross_account_context_denied(self):
        cookie = await self.activate()
        authorization = (await self.adapter.authenticate(cookie)).authorization
        runtime = self.fixture.engine('careertrellis_runtime')
        with tenant_transaction(runtime,authorization.local_id) as c:
            self.assertEqual(0,c.scalar(text('SELECT count(*) FROM careertrellis.profiles')))
        token = current.set(authorization)
        try:
            with tenant_transaction(runtime,authorization.local_id) as c:
                self.assertEqual(1,c.scalar(text('SELECT count(*) FROM careertrellis.profiles')))
            with self.assertRaises(ValueError),tenant_transaction(runtime,authorization.local_id+1): pass
            self.prepare(self.codec.open(cookie))
            with self.assertRaises(PermissionError),tenant_transaction(runtime,authorization.local_id): pass
        finally: current.reset(token)

    async def test_protected_effect_serializes_with_containment(self):
        cookie = await self.activate(); session = self.codec.open(cookie)
        token = current.set((await self.adapter.authenticate(cookie)).authorization)
        try:
            with tenant_transaction(self.fixture.engine('careertrellis_runtime'),session.local_id) as c:
                c.execute(text("UPDATE careertrellis.profiles SET name='synthetic-committed-before-hold'"))
                with self.assertRaises(DBAPIError): self.prepare(session)
            self.prepare(session)
            with self.assertRaises(PermissionError),tenant_transaction(self.fixture.engine('careertrellis_runtime'),session.local_id): pass
        finally: current.reset(token)

    async def test_refresh_racing_hold_does_not_upgrade_epoch(self):
        cookie = await self.activate(); session = self.codec.open(cookie)
        data = self.codec.provider_open(session.provider)
        from workos_transport_candidate import VerifiedTokens
        expired = VerifiedTokens(SUBJECT,session.session_id,session.generation,self.http.token(exp=int(time.time())-1),
            data['refresh_token'],int(time.time())-1,session.provider_binding)
        cookie = self.codec.pack(replace(session,provider=self.codec.provider_seal(expired)))
        async def hold(request):
            if request.method=='POST': self.prepare(session)
        self.http.hook=hold
        result = await self.adapter.authenticate(cookie,refresh=True)
        self.assertEqual('unauthenticated',result.status); self.assertFalse(result.cookie)

    async def test_operator_prepare_resume_and_uncommitted_plan_supersession(self):
        import hashlib
        from unittest.mock import patch
        import admission_operations as ops
        import identity_configuration as configuration
        import identity_operator as operator
        import identity_transition_operator as requests
        import identity_plan_supersession as supersession
        import identity_plan_packet as packets
        import session_v2_transition as transition
        from test_identity_configuration import plan
        cookie=await self.activate(); session=self.codec.open(cookie)
        before=self.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY owner_user_id) FROM careertrellis.profiles p')
        # Dedicated fixture only: reconstruct a pre-binding 0015 ledger, then
        # obtain the real B receipt from the coordinator, never fabricate it.
        self.query('DELETE FROM careertrellis_admission.identity_history')
        self.query('DELETE FROM careertrellis_admission.identity_provider_history')
        self.query('UPDATE careertrellis_admission.identity_accounts SET binding=NULL')
        self.query('UPDATE careertrellis_admission.identity_provider SET binding=NULL')
        self.query('UPDATE careertrellis_admission.policy SET minimum_session_version=1')
        self.configuration_fence()
        try:
            b=plan(SETTINGS.binding); b['expected_generation']=self.query('SELECT generation FROM careertrellis_admission.policy')
            target='e'*64
            p={**b,'operation':str(uuid4()),'mode':'switch_environment','target_binding':target,
                'expected_generation':b['expected_generation']+3,'transitions':sorted([str(uuid4()),str(uuid4())])}
            p['owner_proof']=transition.owner_proof(p['operation'],target,'user_resumed_owner')
            def paired(provider):
                return {'provider':provider,'activation':{'operation':provider['operation'],'application':provider['application'],
                    'operations':provider['operations'],'owner_proof':provider['owner_proof'],'provider_binding':provider['target_binding'],
                    'realm':'synthetic','schema':transition.HEAD,'cap':25,'ttl':600,'session_seconds':3600}}
            original={'version':1,'bind':paired(b),'switch':paired(p)}
            packets.validate(ops.encoded(original))
            engine=self.fixture.engine('careertrellis_migration')
            # Previous fixture cases may leave idle pooled restricted callers;
            # the real operator uses NullPool and rejects even idle survivors.
            for pooled in self.fixture.engines.values(): pooled.dispose()
            with patch.object(operator,'DATABASE','ct_signup_integration'):
                rejected=[]; require=ops.require
                def trace_gate(value):
                    if not value:
                        import traceback
                        rejected.extend(f.lineno for f in traceback.extract_stack() if f.filename.endswith('identity_configuration.py'))
                    require(value)
                try:
                    with patch.object(ops,'require',side_effect=trace_gate):
                        configuration.apply(ops.encoded(b),engine,verified_current_binding=SETTINGS.binding)
                except ops.OperationError:
                    self.fail('Synthetic initial binding rejected gate lines '+str(rejected))
                rows=self.query('SELECT jsonb_agg(to_jsonb(a) ORDER BY id) FROM careertrellis_admission.identity_accounts a')
                saved=[]
                for a,operation in zip(rows,p['transitions']):
                    r={'version':1,'action':'prepare','selection':'synthetic','operation':operation,'previous':None,
                        'account_id':a['id'],'incarnation':a['incarnation'],'epoch':a['epoch'],
                        'old_binding':a['binding'],'old_subject':a['subject'],'target_binding':target,
                        'target_subject':'user_resumed_owner' if a['role']=='owner' else 'user_resumed_member',
                        'deadline':(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat(),'owner_authorized':a['role']=='owner'}
                    requests.validate(ops.encoded(r)); requests.scope(r,b'synthetic',original['switch'])
                    with operator.secure_connection(engine) as c:
                        requests.guard(c,p,original['switch']['activation'])
                        self.assertEqual('held_evidence_required',requests.mutate(c,r,p)['outcome'])
                        self.assertEqual('historical_request_recorded',requests.mutate(c,r,p)['outcome'])
                    saved.append(r)
                await asyncio.sleep(1.1)
                successors=[]
                for r in saved:
                    successor={**r,'action':'resume','previous':r['operation'],'operation':str(uuid4()),'epoch':r['epoch']+1,
                        'deadline':(datetime.now(timezone.utc)+timedelta(seconds=300)).isoformat()}
                    requests.validate(ops.encoded(successor)); requests.scope(successor,b'synthetic',original['switch'])
                    with operator.secure_connection(engine) as c:
                        requests.guard(c,p,original['switch']['activation'])
                        self.assertEqual('held_supersession_required',requests.mutate(c,successor,p)['outcome'])
                    successors.append(successor)
                with operator.secure_connection(engine) as c:
                    operator.setup(c)
                    revised=supersession.replacement(c,original)
                revised['predecessor']=hashlib.sha256(ops.encoded(original)).hexdigest()
                packets.validate(ops.encoded(revised)); packets.successor(original,revised)
                self.assertEqual(p['operation'],revised['switch']['provider']['operation'])
                self.assertEqual(p['expected_generation']+2,revised['switch']['provider']['expected_generation'])
                self.assertEqual(sorted(r['operation'] for r in successors),revised['switch']['provider']['transitions'])
                for r in successors:
                    self.operator("SELECT careertrellis_admission.identity_attest(CAST(:op AS uuid),:e,repeat('a',64),clock_timestamp(),clock_timestamp()+interval '120 seconds',:owner)",
                        op=r['operation'],e=r['epoch']+1,owner=r['owner_authorized'])
                configuration.apply(ops.encoded(revised['switch']['provider']),engine,verified_current_binding=SETTINGS.binding)
                with self.assertRaises(ops.OperationError),operator.secure_connection(engine) as c:
                    operator.setup(c); supersession.replacement(c,revised)
            self.assertEqual(before,self.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY owner_user_id) FROM careertrellis.profiles p'))
            self.assertTrue(self.query('SELECT maintenance AND NOT signup_open AND minimum_session_version=1 FROM careertrellis_admission.policy'))
        finally: self.configuration_unfence_fixture()

    async def evidence_batch_rehearsal(self, count, *, expire_one=False):
        import json
        from unittest.mock import patch
        import admission_operations as ops
        import identity_configuration as configuration
        import identity_operator as operator
        import identity_entry as entry
        import identity_evidence as evidence
        import session_v2_transition as transition
        from test_identity_configuration import plan
        await self.activate()
        self.query('UPDATE careertrellis_admission.policy SET cap=25')
        for number in range(count-2):
            uid=self.query("INSERT INTO careertrellis.app_users(workos_user_id,role) VALUES(:s,'user') RETURNING id",s='user_synthetic_batch_'+str(number))
            self.query("INSERT INTO careertrellis.profiles(owner_user_id,name) VALUES(:uid,'Synthetic batch preservation')",uid=uid)
        accounts=self.query('SELECT jsonb_agg(to_jsonb(a) ORDER BY id) FROM careertrellis_admission.identity_accounts a')
        self.assertEqual(count,len(accounts))
        profiles=self.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY owner_user_id) FROM careertrellis.profiles p')
        import hashlib
        target=hashlib.sha256(uuid4().bytes).hexdigest(); p=plan(SETTINGS.binding)
        p.update(mode='switch_environment',target_binding=target)
        p['owner_proof']=transition.owner_proof(p['operation'],target,'user_synthetic_batch_owner')
        packets=[]; started=time.monotonic()
        for account in accounts:
            operation=str(uuid4())
            self.operator('SELECT careertrellis_admission.identity_prepare(CAST(:op AS uuid),:uid,:b,:s,:e,CAST(:i AS uuid),:tb,:ts,:deadline,:owner)',
                op=operation,uid=account['id'],b=account['binding'],s=account['subject'],e=account['epoch'],i=account['incarnation'],
                tb=target,ts='user_synthetic_batch_owner' if account['role']=='owner' else 'user_synthetic_batch_target_'+str(account['id']),
                deadline=datetime.now(timezone.utc)+timedelta(minutes=55),owner=account['role']=='owner')
            context=self.operator('SELECT careertrellis_admission.identity_evidence_context(CAST(:op AS uuid))',op=operation)
            completed=datetime.now(timezone.utc).isoformat()
            answers=['VISIBLE']+[str(context[k]) for k in ('account_id','incarnation','epoch','old_binding','old_subject','target_binding','target_subject')]
            answers+=['VERIFIED',completed,'AUTHENTICATED',completed,'google','EMAIL-VERIFIED','none_enrolled','REVOKED',completed]
            if account['role']=='owner': answers+=['OWNER-AUTHORIZED',completed]
            answers+=['SAVE']
            raw=entry.build(context,lambda _:answers.pop(0))
            self.assertFalse(answers)
            proof=evidence.validate(raw)
            self.assertEqual(300,(evidence.instant(proof['expires_at'])-evidence.instant(proof['verified_at'])).total_seconds())
            packets.append(proof)
        p['transitions']=sorted(proof['context']['operation'] for proof in packets)
        p['expected_generation']=self.query('SELECT generation FROM careertrellis_admission.policy')
        generation=p['expected_generation']
        self.configuration_fence()
        try:
            for pooled in self.fixture.engines.values(): pooled.dispose()
            engine=self.fixture.engine('careertrellis_migration')
            for proof in packets: evidence.submit(ops.encoded(proof),engine)
            margin=(min(evidence.instant(proof['expires_at']) for proof in packets)-datetime.now(timezone.utc)).total_seconds()
            self.assertGreater(margin,0)
            if expire_one:
                # Age only one synthetic evidence row; do not wait five real
                # minutes or alter any deadline/clock/production state.
                self.query("UPDATE careertrellis_admission.identity_transitions SET evidence_expires=clock_timestamp()-interval '1 second' WHERE operation=CAST(:op AS uuid)",op=packets[-1]['context']['operation'])
            with patch.object(operator,'DATABASE','ct_signup_integration'):
                if expire_one:
                    with self.assertRaises(ops.OperationError): configuration.apply(ops.encoded(p),engine,verified_current_binding=SETTINGS.binding)
                    self.assertEqual(SETTINGS.binding,self.query('SELECT binding FROM careertrellis_admission.identity_provider'))
                    self.assertEqual(generation,self.query('SELECT generation FROM careertrellis_admission.policy'))
                    self.assertTrue(self.query('SELECT bool_and(held) FROM careertrellis_admission.identity_accounts'))
                    self.assertFalse(self.query('SELECT EXISTS(SELECT 1 FROM careertrellis_admission.identity_configuration_receipts WHERE operation=CAST(:op AS uuid))',op=p['operation']))
                else:
                    configuration.apply(ops.encoded(p),engine,verified_current_binding=SETTINGS.binding)
                    self.assertEqual(target,self.query('SELECT binding FROM careertrellis_admission.identity_provider'))
                    self.assertEqual(generation+1+2*count,self.query('SELECT generation FROM careertrellis_admission.policy'))
            self.assertEqual(profiles,self.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY owner_user_id) FROM careertrellis.profiles p'))
            self.assertTrue(self.query('SELECT maintenance AND NOT signup_open FROM careertrellis_admission.policy'))
            print(json.dumps({'synthetic_evidence_batch':count,'expired_member_injected':expire_one,
                'elapsed_seconds':round(time.monotonic()-started,3),'minimum_evidence_seconds_before_apply':round(margin,3)}))
        finally: self.configuration_unfence_fixture()

    async def test_two_account_five_minute_evidence_overlap(self):
        await self.evidence_batch_rehearsal(2)

    async def test_full_beta_cap_five_minute_evidence_overlap(self):
        await self.evidence_batch_rehearsal(25)

    async def test_one_expired_member_rejects_entire_beta_cap_mapping(self):
        await self.evidence_batch_rehearsal(25,expire_one=True)

    async def test_evidence_expiry_before_transition_deadline_retains_hold(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        op=self.prepare(session,seconds=60)
        before=self.query('SELECT row_to_json(p) FROM careertrellis.profiles p WHERE owner_user_id=:uid',uid=session.local_id)
        self.operator("SELECT careertrellis_admission.identity_attest(CAST(:op AS uuid),:e,repeat('a',64),clock_timestamp(),clock_timestamp()+interval '1 second')",op=op,e=session.epoch+1)
        generation=self.query('SELECT generation FROM careertrellis_admission.policy')
        await asyncio.sleep(1.1)
        self.assertTrue(self.query('SELECT evidence_expires<clock_timestamp() AND deadline>clock_timestamp() FROM careertrellis_admission.identity_transitions WHERE operation=CAST(:op AS uuid)',op=op))
        with self.assertRaises(DBAPIError):
            self.operator('SELECT careertrellis_admission.identity_activate(CAST(:op AS uuid),:e)',op=op,e=session.epoch+1)
        with self.assertRaises(DBAPIError):
            self.operator("SELECT careertrellis_admission.identity_attest(CAST(:op AS uuid),:e,repeat('b',64),clock_timestamp(),clock_timestamp()+interval '30 seconds')",op=op,e=session.epoch+1)
        with self.assertRaises(DBAPIError):
            self.operator('SELECT careertrellis_admission.identity_resume(CAST(:old AS uuid),CAST(:new AS uuid),:e,:deadline)',
                old=op,new=str(uuid4()),e=session.epoch+1,deadline=datetime.now(timezone.utc)+timedelta(seconds=60))
        self.assertEqual(generation,self.query('SELECT generation FROM careertrellis_admission.policy'))
        self.assertTrue(self.query('SELECT held AND epoch=:e FROM careertrellis_admission.identity_accounts WHERE id=:uid',e=session.epoch+1,uid=session.local_id))
        self.assertEqual(before,self.query('SELECT row_to_json(p) FROM careertrellis.profiles p WHERE owner_user_id=:uid',uid=session.local_id))
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)

    async def test_expired_transition_resumes_forward_with_new_evidence(self):
        from admission_operations import encoded
        from identity_evidence import EvidenceRejected, submit
        from test_identity_evidence import packet
        cookie = await self.activate(); session = self.codec.open(cookie)
        op = self.prepare(session,seconds=.3)
        context = self.operator('SELECT careertrellis_admission.identity_evidence_context(CAST(:op AS uuid))',op=op)
        proof = packet(context); proof['expires_at'] = context['deadline']
        old_raw = encoded(proof)
        await asyncio.sleep(.35)
        engine = self.fixture.engine('careertrellis_migration')
        with self.assertRaises(EvidenceRejected): submit(old_raw,engine)
        next_op = str(uuid4())
        deadline = datetime.now(timezone.utc)+timedelta(seconds=300)
        sql='SELECT careertrellis_admission.identity_resume(CAST(:old AS uuid),CAST(:new AS uuid),:e,:deadline)'
        args={'old':op,'new':next_op,'e':session.epoch+1,'deadline':deadline}
        self.assertEqual('held',self.operator(sql,**args))
        self.assertEqual('held',self.operator(sql,**args))
        self.assertEqual(session.epoch+2,self.query('SELECT epoch FROM careertrellis_admission.identity_accounts WHERE id=:uid',uid=session.local_id))
        with self.assertRaises(DBAPIError): self.attest_activate(op,session.epoch+1)
        with self.assertRaises(DBAPIError):
            self.operator('SELECT careertrellis_admission.identity_activate(CAST(:op AS uuid),:e)',op=next_op,e=session.epoch+2)
        with self.assertRaises(EvidenceRejected): submit(old_raw,engine)
        context = self.operator('SELECT careertrellis_admission.identity_evidence_context(CAST(:op AS uuid))',op=next_op)
        submit(encoded(packet(context)),engine)
        self.operator('SELECT careertrellis_admission.identity_activate(CAST(:op AS uuid),:e)',op=next_op,e=session.epoch+2)
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)

    async def test_scoped_identity_transition_preserves_local_data_and_retires_old(self):
        from workos_transport_candidate import API
        cookie=await self.activate(); session=self.codec.open(cookie)
        new_settings=replace(SETTINGS,client_id='client_synthetic_production',issuer=API+'/user_management/client_synthetic_production')
        target=(new_settings.binding,'user_synthetic_production')
        op=self.prepare(session,target=target)
        self.attest_activate(op,session.epoch+1)
        self.assertEqual('retired',self.query('SELECT state FROM careertrellis_admission.identity_history WHERE binding=:b AND subject=:s',b=session.provider_binding,s=session.subject))
        self.assertEqual(session.local_id,self.query('SELECT id FROM careertrellis.app_users WHERE workos_user_id=:s',s=target[1]))
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis.profiles WHERE owner_user_id=:uid',uid=session.local_id))
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)
        self.query('UPDATE careertrellis_admission.policy SET maintenance=true,signup_open=false')
        self.operator('SELECT careertrellis_admission.configure_identity_provider(:old,:target)',old=SETTINGS.binding,target=target[0])
        self.query('UPDATE careertrellis_admission.policy SET maintenance=false')
        self.provider.settings=new_settings
        self.codec.provider_binding=target[0]; self.store.provider_binding=target[0]
        self.http.user={'id':target[1],'email_verified':True}
        self.http.claims={'iss':new_settings.issuer,'sub':target[1]}
        self.provider._fetched=None
        flow,state=await self.flow('login')
        fresh=await self.adapter.complete(flow,self.browser,state,'synthetic-production-code')
        self.assertEqual('login',fresh.status)
        authorization=(await self.adapter.authenticate(fresh.cookie)).authorization
        self.assertEqual(session.local_id,authorization.local_id)
        token=current.set(authorization)
        try:
            with tenant_transaction(self.fixture.engine('careertrellis_runtime'),authorization.local_id) as c:
                self.assertEqual(1,c.scalar(text('SELECT count(*) FROM careertrellis.profiles')))
        finally: current.reset(token)
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)

    async def test_private_ledger_and_mutators_denied_after_grant_reapplication(self):
        self.fixture.grants()
        for role in ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker'):
            with self.assertRaises(DBAPIError),self.fixture.engine(role).begin() as c:
                c.execute(text('SELECT * FROM careertrellis_admission.identity_accounts'))
            with self.assertRaises(DBAPIError),self.fixture.engine(role).begin() as c:
                c.execute(text('SELECT careertrellis_admission.identity_activate(gen_random_uuid(),1)'))

    async def test_identity_conflict_is_atomic_and_error_omits_identity(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        with self.assertRaises(DBAPIError) as raised:
            self.prepare(session,target=(SETTINGS.binding,'user_synthetic_owner'))
        self.assertNotIn('user_synthetic_owner',str(raised.exception))
        self.assertEqual('authenticated',(await self.adapter.authenticate(cookie)).status)
        self.assertEqual(session.epoch,self.query('SELECT epoch FROM careertrellis_admission.identity_accounts WHERE id=:uid',uid=session.local_id))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.identity_transitions'))

    async def test_disabled_reenabled_and_recreated_accounts_do_not_restore_cookie(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        self.query('UPDATE careertrellis.app_users SET disabled_at=clock_timestamp() WHERE id=:uid',uid=session.local_id)
        self.query('UPDATE careertrellis.app_users SET disabled_at=NULL WHERE id=:uid',uid=session.local_id)
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)
        self.query('DELETE FROM careertrellis.app_users WHERE id=:uid',uid=session.local_id)
        self.query('INSERT INTO careertrellis.app_users(id,workos_user_id) OVERRIDING SYSTEM VALUE VALUES(:uid,:s)',uid=session.local_id,s=session.subject)
        self.assertNotEqual(session.incarnation,str(self.query('SELECT incarnation FROM careertrellis_admission.identity_accounts WHERE id=:uid',uid=session.local_id)))
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)

    async def test_disable_reenable_invalidates_claimed_and_completed_flow(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        for completed in (False,True):
            flow_cookie,state=await self.flow('login')
            flow=self.adapter._open_flow(flow_cookie,self.browser)
            attempt=str(uuid4())
            self.assertEqual('exchange',(await self.store.claim(flow,'synthetic-code',attempt))['status'])
            identity=await self.provider.exchange('synthetic-code',flow.verifier)
            if completed:
                self.assertEqual('login',(await self.store.finish(flow,attempt,identity))['status'])
            self.query('UPDATE careertrellis.app_users SET disabled_at=clock_timestamp() WHERE id=:uid',uid=session.local_id)
            self.query('UPDATE careertrellis.app_users SET disabled_at=NULL WHERE id=:uid',uid=session.local_id)
            self.assertEqual('expired',(await self.store.finish(flow,attempt,identity))['status'])

    async def test_unsupported_snapshot_isolation_denied_for_runtime_and_worker(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        authorization=(await self.adapter.authenticate(cookie)).authorization
        for role in ('careertrellis_runtime','careertrellis_worker'):
            with self.assertRaises(DBAPIError),self.fixture.engine(role).connect().execution_options(isolation_level='REPEATABLE READ') as c:
                c.execute(text("SELECT set_config('careertrellis.user_id',:uid,true)"),{'uid':str(session.local_id)})
                c.execute(text("SELECT set_config('careertrellis.provider_binding',:b,true),set_config('careertrellis.workos_user_id',:s,true),"
                    "set_config('careertrellis.session_epoch',:e,true),set_config('careertrellis.account_incarnation',:i,true)"),
                    {'b':authorization.provider_binding,'s':authorization.subject,'e':str(authorization.epoch),'i':authorization.incarnation})
                c.execute(text('SELECT careertrellis.current_app_user_id()'))

    async def test_worker_data_pauses_during_hold_and_resumes_without_reownership(self):
        cookie=await self.activate(); session=self.codec.open(cookie)
        worker=self.fixture.engine('careertrellis_worker')
        with tenant_transaction(worker,session.local_id) as c:
            self.assertEqual(1,c.scalar(text('SELECT count(*) FROM careertrellis.profiles')))
        op=self.prepare(session)
        with tenant_transaction(worker,session.local_id) as c:
            self.assertEqual(0,c.scalar(text('SELECT count(*) FROM careertrellis.profiles')))
        self.attest_activate(op,session.epoch+1)
        with tenant_transaction(worker,session.local_id) as c:
            self.assertEqual(1,c.scalar(text('SELECT count(*) FROM careertrellis.profiles')))

    async def test_erasure_worker_can_finalize_disabled_account_with_identity_ledger(self):
        from account_erasure_repository import (PostgreSQLAccountErasureRepository,
            PostgreSQLAccountErasureWorkerRepository,claim_next_account_erasure)
        cookie=await self.activate(); authorization=(await self.adapter.authenticate(cookie)).authorization
        token=current.set(authorization)
        try:
            PostgreSQLAccountErasureRepository(authorization.local_id,engine=self.fixture.engine('careertrellis_runtime')).request_erasure()
        finally: current.reset(token)
        self.assertEqual('unauthenticated',(await self.adapter.authenticate(cookie)).status)
        self.query('UPDATE careertrellis.app_users SET disabled_at=NULL WHERE id=:uid',uid=authorization.local_id)
        flow,state=await self.flow('login')
        result=await self.adapter.complete(flow,self.browser,state,'synthetic-reenabled-erasing')
        self.assertEqual('ineligible',result.status); self.assertFalse(result.cookie)
        self.query('UPDATE careertrellis.app_users SET disabled_at=clock_timestamp() WHERE id=:uid',uid=authorization.local_id)
        worker=self.fixture.engine('careertrellis_erasure_worker')
        claim=claim_next_account_erasure(engine=worker,worker_id=str(uuid4()),lease_seconds=300)
        self.assertTrue(PostgreSQLAccountErasureWorkerRepository(engine=worker).finalize(claim))
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.identity_accounts WHERE id=:uid',uid=authorization.local_id))
