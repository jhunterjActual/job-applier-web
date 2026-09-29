"""Explicit synthetic fixture, not an application installer or launcher.

Requires a fresh dedicated PostgreSQL 18 server and the exact loopback database.
Refuses collisions rather than dropping/reusing somebody else's test state.
"""

from contextlib import contextmanager
import os
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from .integration import DATABASE, isolated_engine


ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'synthetic-integration-only'
ROLES = ('careertrellis_schema_owner', 'careertrellis_migration', 'careertrellis_runtime',
         'careertrellis_worker', 'careertrellis_erasure_worker',
         'ct_admission_owner', 'ct_admission_broker', 'ct_admission_outsider')


class Fixture:
    def __init__(self, url):
        self.url = make_url(url)
        self.admin = isolated_engine(create_engine(self.url, hide_parameters=True))
        self.roles = []
        self.schemas = []
        self.engines = {}

    def engine(self, role):
        if role not in ROLES:
            raise ValueError('Unknown fixture role')
        if role not in self.engines:
            self.engines[role] = create_engine(self.url.set(username=role, password=PASSWORD),
                hide_parameters=True, pool_size=10, max_overflow=0)
        return self.engines[role]

    def setup(self, *, overlay=True):
        with self.admin.begin() as connection:
            version = int(connection.exec_driver_sql('SHOW server_version_num').scalar_one())
            if not 180000 <= version < 190000:
                raise ValueError('PostgreSQL 18 is required')
            if connection.execute(text("SELECT count(*) FROM pg_database WHERE NOT datistemplate "
                    "AND datname NOT IN ('postgres', :name)"), {'name': DATABASE}).scalar_one():
                raise ValueError('Dedicated disposable server required')
            if connection.exec_driver_sql("SELECT count(*) FROM pg_namespace WHERE nspname IN "
                    "('careertrellis', 'ct_admission')").scalar_one():
                raise ValueError('Fixture schema collision; refusing reuse')
            if connection.exec_driver_sql("SELECT count(*) FROM pg_namespace WHERE left(nspname, 3) <> 'pg_' "
                    "AND nspname NOT IN ('public', 'information_schema')").scalar_one():
                raise ValueError('Empty disposable database required')
            if connection.exec_driver_sql("SELECT count(*) FROM pg_class WHERE relnamespace='public'::regnamespace").scalar_one():
                raise ValueError('Empty disposable database required')
            for role in ROLES:
                if connection.execute(text('SELECT 1 FROM pg_roles WHERE rolname = :role'),
                                      {'role': role}).scalar():
                    raise ValueError('Fixture role collision; refusing reuse')
            for role in ROLES:
                login = 'NOLOGIN' if role.endswith('_owner') else "LOGIN PASSWORD 'synthetic-integration-only'"
                connection.exec_driver_sql(f'CREATE ROLE {role} {login} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEROLE NOCREATEDB')
            connection.exec_driver_sql('GRANT careertrellis_schema_owner TO careertrellis_migration')
            connection.exec_driver_sql('GRANT SET ON PARAMETER log_parameter_max_length, '
                                       'log_parameter_max_length_on_error TO careertrellis_migration')
            connection.exec_driver_sql('CREATE SCHEMA careertrellis AUTHORIZATION careertrellis_schema_owner')
        self.roles = list(ROLES)
        self.schemas = ['careertrellis']
        # Actual Alembic environment and grant script, with synthetic config only.
        with patch.dict(os.environ, {'CAREERTRELLIS_CONFIG_READ_ONLY': '1',
                'CAREERTRELLIS_MIGRATION_DATABASE_URL': self.engine('careertrellis_migration').url.render_as_string(hide_password=False)}):
            from alembic import command
            from alembic.config import Config
            config = Config()
            config.set_main_option('script_location', str(ROOT / 'migrations'))
            # This historical experiment remains bound to its evaluated schema.
            command.upgrade(config, '20260903_0013')
            command.upgrade(config, '20260903_0013')
        self.grants()
        with self.admin.begin() as connection:
            preserved_id = connection.exec_driver_sql("INSERT INTO careertrellis.app_users(workos_user_id, role) "
                "VALUES ('user_overlay_preserved', 'owner') RETURNING id").scalar_one()
            profile_id = connection.execute(text('INSERT INTO careertrellis.profiles(owner_user_id, name) '
                "VALUES (:uid, 'Synthetic preservation fixture') RETURNING id"), {'uid': preserved_id}).scalar_one()
        if not overlay:
            return
        with self.admin.begin() as connection:
            connection.execute(text((Path(__file__).with_name('integration.sql')).read_text(encoding='utf-8')))
        self.schemas.append('ct_admission')
        self.grants()  # Original grant reapplication must not leak the private schema.
        with self.admin.begin() as connection:
            preserved = connection.execute(text('SELECT u.id, u.role, p.id, p.name FROM careertrellis.app_users u '
                'JOIN careertrellis.profiles p ON p.owner_user_id=u.id WHERE u.id=:uid'),
                {'uid': preserved_id}).one()
            if tuple(preserved) != (preserved_id, 'owner', profile_id, 'Synthetic preservation fixture'):
                raise AssertionError('Overlay changed existing synthetic tenant data')
            self.preserved_overlay = True

    def grants(self):
        from apply_postgres_runtime_grants import apply_runtime_grants
        with patch.dict(os.environ, {'CAREERTRELLIS_MIGRATION_DATABASE_URL':
                self.engine('careertrellis_migration').url.render_as_string(hide_password=False)}):
            apply_runtime_grants(ROOT.parent / 'deploy/postgres/grant-runtime.sql')

    def upgrade_identity_sessions(self, target_revision='20260909_0015'):
        """Explicit synthetic identity/TOTP fence; never an operational installer."""
        if target_revision not in ('20260909_0015', '20260915_0016', '20260928_0017', '20260928_0018', '20260929_0019', '20260929_0020'):
            raise ValueError('Unsupported synthetic migration target')
        from alembic import command
        from alembic.config import Config
        for engine in self.engines.values():
            engine.dispose()
        callers=('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker')
        with self.admin.begin() as c:
            previous=c.execute(text('SELECT maintenance,signup_open FROM careertrellis_admission.policy')).one()
            states={role:c.execute(text('SELECT rolcanlogin FROM pg_roles WHERE rolname=:role'),{'role':role}).scalar_one() for role in callers}
            c.exec_driver_sql('UPDATE careertrellis_admission.policy SET maintenance=true,signup_open=false')
            for role in callers: c.exec_driver_sql('ALTER ROLE '+role+' NOLOGIN')
        cfg=Config(); cfg.set_main_option('script_location',str(ROOT/'migrations'))
        cfg.attributes['migration_url']=self.engine('careertrellis_migration').url
        command.upgrade(cfg,target_revision)
        self.grants()
        with self.admin.begin() as c:
            c.execute(text('UPDATE careertrellis_admission.policy SET maintenance=:m,signup_open=:s'),{'m':previous[0],'s':previous[1]})
            for role,login in states.items():
                c.exec_driver_sql('ALTER ROLE '+role+(' LOGIN' if login else ' NOLOGIN'))

    def close(self):
        for engine in self.engines.values():
            engine.dispose()
        try:
            with self.admin.begin() as connection:
                for schema in reversed(self.schemas):
                    connection.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
                for role in self.roles:
                    connection.exec_driver_sql(f'DROP OWNED BY {role}')
                for role in reversed(self.roles):
                    connection.exec_driver_sql(f'DROP ROLE {role}')
        finally:
            self.admin.dispose()

    def policy(self, mode='beta', cap=3, ttl=600):
        with self.admin.begin() as connection:
            connection.exec_driver_sql('SELECT ct_admission.lock_lifecycle()')
            connection.execute(text('UPDATE ct_admission.policy SET mode=:mode, cap=:cap, ttl=:ttl'),
                               {'mode': mode, 'cap': cap, 'ttl': ttl})

    def operator(self, operation, subject):
        # No protected-file reader or interactive identity prompt is invoked.
        with patch.dict(os.environ, {'CAREERTRELLIS_CONFIG_READ_ONLY': '1'}):
            from pilot_users import perform_operation
        engine = self.engine('careertrellis_migration')

        class HistoricalOperatorConnection:
            # Preserve this predecessor-overlay experiment after the production
            # operator gains lifecycle SQL. This shim is not production evidence:
            # the outer transaction below already tests the original ct_ gate.
            def scalar(inner, statement, parameters=None):
                if 'careertrellis_admission.operator_allowed' in str(statement):
                    return True
                return inner.connection.scalar(statement, parameters)

            def exec_driver_sql(inner, statement):
                if statement == 'SELECT careertrellis_admission.invalidate_flows()':
                    return inner.connection.exec_driver_sql('SELECT 1')
                return inner.connection.exec_driver_sql(statement)

            def execute(inner, statement, parameters=None):
                return inner.connection.execute(statement, parameters)

        class CoordinatedTransaction:
            @contextmanager
            def begin(inner):
                with engine.begin() as connection:
                    connection.exec_driver_sql("SET LOCAL lock_timeout = '3s'")
                    connection.exec_driver_sql('SELECT ct_admission.lock_lifecycle()')
                    if operation == 'seed' and not connection.execute(
                            text('SELECT ct_admission.seed_allowed(:subject)'), {'subject': subject}).scalar_one():
                        raise ValueError('admission capacity unavailable')
                    historical = HistoricalOperatorConnection()
                    historical.connection = connection
                    yield historical

        return perform_operation(CoordinatedTransaction(), operation, 'user_synthetic_owner', subject)

    def request_erasure(self, uid):
        with self.engine('careertrellis_runtime').begin() as connection:
            connection.execute(text("SELECT set_config('careertrellis.user_id', :uid, true)"), {'uid': str(uid)})
            return connection.exec_driver_sql('SELECT ct_admission.request_erasure()').scalar_one()

    def claim_erasure(self):
        from uuid import uuid4
        with self.engine('careertrellis_erasure_worker').begin() as connection:
            connection.exec_driver_sql('SELECT ct_admission.lock_lifecycle()')
            return dict(connection.execute(text('SELECT * FROM careertrellis.claim_next_account_erasure(:worker, 300)'),
                                           {'worker': uuid4()}).mappings().one())

    def finalize_erasure(self, claim):
        with self.engine('careertrellis_erasure_worker').begin() as connection:
            return connection.execute(text('SELECT ct_admission.finalize_erasure(:request_id, :owner_user_id, :lease_token)'),
                                      claim).scalar_one()

    def worker_operation(self, operation, claim, object_id=None):
        # All exercised worker transitions take the outer lock before real SQL.
        calls = {
            'batch': 'SELECT * FROM careertrellis.account_erasure_object_batch(:request_id, :owner_user_id, :lease_token, 20)',
            'confirm': 'SELECT careertrellis.confirm_account_erasure_object_deleted(:request_id, :owner_user_id, :lease_token, :object_id)',
            'renew': 'SELECT careertrellis.renew_account_erasure_lease(:request_id, :owner_user_id, :lease_token, 300)',
            'retry': "SELECT careertrellis.retry_account_erasure(:request_id, :owner_user_id, :lease_token, 'object_delete_failed')",
        }
        if operation not in calls:
            raise ValueError('Unknown synthetic worker operation')
        with self.engine('careertrellis_erasure_worker').begin() as connection:
            connection.exec_driver_sql('SELECT ct_admission.lock_lifecycle()')
            deadline = connection.execute(text('SELECT ct_admission.erasure_deadline(:request_id, :owner_user_id, :lease_token)'), claim).scalar_one()
            if deadline is None:
                raise ValueError('Synthetic erasure lease expired')
            result = connection.execute(text(calls[operation]), {**claim, 'object_id': object_id})
            value = [dict(row) for row in result.mappings()] if operation == 'batch' else result.scalar_one()
            if connection.exec_driver_sql('SELECT clock_timestamp()').scalar_one() >= deadline:
                raise ValueError('Synthetic erasure lease expired')
            return value
