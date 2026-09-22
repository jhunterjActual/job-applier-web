"""Synthetic standalone tests for durable search-run control."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock, current_thread
from unittest.mock import ANY, Mock, patch

import database as database_module
import config as config_module
import search_run_execution as execution_module
import worker as worker_module
from operations import OperationCancelled
from search_run_repository import (
    SEARCH_FAILED_MESSAGE,
    PostgreSQLSearchRunRepository,
    SQLiteSearchRunRepository,
    SearchRunAlreadyActive,
    SearchRunLeaseLost,
    minimized_search_result,
)


class SQLiteSearchRunTests(unittest.TestCase):
    def test_outcome_summary_survives_durable_readback_without_private_details(self):
        repository = self.repository()
        run = repository.enqueue("00000000-0000-4000-8000-000000000099", "synthetic", "")
        repository.publish_prepared(run, "synthetic", "")
        claim = repository.claim(run, "10000000-0000-4000-8000-000000000099")
        repository.fail(claim, "provider_search_failed", {
            "success": False, "outcome": "source_unavailable", "error": "PRIVATE",
            "counts": {"discovered": 0, "saved": -1, "private": "PRIVATE"},
            "provider_alerts": [{"provider": "lever", "code": "provider_error", "message": "PRIVATE"}],
        })
        result = repository.status()["last_result"]
        self.assertFalse(result["success"])
        self.assertEqual("source_unavailable", result["outcome"])
        self.assertEqual(0, result["counts"]["saved"])
        self.assertEqual("provider_error", result["provider_alerts"][0]["code"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(result, minimized_search_result(result))

    def test_outcome_minimizer_bounds_counts_and_reconstructs_messages(self):
        for outcome in ("no_results", "no_new_matches", "matches_saved", "partial_results"):
            with self.subTest(outcome=outcome):
                result = minimized_search_result({
                    "success": True, "outcome": outcome, "jobs_added": 2,
                    "message": "PRIVATE", "counts": {"discovered": 9999999, "saved": True},
                    "provider_alerts": [{"provider": [], "code": {}}, {"provider": "private", "code": "provider_error"}],
                })
                self.assertEqual(outcome, result["outcome"])
                self.assertEqual(100000, result["counts"]["discovered"])
                self.assertEqual(0, result["counts"]["saved"])
                self.assertEqual([], result["provider_alerts"])
                self.assertEqual(outcome == "partial_results", result["partial"])
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_source_health_history_keeps_only_bounded_aggregate_diagnostics(self):
        from source_diagnostics import safe_provider_counters
        counters = safe_provider_counters({
            "source_attempts": 3, "source_failures": 1, "source_unusable": 2,
            "source_empty": 0, "source_fallbacks": 2,
            "query": "PRIVATE", "response": "PRIVATE",
        })
        self.assertEqual(3, counters["source_attempts"])
        self.assertEqual(1, counters["source_failures"])
        self.assertEqual(2, counters["source_unusable"])
        self.assertNotIn("PRIVATE", json.dumps(counters))

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
        one.publish_prepared(run_one, "security", "Remote")
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
        preparing_operation = "00000000-0000-4000-8000-000000000010"
        repository.enqueue(preparing_operation, "preparing", "")
        self.assertTrue(repository.request_cancel(preparing_operation))
        self.assertTrue(repository.status()["last_result"]["cancelled"])

        queued_operation = "00000000-0000-4000-8000-000000000011"
        queued_run = repository.enqueue(queued_operation, "queued", "")
        repository.publish_prepared(queued_run, "queued", "")
        self.assertTrue(repository.request_cancel(queued_operation))
        self.assertTrue(repository.status()["last_result"]["cancelled"])

        running_operation = "00000000-0000-4000-8000-000000000012"
        run_id = repository.enqueue(running_operation, "running", "")
        repository.publish_prepared(run_id, "running", "")
        claim = repository.claim(run_id, "10000000-0000-4000-8000-000000000011")
        self.assertTrue(repository.request_cancel(running_operation))
        with self.assertRaises(OperationCancelled):
            repository.heartbeat(claim)
        repository.cancel(claim)
        self.assertTrue(repository.status()["last_result"]["cancelled"])

        race_operation = "00000000-0000-4000-8000-000000000013"
        race_run = repository.enqueue(race_operation, "completion race", "")
        repository.publish_prepared(race_run, "completion race", "")
        race_claim = repository.claim(
            race_run, "10000000-0000-4000-8000-000000000013"
        )
        repository.request_cancel(race_operation)
        repository.succeed(race_claim, {"success": True, "jobs_added": 10})
        self.assertTrue(repository.status()["last_result"]["cancelled"])

    def test_active_search_can_be_recovered_and_cancelled_without_operation_id(self) -> None:
        repository = self.repository()
        other_owner = self.repository(2)
        other_owner.enqueue(
            "00000000-0000-4000-8000-000000000114", "other owner", ""
        )
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000014", "private keywords", "Private place"
        )
        preparing = repository.status()
        self.assertTrue(preparing["searching"])
        self.assertEqual("preparing", preparing["active_search"]["phase"])
        self.assertIsNotNone(preparing["active_search"]["started_at"])
        self.assertIsNotNone(preparing["active_search"]["last_activity_at"])
        self.assertNotIn("private keywords", json.dumps(preparing).lower())
        self.assertNotIn("private place", json.dumps(preparing).lower())
        self.assertNotIn("00000000-0000-4000-8000-000000000014", json.dumps(preparing))

        repository.publish_prepared(run_id, "private keywords", "Private place")
        claim = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000014"
        )
        running = repository.status()
        self.assertEqual("preparing_keywords", running["active_search"]["phase"])
        self.assertFalse(running["active_search"]["stop_requested"])

        self.assertTrue(repository.request_cancel_active())
        stopping = repository.status()
        self.assertEqual("cancel_requested", stopping["active_search"]["phase"])
        self.assertTrue(stopping["active_search"]["stop_requested"])
        with self.assertRaises(OperationCancelled):
            repository.heartbeat(claim)
        repository.cancel(claim)
        self.assertFalse(repository.status()["searching"])
        self.assertTrue(repository.status()["last_result"]["cancelled"])
        self.assertTrue(other_owner.status()["searching"])
        self.assertFalse(repository.request_cancel_active())

    def test_expired_orphaned_search_is_cancelled_immediately(self) -> None:
        repository = self.repository()
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000015", "orphan", ""
        )
        repository.publish_prepared(run_id, "orphan", "")
        claim = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000015"
        )
        connection = self.connection()
        connection.execute(
            "UPDATE search_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (run_id,),
        )
        connection.commit()
        connection.close()

        self.assertTrue(repository.request_cancel_active())
        self.assertFalse(repository.status()["searching"])
        self.assertTrue(repository.status()["last_result"]["cancelled"])
        with self.assertRaises(SearchRunLeaseLost):
            repository.succeed(claim, {"success": True, "jobs_added": 99})

    def test_postgres_active_cancel_is_owner_scoped_and_uses_database_lease_time(self) -> None:
        for lease_expired, expected_status in (
            (False, "cancel_requested"),
            (True, "cancelled"),
        ):
            with self.subTest(lease_expired=lease_expired):
                connection = Mock()
                selected = Mock()
                selected.mappings.return_value.first.return_value = {
                    "id": "00000000-0000-4000-8000-000000000016",
                    "status": "running",
                    "lease_expires_at": object(),
                    "lease_expired": lease_expired,
                }
                connection.execute.side_effect = [selected, Mock()]
                transaction = Mock()
                transaction.__enter__ = Mock(return_value=connection)
                transaction.__exit__ = Mock(return_value=False)
                with patch(
                    "search_run_repository.tenant_transaction",
                    return_value=transaction,
                ):
                    repository = PostgreSQLSearchRunRepository(42, engine=Mock())
                    self.assertTrue(repository.request_cancel_active())

                select_sql = str(connection.execute.call_args_list[0].args[0])
                self.assertIn("owner_user_id=:owner", select_sql)
                self.assertIn("lease_expires_at <= CURRENT_TIMESTAMP", select_sql)
                self.assertIn("FOR UPDATE", select_sql)
                update_parameters = connection.execute.call_args_list[1].args[1]
                self.assertEqual(42, update_parameters["owner"])
                self.assertEqual(expected_status, update_parameters["status"])
                self.assertEqual(lease_expired, update_parameters["terminal"])

    def test_two_workers_cannot_claim_the_same_run(self) -> None:
        repository = self.repository()
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000021", "concurrent", ""
        )
        repository.publish_prepared(run_id, "concurrent", "")
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
        repository.publish_prepared(run_id, "retry", "")
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

    def test_progress_is_distinct_from_lease_liveness_and_exposes_bounded_counts(self) -> None:
        repository = self.repository()
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000033", "progress", ""
        )
        repository.publish_prepared(run_id, "progress", "")
        claim = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000033"
        )
        repository.progress(claim, "inspecting_postings", 2, 7)
        status = repository.status()["active_search"]

        self.assertEqual("inspecting_postings", status["phase"])
        self.assertEqual(2, status["completed"])
        self.assertEqual(7, status["total"])
        self.assertEqual(1, status["attempt"])
        self.assertEqual(3, status["max_attempts"])
        self.assertIsNotNone(status["last_progress_at"])
        self.assertIsNotNone(status["last_heartbeat_at"])
        with self.assertRaises(ValueError):
            repository.progress(claim, "private-arbitrary-phase", 0, 1)

    def test_progress_renews_atomically_and_preserves_cancel_classification(self) -> None:
        repository = self.repository()
        operation = "00000000-0000-4000-8000-000000000034"
        run_id = repository.enqueue(operation, "atomic progress", "")
        repository.publish_prepared(run_id, "atomic progress", "")
        claim = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000034"
        )

        with patch.object(
            repository,
            "heartbeat",
            side_effect=AssertionError("progress must not renew in a separate transaction"),
        ):
            repository.progress(claim, "searching_sources", 1, 2)

        self.assertTrue(repository.request_cancel(operation))
        with self.assertRaises(OperationCancelled):
            repository.progress(claim, "searching_sources", 2, 2)

    def test_execution_binds_owner_and_persists_fixed_failure(self) -> None:
        repository = self.repository(42)
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000041", "owner", ""
        )
        repository.publish_prepared(run_id, "owner", "")
        claim = repository.claim(run_id, "10000000-0000-4000-8000-000000000041")
        observed_owners = []

        def fail(*_args, **_kwargs):
            from provider_secrets import current_owner_user_id

            observed_owners.append(current_owner_user_id())
            raise RuntimeError("postgresql://secret@private.test/database")

        with patch.object(execution_module, "run_job_search_and_matching", side_effect=fail):
            execution_module.execute_search_claim(claim, repository)
        self.assertEqual([42], observed_owners)
        serialized = json.dumps(repository.status())
        self.assertIn("The job search could not be completed.", serialized)
        self.assertNotIn("private.test", serialized)

    def test_match_provider_failure_keeps_only_safe_actionable_classification(self) -> None:
        repository = self.repository(42)
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000042", "owner", ""
        )
        repository.publish_prepared(run_id, "owner", "")
        claim = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000042"
        )
        provider_result = {
            "success": False,
            "error": "private provider response and credential",
            "failure_code": "rate_limit",
            "provider_alerts": [{"secret": "discard me"}],
        }

        with patch.object(
            execution_module,
            "run_job_search_and_matching",
            return_value=provider_result,
        ):
            execution_module.execute_search_claim(claim, repository)

        result = repository.status()["last_result"]
        self.assertFalse(result["success"])
        self.assertEqual("rate_limit", result["failure_code"])
        self.assertIn("rate-limited", result["error"])
        serialized = json.dumps(result)
        self.assertNotIn("private provider", serialized)
        self.assertNotIn("discard me", serialized)

    def test_unrecognized_failure_classification_remains_generic(self) -> None:
        minimized = minimized_search_result({
            "success": False,
            "error": "private provider response",
            "failure_code": "private-upstream-detail",
        })

        self.assertEqual({
            "success": False,
            "error": SEARCH_FAILED_MESSAGE,
            "provider_alerts": [],
        }, minimized)

    def test_execution_checks_durable_cancel_before_provider_work(self) -> None:
        repository = self.repository()
        operation = "00000000-0000-4000-8000-000000000051"
        run_id = repository.enqueue(operation, "cancel", "")
        repository.publish_prepared(run_id, "cancel", "")
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
        repository.publish_prepared(run_id, "transactions", "")
        claim = repository.claim(run_id, "10000000-0000-4000-8000-000000000061")
        self.assertEqual([], open_connections)

        def provider(_keywords, _location, checkpoint, **_kwargs):
            self.assertEqual([], open_connections)
            checkpoint()
            self.assertEqual([], open_connections)
            return {"success": True, "jobs_added": 1, "provider_alerts": []}

        with patch.object(execution_module, "run_job_search_and_matching", provider):
            execution_module.execute_search_claim(claim, repository)
        self.assertEqual([], open_connections)

    def test_execution_renews_lease_during_a_blocked_provider_step(self) -> None:
        repository = self.repository()
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000063", "long search", ""
        )
        repository.publish_prepared(run_id, "long search", "")
        claim = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000063"
        )
        original_heartbeat = repository.heartbeat
        heartbeat_lock = Lock()
        heartbeat_count = 0
        background_heartbeat = Event()

        def heartbeat(target_claim, lease_seconds=300):
            nonlocal heartbeat_count
            original_heartbeat(target_claim, lease_seconds)
            with heartbeat_lock:
                heartbeat_count += 1
                if heartbeat_count >= 2:
                    background_heartbeat.set()

        def provider(_keywords, _location, _checkpoint, **_kwargs):
            self.assertTrue(background_heartbeat.wait(1))
            return {"success": True, "jobs_added": 1, "provider_alerts": []}

        repository.heartbeat = heartbeat
        with (
            patch.object(execution_module, "LEASE_HEARTBEAT_SECONDS", 0.01),
            patch.object(execution_module, "run_job_search_and_matching", provider),
        ):
            execution_module.execute_search_claim(claim, repository)

        self.assertGreaterEqual(heartbeat_count, 2)
        self.assertEqual(1, repository.status()["last_result"]["jobs_added"])

    def test_background_heartbeat_observes_cancel_during_provider_step(self) -> None:
        repository = self.repository()
        operation = "00000000-0000-4000-8000-000000000064"
        run_id = repository.enqueue(operation, "long cancellation", "")
        repository.publish_prepared(run_id, "long cancellation", "")
        claim = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000064"
        )
        original_heartbeat = repository.heartbeat
        cancellation_observed = Event()

        def heartbeat(target_claim, lease_seconds=300):
            try:
                original_heartbeat(target_claim, lease_seconds)
            except OperationCancelled:
                cancellation_observed.set()
                raise

        def provider(_keywords, _location, _checkpoint, **_kwargs):
            self.assertTrue(repository.request_cancel(operation))
            self.assertTrue(cancellation_observed.wait(1))
            raise RuntimeError("provider failed after cancellation")

        repository.heartbeat = heartbeat
        with (
            patch.object(execution_module, "LEASE_HEARTBEAT_SECONDS", 0.01),
            patch.object(execution_module, "run_job_search_and_matching", provider),
        ):
            execution_module.execute_search_claim(claim, repository)

        result = repository.status()["last_result"]
        self.assertTrue(result["cancelled"])
        self.assertNotIn("jobs_added", result)

    def test_background_worker_stop_wins_over_provider_failure(self) -> None:
        repository = self.repository()
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000065", "stop race", ""
        )
        repository.publish_prepared(run_id, "stop race", "")
        claim = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000065"
        )
        stop = Event()
        background_stop_observed = Event()

        def stop_requested():
            requested = stop.is_set()
            if requested and current_thread().name == "search-run-lease-heartbeat":
                background_stop_observed.set()
            return requested

        def provider(_keywords, _location, _checkpoint, **_kwargs):
            stop.set()
            self.assertTrue(background_stop_observed.wait(1))
            raise RuntimeError("provider failed during worker shutdown")

        with (
            patch.object(execution_module, "LEASE_HEARTBEAT_SECONDS", 0.01),
            patch.object(execution_module, "run_job_search_and_matching", provider),
        ):
            execution_module.execute_search_claim(
                claim,
                repository,
                stop_requested=stop_requested,
            )

        self.assertTrue(repository.status()["searching"])
        self.assertIsNone(repository.status()["last_result"])

    def test_background_lease_loss_wins_over_provider_failure(self) -> None:
        repository = self.repository()
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000066", "lease race", ""
        )
        repository.publish_prepared(run_id, "lease race", "")
        claim = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000066"
        )
        original_heartbeat = repository.heartbeat
        heartbeat_lock = Lock()
        heartbeat_count = 0
        lease_loss_observed = Event()

        def heartbeat(target_claim, lease_seconds=300):
            nonlocal heartbeat_count
            with heartbeat_lock:
                heartbeat_count += 1
                if heartbeat_count > 1:
                    lease_loss_observed.set()
                    raise SearchRunLeaseLost()
            original_heartbeat(target_claim, lease_seconds)

        def provider(_keywords, _location, _checkpoint, **_kwargs):
            self.assertTrue(lease_loss_observed.wait(1))
            raise RuntimeError("provider failed after lease loss")

        repository.heartbeat = heartbeat
        with (
            patch.object(execution_module, "LEASE_HEARTBEAT_SECONDS", 0.01),
            patch.object(execution_module, "run_job_search_and_matching", provider),
        ):
            execution_module.execute_search_claim(claim, repository)

        self.assertTrue(repository.status()["searching"])
        self.assertIsNone(repository.status()["last_result"])

    def test_preparing_is_active_but_not_claimable_until_published(self) -> None:
        repository = self.repository()
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000062", "submitted", "draft"
        )

        self.assertTrue(repository.has_active())
        self.assertTrue(repository.status()["searching"])
        self.assertIsNone(
            repository.claim(run_id, "10000000-0000-4000-8000-000000000062")
        )

        repository.publish_prepared(run_id, "normalized", "Final place")
        claim = repository.claim(
            run_id, "10000000-0000-4000-8000-000000000062"
        )
        self.assertIsNotNone(claim)
        self.assertEqual("normalized", claim.keywords)
        self.assertEqual("Final place", claim.location)

    def test_worker_activation_switch_is_strict_and_disabled_by_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(config_module.search_worker_enabled())
        with patch.dict(
            "os.environ", {"CAREERTRELLIS_SEARCH_WORKER_ENABLED": "yes"}, clear=True
        ):
            with self.assertRaises(config_module.RuntimeConfigurationError):
                config_module.search_worker_enabled()

    def test_enabled_worker_uses_only_dedicated_runtime_configuration(self) -> None:
        engine = Mock()
        environment = {
            "CAREERTRELLIS_ENVIRONMENT": "staging",
            "CAREERTRELLIS_DATA_DIR": self.temporary_directory.name,
            "CAREERTRELLIS_WORKER_HEARTBEAT_SECONDS": "15",
            "CAREERTRELLIS_SEARCH_WORKER_ENABLED": "true",
            "CAREERTRELLIS_DATA_STORE": "postgres",
        }
        with (
            patch.dict("os.environ", environment, clear=True),
            patch.object(worker_module.config, "DATA_DIR", Path(self.temporary_directory.name)),
            patch.object(
                worker_module.config,
                "validate_worker_runtime_configuration",
                return_value={"environment": "staging"},
            ) as worker_runtime_configuration,
            patch.object(
                worker_module.config,
                "validate_runtime_configuration",
                side_effect=AssertionError("worker loaded common web runtime configuration"),
            ) as common_runtime_configuration,
            patch.object(
                worker_module,
                "validate_data_store_configuration",
                return_value="postgres",
            ),
            patch.object(
                worker_module, "validate_provider_secret_store_configuration"
            ) as provider_secret_configuration,
            patch.object(worker_module, "runtime_postgres_engine", return_value=engine),
            patch.object(
                worker_module,
                "dispatch_due_postgres_saved_searches",
                return_value=[],
            ) as dispatch_due,
            patch.object(
                worker_module,
                "claim_next_postgres_search_run",
                return_value=None,
            ) as claim_next,
        ):
            worker_module.STOP_EVENT.clear()
            self.assertEqual(0, worker_module.run(once=True))

        worker_runtime_configuration.assert_called_once_with()
        common_runtime_configuration.assert_not_called()
        provider_secret_configuration.assert_called_once_with()
        dispatch_due.assert_called_once_with(engine=engine)
        claim_next.assert_called_once_with(ANY, engine=engine)
        heartbeat = json.loads(
            (Path(self.temporary_directory.name) / "worker-heartbeat.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("idle", heartbeat["status"])
        for common_web_credential in (
            "CAREERTRELLIS_ALLOWED_HOSTS",
            "CAREERTRELLIS_TRUSTED_PROXY_TOKEN",
            "WORKOS_API_KEY",
            "WORKOS_CLIENT_ID",
            "WORKOS_COOKIE_PASSWORD",
            "WORKOS_REDIRECT_URI",
            "WORKOS_LOGOUT_RETURN_TO",
        ):
            self.assertNotIn(common_web_credential, environment)

    def test_worker_runtime_validation_does_not_require_web_credentials(self) -> None:
        environment = {
            "CAREERTRELLIS_ENVIRONMENT": "staging",
            "CAREERTRELLIS_WORKER_HEARTBEAT_SECONDS": "15",
            "CAREERTRELLIS_SEARCH_WORKER_ENABLED": "true",
        }
        with (
            patch.dict("os.environ", environment, clear=True),
            patch.object(
                config_module,
                "validate_runtime_configuration",
                side_effect=AssertionError("worker validator delegated to web validation"),
            ) as common_runtime_configuration,
        ):
            summary = config_module.validate_worker_runtime_configuration()

        self.assertEqual("staging", summary["environment"])
        common_runtime_configuration.assert_not_called()

    def test_worker_runtime_rejects_forbidden_web_migration_and_object_credentials(self) -> None:
        base_environment = {
            "CAREERTRELLIS_ENVIRONMENT": "staging",
            "CAREERTRELLIS_WORKER_HEARTBEAT_SECONDS": "15",
            "CAREERTRELLIS_SEARCH_WORKER_ENABLED": "false",
            "CAREERTRELLIS_PROVIDER_SECRET_KEYRING_B64": "worker-owned-keyring",
        }
        for variable in (
            "WORKOS_API_KEY",
            "CAREERTRELLIS_TRUSTED_PROXY_TOKEN",
            "CAREERTRELLIS_MIGRATION_DATABASE_URL",
            "CAREERTRELLIS_OBJECT_STORE_SECRET_ACCESS_KEY",
        ):
            with self.subTest(variable=variable):
                with (
                    patch.dict(
                        "os.environ",
                        {**base_environment, variable: "must-not-be-loaded"},
                        clear=True,
                    ),
                    self.assertRaises(config_module.RuntimeConfigurationError) as raised,
                ):
                    config_module.validate_worker_runtime_configuration()
                self.assertEqual(
                    "The search worker environment contains a credential or setting reserved for the web, migration, or object-storage runtime.",
                    str(raised.exception),
                )

        with patch.dict("os.environ", base_environment, clear=True):
            summary = config_module.validate_worker_runtime_configuration()
        self.assertEqual("staging", summary["environment"])

    def test_worker_stop_leaves_lease_retryable_without_recording_user_cancel(self) -> None:
        repository = self.repository()
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000071", "restart recovery", ""
        )
        repository.publish_prepared(run_id, "restart recovery", "")
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
