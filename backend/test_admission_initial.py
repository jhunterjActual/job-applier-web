"""Protected initial rollout: synthetic PG only, no host or provider operations."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

import admission_operations as ops
from signup_prototype.integration_fixture import Fixture, ROOT


class MigrationInputTests(unittest.TestCase):
    def validate(self, migration, runtime):
        files = {ops.ROOT / 'postgres-migration.env': ('CAREERTRELLIS_MIGRATION_DATABASE_URL=' + migration + '\n').encode(),
            ops.ROOT / 'postgres-runtime.env': ('CAREERTRELLIS_DATABASE_URL=' + runtime + '\n').encode()}
        with patch.object(ops, 'read_protected', side_effect=lambda path: files[path]):
            return ops.migration_connection_url('careertrellis')

    def test_driver_override_trigger_and_alternate_forms_are_rejected(self):
        from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
        from sqlalchemy.engine import make_url
        migration = 'postgresql+psycopg://careertrellis_migration:synthetic@10.70.0.2/careertrellis'
        runtime = migration.replace('careertrellis_migration:', 'careertrellis_runtime:')
        # Demonstrate why inspecting only URL.host/database is insufficient.
        unsafe = make_url(migration + '?host=127.0.0.1&dbname=other')
        effective = PGDialect_psycopg().create_connect_args(unsafe)[1]
        self.assertEqual('10.70.0.2', unsafe.host)
        self.assertEqual('127.0.0.1', effective['host'])
        self.assertEqual('other', effective['dbname'])
        for query in ('host=127.0.0.1', 'hostaddr=127.0.0.1', 'dbname=other', 'user=postgres',
                'password=other', 'port=6543', 'service=other', 'passfile=/other',
                'options=-csearch_path=public', 'sslmode=require&sslmode=disable'):
            for left, right in ((migration + '?' + query, runtime), (migration, runtime + '?' + query)):
                with self.subTest(query=query), self.assertRaises(ops.OperationError):
                    self.validate(left, right)
        for unsafe in (runtime.replace('10.70.0.2', '10.70.0.3'), runtime + '?sslmode=require',
                runtime.replace('/careertrellis', '/other'), runtime.replace('synthetic', '')):
            with self.assertRaises(ops.OperationError):
                self.validate(migration, unsafe)

    def test_legitimate_wireguard_and_explicit_tls_and_driver_alias(self):
        migration = 'postgresql://careertrellis_migration:synthetic@10.70.0.2/careertrellis'
        runtime = migration.replace('careertrellis_migration:', 'careertrellis_runtime:')
        self.assertEqual('postgresql+psycopg', self.validate(migration, runtime).drivername)
        for ssl in ('', '?sslmode=require'):
            self.assertEqual('10.70.0.2', self.validate(migration + ssl, runtime + ssl).host)
        tls = '?sslmode=verify-full&sslrootcert=/etc/careertrellis/ca.crt'
        self.assertEqual('10.108.0.3', self.validate(migration.replace('10.70.0.2', '10.108.0.3') + tls,
            runtime.replace('10.70.0.2', '10.108.0.3') + tls).host)


class InitialMigrationTests(unittest.TestCase):
    def setUp(self):
        url = os.environ.get('CAREERTRELLIS_SIGNUP_ROLLOUT_TEST_URL')
        if not url:
            self.skipTest('Exact empty disposable rollout database required')
        self.fixture = Fixture(url)
        self.addCleanup(self.fixture.close)
        self.fixture.setup(overlay=False)
        self.migration = self.fixture.engine('careertrellis_migration')
        self.sql = (ROOT.parent / 'deploy/postgres/bootstrap-admission-roles.sql').read_text()
        self.operation, self.target = str(uuid4()), 'b'*40
        self.temporary = tempfile.TemporaryDirectory(prefix='ct-initial-state-')
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(ops, 'STATE', self.state))
        # Windows lacks root POSIX file semantics. Native descriptor tests cover
        # those separately; all SQL and state files here are real synthetic data.
        read = Path.read_text
        boot = Path('/proc/sys/kernel/random/boot_id')
        self.stack.enter_context(patch.object(Path, 'read_text',
            lambda path, *a, **kw: 'synthetic-boot' if path == boot else read(path, *a, **kw)))
        self.stack.enter_context(patch.object(ops, 'read_protected', side_effect=lambda path, **kw: path.read_bytes()))
        self.stack.enter_context(patch.object(ops, 'write_protected', side_effect=self.write))
        class Database:
            def query(inner, statement):
                with self.fixture.admin.begin() as connection:
                    # SQLAlchemy passes an empty parameter mapping to psycopg;
                    # escape literal SQL format('%I') for that driver protocol.
                    result = connection.exec_driver_sql(statement.replace('%', '%%'))
                    return result.scalar() if result.returns_rows else None
        self.db = Database()
        class Release:
            name = self.target
            def __truediv__(inner, suffix):
                return ROOT.parent / suffix
        self.release = Release()
        self.original_profile = self.db.query('SELECT row_to_json(p) FROM careertrellis.profiles p')
        self.quiesce()

    def write(self, path, raw, *, previous=None, **kwargs):
        if previous is None:
            with path.open('xb') as stream:
                stream.write(raw)
        else:
            self.assertEqual(previous, path.read_bytes())
            path.write_bytes(raw)

    def quiesce(self):
        for engine in self.fixture.engines.values():
            engine.dispose()

    def begin(self, sql=None):
        try:
            return ops.begin_initial(self.db, self.operation, self.target, self.sql if sql is None else sql)
        finally:
            # Register only synthetic objects actually created, even on failure.
            for role in ('careertrellis_admission_owner', ops.BROKER):
                if self.db.query("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='" + role + "')") and role not in self.fixture.roles:
                    self.fixture.roles.append(role)
            for schema in ('careertrellis_admission', 'careertrellis_lifecycle'):
                if self.db.query("SELECT to_regnamespace('" + schema + "') IS NOT NULL") and schema not in self.fixture.schemas:
                    self.fixture.schemas.append(schema)

    def record(self):
        return json.loads((self.state / 'initial.json').read_bytes())

    def migrate(self, receipt=None):
        if receipt is None:
            receipt = ops.initial_receipt(self.record(), 'initial_quiesced')
        (self.state / 'database-initial-quiesced.json').write_bytes(ops.encoded(receipt))
        values = ('CAREERTRELLIS_MIGRATION_DATABASE_URL=' + self.migration.url.render_as_string(hide_password=False) + '\n').encode()
        runtime = ('CAREERTRELLIS_DATABASE_URL=' + self.fixture.engine('careertrellis_runtime').url.render_as_string(hide_password=False) + '\n').encode()
        read = ops.read_protected
        def protected(path, **kwargs):
            if path == ops.ROOT / 'postgres-migration.env':
                return values
            if path == ops.ROOT / 'postgres-runtime.env':
                return runtime
            return read(path, **kwargs)
        before = dict(os.environ)
        with patch.object(ops, 'read_protected', side_effect=protected), \
                patch.object(ops, 'stopped_services'), patch.object(ops, 'check_worker_delivery'), \
                patch.object(subprocess, 'run', side_effect=AssertionError('No child process may receive the credential')):
            result = ops.migrate_web(self.operation, self.target, self.release, database='ct_signup_integration', initial=True)
        self.assertEqual(before, dict(os.environ))
        return result

    def test_complete_real_upgrade_grants_default_off_and_mixed_role_restoration(self):
        self.db.query('ALTER ROLE careertrellis_worker NOLOGIN')
        original = ops.roles(self.db)
        receipt = self.begin()
        self.assertEqual('initial_quiesced', receipt['phase'])
        self.assertEqual(dict.fromkeys(ops.CALLERS, False), ops.roles(self.db))
        acknowledgement = self.migrate()
        self.assertEqual(ops.INITIAL_HEAD, ops.revision(self.db))
        self.assertEqual(ops.INITIAL_POLICY, ops.policy(self.db))
        self.quiesce()
        ops.finish_initial(self.db, self.operation, acknowledgement)
        self.assertEqual({**original, ops.BROKER: False}, ops.roles(self.db))
        self.assertEqual(self.original_profile, self.db.query('SELECT row_to_json(p) FROM careertrellis.profiles p'))
        ops.assert_passwordless_broker(self.db)
        with self.fixture.engine('careertrellis_runtime').connect() as connection:
            self.assertTrue(connection.scalar(text('SELECT careertrellis.admission_lifecycle_ready()')))
        self.quiesce()
        with self.assertRaises(ops.OperationError):
            ops.finish_initial(self.db, self.operation, acknowledgement)

    def test_live_connection_survives_fence_and_explicit_quiesce_resumes(self):
        held = create_engine(self.fixture.engine('careertrellis_runtime').url, poolclass=NullPool, hide_parameters=True)
        self.addCleanup(held.dispose)
        with held.connect() as existing:
            with self.assertRaises(DBAPIError):
                self.begin()
            self.assertEqual(1, existing.scalar(text('SELECT 1')))
            self.assertEqual('bootstrapped_fenced', self.record()['phase'])
            self.assertFalse((self.state / ('database-initial-' + self.operation + '.json')).exists())
            for role in ops.CALLERS[:-1]:
                engine = self.fixture.engine(role)
                engine.dispose()
                with self.assertRaises(DBAPIError):
                    with engine.connect():
                        pass
            with self.assertRaises(ops.OperationError):
                ops.begin_initial(self.db, str(uuid4()), self.target, self.sql)
        self.quiesce()
        receipt = ops.quiesce_initial(self.db, self.operation)
        self.assertEqual(receipt, ops.quiesce_initial(self.db, self.operation))
        self.assertEqual('initial_quiesced', self.record()['phase'])

    def test_bootstrap_failure_rolls_back_roles_and_fence_retains_attempt(self):
        before = ops.roles(self.db)
        with self.assertRaises(DBAPIError):
            self.begin(self.sql + "DO $$ BEGIN RAISE EXCEPTION 'synthetic failure'; END $$;")
        self.assertEqual(before, ops.roles(self.db))
        self.assertFalse(self.db.query("SELECT to_regnamespace('careertrellis_admission') IS NOT NULL"))
        self.assertEqual('prepared', self.record()['phase'])
        with self.assertRaises(ops.OperationError):
            self.begin()

    def test_wrong_revision_and_preexisting_schema_refuse_without_attempt(self):
        self.db.query("UPDATE careertrellis.alembic_version SET version_num='20260902_0012'")
        with self.assertRaises(DBAPIError):
            self.begin()
        self.assertFalse((self.state / 'initial.json').exists())
        self.db.query("UPDATE careertrellis.alembic_version SET version_num='20260903_0013'")
        self.db.query('CREATE SCHEMA careertrellis_admission')
        self.fixture.schemas.append('careertrellis_admission')
        with self.assertRaises(DBAPIError):
            self.begin()
        self.assertFalse((self.state / 'initial.json').exists())

    def test_elevated_original_caller_refuses_before_bootstrap_or_fence(self):
        self.db.query('ALTER ROLE careertrellis_worker BYPASSRLS')
        with self.assertRaises(DBAPIError):
            self.begin()
        self.assertFalse((self.state / 'initial.json').exists())
        self.assertTrue(ops.roles(self.db)['careertrellis_worker'])
        self.assertNotIn(ops.BROKER, ops.roles(self.db))

    def test_receipt_mismatch_and_regular_flow_substitution_refuse(self):
        receipt = self.begin()
        for change in ({'phase': 'quiesced'}, {'operation': str(uuid4())}, {'target': 'c'*40},
                {'source': '20260902_0012'}, {'head': '20260908_0015'}):
            with self.subTest(change=change), self.assertRaises(ops.OperationError):
                self.migrate({**receipt, **change})
        self.assertFalse((self.state / ('web-attempt-' + self.operation + '.json')).exists())
        acknowledgement = self.migrate()
        for change in ({'phase': 'migrated'}, {'operation': str(uuid4())}, {'target': 'c'*40}):
            with self.subTest(change=change), self.assertRaises(ops.OperationError):
                ops.finish_initial(self.db, self.operation, {**acknowledgement, **change})
        self.assertEqual(dict.fromkeys(ops.CALLERS, False), ops.roles(self.db))

    def test_failed_grants_after_upgrade_never_publishes_success_or_retries(self):
        self.begin()
        with patch('apply_postgres_runtime_grants.apply_runtime_grants', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.migrate()
        self.assertEqual(ops.INITIAL_HEAD, ops.revision(self.db))
        self.assertTrue((self.state / ('web-attempt-' + self.operation + '.json')).exists())
        self.assertFalse((self.state / ('web-' + self.operation + '.json')).exists())
        with self.assertRaises(ops.OperationError):
            self.migrate()
        self.assertEqual(dict.fromkeys(ops.CALLERS, False), ops.roles(self.db))
        with self.assertRaises(ops.OperationError):
            ops.begin_maintenance(self.db, str(uuid4()), self.target)
        with self.assertRaises(ops.OperationError):
            ops.initial_finished()

    def test_policy_drift_and_reboot_refuse_release(self):
        self.begin()
        acknowledgement = self.migrate()
        self.db.query('UPDATE careertrellis_admission.policy SET cap=2')
        with self.assertRaises(ops.OperationError):
            ops.finish_initial(self.db, self.operation, acknowledgement)
        self.db.query('UPDATE careertrellis_admission.policy SET cap=1')
        record = self.record()
        record['boot'] = 'different-boot'
        ops.state_write(self.state / 'initial.json', record)
        with self.assertRaises(ops.OperationError):
            ops.finish_initial(self.db, self.operation, acknowledgement)
        self.assertEqual(dict.fromkeys(ops.CALLERS, False), ops.roles(self.db))

    def test_finish_lost_ack_reconciles_without_repeating_database_mutation(self):
        self.begin()
        acknowledgement = self.migrate()
        with patch.object(ops, 'state_write', side_effect=OSError('synthetic lost acknowledgement')):
            with self.assertRaises(OSError):
                ops.finish_initial(self.db, self.operation, acknowledgement)
        self.assertEqual('initial_quiesced', self.record()['phase'])
        before = ops.roles(self.db)
        with patch.object(self.db, 'query', wraps=self.db.query) as query:
            ops.finish_initial(self.db, self.operation, acknowledgement)
        self.assertFalse(any('ALTER ROLE' in call.args[0] for call in query.call_args_list))
        self.assertEqual(before, ops.roles(self.db))



if __name__ == '__main__':
    unittest.main()
