"""Owner-scoped snapshots for the portable user-data export."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from typing import Any, Protocol, TypedDict

from sqlalchemy import Engine, text

from data_export import EXPORT_FIELDS
from data_store import LOCAL_DATA_STORE, data_store_kind
from database import get_db_connection
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


EXPORT_UNAVAILABLE_MESSAGE = "The user data export is temporarily unavailable."


class UserDataExportUnavailable(RuntimeError):
    """Raised at the fixed, privacy-safe export persistence boundary."""


class UserDataExportSnapshot(TypedDict):
    profile: list[dict[str, Any]]
    base_resumes: list[dict[str, Any]]
    base_resume_versions: list[dict[str, Any]]
    jobs: list[dict[str, Any]]
    saved_searches: list[dict[str, Any]]
    applications: list[dict[str, Any]]
    application_status_history: list[dict[str, Any]]
    application_engagements: list[dict[str, Any]]
    job_suppressions: list[dict[str, Any]]
    source_diagnostics: list[dict[str, Any]]


class UserDataExportRepository(Protocol):
    def snapshot(self) -> UserDataExportSnapshot: ...


_SQLITE_SOURCE_FIELDS = {
    ("base_resumes", "professional_evidence"): "evidence_json",
    ("base_resume_versions", "professional_evidence"): "evidence_json",
    ("source_diagnostics", "counters"): "counters_json",
}
_SQLITE_ORDER_FIELDS = {
    "base_resume_versions": ("base_resume_id", "version_number", "id"),
}


def _sqlite_rows(connection: Any, table: str) -> list[dict[str, Any]]:
    available = {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    selected: list[tuple[str, str]] = []
    for output_name in EXPORT_FIELDS[table]:
        source_name = _SQLITE_SOURCE_FIELDS.get((table, output_name), output_name)
        if source_name in available:
            selected.append((output_name, source_name))
    if not selected:
        return []
    projection = ", ".join(
        f'"{source_name}" AS "{output_name}"'
        for output_name, source_name in selected
    )
    order_fields = _SQLITE_ORDER_FIELDS.get(table, ("id",))
    order_by = ", ".join(
        f'"{field}"' for field in order_fields if field in available
    ) or "rowid"
    rows = connection.execute(
        f'SELECT {projection} FROM "{table}" ORDER BY {order_by}'
    ).fetchall()
    names = [output_name for output_name, _ in selected]
    return [dict(zip(names, row)) for row in rows]


class SQLiteUserDataExportRepository:
    """Read one deterministic SQLite snapshot and close it before serialization."""

    def __init__(self, connection_factory: Callable[[], Any] = get_db_connection):
        self._connection_factory = connection_factory

    def snapshot(self) -> UserDataExportSnapshot:
        connection = None
        try:
            connection = self._connection_factory()
            connection.execute("BEGIN")
            snapshot = {
                table: _sqlite_rows(connection, table)
                for table in EXPORT_FIELDS
            }
            connection.commit()
        except Exception:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
                try:
                    connection.close()
                except Exception:
                    pass
            raise UserDataExportUnavailable(EXPORT_UNAVAILABLE_MESSAGE) from None
        try:
            connection.close()
        except Exception:
            raise UserDataExportUnavailable(EXPORT_UNAVAILABLE_MESSAGE) from None
        return snapshot  # type: ignore[return-value]


# One SQL statement gives all subqueries one PostgreSQL MVCC statement snapshot.
# Every protected relation also retains an explicit authenticated-owner predicate
# in addition to the transaction-local forced-RLS boundary.
_POSTGRES_SNAPSHOT_SQL = """
SELECT jsonb_build_object(
    'profile', COALESCE((
        SELECT jsonb_agg(to_jsonb(export_row) ORDER BY export_row.id)
        FROM (
            SELECT p.id, p.name, p.email, p.phone, p.github, p.linkedin, p.website,
                   COALESCE(r.content, '') AS base_resume_text,
                   p.suggested_keywords, p.ai_provider, p.ai_model, p.maps_provider,
                   p.prefer_us_headquarters, p.interface_language,
                   COALESCE(r.resume_mode, 'general_professional') AS resume_mode,
                   p.active_base_resume_id
            FROM careertrellis.profiles AS p
            LEFT JOIN careertrellis.base_resumes AS r
              ON r.id = p.active_base_resume_id
             AND r.owner_user_id = :owner_user_id
             AND r.owner_user_id = p.owner_user_id
             AND r.deleted_at IS NULL
            WHERE p.owner_user_id = :owner_user_id
        ) AS export_row
    ), '[]'::jsonb),
    'base_resumes', COALESCE((
        SELECT jsonb_agg(to_jsonb(export_row) ORDER BY export_row.id)
        FROM (
            SELECT id, name, resume_mode, content,
                   evidence AS professional_evidence, created_at, updated_at
            FROM careertrellis.base_resumes
            WHERE owner_user_id = :owner_user_id
            ORDER BY id
        ) AS export_row
    ), '[]'::jsonb),
    'base_resume_versions', COALESCE((
        SELECT jsonb_agg(to_jsonb(export_row)
                         ORDER BY export_row.base_resume_id,
                                  export_row.version_number, export_row.id)
        FROM (
            SELECT id, base_resume_id, version_number, name, resume_mode, content,
                   evidence AS professional_evidence, created_at
            FROM careertrellis.base_resume_versions
            WHERE owner_user_id = :owner_user_id
            ORDER BY base_resume_id, version_number, id
        ) AS export_row
    ), '[]'::jsonb),
    'jobs', COALESCE((
        SELECT jsonb_agg(to_jsonb(export_row) ORDER BY export_row.id)
        FROM (
            SELECT id, title, company, description, url, match_score, match_analysis,
                   date_found, status, archived_at, archived_from_status,
                   last_checked_at, is_expired, expiration_reason, location,
                   work_arrangement, employment_type, compensation, source
            FROM careertrellis.jobs
            WHERE owner_user_id = :owner_user_id
            ORDER BY id
        ) AS export_row
    ), '[]'::jsonb),
    'saved_searches', COALESCE((
        SELECT jsonb_agg(to_jsonb(export_row) ORDER BY export_row.id)
        FROM (
            SELECT id, name, keywords, location, created_at, last_run_at,
                   schedule_frequency, next_alert_at, enabled
            FROM careertrellis.saved_searches
            WHERE owner_user_id = :owner_user_id
            ORDER BY id
        ) AS export_row
    ), '[]'::jsonb),
    'applications', COALESCE((
        SELECT jsonb_agg(to_jsonb(export_row) ORDER BY export_row.id)
        FROM (
            SELECT id, job_id, company, position, date_applied, us_hq,
                   tailored_resume_text, cover_letter, status, created_at, tailored_at,
                   form_filled_at, submitted_at, confirmed_at, application_method,
                   submission_evidence, notes, follow_up_date, headquarters_source,
                   headquarters_attribution, base_resume_id, base_resume_name,
                   base_resume_version, interview_prep, interview_prep_updated_at
            FROM careertrellis.applications
            WHERE owner_user_id = :owner_user_id
            ORDER BY id
        ) AS export_row
    ), '[]'::jsonb),
    'application_status_history', COALESCE((
        SELECT jsonb_agg(to_jsonb(export_row) ORDER BY export_row.id)
        FROM (
            SELECT id, job_id, from_status, to_status, changed_at, source, notes, undone_at
            FROM careertrellis.application_status_history
            WHERE owner_user_id = :owner_user_id
            ORDER BY id
        ) AS export_row
    ), '[]'::jsonb),
    'application_engagements', COALESCE((
        SELECT jsonb_agg(to_jsonb(export_row) ORDER BY export_row.id)
        FROM (
            SELECT id, job_id, engagement_type, name, organization,
                   contact_details, status, activity_on, next_action_on, notes,
                   created_at, updated_at
            FROM careertrellis.application_engagements
            WHERE owner_user_id = :owner_user_id
            ORDER BY id
        ) AS export_row
    ), '[]'::jsonb),
    'job_suppressions', COALESCE((
        SELECT jsonb_agg(to_jsonb(export_row) ORDER BY export_row.id)
        FROM (
            SELECT id, hostname, company, title, deleted_at, deletion_source
            FROM careertrellis.job_suppressions
            WHERE owner_user_id = :owner_user_id
            ORDER BY id
        ) AS export_row
    ), '[]'::jsonb),
    'source_diagnostics', COALESCE((
        SELECT jsonb_agg(to_jsonb(export_row) ORDER BY export_row.id)
        FROM (
            SELECT id, recorded_at, provider, diagnostic_code, counters
            FROM careertrellis.source_diagnostics
            WHERE owner_user_id = :owner_user_id
            ORDER BY id
        ) AS export_row
    ), '[]'::jsonb)
) AS snapshot
"""


class PostgreSQLUserDataExportRepository:
    """Read one authenticated owner's minimized snapshot under forced RLS."""

    def __init__(self, owner_user_id: int, engine: Engine | None = None):
        self._owner_user_id = owner_user_id
        self._engine = engine or runtime_postgres_engine()

    def snapshot(self) -> UserDataExportSnapshot:
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                raw_snapshot = connection.execute(
                    text(_POSTGRES_SNAPSHOT_SQL),
                    {"owner_user_id": self._owner_user_id},
                ).scalar_one()
        except Exception:
            raise UserDataExportUnavailable(EXPORT_UNAVAILABLE_MESSAGE) from None

        if isinstance(raw_snapshot, str):
            try:
                raw_snapshot = json.loads(raw_snapshot)
            except (TypeError, json.JSONDecodeError):
                raise UserDataExportUnavailable(EXPORT_UNAVAILABLE_MESSAGE) from None
        if not isinstance(raw_snapshot, Mapping):
            raise UserDataExportUnavailable(EXPORT_UNAVAILABLE_MESSAGE)

        snapshot: dict[str, list[dict[str, Any]]] = {}
        for table in EXPORT_FIELDS:
            rows = raw_snapshot.get(table)
            if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
                raise UserDataExportUnavailable(EXPORT_UNAVAILABLE_MESSAGE)
            snapshot[table] = [dict(row) for row in rows]
        return snapshot  # type: ignore[return-value]


def runtime_user_data_export_repository(
    *,
    sqlite_connection_factory: Callable[[], Any] = get_db_connection,
    engine: Engine | None = None,
) -> UserDataExportRepository:
    """Select the configured store without accepting caller-selected ownership."""
    if data_store_kind() == LOCAL_DATA_STORE:
        return SQLiteUserDataExportRepository(sqlite_connection_factory)
    return PostgreSQLUserDataExportRepository(
        current_owner_user_id(), engine=engine
    )
