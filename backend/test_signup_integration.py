"""Synthetic-only real-schema integration. Never run against a shared server."""

from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import multiprocessing
import os
import re
import secrets
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from signup_prototype.integration import (
    AdmissionStore, Flow, IntegrationAdapter, SyntheticProvider, REALM, digest, isolated_engine,
    MappingUnavailable, MAX_COOKIE_VALUE, packed,
)
from signup_prototype.integration_fixture import Fixture


def competing_process_claim(raw_url, flow_data, code):
    engine = create_engine(raw_url, hide_parameters=True)
    try:
        store = AdmissionStore(engine, engine)
        return store.claim(Flow(**flow_data), code, str(uuid4()))['status']
    finally:
        engine.dispose()


class IntegrationBoundaries(unittest.TestCase):
    def test_refuses_other_databases_and_query_overrides_before_connect(self):
        for url in ('postgresql+psycopg://127.0.0.1/careertrellis',
                    'postgresql+psycopg://example.test/ct_signup_integration',
                    'postgresql+psycopg://127.0.0.1/ct_signup_integration?host=example.test', 'sqlite://'):
            engine = create_engine(url)
            try:
                with self.assertRaisesRegex(ValueError, 'isolated'):
                    isolated_engine(engine)
            finally:
                engine.dispose()

    def test_refuses_real_provider(self):
        with self.assertRaisesRegex(ValueError, 'synthetic'):
            IntegrationAdapter(None, object(), Fernet.generate_key())


