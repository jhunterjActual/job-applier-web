"""Focused regressions for explicit PostgreSQL dependent bulk cleanup."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

import app as app_module
import job_retention_repository as retention_module
import test_postgres_rls as postgres_harness
from application_workspace_repository import (
    ApplicationWorkspaceNotFound,
    PostgreSQLApplicationWorkspaceRepository,
)
from postgres_database import tenant_transaction


def _snapshot_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": 7,
        "status": "matched",
        "archived_from_status": None,
        "url": "https://synthetic.example.test/job",
        "company": "Synthetic Employer",
        "title": "Synthetic Role",
        "application_ids": [70],
        "history_ids": [71],
        "engagement_ids": [72],
        "application_object_refs": ["70:object-resume:object-letter"],
        "object_ids": ["object-letter", "object-resume"],
        "survivor_object_refs": ["80:object-resume:"],
    }
    row.update(overrides)
    return row


class DependentBulkCleanupUnitTests(unittest.TestCase):
    def test_local_preview_is_explicitly_unavailable_and_execution_opens_no_database(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database_path = Path(temporary.name) / "cleanup.db"

        def connection_factory() -> sqlite3.Connection:
            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            return connection

        connection = connection_factory()
        connection.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY, status TEXT, archived_from_status TEXT,
                archived_at TEXT, url TEXT, company TEXT, title TEXT,
                date_found TEXT, match_score INTEGER
            );
            CREATE TABLE applications (id INTEGER PRIMARY KEY, job_id INTEGER);
            CREATE TABLE application_status_history (id INTEGER PRIMARY KEY, job_id INTEGER);
            CREATE TABLE application_engagements (id INTEGER PRIMARY KEY, job_id INTEGER);
            INSERT INTO jobs VALUES (
                1, 'matched', NULL, NULL, 'https://example.test/one',
                'Example', 'One', '2026-08-13', 80
            );
            INSERT INTO jobs VALUES (
                2, 'matched', NULL, NULL, 'https://example.test/two',
                'Example', 'Two', '2026-08-13', 70
            );
            INSERT INTO applications VALUES (20, 2);
            """
        )
        connection.commit()
        connection.close()

        repository = retention_module.SQLiteJobRetentionRepository(connection_factory)
        preview = repository.cleanup_preview()
        self.assertEqual(1, preview["actions"]["delete"]["count"])
        self.assertEqual(
            {
                "available": False,
                "count": 0,
                "preview_token": preview["actions"][retention_module.DEPENDENT_DELETE_ACTION]["preview_token"],
            },
            preview["actions"][retention_module.DEPENDENT_DELETE_ACTION],
        )

        unopened_factory = Mock(side_effect=AssertionError("SQLite must stay closed"))
        unavailable = retention_module.SQLiteJobRetentionRepository(unopened_factory)
        with self.assertRaises(retention_module.DependentCleanupUnavailable):
            unavailable.apply_cleanup(
                retention_module.DEPENDENT_DELETE_ACTION,
                preview["actions"][retention_module.DEPENDENT_DELETE_ACTION]["preview_token"],
            )
        unopened_factory.assert_not_called()
        check = connection_factory()
        try:
            self.assertEqual(2, check.execute("SELECT count(*) FROM jobs").fetchone()[0])
        finally:
            check.close()

    def test_preview_token_binds_every_dependent_and_object_reference_surface(self) -> None:
        original = _snapshot_row()
        token = retention_module._preview_token(
            "postgres:42", retention_module.DEPENDENT_DELETE_ACTION, [original]
        )
        mutations = {
            "application_ids": [70, 73],
            "history_ids": [71, 74],
            "engagement_ids": [72, 75],
            "application_object_refs": ["70:replacement:object-letter"],
            "object_ids": ["object-letter", "replacement"],
            "survivor_object_refs": ["81:object-resume:"],
        }
        for key, value in mutations.items():
            with self.subTest(surface=key):
                changed = _snapshot_row(**{key: value})
                self.assertNotEqual(
                    token,
                    retention_module._preview_token(
                        "postgres:42",
                        retention_module.DEPENDENT_DELETE_ACTION,
                        [changed],
                    ),
                )

    def test_apply_recomputes_preview_after_full_graph_lock_and_reconciles_after_commit(self) -> None:
        events: list[str] = []
        row = _snapshot_row()
        repository = retention_module.PostgreSQLJobRetentionRepository(42, engine=Mock())
        token = retention_module._preview_token(
            repository._mode_owner,
            retention_module.DEPENDENT_DELETE_ACTION,
            [row],
        )
        connection = Mock()
        connection.execute.return_value.all.return_value = [(7,)]

        def candidates(_connection: object, _action: str) -> list[dict[str, object]]:
            events.append("snapshot")
            return [row]

        @contextmanager
        def transaction(_engine: object, _owner: int):
            events.append("transaction_enter")
            try:
                yield connection
            finally:
                events.append("transaction_exit")

        with (
            patch.object(retention_module, "tenant_transaction", transaction),
            patch.object(repository, "_set_write_timeouts"),
            patch.object(repository, "_candidate_rows", side_effect=candidates),
            patch.object(
                retention_module,
                "acquire_owner_job_catalog_lock",
                side_effect=lambda *_args: events.append("catalog_lock"),
            ),
            patch.object(
                repository,
                "_lock_batch_deletion_graph",
                side_effect=lambda *_args: events.append("graph_lock") or ["exclusive"],
            ),
            patch.object(
                repository,
                "_delete_locked_rows",
                side_effect=lambda *_args, **_kwargs: events.append("delete") or True,
            ),
            patch.object(
                repository,
                "_reconcile_deleted_objects",
                side_effect=lambda: events.append("reconcile"),
            ),
        ):
            self.assertEqual(
                1,
                repository.apply_cleanup(
                    retention_module.DEPENDENT_DELETE_ACTION, token
                ),
            )

        first_snapshot, final_snapshot = (
            index for index, event in enumerate(events) if event == "snapshot"
        )
        self.assertLess(first_snapshot, events.index("catalog_lock"))
        self.assertLess(events.index("catalog_lock"), events.index("graph_lock"))
        self.assertLess(events.index("graph_lock"), final_snapshot)
        self.assertLess(final_snapshot, events.index("delete"))
        self.assertLess(events.index("delete"), events.index("transaction_exit"))
        self.assertLess(events.index("transaction_exit"), events.index("reconcile"))

    def test_aggregate_cap_fails_before_catalog_or_graph_lock(self) -> None:
        row = _snapshot_row(
            history_ids=list(range(retention_module.MAX_RETENTION_DEPENDENT_ITEMS + 1))
        )
        repository = retention_module.PostgreSQLJobRetentionRepository(42, engine=Mock())
        connection = Mock()

        @contextmanager
        def transaction(_engine: object, _owner: int):
            yield connection

        with (
            patch.object(retention_module, "tenant_transaction", transaction),
            patch.object(repository, "_set_write_timeouts"),
            patch.object(repository, "_candidate_rows", return_value=[row]),
            patch.object(retention_module, "acquire_owner_job_catalog_lock") as catalog_lock,
            patch.object(repository, "_lock_batch_deletion_graph") as graph_lock,
            self.assertRaises(retention_module.CleanupBatchTooLarge),
        ):
            repository.apply_cleanup(
                retention_module.DEPENDENT_DELETE_ACTION, "0" * 64
            )
        catalog_lock.assert_not_called()
        graph_lock.assert_not_called()
        connection.execute.assert_not_called()

    def test_batch_graph_deduplicates_target_sharing_and_preserves_survivor_refs(self) -> None:
        target_shared = "00000000-0000-4000-8000-000000000091"
        survivor_shared = "00000000-0000-4000-8000-000000000092"
        exclusive = "00000000-0000-4000-8000-000000000093"
        applications_result = Mock()
        applications_result.mappings.return_value.all.return_value = [
            {
                "id": 70,
                "job_id": 7,
                "tailored_resume_object_id": target_shared,
                "cover_letter_object_id": survivor_shared,
            },
            {
                "id": 80,
                "job_id": 8,
                "tailored_resume_object_id": target_shared,
                "cover_letter_object_id": exclusive,
            },
        ]
        survivors_result = Mock()
        survivors_result.mappings.return_value.all.return_value = [{
            "id": 90,
            "tailored_resume_object_id": survivor_shared,
            "cover_letter_object_id": None,
        }]
        objects_result = Mock()
        objects_result.scalars.return_value.all.return_value = [
            target_shared,
            survivor_shared,
            exclusive,
        ]
        connection = Mock()
        connection.execute.side_effect = [
            applications_result,
            survivors_result,
            objects_result,
        ]
        repository = retention_module.PostgreSQLJobRetentionRepository(42, engine=Mock())

        exclusive_ids = repository._lock_batch_deletion_graph(connection, [7, 8])

        self.assertCountEqual([target_shared, exclusive], exclusive_ids)
        self.assertEqual(1, exclusive_ids.count(target_shared))
        self.assertNotIn(survivor_shared, exclusive_ids)

    def test_route_has_fixed_local_refusal_and_shared_success_message(self) -> None:
        request = app_module.CleanupRequest(
            action=retention_module.DEPENDENT_DELETE_ACTION,
            preview_token="a" * 64,
        )
        search_repository = Mock()
        search_repository.has_active.return_value = False
        local_repository = Mock()
        local_repository.apply_cleanup.side_effect = (
            retention_module.DependentCleanupUnavailable("private mode detail")
        )
        with (
            patch.object(app_module, "runtime_search_run_repository", return_value=search_repository),
            patch.object(app_module, "runtime_job_retention_repository", return_value=local_repository),
            self.assertRaises(HTTPException) as raised,
        ):
            app_module.cleanup_jobs(request)
        self.assertEqual(409, raised.exception.status_code)
        self.assertEqual(
            "Dependent bulk deletion is unavailable in standalone mode.",
            raised.exception.detail,
        )

        shared_repository = Mock()
        shared_repository.apply_cleanup.return_value = 2
        with (
            patch.object(app_module, "runtime_search_run_repository", return_value=search_repository),
            patch.object(app_module, "runtime_job_retention_repository", return_value=shared_repository),
        ):
            response = app_module.cleanup_jobs(request)
        self.assertEqual(retention_module.DEPENDENT_DELETE_ACTION, response["action"])
        self.assertEqual(2, response["affected"])
        self.assertEqual(
            "2 job(s) and their saved application work permanently deleted.",
            response["message"],
        )

    def test_batch_sql_is_owner_scoped_and_uses_required_lock_order(self) -> None:
        source = Path(retention_module.__file__).read_text(encoding="utf-8")
        graph = source.split("def _lock_batch_deletion_graph", 1)[1].split(
            "def _delete_locked_rows", 1
        )[0]
        applications = graph.index("FROM careertrellis.applications")
        survivors = graph.index("NOT (job_id = ANY")
        objects = graph.index("FROM careertrellis.stored_objects")
        self.assertLess(applications, survivors)
        self.assertLess(survivors, objects)
        self.assertGreaterEqual(graph.count("owner_user_id = :owner_user_id"), 3)
        self.assertGreaterEqual(graph.count("FOR UPDATE"), 3)
        mutation = source.split("def _delete_locked_rows", 1)[1].split(
            "def _reconcile_deleted_objects", 1
        )[0]
        self.assertLess(
            mutation.index("UPDATE careertrellis.stored_objects"),
            mutation.index("DELETE FROM careertrellis.jobs"),
        )
        self.assertIn("dependent_bulk_cleanup", source)
        self.assertIn("reconcile_deleted_objects()", source)
        self.assertNotIn("include_recent", source)

    def test_ui_requires_server_availability_and_named_destructive_confirmations(self) -> None:
        script = (Path(app_module.__file__).parent / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        markup = (Path(app_module.__file__).parent / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('dependentDelete?.available === true', script)
        self.assertIn('runCleanupAction("delete_with_dependents")', script)
        self.assertIn("application records, lifecycle history, engagements", script)
        self.assertIn("Final confirmation: permanent deletion cannot be undone", script)
        self.assertIn('id="cleanup-dependent-delete-count"', markup)
        self.assertIn('id="delete-dependent-btn" hidden', markup)

    def test_reconciliation_failure_is_best_effort_and_privacy_safe(self) -> None:
        private_detail = "private/object/key/for-owner-42"
        repository = retention_module.PostgreSQLJobRetentionRepository(42, engine=Mock())
        with patch.object(
            retention_module,
            "PostgreSQLGeneratedMaterialRepository",
            side_effect=RuntimeError(private_detail),
        ):
            self.assertIsNone(repository._reconcile_deleted_objects())


class PostgreSQLDependentBulkCleanupTests(unittest.TestCase):
    """Disposable PostgreSQL coverage; skipped without the explicit test URL."""

    @classmethod
    def setUpClass(cls) -> None:
        harness = postgres_harness.PostgreSQLTenantIsolationTests
        harness.setUpClass()
        cls._harness = harness
        for name in ("admin_engine", "runtime_engine"):
            setattr(cls, name, getattr(harness, name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._harness.tearDownClass()

    def setUp(self) -> None:
        postgres_harness.PostgreSQLTenantIsolationTests.setUp(self)

    def _insert_job(
        self,
        owner: int,
        suffix: str,
        *,
        status: str = "matched",
    ) -> int:
        return postgres_harness.PostgreSQLTenantIsolationTests._insert_job(
            self,
            owner,
            f"https://dependent-bulk.example.test/{suffix}",
            title=f"Synthetic {suffix}",
            status=status,
        )

    def _insert_object(self, owner: int, suffix: str) -> str:
        object_id = str(uuid4())
        with self.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.stored_objects (
                        id, owner_user_id, object_key, content_type,
                        plaintext_size, sha256, wrapped_data_key
                    ) VALUES (
                        CAST(:id AS uuid), :owner, :key, 'application/pdf',
                        1, :digest, :wrapped
                    )
                    """
                ),
                {
                    "id": object_id,
                    "owner": owner,
                    "key": f"synthetic/dependent-bulk/{suffix}",
                    "digest": b"d" * 32,
                    "wrapped": b"w" * 48,
                },
            )
        return object_id

    def _insert_application(
        self,
        owner: int,
        job_id: int,
        *,
        resume_id: str | None = None,
        letter_id: str | None = None,
    ) -> int:
        with self.admin_engine.begin() as connection:
            return connection.scalar(
                text(
                    """
                    INSERT INTO careertrellis.applications (
                        owner_user_id, job_id, company, position, status,
                        tailored_resume_object_id, cover_letter_object_id
                    ) VALUES (
                        :owner, :job, 'Synthetic Employer', 'Synthetic Role',
                        'tailored', CAST(:resume AS uuid), CAST(:letter AS uuid)
                    ) RETURNING id
                    """
                ),
                {
                    "owner": owner,
                    "job": job_id,
                    "resume": resume_id,
                    "letter": letter_id,
                },
            )

    def test_owner_batch_cascades_dependents_tombstones_once_and_preserves_survivor(self) -> None:
        target_two = self._insert_job(self.user_one, "target-two")
        survivor_job = self._insert_job(self.user_one, "survivor", status="applied")
        target_shared = self._insert_object(self.user_one, "target-shared")
        survivor_shared = self._insert_object(self.user_one, "survivor-shared")
        exclusive = self._insert_object(self.user_one, "exclusive")
        self._insert_application(
            self.user_one,
            self.job_one,
            resume_id=target_shared,
            letter_id=survivor_shared,
        )
        self._insert_application(
            self.user_one,
            target_two,
            resume_id=target_shared,
            letter_id=exclusive,
        )
        survivor_application = self._insert_application(
            self.user_one, survivor_job, resume_id=survivor_shared
        )
        with self.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.application_status_history
                        (owner_user_id, job_id, from_status, to_status, source)
                    VALUES (:owner, :job, 'matched', 'tailored', 'test')
                    """
                ),
                {"owner": self.user_one, "job": self.job_one},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.application_engagements
                        (owner_user_id, job_id, engagement_type, name)
                    VALUES (:owner, :job, 'recruiter', 'Synthetic contact')
                    """
                ),
                {"owner": self.user_one, "job": target_two},
            )

        repository = retention_module.PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        )
        preview = repository.cleanup_preview()
        action = preview["actions"][retention_module.DEPENDENT_DELETE_ACTION]
        self.assertTrue(action["available"])
        self.assertEqual(2, action["count"])
        self.assertEqual(
            2,
            repository.apply_cleanup(
                retention_module.DEPENDENT_DELETE_ACTION,
                action["preview_token"],
            ),
        )

        with self.admin_engine.begin() as connection:
            self.assertEqual(
                0,
                connection.scalar(text(
                    "SELECT count(*) FROM careertrellis.jobs "
                    "WHERE id IN (:one, :two)"
                ), {"one": self.job_one, "two": target_two}),
            )
            for table in (
                "applications",
                "application_status_history",
                "application_engagements",
            ):
                self.assertEqual(
                    0,
                    connection.scalar(
                        text(
                            f"SELECT count(*) FROM careertrellis.{table} "
                            "WHERE job_id IN (:one, :two)"
                        ),
                        {"one": self.job_one, "two": target_two},
                    ),
                )
            self.assertEqual(
                1,
                connection.scalar(text(
                    "SELECT count(*) FROM careertrellis.applications WHERE id=:id"
                ), {"id": survivor_application}),
            )
            states = connection.execute(
                text(
                    """
                    SELECT id, deleted_at FROM careertrellis.stored_objects
                    WHERE id IN (
                        CAST(:target_shared AS uuid),
                        CAST(:survivor_shared AS uuid),
                        CAST(:exclusive AS uuid)
                    )
                    """
                ),
                {
                    "target_shared": target_shared,
                    "survivor_shared": survivor_shared,
                    "exclusive": exclusive,
                },
            ).mappings().all()
            suppressions = connection.scalar(text(
                "SELECT count(*) FROM careertrellis.job_suppressions "
                "WHERE owner_user_id=:owner AND deletion_source='dependent_bulk_cleanup'"
            ), {"owner": self.user_one})
        deleted = {str(row["id"]): row["deleted_at"] for row in states}
        self.assertIsNotNone(deleted[target_shared])
        self.assertIsNotNone(deleted[exclusive])
        self.assertIsNone(deleted[survivor_shared])
        self.assertEqual(2, suppressions)
        with tenant_transaction(self.runtime_engine, self.user_two) as connection:
            self.assertEqual(
                1,
                connection.scalar(text(
                    "SELECT count(*) FROM careertrellis.jobs WHERE id=:job"
                ), {"job": self.job_two}),
            )

    def test_dependency_change_stales_token_and_rolls_back_every_surface(self) -> None:
        exclusive = self._insert_object(self.user_one, "rollback-exclusive")
        self._insert_application(self.user_one, self.job_one, resume_id=exclusive)
        repository = retention_module.PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        )
        preview = repository.cleanup_preview()
        action = preview["actions"][retention_module.DEPENDENT_DELETE_ACTION]
        with self.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.application_engagements
                        (owner_user_id, job_id, engagement_type, name)
                    VALUES (:owner, :job, 'recruiter', 'Late change')
                    """
                ),
                {"owner": self.user_one, "job": self.job_one},
            )
        with self.assertRaises(retention_module.CleanupPreviewChanged):
            repository.apply_cleanup(
                retention_module.DEPENDENT_DELETE_ACTION,
                action["preview_token"],
            )
        with self.admin_engine.begin() as connection:
            self.assertEqual(1, connection.scalar(text(
                "SELECT count(*) FROM careertrellis.jobs WHERE id=:job"
            ), {"job": self.job_one}))
            self.assertIsNone(connection.scalar(text(
                "SELECT deleted_at FROM careertrellis.stored_objects WHERE id=CAST(:id AS uuid)"
            ), {"id": exclusive}))
            self.assertEqual(0, connection.scalar(text(
                "SELECT count(*) FROM careertrellis.job_suppressions WHERE owner_user_id=:owner"
            ), {"owner": self.user_one}))

    def test_delete_failure_rolls_back_and_engagement_race_is_consistent(self) -> None:
        rollback_object = self._insert_object(self.user_one, "trigger-rollback")
        self._insert_application(
            self.user_one, self.job_one, resume_id=rollback_object
        )
        repository = retention_module.PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        )
        preview = repository.cleanup_preview()
        function_name = "test_fail_dependent_bulk_delete"
        with self.admin_engine.begin() as connection:
            connection.execute(text(f"""CREATE FUNCTION careertrellis.{function_name}()
                RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                    RAISE EXCEPTION 'synthetic bulk delete failure';
                END $$"""))
            connection.execute(text(f"""CREATE TRIGGER {function_name}
                BEFORE DELETE ON careertrellis.jobs FOR EACH ROW
                EXECUTE FUNCTION careertrellis.{function_name}()"""))
        try:
            with self.assertRaises(Exception):
                repository.apply_cleanup(
                    retention_module.DEPENDENT_DELETE_ACTION,
                    preview["actions"][retention_module.DEPENDENT_DELETE_ACTION]["preview_token"],
                )
        finally:
            with self.admin_engine.begin() as connection:
                connection.execute(text(
                    f"DROP TRIGGER IF EXISTS {function_name} ON careertrellis.jobs"
                ))
                connection.execute(text(
                    f"DROP FUNCTION IF EXISTS careertrellis.{function_name}()"
                ))
        with self.admin_engine.begin() as connection:
            self.assertEqual(1, connection.scalar(text(
                "SELECT count(*) FROM careertrellis.jobs WHERE id=:job"
            ), {"job": self.job_one}))
            self.assertEqual(1, connection.scalar(text(
                "SELECT count(*) FROM careertrellis.applications WHERE job_id=:job"
            ), {"job": self.job_one}))
            self.assertEqual(0, connection.scalar(text(
                "SELECT count(*) FROM careertrellis.job_suppressions WHERE owner_user_id=:owner"
            ), {"owner": self.user_one}))
            self.assertIsNone(connection.scalar(text(
                "SELECT deleted_at FROM careertrellis.stored_objects "
                "WHERE id=CAST(:id AS uuid)"
            ), {"id": rollback_object}))

        race_preview = repository.cleanup_preview()["actions"][
            retention_module.DEPENDENT_DELETE_ACTION
        ]
        workspace = PostgreSQLApplicationWorkspaceRepository(
            self.user_one, engine=self.runtime_engine
        )

        def delete_batch() -> str:
            try:
                repository.apply_cleanup(
                    retention_module.DEPENDENT_DELETE_ACTION,
                    race_preview["preview_token"],
                )
                return "deleted"
            except retention_module.CleanupPreviewChanged:
                return "stale"

        def create_engagement() -> str:
            try:
                workspace.create_engagement(
                    self.job_one,
                    {
                        "engagement_type": "recruiter",
                        "name": "Concurrent contact",
                        "organization": "Synthetic Employer",
                        "contact_details": "",
                        "status": "planned",
                        "activity_on": None,
                        "next_action_on": None,
                        "notes": "",
                    },
                )
                return "created"
            except ApplicationWorkspaceNotFound:
                return "missing"

        with ThreadPoolExecutor(max_workers=2) as executor:
            delete_future = executor.submit(delete_batch)
            engagement_future = executor.submit(create_engagement)
            results = {delete_future.result(timeout=10), engagement_future.result(timeout=10)}
        self.assertIn(results, ({"deleted", "missing"}, {"stale", "created"}))
        with self.admin_engine.begin() as connection:
            job_exists = bool(connection.scalar(text(
                "SELECT EXISTS(SELECT 1 FROM careertrellis.jobs WHERE id=:job)"
            ), {"job": self.job_one}))
            engagement_count = int(connection.scalar(text(
                "SELECT count(*) FROM careertrellis.application_engagements WHERE job_id=:job"
            ), {"job": self.job_one}) or 0)
        if job_exists:
            self.assertEqual({"stale", "created"}, results)
            self.assertEqual(1, engagement_count)
        else:
            self.assertEqual({"deleted", "missing"}, results)
            self.assertEqual(0, engagement_count)


if __name__ == "__main__":
    unittest.main()
