"""Disposable PostgreSQL checks for worker-only scheduled-search dispatch."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import unittest
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import test_postgres_rls as postgres_harness
from scheduled_search_dispatch import (
    DEFAULT_DISPATCH_LIMIT,
    ScheduledSearchDispatchUnavailable,
    dispatch_due_postgres_saved_searches,
)
from search_run_repository import (
    PostgreSQLSearchRunRepository,
    SearchRunLeaseLost,
    claim_next_postgres_search_run,
)


class PostgreSQLScheduledSearchDispatchTests(unittest.TestCase):
    """Reuse the isolated PostgreSQL 18 harness without importing live data."""

    @classmethod
    def setUpClass(cls) -> None:
        harness = postgres_harness.PostgreSQLTenantIsolationTests
        harness.setUpClass()
        cls._harness = harness
        for name in (
            "admin_engine",
            "runtime_engine",
            "worker_engine",
            "migration_url",
        ):
            setattr(cls, name, getattr(harness, name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._harness.tearDownClass()

    def setUp(self) -> None:
        postgres_harness.PostgreSQLTenantIsolationTests.setUp(self)

    def _insert_due_search(
        self,
        owner_user_id: int,
        due_at: datetime,
        *,
        keywords: str,
        location: str,
        frequency: str = "daily",
    ) -> int:
        with self.admin_engine.begin() as connection:
            return int(
                connection.scalar(
                    text(
                        """INSERT INTO careertrellis.saved_searches (
                            owner_user_id, name, keywords, location,
                            schedule_frequency, next_alert_at, enabled
                        ) VALUES (
                            :owner, 'Scheduled synthetic search', :keywords, :location,
                            :frequency, :due_at, TRUE
                        ) RETURNING id"""
                    ),
                    {
                        "owner": owner_user_id,
                        "keywords": keywords,
                        "location": location,
                        "frequency": frequency,
                        "due_at": due_at,
                    },
                )
            )

    def test_two_workers_dispatch_each_occurrence_once_and_advance_atomically(self) -> None:
        due_one = datetime.now(timezone.utc) - timedelta(minutes=2)
        due_two = due_one + timedelta(seconds=1)
        search_one = self._insert_due_search(
            self.user_one,
            due_one,
            keywords="security engineering",
            location="Remote",
        )
        search_two = self._insert_due_search(
            self.user_two,
            due_two,
            keywords="platform engineering",
            location="Ohio",
            frequency="weekly",
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            batches = list(
                executor.map(
                    lambda _index: dispatch_due_postgres_saved_searches(
                        engine=self.worker_engine, limit=1
                    ),
                    range(2),
                )
            )
        dispatched = [item for batch in batches for item in batch]

        self.assertEqual({search_one, search_two}, {item.saved_search_id for item in dispatched})
        self.assertEqual({self.user_one, self.user_two}, {item.owner_user_id for item in dispatched})
        self.assertEqual([], dispatch_due_postgres_saved_searches(engine=self.worker_engine))

        with self.admin_engine.begin() as connection:
            runs = connection.execute(
                text(
                    """SELECT owner_user_id, operation_id, keywords, location, status,
                              scheduled_search_id, scheduled_for
                       FROM careertrellis.search_runs
                       ORDER BY owner_user_id"""
                )
            ).mappings().all()
            searches = connection.execute(
                text(
                    """SELECT id, last_run_at, next_alert_at
                       FROM careertrellis.saved_searches
                       WHERE id IN (:one, :two) ORDER BY id"""
                ),
                {"one": search_one, "two": search_two},
            ).mappings().all()

        self.assertEqual(2, len(runs))
        self.assertEqual(["queued", "queued"], [row["status"] for row in runs])
        self.assertEqual(
            {"security engineering", "platform engineering"},
            {row["keywords"] for row in runs},
        )
        self.assertEqual({search_one, search_two}, {row["scheduled_search_id"] for row in runs})
        for row in runs:
            digest = hashlib.md5(
                (
                    "careertrellis-scheduled-search-v1:"
                    f"{row['owner_user_id']}:{row['scheduled_search_id']}:"
                    f"{row['scheduled_for'].astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')}"
                ).encode("utf-8")
            ).hexdigest()
            self.assertEqual(UUID(digest), row["operation_id"])
        self.assertTrue(all(row["last_run_at"] is not None for row in searches))
        self.assertEqual(due_one + timedelta(days=1), searches[0]["next_alert_at"])
        self.assertEqual(due_two + timedelta(days=7), searches[1]["next_alert_at"])

    def test_active_owner_and_disabled_search_are_not_advanced(self) -> None:
        due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        saved_search_id = self._insert_due_search(
            self.user_one,
            due_at,
            keywords="blocked while active",
            location="Remote",
        )
        repository = PostgreSQLSearchRunRepository(
            self.user_one, engine=self.runtime_engine
        )
        operation_id = "00000000-0000-4000-8000-000000000801"
        active_run = repository.enqueue(operation_id, "manual", "")
        repository.publish_prepared(active_run, "manual", "")

        self.assertEqual([], dispatch_due_postgres_saved_searches(engine=self.worker_engine))
        with self.admin_engine.begin() as connection:
            saved = connection.execute(
                text(
                    """SELECT last_run_at, next_alert_at
                       FROM careertrellis.saved_searches WHERE id=:id"""
                ),
                {"id": saved_search_id},
            ).mappings().one()
            deferred_until = connection.scalar(
                text(
                    """SELECT due_at
                       FROM careertrellis.scheduled_search_dispatch_queue
                       WHERE saved_search_id=:id"""
                ),
                {"id": saved_search_id},
            )
            database_now = connection.scalar(text("SELECT CURRENT_TIMESTAMP"))
        self.assertIsNone(saved["last_run_at"])
        self.assertEqual(due_at, saved["next_alert_at"])
        self.assertGreater(deferred_until, database_now)

        self.assertTrue(repository.request_cancel(operation_id))
        with self.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """UPDATE careertrellis.saved_searches SET enabled=enabled
                       WHERE id=:id"""
                ),
                {"id": saved_search_id},
            )
        dispatched = dispatch_due_postgres_saved_searches(engine=self.worker_engine)
        self.assertEqual([saved_search_id], [item.saved_search_id for item in dispatched])

        disabled_id = self._insert_due_search(
            self.user_two,
            due_at,
            keywords="disabled",
            location="",
        )
        with self.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE careertrellis.saved_searches SET enabled=FALSE WHERE id=:id"
                ),
                {"id": disabled_id},
            )
        self.assertEqual([], dispatch_due_postgres_saved_searches(engine=self.worker_engine))

    def test_disabled_owner_schedule_is_deferred_without_provider_work(self) -> None:
        due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        disabled_search = self._insert_due_search(
            self.user_one,
            due_at,
            keywords="disabled owner schedule",
            location="Remote",
        )
        active_search = self._insert_due_search(
            self.user_two,
            due_at + timedelta(seconds=1),
            keywords="active owner schedule",
            location="Ohio",
        )
        with self.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE careertrellis.app_users "
                    "SET disabled_at = CURRENT_TIMESTAMP WHERE id = :owner"
                ),
                {"owner": self.user_one},
            )

        dispatched = dispatch_due_postgres_saved_searches(
            engine=self.worker_engine, limit=2
        )
        self.assertEqual([active_search], [item.saved_search_id for item in dispatched])
        self.assertEqual([self.user_two], [item.owner_user_id for item in dispatched])
        with self.admin_engine.begin() as connection:
            disabled = connection.execute(
                text(
                    "SELECT last_run_at, next_alert_at "
                    "FROM careertrellis.saved_searches WHERE id = :id"
                ),
                {"id": disabled_search},
            ).mappings().one()
            deferred_until = connection.scalar(
                text(
                    "SELECT due_at FROM careertrellis.scheduled_search_dispatch_queue "
                    "WHERE saved_search_id = :id"
                ),
                {"id": disabled_search},
            )
            database_now = connection.scalar(text("SELECT CURRENT_TIMESTAMP"))
            disabled_runs = connection.scalar(
                text(
                    "SELECT count(*) FROM careertrellis.search_runs "
                    "WHERE scheduled_search_id = :id"
                ),
                {"id": disabled_search},
            )
        self.assertIsNone(disabled["last_run_at"])
        self.assertEqual(due_at, disabled["next_alert_at"])
        self.assertGreater(deferred_until, database_now)
        self.assertEqual(0, disabled_runs)

    def test_more_than_one_batch_of_active_owner_rows_does_not_starve_later_owner(
        self,
    ) -> None:
        due_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        blocked_searches = [
            self._insert_due_search(
                self.user_one,
                due_at + timedelta(seconds=index),
                keywords=f"blocked synthetic query {index}",
                location="Remote",
            )
            for index in range(DEFAULT_DISPATCH_LIMIT + 1)
        ]
        eligible_search = self._insert_due_search(
            self.user_two,
            due_at + timedelta(seconds=DEFAULT_DISPATCH_LIMIT + 2),
            keywords="eligible synthetic query",
            location="Ohio",
        )
        repository = PostgreSQLSearchRunRepository(
            self.user_one, engine=self.runtime_engine
        )
        operation_id = "00000000-0000-4000-8000-000000000811"
        active_run = repository.enqueue(operation_id, "manual", "")
        repository.publish_prepared(active_run, "manual", "")

        self.assertEqual(
            [],
            dispatch_due_postgres_saved_searches(engine=self.worker_engine),
        )
        dispatched = dispatch_due_postgres_saved_searches(engine=self.worker_engine)

        self.assertEqual([eligible_search], [item.saved_search_id for item in dispatched])
        with self.admin_engine.begin() as connection:
            blocked = connection.execute(
                text(
                    """SELECT id, last_run_at, next_alert_at
                       FROM careertrellis.saved_searches
                       WHERE id = ANY(CAST(:ids AS bigint[])) ORDER BY id"""
                ),
                {"ids": blocked_searches},
            ).mappings().all()
            broker_due = connection.execute(
                text(
                    """SELECT due_at
                       FROM careertrellis.scheduled_search_dispatch_queue
                       WHERE saved_search_id = ANY(CAST(:ids AS bigint[]))"""
                ),
                {"ids": blocked_searches},
            ).scalars().all()
            database_now = connection.scalar(text("SELECT CURRENT_TIMESTAMP"))
            eligible_run_count = connection.scalar(
                text(
                    """SELECT count(*) FROM careertrellis.search_runs
                       WHERE owner_user_id=:owner AND scheduled_search_id=:saved"""
                ),
                {"owner": self.user_two, "saved": eligible_search},
            )

        self.assertEqual(blocked_searches, [row["id"] for row in blocked])
        self.assertTrue(all(row["last_run_at"] is None for row in blocked))
        self.assertEqual(
            [
                due_at + timedelta(seconds=index)
                for index in range(DEFAULT_DISPATCH_LIMIT + 1)
            ],
            [row["next_alert_at"] for row in blocked],
        )
        self.assertEqual(DEFAULT_DISPATCH_LIMIT + 1, len(broker_due))
        self.assertTrue(all(value > database_now for value in broker_due))
        self.assertEqual(1, eligible_run_count)

    def test_dispatched_occurrence_uses_existing_lease_recovery_path(self) -> None:
        saved_search_id = self._insert_due_search(
            self.user_one,
            datetime.now(timezone.utc) - timedelta(minutes=1),
            keywords="recover after worker exit",
            location="Remote",
        )
        dispatched = dispatch_due_postgres_saved_searches(engine=self.worker_engine)
        self.assertEqual([saved_search_id], [item.saved_search_id for item in dispatched])

        first = claim_next_postgres_search_run(
            "10000000-0000-4000-8000-000000000801",
            engine=self.worker_engine,
        )
        self.assertEqual(dispatched[0].run_id, first.run_id)
        self.assertEqual(self.user_one, first.owner_user_id)
        with self.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """UPDATE careertrellis.search_runs
                       SET lease_expires_at=CURRENT_TIMESTAMP - interval '1 second'
                       WHERE id=CAST(:run_id AS uuid)"""
                ),
                {"run_id": first.run_id},
            )

        second = claim_next_postgres_search_run(
            "10000000-0000-4000-8000-000000000802",
            engine=self.worker_engine,
        )
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(2, second.attempt)
        repository = PostgreSQLSearchRunRepository(
            self.user_one, engine=self.worker_engine
        )
        with self.assertRaises(SearchRunLeaseLost):
            repository.succeed(first, {"success": True, "jobs_added": 99})
        repository.succeed(second, {"success": True, "jobs_added": 1})

    def test_broker_is_private_and_dispatch_is_worker_only(self) -> None:
        for engine in (self.runtime_engine, self.worker_engine):
            with self.subTest(role=str(engine.url.username)), self.assertRaises(DBAPIError):
                with engine.begin() as connection:
                    connection.execute(
                        text("SELECT * FROM careertrellis.scheduled_search_dispatch_queue")
                    )

        with self.assertRaises(ScheduledSearchDispatchUnavailable):
            dispatch_due_postgres_saved_searches(engine=self.runtime_engine)

    def test_invalid_persisted_requests_are_quarantined_without_blocking_later_owner(
        self,
    ) -> None:
        due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        bad_keywords_id = self._insert_due_search(
            self.user_one,
            due_at,
            keywords="x" * 501,
            location="Synthetic",
        )
        bad_location_id = self._insert_due_search(
            self.user_one,
            due_at + timedelta(seconds=1),
            keywords="synthetic",
            location="x" * 241,
        )
        eligible_id = self._insert_due_search(
            self.user_two,
            due_at + timedelta(seconds=2),
            keywords="eligible after invalid requests",
            location="Remote",
        )

        dispatched = dispatch_due_postgres_saved_searches(
            engine=self.worker_engine, limit=3
        )

        self.assertEqual([eligible_id], [item.saved_search_id for item in dispatched])
        with self.admin_engine.begin() as connection:
            invalid = connection.execute(
                text(
                    """SELECT id, last_run_at, next_alert_at
                       FROM careertrellis.saved_searches
                       WHERE id IN (:keywords_id, :location_id) ORDER BY id"""
                ),
                {
                    "keywords_id": bad_keywords_id,
                    "location_id": bad_location_id,
                },
            ).mappings().all()
            quarantine_due = connection.execute(
                text(
                    """SELECT due_at
                       FROM careertrellis.scheduled_search_dispatch_queue
                       WHERE saved_search_id IN (:keywords_id, :location_id)"""
                ),
                {
                    "keywords_id": bad_keywords_id,
                    "location_id": bad_location_id,
                },
            ).scalars().all()
            database_now = connection.scalar(text("SELECT CURRENT_TIMESTAMP"))
            invalid_run_count = connection.scalar(
                text(
                    """SELECT count(*) FROM careertrellis.search_runs
                       WHERE scheduled_search_id IN (:keywords_id, :location_id)"""
                ),
                {
                    "keywords_id": bad_keywords_id,
                    "location_id": bad_location_id,
                },
            )
        self.assertEqual(
            [bad_keywords_id, bad_location_id], [row["id"] for row in invalid]
        )
        self.assertTrue(all(row["last_run_at"] is None for row in invalid))
        self.assertEqual(
            [due_at, due_at + timedelta(seconds=1)],
            [row["next_alert_at"] for row in invalid],
        )
        self.assertEqual(2, len(quarantine_due))
        self.assertTrue(all(value > database_now for value in quarantine_due))
        self.assertEqual(0, invalid_run_count)

        with self.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """UPDATE careertrellis.saved_searches
                       SET keywords='repaired synthetic request'
                       WHERE id=:id"""
                ),
                {"id": bad_keywords_id},
            )
        repaired = dispatch_due_postgres_saved_searches(engine=self.worker_engine)
        self.assertEqual([bad_keywords_id], [item.saved_search_id for item in repaired])


if __name__ == "__main__":
    unittest.main()
