"""Synthetic standalone tests for durable search-run control."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch

import database as database_module
import config as config_module
import search_run_execution as execution_module
from operations import OperationCancelled
from search_run_repository import (
    SQLiteSearchRunRepository,
    SearchRunAlreadyActive,
    SearchRunLeaseLost,
)


class SQLiteSearchRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "search-runs.db"
        with patch.object(database_module, "DB_PATH", self.database_path):
            database_module.init_db()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def connection(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def repository(self, owner: int = 1) -> SQLiteSearchRunRepository:
        return SQLiteSearchRunRepository(owner, self.connection)

    def test_owner_isolation_single_active_run_and_minimized_result(self) -> None:
        one = self.repository(1)
        two = self.repository(2)
        run_one = one.enqueue(
            "00000000-0000-4000-8000-000000000001", "security", "Remote"
        )
        two.enqueue(
            "00000000-0000-4000-8000-000000000002", "platform", "Ohio"
        )
        with self.assertRaises(SearchRunAlreadyActive):
            one.enqueue(
                "00000000-0000-4000-8000-000000000003", "other", ""
            )
        claim = one.claim(run_one, "10000000-0000-4000-8000-000000000001")
        one.succeed(
            claim,
            {
                "success": True,
                "jobs_added": 7,
                "provider_alerts": [{"secret": "do-not-store"}],
                "raw": "private",
            },
        )
        result = one.status()["last_result"]
        self.assertEqual(7, result["jobs_added"])
        self.assertNotIn("raw", result)
        self.assertNotIn("do-not-store", json.dumps(result))
        self.assertTrue(two.status()["searching"])

    def test_queued_and_running_cancellation_are_durable(self) -> None:
        repository = self.repository()
        queued_operation = "00000000-0000-4000-8000-000000000011"
        repository.enqueue(queued_operation, "queued", "")
        self.assertTrue(repository.request_cancel(queued_operation))
        self.assertTrue(repository.status()["last_result"]["cancelled"])

        running_operation = "00000000-0000-4000-8000-000000000012"
        run_id = repository.enqueue(running_operation, "running", "")
        claim = repository.claim(run_id, "10000000-0000-4000-8000-000000000011")
        self.assertTrue(repository.request_cancel(running_operation))
        with self.assertRaises(OperationCancelled):
            repository.heartbeat(claim)
        repository.cancel(claim)
        self.assertTrue(repository.status()["last_result"]["cancelled"])

        race_operation = "00000000-0000-4000-8000-000000000013"
        race_run = repository.enqueue(race_operation, "completion race", "")
        race_claim = repository.claim(
            race_run, "10000000-0000-4000-8000-000000000013"
        )
        repository.request_cancel(race_operation)
        repository.succeed(race_claim, {"success": True, "jobs_added": 10})
        self.assertTrue(repository.status()["last_result"]["cancelled"])

    def test_two_workers_cannot_claim_the_same_run(self) -> None:
        repository = self.repository()
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000021", "concurrent", ""
        )
        barrier = Barrier(2)

        def claim(worker_id: str):
            barrier.wait()
            return self.repository().claim(run_id, worker_id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    claim,
                    (
                        "10000000-0000-4000-8000-000000000021",
                        "10000000-0000-4000-8000-000000000022",
                    ),
                )
            )
        self.assertEqual(1, sum(item is not None for item in claims))

    def test_expired_claim_retries_and_stale_worker_cannot_complete(self) -> None:
        repository = self.repository()
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000031", "retry", ""
        )
        first = repository.claim(run_id, "10000000-0000-4000-8000-000000000031")
        connection = self.connection()
        connection.execute(
            "UPDATE search_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (run_id,),
        )
        connection.commit()
        connection.close()
        second = repository.claim(run_id, "10000000-0000-4000-8000-000000000032")
        self.assertEqual(2, second.attempt)
        with self.assertRaises(SearchRunLeaseLost):
            repository.succeed(first, {"success": True, "jobs_added": 99})
        repository.succeed(second, {"success": True, "jobs_added": 2})
        self.assertEqual(2, repository.status()["last_result"]["jobs_added"])

    def test_execution_binds_owner_and_persists_fixed_failure(self) -> None:
        repository = self.repository(42)
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000041", "owner", ""
        )
        claim = repository.claim(run_id, "10000000-0000-4000-8000-000000000041")
        observed_owners = []

        def fail(*_args):
            from provider_secrets import current_owner_user_id

            observed_owners.append(current_owner_user_id())
            raise RuntimeError("postgresql://secret@private.test/database")

        with patch.object(execution_module, "run_job_search_and_matching", side_effect=fail):
            execution_module.execute_search_claim(claim, repository)
        self.assertEqual([42], observed_owners)
        serialized = json.dumps(repository.status())
        self.assertIn("The job search could not be completed.", serialized)
        self.assertNotIn("private.test", serialized)

    def test_execution_checks_durable_cancel_before_provider_work(self) -> None:
        repository = self.repository()
        operation = "00000000-0000-4000-8000-000000000051"
        run_id = repository.enqueue(operation, "cancel", "")
        claim = repository.claim(run_id, "10000000-0000-4000-8000-000000000051")
        repository.request_cancel(operation)
        provider = Mock()
        with patch.object(execution_module, "run_job_search_and_matching", provider):
            execution_module.execute_search_claim(claim, repository)
        provider.assert_not_called()
        self.assertTrue(repository.status()["last_result"]["cancelled"])

    def test_queue_connections_close_before_and_during_provider_work(self) -> None:
        open_connections = []

        class TrackedConnection:
            def __init__(self, connection):
                self._connection = connection
                open_connections.append(self)

            def close(self):
                self._connection.close()
                open_connections.remove(self)

            def __getattr__(self, name):
                return getattr(self._connection, name)

        def tracked_factory():
            return TrackedConnection(self.connection())

        repository = SQLiteSearchRunRepository(1, tracked_factory)
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000061", "transactions", ""
        )
        claim = repository.claim(run_id, "10000000-0000-4000-8000-000000000061")
        self.assertEqual([], open_connections)

        def provider(_keywords, _location, checkpoint):
            self.assertEqual([], open_connections)
            checkpoint()
            self.assertEqual([], open_connections)
            return {"success": True, "jobs_added": 1, "provider_alerts": []}

        with patch.object(execution_module, "run_job_search_and_matching", provider):
            execution_module.execute_search_claim(claim, repository)
        self.assertEqual([], open_connections)

    def test_worker_activation_switch_is_strict_and_disabled_by_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(config_module.search_worker_enabled())
        with patch.dict(
            "os.environ", {"CAREERTRELLIS_SEARCH_WORKER_ENABLED": "yes"}, clear=True
        ):
            with self.assertRaises(config_module.RuntimeConfigurationError):
                config_module.search_worker_enabled()

    def test_worker_stop_leaves_lease_retryable_without_recording_user_cancel(self) -> None:
        repository = self.repository()
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000071", "restart recovery", ""
        )
        first = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000071"
        )
        provider = Mock()
        with patch.object(execution_module, "run_job_search_and_matching", provider):
            execution_module.execute_search_claim(
                first,
                repository,
                stop_requested=lambda: True,
            )
        provider.assert_not_called()
        self.assertTrue(repository.status()["searching"])
        self.assertIsNone(repository.status()["last_result"])

        connection = self.connection()
        connection.execute(
            "UPDATE search_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (run_id,),
        )
        connection.commit()
        connection.close()
        second = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000072"
        )
        self.assertEqual(2, second.attempt)
        with self.assertRaises(SearchRunLeaseLost):
            repository.fail(first)
        repository.succeed(second, {"success": True, "jobs_added": 1})
        self.assertFalse(repository.status()["searching"])


if __name__ == "__main__":
    unittest.main()
