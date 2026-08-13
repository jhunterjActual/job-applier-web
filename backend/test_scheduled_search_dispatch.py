"""Synthetic tests for the bounded scheduled saved-search dispatch bridge."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import ANY, MagicMock, Mock, call, patch

from sqlalchemy.exc import OperationalError

import scheduled_search_dispatch as dispatch_module
import worker as worker_module


class ScheduledSearchDispatchUnitTests(unittest.TestCase):
    def test_repository_uses_one_bounded_transaction_and_returns_opaque_receipts(self) -> None:
        engine = MagicMock()
        connection = engine.begin.return_value.__enter__.return_value
        connection.execute.return_value.mappings.return_value.all.return_value = [
            {
                "run_id": "00000000-0000-4000-8000-000000000101",
                "owner_user_id": 42,
                "saved_search_id": 7,
                "scheduled_for": datetime(2026, 8, 13, 12, tzinfo=timezone.utc),
            }
        ]

        dispatched = dispatch_module.dispatch_due_postgres_saved_searches(
            engine=engine, limit=5
        )

        self.assertEqual(1, len(dispatched))
        self.assertEqual(42, dispatched[0].owner_user_id)
        self.assertEqual(7, dispatched[0].saved_search_id)
        engine.begin.assert_called_once_with()
        statement, parameters = connection.execute.call_args.args
        self.assertEqual(
            "SELECT * FROM careertrellis.dispatch_due_saved_searches(:limit)",
            str(statement),
        )
        self.assertEqual({"limit": 5}, parameters)
        engine.begin.return_value.__exit__.assert_called_once()

    def test_repository_rejects_unbounded_limits_before_opening_postgres(self) -> None:
        engine = MagicMock()
        for limit in (True, 0, 101, 1.5, "10"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                dispatch_module.dispatch_due_postgres_saved_searches(
                    engine=engine, limit=limit
                )
        engine.begin.assert_not_called()

    def test_repository_wraps_database_details_in_a_fixed_error(self) -> None:
        secret = "postgresql://worker:secret@private-database.test/careertrellis"
        engine = MagicMock()
        connection = engine.begin.return_value.__enter__.return_value
        connection.execute.side_effect = OperationalError(
            "SELECT private", {}, RuntimeError(secret)
        )

        with self.assertRaises(
            dispatch_module.ScheduledSearchDispatchUnavailable
        ) as raised:
            dispatch_module.dispatch_due_postgres_saved_searches(engine=engine)

        self.assertEqual(
            "Scheduled search dispatch is temporarily unavailable.",
            str(raised.exception),
        )
        self.assertNotIn("private", str(raised.exception))

    def test_migration_broker_contains_only_identity_and_due_time(self) -> None:
        migration = (
            Path(__file__).parent
            / "migrations"
            / "versions"
            / "20260813_0008_scheduled_search_dispatch.py"
        ).read_text(encoding="utf-8")
        broker = migration.split(
            'op.create_table(\n        "scheduled_search_dispatch_queue"', 1
        )[1].split("op.create_index(", 1)[0]

        self.assertIn('sa.Column("saved_search_id"', broker)
        self.assertIn('sa.Column("owner_user_id"', broker)
        self.assertIn('sa.Column("due_at"', broker)
        self.assertNotIn("keywords", broker)
        self.assertNotIn("location", broker)
        self.assertIn("FOR UPDATE SKIP LOCKED", migration)
        self.assertIn("SESSION_USER <> 'careertrellis_worker'", migration)
        self.assertIn("SET search_path = pg_catalog, {SCHEMA}", migration)
        self.assertIn("uq_search_runs_owner_scheduled_occurrence", migration)
        self.assertIn("INTERVAL '30 seconds'", migration)
        self.assertIn("INTERVAL '5 minutes'", migration)
        self.assertIn("pg_catalog.char_length(saved.keywords) > 500", migration)
        self.assertIn("pg_catalog.char_length(saved.location) > 240", migration)
        self.assertIn("pg_catalog.octet_length(saved.keywords) > 2000", migration)
        self.assertIn("pg_catalog.octet_length(saved.location) > 960", migration)
        self.assertIn("keywords, location OR DELETE", migration)


class ScheduledSearchWorkerIntegrationTests(unittest.TestCase):
    def worker_environment(self, data_directory: str) -> dict[str, str]:
        return {
            "CAREERTRELLIS_ENVIRONMENT": "staging",
            "CAREERTRELLIS_DATA_DIR": data_directory,
            "CAREERTRELLIS_WORKER_HEARTBEAT_SECONDS": "15",
            "CAREERTRELLIS_SEARCH_WORKER_ENABLED": "true",
            "CAREERTRELLIS_DATA_STORE": "postgres",
        }

    def test_enabled_iteration_dispatches_before_claim_and_provider_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = Mock()
            claim = Mock(owner_user_id=42)
            calls = Mock()
            dispatch = Mock(return_value=[Mock()])
            claim_next = Mock(return_value=claim)
            execute = Mock()
            calls.attach_mock(dispatch, "dispatch")
            calls.attach_mock(claim_next, "claim")
            calls.attach_mock(execute, "execute")
            with (
                patch.dict("os.environ", self.worker_environment(temp_dir), clear=True),
                patch.object(worker_module.config, "DATA_DIR", Path(temp_dir)),
                patch.object(
                    worker_module.config,
                    "validate_worker_runtime_configuration",
                    return_value={"environment": "staging"},
                ),
                patch.object(worker_module, "validate_data_store_configuration", return_value="postgres"),
                patch.object(worker_module, "validate_provider_secret_store_configuration"),
                patch.object(worker_module, "runtime_postgres_engine", return_value=engine),
                patch.object(worker_module, "dispatch_due_postgres_saved_searches", dispatch),
                patch.object(worker_module, "claim_next_postgres_search_run", claim_next),
                patch.object(worker_module, "execute_search_claim", execute),
                patch("builtins.print") as print_message,
            ):
                worker_module.STOP_EVENT.clear()
                self.assertEqual(0, worker_module.run(once=True))

        self.assertEqual(
            ["dispatch", "claim", "execute"],
            [entry[0] for entry in calls.mock_calls],
        )
        dispatch.assert_called_once_with(engine=engine)
        claim_next.assert_called_once_with(ANY, engine=engine)
        execute.assert_called_once_with(
            claim,
            ANY,
            stop_requested=worker_module.STOP_EVENT.is_set,
        )
        messages = [json.loads(item.args[0]) for item in print_message.call_args_list]
        self.assertIn(
            {"event": "scheduled_searches_dispatched", "count": 1}, messages
        )

    def test_dispatch_failure_logs_only_fixed_code_and_still_claims(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = Mock()
            private_detail = "postgresql://worker:secret@private.test/database"
            failure = dispatch_module.ScheduledSearchDispatchUnavailable()
            failure.__cause__ = RuntimeError(private_detail)
            claim = Mock(owner_user_id=42)
            claim_next = Mock(return_value=claim)
            execute = Mock()
            with (
                patch.dict("os.environ", self.worker_environment(temp_dir), clear=True),
                patch.object(worker_module.config, "DATA_DIR", Path(temp_dir)),
                patch.object(
                    worker_module.config,
                    "validate_worker_runtime_configuration",
                    return_value={"environment": "staging"},
                ),
                patch.object(worker_module, "validate_data_store_configuration", return_value="postgres"),
                patch.object(worker_module, "validate_provider_secret_store_configuration"),
                patch.object(worker_module, "runtime_postgres_engine", return_value=engine),
                patch.object(
                    worker_module,
                    "dispatch_due_postgres_saved_searches",
                    side_effect=failure,
                ),
                patch.object(worker_module, "claim_next_postgres_search_run", claim_next),
                patch.object(worker_module, "execute_search_claim", execute),
                patch("builtins.print") as print_message,
            ):
                worker_module.STOP_EVENT.clear()
                self.assertEqual(0, worker_module.run(once=True))

        claim_next.assert_called_once_with(ANY, engine=engine)
        execute.assert_called_once_with(claim, ANY, stop_requested=ANY)
        logged = " ".join(str(item) for item in print_message.call_args_list)
        self.assertIn("scheduled_search_dispatch_failed", logged)
        self.assertIn("persistence_unavailable", logged)
        self.assertNotIn(private_detail, logged)


if __name__ == "__main__":
    unittest.main()
