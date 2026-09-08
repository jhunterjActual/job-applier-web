"""Synthetic-only PostgreSQL 18 admission proof; never uses application settings.

Set CAREERTRELLIS_SIGNUP_PROTOTYPE_URL to a dedicated loopback PostgreSQL 18
database named ct_signup_prototype. Creates test roles/schema, refuses collisions,
and removes only those fixtures. No application imports, provider or paid APIs.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from threading import Barrier, Event
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError

from signup_prototype.service import (
    AdmissionPrototype, SyntheticVerifiedIdentity, new_synthetic_flow,
)


ROLES = ('ct_signup_owner', 'ct_signup_broker', 'ct_signup_reader', 'ct_signup_outsider')
TEST_PASSWORD = 'synthetic-signup-test-only'


class PrototypeBoundaryTests(unittest.TestCase):
    def test_refuses_nonlocal_or_application_database(self):
        for url in ('postgresql+psycopg://example.test/ct_signup_prototype',
                    'postgresql+psycopg://127.0.0.1/careertrellis', 'sqlite://'):
            engine = create_engine(url)
            try:
                with self.assertRaisesRegex(ValueError, 'isolated'):
                    AdmissionPrototype(engine)
            finally:
                engine.dispose()

    def test_database_error_is_sanitized_and_not_capacity_full(self):
        engine = create_engine('postgresql+psycopg://127.0.0.1/ct_signup_prototype')
        try:
            service = AdmissionPrototype(engine)
            with patch.object(engine, 'begin', side_effect=OperationalError(
                    'private SQL', {'secret': 'not-for-output'}, Exception('private detail'))):
                self.assertEqual({'status': 'unavailable'}, service.availability())
        finally:
            engine.dispose()


class SignupPrototypePostgreSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = os.environ.get('CAREERTRELLIS_SIGNUP_PROTOTYPE_URL', '')
        if not raw:
            raise unittest.SkipTest('Dedicated signup prototype PostgreSQL URL is not configured')
        cls.url = make_url(raw)
        cls.admin = create_engine(cls.url, hide_parameters=True)
        AdmissionPrototype(cls.admin)  # Refuse live/nonlocal database before any connection.
        cls.created_roles = []
        cls.created_schema = False
        cls.engines = []
        cls.addClassCleanup(cls.cleanup)
        with cls.admin.begin() as connection:
            version = int(connection.execute(text('SHOW server_version_num')).scalar_one())
            if not 180000 <= version < 190000:
                raise RuntimeError('PostgreSQL 18 is required for this proof')
            if connection.execute(text("SELECT to_regnamespace('admission_prototype')")).scalar():
                raise RuntimeError('Prototype schema already exists; refusing to overwrite')
            for role in ROLES:
                if connection.execute(text('SELECT 1 FROM pg_roles WHERE rolname = :role'),
                                      {'role': role}).scalar():
                    raise RuntimeError('Prototype role collision; refusing to reuse')
            for role in ROLES:
                login = 'NOLOGIN' if role == 'ct_signup_owner' else (
                    "LOGIN PASSWORD 'synthetic-signup-test-only'")
                connection.exec_driver_sql(f'CREATE ROLE {role} {login} NOSUPERUSER NOBYPASSRLS NOINHERIT')
        cls.created_roles.extend(ROLES)
        with cls.admin.begin() as connection:
            connection.execute(text((Path(__file__).parent / 'signup_prototype/schema.sql').read_text()))
        cls.created_schema = True
        for role in ROLES[1:]:
            engine = create_engine(cls.url.set(username=role, password=TEST_PASSWORD),
                                   pool_size=12, max_overflow=0, hide_parameters=True)
            cls.engines.append(engine)
        cls.broker, cls.reader, cls.outsider = cls.engines

    @classmethod
    def cleanup(cls):
        for engine in cls.engines:
            engine.dispose()
        try:
            with cls.admin.begin() as connection:
                if cls.created_schema:
                    connection.exec_driver_sql('DROP SCHEMA admission_prototype CASCADE')
                for role in cls.created_roles:
                    # Only roles freshly created by this fixture in its guarded database.
                    connection.exec_driver_sql(f'DROP OWNED BY {role}')
                    connection.exec_driver_sql(f'DROP ROLE {role}')
        finally:
            cls.admin.dispose()

    def setUp(self):
        with self.admin.begin() as connection:
            connection.exec_driver_sql('TRUNCATE admission_prototype.app_users, '
                                       'admission_prototype.reservations RESTART IDENTITY')
            connection.exec_driver_sql("UPDATE admission_prototype.policy SET mode = 'closed', "
                                       'account_cap = 1, reservation_seconds = 600, erasure_generation = 0')
        self.service = AdmissionPrototype(self.broker)

    def query(self, sql, **parameters):
        with self.admin.begin() as connection:
            result = connection.execute(text(sql), parameters)
            return result.scalar() if result.returns_rows else None

    def policy(self, mode='beta', cap=1):
        self.query('UPDATE admission_prototype.policy SET mode = :mode, account_cap = :cap',
                   mode=mode, cap=cap)

    def account(self, subject='user_existing1', role='user', disabled=False, erasing=False):
        return self.query('INSERT INTO admission_prototype.app_users '
                          '(workos_user_id, role, disabled, erasing) '
                          'VALUES (:subject, :role, :disabled, :erasing) RETURNING id',
                          subject=subject, role=role, disabled=disabled, erasing=erasing)

    def count(self, table):
        self.assertIn(table, ('app_users', 'reservations'))
        return self.query(f'SELECT count(*) FROM admission_prototype.{table}')

    def reserve(self):
        flow = new_synthetic_flow()
        self.assertEqual('reserved', self.service.reserve(flow)['status'])
        return flow

    def activate(self, flow, subject='user_newperson1'):
        return self.service.activate(flow, SyntheticVerifiedIdentity(subject))

    def expire(self, flow):
        self.query('UPDATE admission_prototype.reservations SET expires_at = clock_timestamp() '
                   'WHERE request_id = :request', request=flow.request_id)

    def test_closed_default_and_open_release(self):
        self.assertEqual({'status': 'closed'}, self.service.availability())
        self.assertEqual({'status': 'closed'}, self.service.reserve(new_synthetic_flow()))
        self.assertEqual(0, self.count('reservations'))
        self.policy('open', cap=1)
        for index in range(3):
            self.assertEqual('activated', self.activate(self.reserve(), f'user_open{index:06}')['status'])
        self.assertEqual(3, self.count('app_users'))

    def test_cap_counts_owner_disabled_erasing_and_reservations(self):
        self.policy(cap=4)
        self.account('user_owner000', role='owner')
        self.account('user_disabled', disabled=True)
        self.account('user_erasing0', erasing=True)
        self.reserve()
        self.assertEqual({'status': 'full'}, self.service.availability())
        self.assertEqual({'status': 'full'}, self.service.reserve(new_synthetic_flow()))

    def test_concurrent_final_slot_has_exactly_one_winner(self):
        self.policy()
        barrier = Barrier(8)
        def attempt(_):
            barrier.wait(timeout=5)
            return self.service.reserve(new_synthetic_flow())['status']
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(1, results.count('reserved'))
        self.assertEqual(7, results.count('full'))
        self.assertEqual(1, self.count('reservations'))

    def test_reservation_retry_does_not_renew_or_duplicate(self):
        self.policy()
        flow = self.reserve()
        original = self.query('SELECT expires_at FROM admission_prototype.reservations')
        self.assertEqual('reserved', self.service.reserve(flow)['status'])
        self.assertEqual(original, self.query('SELECT expires_at FROM admission_prototype.reservations'))
        self.assertEqual(1, self.count('reservations'))
        self.assertEqual('invalid', self.service.reserve(
            replace(flow, deadline=flow.deadline - timedelta(seconds=1)))['status'])

    def test_concurrent_duplicate_activation_and_new_service_retry(self):
        self.policy()
        flow = self.reserve()
        barrier = Barrier(8)
        def attempt(_):
            barrier.wait(timeout=5)
            return self.activate(flow)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual('activated', results[0]['status'])
        self.assertEqual(1, self.count('app_users'))
        # A new pool/service, not an in-memory idempotency cache; discard first result.
        restarted = create_engine(self.broker.url, hide_parameters=True)
        try:
            replay = AdmissionPrototype(restarted).activate(flow, SyntheticVerifiedIdentity('user_newperson1'))
            self.assertEqual(results[0], replay)
        finally:
            restarted.dispose()
        self.assertEqual('user', self.query('SELECT role FROM admission_prototype.app_users'))

    def test_different_flows_same_identity_preserve_one_account(self):
        self.policy(cap=2)
        first, second = self.reserve(), self.reserve()
        result = self.activate(first)
        self.assertEqual('existing_account', self.activate(second)['status'])
        self.assertEqual(result['local_user_id'], self.query('SELECT id FROM admission_prototype.app_users'))
        self.assertEqual(1, self.count('app_users'))

    def test_unverified_impersonated_or_invalid_identity_cannot_activate(self):
        self.policy()
        flow = self.reserve()
        for identity in (SyntheticVerifiedIdentity('user_valid000', email_verified=False),
                         SyntheticVerifiedIdentity('user_valid000', impersonated=True),
                         SyntheticVerifiedIdentity('other_invalid')):
            self.assertEqual('ineligible', self.service.activate(flow, identity)['status'])
        self.assertEqual(0, self.count('app_users'))

    def test_wrong_browser_and_changed_consumed_identity_fail(self):
        self.policy()
        flow = self.reserve()
        wrong = replace(flow, browser_secret=new_synthetic_flow().browser_secret)
        self.assertEqual('invalid', self.service.reserve(wrong)['status'])
        self.assertEqual('invalid', self.activate(wrong)['status'])
        self.assertEqual('invalid', self.service.cancel(wrong)['status'])
        self.assertEqual('activated', self.activate(flow)['status'])
        self.assertEqual('ineligible', self.activate(flow, 'user_changed000')['status'])
        self.assertEqual(1, self.count('app_users'))

    def test_no_reservation_is_not_fresh_registration(self):
        self.policy('open')
        self.assertEqual('invalid', self.activate(new_synthetic_flow())['status'])
        self.assertEqual(0, self.count('app_users'))

    def test_expiry_boundary_releases_capacity_and_cannot_renew(self):
        self.policy()
        flow = self.reserve()
        self.expire(flow)
        self.assertEqual('expired', self.activate(flow)['status'])
        self.assertEqual('expired', self.service.reserve(flow)['status'])
        self.assertEqual({'status': 'available'}, self.service.availability())
        self.reserve()

    def test_expiry_is_evaluated_after_lock_wait(self):
        self.policy()
        flow = self.reserve()
        entered = Event()
        # The activation transaction begins before expiry and waits behind the policy lock.
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.admin.begin() as connection:
                connection.exec_driver_sql('SELECT singleton FROM admission_prototype.policy FOR UPDATE')
                def activate_waiting():
                    entered.set()
                    return self.activate(flow)
                result = pool.submit(activate_waiting)
                self.assertTrue(entered.wait(2))
                waiting = False
                for _ in range(100):
                    waiting = connection.execute(text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE usename = 'ct_signup_broker' AND wait_event_type = 'Lock' "
                        "AND query LIKE 'SELECT admission_prototype.activate%')")).scalar()
                    if waiting:
                        break
                    connection.exec_driver_sql('SELECT pg_stat_clear_snapshot()')
                    connection.exec_driver_sql('SELECT pg_sleep(0.01)')
                self.assertTrue(waiting, 'Activation must demonstrably wait across expiry')
                connection.exec_driver_sql('UPDATE admission_prototype.reservations SET expires_at = clock_timestamp()')
            self.assertEqual('expired', result.result(timeout=5)['status'])
        self.assertEqual(0, self.count('app_users'))

    def test_cancel_provider_failure_and_retry_release_slot(self):
        self.policy()
        flow = self.reserve()
        self.assertEqual({'status': 'cancelled'}, self.service.cancel(flow))
        self.assertEqual({'status': 'cancelled'}, self.service.cancel(flow))
        self.assertEqual('cancelled', self.activate(flow)['status'])
        self.assertEqual('cancelled', self.service.reserve(flow)['status'])
        self.reserve()

    def test_closed_and_cap_reduction_apply_to_activation(self):
        self.policy(cap=2)
        first, second = self.reserve(), self.reserve()
        self.policy('closed', cap=2)
        self.assertEqual('closed', self.activate(first)['status'])
        self.policy(cap=1)
        self.assertEqual('activated', self.activate(first)['status'])
        self.assertEqual('full', self.activate(second, 'user_another000')['status'])
        self.assertEqual(1, self.count('app_users'))

    def test_existing_owner_is_not_rebound_or_demoted(self):
        self.policy(cap=2)
        owner_id = self.account('user_owner000', role='owner')
        self.assertEqual('existing_account', self.activate(self.reserve(), 'user_owner000')['status'])
        self.assertEqual(owner_id, self.query('SELECT id FROM admission_prototype.app_users'))
        self.assertEqual('owner', self.query('SELECT role FROM admission_prototype.app_users'))

    def test_disabled_and_erasing_accounts_are_not_reactivated(self):
        self.policy(cap=3)
        for index, fields in enumerate(({'disabled': True}, {'erasing': True})):
            subject = f'user_ineligible{index}'
            self.account(subject, **fields)
            self.assertEqual('ineligible', self.activate(self.reserve(), subject)['status'])
        self.assertEqual(2, self.count('app_users'))

    def test_disable_after_activation_rejects_replay(self):
        self.policy()
        flow = self.reserve()
        self.activate(flow)
        self.query('UPDATE admission_prototype.app_users SET disabled = true')
        self.assertEqual('ineligible', self.activate(flow)['status'])

    def test_erasure_rejects_consumed_and_outstanding_old_flows(self):
        self.policy(cap=3)
        consumed, outstanding = self.reserve(), self.reserve()
        user_id = self.activate(consumed)['local_user_id']
        self.query('SELECT admission_prototype.erase_fixture(:target)', target=user_id)
        self.assertEqual('ineligible', self.activate(consumed)['status'])
        self.assertEqual('expired', self.activate(outstanding)['status'])
        self.assertEqual(0, self.count('app_users'))
        fresh = self.activate(self.reserve())
        self.assertEqual('activated', fresh['status'])
        self.assertNotEqual(user_id, fresh['local_user_id'])

    def test_expired_receipt_purge_never_restores_callback_authority(self):
        self.policy()
        flow = self.reserve()
        self.activate(flow)
        self.expire(flow)
        self.query('SELECT admission_prototype.purge_expired()')
        self.assertEqual(1, self.count('reservations'))
        self.assertEqual('expired', self.activate(flow)['status'])
        self.assertEqual('expired', self.service.reserve(flow)['status'])

    def test_reservation_purge_waits_for_original_flow_deadline(self):
        self.policy()
        flow = replace(new_synthetic_flow(), deadline=datetime.now(timezone.utc) + timedelta(seconds=0.4))
        self.assertEqual('reserved', self.service.reserve(flow)['status'])
        self.query('SELECT pg_sleep(0.5)')
        self.query('SELECT admission_prototype.purge_expired()')
        self.assertEqual(0, self.count('reservations'))
        self.assertEqual('invalid', self.activate(flow)['status'])
        self.assertEqual('invalid', self.service.reserve(flow)['status'])

    def test_abandoned_lease_purge_cannot_renew_same_still_valid_flow(self):
        self.policy()
        flow = self.reserve()
        self.expire(flow)
        self.query('SELECT admission_prototype.purge_expired()')
        self.assertEqual('expired', self.service.reserve(flow)['status'])
        self.reserve()  # Another genuinely fresh flow may use the released slot.

    def test_transaction_failure_rolls_back_insert_and_consumption(self):
        self.policy()
        flow = self.reserve()
        try:
            with self.admin.begin() as connection:
                connection.exec_driver_sql("CREATE FUNCTION admission_prototype.inject_failure() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic rollback'; END $$")
                connection.exec_driver_sql('CREATE TRIGGER inject_failure BEFORE UPDATE '
                    'ON admission_prototype.reservations FOR EACH ROW '
                    'EXECUTE FUNCTION admission_prototype.inject_failure()')
            self.assertEqual('unavailable', self.activate(flow)['status'])
            self.assertEqual(0, self.count('app_users'))
            self.assertEqual('reserved', self.query('SELECT state FROM admission_prototype.reservations'))
        finally:
            with self.admin.begin() as connection:
                connection.exec_driver_sql('DROP TRIGGER IF EXISTS inject_failure ON admission_prototype.reservations')
                connection.exec_driver_sql('DROP FUNCTION IF EXISTS admission_prototype.inject_failure()')
        self.assertEqual('activated', self.activate(flow)['status'])

    def test_lock_timeout_is_unavailable_not_full(self):
        self.policy()
        with self.admin.begin() as connection:
            connection.exec_driver_sql('SELECT singleton FROM admission_prototype.policy FOR UPDATE')
            self.assertEqual({'status': 'unavailable'}, self.service.availability())
        self.assertEqual({'status': 'available'}, self.service.availability())

    def test_missing_policy_fails_closed_as_unavailable(self):
        try:
            self.query('DELETE FROM admission_prototype.policy')
            self.assertEqual({'status': 'unavailable'}, self.service.availability())
            self.assertEqual({'status': 'unavailable'}, self.service.reserve(new_synthetic_flow()))
        finally:
            self.query('INSERT INTO admission_prototype.policy DEFAULT VALUES')

    def assert_denied(self, engine, sql):
        with self.assertRaises(DBAPIError):
            with engine.begin() as connection:
                connection.exec_driver_sql(sql)

    def test_broker_cannot_read_mutate_or_escalate_outside_functions(self):
        for sql in ('SELECT * FROM admission_prototype.app_users',
                    'SELECT * FROM admission_prototype.reservations',
                    'SELECT * FROM admission_prototype.policy',
                    "INSERT INTO admission_prototype.app_users (workos_user_id, role) VALUES ('user_evil000', 'owner')",
                    'UPDATE admission_prototype.app_users SET disabled = false',
                    'DELETE FROM admission_prototype.app_users',
                    "UPDATE admission_prototype.policy SET mode = 'open'",
                    'CREATE TABLE admission_prototype.evil (id int)',
                    'SET ROLE ct_signup_owner',
                    'SELECT admission_prototype.erase_fixture(1)',
                    'SELECT admission_prototype.purge_expired()'):
            with self.subTest(operation=sql.split()[0]):
                self.assert_denied(self.broker, sql)

    def test_reader_and_public_cannot_call_broker_or_admin(self):
        flow = new_synthetic_flow()
        for engine in (self.reader, self.outsider):
            for sql in (f"SELECT admission_prototype.reserve('{flow.request_id}', repeat('a', 64), now())",
                        f"SELECT admission_prototype.activate('{flow.request_id}', repeat('a', 64), 'user_fake000')",
                        f"SELECT admission_prototype.cancel('{flow.request_id}', repeat('a', 64))",
                        'SELECT admission_prototype.erase_fixture(1)',
                        'SELECT admission_prototype.purge_expired()',
                        'SELECT * FROM admission_prototype.reservations'):
                self.assert_denied(engine, sql)
        self.assertEqual({'status': 'closed'}, AdmissionPrototype(self.reader).availability())

    def test_rls_login_visibility_is_independent_of_closed_signup(self):
        first = self.account('user_first000')
        self.account('user_second000')
        with self.reader.begin() as connection:
            self.assertEqual(0, connection.exec_driver_sql('SELECT count(*) FROM admission_prototype.app_users').scalar())
            connection.exec_driver_sql("SELECT set_config('careertrellis.workos_user_id', 'user_first000', true)")
            self.assertEqual([first], connection.exec_driver_sql('SELECT id FROM admission_prototype.app_users').scalars().all())
        self.assertEqual('closed', self.service.availability()['status'])

    def test_new_owner_functions_are_not_public_by_default(self):
        try:
            with self.admin.begin() as connection:
                connection.exec_driver_sql('SET LOCAL ROLE ct_signup_owner')
                connection.exec_driver_sql('CREATE FUNCTION admission_prototype.future_function() RETURNS int '
                                           'LANGUAGE sql AS $$ SELECT 1 $$')
            for engine in (self.broker, self.reader, self.outsider):
                self.assert_denied(engine, 'SELECT admission_prototype.future_function()')
        finally:
            self.query('DROP FUNCTION IF EXISTS admission_prototype.future_function()')


if __name__ == '__main__':
    unittest.main()
