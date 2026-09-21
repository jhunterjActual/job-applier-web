"""Synthetic PostgreSQL and protected web-switch transition failure rehearsal."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
import admission_operations as ops
import session_v2_transition as transition
import test_admission_initial as initial_tests


def synthetic_plan():
    from workos_transport_candidate import ProviderSettings
    p = {'operation': str(uuid4()), 'application': 'b'*40, 'operations': 'c'*40,
        'realm': 'synthetic-provider-v2', 'cap': 25, 'ttl': 600, 'session_seconds': 3600,
        'schema': transition.HEAD}
    p['provider_binding'] = ProviderSettings('client_synthetic_test','synthetic-only',
        'https://api.workos.com/user_management/client_synthetic_test',p['realm']).binding
    p['owner_proof'] = transition.owner_proof(p['operation'],p['provider_binding'],'user_synthetic_owner')
    return p


class PlanTests(unittest.TestCase):
    def test_strict_shape_and_approved_lifetime(self):
        p = synthetic_plan()
        self.assertEqual(p, transition.validate_plan(ops.encoded(p)))
        for changes in ({'cap': True}, {'cap': 0}, {'ttl': 901}, {'session_seconds': 7200},
                {'realm': 'with space'}, {'application': 'main'}, {'extra': True},
                {'schema':ops.INITIAL_HEAD}, {'provider_binding':'invalid'}, {'owner_proof':True}):
            with self.assertRaises(ops.OperationError):
                transition.validate_plan(ops.encoded({**p, **changes}))


class WebSwitchTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(ops, 'ROOT', self.root))
        self.stack.enter_context(patch.object(ops, 'STATE', self.root))
        self.stack.enter_context(patch.object(ops, 'AUTH_FILE', self.root / 'auth-v2.env'))
        self.stack.enter_context(patch.object(ops, 'read_protected', side_effect=lambda path, **kw: path.read_bytes()))
        self.stack.enter_context(patch.object(ops, 'write_protected', side_effect=self.write))
        self.stack.enter_context(patch.object(ops, 'stopped_services'))
        self.stack.enter_context(patch.object(ops, 'check_worker_delivery'))
        self.stack.enter_context(patch.object(transition, 'boot', return_value='synthetic-boot'))
        self.p = synthetic_plan()
        original_resolve = Path.resolve
        self.stack.enter_context(patch.object(Path, 'resolve', lambda path, *a, **kw:
            Path('/opt/careertrellis/staging/releases') / self.p['application']
            if str(path).replace('\\', '/') == '/opt/careertrellis/staging/current' else original_resolve(path, *a, **kw)))
        self.metadata = {'issuer': 'https://api.workos.com/user_management/client_synthetic_test',
            'generation': self.p['realm'], 'key_id': 'synthetic-k1', 'session_seconds': 3600}
        values = {'CAREERTRELLIS_AUTH_V2_ENABLED': '0',
            'CAREERTRELLIS_AUTH_V2_ISSUER': self.metadata['issuer'],
            'CAREERTRELLIS_AUTH_V2_GENERATION': self.p['realm'],
            'CAREERTRELLIS_AUTH_V2_KEY_ID': 'synthetic-k1',
            'CAREERTRELLIS_AUTH_V2_SESSION_SECONDS': '3600',
            'CAREERTRELLIS_AUTH_V2_ENVELOPE_KEY': Fernet.generate_key().decode(),
            'CAREERTRELLIS_AUTH_V2_FLOW_KEY': Fernet.generate_key().decode(),
            'CAREERTRELLIS_ADMISSION_DATABASE_URL': 'postgresql+psycopg://careertrellis_admission_broker:synthetic@10.70.0.2/careertrellis'}
        self.raw = ''.join(k + '=' + v + '\n' for k, v in values.items()).encode()
        ops.AUTH_FILE.write_bytes(self.raw)
        (self.root / 'staging.env').write_text('CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID=user_synthetic_owner\nWORKOS_CLIENT_ID=client_synthetic_test\nWORKOS_API_KEY=synthetic-only\nWORKOS_COOKIE_PASSWORD=' + Fernet.generate_key().decode() + '\n')
        (self.root / 'object-storage.env').write_bytes(b'')
        (self.root / 'provider-secrets.env').write_bytes(b'')
        (self.root / 'postgres-runtime.env').write_text('CAREERTRELLIS_DATABASE_URL=postgresql+psycopg://careertrellis_runtime:synthetic@10.70.0.2/careertrellis\n')
        (self.root / 'admission-web.json').write_bytes(ops.encoded(self.metadata))
        transition.path(self.p, 'armed').write_bytes(ops.encoded(transition.receipt(self.p, 'armed')))

    def write(self, path, raw, *, previous=None, **kw):
        if previous is None:
            with path.open('xb') as stream:
                stream.write(raw)
        else:
            self.assertEqual(previous, path.read_bytes())
            path.write_bytes(raw)

    def test_disabled_enrollment_fields_preserved_by_primary_enable(self):
        suffix=b'CAREERTRELLIS_AUTH_V2_TOTP_ENABLED=0\nCAREERTRELLIS_AUTH_V2_TOTP_KEY='+Fernet.generate_key()+b'\n'
        ops.AUTH_FILE.write_bytes(self.raw+suffix)
        transition.web_enable(self.p)
        self.assertEqual(self.raw.replace(b'CAREERTRELLIS_AUTH_V2_ENABLED=0',b'CAREERTRELLIS_AUTH_V2_ENABLED=1')+suffix,ops.AUTH_FILE.read_bytes())

    def test_enabled_enrollment_rejects_0015_without_write(self):
        raw=self.raw+b'CAREERTRELLIS_AUTH_V2_TOTP_ENABLED=1\nCAREERTRELLIS_AUTH_V2_TOTP_KEY='+Fernet.generate_key()+b'\n'
        ops.AUTH_FILE.write_bytes(raw)
        with self.assertRaises(ValueError): transition.web_enable(self.p)
        self.assertEqual(raw,ops.AUTH_FILE.read_bytes())

    def test_provisioned_key_change_after_completed_switch_rejects_resume(self):
        first=Fernet.generate_key()
        raw=self.raw+b'CAREERTRELLIS_AUTH_V2_TOTP_ENABLED=0\nCAREERTRELLIS_AUTH_V2_TOTP_KEY='+first+b'\n'
        ops.AUTH_FILE.write_bytes(raw)
        transition.web_enable(self.p)
        changed=ops.AUTH_FILE.read_bytes().replace(first,Fernet.generate_key())
        ops.AUTH_FILE.write_bytes(changed)
        with self.assertRaises(ops.OperationError): transition.web_enable(self.p)
        self.assertEqual(changed,ops.AUTH_FILE.read_bytes())

    def test_only_switch_bytes_change_and_lost_receipt_reconciles(self):
        original = ops.state_write
        def write(path, value):
            if value.get('phase') == 'enabled':
                raise OSError('synthetic lost acknowledgement')
            original(path, value)
        with patch.object(ops, 'state_write', side_effect=write), self.assertRaises(OSError):
            transition.web_enable(self.p)
        expected = self.raw.replace(b'ENABLED=0', b'ENABLED=1')
        self.assertEqual(expected, ops.AUTH_FILE.read_bytes())
        transition.web_enable(self.p)
        self.assertEqual(expected, ops.AUTH_FILE.read_bytes())
        self.assertEqual('enabled', json.loads(transition.path(self.p, 'web').read_bytes())['phase'])

    def test_wrong_receipt_metadata_or_lifetime_never_changes_switch(self):
        for name, value in (('session_seconds', 7200), ('generation', 'different'), ('key_id', 'different')):
            (self.root / 'admission-web.json').write_bytes(ops.encoded({**self.metadata, name: value}))
            with self.assertRaises(ops.OperationError):
                transition.web_enable(self.p)
            self.assertEqual(self.raw, ops.AUTH_FILE.read_bytes())
        (self.root / 'admission-web.json').write_bytes(ops.encoded(self.metadata))
        transition.path(self.p, 'armed').write_bytes(ops.encoded(transition.receipt({**self.p, 'operation': str(uuid4())}, 'armed')))
        with self.assertRaises(ops.OperationError):
            transition.web_enable(self.p)

    def test_preenabled_without_attempt_is_not_adopted(self):
        ops.AUTH_FILE.write_bytes(self.raw.replace(b'ENABLED=0', b'ENABLED=1'))
        with self.assertRaises(ops.OperationError):
            transition.web_enable(self.p)

    def test_completed_switch_and_changed_credentials_are_drift(self):
        transition.web_enable(self.p)
        ops.AUTH_FILE.write_bytes(self.raw)
        with self.assertRaises(ops.OperationError):
            transition.web_enable(self.p)
        self.assertEqual(self.raw, ops.AUTH_FILE.read_bytes())
        ops.AUTH_FILE.write_bytes(self.raw.replace(b'ENABLED=0', b'ENABLED=1'))
        staging = self.root / 'staging.env'
        staging.write_bytes(staging.read_bytes().replace(b'synthetic-only', b'synthetic-changed'))
        with self.assertRaises(ops.OperationError):
            transition.web_enable(self.p)

    def test_read_only_preflight_requires_no_armed_receipt_or_stopped_services(self):
        transition.path(self.p, 'armed').unlink()
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with patch.object(ops, 'stopped_services', side_effect=AssertionError('must be online')):
            transition.web_enable(self.p, preflight=True)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_web_reboot_blocks_reconciliation(self):
        transition.web_enable(self.p)
        with patch.object(transition, 'boot', return_value='different-boot'), self.assertRaises(ops.OperationError):
            transition.web_enable(self.p)

    def test_provider_and_owner_plan_mismatch_never_enables(self):
        for key in ('provider_binding','owner_proof'):
            with self.assertRaises(ops.OperationError):
                transition.web_enable({**self.p,key:'f'*64},preflight=True)
            self.assertEqual(self.raw,ops.AUTH_FILE.read_bytes())

    def test_later_environment_files_cannot_override_verified_identity(self):
        for filename in ('provider-secrets.env','object-storage.env'):
            for setting in ('CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID','WORKOS_CLIENT_ID','CAREERTRELLIS_AUTH_MODE'):
                (self.root/filename).write_text(setting+'=synthetic_override\n')
                with self.assertRaises(ops.OperationError): transition.web_enable(self.p,preflight=True)
                self.assertEqual(self.raw,ops.AUTH_FILE.read_bytes())
                (self.root/filename).write_bytes(b'')


class DatabaseTransitionTests(unittest.TestCase):
    # Reuse the strict empty-database fixture, not its test methods.
    setUp = initial_tests.InitialMigrationTests.setUp
    write = initial_tests.InitialMigrationTests.write
    quiesce = initial_tests.InitialMigrationTests.quiesce
    begin = initial_tests.InitialMigrationTests.begin
    record = initial_tests.InitialMigrationTests.record
    migrate = initial_tests.InitialMigrationTests.migrate

    def prepare(self, *, prior_invalidations=0, schema=transition.HEAD):
        self.begin()
        self.migrate()
        ops.finish_initial(self.db, self.operation, ops.initial_receipt(self.record(), 'initial_migrated'))
        self.db.query('ALTER ROLE careertrellis_admission_broker LOGIN;')
        self.p = synthetic_plan()
        self.p['schema'] = schema
        self.p['application'] = self.target
        for _ in range(prior_invalidations):
            self.db.query('SELECT careertrellis_admission.invalidate_flows();')
        ops.begin_maintenance(self.db, self.p['operation'], self.target)
        from alembic import command
        from alembic.config import Config
        from signup_prototype.integration_fixture import ROOT
        from sqlalchemy import text
        cfg = Config(); cfg.set_main_option('script_location',str(ROOT/'migrations'))
        cfg.attributes['migration_url'] = self.migration.url
        command.upgrade(cfg,schema)
        # Synthetic current-environment binding. No provider environment switch.
        with self.migration.begin() as c:
            c.execute(text('SELECT careertrellis_admission.configure_identity_provider(NULL,:binding)'),{'binding':self.p['provider_binding']})
        subject = self.db.query("SELECT to_json(workos_user_id) FROM careertrellis.app_users WHERE role='owner'")
        self.p['owner_proof'] = transition.owner_proof(self.p['operation'],self.p['provider_binding'],subject)
        (ops.STATE / ('web-' + self.p['operation'] + '.json')).write_bytes(ops.encoded({
            'phase':'migrated','operation':self.p['operation'],'target':self.target,'head':schema}))
        self.quiesce()

    def accept_web(self):
        transition.path(self.p, 'web').write_bytes(ops.encoded(transition.receipt(self.p, 'enabled')))

    def test_0016_exact_plan_receipt_closed_activation_and_purge(self):
        import admission_purge
        from sqlalchemy.exc import DBAPIError
        self.prepare(schema='20260915_0016')
        wrong={**self.p,'schema':transition.HEAD}
        with self.assertRaises(ops.OperationError): transition.db_arm(self.db,wrong)
        with self.assertRaises(DBAPIError): self.db.query(transition.assert_identity_sql(wrong))
        with self.assertRaises(DBAPIError): self.db.query(admission_purge.schema_gate_sql())
        transition.db_arm(self.db,self.p)
        self.accept_web(); transition.db_release(self.db,self.p)
        self.assertEqual(2,ops.policy(self.db)['minimum_session_version'])
        self.assertFalse(ops.policy(self.db)['signup_open'])
        self.db.query(admission_purge.schema_gate_sql())
        self.assertEqual(self.original_profile,self.db.query('SELECT row_to_json(p) FROM careertrellis.profiles p'))

    def test_nonzero_maintenance_generation_is_preserved_and_mismatch_rejected(self):
        self.prepare(prior_invalidations=6)
        with self.assertRaises(ops.OperationError): transition.db_arm(self.db,self.p,baseline_generation=0)
        self.assertFalse(transition.path(self.p,'database').exists())
        transition.db_arm(self.db,self.p,baseline_generation=6)
        self.assertEqual(8,ops.policy(self.db)['generation'])
        with self.assertRaises(ops.OperationError): transition.db_arm(self.db,self.p,baseline_generation=5)
        self.assertEqual(8,ops.policy(self.db)['generation'])
        transition.db_arm(self.db,self.p,baseline_generation=6)
        self.accept_web(); transition.db_release(self.db,self.p)
        self.assertEqual(8,ops.policy(self.db)['generation'])
        self.assertEqual(2,ops.policy(self.db)['minimum_session_version'])
        self.assertFalse(ops.policy(self.db)['signup_open'])

    def test_closed_transition_preserves_profile_and_never_runs_migration_again(self):
        self.prepare()
        transition.db_arm(self.db, self.p)
        self.assertEqual(2, ops.policy(self.db)['minimum_session_version'])
        self.assertTrue(ops.policy(self.db)['maintenance'])
        self.accept_web()
        transition.db_release(self.db, self.p)
        self.assertFalse(ops.policy(self.db)['signup_open'])
        self.assertFalse(ops.policy(self.db)['maintenance'])
        self.assertEqual(self.original_profile, self.db.query('SELECT row_to_json(p) FROM careertrellis.profiles p'))
        self.assertEqual(transition.HEAD, ops.revision(self.db))

    def test_owner_only_complete_bind_switch_and_closed_activation_after_six_disables(self):
        import hashlib
        from datetime import datetime, timedelta, timezone
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import text
        import identity_configuration as configuration
        import identity_operator as operator
        import identity_plan_packet as packets
        import identity_plan_store as store
        from pilot_users import perform_operation
        from signup_prototype.integration_fixture import ROOT
        from test_identity_configuration import plan

        self.begin(); self.migrate()
        ops.finish_initial(self.db,self.operation,ops.initial_receipt(self.record(),'initial_migrated'))
        self.db.query('ALTER ROLE careertrellis_admission_broker LOGIN')
        owner_subject='user_overlay_preserved'
        for index in range(6):
            subject='user_synthetic_pilot_'+str(index)
            self.assertEqual('created',perform_operation(self.migration,'seed',owner_subject,subject).result)
            self.assertEqual('disabled',perform_operation(self.migration,'disable',owner_subject,subject).result)
        baseline=ops.policy(self.db)['generation']
        self.assertEqual(6,baseline)
        before_profiles=self.db.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM careertrellis.profiles p')
        before_pilots=self.db.query("SELECT jsonb_agg(to_jsonb(u) ORDER BY id) FROM careertrellis.app_users u WHERE role='user'")
        source=synthetic_plan()['provider_binding']; target='e'*64
        owner_operation=str(uuid4())
        b=plan(source,owner_subject); b['expected_generation']=baseline
        p=plan(source,'user_synthetic_production_owner')
        p.update(mode='switch_environment',target_binding=target,expected_generation=baseline+2,
            transitions=[owner_operation],quarantined_accounts=[row['id'] for row in before_pilots])
        p['owner_proof']=transition.owner_proof(p['operation'],target,'user_synthetic_production_owner')
        def pair(provider):
            return {'provider':provider,'activation':{**synthetic_plan(),
                **{key:provider[key] for key in ('operation','application','operations','owner_proof')},
                'provider_binding':provider['target_binding']}}
        packet={'version':1,'bind':pair(b),'switch':pair(p)}
        raw=ops.encoded(packet)
        self.assertEqual(packet,packets.validate(raw))
        self.p=packet['switch']['activation']
        self.quiesce()
        ops.begin_maintenance(self.db,self.p['operation'],self.target)
        marker=ops.strict_json((ops.STATE/('maintenance-'+self.p['operation']+'.json')).read_bytes())
        self.assertEqual(baseline,marker['policy']['generation'])
        cfg=Config(); cfg.set_main_option('script_location',str(ROOT/'migrations'))
        cfg.attributes['migration_url']=self.migration.url
        command.upgrade(cfg,transition.HEAD)
        self.quiesce()
        with patch.object(operator,'DATABASE','ct_signup_integration'):
            self.assertEqual({'outcome':'configuration_committed_maintenance_retained'},
                configuration.apply(ops.encoded(b),self.migration,verified_current_binding=source))
            self.assertEqual(baseline+1,ops.policy(self.db)['generation'])
            owner=self.db.query("SELECT row_to_json(a) FROM careertrellis_admission.identity_accounts a WHERE role='owner'")
            with operator.secure_connection(self.migration) as c:
                operator.setup(c)
                c.execute(text('SELECT careertrellis_admission.identity_prepare(CAST(:op AS uuid),:uid,:b,:s,:e,CAST(:i AS uuid),:tb,:ts,:deadline,true)'),
                    {'op':owner_operation,'uid':owner['id'],'b':source,'s':owner['subject'],
                     'e':owner['epoch'],'i':owner['incarnation'],'tb':target,
                     'ts':'user_synthetic_production_owner','deadline':datetime.now(timezone.utc)+timedelta(seconds=300)})
                c.execute(text("SELECT careertrellis_admission.identity_attest(CAST(:op AS uuid),:e,repeat('a',64),clock_timestamp(),clock_timestamp()+interval '120 seconds',true)"),
                    {'op':owner_operation,'e':owner['epoch']+1})
            self.assertEqual(p['expected_generation'],ops.policy(self.db)['generation'])
            self.assertEqual({'outcome':'configuration_committed_maintenance_retained'},
                configuration.apply(ops.encoded(p),self.migration,verified_current_binding=source))
        self.assertEqual(baseline+5,ops.policy(self.db)['generation'])
        # Exercise the selected immutable B/P archive contract, not a caller-supplied zero.
        digest=hashlib.sha256(raw).hexdigest()
        store.archive(digest).write_bytes(raw)
        with patch.object(ops,'ROOT',self.state):
            store.location().write_bytes(ops.encoded({'version':1,'digest':digest,
                'operation':p['operation'],'sequence':1}))
            selected_baseline=transition.arm_baseline(self.p)
        self.assertEqual(baseline,selected_baseline)
        (ops.STATE/('web-'+self.p['operation']+'.json')).write_bytes(ops.encoded({
            'phase':'migrated','operation':self.p['operation'],'target':self.target,'head':transition.HEAD}))
        self.quiesce()
        with self.assertRaises(ops.OperationError): transition.db_arm(self.db,self.p,baseline_generation=0)
        transition.db_arm(self.db,self.p,baseline_generation=selected_baseline)
        self.accept_web(); transition.db_release(self.db,self.p)
        self.assertEqual(baseline+6,ops.policy(self.db)['generation'])
        self.assertEqual(2,ops.policy(self.db)['minimum_session_version'])
        self.assertFalse(ops.policy(self.db)['signup_open'])
        self.assertFalse(ops.policy(self.db)['maintenance'])
        self.assertEqual(before_profiles,self.db.query('SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM careertrellis.profiles p'))
        self.assertEqual(before_pilots,self.db.query("SELECT jsonb_agg(to_jsonb(u) ORDER BY id) FROM careertrellis.app_users u WHERE role='user'"))
        self.assertEqual(6,self.db.query("SELECT count(*) FROM careertrellis_admission.identity_accounts WHERE role='user' AND disabled AND NOT held AND binding='"+source+"'"))
        self.assertEqual(6,self.db.query("SELECT count(*) FROM careertrellis_admission.identity_history WHERE account_id<>"+str(owner['id'])+" AND state='active' AND binding='"+source+"'"))
        self.assertEqual('user_synthetic_production_owner',self.db.query("SELECT to_json(workos_user_id) FROM careertrellis.app_users WHERE role='owner'"))

    def test_lost_arm_and_release_acknowledgements_reconcile_forward(self):
        self.prepare()
        original = ops.state_write
        def fail_armed(path, value):
            if value.get('phase') == 'armed':
                raise OSError('synthetic lost acknowledgement')
            original(path, value)
        with patch.object(ops, 'state_write', side_effect=fail_armed), self.assertRaises(OSError):
            transition.db_arm(self.db, self.p)
        self.assertEqual(2, ops.policy(self.db)['minimum_session_version'])
        transition.db_arm(self.db, self.p)
        self.accept_web()
        with patch.object(ops, 'state_write', side_effect=OSError()), self.assertRaises(OSError):
            transition.db_release(self.db, self.p)
        transition.db_release(self.db, self.p)
        self.assertEqual(2, ops.policy(self.db)['minimum_session_version'])
        self.assertEqual(2, ops.policy(self.db)['generation'])

    def test_mismatched_web_receipt_and_policy_drift_retain_fence(self):
        self.prepare()
        transition.db_arm(self.db, self.p)
        transition.path(self.p, 'web').write_bytes(ops.encoded(transition.receipt({**self.p, 'cap': 26}, 'enabled')))
        with self.assertRaises(ops.OperationError):
            transition.db_release(self.db, self.p)
        self.accept_web()
        self.db.query('UPDATE careertrellis_admission.policy SET ttl=601;')
        with self.assertRaises(ops.OperationError):
            transition.db_release(self.db, self.p)
        self.assertEqual(dict.fromkeys(ops.CALLERS, False), ops.roles(self.db))
        self.assertEqual(2, ops.policy(self.db)['minimum_session_version'])

    def test_active_migration_connection_blocks_arm(self):
        self.prepare()
        with self.migration.connect():
            with self.assertRaises(Exception):
                transition.db_arm(self.db, self.p)
        self.assertEqual(1, ops.policy(self.db)['minimum_session_version'])
        self.quiesce()
        transition.db_arm(self.db, self.p)

    def test_missing_maintenance_marker_never_releases_fence(self):
        self.prepare()
        transition.db_arm(self.db, self.p)
        self.accept_web()
        (ops.STATE / ('maintenance-' + self.p['operation'] + '.json')).unlink()
        with self.assertRaises(FileNotFoundError):
            transition.db_release(self.db, self.p)
        self.assertTrue(ops.policy(self.db)['maintenance'])
        self.assertEqual(dict.fromkeys(ops.CALLERS, False), ops.roles(self.db))

    def test_wrong_migration_receipt_and_owner_state_block_activation(self):
        self.prepare()
        path = ops.STATE / ('web-' + self.p['operation'] + '.json')
        original = path.read_bytes()
        for key,value in (('head',ops.INITIAL_HEAD),('operation',str(uuid4())),('target','d'*40)):
            path.write_bytes(ops.encoded({**json.loads(original),key:value}))
            with self.assertRaises(ops.OperationError): transition.db_arm(self.db,self.p)
            self.assertEqual(1,ops.policy(self.db)['minimum_session_version'])
        path.write_bytes(original)
        self.db.query("UPDATE careertrellis.app_users SET disabled_at=clock_timestamp() WHERE role='owner'")
        with self.assertRaises(Exception): transition.db_arm(self.db,self.p)
        self.assertEqual(1,ops.policy(self.db)['minimum_session_version'])
        self.assertTrue(ops.policy(self.db)['maintenance'])

    def test_armed_owner_drift_and_generic_finish_cannot_release(self):
        self.prepare(); transition.db_arm(self.db,self.p); self.accept_web()
        with self.assertRaises(ops.OperationError):
            ops.finish_maintenance(self.db,self.p['operation'],{'phase':'migrated',
                'operation':self.p['operation'],'target':self.target,'head':transition.HEAD})
        self.db.query("UPDATE careertrellis_admission.identity_accounts SET held=true WHERE role='owner'")
        with self.assertRaises(Exception): transition.db_release(self.db,self.p)
        self.assertEqual(2,ops.policy(self.db)['minimum_session_version'])
        self.assertTrue(ops.policy(self.db)['maintenance'])
        self.assertEqual(dict.fromkeys(ops.CALLERS,False),ops.roles(self.db))

    def test_0015_purge_waits_for_release_and_preserves_expired_identity_evidence(self):
        import admission_purge as purge
        from sqlalchemy import text
        import time
        self.prepare()
        fixture = self.fixture
        class Database:
            def query(inner,sql):
                with fixture.admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
                    with c.connection.cursor() as cursor:
                        cursor.execute(sql); answer=None
                        while True:
                            if cursor.description:
                                row=cursor.fetchone()
                                if row: answer=row[0]
                            if not cursor.nextset(): break
                        return answer
        db = Database()
        with self.assertRaises(Exception): purge.purge(db)
        transition.db_arm(self.db,self.p)
        with self.assertRaises(Exception): purge.purge(db)
        self.accept_web(); transition.db_release(self.db,self.p)
        # Exercise the real hold/evidence functions, then let evidence expire.
        op=str(uuid4())
        with self.migration.begin() as c:
            c.exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
            c.execute(text("SELECT careertrellis_admission.identity_prepare(CAST(:op AS uuid),a.id,a.binding,a.subject,a.epoch,a.incarnation,"
                "a.binding,a.subject,clock_timestamp()+interval '0.3 seconds',true) FROM careertrellis_admission.identity_accounts a"),{'op':op})
            c.execute(text("SELECT careertrellis_admission.identity_attest(CAST(:op AS uuid),a.epoch,repeat('a',64),"
                "clock_timestamp(),clock_timestamp()+interval '0.1 seconds',true) FROM careertrellis_admission.identity_accounts a"),{'op':op})
        time.sleep(.35)
        before = {table:self.db.query('SELECT jsonb_agg(to_jsonb(t)) FROM careertrellis_admission.'+table+' t')
            for table in ('identity_accounts','identity_history','identity_transitions')}
        self.db.query("INSERT INTO careertrellis_admission.flows(id,binding,deadline,expires,generation,purpose,state) "
            "VALUES(gen_random_uuid(),repeat('a',64),now()-interval '1 hour',now()-interval '1 hour',0,'signup','activated'); "
            "INSERT INTO careertrellis_admission.http_revocations VALUES(repeat('b',64),now()+interval '24 hours');")
        counts,result=purge.purge(db)
        self.assertEqual('ok',result); self.assertEqual(1,counts['flows']); self.assertEqual(0,counts['http_revocations'])
        for table,rows in before.items():
            self.assertEqual(rows,self.db.query('SELECT jsonb_agg(to_jsonb(t)) FROM careertrellis_admission.'+table+' t'))
        self.assertEqual(self.original_profile,self.db.query('SELECT row_to_json(p) FROM careertrellis.profiles p'))
        # An unbound or unknown schema never gains acceptance from the allowlist.
        self.db.query('UPDATE careertrellis_admission.identity_provider SET binding=NULL')
        with self.assertRaises(Exception): purge.purge(db)
