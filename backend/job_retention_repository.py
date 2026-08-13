"""Dual-mode, owner-scoped job archive, restore, and retention writes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
from typing import Any, Protocol

from sqlalchemy import Engine, text

from data_store import LOCAL_DATA_STORE, data_store_kind
from database import get_db_connection
from discovery_repository import acquire_owner_job_catalog_lock
from job_suppressions import job_url_fingerprint, record_job_suppression, suppression_values
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


MAX_RETENTION_BATCH = 500
_PREVIEW_HMAC_KEY = secrets.token_bytes(32)
_ACTIONS = frozenset({"archive", "delete", "restore"})

_ACTIVE_SQL = """
    jobs.status = 'matched'
    AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = jobs.id)
    AND NOT EXISTS (
        SELECT 1 FROM application_status_history h WHERE h.job_id = jobs.id
    )
    AND NOT EXISTS (
        SELECT 1 FROM application_engagements e WHERE e.job_id = jobs.id
    )
"""
_ARCHIVED_SQL = """
    jobs.status = 'archived'
    AND COALESCE(jobs.archived_from_status, 'matched') = 'matched'
    AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = jobs.id)
    AND NOT EXISTS (
        SELECT 1 FROM application_status_history h WHERE h.job_id = jobs.id
    )
    AND NOT EXISTS (
        SELECT 1 FROM application_engagements e WHERE e.job_id = jobs.id
    )
"""

_POSTGRES_ACTIVE_SQL = """
    j.status = 'matched'
    AND NOT EXISTS (
        SELECT 1 FROM careertrellis.applications a
        WHERE a.job_id = j.id AND a.owner_user_id = :owner_user_id
    )
    AND NOT EXISTS (
        SELECT 1 FROM careertrellis.application_status_history h
        WHERE h.job_id = j.id AND h.owner_user_id = :owner_user_id
    )
    AND NOT EXISTS (
        SELECT 1 FROM careertrellis.application_engagements e
        WHERE e.job_id = j.id AND e.owner_user_id = :owner_user_id
    )
"""
_POSTGRES_ARCHIVED_SQL = """
    j.status = 'archived'
    AND COALESCE(j.archived_from_status, 'matched') = 'matched'
    AND NOT EXISTS (
        SELECT 1 FROM careertrellis.applications a
        WHERE a.job_id = j.id AND a.owner_user_id = :owner_user_id
    )
    AND NOT EXISTS (
        SELECT 1 FROM careertrellis.application_status_history h
        WHERE h.job_id = j.id AND h.owner_user_id = :owner_user_id
    )
    AND NOT EXISTS (
        SELECT 1 FROM careertrellis.application_engagements e
        WHERE e.job_id = j.id AND e.owner_user_id = :owner_user_id
    )
