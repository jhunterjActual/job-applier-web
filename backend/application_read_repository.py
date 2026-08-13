"""Dual-mode repositories for application records and outcome insights."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any, Protocol

from sqlalchemy import Engine, text

from application_insights import (
    build_application_insights,
    build_application_insights_from_rows,
)
from data_store import LOCAL_DATA_STORE, data_store_kind
from database import get_db_connection
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


class ApplicationReadRepository(Protocol):
    def list_applications(self) -> list[dict[str, Any]]: ...
    def application_insights(self) -> dict[str, object]: ...


def _api_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _application_record(row: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: _api_value(value) for key, value in dict(row).items()}
    for private_field in (
        "owner_user_id",
        "tailored_resume_object_id",
        "cover_letter_object_id",
        "tailored_resume_path",
        "cover_letter_path",
        "tailored_resume_text",
        "cover_letter",
        "interview_prep",
        "submission_evidence",
        "notes",
    ):
        result.pop(private_field, None)
    for key in (
        "resume_download_available",
        "cover_letter_download_available",
        "interview_prep_available",
    ):
        result[key] = bool(result.get(key))
    result["engagement_count"] = int(result.get("engagement_count") or 0)
    return result


class SQLiteApplicationReadRepository:
    """Preserve standalone record and insight reads without exposing file paths."""

    def __init__(self, connection_factory: Callable[[], Any] = get_db_connection):
        self._connection_factory = connection_factory

    def list_applications(self) -> list[dict[str, Any]]:
        connection = self._connection_factory()
        try:
            rows = connection.execute(
                """
                SELECT a.id, a.job_id, a.company, a.position, a.date_applied,
                       a.us_hq, a.status, a.created_at, a.tailored_at,
                       a.form_filled_at, a.submitted_at, a.confirmed_at,
                       a.application_method, a.follow_up_date,
                       a.headquarters_source,
                       a.headquarters_attribution, a.base_resume_id,
                       a.base_resume_name, a.base_resume_version,
                       a.interview_prep_updated_at, j.url,
                       (SELECT COUNT(*) FROM application_engagements e
                        WHERE e.job_id = a.job_id) AS engagement_count,
                       CASE WHEN NULLIF(TRIM(COALESCE(a.tailored_resume_path, '')), '')
                                      IS NOT NULL
                            THEN 1 ELSE 0 END AS resume_download_available,
                       CASE WHEN NULLIF(TRIM(COALESCE(a.cover_letter_path, '')), '')
                                      IS NOT NULL
                                  OR NULLIF(TRIM(COALESCE(a.cover_letter, '')), '')
                                      IS NOT NULL
                            THEN 1 ELSE 0 END AS cover_letter_download_available,
                       CASE WHEN NULLIF(TRIM(COALESCE(a.interview_prep, '')), '')
                                      IS NOT NULL
                            THEN 1 ELSE 0 END AS interview_prep_available
                FROM applications a
                LEFT JOIN jobs j ON a.job_id = j.id
                ORDER BY CASE WHEN a.date_applied IS NULL OR a.date_applied = ''
                              THEN 1 ELSE 0 END,
                         a.date_applied DESC, a.id DESC
                """
            ).fetchall()
            return [_application_record(row) for row in rows]
        finally:
            connection.close()

    def application_insights(self) -> dict[str, object]:
        connection = self._connection_factory()
        try:
            return build_application_insights(connection)
        finally:
            connection.close()


class PostgreSQLApplicationReadRepository:
    """Owner-scoped PostgreSQL application reads under forced RLS."""

    def __init__(self, owner_user_id: int, engine: Engine | None = None):
        self._owner_user_id = owner_user_id
        self._engine = engine or runtime_postgres_engine()

    def list_applications(self) -> list[dict[str, Any]]:
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT a.id, a.job_id, a.company, a.position, a.date_applied,
                           a.us_hq, a.status, a.created_at, a.tailored_at,
                           a.form_filled_at, a.submitted_at, a.confirmed_at,
                           a.application_method, a.follow_up_date,
                           a.headquarters_source,
                           a.headquarters_attribution, a.base_resume_id,
                           a.base_resume_name, a.base_resume_version,
                           a.interview_prep_updated_at, j.url,
                           (SELECT COUNT(*)
                            FROM careertrellis.application_engagements e
                            WHERE e.job_id = a.job_id
                              AND e.owner_user_id = :owner_user_id)
                               AS engagement_count,
                           a.tailored_resume_object_id IS NOT NULL
                               AS resume_download_available,
                           a.cover_letter_object_id IS NOT NULL
                               AS cover_letter_download_available,
                           NULLIF(BTRIM(COALESCE(a.interview_prep, '')), '')
                               IS NOT NULL AS interview_prep_available
                    FROM careertrellis.applications a
                    LEFT JOIN careertrellis.jobs j
                      ON j.id = a.job_id
                     AND j.owner_user_id = a.owner_user_id
                    WHERE a.owner_user_id = :owner_user_id
                    ORDER BY a.date_applied DESC NULLS LAST, a.id DESC
                    """
                ),
                {"owner_user_id": self._owner_user_id},
            ).mappings().all()
            return [_application_record(row) for row in rows]

    def application_insights(self) -> dict[str, object]:
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            applications = connection.execute(
                text(
                    """
                    SELECT a.job_id, a.position, a.date_applied, a.confirmed_at,
                           a.application_method, a.base_resume_name,
                           a.base_resume_version, a.status, j.source, j.location
                    FROM careertrellis.applications a
                    LEFT JOIN careertrellis.jobs j
                      ON j.id = a.job_id
                     AND j.owner_user_id = a.owner_user_id
                    WHERE a.owner_user_id = :owner_user_id
                      AND a.status IN (
                          'applied', 'interview', 'offer', 'rejected',
                          'withdrawn', 'closed'
                    )
                    ORDER BY COALESCE(
                        a.date_applied,
                        CAST(a.confirmed_at AS date),
                        CAST(a.created_at AS date)
                    ), a.id
                    """
                ),
                {"owner_user_id": self._owner_user_id},
            ).mappings().all()
            histories = connection.execute(
                text(
                    """
                    SELECT h.job_id, h.to_status, h.changed_at, h.undone_at
                    FROM careertrellis.application_status_history h
                    INNER JOIN careertrellis.applications a
                      ON a.job_id = h.job_id
                     AND a.owner_user_id = h.owner_user_id
                    WHERE h.owner_user_id = :owner_user_id
                      AND a.status IN (
                          'applied', 'interview', 'offer', 'rejected',
                          'withdrawn', 'closed'
                      )
                    ORDER BY h.changed_at, h.id
                    """
                ),
                {"owner_user_id": self._owner_user_id},
            ).mappings().all()
        return build_application_insights_from_rows(applications, histories)


def runtime_application_read_repository(
    *,
    sqlite_connection_factory: Callable[[], Any] = get_db_connection,
    engine: Engine | None = None,
) -> ApplicationReadRepository:
    """Return the configured application reader without caller-selected ownership."""
    if data_store_kind() == LOCAL_DATA_STORE:
        return SQLiteApplicationReadRepository(sqlite_connection_factory)
    return PostgreSQLApplicationReadRepository(
        current_owner_user_id(), engine=engine
    )
