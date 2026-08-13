"""PostgreSQL migration and row-level isolation integration tests.

Set CAREERTRELLIS_POSTGRES_TEST_ADMIN_URL to an isolated PostgreSQL 18 server.
The test creates and removes one dedicated database and four fixed test roles.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError

from sqlite_postgres_import import import_sqlite_workspace
import object_store as object_store_module
import postgres_database as postgres_database_module
import provider_secrets as provider_secrets_module
import stored_objects as stored_objects_module
from base_resumes import BaseResumeNotFound, LastBaseResumeError
from profile_repository import PostgreSQLProfileRepository
from discovery_repository import PostgreSQLDiscoveryRepository
from headquarters_cache_repository import PostgreSQLHeadquartersCacheRepository
from search_run_repository import (
    PostgreSQLSearchRunRepository,
    SearchRunAlreadyActive,
    SearchRunLeaseLost,
    SearchRunUnavailable,
    claim_next_postgres_search_run,
)
from user_data_export_repository import PostgreSQLUserDataExportRepository
from lifecycle_repository import PostgreSQLLifecycleRepository
from verification_repository import (
    PostgreSQLVerificationRepository,
    VerificationTargetChanged,
)
from job_retention_repository import (
    MAX_RETENTION_BATCH,
    CleanupBatchTooLarge,
    CleanupPreviewChanged,
    JobRetentionNotFound,
    JobRetentionProtected,
    PostgreSQLJobRetentionRepository,
)
from application_workspace_repository import (
    ApplicationWorkspaceChanged,
    ApplicationWorkspaceNotFound,
    PostgreSQLApplicationWorkspaceRepository,
)
from application_read_repository import PostgreSQLApplicationReadRepository
from application_engagements import EngagementLimitError, MAX_ENGAGEMENTS_PER_JOB
from job_suppressions import job_url_fingerprint
from maps_providers import HeadquartersResult

from postgres_database import (
    PostgreSQLConfigurationError,
    dispose_postgres_engines,
    identity_transaction,
    postgres_url,
    runtime_postgres_engine,
    set_identity_context,
    set_tenant_context,
    tenant_transaction,
)


ADMIN_URL_ENV = "CAREERTRELLIS_POSTGRES_TEST_ADMIN_URL"
DATABASE_NAME = "careertrellis_rls_test"
SCHEMA_OWNER = "careertrellis_schema_owner"
MIGRATION_ROLE = "careertrellis_migration"
RUNTIME_ROLE = "careertrellis_runtime"
WORKER_ROLE = "careertrellis_worker"
MIGRATION_PASSWORD = "migration-test-only"
RUNTIME_PASSWORD = "runtime-test-only"
WORKER_PASSWORD = "worker-test-only"


def _provider_secret_environment(active_key_id: str = "kek-test-a") -> dict[str, str]:
    keys = {
        "kek-test-a": base64.urlsafe_b64encode(bytes(range(32))).decode("ascii"),
        "kek-test-b": base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode("ascii"),
    }
    return {
        "CAREERTRELLIS_PROVIDER_SECRET_STORE": "postgres_encrypted",
        "CAREERTRELLIS_PROVIDER_SECRET_ACTIVE_KEY_ID": active_key_id,
        "CAREERTRELLIS_PROVIDER_SECRET_KEYRING_B64": base64.urlsafe_b64encode(
            json.dumps(keys, separators=(",", ":")).encode("utf-8")
        ).decode("ascii"),
    }


class PostgreSQLConfigurationTests(unittest.TestCase):
    def test_postgres_url_requires_postgresql_and_psycopg(self) -> None:
        normalized = postgres_url("postgresql://runtime:secret@database.test/app")
        self.assertEqual("postgresql+psycopg", normalized.drivername)
        with self.assertRaises(PostgreSQLConfigurationError):
            postgres_url("sqlite:///local.db")
        with self.assertRaises(PostgreSQLConfigurationError):
            postgres_url("postgresql+psycopg2://runtime:secret@database.test/app")
        with self.assertRaises(PostgreSQLConfigurationError):
            postgres_url("postgresql://runtime:secret@database.test")

    def test_tenant_context_rejects_ambiguous_or_nonpositive_ids(self) -> None:
        connection = Mock()
        for value in (True, 0, -1, 1.5, "1.5", "", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    set_tenant_context(connection, value)
        connection.execute.assert_not_called()

        set_tenant_context(connection, "42")
        connection.execute.assert_called_once()

    def test_identity_context_requires_a_bounded_workos_user_id(self) -> None:
        connection = Mock()
        for value in (
            None,
            "",
            "user_",
            "other_123",
            "user_bad id",
            "user_" + "x" * 251,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    set_identity_context(connection, value)
        connection.execute.assert_not_called()

        set_identity_context(connection, "user_valid123")
        connection.execute.assert_called_once()

    def test_runtime_pool_is_reused_and_disposed_without_logging_its_url(self) -> None:
        engine = Mock()
        dispose_postgres_engines()
        with (
            patch.dict(
                os.environ,
                {"CAREERTRELLIS_DATABASE_URL": "postgresql+psycopg://runtime:secret@database.test/app"},
                clear=False,
            ),
            patch("postgres_database.create_postgres_engine", return_value=engine) as create,
        ):
            self.assertIs(engine, runtime_postgres_engine())
            self.assertIs(engine, runtime_postgres_engine())
        create.assert_called_once()
        dispose_postgres_engines()
        engine.dispose.assert_called_once_with()


class PostgreSQLTenantIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw_admin_url = os.environ.get(ADMIN_URL_ENV, "").strip()
        if not raw_admin_url:
            raise unittest.SkipTest(f"{ADMIN_URL_ENV} is not configured")
        cls.server_url = make_url(raw_admin_url).set(database="postgres")
        if cls.server_url.get_backend_name() != "postgresql":
            raise RuntimeError("The PostgreSQL integration-test URL must use PostgreSQL.")
        cls.admin_server_engine = create_engine(cls.server_url, isolation_level="AUTOCOMMIT")
        cls._drop_cluster_objects()
        cls._create_cluster_objects()

        cls.admin_database_url = cls.server_url.set(database=DATABASE_NAME)
        cls.admin_engine = create_engine(cls.admin_database_url)
        with cls.admin_engine.begin() as connection:
            connection.execute(
                text(f"CREATE SCHEMA careertrellis AUTHORIZATION {SCHEMA_OWNER}")
            )

        cls.migration_url = cls._role_url(MIGRATION_ROLE, MIGRATION_PASSWORD)
        previous_migration_url = os.environ.get("CAREERTRELLIS_MIGRATION_DATABASE_URL")
        os.environ["CAREERTRELLIS_MIGRATION_DATABASE_URL"] = cls.migration_url.render_as_string(
            hide_password=False
        )
        try:
            alembic_config = Config(str(Path(__file__).with_name("alembic.ini")))
            command.upgrade(alembic_config, "head")
            command.upgrade(alembic_config, "head")
            command.current(alembic_config, check_heads=True)
        finally:
            if previous_migration_url is None:
                os.environ.pop("CAREERTRELLIS_MIGRATION_DATABASE_URL", None)
            else:
                os.environ["CAREERTRELLIS_MIGRATION_DATABASE_URL"] = previous_migration_url

        cls._grant_runtime_privileges()
        cls.runtime_engine = create_engine(
            cls._role_url(RUNTIME_ROLE, RUNTIME_PASSWORD),
            pool_size=2,
            max_overflow=0,
            pool_reset_on_return="rollback",
        )
        cls.worker_engine = create_engine(
            cls._role_url(WORKER_ROLE, WORKER_PASSWORD),
            pool_size=2,
            max_overflow=0,
            pool_reset_on_return="rollback",
        )

    @classmethod
    def _role_url(cls, role: str, password: str) -> URL:
        return cls.server_url.set(
            drivername="postgresql+psycopg",
            username=role,
            password=password,
            database=DATABASE_NAME,
        )

    @classmethod
    def _drop_cluster_objects(cls) -> None:
        with cls.admin_server_engine.connect() as connection:
            connection.exec_driver_sql(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{DATABASE_NAME}' AND pid <> pg_backend_pid()"
            )
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{DATABASE_NAME}"')
            for role in (MIGRATION_ROLE, RUNTIME_ROLE, WORKER_ROLE, SCHEMA_OWNER):
                connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')

    @classmethod
    def _create_cluster_objects(cls) -> None:
        with cls.admin_server_engine.connect() as connection:
            connection.exec_driver_sql(
                f"CREATE ROLE {SCHEMA_OWNER} NOLOGIN NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
            )
            connection.exec_driver_sql(
                f"CREATE ROLE {MIGRATION_ROLE} LOGIN NOSUPERUSER NOCREATEDB "
                f"NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD '{MIGRATION_PASSWORD}'"
            )
            connection.exec_driver_sql(
                f"CREATE ROLE {RUNTIME_ROLE} LOGIN NOSUPERUSER NOCREATEDB "
                f"NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD '{RUNTIME_PASSWORD}'"
            )
            connection.exec_driver_sql(
                f"CREATE ROLE {WORKER_ROLE} LOGIN NOSUPERUSER NOCREATEDB "
                f"NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD '{WORKER_PASSWORD}'"
            )
            connection.exec_driver_sql(f"GRANT {SCHEMA_OWNER} TO {MIGRATION_ROLE}")
            connection.exec_driver_sql(f'CREATE DATABASE "{DATABASE_NAME}"')
            connection.exec_driver_sql(f'REVOKE ALL ON DATABASE "{DATABASE_NAME}" FROM PUBLIC')
            connection.exec_driver_sql(
                f"GRANT CONNECT ON DATABASE \"{DATABASE_NAME}\" TO "
                f"{MIGRATION_ROLE}, {RUNTIME_ROLE}, {WORKER_ROLE}"
            )

    @classmethod
    def _grant_runtime_privileges(cls) -> None:
        with cls.admin_engine.begin() as connection:
            connection.exec_driver_sql(f"SET ROLE {SCHEMA_OWNER}")
            connection.exec_driver_sql(
                f"GRANT USAGE ON SCHEMA careertrellis TO {RUNTIME_ROLE}, {WORKER_ROLE}"
            )
            connection.exec_driver_sql(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA careertrellis "
                f"TO {RUNTIME_ROLE}, {WORKER_ROLE}"
            )
            connection.exec_driver_sql(
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA careertrellis "
                f"TO {RUNTIME_ROLE}, {WORKER_ROLE}"
            )
            connection.exec_driver_sql(
                "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA careertrellis "
                f"TO {RUNTIME_ROLE}, {WORKER_ROLE}"
            )
            connection.exec_driver_sql(
                "REVOKE ALL ON careertrellis.search_run_claim_queue "
                f"FROM {RUNTIME_ROLE}, {WORKER_ROLE}"
            )
            connection.exec_driver_sql(
                "REVOKE ALL ON FUNCTION careertrellis.sync_search_run_claim_queue() "
                f"FROM {RUNTIME_ROLE}, {WORKER_ROLE}"
            )
            connection.exec_driver_sql(
                "REVOKE ALL ON FUNCTION careertrellis.claim_next_search_run(uuid, integer) "
                f"FROM {RUNTIME_ROLE}"
            )
            connection.exec_driver_sql(
                "REVOKE INSERT, UPDATE ON careertrellis.app_users "
                f"FROM {RUNTIME_ROLE}, {WORKER_ROLE}"
            )
            connection.exec_driver_sql(
                "GRANT INSERT (workos_user_id, last_seen_at) ON careertrellis.app_users "
                f"TO {RUNTIME_ROLE}, {WORKER_ROLE}"
            )
            connection.exec_driver_sql(
                "GRANT UPDATE (last_seen_at) ON careertrellis.app_users "
                f"TO {RUNTIME_ROLE}, {WORKER_ROLE}"
            )
            connection.exec_driver_sql("RESET ROLE")

    @classmethod
    def tearDownClass(cls) -> None:
        for name in ("runtime_engine", "worker_engine", "admin_engine"):
            engine = getattr(cls, name, None)
            if engine is not None:
                engine.dispose()
        try:
            cls._drop_cluster_objects()
        finally:
            cls.admin_server_engine.dispose()

    def setUp(self) -> None:
        with self.admin_engine.begin() as connection:
            connection.exec_driver_sql(
                "TRUNCATE TABLE careertrellis.app_users RESTART IDENTITY CASCADE"
            )
            self.user_one = connection.scalar(
                text(
                    "INSERT INTO careertrellis.app_users "
                    "(workos_user_id, role) VALUES ('user_tenant_one', 'owner') RETURNING id"
                )
            )
            self.user_two = connection.scalar(
                text(
                    "INSERT INTO careertrellis.app_users "
                    "(workos_user_id, role) VALUES ('user_tenant_two', 'user') RETURNING id"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO careertrellis.profiles (owner_user_id, name, email) "
                    "VALUES (:one, 'Tenant One', 'one@example.test'), "
                    "(:two, 'Tenant Two', 'two@example.test')"
                ),
                {"one": self.user_one, "two": self.user_two},
            )
            self.job_one = connection.scalar(
                text(
                    "INSERT INTO careertrellis.jobs (owner_user_id, title, url) "
                    "VALUES (:owner, 'One Job', 'https://example.test/one') RETURNING id"
                ),
                {"owner": self.user_one},
            )
            self.job_two = connection.scalar(
                text(
                    "INSERT INTO careertrellis.jobs (owner_user_id, title, url) "
                    "VALUES (:owner, 'Two Job', 'https://example.test/two') RETURNING id"
                ),
                {"owner": self.user_two},
            )

    def _insert_job(
        self,
        owner_user_id: int,
        url: str,
        *,
        title: str = "Retention job",
        status: str = "matched",
        archived_from_status: str | None = None,
    ) -> int:
        with self.admin_engine.begin() as connection:
            return connection.scalar(
                text(
                    """
                    INSERT INTO careertrellis.jobs (
                        owner_user_id, title, company, url, status,
                        archived_at, archived_from_status
                    ) VALUES (
                        :owner, :title, 'Synthetic Employer', :url, :status,
                        CASE WHEN :status = 'archived' THEN CURRENT_TIMESTAMP END,
                        :archived_from_status
                    ) RETURNING id
                    """
                ),
                {
                    "owner": owner_user_id,
                    "title": title,
                    "url": url,
                    "status": status,
                    "archived_from_status": archived_from_status,
                },
            )

    def test_no_context_exposes_no_protected_rows(self) -> None:
        with self.runtime_engine.begin() as connection:
            self.assertEqual(0, connection.scalar(text("SELECT count(*) FROM careertrellis.jobs")))
            self.assertEqual(0, connection.scalar(text("SELECT count(*) FROM careertrellis.profiles")))

    def test_runtime_readiness_probe_requires_no_owner_or_tenant_context(self) -> None:
        postgres_database_module.check_postgres_ready(self.runtime_engine)

        with self.runtime_engine.connect() as connection:
            self.assertEqual(
                1,
                connection.scalar(text("SELECT 1")),
            )
            self.assertIn(
                connection.scalar(
                    text("SELECT current_setting('careertrellis.user_id', true)")
                ),
                (None, ""),
            )
            self.assertIn(
                connection.scalar(
                    text("SELECT current_setting('careertrellis.workos_user_id', true)")
                ),
                (None, ""),
            )
            self.assertEqual(
                0,
                connection.scalar(text("SELECT count(*) FROM careertrellis.jobs")),
            )

    def test_identity_context_exposes_only_the_matching_user_mapping(self) -> None:
        with identity_transaction(self.runtime_engine, "user_tenant_one") as connection:
            rows = connection.execute(
                text("SELECT workos_user_id FROM careertrellis.app_users")
            ).scalars().all()
        self.assertEqual(["user_tenant_one"], rows)

    def test_runtime_cannot_choose_or_change_authorization_fields(self) -> None:
        with self.assertRaises(DBAPIError):
            with identity_transaction(self.runtime_engine, "user_new") as connection:
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.app_users (workos_user_id, role) "
                        "VALUES ('user_new', 'owner')"
                    )
                )

        with self.assertRaises(DBAPIError):
            with tenant_transaction(
                self.runtime_engine, self.user_one, "user_tenant_one"
            ) as connection:
                connection.execute(
                    text("UPDATE careertrellis.app_users SET role = 'owner' WHERE id = :id"),
                    {"id": self.user_one},
                )

        with tenant_transaction(
            self.runtime_engine, self.user_one, "user_tenant_one"
        ) as connection:
            updated = connection.execute(
                text(
                    "UPDATE careertrellis.app_users "
                    "SET last_seen_at = CURRENT_TIMESTAMP WHERE id = :id"
                ),
                {"id": self.user_one},
            )
            self.assertEqual(1, updated.rowcount)

    def test_tenant_context_blocks_cross_user_reads_updates_and_inserts(self) -> None:
        with tenant_transaction(
            self.runtime_engine, self.user_one, "user_tenant_one"
        ) as connection:
            self.assertEqual(
                [("One Job",)],
                connection.execute(
                    text("SELECT title FROM careertrellis.jobs ORDER BY id")
                ).all(),
            )
            updated = connection.execute(
                text("UPDATE careertrellis.jobs SET title = 'Changed' WHERE id = :id"),
                {"id": self.job_two},
            )
            self.assertEqual(0, updated.rowcount)
            deleted = connection.execute(
                text("DELETE FROM careertrellis.jobs WHERE id = :id"),
                {"id": self.job_two},
            )
            self.assertEqual(0, deleted.rowcount)

        with self.assertRaises(DBAPIError):
            with tenant_transaction(self.runtime_engine, self.user_one) as connection:
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.jobs (owner_user_id, title, url) "
                        "VALUES (:other, 'Forbidden', 'https://example.test/forbidden')"
                    ),
                    {"other": self.user_two},
                )

    def test_user_data_export_snapshot_is_owner_scoped_and_omits_internal_material(self) -> None:
        with self.admin_engine.begin() as connection:
            resume_one = connection.scalar(
                text(
                    "INSERT INTO careertrellis.base_resumes "
                    "(owner_user_id, name, content, evidence) "
                    "VALUES (:owner, 'One Resume', 'one private resume', "
                    "'{\"skills\":\"One\"}'::jsonb) RETURNING id"
                ),
                {"owner": self.user_one},
            )
            resume_two = connection.scalar(
                text(
                    "INSERT INTO careertrellis.base_resumes "
                    "(owner_user_id, name, content, evidence) "
                    "VALUES (:owner, 'Two Resume', 'two private resume', "
                    "'{\"skills\":\"Two\"}'::jsonb) RETURNING id"
                ),
                {"owner": self.user_two},
            )
            connection.execute(
                text(
                    "UPDATE careertrellis.profiles SET active_base_resume_id = :resume "
                    "WHERE owner_user_id = :owner"
                ),
                {"resume": resume_one, "owner": self.user_one},
            )
            connection.execute(
                text(
                    "UPDATE careertrellis.profiles SET active_base_resume_id = :resume "
                    "WHERE owner_user_id = :owner"
                ),
                {"resume": resume_two, "owner": self.user_two},
            )
            for owner, resume, job, marker in (
                (self.user_one, resume_one, self.job_one, "one"),
                (self.user_two, resume_two, self.job_two, "two"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.base_resume_versions "
                        "(owner_user_id, base_resume_id, version_number, name, resume_mode, "
                        " content, evidence) VALUES "
                        "(:owner, :resume, 1, :name, 'general_professional', :content, '{}'::jsonb)"
                    ),
                    {
                        "owner": owner,
                        "resume": resume,
                        "name": f"{marker} version",
                        "content": f"{marker} version content",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.saved_searches "
                        "(owner_user_id, name, keywords) VALUES (:owner, :name, :keywords)"
                    ),
                    {"owner": owner, "name": f"{marker} search", "keywords": marker},
                )
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.applications "
                        "(owner_user_id, job_id, company, position, tailored_resume_text, "
                        " cover_letter, submission_evidence, notes, interview_prep) VALUES "
                        "(:owner, :job, :company, :position, :resume_text, :letter, "
                        " :evidence, :notes, :prep)"
                    ),
                    {
                        "owner": owner,
                        "job": job,
                        "company": f"{marker} company",
                        "position": f"{marker} position",
                        "resume_text": f"{marker} tailored resume",
                        "letter": f"{marker} cover letter",
                        "evidence": json.dumps({"marker": marker}),
                        "notes": f"{marker} application notes",
                        "prep": f"{marker} interview prep",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.application_status_history "
                        "(owner_user_id, job_id, from_status, to_status, source, notes) "
                        "VALUES (:owner, :job, 'matched', 'applied', 'manual', :notes)"
                    ),
                    {"owner": owner, "job": job, "notes": f"{marker} history"},
                )
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.application_engagements "
                        "(owner_user_id, job_id, engagement_type, name, notes) "
                        "VALUES (:owner, :job, 'recruiter', :name, :notes)"
                    ),
                    {"owner": owner, "job": job, "name": f"{marker} recruiter", "notes": marker},
                )
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.job_suppressions "
                        "(owner_user_id, url_fingerprint, hostname, company, title, deletion_source) "
                        "VALUES (:owner, :fingerprint, 'example.test', :company, :title, 'manual_delete')"
                    ),
                    {
                        "owner": owner,
                        "fingerprint": f"private-{marker}-fingerprint",
                        "company": f"{marker} suppressed company",
                        "title": f"{marker} suppressed title",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.source_diagnostics "
                        "(owner_user_id, provider, diagnostic_code, counters) "
                        "VALUES (:owner, 'lever', 'format_drift', CAST(:counters AS jsonb))"
                    ),
                    {"owner": owner, "counters": json.dumps({"marker": marker})},
                )
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.headquarters_cache "
                        "(owner_user_id, cache_key, provider, address, country_code) "
                        "VALUES (:owner, :cache_key, 'openstreetmap', :address, 'us')"
                    ),
                    {
                        "owner": owner,
                        "cache_key": f"private-{marker}-cache-key",
                        "address": f"private {marker} cached address",
                    },
                )

        snapshot_one = PostgreSQLUserDataExportRepository(
            self.user_one, engine=self.runtime_engine
        ).snapshot()
        snapshot_two = PostgreSQLUserDataExportRepository(
            self.user_two, engine=self.runtime_engine
        ).snapshot()

        encoded_one = json.dumps(snapshot_one)
        self.assertEqual(["Tenant One"], [row["name"] for row in snapshot_one["profile"]])
        self.assertEqual(["One Job"], [row["title"] for row in snapshot_one["jobs"]])
        self.assertEqual(["Tenant Two"], [row["name"] for row in snapshot_two["profile"]])
        self.assertIn("one private resume", encoded_one)
        self.assertNotIn("two private resume", encoded_one)
        self.assertNotIn("owner_user_id", encoded_one)
        self.assertNotIn("private-one-fingerprint", encoded_one)
        self.assertNotIn("private-one-cache-key", encoded_one)
        self.assertNotIn("private one cached address", encoded_one)

    def test_base_resume_soft_delete_is_available_and_tenant_isolated(self) -> None:
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            resume_id = connection.scalar(
                text(
                    "INSERT INTO careertrellis.base_resumes "
                    "(owner_user_id, name, content) "
                    "VALUES (:owner, 'Primary Resume', 'Synthetic content') RETURNING id"
                ),
                {"owner": self.user_one},
            )
            updated = connection.execute(
                text(
                    "UPDATE careertrellis.base_resumes "
                    "SET deleted_at = CURRENT_TIMESTAMP WHERE id = :resume_id"
                ),
                {"resume_id": resume_id},
            )
            self.assertEqual(1, updated.rowcount)
            self.assertIsNotNone(
                connection.scalar(
                    text(
                        "SELECT deleted_at FROM careertrellis.base_resumes "
                        "WHERE id = :resume_id"
                    ),
                    {"resume_id": resume_id},
                )
            )

        with tenant_transaction(self.runtime_engine, self.user_two) as connection:
            self.assertIsNone(
                connection.scalar(
                    text(
                        "SELECT deleted_at FROM careertrellis.base_resumes "
                        "WHERE id = :resume_id"
                    ),
                    {"resume_id": resume_id},
                )
            )
            self.assertEqual(
                0,
                connection.execute(
                    text(
                        "UPDATE careertrellis.base_resumes "
                        "SET deleted_at = NULL WHERE id = :resume_id"
                    ),
                    {"resume_id": resume_id},
                ).rowcount,
            )

        with self.admin_engine.begin() as connection:
            index_definition = connection.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'careertrellis' "
                    "AND indexname = 'idx_base_resumes_owner_deleted_updated'"
                )
            )
        self.assertIsNotNone(index_definition)
        self.assertIn("owner_user_id", index_definition)
        self.assertIn("deleted_at", index_definition)
        self.assertIn("updated_at DESC", index_definition)

    def test_profile_repository_is_owner_scoped_atomic_and_soft_deletes_resumes(self) -> None:
        owner_one = PostgreSQLProfileRepository(self.user_one, engine=self.runtime_engine)
        owner_two = PostgreSQLProfileRepository(self.user_two, engine=self.runtime_engine)

        self.assertEqual("Tenant One", owner_one.get_profile()["name"])
        self.assertEqual("Tenant Two", owner_two.get_profile()["name"])
        self.assertNotIn("owner_user_id", owner_one.get_profile())
        created = owner_one.update_profile(
            {
                "name": "Updated Tenant One",
                "email": "updated@example.test",
                "phone": "",
                "github": "",
                "linkedin": "",
                "website": "",
                "base_resume_id": None,
                "base_resume_id_provided": False,
                "base_resume_name": "Primary Resume",
                "base_resume_text": "Synthetic primary resume",
                "professional_evidence": {"skills": "PostgreSQL"},
                "resume_mode": "it",
                "ai_provider": "gemini",
                "ai_model": "gemini-2.5-flash",
                "maps_provider": "openstreetmap",
                "prefer_us_headquarters": True,
                "interface_language": "en",
            }
        )
        first_resume_id = created["base_resume_id"]
        self.assertEqual(1, created["base_resume_version"])
        self.assertTrue(created["base_resume_version_created"])
        synthesized = owner_one.get_profile()
        self.assertEqual("Updated Tenant One", synthesized["name"])
        self.assertEqual("Synthetic primary resume", synthesized["base_resume_text"])
        self.assertEqual("it", synthesized["resume_mode"])
        self.assertEqual(first_resume_id, synthesized["active_base_resume_id"])
        stored_resume = owner_one.get_base_resume(first_resume_id)
        self.assertNotIn("owner_user_id", stored_resume)
        self.assertEqual("PostgreSQL", stored_resume["professional_evidence"]["skills"])
        with self.assertRaises(BaseResumeNotFound):
            owner_two.get_base_resume(first_resume_id)

        second = owner_one.update_profile(
            {
                "name": "Updated Tenant One",
                "email": "updated@example.test",
                "phone": "",
                "github": "",
                "linkedin": "",
                "website": "",
                "base_resume_id": None,
                "base_resume_id_provided": True,
                "base_resume_name": "Executive Resume",
                "base_resume_text": "Synthetic executive resume",
                "professional_evidence": {},
                "resume_mode": "technical_executive",
                "ai_provider": "gemini",
                "ai_model": "gemini-2.5-flash",
                "maps_provider": "openstreetmap",
                "prefer_us_headquarters": True,
                "interface_language": "en",
            }
        )
        second_resume_id = second["base_resume_id"]
        self.assertNotEqual(first_resume_id, second_resume_id)
        replacement = owner_one.delete_base_resume(second_resume_id)
        self.assertEqual(first_resume_id, replacement["id"])
        with self.assertRaises(BaseResumeNotFound):
            owner_one.get_base_resume(second_resume_id)
        with self.assertRaises(LastBaseResumeError):
            owner_one.delete_base_resume(first_resume_id)
        with self.admin_engine.begin() as connection:
            self.assertIsNotNone(
                connection.scalar(
                    text(
                        "SELECT deleted_at FROM careertrellis.base_resumes "
                        "WHERE id = :resume_id"
                    ),
                    {"resume_id": second_resume_id},
                )
            )

    def test_search_keyword_cache_and_catalog_context_are_owner_isolated(self) -> None:
        profile_one = PostgreSQLProfileRepository(self.user_one, engine=self.runtime_engine)
        profile_two = PostgreSQLProfileRepository(self.user_two, engine=self.runtime_engine)
        discovery_one = PostgreSQLDiscoveryRepository(self.user_one, engine=self.runtime_engine)
        discovery_two = PostgreSQLDiscoveryRepository(self.user_two, engine=self.runtime_engine)

        self.assertTrue(
            profile_one.cache_suggested_keywords_if_empty(
                "Owner One Director,Owner One Analyst"
            )
        )
        self.assertFalse(
            profile_one.cache_suggested_keywords_if_empty("Must not replace owner one")
        )
        self.assertEqual(
            "Owner One Director,Owner One Analyst",
            profile_one.get_profile()["suggested_keywords"],
        )
        self.assertEqual("", profile_two.get_profile()["suggested_keywords"])
        self.assertTrue(
            profile_two.cache_suggested_keywords_if_empty("Owner Two Engineer")
        )
        self.assertEqual(
            "Owner Two Engineer", profile_two.get_profile()["suggested_keywords"]
        )

        archived_url = "https://jobs.lever.co/tenant-one/archived"
        self._insert_job(
            self.user_one,
            archived_url,
            title="Owner one archived role",
            status="archived",
            archived_from_status="matched",
        )
        suppressed_one = "https://jobs.lever.co/tenant-one/suppressed"
        suppressed_two = "https://jobs.lever.co/tenant-two/suppressed"
        for owner_user_id, suppressed_url in (
            (self.user_one, suppressed_one),
            (self.user_two, suppressed_two),
        ):
            with tenant_transaction(self.runtime_engine, owner_user_id) as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO careertrellis.job_suppressions
                            (owner_user_id, url_fingerprint, hostname, deletion_source)
                        VALUES (:owner, :fingerprint, 'jobs.lever.co', 'manual')
                        """
                    ),
                    {
                        "owner": owner_user_id,
                        "fingerprint": job_url_fingerprint(suppressed_url),
                    },
                )

        self.assertEqual(
            {
                "known_status_by_url": {
                    "https://example.test/one": "matched",
                    archived_url: "archived",
                },
                "suppressed_fingerprints": {job_url_fingerprint(suppressed_one)},
            },
            discovery_one.search_catalog_context(),
        )
        self.assertEqual(
            {
                "known_status_by_url": {"https://example.test/two": "matched"},
                "suppressed_fingerprints": {job_url_fingerprint(suppressed_two)},
            },
            discovery_two.search_catalog_context(),
        )

        with self.runtime_engine.begin() as connection:
            self.assertEqual(
                [],
                connection.execute(
                    text("SELECT url, status FROM careertrellis.jobs")
                ).all(),
            )
            self.assertEqual(
                [],
                connection.execute(
                    text("SELECT url_fingerprint FROM careertrellis.job_suppressions")
                ).all(),
            )
            self.assertEqual(
                [],
                connection.execute(
                    text("SELECT suggested_keywords FROM careertrellis.profiles")
                ).all(),
            )

    def test_headquarters_cache_is_owner_isolated_and_hidden_without_context(self) -> None:
        owner_one = PostgreSQLHeadquartersCacheRepository(
            self.user_one, engine=self.runtime_engine
        )
        owner_two = PostgreSQLHeadquartersCacheRepository(
            self.user_two, engine=self.runtime_engine
        )
        shared_key = "a" * 64
        owner_one_result = HeadquartersResult(
            address="1 Synthetic Plaza, Dayton, OH 45402, United States",
            source="openstreetmap",
            attribution="© OpenStreetMap contributors",
            country_code="US",
        )
        owner_two_result = HeadquartersResult(
            address="2 Synthetic Way, Columbus, OH 43215, United States",
            source="openstreetmap",
            attribution="© OpenStreetMap contributors",
            country_code="US",
        )

        self.assertTrue(
            owner_one.cache_openstreetmap_result(
                shared_key, owner_one_result, "openstreetmap"
            )
        )
        self.assertEqual(owner_one_result, owner_one.get(shared_key))
        self.assertIsNone(owner_two.get(shared_key))
        self.assertTrue(
            owner_two.cache_openstreetmap_result(
                shared_key, owner_two_result, "openstreetmap"
            )
        )
        self.assertEqual(owner_one_result, owner_one.get(shared_key))
        self.assertEqual(owner_two_result, owner_two.get(shared_key))

        with self.runtime_engine.begin() as connection:
            self.assertEqual(
                [],
                connection.execute(
                    text(
                        "SELECT cache_key, address "
                        "FROM careertrellis.headquarters_cache"
                    )
                ).all(),
            )

        with self.admin_engine.begin() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT owner_user_id, address
                    FROM careertrellis.headquarters_cache
                    WHERE cache_key = :cache_key
                    ORDER BY owner_user_id
                    """
                ),
                {"cache_key": shared_key},
            ).all()
        self.assertEqual(
            [
                (self.user_one, owner_one_result.address),
                (self.user_two, owner_two_result.address),
            ],
            [tuple(row) for row in rows],
        )

    def test_provider_context_profiles_are_owner_isolated_for_validation_ocr_and_import(self) -> None:
        owner_one = PostgreSQLProfileRepository(self.user_one, engine=self.runtime_engine)
        owner_two = PostgreSQLProfileRepository(self.user_two, engine=self.runtime_engine)

        def profile_values(
            name: str,
            resume_text: str,
            ai_provider: str,
            ai_model: str,
            maps_provider: str,
        ) -> dict:
            return {
                "name": name,
                "email": f"{name.lower().replace(' ', '-')}@example.test",
                "phone": "",
                "github": "",
                "linkedin": "",
                "website": "",
                "base_resume_id": None,
                "base_resume_id_provided": False,
                "base_resume_name": f"{name} Resume",
                "base_resume_text": resume_text,
                "professional_evidence": {},
                "resume_mode": "general_professional",
                "ai_provider": ai_provider,
                "ai_model": ai_model,
                "maps_provider": maps_provider,
                "prefer_us_headquarters": False,
                "interface_language": "en",
            }

        saved_one = owner_one.update_profile(
            profile_values(
                "Tenant One",
                "Owner one synthetic resume for OCR and manual import matching",
                "openai",
                "gpt-5-mini",
                "google",
            )
        )
        saved_two = owner_two.update_profile(
            profile_values(
                "Tenant Two",
                "Owner two synthetic resume for OCR and manual import matching",
                "gemini",
                "gemini-2.5-flash",
                "openstreetmap",
            )
        )

        profile_one = owner_one.get_profile()
        profile_two = owner_two.get_profile()
        self.assertEqual("openai", profile_one["ai_provider"])
        self.assertEqual("gpt-5-mini", profile_one["ai_model"])
        self.assertEqual("google", profile_one["maps_provider"])
        self.assertEqual(
            "Owner one synthetic resume for OCR and manual import matching",
            profile_one["base_resume_text"],
        )
        self.assertEqual("gemini", profile_two["ai_provider"])
        self.assertEqual("gemini-2.5-flash", profile_two["ai_model"])
        self.assertEqual("openstreetmap", profile_two["maps_provider"])
        self.assertEqual(
            "Owner two synthetic resume for OCR and manual import matching",
            profile_two["base_resume_text"],
        )
        self.assertNotEqual(saved_one["base_resume_id"], saved_two["base_resume_id"])
        with self.assertRaises(BaseResumeNotFound):
            owner_one.get_base_resume(saved_two["base_resume_id"])
        with self.assertRaises(BaseResumeNotFound):
            owner_two.get_base_resume(saved_one["base_resume_id"])

    def test_discovery_repository_is_owner_scoped_and_privacy_minimized(self) -> None:
        owner_one = PostgreSQLDiscoveryRepository(self.user_one, engine=self.runtime_engine)
        owner_two = PostgreSQLDiscoveryRepository(self.user_two, engine=self.runtime_engine)
        now = datetime.now(timezone.utc)
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            search_one = connection.scalar(
                text(
                    """
                    INSERT INTO careertrellis.saved_searches
                        (owner_user_id, name, keywords, location, schedule_frequency, next_alert_at)
                    VALUES
                        (:owner, 'Due Search', 'data', 'remote', 'daily', :next_alert)
                    RETURNING id
                    """
                ),
                {"owner": self.user_one, "next_alert": now - timedelta(minutes=1)},
            )
            suppression_one = connection.scalar(
                text(
                    """
                    INSERT INTO careertrellis.job_suppressions
                        (owner_user_id, url_fingerprint, hostname, company, title, deletion_source)
                    VALUES
                        (:owner, :fingerprint, 'example.test', 'Tenant One', 'Hidden', 'manual')
                    RETURNING id
                    """
                ),
                {
                    "owner": self.user_one,
                    "fingerprint": job_url_fingerprint("https://example.test/hidden-one"),
                },
            )
        with tenant_transaction(self.runtime_engine, self.user_two) as connection:
            search_two = connection.scalar(
                text(
                    """
                    INSERT INTO careertrellis.saved_searches
                        (owner_user_id, name, keywords, location, schedule_frequency, next_alert_at)
                    VALUES
                        (:owner, 'Other Search', 'private', 'elsewhere', 'daily', :next_alert)
                    RETURNING id
                    """
                ),
                {"owner": self.user_two, "next_alert": now - timedelta(minutes=1)},
            )
            suppression_two = connection.scalar(
                text(
                    """
                    INSERT INTO careertrellis.job_suppressions
                        (owner_user_id, url_fingerprint, hostname, company, title, deletion_source)
                    VALUES
                        (:owner, :fingerprint, 'other.test', 'Tenant Two', 'Other', 'manual')
                    RETURNING id
                    """
                ),
                {
                    "owner": self.user_two,
                    "fingerprint": job_url_fingerprint("https://other.test/hidden-two"),
                },
            )

        jobs = owner_one.list_jobs()
        self.assertEqual([self.job_one], [row["id"] for row in jobs])
        self.assertNotIn("owner_user_id", jobs[0])
        self.assertEqual(
            {"suppressed": False, "existing": None},
            owner_one.job_identity("https://example.test/two"),
        )
        self.assertTrue(
            owner_one.job_identity("https://example.test/hidden-one")["suppressed"]
        )
        suppressions = owner_one.list_job_suppressions()
        self.assertEqual(1, suppressions["count"])
        self.assertNotIn("url_fingerprint", suppressions["items"][0])
        self.assertNotIn("owner_user_id", suppressions["items"][0])
        self.assertEqual(0, owner_one.clear_job_suppression(suppression_two))
        self.assertEqual(1, owner_one.clear_job_suppression(suppression_one))

        self.assertEqual([search_one], [row["id"] for row in owner_one.list_saved_searches()])
        self.assertEqual([search_one], [row["id"] for row in owner_one.list_due_saved_searches(now)])
        self.assertFalse(owner_one.delete_saved_search(search_two))
        self.assertTrue(owner_one.delete_saved_search(search_one))
        self.assertEqual([search_two], [row["id"] for row in owner_two.list_saved_searches()])
        self.assertEqual([suppression_two], [row["id"] for row in owner_two.list_job_suppressions()["items"]])

        diagnostic_result = {
            "provider_alerts": [
                {"provider": "greenhouse", "code": "content_format_drift"},
                {"provider": "unknown", "code": "private-error"},
            ],
            "provider_health": {
                "greenhouse": {
                    "raw_candidates": 4,
                    "errors": ["must not be stored"],
                }
            },
        }
        self.assertEqual(1, owner_one.persist_source_diagnostics(diagnostic_result, now))
        self.assertEqual(1, owner_two.persist_source_diagnostics(diagnostic_result, now))
        history_one = owner_one.list_source_diagnostics()
        self.assertEqual(1, history_one["count"])
        self.assertEqual("greenhouse", history_one["items"][0]["provider"])
        serialized = json.dumps(history_one, sort_keys=True)
        self.assertNotIn("must not be stored", serialized)
        self.assertNotIn("private-error", serialized)
        self.assertEqual(1, owner_one.clear_source_diagnostics())
        self.assertEqual(1, owner_two.list_source_diagnostics()["count"])

    def test_job_catalog_creation_is_suppression_aware_owner_scoped_and_atomic(self) -> None:
        owner_one = PostgreSQLDiscoveryRepository(self.user_one, engine=self.runtime_engine)
        owner_two = PostgreSQLDiscoveryRepository(self.user_two, engine=self.runtime_engine)
        checked_at = datetime(2026, 8, 12, 18, 30, tzinfo=timezone.utc)

        def values(url: str, title: str = "Catalog job") -> dict:
            return {
                "title": title,
                "company": "Synthetic Employer",
                "description": "Synthetic job description for repository integration testing.",
                "url": url,
                "match_score": None,
                "match_analysis": "Awaiting initial analysis.",
                "date_found": "2026-08-12",
                "location": "Remote",
                "work_arrangement": "remote",
                "employment_type": "full-time",
                "compensation": "$100,000",
                "source": "manual",
                "last_checked_at": checked_at,
            }

        suppressed_url = "https://catalog.example.test/suppressed"
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.job_suppressions
                        (owner_user_id, url_fingerprint, hostname, deletion_source)
                    VALUES (:owner, :fingerprint, 'catalog.example.test', 'manual')
                    """
                ),
                {
                    "owner": self.user_one,
                    "fingerprint": job_url_fingerprint(suppressed_url),
                },
            )
        self.assertEqual(
            {"outcome": "suppressed"},
            owner_one.create_job_if_available(**values(suppressed_url)),
        )
        other_created = owner_two.create_job_if_available(**values(suppressed_url))
        self.assertEqual("created", other_created["outcome"])

        shared_url = "HTTPS://catalog.example.test/shared/"
        one = owner_one.create_job_if_available(**values(shared_url, "Owner one"))
        two = owner_two.create_job_if_available(**values(shared_url, "Owner two"))
        self.assertEqual("created", one["outcome"])
        self.assertEqual("created", two["outcome"])
        self.assertNotEqual(one["job"]["id"], two["job"]["id"])
        self.assertEqual("https://catalog.example.test/shared", one["job"]["url"])
        self.assertNotIn("owner_user_id", one["job"])

        concurrent_url = "https://catalog.example.test/concurrent"
        barrier = Barrier(2)

        def create(title: str) -> dict:
            barrier.wait(timeout=5)
            return owner_one.create_job_if_available(**values(concurrent_url, title))

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, ("First contender", "Second contender")))
        self.assertCountEqual(
            ["created", "duplicate"], [result["outcome"] for result in results]
        )
        self.assertEqual(1, len({result["job"]["id"] for result in results}))
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            self.assertEqual(
                1,
                connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM careertrellis.jobs "
                        "WHERE owner_user_id = :owner AND url = :url"
                    ),
                    {
                        "owner": self.user_one,
                        "url": "https://catalog.example.test/concurrent",
                    },
                ),
            )

    def test_initial_job_score_completion_is_owner_scoped_compare_and_set(self) -> None:
        owner_one = PostgreSQLDiscoveryRepository(self.user_one, engine=self.runtime_engine)
        owner_two = PostgreSQLDiscoveryRepository(self.user_two, engine=self.runtime_engine)
        created = owner_one.create_job_if_available(
            title="Unscored role",
            company="Synthetic Employer",
            description="Synthetic job description for score completion testing.",
            url="https://catalog.example.test/unscored",
            match_score=None,
            match_analysis="Awaiting initial analysis.",
            date_found=date(2026, 8, 12),
            location="Remote",
            work_arrangement="remote",
            employment_type="full-time",
            compensation="",
            source="manual",
            last_checked_at=datetime(2026, 8, 12, 19, 0, tzinfo=timezone.utc),
        )
        job_id = created["job"]["id"]
        self.assertIsNone(owner_two.complete_initial_job_match(job_id, 99, "Foreign score"))

        barrier = Barrier(2)

        def complete(arguments: tuple[int, str]) -> dict | None:
            barrier.wait(timeout=5)
            return owner_one.complete_initial_job_match(job_id, *arguments)

        contenders = ((71, "First analysis"), (89, "Second analysis"))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(complete, contenders))
        accepted = [result for result in results if result is not None]
        self.assertEqual(1, len(accepted))
        self.assertNotIn("owner_user_id", accepted[0])
        self.assertIn(
            (accepted[0]["match_score"], accepted[0]["match_analysis"]), contenders
        )
        self.assertIsNone(
            owner_one.complete_initial_job_match(job_id, 100, "Stale completion")
        )
        with self.assertRaises(ValueError):
            owner_one.complete_initial_job_match(job_id, 101, "Invalid score")

        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            stored = connection.execute(
                text(
                    "SELECT match_score, match_analysis FROM careertrellis.jobs "
                    "WHERE owner_user_id = :owner AND id = :job_id"
                ),
                {"owner": self.user_one, "job_id": job_id},
            ).one()
        self.assertEqual(
            (accepted[0]["match_score"], accepted[0]["match_analysis"]), tuple(stored)
        )

    def test_discovery_repository_indexes_are_owner_first(self) -> None:
        expected = {
            "idx_jobs_owner_score_active": ("jobs", "owner_user_id"),
            "idx_saved_searches_owner_due": ("saved_searches", "owner_user_id"),
            "idx_job_suppressions_owner_deleted": ("job_suppressions", "owner_user_id"),
            "idx_source_diagnostics_owner_recorded": ("source_diagnostics", "owner_user_id"),
        }
        with self.admin_engine.begin() as connection:
            definitions = dict(
                connection.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = 'careertrellis' "
                        "AND indexname = ANY(:index_names)"
                    ),
                    {"index_names": list(expected)},
                ).all()
            )
        self.assertEqual(set(expected), set(definitions))
        for index_name, (table_name, first_column) in expected.items():
            definition = definitions[index_name]
            self.assertIn(f"ON careertrellis.{table_name}", definition)
            self.assertRegex(definition, rf"\({first_column}(?: ASC| DESC)?[,)]")
        jobs_definition = definitions["idx_jobs_owner_score_active"]
        self.assertIn("match_score DESC NULLS LAST", jobs_definition)
        self.assertIn("id DESC", jobs_definition)
        self.assertIn("WHERE (status <> 'archived'::text)", jobs_definition)

    def test_job_retention_index_is_exact_valid_and_ready(self) -> None:
        with self.admin_engine.begin() as connection:
            index = connection.execute(
                text(
                    """
                    SELECT pg_get_indexdef(i.indexrelid), i.indisvalid, i.indisready
                    FROM pg_index i
                    JOIN pg_class c ON c.oid = i.indexrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'careertrellis'
                      AND c.relname = 'idx_jobs_owner_retention_candidates'
                    """
                )
            ).one()
        self.assertTrue(index.indisvalid)
        self.assertTrue(index.indisready)
        self.assertIn("(owner_user_id, status, id)", index[0])
        self.assertIn("INCLUDE (archived_from_status)", index[0])
        self.assertIn("WHERE (status = ANY (ARRAY['matched'::text, 'archived'::text]))", index[0])

    def test_retention_preview_is_owner_isolated_bound_and_progress_protected(self) -> None:
        owner_one = PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        )
        owner_two = PostgreSQLJobRetentionRepository(
            self.user_two, engine=self.runtime_engine
        )
        history_job = self._insert_job(
            self.user_one, "https://retention.example.test/history"
        )
        engagement_job = self._insert_job(
            self.user_one, "https://retention.example.test/engagement"
        )
        free_job = self._insert_job(
            self.user_one, "https://retention.example.test/free", title="Free job"
        )
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            connection.execute(
                text(
                    "INSERT INTO careertrellis.applications "
                    "(owner_user_id, job_id) VALUES (:owner, :job_id)"
                ),
                {"owner": self.user_one, "job_id": self.job_one},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.application_status_history
                        (owner_user_id, job_id, from_status, to_status, source)
                    VALUES (:owner, :job_id, 'matched', 'tailored', 'test')
                    """
                ),
                {"owner": self.user_one, "job_id": history_job},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.application_engagements
                        (owner_user_id, job_id, engagement_type, name)
                    VALUES (:owner, :job_id, 'recruiter', 'Synthetic contact')
                    """
                ),
                {"owner": self.user_one, "job_id": engagement_job},
            )

        preview_one = owner_one.cleanup_preview()
        preview_two = owner_two.cleanup_preview()
        self.assertEqual(1, preview_one["actions"]["delete"]["count"])
        self.assertEqual(3, preview_one["protected_count"])
        self.assertEqual([free_job], [row["id"] for row in preview_one["sample"]])
        self.assertEqual(1, preview_two["actions"]["delete"]["count"])
        self.assertNotEqual(
            preview_one["actions"]["delete"]["preview_token"],
            preview_two["actions"]["delete"]["preview_token"],
        )
        self.assertNotIn("owner_user_id", json.dumps(preview_one, sort_keys=True))

        with self.assertRaises(CleanupPreviewChanged):
            owner_two.apply_cleanup(
                "delete", preview_one["actions"]["delete"]["preview_token"]
            )
        with self.assertRaises(CleanupPreviewChanged):
            owner_one.apply_cleanup(
                "archive", preview_one["actions"]["delete"]["preview_token"]
            )
        self._insert_job(self.user_one, "https://retention.example.test/stale")
        with self.assertRaises(CleanupPreviewChanged):
            owner_one.apply_cleanup(
                "delete", preview_one["actions"]["delete"]["preview_token"]
            )

        for protected_job in (self.job_one, history_job, engagement_job):
            with self.subTest(job_id=protected_job):
                with self.assertRaises(JobRetentionProtected):
                    owner_one.delete_job(protected_job)
        with self.assertRaises(JobRetentionNotFound):
            owner_two.delete_job(self.job_one)
        with self.assertRaises(JobRetentionNotFound):
            owner_one.delete_job(999_999_999)
        with self.runtime_engine.begin() as connection:
            self.assertEqual(
                0,
                connection.execute(
                    text("DELETE FROM careertrellis.jobs WHERE id = :job_id"),
                    {"job_id": free_job},
                ).rowcount,
            )

    def test_retention_archive_restore_and_deletes_are_atomic_and_owner_scoped(self) -> None:
        owner_one = PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        )
        owner_two = PostgreSQLJobRetentionRepository(
            self.user_two, engine=self.runtime_engine
        )
        second_job = self._insert_job(
            self.user_one, "https://retention.example.test/second"
        )

        preview = owner_one.cleanup_preview()
        self.assertEqual(2, owner_one.apply_cleanup(
            "archive", preview["actions"]["archive"]["preview_token"]
        ))
        preview = owner_one.cleanup_preview()
        self.assertEqual(2, owner_one.apply_cleanup(
            "restore", preview["actions"]["restore"]["preview_token"]
        ))
        preview = owner_one.cleanup_preview()
        self.assertEqual(2, owner_one.apply_cleanup(
            "delete", preview["actions"]["delete"]["preview_token"]
        ))
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            self.assertEqual(0, connection.scalar(text(
                "SELECT COUNT(*) FROM careertrellis.jobs"
            )))
            suppressions = connection.execute(text(
                "SELECT deletion_source, hostname FROM careertrellis.job_suppressions "
                "ORDER BY id"
            )).all()
        self.assertEqual(2, len(suppressions))
        self.assertTrue(all(row.deletion_source == "bulk_cleanup" for row in suppressions))
        self.assertTrue(all(row.hostname in {"example.test", "retention.example.test"} for row in suppressions))
        self.assertEqual(1, owner_two.cleanup_preview()["actions"]["delete"]["count"])

        manual_job = self._insert_job(
            self.user_one, "https://retention.example.test/manual-secret-path"
        )
        owner_one.delete_job(manual_job)
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            manual = connection.execute(text(
                "SELECT * FROM careertrellis.job_suppressions "
                "WHERE deletion_source = 'manual'"
            )).mappings().one()
        self.assertEqual("retention.example.test", manual["hostname"])
        self.assertNotIn("manual-secret-path", " ".join(str(value) for value in manual.values()))
        self.assertNotIn("owner_user_id", owner_two.cleanup_preview()["sample"][0])

    def test_retention_delete_rolls_back_suppression_on_injected_failure(self) -> None:
        repository = PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        )
        original = repository._upsert_suppression

        def fail_after_suppression(*args, **kwargs) -> None:
            original(*args, **kwargs)
            raise RuntimeError("synthetic post-suppression failure")

        with patch.object(repository, "_upsert_suppression", side_effect=fail_after_suppression):
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                repository.delete_job(self.job_one)
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            self.assertEqual(1, connection.scalar(text(
                "SELECT COUNT(*) FROM careertrellis.jobs WHERE id = :job_id"
            ), {"job_id": self.job_one}))
            self.assertEqual(0, connection.scalar(text(
                "SELECT COUNT(*) FROM careertrellis.job_suppressions"
            )))

    def test_retention_and_discovery_share_catalog_lock(self) -> None:
        retention = PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        )
        discovery = PostgreSQLDiscoveryRepository(
            self.user_one, engine=self.runtime_engine
        )
        url = "https://retention.example.test/concurrent"
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            connection.execute(
                text("UPDATE careertrellis.jobs SET url = :url WHERE id = :job_id"),
                {"url": url, "job_id": self.job_one},
            )
        barrier = Barrier(2)

        def delete() -> None:
            barrier.wait(timeout=5)
            retention.delete_job(self.job_one)

        def create() -> dict:
            barrier.wait(timeout=5)
            return discovery.create_job_if_available(
                title="Concurrent recreation",
                company="Synthetic Employer",
                description="Synthetic description",
                url=url,
                match_score=None,
                match_analysis=None,
                date_found=date(2026, 8, 13),
                location="Remote",
                work_arrangement="remote",
                employment_type="full-time",
                compensation="",
                source="manual",
                last_checked_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            delete_future = executor.submit(delete)
            create_future = executor.submit(create)
            delete_future.result(timeout=10)
            created = create_future.result(timeout=10)
        self.assertIn(created["outcome"], {"duplicate", "suppressed"})
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            self.assertEqual(0, connection.scalar(text(
                "SELECT COUNT(*) FROM careertrellis.jobs WHERE url = :url"
            ), {"url": url}))
            self.assertEqual(1, connection.scalar(text(
                "SELECT COUNT(*) FROM careertrellis.job_suppressions "
                "WHERE url_fingerprint = :fingerprint"
            ), {"fingerprint": job_url_fingerprint(url)}))

    def test_retention_batch_cap_fails_before_mutation(self) -> None:
        repository = PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        )
        with self.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.jobs (owner_user_id, title, url)
                    SELECT :owner, 'Batch ' || value,
                           'https://batch.example.test/' || value
                    FROM generate_series(1, :count) AS value
                    """
                ),
                {"owner": self.user_one, "count": MAX_RETENTION_BATCH},
            )
        preview = repository.cleanup_preview()
        self.assertEqual(
            MAX_RETENTION_BATCH + 1,
            preview["actions"]["archive"]["count"],
        )
        with self.assertRaises(CleanupBatchTooLarge):
            repository.apply_cleanup(
                "archive", preview["actions"]["archive"]["preview_token"]
            )
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            self.assertEqual(
                MAX_RETENTION_BATCH + 1,
                connection.scalar(text(
                    "SELECT COUNT(*) FROM careertrellis.jobs WHERE status = 'matched'"
                )),
            )

    @staticmethod
    def _engagement_values(name: str = "Synthetic recruiter") -> dict[str, object]:
        return {
            "engagement_type": "recruiter",
            "name": name,
            "organization": "Synthetic Employer",
            "contact_details": "synthetic@example.test",
            "status": "planned",
            "activity_on": "2026-08-13",
            "next_action_on": "2026-08-20",
            "notes": "Synthetic engagement notes.",
        }

    def test_application_workspace_engagement_crud_is_owner_scoped(self) -> None:
        owner_one = PostgreSQLApplicationWorkspaceRepository(
            self.user_one, engine=self.runtime_engine
        )
        owner_two = PostgreSQLApplicationWorkspaceRepository(
            self.user_two, engine=self.runtime_engine
        )
        with self.assertRaises(ApplicationWorkspaceNotFound):
            owner_two.list_engagements(self.job_one)
        with self.assertRaises(ApplicationWorkspaceNotFound):
            owner_two.create_engagement(
                self.job_one, self._engagement_values("Cross-tenant create")
            )

        created = owner_one.create_engagement(
            self.job_one, self._engagement_values()
        )
        self.assertNotIn("owner_user_id", created)
        self.assertEqual("Synthetic recruiter", created["name"])
        listed = owner_one.list_engagements(self.job_one)
        self.assertEqual(1, listed["count"])
        self.assertEqual(created["id"], listed["engagements"][0]["id"])
        self.assertNotIn("owner_user_id", json.dumps(listed, sort_keys=True))

        other_owned_job = self._insert_job(
            self.user_one, "https://workspace.example.test/other"
        )
        with self.assertRaises(ApplicationWorkspaceNotFound):
            owner_one.update_engagement(
                other_owned_job,
                created["id"],
                self._engagement_values("Wrong job"),
            )
        with self.assertRaises(ApplicationWorkspaceNotFound):
            owner_two.update_engagement(
                self.job_two,
                created["id"],
                self._engagement_values("Wrong owner"),
            )
        updated = owner_one.update_engagement(
            self.job_one,
            created["id"],
            {**self._engagement_values("Updated recruiter"), "status": "contacted"},
        )
        self.assertEqual("Updated recruiter", updated["name"])
        self.assertEqual("contacted", updated["status"])
        owner_one.delete_engagement(self.job_one, created["id"])
        with self.assertRaises(ApplicationWorkspaceNotFound):
            owner_one.delete_engagement(self.job_one, created["id"])
        with self.runtime_engine.begin() as connection:
            self.assertEqual(
                0,
                connection.scalar(
                    text("SELECT COUNT(*) FROM careertrellis.application_engagements")
                ),
            )

    def test_application_workspace_serializes_limit_update_delete_and_retention(self) -> None:
        workspace = PostgreSQLApplicationWorkspaceRepository(
            self.user_one, engine=self.runtime_engine
        )
        with self.admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.application_engagements (
                        owner_user_id, job_id, engagement_type, name
                    )
                    SELECT :owner, :job_id, 'recruiter', 'Synthetic ' || value
                    FROM generate_series(1, :count) AS value
                    """
                ),
                {
                    "owner": self.user_one,
                    "job_id": self.job_one,
                    "count": MAX_ENGAGEMENTS_PER_JOB - 1,
                },
            )
        barrier = Barrier(2)

        def create(name: str) -> dict:
            barrier.wait(timeout=5)
            return workspace.create_engagement(
                self.job_one, self._engagement_values(name)
            )

        outcomes: list[object] = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(create, name) for name in ("Limit A", "Limit B")]
            for future in futures:
                try:
                    outcomes.append(future.result(timeout=10))
                except Exception as exc:
                    outcomes.append(exc)
        self.assertEqual(1, sum(isinstance(item, dict) for item in outcomes))
        self.assertEqual(1, sum(isinstance(item, EngagementLimitError) for item in outcomes))
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            self.assertEqual(
                MAX_ENGAGEMENTS_PER_JOB,
                connection.scalar(text(
                    "SELECT COUNT(*) FROM careertrellis.application_engagements "
                    "WHERE job_id = :job_id"
                ), {"job_id": self.job_one}),
            )

        race_job = self._insert_job(
            self.user_one, "https://workspace.example.test/update-delete"
        )
        engagement = workspace.create_engagement(
            race_job, self._engagement_values("Race")
        )
        barrier = Barrier(2)

        def update() -> None:
            barrier.wait(timeout=5)
            workspace.update_engagement(
                race_job, engagement["id"], self._engagement_values("Updated")
            )

        def delete() -> None:
            barrier.wait(timeout=5)
            workspace.delete_engagement(race_job, engagement["id"])

        race_outcomes: list[object] = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(update), executor.submit(delete)]
            for future in futures:
                try:
                    future.result(timeout=10)
                    race_outcomes.append("ok")
                except Exception as exc:
                    race_outcomes.append(exc)
        self.assertIn("ok", race_outcomes)
        self.assertTrue(all(
            item == "ok" or isinstance(item, ApplicationWorkspaceNotFound)
            for item in race_outcomes
        ))
        self.assertEqual(0, workspace.list_engagements(race_job)["count"])

        retention_job = self._insert_job(
            self.user_one, "https://workspace.example.test/retention-race"
        )
        retention = PostgreSQLJobRetentionRepository(
            self.user_one, engine=self.runtime_engine
        )
        barrier = Barrier(2)

        def retain() -> None:
            barrier.wait(timeout=5)
            retention.delete_job(retention_job)

        def engage() -> None:
            barrier.wait(timeout=5)
            workspace.create_engagement(
                retention_job, self._engagement_values("Retention race")
            )

        retention_outcomes: list[object] = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(retain), executor.submit(engage)]
            for future in futures:
                try:
                    future.result(timeout=10)
                    retention_outcomes.append("ok")
                except Exception as exc:
                    retention_outcomes.append(exc)
        self.assertEqual(1, sum(item == "ok" for item in retention_outcomes))
        self.assertTrue(any(isinstance(
            item, (JobRetentionProtected, ApplicationWorkspaceNotFound)
        ) for item in retention_outcomes))
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            job_count = connection.scalar(text(
                "SELECT COUNT(*) FROM careertrellis.jobs WHERE id = :job_id"
            ), {"job_id": retention_job})
            engagement_count = connection.scalar(text(
                "SELECT COUNT(*) FROM careertrellis.application_engagements "
                "WHERE job_id = :job_id"
            ), {"job_id": retention_job})
            suppression_count = connection.scalar(text(
                "SELECT COUNT(*) FROM careertrellis.job_suppressions "
                "WHERE url_fingerprint = :fingerprint"
            ), {"fingerprint": job_url_fingerprint(
                "https://workspace.example.test/retention-race"
            )})
        self.assertIn(
            (job_count, engagement_count, suppression_count),
            {(1, 1, 0), (0, 0, 1)},
        )

    def test_application_workspace_context_token_is_owner_bound_and_complete(self) -> None:
        owner_one = PostgreSQLApplicationWorkspaceRepository(
            self.user_one, engine=self.runtime_engine
        )
        owner_two = PostgreSQLApplicationWorkspaceRepository(
            self.user_two, engine=self.runtime_engine
        )
        PostgreSQLLifecycleRepository(
            self.user_one, engine=self.runtime_engine
        ).update_lifecycle(self.job_one, "tailored", notes="Initial notes")
        PostgreSQLLifecycleRepository(
            self.user_two, engine=self.runtime_engine
        ).update_lifecycle(self.job_two, "tailored", notes="Other notes")

        first = owner_one.get_interview_workspace(
            self.job_one, for_generation=True
        )
        other = owner_two.get_interview_workspace(
            self.job_two, for_generation=True
        )
        self.assertNotEqual(first["context_token"], other["context_token"])
        self.assertNotIn("owner_user_id", json.dumps(first, sort_keys=True))
        with self.assertRaises(ApplicationWorkspaceNotFound):
            owner_two.get_interview_workspace(self.job_one, for_generation=True)
        with self.assertRaises(ApplicationWorkspaceChanged):
            owner_one.save_interview_prep(
                self.job_one,
                "Wrong token content",
                expected_context_token=other["context_token"],
            )

        changes = (
            ("jobs", "title", "Changed title"),
            ("jobs", "description", "Changed description"),
            ("jobs", "match_analysis", "Changed analysis"),
            ("applications", "company", "Changed company"),
            ("applications", "position", "Changed position"),
            ("applications", "status", "interview"),
            ("applications", "notes", "Changed notes"),
            ("applications", "tailored_resume_text", "Changed resume"),
        )
        for table, column, value in changes:
            with self.subTest(table=table, column=column):
                context = owner_one.get_interview_workspace(
                    self.job_one, for_generation=True
                )
                with tenant_transaction(self.runtime_engine, self.user_one) as connection:
                    connection.execute(
                        text(
                            f"UPDATE careertrellis.{table} SET {column} = :value "
                            "WHERE job_id = :job_id AND owner_user_id = :owner"
                            if table == "applications" else
                            f"UPDATE careertrellis.{table} SET {column} = :value "
                            "WHERE id = :job_id AND owner_user_id = :owner"
                        ),
                        {"value": value, "job_id": self.job_one, "owner": self.user_one},
                    )
                with self.assertRaises(ApplicationWorkspaceChanged):
                    owner_one.save_interview_prep(
                        self.job_one,
                        f"Stale after {column}",
                        expected_context_token=context["context_token"],
                    )

        context = owner_one.get_interview_workspace(self.job_one, for_generation=True)
        owner_one.save_interview_prep(self.job_one, "Manual newer preparation")
        with self.assertRaises(ApplicationWorkspaceChanged):
            owner_one.save_interview_prep(
                self.job_one,
                "Generated stale preparation",
                expected_context_token=context["context_token"],
            )
        current = owner_one.get_interview_workspace(
            self.job_one, for_generation=True
        )
        saved = owner_one.save_interview_prep(
            self.job_one,
            "Generated current preparation",
            expected_context_token=current["context_token"],
        )
        self.assertEqual("Generated current preparation", saved["content"])

    def test_application_workspace_create_rolls_back_after_return_failure(self) -> None:
        repository = PostgreSQLApplicationWorkspaceRepository(
            self.user_one, engine=self.runtime_engine
        )
        with patch(
            "application_workspace_repository._api_row",
            side_effect=RuntimeError("synthetic result failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic result failure"):
                repository.create_engagement(
                    self.job_one, self._engagement_values("Rollback")
                )
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            self.assertEqual(
                0,
                connection.scalar(text(
                    "SELECT COUNT(*) FROM careertrellis.application_engagements "
                    "WHERE job_id = :job_id"
                ), {"job_id": self.job_one}),
            )

    def test_saved_search_writes_are_owner_scoped_monotonic_and_constrained(self) -> None:
        owner_one = PostgreSQLDiscoveryRepository(self.user_one, engine=self.runtime_engine)
        owner_two = PostgreSQLDiscoveryRepository(self.user_two, engine=self.runtime_engine)
        first_run = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
        one = owner_one.upsert_saved_search(
            name="Owner one",
            keywords="shared query",
            location="Remote",
            schedule_frequency="daily",
            run_at=first_run,
        )
        two = owner_two.upsert_saved_search(
            name="Owner two",
            keywords="shared query",
            location="Remote",
            schedule_frequency="weekly",
            run_at=first_run,
        )
        self.assertNotEqual(one["id"], two["id"])
        self.assertNotIn("owner_user_id", one)
        self.assertEqual((first_run + timedelta(days=1)).isoformat(), one["next_alert_at"])

        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            connection.execute(
                text(
                    "UPDATE careertrellis.saved_searches SET enabled = FALSE "
                    "WHERE id = :search_id"
                ),
                {"search_id": one["id"]},
            )
        later_run = first_run + timedelta(hours=1)
        reenlisted = owner_one.upsert_saved_search(
            name="Renamed",
            keywords="shared query",
            location="Remote",
            schedule_frequency="none",
            run_at=later_run,
        )
        self.assertEqual(one["id"], reenlisted["id"])
        self.assertEqual(one["created_at"], reenlisted["created_at"])
        self.assertEqual(1, reenlisted["enabled"])
        self.assertIsNone(reenlisted["next_alert_at"])

        self.assertIsNone(owner_one.advance_saved_search_run(two["id"], later_run))
        with self.assertRaises(ValueError):
            owner_one.advance_saved_search_run(one["id"], datetime(2026, 8, 12, 20, 0))
        with tenant_transaction(self.runtime_engine, self.user_two) as connection:
            unchanged = connection.execute(
                text(
                    "SELECT last_run_at, next_alert_at FROM careertrellis.saved_searches "
                    "WHERE id = :search_id"
                ),
                {"search_id": two["id"]},
            ).one()
        self.assertEqual(first_run, unchanged.last_run_at)
        self.assertEqual(first_run + timedelta(days=7), unchanged.next_alert_at)

        with self.admin_engine.begin() as connection:
            validated = connection.scalar(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conname = 'ck_saved_searches_schedule_frequency'"
                )
            )
        self.assertTrue(validated)
        with self.assertRaises(DBAPIError):
            with tenant_transaction(self.runtime_engine, self.user_one) as connection:
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.saved_searches "
                        "(owner_user_id, name, keywords, location, schedule_frequency) "
                        "VALUES (:owner, 'Invalid', 'invalid', '', 'monthly')"
                    ),
                    {"owner": self.user_one},
                )

    def test_concurrent_saved_search_writes_keep_one_monotonic_owner_row(self) -> None:
        owner_one = PostgreSQLDiscoveryRepository(self.user_one, engine=self.runtime_engine)
        older = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
        newer = older + timedelta(hours=2)
        barrier = Barrier(2)

        def upsert(name: str, frequency: str, run_at: datetime) -> dict:
            barrier.wait(timeout=5)
            return owner_one.upsert_saved_search(
                name=name,
                keywords="concurrent",
                location="Remote",
                schedule_frequency=frequency,
                run_at=run_at,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda arguments: upsert(*arguments),
                    (("Older", "daily", older), ("Newer", "weekly", newer)),
                )
            )
        self.assertEqual(1, len({result["id"] for result in results}))
        owner_one.upsert_saved_search(
            name="Stale retry",
            keywords="concurrent",
            location="Remote",
            schedule_frequency="none",
            run_at=older,
        )
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            rows = connection.execute(
                text(
                    "SELECT name, schedule_frequency, last_run_at, next_alert_at "
                    "FROM careertrellis.saved_searches "
                    "WHERE keywords = 'concurrent' AND location = 'Remote'"
                )
            ).all()
        self.assertEqual(1, len(rows))
        self.assertEqual("Newer", rows[0].name)
        self.assertEqual("weekly", rows[0].schedule_frequency)
        self.assertEqual(newer, rows[0].last_run_at)
        self.assertEqual(newer + timedelta(days=7), rows[0].next_alert_at)

    def test_composite_foreign_keys_prevent_cross_tenant_relationships(self) -> None:
        with self.assertRaises(DBAPIError):
            with tenant_transaction(self.runtime_engine, self.user_one) as connection:
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.applications "
                        "(owner_user_id, job_id, company, position) "
                        "VALUES (:owner, :other_job, 'Other', 'Forbidden')"
                    ),
                    {"owner": self.user_one, "other_job": self.job_two},
                )

    def test_lifecycle_writes_are_owner_scoped_atomic_reversible_and_indexed(self) -> None:
        owner_one = PostgreSQLLifecycleRepository(self.user_one, engine=self.runtime_engine)
        owner_two = PostgreSQLLifecycleRepository(self.user_two, engine=self.runtime_engine)
        applied_on = date(2026, 8, 13)
        applied = owner_one.update_lifecycle(
            self.job_one,
            "applied",
            applied_on=applied_on,
            method="referral",
            notes="Synthetic referral",
            follow_up_on=date(2026, 8, 20),
        )
        self.assertEqual("matched", applied["previous_status"])
        self.assertEqual("applied", applied["status"])
        self.assertEqual(applied_on.isoformat(), applied["date_applied"])
        self.assertNotIn("owner_user_id", applied)

        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            job_status = connection.scalar(
                text("SELECT status FROM careertrellis.jobs WHERE id = :job_id"),
                {"job_id": self.job_one},
            )
            application = connection.execute(
                text(
                    "SELECT status, date_applied, application_method, submission_evidence, "
                    "notes, follow_up_date FROM careertrellis.applications "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": self.job_one},
            ).mappings().one()
            history = connection.execute(
                text(
                    "SELECT from_status, to_status, source, undone_at "
                    "FROM careertrellis.application_status_history "
                    "WHERE job_id = :job_id ORDER BY id"
                ),
                {"job_id": self.job_one},
            ).mappings().all()
        self.assertEqual("applied", job_status)
        self.assertEqual("applied", application["status"])
        self.assertEqual(applied_on, application["date_applied"])
        self.assertEqual("manual:referral", application["application_method"])
        self.assertTrue(json.loads(application["submission_evidence"])["confirmed_by_user"])
        self.assertEqual("Synthetic referral", application["notes"])
        self.assertEqual(date(2026, 8, 20), application["follow_up_date"])
        self.assertEqual(
            [{"from_status": "matched", "to_status": "applied", "source": "manual", "undone_at": None}],
            [dict(row) for row in history],
        )

        with self.assertRaisesRegex(LookupError, "Job not found"):
            owner_two.update_lifecycle(self.job_one, "interview")
        with self.assertRaisesRegex(LookupError, "Job not found"):
            owner_two.undo_latest_lifecycle_change(self.job_one)

        undone = owner_one.undo_latest_lifecycle_change(self.job_one)
        self.assertEqual("matched", undone["status"])
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            self.assertEqual(
                "matched",
                connection.scalar(
                    text("SELECT status FROM careertrellis.jobs WHERE id = :job_id"),
                    {"job_id": self.job_one},
                ),
            )
            self.assertEqual(
                0,
                connection.scalar(
                    text("SELECT count(*) FROM careertrellis.applications WHERE job_id = :job_id"),
                    {"job_id": self.job_one},
                ),
            )
            self.assertIsNotNone(
                connection.scalar(
                    text(
                        "SELECT undone_at FROM careertrellis.application_status_history "
                        "WHERE job_id = :job_id"
                    ),
                    {"job_id": self.job_one},
                )
            )

        with self.admin_engine.begin() as connection:
            index = connection.execute(
                text(
                    "SELECT pg_get_indexdef(i.indexrelid), i.indisvalid, i.indisready "
                    "FROM pg_index AS i "
                    "JOIN pg_class AS c ON c.oid = i.indexrelid "
                    "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'careertrellis' "
                    "AND c.relname = 'idx_application_status_history_owner_job_active'"
                )
            ).one()
        self.assertIn("(owner_user_id, job_id, id DESC)", index[0])
        self.assertIn("WHERE (undone_at IS NULL)", index[0])
        self.assertTrue(index.indisvalid)
        self.assertTrue(index.indisready)

    def test_concurrent_lifecycle_writes_serialize_one_consistent_history(self) -> None:
        repository = PostgreSQLLifecycleRepository(self.user_one, engine=self.runtime_engine)
        barrier = Barrier(2)

        def update(status: str) -> dict:
            barrier.wait(timeout=5)
            return repository.update_lifecycle(self.job_one, status)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, ("applied", "interview")))
        self.assertEqual({"applied", "interview"}, {result["status"] for result in results})
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            job_status = connection.scalar(
                text("SELECT status FROM careertrellis.jobs WHERE id = :job_id"),
                {"job_id": self.job_one},
            )
            application_status = connection.scalar(
                text("SELECT status FROM careertrellis.applications WHERE job_id = :job_id"),
                {"job_id": self.job_one},
            )
            history = connection.execute(
                text(
                    "SELECT from_status, to_status FROM careertrellis.application_status_history "
                    "WHERE job_id = :job_id ORDER BY id"
                ),
                {"job_id": self.job_one},
            ).all()
        self.assertEqual(job_status, application_status)
        self.assertEqual(2, len(history))
        self.assertEqual("matched", history[0].from_status)
        self.assertEqual(history[0].to_status, history[1].from_status)
        self.assertEqual(job_status, history[1].to_status)

        reverted = repository.undo_latest_lifecycle_change(self.job_one)
        self.assertEqual(history[1].from_status, reverted["status"])

    def test_verification_writes_are_owner_scoped_and_close_pre_application_state(self) -> None:
        owner_one = PostgreSQLVerificationRepository(self.user_one, engine=self.runtime_engine)
        owner_two = PostgreSQLVerificationRepository(self.user_two, engine=self.runtime_engine)
        checked_at = datetime(2026, 8, 13, 15, 0, tzinfo=timezone.utc)
        self.assertIsNone(owner_two.get_verification_target(self.job_one))
        self.assertIsNone(
            owner_two.apply_verification(
                self.job_one,
                expected_url="https://example.test/one",
                details=None,
                checked_at=checked_at,
            )
        )

        active = owner_one.apply_verification(
            self.job_one,
            expected_url="https://example.test/one",
            details={
                "title": "Verified One Job",
                "company": "Tenant One Company",
                "description": "A complete verified description",
                "location": "Remote",
                "work_arrangement": "remote",
                "employment_type": "full_time",
                "compensation": "$150,000",
                "source": "employer",
            },
            checked_at=checked_at,
        )
        self.assertFalse(active["expired"])
        self.assertNotIn("owner_user_id", active)
        PostgreSQLLifecycleRepository(
            self.user_one, engine=self.runtime_engine
        ).update_lifecycle(self.job_one, "tailored")

        stale = owner_one.apply_verification(
            self.job_one,
            expected_url="https://example.test/one",
            details=None,
            checked_at=checked_at + timedelta(hours=1),
        )
        self.assertTrue(stale["expired"])
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            job = connection.execute(
                text(
                    "SELECT title, status, is_expired, expiration_reason "
                    "FROM careertrellis.jobs WHERE id = :job_id"
                ),
                {"job_id": self.job_one},
            ).mappings().one()
            application_status = connection.scalar(
                text(
                    "SELECT status FROM careertrellis.applications "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": self.job_one},
            )
        with tenant_transaction(self.runtime_engine, self.user_two) as connection:
            other_job = connection.execute(
                text(
                    "SELECT status, is_expired FROM careertrellis.jobs "
                    "WHERE id = :job_id"
                ),
                {"job_id": self.job_two},
            ).one()
        self.assertEqual("Verified One Job", job["title"])
        self.assertEqual("closed", job["status"])
        self.assertTrue(job["is_expired"])
        self.assertEqual("Source reports posting is no longer published", job["expiration_reason"])
        self.assertEqual("closed", application_status)
        self.assertEqual("matched", other_job.status)
        self.assertFalse(other_job.is_expired)

    def test_application_reads_and_insights_are_owner_scoped_and_minimized(self) -> None:
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            connection.execute(
                text(
                    """
                    UPDATE careertrellis.jobs
                    SET company = 'Tenant One Company', source = 'lever',
                        location = 'Remote'
                    WHERE id = :job_id AND owner_user_id = :owner_user_id
                    """
                ),
                {"job_id": self.job_one, "owner_user_id": self.user_one},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.applications (
                        owner_user_id, job_id, company, position, date_applied,
                        tailored_resume_text, cover_letter, status, confirmed_at,
                        application_method, submission_evidence, notes,
                        base_resume_name, base_resume_version, interview_prep,
                        interview_prep_updated_at
                    ) VALUES (
                        :owner_user_id, :job_id, 'Tenant One Company',
                        'One Job', DATE '2026-08-10', 'private resume source',
                        'private cover letter', 'interview',
                        TIMESTAMPTZ '2026-08-10 09:00:00+00',
                        'manual:referral', 'private evidence', 'private notes',
                        'Executive', 2, '# Private prep',
                        TIMESTAMPTZ '2026-08-11 10:00:00+00'
                    )
                    """
                ),
                {"owner_user_id": self.user_one, "job_id": self.job_one},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.application_status_history (
                        owner_user_id, job_id, from_status, to_status,
                        changed_at, source
                    ) VALUES (
                        :owner_user_id, :job_id, 'applied', 'interview',
                        TIMESTAMPTZ '2026-08-12 09:00:00+00', 'manual'
                    )
                    """
                ),
                {"owner_user_id": self.user_one, "job_id": self.job_one},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.application_engagements (
                        owner_user_id, job_id, engagement_type, name
                    ) VALUES (
                        :owner_user_id, :job_id, 'recruiter', 'Private Person'
                    )
                    """
                ),
                {"owner_user_id": self.user_one, "job_id": self.job_one},
            )
        with tenant_transaction(self.runtime_engine, self.user_two) as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.applications (
                        owner_user_id, job_id, company, position, date_applied,
                        status, confirmed_at, notes
                    ) VALUES (
                        :owner_user_id, :job_id, 'Tenant Two Company',
                        'Two Job', DATE '2026-08-09', 'rejected',
                        TIMESTAMPTZ '2026-08-09 09:00:00+00',
                        'other tenant private notes'
                    )
                    """
                ),
                {"owner_user_id": self.user_two, "job_id": self.job_two},
            )

        with self.runtime_engine.begin() as connection:
            for table in (
                "applications",
                "application_status_history",
                "application_engagements",
            ):
                with self.subTest(table=table):
                    self.assertEqual(
                        0,
                        connection.scalar(
                            text(f"SELECT COUNT(*) FROM careertrellis.{table}")
                        ),
                    )

        owner_one = PostgreSQLApplicationReadRepository(
            self.user_one, engine=self.runtime_engine
        )
        owner_two = PostgreSQLApplicationReadRepository(
            self.user_two, engine=self.runtime_engine
        )
        one_records = owner_one.list_applications()
        two_records = owner_two.list_applications()

        self.assertEqual([self.job_one], [row["job_id"] for row in one_records])
        self.assertEqual([self.job_two], [row["job_id"] for row in two_records])
        self.assertEqual(1, one_records[0]["engagement_count"])
        self.assertTrue(one_records[0]["interview_prep_available"])
        self.assertFalse(one_records[0]["resume_download_available"])
        self.assertFalse(one_records[0]["cover_letter_download_available"])
        for private_field in (
            "owner_user_id",
            "tailored_resume_text",
            "cover_letter",
            "interview_prep",
            "tailored_resume_object_id",
            "cover_letter_object_id",
            "submission_evidence",
            "notes",
        ):
            self.assertNotIn(private_field, one_records[0])
        self.assertNotIn("Tenant Two", json.dumps(one_records))

        one_insights = owner_one.application_insights()
        two_insights = owner_two.application_insights()
        self.assertEqual(1, one_insights["summary"]["applications"])
        self.assertEqual(1, one_insights["summary"]["interviews"])
        self.assertEqual(0, one_insights["summary"]["rejections"])
        self.assertEqual(1, two_insights["summary"]["rejections"])

    def test_verification_rejects_a_changed_target_and_serializes_with_lifecycle(self) -> None:
        verification = PostgreSQLVerificationRepository(
            self.user_one, engine=self.runtime_engine
        )
        target = verification.get_verification_target(self.job_one)
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            connection.execute(
                text("UPDATE careertrellis.jobs SET url = :url WHERE id = :job_id"),
                {"url": "https://example.test/one-replaced", "job_id": self.job_one},
            )
        with self.assertRaises(VerificationTargetChanged):
            verification.apply_verification(
                self.job_one,
                expected_url=target["url"],
                details=None,
                checked_at=datetime(2026, 8, 13, 15, 0, tzinfo=timezone.utc),
            )
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            unchanged = connection.execute(
                text(
                    "SELECT status, is_expired, last_checked_at "
                    "FROM careertrellis.jobs WHERE id = :job_id"
                ),
                {"job_id": self.job_one},
            ).one()
        self.assertEqual("matched", unchanged.status)
        self.assertFalse(unchanged.is_expired)
        self.assertIsNone(unchanged.last_checked_at)

        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            connection.execute(
                text("UPDATE careertrellis.jobs SET url = :url WHERE id = :job_id"),
                {"url": target["url"], "job_id": self.job_one},
            )
        lifecycle = PostgreSQLLifecycleRepository(self.user_one, engine=self.runtime_engine)
        barrier = Barrier(2)

        def mark_stale() -> dict:
            barrier.wait(timeout=5)
            return verification.apply_verification(
                self.job_one,
                expected_url=target["url"],
                details=None,
                checked_at=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
            )

        def mark_applied() -> dict:
            barrier.wait(timeout=5)
            return lifecycle.update_lifecycle(self.job_one, "applied")

        with ThreadPoolExecutor(max_workers=2) as executor:
            stale_future = executor.submit(mark_stale)
            applied_future = executor.submit(mark_applied)
            stale_future.result(timeout=10)
            applied_future.result(timeout=10)
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            job = connection.execute(
                text(
                    "SELECT status, is_expired FROM careertrellis.jobs "
                    "WHERE id = :job_id"
                ),
                {"job_id": self.job_one},
            ).one()
            application_status = connection.scalar(
                text(
                    "SELECT status FROM careertrellis.applications "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": self.job_one},
            )
        self.assertEqual("applied", job.status)
        self.assertTrue(job.is_expired)
        self.assertEqual("applied", application_status)

    def test_context_is_transaction_local_and_cleared_before_pool_reuse(self) -> None:
        with tenant_transaction(self.runtime_engine, self.user_one) as connection:
            self.assertEqual(
                str(self.user_one),
                connection.scalar(
                    text("SELECT current_setting('careertrellis.user_id', true)")
                ),
            )
        with self.runtime_engine.begin() as connection:
            self.assertIn(
                connection.scalar(
                    text("SELECT current_setting('careertrellis.user_id', true)")
                ),
                (None, ""),
            )
            self.assertEqual(0, connection.scalar(text("SELECT count(*) FROM careertrellis.jobs")))

    def test_provider_secrets_are_encrypted_isolated_revocable_and_rotatable(self) -> None:
        with (
            patch.dict(os.environ, _provider_secret_environment(), clear=False),
            patch.object(
                provider_secrets_module,
                "runtime_postgres_engine",
                return_value=self.runtime_engine,
            ),
        ):
            provider_secrets_module.save_provider_secret(
                self.user_one,
                "gemini",
                "synthetic-provider-secret",
            )
            self.assertEqual(
                "synthetic-provider-secret",
                provider_secrets_module.load_provider_secret(self.user_one, "gemini"),
            )
            self.assertEqual(
                "",
                provider_secrets_module.load_provider_secret(self.user_two, "gemini"),
            )
            with self.admin_engine.begin() as connection:
                stored = connection.execute(
                    text(
                        "SELECT key_id, ciphertext FROM careertrellis.provider_secrets "
                        "WHERE owner_user_id = :owner"
                    ),
                    {"owner": self.user_one},
                ).one()
            self.assertEqual("kek-test-a", stored.key_id)
            self.assertNotIn(b"synthetic-provider-secret", bytes(stored.ciphertext))

            with patch.dict(
                os.environ,
                _provider_secret_environment(active_key_id="kek-test-b"),
                clear=False,
            ):
                self.assertEqual(
                    1,
                    provider_secrets_module.rotate_provider_secrets(self.user_one),
                )
                self.assertEqual(
                    "synthetic-provider-secret",
                    provider_secrets_module.load_provider_secret(self.user_one, "gemini"),
                )
                with self.admin_engine.begin() as connection:
                    self.assertEqual(
                        "kek-test-b",
                        connection.scalar(
                            text(
                                "SELECT key_id FROM careertrellis.provider_secrets "
                                "WHERE owner_user_id = :owner"
                            ),
                            {"owner": self.user_one},
                        ),
                    )

            provider_secrets_module.revoke_provider_secret(self.user_one, "gemini")
            self.assertEqual(
                "",
                provider_secrets_module.load_provider_secret(self.user_one, "gemini"),
            )

    def test_all_protected_tables_force_rls_and_runtime_roles_cannot_bypass_it(self) -> None:
        with self.admin_engine.begin() as connection:
            table_rows = connection.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity "
                    "FROM pg_class JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace "
                    "WHERE nspname = 'careertrellis' AND relkind = 'r' "
                    "AND relname NOT IN ('alembic_version', 'search_run_claim_queue')"
                )
            ).all()
            self.assertGreaterEqual(len(table_rows), 14)
            self.assertTrue(all(enabled and forced for _, enabled, forced in table_rows))
            roles = connection.execute(
                text(
                    "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname IN ('careertrellis_runtime', 'careertrellis_worker')"
                )
            ).all()
            self.assertEqual(2, len(roles))
            self.assertTrue(all(not superuser and not bypass for _, superuser, bypass in roles))
            owners = connection.execute(
                text(
                    "SELECT DISTINCT owner.rolname FROM pg_class "
                    "JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace "
                    "JOIN pg_roles owner ON owner.oid = pg_class.relowner "
                    "WHERE nspname = 'careertrellis' AND relkind = 'r'"
                )
            ).scalars().all()
            self.assertEqual([SCHEMA_OWNER], owners)
            public_database_privileges = connection.execute(
                text(
                    "SELECT privilege_type FROM pg_database, "
                    "LATERAL aclexplode(COALESCE(datacl, acldefault('d', datdba))) "
                    "WHERE datname = :database_name AND grantee = 0"
                ),
                {"database_name": DATABASE_NAME},
            ).scalars().all()
            self.assertEqual([], public_database_privileges)

        with self.assertRaises(DBAPIError):
            with self.worker_engine.begin() as connection:
                connection.execute(text("SELECT * FROM careertrellis.search_run_claim_queue"))

    def test_search_runs_are_owner_isolated_and_claim_broker_is_worker_only(self) -> None:
        owner_one = PostgreSQLSearchRunRepository(self.user_one, engine=self.runtime_engine)
        owner_two = PostgreSQLSearchRunRepository(self.user_two, engine=self.runtime_engine)
        run_one = owner_one.enqueue(
            "00000000-0000-4000-8000-000000000001", "security", "Remote"
        )
        run_two = owner_two.enqueue(
            "00000000-0000-4000-8000-000000000002", "platform", "Ohio"
        )
        self.assertTrue(owner_one.status()["searching"])
        self.assertTrue(owner_two.status()["searching"])
        with self.assertRaises(SearchRunAlreadyActive):
            owner_one.enqueue(
                "00000000-0000-4000-8000-000000000003", "other", ""
            )
        with self.assertRaises(SearchRunUnavailable):
            claim_next_postgres_search_run(
                "10000000-0000-4000-8000-000000000001",
                engine=self.runtime_engine,
            )
        self.assertIsNone(
            claim_next_postgres_search_run(
                "10000000-0000-4000-8000-000000000010",
                engine=self.worker_engine,
            )
        )
        owner_one.publish_prepared(run_one, "security", "Remote")
        owner_two.publish_prepared(run_two, "platform", "Ohio")

        barrier = Barrier(2)

        def claim(worker_id: str):
            barrier.wait()
            return claim_next_postgres_search_run(worker_id, engine=self.worker_engine)

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    claim,
                    (
                        "10000000-0000-4000-8000-000000000001",
                        "10000000-0000-4000-8000-000000000002",
                    ),
                )
            )
        self.assertEqual({run_one, run_two}, {item.run_id for item in claims})
        self.assertEqual({self.user_one, self.user_two}, {item.owner_user_id for item in claims})
        for item in claims:
            repository = owner_one if item.owner_user_id == self.user_one else owner_two
            repository.succeed(item, {"success": True, "jobs_added": 1, "secret": "omitted"})
            self.assertEqual(1, repository.status()["last_result"]["jobs_added"])
            self.assertNotIn("secret", repository.status()["last_result"])

    def test_search_run_expiry_retries_and_rejects_the_stale_lease(self) -> None:
        repository = PostgreSQLSearchRunRepository(self.user_one, engine=self.runtime_engine)
        run_id = repository.enqueue(
            "00000000-0000-4000-8000-000000000011", "recovery", ""
        )
        repository.publish_prepared(run_id, "recovery", "")
        first = claim_next_postgres_search_run(
            "10000000-0000-4000-8000-000000000011", engine=self.worker_engine
        )
        self.assertEqual(run_id, first.run_id)
        with self.admin_engine.begin() as connection:
            connection.execute(
                text("""UPDATE careertrellis.search_runs
                    SET lease_expires_at=CURRENT_TIMESTAMP - interval '1 second'
                    WHERE id=CAST(:run_id AS uuid)"""),
                {"run_id": run_id},
            )
        second = claim_next_postgres_search_run(
            "10000000-0000-4000-8000-000000000012", engine=self.worker_engine
        )
        self.assertEqual(run_id, second.run_id)
        self.assertEqual(2, second.attempt)
        with self.assertRaises(SearchRunLeaseLost):
            repository.succeed(first, {"success": True, "jobs_added": 99})
        repository.succeed(second, {"success": True, "jobs_added": 2})
        self.assertEqual(2, repository.status()["last_result"]["jobs_added"])

    def test_search_run_cancel_and_attempt_exhaustion_are_terminal(self) -> None:
        repository = PostgreSQLSearchRunRepository(self.user_one, engine=self.runtime_engine)
        operation = "00000000-0000-4000-8000-000000000021"
        run_id = repository.enqueue(operation, "cancel", "")
        repository.publish_prepared(run_id, "cancel", "")
        claim = claim_next_postgres_search_run(
            "10000000-0000-4000-8000-000000000021", engine=self.worker_engine
        )
        self.assertTrue(repository.request_cancel(operation))
        with self.admin_engine.begin() as connection:
            connection.execute(
                text("""UPDATE careertrellis.search_runs
                    SET lease_expires_at=CURRENT_TIMESTAMP - interval '1 second'
                    WHERE id=CAST(:run_id AS uuid)"""),
                {"run_id": run_id},
            )
        self.assertIsNone(
            claim_next_postgres_search_run(
                "10000000-0000-4000-8000-000000000022", engine=self.worker_engine
            )
        )
        self.assertFalse(repository.status()["searching"])
        self.assertTrue(repository.status()["last_result"]["cancelled"])

        exhausted = repository.enqueue(
            "00000000-0000-4000-8000-000000000022", "retry", ""
        )
        repository.publish_prepared(exhausted, "retry", "")
        with self.admin_engine.begin() as connection:
            connection.execute(
                text("""UPDATE careertrellis.search_runs SET attempts=max_attempts
                    WHERE id=CAST(:run_id AS uuid)"""),
                {"run_id": exhausted},
            )
        self.assertIsNone(
            claim_next_postgres_search_run(
                "10000000-0000-4000-8000-000000000023", engine=self.worker_engine
            )
        )
        self.assertFalse(repository.status()["searching"])
        self.assertFalse(repository.status()["last_result"]["success"])

    def test_search_run_migration_downgrade_and_upgrade_restore_one_head(self) -> None:
        variable = "CAREERTRELLIS_MIGRATION_DATABASE_URL"
        previous = os.environ.get(variable)
        os.environ[variable] = self.migration_url.render_as_string(hide_password=False)
        alembic_config = Config(str(Path(__file__).with_name("alembic.ini")))
        try:
            command.downgrade(alembic_config, "20260813_0006")
            with self.admin_engine.begin() as connection:
                self.assertIsNone(
                    connection.scalar(
                        text("SELECT to_regclass('careertrellis.search_runs')")
                    )
                )
            command.upgrade(alembic_config, "head")
            command.current(alembic_config, check_heads=True)
        finally:
            if previous is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = previous
        self._grant_runtime_privileges()

    def test_atomic_sqlite_import_is_visible_only_to_the_mapped_owner(self) -> None:
        with self.admin_engine.begin() as connection:
            connection.exec_driver_sql(
                "TRUNCATE TABLE careertrellis.app_users RESTART IDENTITY CASCADE"
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "workspace.db"
            output_path = Path(temp_dir) / "output"
            output_path.mkdir()
            resume_path = output_path / "tailored_resume_1.pdf"
            letter_path = output_path / "cover_letter_1.txt"
            resume_path.write_bytes(b"%PDF-1.7\nSynthetic private resume")
            letter_path.write_bytes(b"Synthetic private cover letter\n")
            source = sqlite3.connect(source_path)
            try:
                source.executescript(
                    """
                    CREATE TABLE app_users (
                        id INTEGER PRIMARY KEY, workos_user_id TEXT, role TEXT,
                        created_at TEXT, last_seen_at TEXT, disabled_at TEXT
                    );
                    CREATE TABLE profile (
                        id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT,
                        github TEXT, linkedin TEXT, website TEXT, base_resume_text TEXT,
                        gemini_api_key TEXT, openai_api_key TEXT, google_maps_api_key TEXT,
                        suggested_keywords TEXT, ai_provider TEXT, ai_model TEXT,
                        maps_provider TEXT, prefer_us_headquarters INTEGER,
                        interface_language TEXT, active_base_resume_id INTEGER
                    );
                    CREATE TABLE base_resumes (
                        id INTEGER PRIMARY KEY, name TEXT, resume_mode TEXT, content TEXT,
                        evidence_json TEXT, created_at TEXT, updated_at TEXT
                    );
                    CREATE TABLE base_resume_versions (
                        id INTEGER PRIMARY KEY, base_resume_id INTEGER, version_number INTEGER,
                        name TEXT, resume_mode TEXT, content TEXT, evidence_json TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE jobs (
                        id INTEGER PRIMARY KEY, title TEXT, company TEXT, description TEXT,
                        url TEXT, match_score INTEGER, match_analysis TEXT, date_found TEXT,
                        status TEXT, archived_at TEXT, archived_from_status TEXT,
                        last_checked_at TEXT, is_expired INTEGER, expiration_reason TEXT,
                        location TEXT, work_arrangement TEXT, employment_type TEXT,
                        compensation TEXT, source TEXT
                    );
                    CREATE TABLE saved_searches (
                        id INTEGER PRIMARY KEY, name TEXT, keywords TEXT, location TEXT,
                        created_at TEXT, last_run_at TEXT, schedule_frequency TEXT,
                        next_alert_at TEXT, enabled INTEGER
                    );
                    CREATE TABLE applications (
                        id INTEGER PRIMARY KEY, job_id INTEGER, company TEXT, position TEXT,
                        date_applied TEXT, us_hq TEXT, tailored_resume_path TEXT,
                        cover_letter_path TEXT, tailored_resume_text TEXT, cover_letter TEXT,
                        status TEXT, created_at TEXT, tailored_at TEXT, form_filled_at TEXT,
                        submitted_at TEXT, confirmed_at TEXT, application_method TEXT,
                        submission_evidence TEXT, notes TEXT, follow_up_date TEXT,
                        headquarters_source TEXT, headquarters_attribution TEXT,
                        base_resume_id INTEGER, base_resume_name TEXT,
                        base_resume_version INTEGER, interview_prep TEXT,
                        interview_prep_updated_at TEXT
                    );
                    CREATE TABLE application_status_history (
                        id INTEGER PRIMARY KEY, job_id INTEGER, from_status TEXT,
                        to_status TEXT, changed_at TEXT, source TEXT, notes TEXT,
                        undone_at TEXT
                    );
                    CREATE TABLE application_engagements (
                        id INTEGER PRIMARY KEY, job_id INTEGER, engagement_type TEXT,
                        name TEXT, organization TEXT, contact_details TEXT, status TEXT,
                        activity_on TEXT, next_action_on TEXT, notes TEXT,
                        created_at TEXT, updated_at TEXT
                    );
                    CREATE TABLE job_suppressions (
                        id INTEGER PRIMARY KEY, url_fingerprint TEXT, hostname TEXT,
                        company TEXT, title TEXT, deleted_at TEXT, deletion_source TEXT
                    );
                    CREATE TABLE source_diagnostics (
                        id INTEGER PRIMARY KEY, recorded_at TEXT, provider TEXT,
                        diagnostic_code TEXT, counters_json TEXT
                    );
                    CREATE TABLE headquarters_cache (
                        cache_key TEXT PRIMARY KEY, provider TEXT, address TEXT,
                        country_code TEXT, attribution TEXT, resolved_at TEXT
                    );
                    """
                )
                timestamp = "2026-08-11T12:00:00"
                source.execute(
                    "INSERT INTO app_users VALUES (1, ?, 'owner', ?, ?, NULL)",
                    ("user_import_owner", timestamp, timestamp),
                )
                source.execute(
                    """
                    INSERT INTO profile
                        (id, name, email, gemini_api_key, openai_api_key,
                         google_maps_api_key, suggested_keywords, ai_provider,
                         ai_model, maps_provider, prefer_us_headquarters,
                         interface_language, active_base_resume_id)
                    VALUES
                        (1, 'Synthetic Owner', 'owner@example.test',
                         'synthetic-import-secret', '', '',
                         'python', 'gemini', 'gemini-2.5-flash',
                         'openstreetmap', 1, 'en', NULL)
                    """
                )
                source.execute(
                    """
                    INSERT INTO jobs VALUES
                        (1, 'Synthetic Role', 'Example', 'Description',
                         'https://example.test/job', 80, 'Good fit', '2026-08-11',
                         'matched', NULL, NULL, NULL, 0, NULL, 'Remote', 'remote',
                         'full_time', NULL, 'manual')
                    """
                )
                source.execute(
                    """
                    INSERT INTO applications (
                        id, job_id, company, position, tailored_resume_path,
                        cover_letter_path, tailored_resume_text, cover_letter,
                        status, created_at, tailored_at
                    ) VALUES (1, 1, 'Example', 'Synthetic Role', ?, ?,
                              'Synthetic resume source', 'Synthetic letter source',
                              'tailored', ?, ?)
                    """,
                    (str(resume_path), str(letter_path), timestamp, timestamp),
                )
                source.commit()
            finally:
                source.close()

            migration_engine = create_engine(self.migration_url)
            object_backend = object_store_module.InMemoryObjectBackend()
            object_keyring = object_store_module.ObjectKeyring(
                "object-kek-test",
                {"object-kek-test": b"o" * 32},
            )
            try:
                with patch.dict(os.environ, _provider_secret_environment(), clear=False):
                    manifest = import_sqlite_workspace(
                        source_path,
                        "user_import_owner",
                        engine=migration_engine,
                        object_backend=object_backend,
                        object_keyring=object_keyring,
                    )
                with migration_engine.connect() as connection:
                    self.assertEqual(
                        MIGRATION_ROLE,
                        connection.scalar(text("SELECT current_user")),
                    )
                with self.assertRaises(RuntimeError):
                    import_sqlite_workspace(
                        source_path,
                        "user_import_owner",
                        engine=migration_engine,
                        object_backend=object_backend,
                        object_keyring=object_keyring,
                    )
                with migration_engine.connect() as connection:
                    self.assertEqual(
                        MIGRATION_ROLE,
                        connection.scalar(text("SELECT current_user")),
                    )
            finally:
                migration_engine.dispose()

        self.assertEqual(1, manifest.counts["profile"])
        self.assertEqual(1, manifest.counts["jobs"])
        self.assertEqual(1, manifest.counts["applications"])
        self.assertEqual(2, manifest.generated_file_paths)
        with identity_transaction(self.runtime_engine, "user_import_owner") as connection:
            owner_id = connection.scalar(
                text("SELECT id FROM careertrellis.app_users WHERE workos_user_id = 'user_import_owner'")
            )
        with tenant_transaction(
            self.runtime_engine, owner_id, "user_import_owner"
        ) as connection:
            self.assertEqual(
                [("Synthetic Role",)],
                connection.execute(text("SELECT title FROM careertrellis.jobs")).all(),
            )
            object_ids = connection.execute(
                text(
                    "SELECT tailored_resume_object_id, cover_letter_object_id "
                    "FROM careertrellis.applications"
                )
            ).one()
            self.assertTrue(all(object_ids))
            self.assertEqual(
                2,
                connection.scalar(text("SELECT count(*) FROM careertrellis.stored_objects")),
            )
        resume_download = stored_objects_module.download_stored_object(
            owner_id,
            object_ids.tailored_resume_object_id,
            engine=self.runtime_engine,
            backend=object_backend,
            keyring=object_keyring,
        )
        self.assertEqual(b"%PDF-1.7\nSynthetic private resume", resume_download.content)
        letter_download = stored_objects_module.download_stored_object(
            owner_id,
            object_ids.cover_letter_object_id,
            engine=self.runtime_engine,
            backend=object_backend,
            keyring=object_keyring,
        )
        self.assertEqual(b"Synthetic private cover letter\n", letter_download.content)
        with (
            patch.dict(os.environ, _provider_secret_environment(), clear=False),
            patch.object(
                provider_secrets_module,
                "runtime_postgres_engine",
                return_value=self.runtime_engine,
            ),
        ):
            self.assertEqual(
                "synthetic-import-secret",
                provider_secrets_module.load_provider_secret(owner_id, "gemini"),
            )
        with identity_transaction(self.runtime_engine, "user_other_identity") as connection:
            self.assertEqual(0, connection.scalar(text("SELECT count(*) FROM careertrellis.app_users")))
        with self.runtime_engine.begin() as connection:
            self.assertEqual(
                0,
                connection.scalar(text("SELECT count(*) FROM careertrellis.stored_objects")),
            )
        with tenant_transaction(self.runtime_engine, self.user_two) as connection:
            self.assertEqual(
                0,
                connection.execute(
                    text("UPDATE careertrellis.stored_objects SET deleted_at = CURRENT_TIMESTAMP WHERE id = :id"),
                    {"id": object_ids.tailored_resume_object_id},
                ).rowcount,
            )
            self.assertEqual(
                0,
                connection.execute(
                    text("DELETE FROM careertrellis.stored_objects WHERE id = :id"),
                    {"id": object_ids.tailored_resume_object_id},
                ).rowcount,
            )
        with self.assertRaises(DBAPIError):
            with tenant_transaction(self.runtime_engine, self.user_two) as connection:
                connection.execute(
                    text(
                        "INSERT INTO careertrellis.stored_objects "
                        "(owner_user_id, object_key, content_type, plaintext_size, sha256, wrapped_data_key) "
                        "VALUES (:owner, 'v1/00000000000000000000000000000000', "
                        "'application/pdf', 1, :digest, :wrapped)"
                    ),
                    {"owner": owner_id, "digest": b"d" * 32, "wrapped": b"wrapped"},
                )
        with (
            patch.object(object_backend, "get", wraps=object_backend.get) as backend_get,
            self.assertRaises(stored_objects_module.StoredObjectNotFound),
        ):
            stored_objects_module.download_stored_object(
                self.user_two,
                object_ids.tailored_resume_object_id,
                engine=self.runtime_engine,
                backend=object_backend,
                keyring=object_keyring,
            )
        backend_get.assert_not_called()