"""


class JobRetentionNotFound(LookupError):
    """Raised when the selected tenant cannot see the requested job."""


class JobRetentionProtected(RuntimeError):
    """Raised when related user work makes permanent deletion unsafe."""


class CleanupPreviewChanged(ValueError):
    """Raised when the owner-scoped candidate set no longer matches preview."""


class CleanupBatchTooLarge(ValueError):
    """Raised before an unbounded cleanup mutation can acquire row locks."""


class JobRetentionRepository(Protocol):
    def cleanup_preview(self) -> dict[str, Any]: ...
    def apply_cleanup(self, action: str, preview_token: str) -> int: ...
    def delete_job(self, job_id: int) -> None: ...


def _action(value: object) -> str:
    if not isinstance(value, str) or value not in _ACTIONS:
        raise ValueError("Unsupported cleanup action.")
    return value


def _positive_job_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Job identifier must be a positive integer.")
    return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot_rows(action: str, rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    snapshot = []
    for row in rows:
        item = {
            "id": int(row["id"]),
            "status": str(row["status"]),
            "archived_from_status": row["archived_from_status"],
        }
        if action == "delete":
            item["url_fingerprint"] = job_url_fingerprint(str(row["url"]))
        snapshot.append(item)
    return snapshot


def _preview_token(
    mode_owner: str, action: str, rows: list[Mapping[str, Any]]
) -> str:
    payload = json.dumps(
        {
            "mode_owner": mode_owner,
            "action": action,
            "rows": _snapshot_rows(action, rows),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(_PREVIEW_HMAC_KEY, payload, hashlib.sha256).hexdigest()


def _require_current_preview(
    mode_owner: str,
    action: str,
    rows: list[Mapping[str, Any]],
    supplied_token: object,
) -> None:
    expected = _preview_token(mode_owner, action, rows)
    candidate = supplied_token if isinstance(supplied_token, str) else ""
    if not hmac.compare_digest(expected, candidate):
        raise CleanupPreviewChanged(
            "The job list changed after preview. Refresh the preview before continuing."
        )


def _require_bounded(rows: list[Mapping[str, Any]]) -> None:
    if len(rows) > MAX_RETENTION_BATCH:
        raise CleanupBatchTooLarge(
            f"Cleanup is limited to {MAX_RETENTION_BATCH} jobs at a time."
        )


def _sqlite_where(action: str) -> str:
    if action == "archive":
        return _ACTIVE_SQL
    if action == "restore":
        return _ARCHIVED_SQL
    return f"({_ACTIVE_SQL}) OR ({_ARCHIVED_SQL})"


def _postgres_where(action: str) -> str:
    if action == "archive":
        return _POSTGRES_ACTIVE_SQL
    if action == "restore":
        return _POSTGRES_ARCHIVED_SQL
    return f"({_POSTGRES_ACTIVE_SQL}) OR ({_POSTGRES_ARCHIVED_SQL})"


class SQLiteJobRetentionRepository:
    """Preserve standalone cleanup behavior under SQLite write transactions."""

    _MODE_OWNER = "sqlite:standalone"

    def __init__(self, connection_factory: Callable[[], Any] = get_db_connection):
        self._connection_factory = connection_factory

    @staticmethod
    def _candidate_rows(connection: Any, action: str) -> list[Mapping[str, Any]]:
        return connection.execute(
            f"""
            SELECT id, status, archived_from_status, url, company, title
            FROM jobs WHERE {_sqlite_where(action)} ORDER BY id
            LIMIT {MAX_RETENTION_BATCH + 1}
            """
        ).fetchall()

    @staticmethod
    def _candidate_count(connection: Any, action: str) -> int:
        return int(connection.execute(
            f"SELECT COUNT(*) FROM jobs WHERE {_sqlite_where(action)}"
        ).fetchone()[0])

    @staticmethod
    def _is_protected(connection: Any, job_id: int) -> bool:
        return connection.execute(
            """
            SELECT EXISTS(SELECT 1 FROM applications WHERE job_id = ?)
                OR EXISTS(SELECT 1 FROM application_status_history WHERE job_id = ?)
                OR EXISTS(SELECT 1 FROM application_engagements WHERE job_id = ?)
            """,
            (job_id, job_id, job_id),
        ).fetchone()[0] != 0

    def cleanup_preview(self) -> dict[str, Any]:
        connection = self._connection_factory()
        try:
            action_rows = {
                action: self._candidate_rows(connection, action)
                for action in ("archive", "delete", "restore")
            }
            action_counts = {
                action: self._candidate_count(connection, action)
                for action in ("archive", "delete", "restore")
            }
            total = int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
            sample = connection.execute(
                f"""
                SELECT id, company, title, date_found FROM jobs
                WHERE {_ACTIVE_SQL}
                ORDER BY match_score DESC, id LIMIT 5
                """
            ).fetchall()
            return {
                "definition": "Matched jobs with no application record or related progress.",
                "actions": {
                    action: {
                        "count": action_counts[action],
                        "preview_token": _preview_token(self._MODE_OWNER, action, rows),
                    }
                    for action, rows in action_rows.items()
                },
                "protected_count": total - action_counts["delete"],
                "sample": [dict(row) for row in sample],
            }
        finally:
            connection.close()

    def apply_cleanup(self, action: str, preview_token: str) -> int:
        safe_action = _action(action)
        connection = self._connection_factory()
        try:
            # Bound the set before SQLite's database-wide write reservation,
            # then repeat the decision under that reservation.
            _require_bounded(self._candidate_rows(connection, safe_action))
            connection.execute("BEGIN IMMEDIATE")
            rows = self._candidate_rows(connection, safe_action)
            _require_bounded(rows)
            _require_current_preview(
                self._MODE_OWNER, safe_action, rows, preview_token
            )
            if not rows:
                connection.commit()
                return 0
            ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            now = _utc_now().isoformat(timespec="seconds")
            if safe_action == "archive":
                cursor = connection.execute(
                    f"""
                    UPDATE jobs SET archived_from_status = status,
                        status = 'archived', archived_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (now, *ids),
                )
            elif safe_action == "restore":
                cursor = connection.execute(
                    f"""
                    UPDATE jobs SET status = COALESCE(archived_from_status, 'matched'),
                        archived_from_status = NULL, archived_at = NULL
                    WHERE id IN ({placeholders})
                    """,
                    ids,
                )
            else:
                for row in rows:
                    record_job_suppression(
                        connection,
                        url=row["url"],
                        company=row["company"],
                        title=row["title"],
                        deleted_at=now,
                        deletion_source="bulk_cleanup",
                    )
                cursor = connection.execute(
                    f"DELETE FROM jobs WHERE id IN ({placeholders})", ids
                )
            if cursor.rowcount != len(ids):
                raise CleanupPreviewChanged("The job list changed during cleanup.")
            connection.commit()
            return len(ids)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_job(self, job_id: int) -> None:
        safe_job_id = _positive_job_id(job_id)
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT id, url, company, title FROM jobs WHERE id = ?",
                (safe_job_id,),
            ).fetchone()
            if not job:
                raise JobRetentionNotFound("Job not found.")
            if self._is_protected(connection, safe_job_id):
                raise JobRetentionProtected(
                    "Jobs with application activity or generated materials cannot be deleted."
                )
            record_job_suppression(
                connection,
                url=job["url"],
                company=job["company"],
                title=job["title"],
                deleted_at=_utc_now().isoformat(timespec="seconds"),
                deletion_source="manual",
            )
            cursor = connection.execute("DELETE FROM jobs WHERE id = ?", (safe_job_id,))
            if cursor.rowcount != 1:
                raise JobRetentionNotFound("Job not found.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class PostgreSQLJobRetentionRepository:
    """Owner-scoped retention writes under transaction-local forced RLS."""

    def __init__(self, owner_user_id: int, engine: Engine | None = None):
        self._owner_user_id = owner_user_id
        self._engine = engine or runtime_postgres_engine()
        self._mode_owner = f"postgres:{owner_user_id}"

    def _candidate_rows(self, connection: Any, action: str) -> list[Mapping[str, Any]]:
        return connection.execute(
            text(
                f"""
                SELECT j.id, j.status, j.archived_from_status, j.url,
                       j.company, j.title
                FROM careertrellis.jobs j
                WHERE j.owner_user_id = :owner_user_id
                  AND ({_postgres_where(action)})
                ORDER BY j.id
                LIMIT {MAX_RETENTION_BATCH + 1}
                """
            ),
            {"owner_user_id": self._owner_user_id},
        ).mappings().all()

    def _candidate_count(self, connection: Any, action: str) -> int:
        return int(connection.scalar(
            text(
                f"""
                SELECT COUNT(*) FROM careertrellis.jobs j
                WHERE j.owner_user_id = :owner_user_id
                  AND ({_postgres_where(action)})
                """
            ),
            {"owner_user_id": self._owner_user_id},
        ) or 0)

    def _set_write_timeouts(self, connection: Any) -> None:
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(text("SET LOCAL statement_timeout = '30s'"))

    def _upsert_suppression(
        self,
        connection: Any,
        row: Mapping[str, Any],
        *,
        deleted_at: datetime,
        deletion_source: str,
    ) -> None:
        values = suppression_values(
            url=str(row["url"]),
            company=row["company"],
            title=row["title"],
            deleted_at=deleted_at,
            deletion_source=deletion_source,
        )
        connection.execute(
            text(
                """
                INSERT INTO careertrellis.job_suppressions (
                    owner_user_id, url_fingerprint, hostname, company, title,
                    deleted_at, deletion_source
                ) VALUES (
                    :owner_user_id, :url_fingerprint, :hostname, :company, :title,
                    :deleted_at, :deletion_source
                )
                ON CONFLICT (owner_user_id, url_fingerprint) DO UPDATE SET
                    hostname = EXCLUDED.hostname, company = EXCLUDED.company,
                    title = EXCLUDED.title, deleted_at = EXCLUDED.deleted_at,
                    deletion_source = EXCLUDED.deletion_source
                """
            ),
            {"owner_user_id": self._owner_user_id, **values},
        )

    def cleanup_preview(self) -> dict[str, Any]:
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            action_rows = {
                action: self._candidate_rows(connection, action)
                for action in ("archive", "delete", "restore")
            }
            action_counts = {
                action: self._candidate_count(connection, action)
                for action in ("archive", "delete", "restore")
            }
            total = int(connection.scalar(text(
                "SELECT COUNT(*) FROM careertrellis.jobs "
                "WHERE owner_user_id = :owner_user_id"
            ), {"owner_user_id": self._owner_user_id}) or 0)
            sample = connection.execute(
                text(
                    f"""
                    SELECT j.id, j.company, j.title, j.date_found
                    FROM careertrellis.jobs j
                    WHERE j.owner_user_id = :owner_user_id
                      AND ({_POSTGRES_ACTIVE_SQL})
                    ORDER BY j.match_score DESC NULLS LAST, j.id LIMIT 5
                    """
                ),
                {"owner_user_id": self._owner_user_id},
            ).mappings().all()
            return {
                "definition": "Matched jobs with no application record or related progress.",
                "actions": {
                    action: {
                        "count": action_counts[action],
                        "preview_token": _preview_token(self._mode_owner, action, rows),
                    }
                    for action, rows in action_rows.items()
                },
                "protected_count": total - action_counts["delete"],
                "sample": [dict(row) for row in sample],
            }

    def apply_cleanup(self, action: str, preview_token: str) -> int:
        safe_action = _action(action)
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            self._set_write_timeouts(connection)
            preliminary = self._candidate_rows(connection, safe_action)
            _require_bounded(preliminary)
            acquire_owner_job_catalog_lock(connection, self._owner_user_id)
            ids = [int(row["id"]) for row in preliminary]
            if ids:
                connection.execute(
                    text(
                        """
                        SELECT id FROM careertrellis.jobs
                        WHERE owner_user_id = :owner_user_id
                          AND id = ANY(CAST(:job_ids AS BIGINT[]))
                        ORDER BY id FOR UPDATE
                        """
                    ),
                    {"owner_user_id": self._owner_user_id, "job_ids": ids},
                ).all()
            rows = self._candidate_rows(connection, safe_action)
            _require_bounded(rows)
            if [int(row["id"]) for row in rows] != ids:
                raise CleanupPreviewChanged(
                    "The job list changed after preview. Refresh the preview before continuing."
                )
            _require_current_preview(
                self._mode_owner, safe_action, rows, preview_token
            )
            if not rows:
                return 0
            ids = [int(row["id"]) for row in rows]
            parameters = {"owner_user_id": self._owner_user_id, "job_ids": ids}
            now = _utc_now()
            if safe_action == "archive":
                cursor = connection.execute(
                    text(
                        """
                        UPDATE careertrellis.jobs
                        SET archived_from_status = status, status = 'archived',
                            archived_at = :now
                        WHERE owner_user_id = :owner_user_id
                          AND id = ANY(CAST(:job_ids AS BIGINT[]))
                        """
                    ),
                    {**parameters, "now": now},
                )
            elif safe_action == "restore":
                cursor = connection.execute(
                    text(
                        """
                        UPDATE careertrellis.jobs
                        SET status = COALESCE(archived_from_status, 'matched'),
                            archived_from_status = NULL, archived_at = NULL
                        WHERE owner_user_id = :owner_user_id
                          AND id = ANY(CAST(:job_ids AS BIGINT[]))
                        """
                    ),
                    parameters,
                )
            else:
                for row in rows:
                    self._upsert_suppression(
                        connection,
                        row,
                        deleted_at=now,
                        deletion_source="bulk_cleanup",
                    )
                cursor = connection.execute(
                    text(
                        """
                        DELETE FROM careertrellis.jobs
                        WHERE owner_user_id = :owner_user_id
                          AND id = ANY(CAST(:job_ids AS BIGINT[]))
                        """
                    ),
                    parameters,
                )
            if cursor.rowcount != len(ids):
                raise CleanupPreviewChanged("The job list changed during cleanup.")
            return len(ids)

    def delete_job(self, job_id: int) -> None:
        safe_job_id = _positive_job_id(job_id)
        parameters = {
            "owner_user_id": self._owner_user_id,
            "job_id": safe_job_id,
        }
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            self._set_write_timeouts(connection)
            acquire_owner_job_catalog_lock(connection, self._owner_user_id)
            job = connection.execute(
                text(
                    """
                    SELECT id, url, company, title FROM careertrellis.jobs
                    WHERE id = :job_id AND owner_user_id = :owner_user_id
                    FOR UPDATE
                    """
                ),
                parameters,
            ).mappings().one_or_none()
            if not job:
                raise JobRetentionNotFound("Job not found.")
            protected = connection.scalar(
                text(
                    """
                    SELECT
                      EXISTS(SELECT 1 FROM careertrellis.applications
                             WHERE job_id = :job_id AND owner_user_id = :owner_user_id)
                      OR EXISTS(SELECT 1 FROM careertrellis.application_status_history
                                WHERE job_id = :job_id AND owner_user_id = :owner_user_id)
                      OR EXISTS(SELECT 1 FROM careertrellis.application_engagements
                                WHERE job_id = :job_id AND owner_user_id = :owner_user_id)
                    """
                ),
                parameters,
            )
            if protected:
                raise JobRetentionProtected(
                    "Jobs with application activity or generated materials cannot be deleted."
                )
            self._upsert_suppression(
                connection,
                job,
                deleted_at=_utc_now(),
                deletion_source="manual",
            )
            cursor = connection.execute(
                text(
                    "DELETE FROM careertrellis.jobs "
                    "WHERE id = :job_id AND owner_user_id = :owner_user_id"
                ),
                parameters,
            )
            if cursor.rowcount != 1:
                raise JobRetentionNotFound("Job not found.")


def runtime_job_retention_repository(
    *,
    sqlite_connection_factory: Callable[[], Any] = get_db_connection,
    engine: Engine | None = None,
) -> JobRetentionRepository:
    """Return the configured repository without caller-selected ownership."""
    if data_store_kind() == LOCAL_DATA_STORE:
        return SQLiteJobRetentionRepository(sqlite_connection_factory)
    return PostgreSQLJobRetentionRepository(current_owner_user_id(), engine=engine)
