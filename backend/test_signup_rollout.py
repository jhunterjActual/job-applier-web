"""Disposable broker/migration/session-v2 rehearsal, never a live installer.

Uses the guarded empty PostgreSQL fixture, real roles/SQL/application lifespan,
and signed synthetic provider responses. All network beyond the loopback test
database and the exact labeled disposable Docker container is prohibited.
"""

import asyncio
import base64
from contextlib import ExitStack
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

import admission_migration_preflight
import app
import config
import postgres_database
import signup_http
import web_auth
from pilot_users import perform_operation
from signup_prototype.integration_fixture import Fixture, ROOT, PASSWORD
from test_provider_session_candidate import SyntheticHTTP, StreamingMockTransport, SETTINGS, GENERATION
from workos_transport_candidate import WorkOSTransportCandidate


ORIGIN = 'https://staging-app.jhunterj.com'
OWNER = 'user_overlay_preserved'
PILOT = 'user_synthetic_pilot'
NEW_USER = 'user_synthetic_rollout'
CONTAINER = 'careertrellis-admission-rollout-20260908'


class SignupRolloutTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if os.name == 'nt':
            previous = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            cls.addClassCleanup(asyncio.set_event_loop_policy, previous)
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    async def asyncSetUp(self):
        url = os.environ.get('CAREERTRELLIS_SIGNUP_ROLLOUT_TEST_URL')
        if not url:
            self.skipTest('Exact empty disposable rollout database required')
        self.fixture = Fixture(url)
        self.addCleanup(self.fixture.close)
        self.fixture.setup(overlay=False)
        self.migration = self.fixture.engine('careertrellis_migration')
        self.config = Config()
        self.config.set_main_option('script_location', str(ROOT / 'migrations'))
        self.bootstrap_sql = (ROOT.parent / 'deploy/postgres/bootstrap-admission-roles.sql').read_text()
        self.bootstrap()
        self.fixture.roles += ['careertrellis_admission_owner', 'careertrellis_admission_broker']
        self.fixture.schemas += ['careertrellis_admission', 'careertrellis_lifecycle']
        self.broker = create_engine(self.fixture.url.set(username='careertrellis_admission_broker', password=PASSWORD),
            poolclass=NullPool, hide_parameters=True, connect_args={'connect_timeout': 2})
        self.addCleanup(self.broker.dispose)

    def query(self, statement, engine=None, **parameters):
        with (engine or self.fixture.admin).begin() as connection:
            result = connection.execute(text(statement), parameters)
            return result.scalar() if result.returns_rows else None

    def bootstrap(self):
        with self.fixture.admin.begin() as connection:
            connection.execute(text(self.bootstrap_sql))

    def quiesce_owned_pools(self):
        for engine in self.fixture.engines.values():
            engine.dispose()
        self.broker.dispose()
        postgres_database.dispose_postgres_engines()

    def migrate(self):
        self.quiesce_owned_pools()
        admission_migration_preflight.check(self.migration)
        with patch.dict(os.environ, {'CAREERTRELLIS_CONFIG_READ_ONLY': '1',
                'CAREERTRELLIS_MIGRATION_DATABASE_URL': self.migration.url.render_as_string(hide_password=False)}):
            command.upgrade(self.config, 'head')
        self.fixture.grants()

    def install_synthetic_login(self):
        # Synthetic test material only. No actual credential installer or
        # protected environment-file writer is called by this rehearsal.
        self.query("SET password_encryption='scram-sha-256'")
        self.query("ALTER ROLE careertrellis_admission_broker LOGIN PASSWORD 'synthetic-integration-only'")

    def enable_v2_closed(self):
        self.query("UPDATE careertrellis_admission.policy SET phase='beta',cap=3,"
            "signup_open=false,adapter_ready=true,minimum_session_version=2,realm=:realm", realm=GENERATION)

    async def test_01_broker_installation_migration_and_next_maintenance(self):
        original = self.query("SELECT row_to_json(p)::text FROM careertrellis.profiles p")
        self.bootstrap()  # Idempotent pre-login bootstrap.
        with self.assertRaises(DBAPIError):
            self.query('SELECT 1', self.broker)
        self.quiesce_owned_pools()
        admission_migration_preflight.check(self.migration)
        with self.fixture.engine('careertrellis_runtime').connect():
            with self.assertRaisesRegex(RuntimeError, 'quiesced'):
                admission_migration_preflight.check(self.migration)
        self.migrate()
        self.migrate()
        self.assertEqual(original, self.query('SELECT row_to_json(p)::text FROM careertrellis.profiles p'))
        self.assertEqual('20260907_0014', self.query('SELECT version_num FROM careertrellis.alembic_version'))
        self.assertTrue(self.query('SELECT careertrellis.admission_lifecycle_ready()', self.fixture.engine('careertrellis_runtime')))
        self.install_synthetic_login()
        self.assertEqual('closed', self.query('SELECT careertrellis_admission.availability()', self.broker)['status'])
        self.assertTrue(self.query("SELECT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole "
            "AND NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls FROM pg_roles WHERE rolname='careertrellis_admission_broker'"))
        self.assertTrue(self.query("SELECT rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname='careertrellis_admission_broker'"))
        for statement in ('SELECT * FROM careertrellis_admission.policy', 'SELECT * FROM careertrellis.app_users',
                          'SET ROLE careertrellis_admission_owner', 'CREATE TABLE public.unexpected(id integer)'):
            with self.subTest(statement=statement), self.assertRaises(DBAPIError):
                self.query(statement, self.broker)
        with self.assertRaises(RuntimeError):
            admission_migration_preflight.check(self.migration)
        with self.assertRaises(DBAPIError):
            self.bootstrap()
        self.enable_v2_closed()
        with self.broker.connect() as existing:
            self.query('ALTER ROLE careertrellis_admission_broker NOLOGIN')
            self.assertEqual(1, existing.scalar(text('SELECT 1')))
            with self.assertRaises(DBAPIError):
                self.query('SELECT 1', self.broker)
            with self.assertRaisesRegex(RuntimeError, 'quiesced'):
                admission_migration_preflight.check(self.migration)
        self.migrate()  # Existing head check: no fabricated forward migration.
        self.assertEqual(2, self.query('SELECT minimum_session_version FROM careertrellis_admission.policy'))
        self.assertEqual(original, self.query('SELECT row_to_json(p)::text FROM careertrellis.profiles p'))
        self.query('ALTER ROLE careertrellis_admission_broker LOGIN')
        self.assertEqual('closed', self.query('SELECT careertrellis_admission.availability()', self.broker)['status'])
        self.assertFalse(self.query('SELECT careertrellis.admission_lifecycle_ready()', self.fixture.engine('careertrellis_runtime')))

    def docker(self, *arguments):
        result = subprocess.run(['docker', *arguments], capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError('Disposable container operation failed; raw output suppressed')
        return result.stdout

    async def test_02_real_hba_allows_only_exact_database_source_and_scram(self):
        details = json.loads(self.docker('inspect', CONTAINER))[0]
        ports = details['NetworkSettings']['Ports'].get('5432/tcp')
        if (details['Config']['Labels'].get('careertrellis.scope') != 'isolated-admission-rollout'
                or not details['HostConfig']['AutoRemove'] or details['Mounts']
                or ports != [{'HostIp': '127.0.0.1', 'HostPort': str(self.fixture.url.port)}]):
            self.fail('Disposable container binding does not match the guarded fixture')
        self.migrate()
        self.install_synthetic_login()
        original_hba = self.query('SHOW hba_file')
        peer = ipaddress.ip_address(str(self.query('SELECT inet_client_addr()')))
        source = f'{peer}/{peer.max_prefixlen}'
        # The first rules are the installation rehearsal. The remaining rules
        # keep this test-only server's other synthetic fixture roles usable.
        self.assertEqual('/var/lib/postgresql/18/docker/pg_hba.conf', original_hba)
        lines = [f'host ct_signup_integration careertrellis_admission_broker {source} scram-sha-256',
            'host all careertrellis_admission_broker all reject',
            'local all careertrellis_admission_broker reject']
        original = bytes(self.query('SELECT pg_read_binary_file(current_setting(\'hba_file\'))'))
        candidate = ('\n'.join(lines) + '\n').encode('ascii') + original

        def write_hba(contents):
            # Docker archive copy cannot see this container's tmpfs. Stream
            # synthetic fixture bytes to the exact guarded file, preserving its
            # existing owner and mode. This never targets a host-mounted file.
            result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'dd',
                'of=' + original_hba, 'status=none'], input=contents, capture_output=True, timeout=15)
            self.assertEqual(0, result.returncode, 'Disposable HBA fixture write failed')
        try:
            # hba_file's path is startup-only; change the exact verified test
            # file contents and reload, never assume changing the path reloaded.
            write_hba(candidate)
            self.query('SELECT pg_reload_conf()')
            await asyncio.sleep(.2)
            self.assertEqual(0, self.query('SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL'))
            self.assertEqual('closed', self.query('SELECT careertrellis_admission.availability()', self.broker)['status'])
            wrong_password = create_engine(self.broker.url.set(password='synthetic-wrong-password'), poolclass=NullPool, hide_parameters=True)
            other_database = create_engine(self.broker.url.set(database='postgres'), poolclass=NullPool, hide_parameters=True)
            try:
                for engine in (wrong_password, other_database):
                    with self.assertRaises(DBAPIError):
                        self.query('SELECT 1', engine)
            finally:
                wrong_password.dispose(); other_database.dispose()
            # An in-container TCP connection has a different source from the
            # Docker Desktop host connection, and must hit the reject rule.
            result = subprocess.run(['docker', 'exec', CONTAINER, 'psql', '-h', '127.0.0.1', '-w',
                '-U', 'careertrellis_admission_broker', '-d', 'ct_signup_integration', '-c', 'SELECT 1'],
                capture_output=True, text=True, timeout=10)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('pg_hba.conf rejects connection', result.stderr)
        finally:
            write_hba(original)
            self.query('SELECT pg_reload_conf()')
            await asyncio.sleep(.2)
            self.assertEqual(original_hba, self.query('SHOW hba_file'))
            self.assertEqual(original, bytes(self.query('SELECT pg_read_binary_file(current_setting(\'hba_file\'))')))

    def environment(self):
        keyring = base64.urlsafe_b64encode(json.dumps({'synthetic': Fernet.generate_key().decode()}).encode()).decode()
        return {
            'CAREERTRELLIS_CONFIG_READ_ONLY': '1', 'CAREERTRELLIS_ENVIRONMENT': 'staging',
            'CAREERTRELLIS_ALLOWED_HOSTS': 'staging-app.jhunterj.com',
            'CAREERTRELLIS_TRUSTED_PROXY_TOKEN': 'synthetic-only-proxy-boundary-token-12345',
            'CAREERTRELLIS_DATA_STORE': 'postgres', 'CAREERTRELLIS_IDENTITY_STORE': 'postgres',
            'CAREERTRELLIS_DATABASE_URL': self.fixture.engine('careertrellis_runtime').url.render_as_string(hide_password=False),
            'CAREERTRELLIS_AUTH_MODE': 'workos', 'WORKOS_API_KEY': SETTINGS.client_secret,
            'WORKOS_CLIENT_ID': SETTINGS.client_id, 'WORKOS_COOKIE_PASSWORD': Fernet.generate_key().decode(),
            'WORKOS_REDIRECT_URI': ORIGIN + '/auth/callback', 'WORKOS_LOGOUT_RETURN_TO': ORIGIN + '/',
            'CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID': OWNER,
            'CAREERTRELLIS_PROVIDER_SECRET_STORE': 'postgres_encrypted',
            'CAREERTRELLIS_PROVIDER_SECRET_ACTIVE_KEY_ID': 'synthetic', 'CAREERTRELLIS_PROVIDER_SECRET_KEYRING_B64': keyring,
            'CAREERTRELLIS_OBJECT_STORE': 'r2_encrypted',
            'CAREERTRELLIS_OBJECT_STORE_ENDPOINT': 'https://' + '0'*32 + '.r2.cloudflarestorage.com',
            'CAREERTRELLIS_OBJECT_STORE_BUCKET': 'synthetic-rollout-only',
            'CAREERTRELLIS_OBJECT_STORE_ACCESS_KEY_ID': 'synthetic-access-key-only',
            'CAREERTRELLIS_OBJECT_STORE_SECRET_ACCESS_KEY': 'synthetic-secret-key-with-no-provider-access',
            'CAREERTRELLIS_OBJECT_STORE_ACTIVE_KEY_ID': 'synthetic', 'CAREERTRELLIS_OBJECT_STORE_KEYRING_B64': keyring,
            'CAREERTRELLIS_AUTH_V2_ENABLED': '0', 'CAREERTRELLIS_AUTH_V2_ISSUER': SETTINGS.issuer,
            'CAREERTRELLIS_AUTH_V2_GENERATION': GENERATION, 'CAREERTRELLIS_AUTH_V2_KEY_ID': 'synthetic-k1',
            'CAREERTRELLIS_AUTH_V2_ENVELOPE_KEY': Fernet.generate_key().decode(),
            'CAREERTRELLIS_AUTH_V2_FLOW_KEY': Fernet.generate_key().decode(), 'CAREERTRELLIS_AUTH_V2_SESSION_SECONDS': '3600',
            'CAREERTRELLIS_ADMISSION_DATABASE_URL': self.broker.url.render_as_string(hide_password=False),
        }

    def identity(self, subject):
        self.http.user = {'id': subject, 'email_verified': True}
        self.http.claims = {'sub': subject, 'sid': 'session_synthetic_' + uuid4().hex}
        self.http.refresh_token = 'synthetic-refresh-' + uuid4().hex

    async def sign_in(self, client, purpose, subject):
        self.identity(subject)
        landing = await client.get('/auth/sign-in')
        self.assertEqual(200, landing.status_code)
        csrf = re.search('name="csrf-token" content="([^"]+)"', landing.text)[1]
        started = await client.post('/auth/start', data={'csrf': csrf, 'purpose': purpose, 'choice': 'same'})
        self.assertEqual(200, started.status_code)
        destination = html.unescape(re.search('name="authorization-url" content="([^"]+)"', started.text)[1])
        state = parse_qs(urlsplit(destination).query)['state'][0]
        result = await client.post('/auth/complete', json={'csrf': csrf, 'state': state, 'code': 'synthetic-code-' + uuid4().hex, 'error': ''})
        self.assertEqual(200, result.status_code)
        self.assertIsNotNone(client.cookies.get(signup_http.COOKIE_NAME))
        return csrf

    async def test_03_actual_lifespan_pilot_v2_profile_closed_login_and_return_closed(self):
        self.migrate()
        perform_operation(self.migration, 'seed', OWNER, PILOT)
        preserved = self.query('SELECT row_to_json(p)::text FROM careertrellis.profiles p ORDER BY id LIMIT 1')
        self.install_synthetic_login()
        self.http = SyntheticHTTP(self.signing_key)
        with tempfile.TemporaryDirectory(prefix='ct-rollout-app-') as temporary, ExitStack() as stack:
            root = Path(temporary)
            for name in ('DATA_DIR', 'UPLOAD_DIR', 'OUTPUT_DIR'):
                stack.enter_context(patch.object(config, name, root))
            stack.enter_context(patch.dict(os.environ, self.environment()))
            stack.enter_context(patch.object(app, 'initialize_analytics'))
            stack.enter_context(patch.object(app, 'shutdown_analytics'))
            # Validate the real object configuration, but substitute external
            # reachability; no real object, AI, WorkOS or analytics request.
            stack.enter_context(patch.object(app, 'check_object_store_ready'))
            provider_factory = stack.enter_context(patch.object(signup_http, 'WorkOSTransportCandidate',
                side_effect=lambda settings: WorkOSTransportCandidate(settings, transport=StreamingMockTransport(self.http), rotation_seconds=0)))
            legacy = stack.enter_context(patch.object(app, 'authenticate_session', return_value=web_auth.SessionAuthentication(
                'authenticated', workos_user_id=PILOT, session_id='session_synthetic_legacy')))
            headers = {'Origin': ORIGIN, app.TRUSTED_PROXY_TOKEN_HEADER: os.environ['CAREERTRELLIS_TRUSTED_PROXY_TOKEN']}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app.app), base_url=ORIGIN, headers=headers) as client:
                client.cookies.set(web_auth.SESSION_COOKIE_NAME, 'synthetic-legacy-proof', domain='staging-app.jhunterj.com', path='/')
                async with app.lifespan(app.app):
                    self.assertIsNone(app.app.state.signup_runtime)
                    self.assertEqual(200, (await client.get('/health/ready')).status_code)
                    self.assertEqual(200, (await client.get('/api/version')).status_code)
                    self.assertEqual(200, (await client.get('/')).status_code)
                    self.assertTrue((await client.get('/api/session')).json()['authenticated'])
                    provider_factory.assert_not_called()
                with patch.dict(os.environ, {'CAREERTRELLIS_AUTH_V2_ENABLED': '1'}):
                    with self.assertRaises(web_auth.AuthenticationConfigurationError):
                        async with app.lifespan(app.app):
                            self.fail('Default database policy must block enabled startup')
                self.enable_v2_closed()
                async with app.lifespan(app.app):
                    self.assertEqual(503, (await client.get('/health/ready')).status_code)
                    self.assertEqual(503, (await client.get('/api/version')).status_code)
                os.environ['CAREERTRELLIS_AUTH_V2_ENABLED'] = '1'
                async with app.lifespan(app.app):
                    legacy.reset_mock()
                    self.assertEqual(200, (await client.get('/health/ready')).status_code)
                    self.assertEqual(401, (await client.get('/api/version')).status_code)
                    legacy.assert_not_called()
                    csrf = await self.sign_in(client, 'login', PILOT)
                    self.assertEqual(200, (await client.get('/api/version')).status_code)
                    self.assertEqual(200, (await client.get('/')).status_code)
                    profile = {'name': 'Synthetic pilot profile', 'email': 'synthetic@example.test', 'phone': '',
                        'github': '', 'linkedin': '', 'website': '', 'base_resume_text': 'Synthetic resume evidence only.'}
                    saved = await client.post('/api/profile', json=profile)
                    self.assertEqual(200, saved.status_code)
                    self.assertTrue(saved.json()['success'])
                    self.assertEqual(profile['name'], (await client.get('/api/profile')).json()['name'])
                    self.assertEqual(preserved, self.query('SELECT row_to_json(p)::text FROM careertrellis.profiles p ORDER BY id LIMIT 1'))
                    existing_count = self.query('SELECT count(*) FROM careertrellis.app_users')
                    self.query('UPDATE careertrellis_admission.policy SET signup_open=true')
                    await self.sign_in(client, 'signup', NEW_USER)
                    self.assertEqual(existing_count + 1, self.query('SELECT count(*) FROM careertrellis.app_users'))
                    admitted = self.query('SELECT id FROM careertrellis.app_users WHERE workos_user_id=:subject', subject=NEW_USER)
                    self.assertEqual(1, self.query('SELECT count(*) FROM careertrellis.profiles WHERE owner_user_id=:uid', uid=admitted))
                    new_profile = {**profile, 'name': 'Synthetic newly admitted profile'}
                    self.assertEqual(200, (await client.post('/api/profile', json=new_profile)).status_code)
                    self.assertEqual(new_profile['name'], (await client.get('/api/profile')).json()['name'])
                    # Closing beta preserves admitted accounts and v2 sessions.
                    self.query('UPDATE careertrellis_admission.policy SET signup_open=false,generation=generation+1')
                    self.assertEqual(200, (await client.get('/api/version')).status_code)
                    await self.sign_in(client, 'login', PILOT)
                    self.assertEqual(profile['name'], (await client.get('/api/profile')).json()['name'])
                    self.assertEqual(2, self.query('SELECT minimum_session_version FROM careertrellis_admission.policy'))
                self.assertIsNone(app.app.state.signup_runtime)
                os.environ['CAREERTRELLIS_AUTH_V2_ENABLED'] = '0'
                async with app.lifespan(app.app):
                    self.assertEqual(503, (await client.get('/api/version')).status_code)
            self.assertEqual(existing_count + 1, self.query('SELECT count(*) FROM careertrellis.app_users'))

    async def test_04_operator_profile_atomic_idempotent_repair_and_preservation(self):
        from profile_repository import PostgreSQLProfileRepository
        self.migrate()
        perform_operation(self.migration, 'seed', OWNER, PILOT)
        uid = self.query('SELECT id FROM careertrellis.app_users WHERE workos_user_id=:subject', subject=PILOT)
        self.assertEqual(1, self.query('SELECT count(*) FROM careertrellis.profiles WHERE owner_user_id=:uid', uid=uid))
        repository = PostgreSQLProfileRepository(uid, engine=self.fixture.engine('careertrellis_runtime'))
        repository.update_profile({**repository.get_profile(), 'name': 'Synthetic first save'})
        before = self.query('SELECT row_to_json(p)::text FROM careertrellis.profiles p WHERE owner_user_id=:uid', uid=uid)
        self.assertFalse(perform_operation(self.migration, 'seed', OWNER, PILOT).changed)
        self.assertEqual(before, self.query('SELECT row_to_json(p)::text FROM careertrellis.profiles p WHERE owner_user_id=:uid', uid=uid))
        self.query('DELETE FROM careertrellis.profiles WHERE owner_user_id=:uid', uid=uid)  # Synthetic old missing-profile fixture.
        self.assertTrue(perform_operation(self.migration, 'seed', OWNER, PILOT).changed)
        self.assertEqual(1, self.query('SELECT count(*) FROM careertrellis.profiles WHERE owner_user_id=:uid', uid=uid))
        self.query('ALTER TABLE careertrellis.profiles ADD CONSTRAINT synthetic_profile_failure CHECK(owner_user_id=' + str(uid) + ') NOT VALID')
        with self.assertRaises(DBAPIError):
            perform_operation(self.migration, 'seed', OWNER, NEW_USER)
        self.assertEqual(0, self.query('SELECT count(*) FROM careertrellis.app_users WHERE workos_user_id=:subject', subject=NEW_USER))

    async def test_05_worker_web_secret_rejection(self):
        minimal = {'CAREERTRELLIS_ENVIRONMENT': 'staging', 'CAREERTRELLIS_SEARCH_WORKER_ENABLED': 'true',
            'CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED': 'false'}
        with patch.dict(os.environ, minimal, clear=True):
            for name in ('CAREERTRELLIS_ADMISSION_DATABASE_URL', 'CAREERTRELLIS_AUTH_V2_ENVELOPE_KEY', 'CAREERTRELLIS_AUTH_V2_FLOW_KEY'):
                with patch.dict(os.environ, {name: 'synthetic-unexpected-web-setting'}):
                    with self.assertRaises(config.RuntimeConfigurationError):
                        config.validate_worker_runtime_configuration()
                    with patch.dict(os.environ, {'CAREERTRELLIS_SEARCH_WORKER_ENABLED': ''}):
                        with self.assertRaises(config.RuntimeConfigurationError):
                            config.validate_account_erasure_worker_configuration()

    async def test_06_worker_entrypoints_continue_at_session_v2_minimum(self):
        import worker
        import account_erasure_worker
        from account_erasure_repository import PostgreSQLAccountErasureRepository
        from search_run_repository import PostgreSQLSearchRunRepository

        self.migrate()
        perform_operation(self.migration, 'seed', OWNER, PILOT)
        uid = self.query('SELECT id FROM careertrellis.app_users WHERE workos_user_id=:subject', subject=PILOT)
        self.enable_v2_closed()
        runtime = self.fixture.engine('careertrellis_runtime')
        repository = PostgreSQLSearchRunRepository(uid, engine=runtime)
        run_id = repository.enqueue(str(uuid4()), 'synthetic', '')
        repository.publish_prepared(run_id, 'synthetic', '')
        keyring = base64.urlsafe_b64encode(json.dumps({'synthetic': Fernet.generate_key().decode()}).encode()).decode()
        search_environment = {
            'CAREERTRELLIS_CONFIG_READ_ONLY': '1', 'CAREERTRELLIS_ENVIRONMENT': 'staging',
            'CAREERTRELLIS_DATA_STORE': 'postgres', 'CAREERTRELLIS_SEARCH_WORKER_ENABLED': 'true',
            'CAREERTRELLIS_DATABASE_URL': self.fixture.engine('careertrellis_worker').url.render_as_string(hide_password=False),
            'CAREERTRELLIS_PROVIDER_SECRET_STORE': 'postgres_encrypted',
            'CAREERTRELLIS_PROVIDER_SECRET_ACTIVE_KEY_ID': 'synthetic', 'CAREERTRELLIS_PROVIDER_SECRET_KEYRING_B64': keyring,
        }
        with tempfile.TemporaryDirectory(prefix='ct-rollout-workers-') as temporary, ExitStack() as stack:
            root = Path(temporary)
            stack.enter_context(patch.object(config, 'DATA_DIR', root))
            # Main-process tests must not install persistent signal handlers.
            stack.enter_context(patch.object(worker.signal, 'signal'))
            stack.enter_context(patch.object(worker, 'STOP_EVENT', Event()))
            stack.enter_context(patch.object(account_erasure_worker, 'STOP_EVENT', Event()))
            with patch.dict(os.environ, search_environment, clear=True), patch(
                    'search_run_execution.run_job_search_and_matching',
                    return_value={'success': True, 'jobs_found': 0, 'jobs_matched': 0}) as provider:
                try:
                    self.assertEqual(0, worker.run(once=True))
                    provider.assert_called_once()
                    self.assertEqual('succeeded', self.query('SELECT status FROM careertrellis.search_runs WHERE id=:rid', rid=run_id))
                    self.assertEqual(0, worker.run(once=True))
                    self.assertEqual('idle', json.loads((root / 'worker-heartbeat.json').read_text())['status'])
                finally:
                    postgres_database.dispose_postgres_engines()
            oid = uuid4()
            self.query("INSERT INTO careertrellis.stored_objects(id,owner_user_id,object_key,content_type,plaintext_size,sha256,wrapped_data_key) VALUES(:oid,:uid,:key,'application/pdf',1,:sha,:wrapped)",
                oid=oid, uid=uid, key='v1/' + oid.hex, sha=b'a'*32, wrapped=b'synthetic')
            PostgreSQLAccountErasureRepository(uid, engine=runtime).request_erasure()
            erasure_environment = {
                'CAREERTRELLIS_CONFIG_READ_ONLY': '1', 'CAREERTRELLIS_ENVIRONMENT': 'staging',
                'CAREERTRELLIS_DATA_STORE': 'postgres', 'CAREERTRELLIS_OBJECT_STORE': 'r2_encrypted',
                'CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED': 'true',
                'CAREERTRELLIS_DATABASE_URL': self.fixture.engine('careertrellis_erasure_worker').url.render_as_string(hide_password=False),
            }
            deletion = Mock()
            with patch.dict(os.environ, erasure_environment, clear=True), patch.object(
                    account_erasure_worker, 'runtime_account_erasure_delete_backend', return_value=deletion):
                try:
                    self.assertEqual(0, account_erasure_worker.run(once=True))
                    self.assertEqual('completed', json.loads((root / 'account-erasure-worker-heartbeat.json').read_text())['status'])
                    deletion.delete.assert_called_once_with('v1/' + oid.hex)
                finally:
                    postgres_database.dispose_postgres_engines()
        self.assertEqual(0, self.query('SELECT count(*) FROM careertrellis.app_users WHERE id=:uid', uid=uid))
        self.assertEqual(1, self.query('SELECT count(*) FROM careertrellis.app_users WHERE workos_user_id=:subject', subject=OWNER))
        self.assertEqual(2, self.query('SELECT minimum_session_version FROM careertrellis_admission.policy'))
        self.assertFalse(self.query('SELECT signup_open FROM careertrellis_admission.policy'))

    async def test_07_actual_maintenance_fence_receipt_and_policy_preservation(self):
        import admission_operations as ops
        self.migrate()
        self.install_synthetic_login()
        self.enable_v2_closed()
        state = {}
        owner = self

        class Database:
            def query(self, statement):
                with owner.fixture.admin.begin() as connection:
                    result = connection.exec_driver_sql(statement)
                    return result.scalar() if result.returns_rows else None

        def write(path, value):
            state[str(path)] = ops.encoded(value)

        db = Database()
        operation, target = str(uuid4()), 'a'*40
        boot = Path('/proc/sys/kernel/random/boot_id')
        original_read = Path.read_text
        with patch.object(ops, 'state_write', side_effect=write), \
                patch.object(ops, 'read_protected', side_effect=lambda path: state[str(path)]), \
                patch.object(Path, 'read_text', lambda path, *a, **kw: 'synthetic-boot' if path == boot else original_read(path, *a, **kw)):
            self.quiesce_owned_pools()
            before, role_state = ops.policy(db), ops.roles(db)
            # A live session survives NOLOGIN; begin must retain the fence and
            # decline a quiesced receipt rather than terminating that session.
            with self.broker.connect():
                with self.assertRaises(DBAPIError):
                    ops.begin_maintenance(db, operation, target)
            self.assertEqual('fenced', json.loads(state[str(ops.STATE / ('maintenance-' + operation + '.json'))])['phase'])
            for role in ops.CALLERS:
                engine = self.broker if role == ops.BROKER else self.fixture.engine(role)
                with self.assertRaises(DBAPIError):
                    self.query('SELECT 1', engine)
            # An explicit fresh operator attempt cannot overwrite an unresolved
            # maintenance fence. No session or service is killed by this code.
            with self.assertRaises(ops.OperationError):
                ops.begin_maintenance(db, str(uuid4()), target)
            self.quiesce_owned_pools()
            ops.quiesce_maintenance(db, operation)
            self.assertEqual(dict.fromkeys(ops.CALLERS, False), ops.roles(db))
            # Actual coordinator runs real preflight/Alembic/grants under the
            # fence; only native files/service inventory are substituted here.
            state[str(ops.STATE / 'database-quiesced.json')] = ops.encoded(
                {'phase': 'quiesced', 'operation': operation, 'target': target})
            migration_file = ops.ROOT / 'postgres-migration.env'
            state[str(migration_file)] = ('CAREERTRELLIS_MIGRATION_DATABASE_URL=' +
                self.migration.url.render_as_string(hide_password=False) + '\n').encode()
            state[str(ops.ROOT / 'postgres-runtime.env')] = ('CAREERTRELLIS_DATABASE_URL=' +
                self.fixture.engine('careertrellis_runtime').url.render_as_string(hide_password=False) + '\n').encode()
            class Release:
                name = target
                def __truediv__(self, suffix):
                    return ROOT.parent / suffix
            with patch.object(ops, 'stopped_services'), patch.object(ops, 'check_worker_delivery'), \
                    patch.object(ops, 'write_protected', side_effect=lambda path, raw: state.__setitem__(str(path), raw)):
                acknowledgement = ops.migrate_web(operation, target, Release(), database='ct_signup_integration')
            acknowledgement = {'phase': 'migrated', 'operation': operation, 'target': target, 'head': '20260907_0014'}
            with self.assertRaises(ops.OperationError):
                ops.finish_maintenance(db, operation, {**acknowledgement, 'operation': str(uuid4())})
            self.assertTrue(ops.policy(db)['maintenance'])
            self.quiesce_owned_pools()
            ops.finish_maintenance(db, operation, acknowledgement)
            self.assertEqual(before, ops.policy(db))
            self.assertEqual(role_state, ops.roles(db))
            with self.assertRaises(ops.OperationError):
                ops.finish_maintenance(db, operation, acknowledgement)

    async def test_08_web_installer_mandatory_broker_proof_and_default_off_keys(self):
        import admission_operations as ops
        self.migrate()
        password = 's'*64
        with self.fixture.admin.begin() as connection:
            connection.exec_driver_sql('ALTER ROLE careertrellis_admission_broker LOGIN PASSWORD ' + ops.literal(ops.scram(password)))
        engine = create_engine(self.broker.url.set(password=password), poolclass=NullPool, hide_parameters=True)
        self.addCleanup(engine.dispose)
        material = {'version': 1, 'password': password, 'database_host': '10.70.0.2', 'transport': 'wireguard'}
        metadata = {'issuer': SETTINGS.issuer, 'generation': GENERATION, 'key_id': 'synthetic', 'session_seconds': 3600}
        cookie_key = Fernet.generate_key().decode()
        files = {str(ops.ROOT / name): b'CAREERTRELLIS_ENVIRONMENT=staging\n' for name in ops.WORKER_FILES}
        files.update({str(ops.ROOT / 'staging.env'): (
            'WORKOS_CLIENT_ID=' + SETTINGS.client_id + '\nWORKOS_API_KEY=' + SETTINGS.client_secret +
            '\nWORKOS_COOKIE_PASSWORD=' + cookie_key + '\n').encode(),
            str(ops.ROOT / 'object-storage.env'): b'CAREERTRELLIS_OBJECT_STORE=r2_encrypted\n',
            str(ops.ROOT / 'admission-web.json'): ops.encoded(metadata),
            str(ops.ROOT / 'postgres-runtime.env'): b'CAREERTRELLIS_DATABASE_URL=postgresql+psycopg://careertrellis_runtime:synthetic@10.70.0.2:5432/careertrellis\n',
            str(ops.BUNDLE): b'synthetic-ciphertext'})
        def connect(url, **kwargs):
            self.assertEqual(ops.BROKER, url.username)
            self.assertEqual(password, url.password)
            self.assertEqual('10.70.0.2', url.host)
            self.assertEqual('careertrellis', url.database)
            return engine  # Only physical routing differs for this guarded fixture.
        with patch.object(ops, 'read_protected', side_effect=lambda path: files[str(path)]), \
                patch.object(ops, 'write_protected') as write, patch.object(ops, 'stopped_services'), \
                patch('sqlalchemy.create_engine', side_effect=connect):
            ops.install_web(lambda raw: ops.encoded(material))
            raw = write.call_args.args[1]
            values = ops.assignments(raw)
            self.assertEqual('0', values['CAREERTRELLIS_AUTH_V2_ENABLED'])
            self.assertEqual(metadata['issuer'], values['CAREERTRELLIS_AUTH_V2_ISSUER'])
            self.assertEqual(3, len({cookie_key, values['CAREERTRELLIS_AUTH_V2_ENVELOPE_KEY'], values['CAREERTRELLIS_AUTH_V2_FLOW_KEY']}))
            write.reset_mock()
            self.query('ALTER ROLE careertrellis_admission_broker NOLOGIN')
            with self.assertRaises(DBAPIError):
                ops.install_web(lambda raw: ops.encoded(material))
            write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
