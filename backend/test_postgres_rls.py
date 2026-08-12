"""PostgreSQL migration and row-level isolation integration tests.

Set CAREERTRELLIS_POSTGRES_TEST_ADMIN_URL to an isolated PostgreSQL 18 server.
The test creates and removes one dedicated database and four fixed test roles.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError

from sqlite_postgres_import import import_sqlite_workspace

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
            pool_size=1,
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

    def test_no_context_exposes_no_protected_rows(self) -> None:
        with self.runtime_engine.begin() as connection:
            self.assertEqual(0, connection.scalar(text("SELECT count(*) FROM careertrellis.jobs")))
            self.assertEqual(0, connection.scalar(text("SELECT count(*) FROM careertrellis.profiles")))

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

    def test_all_protected_tables_force_rls_and_runtime_roles_cannot_bypass_it(self) -> None:
        with self.admin_engine.begin() as connection:
            table_rows = connection.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity "
                    "FROM pg_class JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace "
                    "WHERE nspname = 'careertrellis' AND relkind = 'r' "
                    "AND relname <> 'alembic_version'"
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

    def test_atomic_sqlite_import_is_visible_only_to_the_mapped_owner(self) -> None:
        with self.admin_engine.begin() as connection:
            connection.exec_driver_sql(
                "TRUNCATE TABLE careertrellis.app_users RESTART IDENTITY CASCADE"
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "workspace.db"
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
                        (1, 'Synthetic Owner', 'owner@example.test', '', '', '',
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
                source.commit()
            finally:
                source.close()

            migration_engine = create_engine(self.migration_url)
            try:
                manifest = import_sqlite_workspace(
                    source_path,
                    "user_import_owner",
                    omit_provider_secrets=True,
                    omit_generated_files=True,
                    engine=migration_engine,
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
                        omit_provider_secrets=True,
                        omit_generated_files=True,
                        engine=migration_engine,
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
        with identity_transaction(self.runtime_engine, "user_other_identity") as connection:
            self.assertEqual(0, connection.scalar(text("SELECT count(*) FROM careertrellis.app_users")))
