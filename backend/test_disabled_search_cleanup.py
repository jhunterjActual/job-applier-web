"""Least-privilege disabled-owner cleanup on the disposable PostgreSQL harness."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import time
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

import test_postgres_rls as postgres_harness
import pilot_users
from postgres_database import tenant_transaction
from search_run_repository import PostgreSQLSearchRunRepository, claim_next_postgres_search_run


class DisabledSearchCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness = postgres_harness.PostgreSQLTenantIsolationTests
        harness.setUpClass()
        cls._harness = harness
        for name in ('admin_engine', 'runtime_engine', 'worker_engine'):
            setattr(cls, name, getattr(harness, name))

    @classmethod
    def tearDownClass(cls):
        cls._harness.tearDownClass()

    def setUp(self):
        self._harness.setUp(self)

    def claimed(self):
        repository = PostgreSQLSearchRunRepository(self.user_two, engine=self.runtime_engine)
        run = repository.enqueue(str(uuid4()), 'synthetic cleanup', 'Ohio')
        repository.publish_prepared(run, 'synthetic cleanup', 'Ohio')
        claim = claim_next_postgres_search_run(str(uuid4()), engine=self.worker_engine)
        self.assertIsNotNone(claim)
        self.assertEqual(run, claim.run_id)
        return claim

    def disable(self):
        with self.admin_engine.begin() as connection:
            connection.execute(text('UPDATE careertrellis.app_users SET disabled_at=clock_timestamp() '
                                    'WHERE id=:owner'), {'owner': self.user_two})

    def state(self, claim):
        with self.admin_engine.connect() as connection:
            return dict(connection.execute(text('SELECT status, result_json, error_code, lease_token, '
                'lease_expires_at, worker_id, completed_at FROM careertrellis.search_runs '
                'WHERE id=CAST(:run AS uuid)'), {'run': claim.run_id}).mappings().one())

    def cancel(self, claim, *, owner=None, run=None, lease=None, engine=None):
        owner = self.user_two if owner is None else owner
        with tenant_transaction(engine or self.worker_engine, owner) as connection:
            connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
            return connection.scalar(text('SELECT careertrellis.cancel_disabled_search_run('
                'CAST(:run AS uuid), :owner, CAST(:lease AS uuid))'),
                {'run': run or claim.run_id, 'owner': owner, 'lease': lease or claim.lease_token})

    def test_active_owner_is_unchanged(self):
        claim = self.claimed()
        before = self.state(claim)
        self.assertIs(self.cancel(claim), False)
        self.assertEqual(before, self.state(claim))

    def test_disabled_cleanup_is_terminal_exact_and_idempotent(self):
        claim = self.claimed()
        self.disable()
        self.assertIs(self.cancel(claim), True)
        state = self.state(claim)
        self.assertEqual('cancelled', state['status'])
        self.assertEqual('owner_disabled', state['error_code'])
        for field in ('result_json', 'lease_token', 'lease_expires_at', 'worker_id'):
            self.assertIsNone(state[field])
        self.assertIsNotNone(state['completed_at'])
        self.assertIs(self.cancel(claim), False)
        self.assertEqual(state, self.state(claim))
        with self.admin_engine.connect() as connection:
            self.assertEqual(0, connection.scalar(text('SELECT count(*) FROM '
                'careertrellis.search_run_claim_queue WHERE run_id=CAST(:run AS uuid)'),
                {'run': claim.run_id}))

    def test_canonical_operator_disable_allows_only_terminal_worker_completion(self):
        claim = self.claimed()
        migration_engine = create_engine(self._harness.migration_url)
        try:
            result = pilot_users.perform_operation(migration_engine, 'disable',
                'user_tenant_one', 'user_tenant_two')
        finally:
            migration_engine.dispose()
        self.assertEqual(('disabled', True), (result.result, result.changed))
        worker = PostgreSQLSearchRunRepository(self.user_two, engine=self.worker_engine)
        worker.succeed(claim, {'success': True, 'jobs_added': 7, 'secret': 'discard this'})
        state = self.state(claim)
        self.assertEqual('cancelled', state['status'])
        self.assertEqual('owner_disabled', state['error_code'])
        self.assertIsNone(state['result_json'])

    def test_wrong_owner_run_or_lease_does_not_mutate(self):
        claim = self.claimed()
        self.disable()
        before = self.state(claim)
        for fields in ({'owner': self.user_one}, {'run': str(uuid4())}, {'lease': str(uuid4())}):
            with self.subTest(fields=fields):
                self.assertIs(self.cancel(claim, **fields), False)
                self.assertEqual(before, self.state(claim))

    def test_expired_lease_does_not_mutate(self):
        claim = self.claimed()
        self.disable()
        with self.admin_engine.begin() as connection:
            connection.execute(text("UPDATE careertrellis.search_runs SET lease_expires_at="
                "clock_timestamp()-interval '1 second' WHERE id=CAST(:run AS uuid)"),
                {'run': claim.run_id})
        before = self.state(claim)
        self.assertIs(self.cancel(claim), False)
        self.assertEqual(before, self.state(claim))

    def test_held_identity_is_not_disabled_cleanup_authority(self):
        claim = self.claimed()
        with self.admin_engine.begin() as connection:
            connection.execute(text('UPDATE careertrellis_admission.identity_accounts SET held=true '
                                    'WHERE id=:owner'), {'owner': self.user_two})
        before = self.state(claim)
        self.assertIs(self.cancel(claim), False)
        self.assertEqual(before, self.state(claim))

    def test_erasing_identity_is_not_disabled_cleanup_authority(self):
        claim = self.claimed()
        with self.admin_engine.begin() as connection:
            connection.execute(text('INSERT INTO careertrellis.account_erasure_requests(owner_user_id) '
                                    'VALUES (:owner)'), {'owner': self.user_two})
        before = self.state(claim)
        self.assertIs(self.cancel(claim), False)
        self.assertEqual(before, self.state(claim))

    def test_hidden_queued_identity_is_deferred_without_losing_work(self):
        for denial in ('held', 'erasing'):
            with self.subTest(denial=denial):
                self._harness.setUp(self)
                repository = PostgreSQLSearchRunRepository(self.user_two, engine=self.runtime_engine)
                run = repository.enqueue(str(uuid4()), 'retained synthetic work', 'Ohio')
                repository.publish_prepared(run, 'retained synthetic work', 'Ohio')
                with self.admin_engine.begin() as connection:
                    if denial == 'held':
                        connection.execute(text('UPDATE careertrellis_admission.identity_accounts '
                            'SET held=true WHERE id=:owner'), {'owner': self.user_two})
                    else:
                        connection.execute(text('INSERT INTO careertrellis.account_erasure_requests('
                            'owner_user_id) VALUES (:owner)'), {'owner': self.user_two})
                with self.worker_engine.begin() as connection:
                    connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
                    self.assertIsNone(connection.execute(text('SELECT * FROM '
                        'careertrellis.claim_next_search_run(CAST(:worker AS uuid), 300)'),
                        {'worker': str(uuid4())}).first())
                with self.admin_engine.connect() as connection:
                    row = connection.execute(text('SELECT r.status, r.keywords, '
                        'q.available_at > clock_timestamp() AS deferred '
                        'FROM careertrellis.search_runs r JOIN careertrellis.search_run_claim_queue q '
                        'ON q.run_id=r.id WHERE r.id=CAST(:run AS uuid)'), {'run': run}).one()
                self.assertEqual('queued', row.status)
                self.assertEqual('retained synthetic work', row.keywords)
                self.assertTrue(row.deferred)

    def test_runtime_cannot_invoke_disabled_cleanup(self):
        claim = self.claimed()
        self.disable()
        before = self.state(claim)
        with self.assertRaises(DBAPIError):
            self.cancel(claim, engine=self.runtime_engine)
        self.assertEqual(before, self.state(claim))

    def test_worker_cannot_use_other_tenant_context_or_private_due_mode(self):
        claim = self.claimed()
        self.disable()
        before = self.state(claim)
        with self.assertRaises(DBAPIError):
            with tenant_transaction(self.worker_engine, self.user_one) as connection:
                connection.scalar(text('SELECT careertrellis.cancel_disabled_search_run('
                    'CAST(:run AS uuid), :owner, CAST(:lease AS uuid))'),
                    {'run': claim.run_id, 'owner': self.user_two, 'lease': claim.lease_token})
        with self.assertRaises(DBAPIError):
            with tenant_transaction(self.worker_engine, self.user_two) as connection:
                connection.scalar(text('SELECT careertrellis_admission.cancel_disabled_search_run('
                    'CAST(:run AS uuid), :owner, NULL, true)'),
                    {'run': claim.run_id, 'owner': self.user_two})
        self.assertEqual(before, self.state(claim))

    def test_worker_still_cannot_read_or_write_disabled_tenant(self):
        claim = self.claimed()
        self.disable()
        before = self.state(claim)
        with tenant_transaction(self.worker_engine, self.user_two) as connection:
            self.assertIsNone(connection.scalar(text('SELECT careertrellis.current_app_user_id()')))
            self.assertEqual(0, connection.scalar(text('SELECT count(*) FROM careertrellis.search_runs')))
            self.assertEqual(0, connection.execute(text('UPDATE careertrellis.search_runs SET '
                "keywords='forbidden' WHERE id=CAST(:run AS uuid)"), {'run': claim.run_id}).rowcount)
        self.assertEqual(before, self.state(claim))

    def test_parallel_cleanup_has_one_winner_and_no_duplicate_transition(self):
        claim = self.claimed()
        self.disable()
        barrier = Barrier(2, timeout=5)

        def finish():
            barrier.wait()
            return self.cancel(claim)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(finish) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual([False, True], sorted(results))
        self.assertEqual('cancelled', self.state(claim)['status'])

    def test_claim_lock_waits_for_queue_before_locking_run(self):
        claim = self.claimed()
        started = Event()
        worker_pid = []

        def lock_claim():
            with tenant_transaction(self.worker_engine, self.user_two) as connection:
                connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
                worker_pid.append(connection.scalar(text('SELECT pg_backend_pid()')))
                started.set()
                row = connection.execute(text('SELECT * FROM careertrellis.lock_search_run_claim('
                    'CAST(:run AS uuid), :owner, CAST(:lease AS uuid))'),
                    {'run': claim.run_id, 'owner': self.user_two,
                     'lease': claim.lease_token}).mappings().one()
                return dict(row)

        # Hold the queue only. Once the worker is proven blocked, the run must
        # still be independently lockable; the historical run-first path fails.
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.admin_engine.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(text('SELECT run_id FROM careertrellis.search_run_claim_queue '
                        'WHERE run_id=CAST(:run AS uuid) FOR UPDATE'), {'run': claim.run_id}).one()
                    future = executor.submit(lock_claim)
                    self.assertTrue(started.wait(timeout=2), 'worker did not start')
                    deadline = time.monotonic() + 3
                    blocked = False
                    while time.monotonic() < deadline:
                        connection.execute(text('SELECT pg_stat_clear_snapshot()'))
                        blocked = connection.scalar(text("SELECT wait_event_type='Lock' "
                            "AND query LIKE '%lock_search_run_claim%' FROM pg_stat_activity "
                            'WHERE pid=:pid'), {'pid': worker_pid[0]})
                        if blocked:
                            break
                        if future.done():
                            future.result()
                            self.fail('worker bypassed the held queue lock')
                        Event().wait(0.01)
                    self.assertTrue(blocked, 'worker did not block on the held queue')
                    connection.execute(text('SELECT id FROM careertrellis.search_runs '
                        'WHERE id=CAST(:run AS uuid) FOR UPDATE NOWAIT'), {'run': claim.run_id}).one()
                    transaction.commit()
                finally:
                    if transaction.is_active:
                        transaction.rollback()
            row = future.result(timeout=6)
        self.assertEqual('running', row['run_status'])
        self.assertIs(row['owner_disabled'], False)

    def test_claim_lock_does_not_wait_for_uncommitted_disable_row(self):
        claim = self.claimed()
        locked = Event()
        finish = Event()
        disabling = Event()
        disable_pid = []
        query = text('SELECT * FROM careertrellis.lock_search_run_claim('
            'CAST(:run AS uuid), :owner, CAST(:lease AS uuid))')
        parameters = {'run': claim.run_id, 'owner': self.user_two, 'lease': claim.lease_token}

        def worker():
            with tenant_transaction(self.worker_engine, self.user_two) as connection:
                connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
                connection.execute(query, parameters).one()
                locked.set()
                if not finish.wait(timeout=4):
                    raise AssertionError('disable was not observed')
                # Its AFTER trigger is waiting on our lifecycle lock. Reading
                # the previously committed owner state must not wait on its row.
                row = connection.execute(query, parameters).mappings().one()
                connection.execute(text("UPDATE careertrellis.search_runs SET status='succeeded', "
                    'completed_at=clock_timestamp(), lease_token=NULL, lease_expires_at=NULL, '
                    'worker_id=NULL WHERE id=CAST(:run AS uuid)'), {'run': claim.run_id})
                return row['owner_disabled']

        def disable():
            with self.admin_engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
                disable_pid.append(connection.scalar(text('SELECT pg_backend_pid()')))
                disabling.set()
                connection.execute(text('UPDATE careertrellis.app_users SET disabled_at=clock_timestamp() '
                    'WHERE id=:owner'), {'owner': self.user_two})

        with ThreadPoolExecutor(max_workers=2) as executor:
            worker_future = executor.submit(worker)
            try:
                self.assertTrue(locked.wait(timeout=2))
                disable_future = executor.submit(disable)
                self.assertTrue(disabling.wait(timeout=2))
                deadline = time.monotonic() + 3
                blocked = False
                with self.admin_engine.connect() as connection:
                    while time.monotonic() < deadline:
                        connection.execute(text('SELECT pg_stat_clear_snapshot()'))
                        blocked = connection.scalar(text("SELECT wait_event_type='Lock' AND wait_event='advisory' "
                            "AND query LIKE '%UPDATE careertrellis.app_users%' FROM pg_stat_activity "
                            'WHERE pid=:pid'), {'pid': disable_pid[0]})
                        if blocked:
                            break
                        if disable_future.done():
                            disable_future.result()
                            self.fail('disable bypassed the held lifecycle lock')
                        Event().wait(0.01)
                self.assertTrue(blocked, 'disable did not reach the lifecycle fence')
            finally:
                finish.set()
            self.assertIs(worker_future.result(timeout=6), False)
            disable_future.result(timeout=6)
        self.assertEqual('succeeded', self.state(claim)['status'])


if __name__ == '__main__':
    unittest.main()
