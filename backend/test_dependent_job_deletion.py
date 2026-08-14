"""Focused regressions for owner-scoped deletion of dependent job data."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import text

import app as app_module
import generated_material_repository as material_module
import job_retention_repository as retention_module
import test_postgres_rls as postgres_harness
from data_store import LOCAL_DATA_STORE, SHARED_DATA_STORE
from provider_secrets import ProviderSecretAuthorizationError


class SQLiteDependentJobDeletionParityTests(unittest.TestCase):
    """Dependent jobs retain the existing protected behavior in local mode."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "local-delete.db"
        connection = self.connection_factory()
        connection.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY, url TEXT, company TEXT, title TEXT
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
                interview_prep TEXT
            );
            CREATE TABLE application_status_history (
                id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL
            );
            CREATE TABLE application_engagements (
                id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL
            );
            CREATE TABLE job_suppressions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url_fingerprint TEXT NOT NULL UNIQUE,
                hostname TEXT NOT NULL, company TEXT, title TEXT,
                deleted_at TEXT NOT NULL, deletion_source TEXT NOT NULL
            );
            INSERT INTO jobs VALUES (
                7, 'https://local.example.test/private-path',
                'Synthetic Employer', 'Synthetic Role'
            );
            INSERT INTO applications VALUES (70, 7, 'Synthetic interview notes');
            INSERT INTO application_status_history VALUES (71, 7);
            INSERT INTO application_engagements VALUES (72, 7);
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def connection_factory(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def test_dependent_job_remains_protected_with_every_local_row_unchanged(self) -> None:
        repository = retention_module.SQLiteJobRetentionRepository(
            self.connection_factory
        )

        with self.assertRaises(retention_module.JobRetentionProtected):
            repository.delete_job(7)

        connection = self.connection_factory()
        try:
            self.assertEqual(1, connection.execute(
                "SELECT count(*) FROM jobs WHERE id=7"
            ).fetchone()[0])
            self.assertEqual(1, connection.execute(
                "SELECT count(*) FROM applications WHERE job_id=7 "
                "AND interview_prep='Synthetic interview notes'"
            ).fetchone()[0])
            for table in (
                "application_status_history",
                "application_engagements",
            ):
                self.assertEqual(1, connection.execute(
                    f"SELECT count(*) FROM {table} WHERE job_id=7"
                ).fetchone()[0])
            self.assertEqual(0, connection.execute(
                "SELECT count(*) FROM job_suppressions"
            ).fetchone()[0])
        finally:
            connection.close()

    def test_runtime_factory_preserves_local_dispatch_without_owner_lookup(self) -> None:
        with (
            patch.object(retention_module, "data_store_kind", return_value=LOCAL_DATA_STORE),
            patch.object(retention_module, "current_owner_user_id") as owner,
        ):
            repository = retention_module.runtime_job_retention_repository(
                sqlite_connection_factory=self.connection_factory
            )

        self.assertIsInstance(repository, retention_module.SQLiteJobRetentionRepository)
        self.assertIs(self.connection_factory.__self__, repository._connection_factory.__self__)
        owner.assert_not_called()


class SharedDependentJobDeletionBoundaryTests(unittest.TestCase):
    def test_generated_material_publish_locks_catalog_before_context_rows(self) -> None:
        events: list[object] = []
        connection = Mock()
        resume = Mock()
        resume.metadata.id = uuid4()
        letter = Mock()
        letter.metadata.id = uuid4()
        repository = material_module.PostgreSQLGeneratedMaterialRepository(
            42,
            engine=Mock(),
            backend=Mock(),
        )
        context = {
            "job": {"id": 7},
            "profile": {},
            "resume": {},
            "resume_version": 1,
            "application": None,
        }

        @contextmanager
        def transaction(_engine, _owner):
            yield connection

        def catalog_lock(selected_connection, owner_user_id):
            events.append(("catalog_lock", selected_connection, owner_user_id))

        def context_rows(selected_connection, job_id, *, lock=False):
            events.append(("context_rows", selected_connection, job_id, lock))
            return context

        with (
            patch.object(material_module, "tenant_transaction", transaction),
            patch.object(
                material_module,
                "acquire_owner_job_catalog_lock",
                side_effect=catalog_lock,
            ),
            patch.object(repository, "_context_rows", side_effect=context_rows),
            patch.object(repository, "_insert_metadata"),
            patch.object(material_module, "_context_token", return_value="current"),
        ):
            repository._commit_replacement(
                7,
                "current",
                resume,
                letter,
                {},
                require_application=False,
            )

        self.assertEqual("catalog_lock", events[0][0])
        self.assertEqual("context_rows", events[1][0])
        self.assertIs(connection, events[0][1])
        self.assertEqual(42, events[0][2])
        self.assertEqual((connection, 7, True), events[1][1:])

    def test_shared_factory_binds_owner_and_never_calls_sqlite(self) -> None:
        sqlite_factory = Mock(side_effect=AssertionError("SQLite must not open"))
        engine = Mock()
        with (
            patch.object(retention_module, "data_store_kind", return_value=SHARED_DATA_STORE),
            patch.object(retention_module, "current_owner_user_id", return_value=42),
        ):
            repository = retention_module.runtime_job_retention_repository(
                sqlite_connection_factory=sqlite_factory,
                engine=engine,
            )

        self.assertIsInstance(repository, retention_module.PostgreSQLJobRetentionRepository)
        self.assertEqual(42, repository._owner_user_id)
        self.assertIs(engine, repository._engine)
        sqlite_factory.assert_not_called()

    def test_shared_factory_fails_closed_without_owner_context(self) -> None:
        sqlite_factory = Mock(side_effect=AssertionError("SQLite must not open"))
        private_detail = "user_private-owner-identifier"
        with (
            patch.object(retention_module, "data_store_kind", return_value=SHARED_DATA_STORE),
            patch.object(
                retention_module,
                "current_owner_user_id",
                side_effect=ProviderSecretAuthorizationError(private_detail),
            ),
            self.assertRaises(ProviderSecretAuthorizationError) as raised,
        ):
            retention_module.runtime_job_retention_repository(
                sqlite_connection_factory=sqlite_factory,
                engine=Mock(),
            )

        self.assertNotIn(private_detail, str(raised.exception.__class__))
        sqlite_factory.assert_not_called()

    def test_route_uses_fixed_success_not_found_protected_and_failure_responses(self) -> None:
        repository = Mock()
        private_detail = "postgresql://runtime:secret@private.test/database"
        with patch.object(
            app_module,
            "runtime_job_retention_repository",
            return_value=repository,
        ) as factory:
            self.assertEqual(
                {"success": True, "message": "Job posting deleted successfully."},
                app_module.delete_job(7),
            )
            for failure, status, detail in (
                (retention_module.JobRetentionNotFound(private_detail), 404, "Job not found."),
                (
                    retention_module.JobRetentionProtected(private_detail),
                    409,
                    "This job cannot be deleted because it has related application activity or generated materials.",
                ),
                (
                    RuntimeError(private_detail),
                    500,
                    "CareerTrellis could not delete the job posting.",
                ),
            ):
                repository.delete_job.side_effect = failure
                with self.subTest(status=status), self.assertRaises(
                    app_module.HTTPException
                ) as raised:
                    app_module.delete_job(7)
                self.assertEqual(status, raised.exception.status_code)
                self.assertEqual(detail, raised.exception.detail)
                self.assertNotIn(private_detail, str(raised.exception.detail))

        self.assertEqual(4, factory.call_count)
        for invocation in factory.call_args_list:
            self.assertIs(
                app_module.get_db_connection,
                invocation.kwargs["sqlite_connection_factory"],
            )

    def test_reconciliation_starts_only_after_commit_and_failure_is_swallowed(self) -> None:
        events: list[str] = []
        connection = Mock()
        job = {
            "id": 7,
            "url": "https://synthetic.example.test/private-job-path",
            "company": "Synthetic Employer",
            "title": "Synthetic Role",
        }
        application = {
            "id": 70,
            "tailored_resume_object_id": "00000000-0000-4000-8000-000000000071",
            "cover_letter_object_id": "00000000-0000-4000-8000-000000000072",
        }

        def execute(statement, _parameters=None):
            sql = " ".join(str(statement).split())
            result = Mock()
            if "SELECT id, url, company, title" in sql:
                result.mappings.return_value.one_or_none.return_value = job
            elif "SELECT id, tailored_resume_object_id" in sql:
                if "job_id = :job_id" in sql:
                    result.mappings.return_value.one_or_none.return_value = application
                else:
                    result.mappings.return_value.all.return_value = []
            elif "SELECT id FROM careertrellis.stored_objects" in sql:
                result.scalars.return_value.all.return_value = [
                    application["tailored_resume_object_id"],
                    application["cover_letter_object_id"],
                ]
            elif "UPDATE careertrellis.stored_objects" in sql:
                events.append("tombstone")
                result.rowcount = 2
            elif "DELETE FROM careertrellis.jobs" in sql:
                events.append("delete")
                result.rowcount = 1
            return result

        connection.execute.side_effect = execute

        @contextmanager
        def transaction(_engine, _owner):
            events.append("transaction_enter")
            try:
                yield connection
            finally:
                events.append("transaction_exit_commit")

        reconciler = Mock()

        def material_repository(*_args, **_kwargs):
            events.append("reconciler_construct")
            return reconciler

        repository = retention_module.PostgreSQLJobRetentionRepository(
            42, engine=Mock()
        )
        with (
            patch.object(retention_module, "tenant_transaction", transaction),
            patch.object(retention_module, "acquire_owner_job_catalog_lock"),
            patch.object(repository, "_set_write_timeouts"),
            patch.object(repository, "_upsert_suppression"),
            patch.object(
                retention_module,
                "PostgreSQLGeneratedMaterialRepository",
                side_effect=material_repository,
            ),
        ):
            repository.delete_job(7)

        self.assertLess(events.index("tombstone"), events.index("delete"))
        self.assertLess(events.index("delete"), events.index("transaction_exit_commit"))
        self.assertLess(
            events.index("transaction_exit_commit"), events.index("reconciler_construct")
        )
        reconciler.reconcile_deleted_objects.assert_called_once_with()

        private_detail = "objects/private-owner/object-key"
        reconcile_failure = Mock()
        reconcile_failure.reconcile_deleted_objects.side_effect = RuntimeError(
            private_detail
        )
        for material_side_effect in (
            RuntimeError(private_detail),
            lambda *_args, **_kwargs: reconcile_failure,
        ):
            with (
                self.subTest(failure=type(material_side_effect).__name__),
                patch.object(retention_module, "tenant_transaction", transaction),
                patch.object(retention_module, "acquire_owner_job_catalog_lock"),
                patch.object(repository, "_set_write_timeouts"),
                patch.object(repository, "_upsert_suppression"),
                patch.object(
                    retention_module,
                    "PostgreSQLGeneratedMaterialRepository",
                    side_effect=material_side_effect,
                ),
                patch.object(
                    app_module,
                    "runtime_job_retention_repository",
                    return_value=repository,
                ),
            ):
                response = app_module.delete_job(7)
            self.assertEqual(
                {"success": True, "message": "Job posting deleted successfully."},
                response,
            )
            self.assertNotIn(private_detail, str(response))

    def test_shared_delete_sql_is_owner_scoped_and_tombstones_before_job_delete(self) -> None:
        source = Path(retention_module.__file__).read_text(encoding="utf-8")
        shared_delete = source.split(
            "class PostgreSQLJobRetentionRepository", 1
        )[1].split("def runtime_job_retention_repository", 1)[0]
        tombstone_position = shared_delete.index(
            "UPDATE careertrellis.stored_objects"
        )
        job_delete_position = shared_delete.rindex(
            "DELETE FROM careertrellis.jobs"
        )

        self.assertLess(tombstone_position, job_delete_position)
        for statement in (
            "WHERE job_id = :job_id AND owner_user_id = :owner_user_id",
            "WHERE owner_user_id = :owner_user_id",
            "WHERE id = :job_id AND owner_user_id = :owner_user_id",
        ):
            self.assertIn(statement, shared_delete)
        self.assertIn("FOR UPDATE", shared_delete)
        self.assertIn("AND id <> :application_id", shared_delete)
        self.assertIn("reconcile_deleted_objects()", shared_delete)
        self.assertNotIn("include_recent", shared_delete)

    def test_transaction_failure_never_starts_object_reconciliation(self) -> None:
        connection = Mock()
        application = {
            "id": 70,
            "tailored_resume_object_id": "00000000-0000-4000-8000-000000000071",
            "cover_letter_object_id": "00000000-0000-4000-8000-000000000072",
        }

        def execute(statement, _parameters=None):
            sql = " ".join(str(statement).split())
            result = Mock()
            if "SELECT id, url, company, title" in sql:
                result.mappings.return_value.one_or_none.return_value = {
                    "id": 7,
                    "url": "https://synthetic.example.test/job",
                    "company": "Synthetic Employer",
                    "title": "Synthetic Role",
                }
            elif "SELECT id, tailored_resume_object_id" in sql:
                if "job_id = :job_id" in sql:
                    result.mappings.return_value.one_or_none.return_value = application
                else:
                    result.mappings.return_value.all.return_value = []
            elif "SELECT id FROM careertrellis.stored_objects" in sql:
                result.scalars.return_value.all.return_value = [
                    application["tailored_resume_object_id"],
                    application["cover_letter_object_id"],
                ]
            elif "UPDATE careertrellis.stored_objects" in sql:
                result.rowcount = 2
            elif "DELETE FROM careertrellis.jobs" in sql:
                raise RuntimeError("synthetic transactional failure")
            return result

        connection.execute.side_effect = execute

        @contextmanager
        def transaction(_engine, _owner):
            yield connection

        repository = retention_module.PostgreSQLJobRetentionRepository(
            42, engine=Mock()
        )
        with (
            patch.object(retention_module, "tenant_transaction", transaction),
            patch.object(retention_module, "acquire_owner_job_catalog_lock"),
            patch.object(repository, "_set_write_timeouts"),
            patch.object(repository, "_upsert_suppression"),
            patch.object(
                retention_module, "PostgreSQLGeneratedMaterialRepository"
            ) as material_repository,
            self.assertRaisesRegex(RuntimeError, "transactional failure"),
        ):
            repository.delete_job(7)

        material_repository.assert_not_called()

    def test_same_owner_survivor_reference_excludes_shared_object_from_tombstone(self) -> None:
        shared_id = "00000000-0000-4000-8000-000000000081"
        exclusive_id = "00000000-0000-4000-8000-000000000082"
        application = {
            "id": 70,
            "tailored_resume_object_id": shared_id,
            "cover_letter_object_id": exclusive_id,
        }
        survivor = {
            "id": 80,
            "tailored_resume_object_id": shared_id,
            "cover_letter_object_id": None,
        }
        tombstoned: list[str] = []
        connection = Mock()

        def execute(statement, parameters=None):
            sql = " ".join(str(statement).split())
            result = Mock()
            if "SELECT id, url, company, title" in sql:
                result.mappings.return_value.one_or_none.return_value = {
                    "id": 7,
                    "url": "https://synthetic.example.test/job",
                    "company": "Synthetic Employer",
                    "title": "Synthetic Role",
                }
            elif "SELECT id, tailored_resume_object_id" in sql:
                if "job_id = :job_id" in sql:
                    result.mappings.return_value.one_or_none.return_value = application
                else:
                    result.mappings.return_value.all.return_value = [survivor]
            elif "SELECT id FROM careertrellis.stored_objects" in sql:
                result.scalars.return_value.all.return_value = [shared_id, exclusive_id]
            elif "UPDATE careertrellis.stored_objects" in sql:
                tombstoned.extend(str(item) for item in parameters["object_ids"])
                result.rowcount = 1
            elif "DELETE FROM careertrellis.jobs" in sql:
                result.rowcount = 1
            return result

        connection.execute.side_effect = execute

        @contextmanager
        def transaction(_engine, _owner):
            yield connection

        repository = retention_module.PostgreSQLJobRetentionRepository(
            42, engine=Mock()
        )
        with (
            patch.object(retention_module, "tenant_transaction", transaction),
            patch.object(retention_module, "acquire_owner_job_catalog_lock"),
            patch.object(repository, "_set_write_timeouts"),
            patch.object(repository, "_upsert_suppression"),
            patch.object(retention_module, "PostgreSQLGeneratedMaterialRepository"),
        ):
            repository.delete_job(7)

        self.assertEqual([exclusive_id], tombstoned)
        survivor_query = next(
            " ".join(str(invocation.args[0]).split())
            for invocation in connection.execute.call_args_list
            if "AND id <> :application_id" in str(invocation.args[0])
        )
        self.assertIn("WHERE owner_user_id = :owner_user_id", survivor_query)
        self.assertIn("FOR UPDATE", survivor_query)


class PostgreSQLDependentJobDeletionTests(unittest.TestCase):
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

    def _seed_dependents(self, owner_user_id: int, job_id: int, suffix: str) -> tuple[str, str]:
        resume_id = str(uuid4())
        letter_id = str(uuid4())
        with self.admin_engine.begin() as connection:
            for object_id, kind in ((resume_id, "resume"), (letter_id, "letter")):
                connection.execute(
                    text(
                        """INSERT INTO careertrellis.stored_objects
                           (id, owner_user_id, object_key, content_type,
                            plaintext_size, sha256, wrapped_data_key)
                           VALUES (CAST(:id AS uuid), :owner, :key, :content_type,
                                   1, :sha256, :wrapped)"""
                    ),
                    {
                        "id": object_id,
                        "owner": owner_user_id,
                        "key": f"synthetic/{suffix}/{kind}",
                        "content_type": "application/pdf" if kind == "resume" else "text/plain",
                        "sha256": b"s" * 32,
                        "wrapped": b"w" * 48,
                    },
                )
            connection.execute(
                text(
                    """INSERT INTO careertrellis.applications
                       (owner_user_id, job_id, company, position, status,
                        interview_prep, tailored_resume_object_id,
                        cover_letter_object_id)
                       VALUES (:owner, :job, 'Same Synthetic Employer',
                               'Same Synthetic Role', 'interviewing',
                               'Synthetic interview preparation',
                               CAST(:resume AS uuid), CAST(:letter AS uuid))"""
                ),
                {
                    "owner": owner_user_id,
                    "job": job_id,
                    "resume": resume_id,
                    "letter": letter_id,
                },
            )
            connection.execute(
                text(
                    """INSERT INTO careertrellis.application_status_history
                       (owner_user_id, job_id, from_status, to_status, source)
                       VALUES (:owner, :job, 'tailored', 'interviewing', 'manual')"""
                ),
                {"owner": owner_user_id, "job": job_id},
            )
            connection.execute(
                text(
                    """INSERT INTO careertrellis.application_engagements
                       (owner_user_id, job_id, engagement_type, name)
                       VALUES (:owner, :job, 'interview', 'Synthetic contact')"""
                ),
                {"owner": owner_user_id, "job": job_id},
            )
        return resume_id, letter_id

    def _dependent_counts(self, owner_user_id: int, job_id: int) -> dict[str, int]:
        with self.admin_engine.begin() as connection:
            return {
                table: int(connection.scalar(text(
                    f"SELECT count(*) FROM careertrellis.{table} "
                    "WHERE owner_user_id=:owner AND job_id=:job"
                ), {"owner": owner_user_id, "job": job_id}) or 0)
                for table in (
                    "applications",
                    "application_status_history",
                    "application_engagements",
                )
            }

    def test_owner_delete_cascades_dependents_and_tombstones_only_owned_objects(self) -> None:
        owner_one_objects = self._seed_dependents(
            self.user_one, self.job_one, "owner-one"
        )
        owner_two_objects = self._seed_dependents(
            self.user_two, self.job_two, "owner-two"
        )
        repository = retention_module.PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        )

        repository.delete_job(self.job_one)

        self.assertEqual(
            {"applications": 0, "application_status_history": 0, "application_engagements": 0},
            self._dependent_counts(self.user_one, self.job_one),
        )
        self.assertEqual(
            {"applications": 1, "application_status_history": 1, "application_engagements": 1},
            self._dependent_counts(self.user_two, self.job_two),
        )
        with self.admin_engine.begin() as connection:
            self.assertEqual(0, connection.scalar(text(
                "SELECT count(*) FROM careertrellis.jobs WHERE id=:job AND owner_user_id=:owner"
            ), {"job": self.job_one, "owner": self.user_one}))
            self.assertEqual(1, connection.scalar(text(
                "SELECT count(*) FROM careertrellis.jobs WHERE id=:job AND owner_user_id=:owner"
            ), {"job": self.job_two, "owner": self.user_two}))
            owner_one_deleted = connection.execute(text(
                """SELECT deleted_at FROM careertrellis.stored_objects
                   WHERE id IN (CAST(:resume AS uuid), CAST(:letter AS uuid))
                   ORDER BY id"""
            ), {"resume": owner_one_objects[0], "letter": owner_one_objects[1]}).scalars().all()
            owner_two_deleted = connection.execute(text(
                """SELECT deleted_at FROM careertrellis.stored_objects
                   WHERE id IN (CAST(:resume AS uuid), CAST(:letter AS uuid))
                   ORDER BY id"""
            ), {"resume": owner_two_objects[0], "letter": owner_two_objects[1]}).scalars().all()
        self.assertEqual(2, len(owner_one_deleted))
        self.assertTrue(all(value is not None for value in owner_one_deleted))
        self.assertEqual([None, None], owner_two_deleted)

    def test_same_owner_shared_object_survives_with_other_application_reference(self) -> None:
        shared_id, exclusive_id = self._seed_dependents(
            self.user_one, self.job_one, "shared-source"
        )
        other_job = postgres_harness.PostgreSQLTenantIsolationTests._insert_job(
            self,
            self.user_one,
            "https://dependent-delete.example.test/shared-survivor",
            title="Shared survivor",
        )
        with self.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO careertrellis.applications
                       (owner_user_id, job_id, company, position, status,
                        tailored_resume_object_id)
                       VALUES (:owner, :job, 'Same Synthetic Employer',
                               'Shared Survivor', 'tailored', CAST(:shared AS uuid))"""
                ),
                {"owner": self.user_one, "job": other_job, "shared": shared_id},
            )

        retention_module.PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        ).delete_job(self.job_one)

        with self.admin_engine.begin() as connection:
            survivor = connection.execute(
                text(
                    """SELECT tailored_resume_object_id
                       FROM careertrellis.applications
                       WHERE owner_user_id=:owner AND job_id=:job"""
                ),
                {"owner": self.user_one, "job": other_job},
            ).scalar_one()
            object_state = connection.execute(
                text(
                    """SELECT id, deleted_at FROM careertrellis.stored_objects
                       WHERE id IN (CAST(:shared AS uuid), CAST(:exclusive AS uuid))"""
                ),
                {"shared": shared_id, "exclusive": exclusive_id},
            ).mappings().all()
        deleted_by_id = {str(row["id"]): row["deleted_at"] for row in object_state}
        self.assertEqual(shared_id, str(survivor))
        self.assertIsNone(deleted_by_id[shared_id])
        self.assertIsNotNone(deleted_by_id[exclusive_id])

    def test_cross_owner_missing_and_concurrent_stale_delete_fail_closed(self) -> None:
        owner_one = retention_module.PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        )
        owner_two = retention_module.PostgreSQLJobRetentionRepository(
            self.user_two, engine=self.runtime_engine
        )
        with self.assertRaises(retention_module.JobRetentionNotFound):
            owner_two.delete_job(self.job_one)
        with self.assertRaises(retention_module.JobRetentionNotFound):
            owner_one.delete_job(999_999_999)

        results: list[str] = []

        def delete_once() -> None:
            try:
                owner_one.delete_job(self.job_one)
                results.append("deleted")
            except retention_module.JobRetentionNotFound:
                results.append("stale")

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda _index: delete_once(), range(2)))
        self.assertEqual(["deleted", "stale"], sorted(results))

    def test_delete_failure_rolls_back_job_dependencies_and_object_tombstones(self) -> None:
        object_ids = self._seed_dependents(self.user_one, self.job_one, "rollback")
        function_name = "test_fail_dependent_job_delete"
        with self.admin_engine.begin() as connection:
            connection.execute(text(f"""CREATE FUNCTION careertrellis.{function_name}()
                RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                    RAISE EXCEPTION 'synthetic delete failure';
                END $$"""))
            connection.execute(text(f"""CREATE TRIGGER {function_name}
                BEFORE DELETE ON careertrellis.jobs FOR EACH ROW
                EXECUTE FUNCTION careertrellis.{function_name}()"""))
        try:
            repository = retention_module.PostgreSQLJobRetentionRepository(
                self.user_one, engine=self.runtime_engine
            )
            with self.assertRaises(Exception):
                repository.delete_job(self.job_one)
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
            self.assertEqual(0, connection.scalar(text(
                """SELECT count(*) FROM careertrellis.job_suppressions
                   WHERE owner_user_id=:owner"""
            ), {"owner": self.user_one}))
            active_objects = connection.scalar(text(
                """SELECT count(*) FROM careertrellis.stored_objects
                   WHERE id IN (CAST(:resume AS uuid), CAST(:letter AS uuid))
                     AND deleted_at IS NULL"""
            ), {"resume": object_ids[0], "letter": object_ids[1]})
        self.assertEqual(2, active_objects)
        self.assertEqual(
            {"applications": 1, "application_status_history": 1, "application_engagements": 1},
            self._dependent_counts(self.user_one, self.job_one),
        )


if __name__ == "__main__":
    unittest.main()