@unittest.skipUnless(os.environ.get('CAREERTRELLIS_SIGNUP_INTEGRATION_URL'), 'Dedicated integration URL required')
class FixtureIsolationTests(unittest.TestCase):
    def test_collision_preserves_existing_role(self):
        fixture = Fixture(os.environ['CAREERTRELLIS_SIGNUP_INTEGRATION_URL'])
        with fixture.admin.begin() as connection:
            connection.exec_driver_sql('CREATE ROLE ct_admission_owner NOLOGIN')
        try:
            with self.assertRaisesRegex(ValueError, 'collision'):
                fixture.setup()
            fixture.close()
            with fixture.admin.begin() as connection:
                self.assertTrue(connection.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ct_admission_owner')").scalar_one())
        finally:
            with fixture.admin.begin() as connection:
                connection.exec_driver_sql('DROP ROLE ct_admission_owner')
            fixture.admin.dispose()

    def test_foreign_schema_refusal_does_not_remove_it(self):
        fixture = Fixture(os.environ['CAREERTRELLIS_SIGNUP_INTEGRATION_URL'])
        with fixture.admin.begin() as connection:
            connection.exec_driver_sql('CREATE SCHEMA synthetic_preexisting')
        try:
            with self.assertRaisesRegex(ValueError, 'Empty disposable'):
                fixture.setup()
            fixture.close()
            with fixture.admin.begin() as connection:
                self.assertIsNotNone(connection.exec_driver_sql("SELECT to_regnamespace('synthetic_preexisting')").scalar_one())
        finally:
            with fixture.admin.begin() as connection:
                connection.exec_driver_sql('DROP SCHEMA synthetic_preexisting')
            fixture.admin.dispose()

    def test_partial_migration_failure_cleans_only_created_objects(self):
        fixture = Fixture(os.environ['CAREERTRELLIS_SIGNUP_INTEGRATION_URL'])
        with patch('alembic.command.upgrade', side_effect=RuntimeError('synthetic migration failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic'):
                fixture.setup()
        fixture.close()
        with fixture.admin.begin() as connection:
            self.assertIsNone(connection.exec_driver_sql("SELECT to_regnamespace('careertrellis')").scalar_one())
            self.assertEqual(0, connection.exec_driver_sql("SELECT count(*) FROM pg_roles WHERE rolname='careertrellis_migration'").scalar_one())
        fixture.admin.dispose()


class SignupIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get('CAREERTRELLIS_SIGNUP_INTEGRATION_URL', '')
        if not url:
            raise unittest.SkipTest('Dedicated isolated integration URL is not configured')
        cls.fixture = Fixture(url)
        cls.addClassCleanup(cls.fixture.close)
        cls.fixture.setup()
        cls.admin = cls.fixture.admin
        cls.broker = cls.fixture.engine('ct_admission_broker')
        cls.runtime = cls.fixture.engine('careertrellis_runtime')

    def query(self, sql, **params):
        with self.admin.begin() as connection:
            result = connection.execute(text(sql), params)
            return result.scalar() if result.returns_rows else None

    def setUp(self):
        self.query('TRUNCATE careertrellis.app_users CASCADE')
        self.query("UPDATE ct_admission.policy SET mode='closed', cap=3, ttl=600, generation=0, realm=:realm", realm=REALM)
        self.owner = self.query("INSERT INTO careertrellis.app_users(workos_user_id, role) "
                               "VALUES ('user_synthetic_owner', 'owner') RETURNING id")
        self.query('INSERT INTO careertrellis.profiles(owner_user_id) VALUES (:uid)', uid=self.owner)
        self.fixture.policy(cap=3)
        self.key = Fernet.generate_key()
        self.provider = SyntheticProvider()
        self.store = AdmissionStore(self.broker, self.runtime)
        self.adapter = IntegrationAdapter(self.store, self.provider, self.key)
        self.browser = secrets.token_urlsafe(32)

    def start(self, purpose='signup', browser=None):
        browser = browser or self.browser
        started = self.adapter.begin(browser, purpose)
        self.assertEqual('reserved', started.status)
        return started

    def finish(self, started, subject='user_synthetic_new', browser=None, **identity):
        code = self.provider.issue(started.state, subject, **identity)
        return self.adapter.complete(started.cookie, browser or self.browser, started.state, code), code

    def uid(self, subject='user_synthetic_new'):
        return self.query('SELECT id FROM careertrellis.app_users WHERE workos_user_id=:subject', subject=subject)

    def count(self):
        return self.query('SELECT count(*) FROM careertrellis.app_users')

    def expire(self, started):
        flow = self.adapter.open_flow(started.cookie, self.browser)
        self.query('UPDATE ct_admission.flows SET expires=clock_timestamp() WHERE id=:rid', rid=flow.id)

    def test_closed_default_open_full_and_existing_login_independence(self):
        self.fixture.policy('closed', 1)
        self.assertEqual('closed', self.adapter.begin(self.browser).status)
        login, _ = self.finish(self.start('login'), 'user_synthetic_owner')
        self.assertEqual('login', login.status)
        self.assertEqual('authenticated', self.adapter.authenticate(login.cookie).status)
        self.fixture.policy('beta', 1)
        self.assertEqual('full', self.store.availability()['status'])
        self.assertEqual('reserved', self.adapter.begin(self.browser, 'login').status)
        self.fixture.policy('open', 1)
        self.assertEqual('reserved', self.adapter.begin(self.browser).status)

    def test_new_account_profile_and_first_save_are_real_and_atomic(self):
        self.assertTrue(self.fixture.preserved_overlay)
        result, _ = self.finish(self.start())
        self.assertEqual('activated', result.status)
        uid = self.uid()
        self.assertEqual('user', self.query('SELECT role FROM careertrellis.app_users WHERE id=:uid', uid=uid))
        self.assertEqual(1, self.query('SELECT count(*) FROM careertrellis.profiles WHERE owner_user_id=:uid', uid=uid))
        with patch.dict(os.environ, {'CAREERTRELLIS_CONFIG_READ_ONLY': '1'}):
            from profile_repository import PostgreSQLProfileRepository
        repository = PostgreSQLProfileRepository(uid, self.runtime)
        self.assertIsNone(repository.get_profile()['name'])
        repository.update_profile({'name': 'Synthetic Candidate', 'ai_provider': 'gemini',
            'ai_model': 'gemini-2.5-flash', 'maps_provider': 'openstreetmap',
            'prefer_us_headquarters': False, 'interface_language': 'en'})
        self.assertEqual('Synthetic Candidate', repository.get_profile()['name'])
        self.assertIsNone(self.query('SELECT name FROM careertrellis.profiles WHERE owner_user_id=:uid', uid=self.owner))
        self.assertEqual('authenticated', self.adapter.authenticate(result.cookie).status)

    def test_profile_insert_failure_rolls_back_account_and_does_not_issue_session(self):
        self.query("ALTER TABLE careertrellis.profiles ADD CONSTRAINT synthetic_reject CHECK (owner_user_id = %s)" % self.owner)
        try:
            started = self.start()
            result, _ = self.finish(started)
            self.assertEqual('unavailable', result.status)
            self.assertFalse(result.cookie)
            self.assertEqual(1, self.count())
            self.assertEqual('incomplete', self.adapter.result(started.cookie, self.browser).status)
        finally:
            self.query('ALTER TABLE careertrellis.profiles DROP CONSTRAINT synthetic_reject')

    def test_unmapped_login_and_unverified_impersonated_or_wrong_realm_never_admit(self):
        result, _ = self.finish(self.start('login'))
        self.assertEqual('ineligible', result.status)
        for changes in ({'verified': False}, {'impersonated': True}, {'realm': 'other-realm'}):
            result, _ = self.finish(self.start(), **changes)
            self.assertEqual('ineligible', result.status)
            self.assertFalse(result.cookie)
        self.assertEqual(1, self.count())

    def test_existing_identity_signup_does_not_rebind_or_consume_extra_account(self):
        self.finish(self.start())
        uid = self.uid()
        result, _ = self.finish(self.start())
        self.assertEqual('existing', result.status)
        self.assertFalse(result.cookie)
        self.assertEqual(uid, self.uid())
        self.assertEqual(2, self.count())

    def test_owner_subject_never_becomes_ordinary_user(self):
        result, _ = self.finish(self.start(), 'user_synthetic_owner')
        self.assertEqual('ineligible', result.status)
        self.assertEqual('owner', self.query('SELECT role FROM careertrellis.app_users WHERE id=:uid', uid=self.owner))

    def test_tampered_flow_wrong_browser_or_state_never_exchanges(self):
        started = self.start()
        code = self.provider.issue(started.state)
        for cookie, browser, state in ((started.cookie, secrets.token_urlsafe(32), started.state),
                (started.cookie, self.browser, secrets.token_urlsafe(32)),
                ('corrupt', self.browser, started.state)):
            self.assertEqual('invalid', self.adapter.complete(cookie, browser, state, code).status)
        self.assertEqual(0, self.provider.exchanges)

    def test_last_slot_concurrent_signup_and_real_operator_seed(self):
        self.fixture.policy(cap=2)
        barrier = threading.Barrier(2)
        def signup():
            barrier.wait()
            return self.adapter.begin(self.browser).status
        def seed():
            barrier.wait()
            try:
                return self.fixture.operator('seed', 'user_seed_racer').result
            except ValueError:
                return 'full'
        with ThreadPoolExecutor(2) as pool:
            a, b = pool.submit(signup), pool.submit(seed)
            statuses = [a.result(10), b.result(10)]
        self.assertEqual(1, sum(s in ('reserved', 'created') for s in statuses))
        self.assertEqual('full', self.store.availability()['status'])

    def test_eight_parallel_reservations_only_one_last_slot(self):
        self.fixture.policy(cap=2)
        barrier = threading.Barrier(8)
        def run(_):
            barrier.wait()
            return self.adapter.begin(secrets.token_urlsafe(32)).status
        with ThreadPoolExecutor(8) as pool:
            statuses = list(pool.map(run, range(8)))
        self.assertEqual(1, statuses.count('reserved'))
        self.assertEqual(7, statuses.count('full'))

    def test_same_subject_two_flows_creates_one_account(self):
        starts = [self.start(), self.start()]
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda s: self.finish(s)[0].status, starts))
        self.assertCountEqual(['activated', 'existing'], results)
        self.assertEqual(2, self.count())

    def test_multiprocess_callback_claim_is_durable_and_never_reclaimed(self):
        started = self.start()
        flow = self.adapter.open_flow(started.cookie, self.browser)
        url = self.broker.url.render_as_string(hide_password=False)
        with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context('spawn')) as pool:
            calls = [pool.submit(competing_process_claim, url, flow.__dict__, 'synthetic-code') for _ in range(2)]
            statuses = [c.result(30) for c in calls]
        self.assertCountEqual(['exchange', 'incomplete'], statuses)
        self.assertEqual('incomplete', self.store.claim(flow, 'synthetic-code', str(uuid4()))['status'])

    def test_concurrent_duplicate_exchange_once_and_terminal_no_cookie(self):
        started = self.start()
        code = self.provider.issue(started.state)
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda _: self.adapter.complete(started.cookie, self.browser, started.state, code), range(4)))
        self.assertEqual(1, self.provider.exchanges)
        self.assertEqual(1, sum(bool(r.cookie) for r in results))
        duplicate = self.adapter.complete(started.cookie, self.browser, started.state, code)
        self.assertEqual('activated', duplicate.status)
        self.assertFalse(duplicate.cookie)
        self.assertEqual('invalid', self.adapter.complete(started.cookie, self.browser, started.state, 'different-code').status)

    def test_provider_failure_restart_cannot_reexchange_consumed_code(self):
        started = self.start()
        def fail():
            raise RuntimeError('synthetic-private-diagnostic')
        self.provider.before_exchange = fail
        result, code = self.finish(started)
        self.assertEqual('incomplete', result.status)
        restarted = IntegrationAdapter(AdmissionStore(self.broker, self.runtime), self.provider, self.key)
        self.assertEqual('incomplete', restarted.complete(started.cookie, self.browser, started.state, code).status)
        self.assertEqual(1, self.provider.exchanges)
        self.assertEqual(1, self.count())

    def test_commit_acknowledgement_loss_recovers_by_fresh_login(self):
        started = self.start()
        original = self.broker.begin
        @contextmanager
        def uncertain():
            with original() as connection:
                yield connection
            raise OperationalError('synthetic private SQL', {}, Exception('synthetic lost acknowledgement'))
        original_finish = self.store.finish
        def finish(*args):
            with patch.object(self.broker, 'begin', uncertain):
                return original_finish(*args)
        with patch.object(self.store, 'finish', finish):
            result, code = self.finish(started)
        self.assertEqual('unavailable', result.status)
        self.assertFalse(result.cookie)
        self.assertEqual(2, self.count())
        duplicate = self.adapter.complete(started.cookie, self.browser, started.state, code)
        self.assertEqual('activated', duplicate.status)
        self.assertFalse(duplicate.cookie)
        login, _ = self.finish(self.start('login'))
        self.assertEqual('login', login.status)
        self.assertEqual('authenticated', self.adapter.authenticate(login.cookie).status)

    def test_lost_cookie_response_and_new_pool_does_not_allocate_again(self):
        self.finish(self.start())  # Deliberately discard the completed response.
        uid = self.uid()
        engine = create_engine(self.broker.url, hide_parameters=True)
        try:
            restarted = IntegrationAdapter(AdmissionStore(engine, self.runtime), self.provider, self.key)
            start = restarted.begin(self.browser, 'login')
            code = self.provider.issue(start.state)
            result = restarted.complete(start.cookie, self.browser, start.state, code)
            self.assertEqual('login', result.status)
            self.assertEqual(uid, self.uid())
            self.assertEqual(2, self.count())
        finally:
            engine.dispose()

    def test_expiry_and_closure_during_exchange_fence_activation(self):
        for close in (False, True):
            started = self.start()
            self.provider.before_exchange = (lambda: self.fixture.policy('closed')) if close else (lambda: self.expire(started))
            result, _ = self.finish(started)
            self.assertEqual('closed' if close else 'expired', result.status)
            self.assertFalse(result.cookie)
            self.assertEqual(1, self.count())
            self.fixture.policy()

    def test_cancel_while_provider_is_in_flight_and_late_cancel_after_success(self):
        started = self.start()
        self.provider.before_exchange = lambda: self.adapter.complete(started.cookie, self.browser, started.state, cancelled=True)
        result, _ = self.finish(started)
        self.assertEqual('cancelled', result.status)
        self.provider.before_exchange = lambda: None
        started = self.start()
        result, _ = self.finish(started)
        self.assertEqual('activated', result.status)
        late = self.adapter.complete(started.cookie, self.browser, started.state, cancelled=True)
        self.assertEqual('activated', late.status)
        self.assertEqual(2, self.count())

    def test_disable_reinvite_and_erasure_keep_capacity_until_final_delete(self):
        result, _ = self.finish(self.start())
        uid = self.uid()
        self.fixture.policy(cap=2)
        self.fixture.operator('disable', 'user_synthetic_new')
        self.assertEqual('unauthenticated', self.adapter.authenticate(result.cookie).status)
        self.assertEqual('full', self.store.availability()['status'])
        self.assertEqual('reactivated', self.fixture.operator('reinvite', 'user_synthetic_new').result)
        self.assertEqual('accepted', self.fixture.request_erasure(uid))
        generation = self.query('SELECT generation FROM ct_admission.policy')
        self.assertEqual('accepted', self.fixture.request_erasure(uid))
        self.assertEqual(generation, self.query('SELECT generation FROM ct_admission.policy'))
        self.assertEqual('erasure_pending', self.fixture.operator('reinvite', 'user_synthetic_new').result)
        self.assertEqual('full', self.store.availability()['status'])
        claim = self.fixture.claim_erasure()
        self.assertFalse(self.fixture.finalize_erasure({**claim, 'lease_token': uuid4()}))
        self.assertEqual(generation, self.query('SELECT generation FROM ct_admission.policy'))
        self.assertTrue(self.fixture.finalize_erasure(claim))
        self.assertEqual('available', self.store.availability()['status'])
        self.assertEqual(0, self.query('SELECT count(*) FROM careertrellis.profiles WHERE owner_user_id=:uid', uid=uid))
        self.assertEqual('completed', self.query('SELECT status FROM careertrellis.account_erasure_requests'))
        self.assertIsNone(self.query('SELECT owner_user_id FROM careertrellis.account_erasure_requests'))
        self.assertIsNone(self.query("SELECT local_id FROM ct_admission.flows WHERE state='activated'"))

    def test_erase_during_provider_exchange_invalidates_pending_and_preserves_owner(self):
        self.finish(self.start())
        uid = self.uid()
        started = self.start()
        def erase():
            self.fixture.request_erasure(uid)
            self.assertTrue(self.fixture.finalize_erasure(self.fixture.claim_erasure()))
        self.provider.before_exchange = erase  # Completes without waiting on adapter's DB lock.
        result, _ = self.finish(started, 'user_another_new')
        self.assertEqual('expired', result.status)
        self.assertEqual(1, self.count())
        self.assertEqual(self.owner, self.uid('user_synthetic_owner'))

    def test_old_session_after_recreation_and_refresh_binding_logout_exception(self):
        first, _ = self.finish(self.start())
        old_id = self.uid()
        refreshed = self.adapter.authenticate(first.cookie, refresh=True)
        self.assertEqual('authenticated', refreshed.status)
        self.fixture.request_erasure(old_id)
        self.assertTrue(self.fixture.finalize_erasure(self.fixture.claim_erasure()))
        second, _ = self.finish(self.start())
        self.assertNotEqual(old_id, self.uid())
        for cookie in (first.cookie, refreshed.cookie):
            self.assertEqual('unauthenticated', self.adapter.authenticate(cookie).status)
        self.assertEqual('authenticated', self.adapter.authenticate(second.cookie).status)
        self.assertEqual('signed_out', self.adapter.logout(first.cookie).status)
        self.assertEqual('authenticated', self.adapter.authenticate(second.cookie).status)
        bare = self.adapter._decrypt(second.cookie, 'session', 86400)['provider']
        self.assertEqual('unauthenticated', self.adapter.authenticate(bare).status)

    def test_expired_envelope_and_purge_never_renew_same_flow(self):
        started = self.start()
        flow = self.adapter.open_flow(started.cookie, self.browser)
        self.expire(started)
        self.assertEqual(0, self.query('SELECT ct_admission.purge()'))
        self.assertEqual('expired', self.store.reserve(flow)['status'])
        self.query('UPDATE ct_admission.flows SET deadline=clock_timestamp() WHERE id=:rid', rid=flow.id)
        self.assertEqual(1, self.query('SELECT ct_admission.purge()'))
        with patch.object(self.adapter, 'clock', return_value=flow.deadline + 1):
            self.assertEqual('invalid', self.adapter.result(started.cookie, self.browser).status)

    def test_actual_grant_reapplication_preserves_private_ledger_and_no_runtime_insert(self):
        self.fixture.grants()
        for role in ('ct_admission_broker', 'careertrellis_runtime', 'careertrellis_worker',
                     'careertrellis_erasure_worker', 'ct_admission_outsider'):
            engine = self.fixture.engine(role)
            for sql in ('SELECT * FROM ct_admission.flows', 'UPDATE ct_admission.policy SET cap=999',
                        'SET ROLE ct_admission_owner', 'SELECT ct_admission.purge()',
                        "INSERT INTO careertrellis.app_users(workos_user_id) VALUES ('user_forbidden')"):
                with self.subTest(role=role, sql=sql), self.assertRaises(DBAPIError):
                    with engine.begin() as connection:
                        connection.exec_driver_sql(sql)
        self.assertEqual('available', self.store.availability()['status'])

    def test_runtime_rls_cannot_see_other_account_while_broker_counts_both(self):
        self.finish(self.start())
        uid = self.uid()
        with self.runtime.begin() as connection:
            connection.execute(text("SELECT set_config('careertrellis.workos_user_id', :subject, true)"),
                               {'subject': 'user_synthetic_new'})
            self.assertEqual(1, connection.exec_driver_sql('SELECT count(*) FROM careertrellis.app_users').scalar_one())
            connection.execute(text("SELECT set_config('careertrellis.user_id', :uid, true)"), {'uid': str(uid)})
            self.assertEqual(1, connection.exec_driver_sql('SELECT count(*) FROM careertrellis.profiles').scalar_one())
        self.fixture.policy(cap=2)
        self.assertEqual('full', self.store.availability()['status'])

    def test_expiry_after_observed_real_lock_wait(self):
        started = self.start()
        code = self.provider.issue(started.state)
        flow = self.adapter.open_flow(started.cookie, self.browser)
        with self.admin.begin() as holder, ThreadPoolExecutor(1) as pool:
            holder.exec_driver_sql('SELECT ct_admission.lock_lifecycle()')
            pending = pool.submit(self.adapter.complete, started.cookie, self.browser, started.state, code)
            deadline = time.monotonic() + 1.5
            observed = False
            while time.monotonic() < deadline:
                observed = bool(self.query("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE "
                    "usename='ct_admission_broker' AND wait_event_type='Lock')"))
                if observed:
                    break
                time.sleep(.02)
            # Change lease under the held lock; release only after observing a waiter.
            holder.execute(text('UPDATE ct_admission.flows SET expires=clock_timestamp() WHERE id=:rid'), {'rid': flow.id})
            holder.commit()
            result = pending.result(8)
        self.assertTrue(observed)
        self.assertEqual('expired', result.status)
        self.assertEqual(0, self.provider.exchanges)

    def test_mapping_outage_after_commit_and_on_existing_session_is_unavailable(self):
        started = self.start()
        with patch.object(self.runtime, 'begin', side_effect=OperationalError('synthetic SQL', {}, Exception('private detail'))):
            result, _ = self.finish(started)
        self.assertEqual('unavailable', result.status)
        self.assertFalse(result.cookie)
        self.assertEqual(2, self.count())
        result, _ = self.finish(self.start('login'))
        with patch.object(self.store, 'mapping', side_effect=MappingUnavailable()):
            self.assertEqual('unavailable', self.adapter.authenticate(result.cookie).status)
        self.assertEqual('authenticated', self.adapter.authenticate(result.cookie).status)

    def test_wrong_claim_and_cap_reduction_reject_without_account(self):
        started = self.start()
        flow = self.adapter.open_flow(started.cookie, self.browser)
        code = self.provider.issue(started.state)
        claim = str(uuid4())
        self.assertEqual('exchange', self.store.claim(flow, code, claim)['status'])
        identity = self.provider.exchange(code, flow.verifier)
        self.assertEqual('invalid', self.store.finish(flow, str(uuid4()), identity)['status'])
        self.fixture.policy(cap=1)
        self.assertEqual('full', self.store.finish(flow, claim, identity)['status'])
        self.assertEqual(1, self.count())

    def test_session_wrong_key_realm_expiry_and_changed_refresh_identity(self):
        result, _ = self.finish(self.start())
        other = IntegrationAdapter(self.store, self.provider, Fernet.generate_key())
        self.assertEqual('unauthenticated', other.authenticate(result.cookie).status)
        data = self.adapter._decrypt(result.cookie, 'session', 86400)
        invalid = self.adapter._encrypt('session', {**data, 'realm': 'wrong-realm'})
        self.assertEqual('unauthenticated', self.adapter.authenticate(invalid).status)
        with patch.object(self.adapter, 'clock', return_value=time.time() + 86402):
            self.assertEqual('unauthenticated', self.adapter.authenticate(result.cookie).status)
        original = self.provider.authenticate(data['provider'])
        for field, changed in (('subject', 'user_someone_else'), ('sid', str(uuid4()))):
            forged = self.provider._seal.encrypt(packed({**original, field: changed}).encode()).decode()
            with patch.object(self.provider, 'refresh', return_value=forged):
                self.assertEqual('unauthenticated', self.adapter.authenticate(result.cookie, refresh=True).status)

    def test_oversized_initial_and_refreshed_envelopes_never_emit_cookie(self):
        original_exchange = self.provider.exchange
        def huge(*args):
            verified = original_exchange(*args)
            payload = self.provider.authenticate(verified.provider_session)
            padded = self.provider._seal.encrypt(packed({**payload, 'padding': 'x' * 3000}).encode()).decode()
            return replace(verified, provider_session=padded)
        with patch.object(self.provider, 'exchange', huge):
            result, _ = self.finish(self.start())
        self.assertEqual('incomplete', result.status)
        self.assertFalse(result.cookie)
        self.assertEqual(2, self.count())
        login, _ = self.finish(self.start('login'))
        data = self.adapter._decrypt(login.cookie, 'session', 86400)
        payload = self.provider.authenticate(data['provider'])
        padded = self.provider._seal.encrypt(packed({**payload, 'padding': 'x' * 3000}).encode()).decode()
        with patch.object(self.provider, 'refresh', return_value=padded):
            result = self.adapter.authenticate(login.cookie, refresh=True)
        self.assertEqual('unavailable', result.status)
        self.assertFalse(result.cookie)

    def test_finalize_expired_during_observed_outer_lock_wait_does_not_delete(self):
        self.finish(self.start())
        uid = self.uid()
        self.fixture.request_erasure(uid)
        claim = self.fixture.claim_erasure()
        generation = self.query('SELECT generation FROM ct_admission.policy')
        with self.admin.begin() as holder, ThreadPoolExecutor(1) as pool:
            holder.exec_driver_sql('SELECT ct_admission.lock_lifecycle()')
            pending = pool.submit(self.fixture.finalize_erasure, claim)
            observed = False
            end = time.monotonic() + 2
            while time.monotonic() < end:
                observed = bool(self.query("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE "
                    "usename='careertrellis_erasure_worker' AND wait_event_type='Lock')"))
                if observed:
                    break
                time.sleep(.02)
            holder.execute(text('UPDATE careertrellis.account_erasure_requests SET lease_expires_at=clock_timestamp() '
                                'WHERE id=:rid'), {'rid': claim['request_id']})
            holder.commit()
            self.assertFalse(pending.result(8))
        self.assertTrue(observed)
        self.assertEqual(uid, self.uid())
        self.assertEqual(generation, self.query('SELECT generation FROM ct_admission.policy'))

    def test_erasure_claim_and_finalize_both_wait_for_outer_lock(self):
        self.finish(self.start())
        uid = self.uid()
        self.fixture.request_erasure(uid)
        claim = self.fixture.claim_erasure()
        with self.admin.begin() as holder, ThreadPoolExecutor(2) as pool:
            holder.exec_driver_sql('SELECT ct_admission.lock_lifecycle()')
            a = pool.submit(self.fixture.claim_erasure)
            b = pool.submit(self.fixture.finalize_erasure, claim)
            observed = False
            end = time.monotonic() + 2
            while time.monotonic() < end:
                observed = self.query("SELECT count(*) FROM pg_stat_activity WHERE "
                    "usename='careertrellis_erasure_worker' AND wait_event_type='Lock'") == 2
                if observed:
                    break
                time.sleep(.02)
            holder.commit()
            # No eligible second claim while the original lease is valid.
            from sqlalchemy.exc import NoResultFound
            with self.assertRaises(NoResultFound):
                a.result(8)
            self.assertTrue(b.result(8))
        self.assertTrue(observed)

    def test_wrong_role_cannot_execute_lifecycle_or_exchange_functions(self):
        calls = [('ct_admission_broker', 'SELECT ct_admission.request_erasure()'),
                 ('careertrellis_runtime', "SELECT ct_admission.seed_allowed('user_synthetic_new')"),
                 ('careertrellis_erasure_worker', 'SELECT ct_admission.request_erasure()'),
                 ('careertrellis_migration', 'SELECT ct_admission.purge()'),
                 ('ct_admission_outsider', 'SELECT ct_admission.availability()')]
        for role, sql in calls:
            with self.subTest(role=role), self.assertRaises(DBAPIError):
                with self.fixture.engine(role).begin() as connection:
                    connection.exec_driver_sql(sql)

    def http_client(self):
        from fastapi.testclient import TestClient
        from signup_prototype.integration_http import create_app, ORIGIN
        return TestClient(create_app(self.adapter), base_url=ORIGIN)

    def http_start(self, client, mode='signup', follow_redirects=True):
        from signup_prototype.integration_http import ORIGIN
        home = client.get('/')
        csrf = re.search(r'name="csrf" value="([^"]+)"', home.text).group(1)
        return client.post('/start', data={'csrf': csrf, 'mode': mode},
                           headers={'Origin': ORIGIN}, follow_redirects=follow_redirects)

    def test_http_complete_flow_clean_result_secure_cookie_and_private_session(self):
        from signup_prototype.integration_http import SESSION
        with self.http_client() as client:
            self.assertEqual('isolated-synthetic-integration', client.get('/api/version').json()['build'])
            result = self.http_start(client)
            self.assertEqual('/result', result.url.path)
            self.assertFalse(result.url.query)
            self.assertEqual('activated', result.json()['status'])
            self.assertEqual('no-store', result.headers['cache-control'])
            self.assertEqual('no-referrer', result.headers['referrer-policy'])
            self.assertEqual('authenticated', client.get('/api/session').json()['status'])
            header = next(v for r in result.history for v in r.headers.get_list('set-cookie')
                          if v.startswith(SESSION + '=') and 'Max-Age=86400' in v)
            self.assertLess(len(header.encode()), 4096)
            for flag in ('Secure', 'HttpOnly', 'SameSite=lax', 'Path=/'):
                self.assertIn(flag, header)
            self.assertNotIn('user_synthetic', result.text)

    def test_http_full_has_no_create_button_but_existing_login_works(self):
        with self.http_client() as client:
            self.http_start(client)
            self.fixture.policy(cap=2)
            page = client.get('/').text
            self.assertNotIn('Create test account', page)
            self.assertIn('Sign in to test account', page)
            self.assertEqual('login', self.http_start(client, 'login').json()['status'])
            self.assertEqual('full', self.http_start(client, 'signup').json()['status'])

    def test_http_missing_context_csrf_duplicate_callback_and_unknown_host(self):
        from signup_prototype.integration_http import ORIGIN
        with self.http_client() as client:
            self.assertEqual('no_browser_context', client.get('/result').json()['status'])
            home = client.get('/')
            csrf = re.search(r'name="csrf" value="([^"]+)"', home.text).group(1)
            for origin in ('null', 'https://other.test', ''):
                result = client.post('/start', data={'csrf': csrf, 'mode': 'signup'}, headers={'Origin': origin})
                self.assertEqual(403, result.status_code)
            self.assertEqual(403, client.post('/start', data={'csrf': 'invalid', 'mode': 'signup'}, headers={'Origin': ORIGIN}).status_code)
            for body in (f'mode=&mode=signup&csrf={csrf}', f'mode=signup&csrf=&csrf={csrf}'):
                self.assertEqual(403, client.post('/start', content=body,
                    headers={'Origin': ORIGIN, 'Content-Type': 'application/x-www-form-urlencoded'}).status_code)
            self.assertEqual(400, client.get('/', headers={'Host': 'other.test'}).status_code)
            self.assertEqual('invalid', client.get('/callback?code=synthetic&state=a&state=b').json()['status'])
            self.assertEqual(0, self.provider.exchanges)

    def test_blocked_synthetic_exchange_does_not_block_duplicate_or_cancel(self):
        started = self.start()
        code = self.provider.issue(started.state)
        entered, release = threading.Event(), threading.Event()
        def wait():
            entered.set()
            if not release.wait(5):
                raise RuntimeError('synthetic test timeout')
        self.provider.before_exchange = wait
        with ThreadPoolExecutor(1) as pool:
            running = pool.submit(self.adapter.complete, started.cookie, self.browser, started.state, code)
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual('incomplete', self.adapter.complete(started.cookie, self.browser, started.state, code).status)
                self.assertEqual('cancelled', self.adapter.complete(started.cookie, self.browser, started.state, cancelled=True).status)
            finally:
                release.set()
            self.assertEqual('cancelled', running.result(5).status)

    def test_real_object_erasure_acknowledgement_required_and_other_tenant_preserved(self):
        self.finish(self.start())
        uid, object_id, owner_object = self.uid(), uuid4(), uuid4()
        for account, oid in ((uid, object_id), (self.owner, owner_object)):
            self.query('INSERT INTO careertrellis.stored_objects '
                '(id,owner_user_id,object_key,content_type,plaintext_size,sha256,wrapped_data_key) '
                "VALUES (:oid,:uid,:key,'application/pdf',1,:sha,:wrapped)",
                oid=oid, uid=account, key='synthetic/' + oid.hex, sha=b'd' * 32, wrapped=b'synthetic')
        self.fixture.request_erasure(uid)
        claim = self.fixture.claim_erasure()
        generation = self.query('SELECT generation FROM ct_admission.policy')
        self.assertFalse(self.fixture.finalize_erasure(claim))
        self.assertEqual(generation, self.query('SELECT generation FROM ct_admission.policy'))
        self.assertEqual(uid, self.uid())
        claim = self.fixture.claim_erasure()
        self.assertTrue(self.fixture.worker_operation('renew', claim))
        batch = self.fixture.worker_operation('batch', claim)
        self.assertEqual([object_id], [row['object_id'] for row in batch])
        self.assertTrue(self.fixture.worker_operation('confirm', claim, object_id))
        self.assertTrue(self.fixture.finalize_erasure(claim))
        self.assertEqual(1, self.query('SELECT count(*) FROM careertrellis.stored_objects WHERE owner_user_id=:uid', uid=self.owner))
        self.assertEqual(timedelta(days=30), self.query('SELECT receipt_expires_at-completed_at FROM careertrellis.account_erasure_requests'))
        self.assertEqual(0, self.query('SELECT count(*) FROM careertrellis.account_erasure_claim_queue'))

    def test_finalize_rolls_back_if_catalog_lock_wait_outlives_lease(self):
        self.finish(self.start())
        uid = self.uid()
        self.fixture.request_erasure(uid)
        claim = self.fixture.claim_erasure()
        generation = self.query('SELECT generation FROM ct_admission.policy')
        deadline = self.query("UPDATE careertrellis.account_erasure_requests "
            "SET lease_expires_at=clock_timestamp()+interval '1 second' WHERE id=:rid RETURNING lease_expires_at",
            rid=claim['request_id'])
        with self.admin.begin() as holder, ThreadPoolExecutor(1) as pool:
            holder.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                           {'key': 'careertrellis-job-catalog:' + str(uid)})
            pending = pool.submit(self.fixture.finalize_erasure, claim)
            observed = False
            end = time.monotonic() + 2
            while time.monotonic() < end:
                observed = bool(self.query("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE "
                    "usename='careertrellis_erasure_worker' AND wait_event_type='Lock')"))
                if observed:
                    break
                time.sleep(.01)
            while self.query('SELECT clock_timestamp()') <= deadline and time.monotonic() < end:
                time.sleep(.02)
            holder.commit()
            with self.assertRaises(DBAPIError):
                pending.result(5)
        self.assertTrue(observed)
        self.assertEqual(uid, self.uid())
        self.assertEqual(generation, self.query('SELECT generation FROM ct_admission.policy'))

    def test_worker_retry_is_coordinated_and_expired_renew_is_denied(self):
        self.finish(self.start())
        uid = self.uid()
        self.fixture.request_erasure(uid)
        claim = self.fixture.claim_erasure()
        self.assertTrue(self.fixture.worker_operation('retry', claim))
        self.query('UPDATE careertrellis.account_erasure_requests SET available_at=clock_timestamp() WHERE id=:rid', rid=claim['request_id'])
        claim = self.fixture.claim_erasure()
        self.query('UPDATE careertrellis.account_erasure_requests SET lease_expires_at=clock_timestamp() WHERE id=:rid', rid=claim['request_id'])
        with self.assertRaisesRegex(ValueError, 'lease expired'):
            self.fixture.worker_operation('renew', claim)
        self.assertEqual(uid, self.uid())

    @unittest.skipUnless(os.name == 'nt', 'Native Edge verification on Windows')
    def test_native_edge_form_and_near_limit_cookie_survive_real_browser_storage(self):
        import ipaddress
        import socket
        import tempfile
        from pathlib import Path
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from playwright.sync_api import sync_playwright
        import uvicorn
        from signup_prototype.integration_http import ORIGIN, SESSION, create_app
        original_exchange = self.provider.exchange
        def near_limit(*args):
            verified = original_exchange(*args)
            payload = self.provider.authenticate(verified.provider_session)
            # Synthetic padding only, calibrated against the actual sealed format.
            low, high, accepted = 0, 2500, verified.provider_session
            while low <= high:
                middle = (low + high) // 2
                candidate = self.provider._seal.encrypt(packed({**payload, 'padding': 'x' * middle}).encode()).decode()
                size = len(self.adapter._encrypt('session', {'local_id': 999999, 'realm': REALM, 'provider': candidate}))
                if size <= MAX_COOKIE_VALUE:
                    accepted, low = candidate, middle + 1
                else:
                    high = middle - 1
            return replace(verified, provider_session=accepted)
        # Real loopback HTTPS transport so browser redirects and cookie storage
        # are not emulated by the HTTP client's cookie jar. Disposable TLS key
        # is synthetic test material in TemporaryDirectory, never operator data.
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 8766))  # Fail on collision; never stop another listener.
        with tempfile.TemporaryDirectory(prefix='ct-synthetic-https-') as temporary, sync_playwright() as playwright:
            private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic loopback test')])
            now = datetime.now(timezone.utc)
            certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(private.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
                .sign(private, hashes.SHA256()))
            key_path, cert_path = Path(temporary) / 'synthetic.key', Path(temporary) / 'synthetic.crt'
            key_path.write_bytes(private.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            app = create_app(self.adapter)
            observed_headers, posts, external = [], [], []
            @app.middleware('http')
            async def observe(request, call_next):
                response = await call_next(request)
                if request.method == 'POST':
                    posts.append((request.headers.get('origin'), response.status_code))
                observed_headers.extend(len(v.encode()) for v in response.headers.getlist('set-cookie')
                    if v.startswith(SESSION + '=') and 'Max-Age=86400' in v)
                return response
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8766,
                ssl_keyfile=str(key_path), ssl_certfile=str(cert_path), access_log=False,
                log_config=None, log_level='critical'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
            thread.start()
            browser = context = None
            def guard(route):
                if not route.request.url.startswith(ORIGIN + '/'):
                    external.append(True)
                    route.abort()
                else:
                    route.continue_()
            try:
                browser = playwright.chromium.launch(channel='msedge', headless=True)
                context = browser.new_context(ignore_https_errors=True)
                end = time.monotonic() + 5
                while not server.started and time.monotonic() < end:
                    time.sleep(.02)
                self.assertTrue(server.started)
                context.route('**/*', guard)
                page = context.new_page()
                page.goto(ORIGIN + '/api/version')
                self.assertIn('isolated-synthetic-integration', page.locator('body').inner_text())
                with patch.object(self.provider, 'exchange', near_limit):
                    page.goto(ORIGIN + '/')
                    page.get_by_role('button', name='Create test account', exact=True).click()
                    page.wait_for_url(ORIGIN + '/result')
                    self.assertIn('activated', page.locator('body').inner_text())
                    page.goto(ORIGIN + '/api/session')
                    self.assertIn('authenticated', page.locator('body').inner_text())
                self.assertEqual([(ORIGIN, 303)], posts)
                self.assertEqual(1, len(observed_headers))
                self.assertGreater(observed_headers[0], 3400)
                self.assertLess(observed_headers[0], 4096)
                self.assertEqual([], external)
            finally:
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
                server.should_exit = True
                thread.join(5)
                listener.close()
                self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
