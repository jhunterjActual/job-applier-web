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
from generated_material_repository import PostgreSQLGeneratedMaterialRepository
from job_suppressions import job_url_fingerprint, record_job_suppression, suppression_values
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


MAX_RETENTION_BATCH = 500
MAX_RETENTION_DEPENDENT_ITEMS = 5000
DEPENDENT_DELETE_ACTION = "delete_with_dependents"
_PREVIEW_HMAC_KEY = secrets.token_bytes(32)
_LOCAL_ACTIONS = frozenset({"archive", "delete", "restore"})
_ACTIONS = frozenset({*_LOCAL_ACTIONS, DEPENDENT_DELETE_ACTION})

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
_POSTGRES_DEPENDENT_SQL = """
    (
        j.status = 'matched'
        OR (
            j.status = 'archived'
            AND COALESCE(j.archived_from_status, 'matched') = 'matched'
        )
    )
    AND (
        EXISTS (
            SELECT 1 FROM careertrellis.applications a
            WHERE a.job_id = j.id AND a.owner_user_id = :owner_user_id
        )
        OR EXISTS (
            SELECT 1 FROM careertrellis.application_status_history h
            WHERE h.job_id = j.id AND h.owner_user_id = :owner_user_id
        )
        OR EXISTS (
            SELECT 1 FROM careertrellis.application_engagements e
            WHERE e.job_id = j.id AND e.owner_user_id = :owner_user_id
        )
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


class DependentCleanupUnavailable(ValueError):
    """Raised when standalone mode is asked to delete protected work in bulk."""


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


def _stable_list(value: Any) -> list[Any]:
    """Return JSON-stable scalar identities without exposing them in responses."""
    if value is None:
        return []
    return [item if isinstance(item, (int, float, bool)) else str(item) for item in value]


def _snapshot_rows(action: str, rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    snapshot = []
    for row in rows:
        item = {
            "id": int(row["id"]),
            "status": str(row["status"]),
            "archived_from_status": row["archived_from_status"],
        }
        if action in {"delete", DEPENDENT_DELETE_ACTION}:
            item["url_fingerprint"] = job_url_fingerprint(str(row["url"]))
        if action == DEPENDENT_DELETE_ACTION:
            for key in (
                "application_ids",
                "history_ids",
                "engagement_ids",
                "application_object_refs",
                "object_ids",
                "survivor_object_refs",
            ):
                item[key] = _stable_list(row[key])
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


def _require_dependent_work_bounded(rows: list[Mapping[str, Any]]) -> None:
    row_items = sum(
        len(row[key] or ())
        for row in rows
        for key in ("application_ids", "history_ids", "engagement_ids")
    )
    object_refs = sum(
        1
        for row in rows
        for key in ("application_object_refs", "survivor_object_refs")
        for encoded in (row[key] or ())
        for object_id in str(encoded).split(":")[1:]
        if object_id
    )
    total = row_items + object_refs
    if total > MAX_RETENTION_DEPENDENT_ITEMS:
        raise CleanupBatchTooLarge(
            "The dependent cleanup contains too much saved work for one transaction."
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
    if action == DEPENDENT_DELETE_ACTION:
        return _POSTGRES_DEPENDENT_SQL
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
                    **{
                        action: {
                            "count": action_counts[action],
                            "preview_token": _preview_token(
                                self._MODE_OWNER, action, rows
                            ),
                        }
                        for action, rows in action_rows.items()
                    },
                    DEPENDENT_DELETE_ACTION: {
                        "available": False,
                        "count": 0,
                        "preview_token": _preview_token(
                            self._MODE_OWNER, DEPENDENT_DELETE_ACTION, []
                        ),
                    },
                },
                "protected_count": total - action_counts["delete"],
                "sample": [dict(row) for row in sample],
            }
        finally:
            connection.close()

    def apply_cleanup(self, action: str, preview_token: str) -> int:
        safe_action = _action(action)
        if safe_action not in _LOCAL_ACTIONS:
            raise DependentCleanupUnavailable(
                "Dependent bulk deletion is unavailable in standalone mode."
            )
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
        if action == DEPENDENT_DELETE_ACTION:
            return self._dependent_candidate_rows(connection)
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

    def _dependent_candidate_rows(self, connection: Any) -> list[Mapping[str, Any]]:
        """Snapshot the exact protected graph used to authorize a bulk deletion."""
        return connection.execute(
            text(
                f"""
                SELECT j.id, j.status, j.archived_from_status, j.url,
                       j.company, j.title,
                       ARRAY(
                           SELECT a.id FROM careertrellis.applications a
                           WHERE a.owner_user_id = :owner_user_id
                             AND a.job_id = j.id
                           ORDER BY a.id
                           LIMIT {MAX_RETENTION_DEPENDENT_ITEMS + 1}
                       ) AS application_ids,
                       ARRAY(
                           SELECT h.id FROM careertrellis.application_status_history h
                           WHERE h.owner_user_id = :owner_user_id
                             AND h.job_id = j.id
                           ORDER BY h.id
                           LIMIT {MAX_RETENTION_DEPENDENT_ITEMS + 1}
                       ) AS history_ids,
                       ARRAY(
                           SELECT e.id FROM careertrellis.application_engagements e
                           WHERE e.owner_user_id = :owner_user_id
                             AND e.job_id = j.id
                           ORDER BY e.id
                           LIMIT {MAX_RETENTION_DEPENDENT_ITEMS + 1}
                       ) AS engagement_ids,
                       ARRAY(
                           SELECT pg_catalog.concat_ws(
                               ':', a.id::text,
                               COALESCE(a.tailored_resume_object_id::text, ''),
                               COALESCE(a.cover_letter_object_id::text, '')
                           )
                           FROM careertrellis.applications a
                           WHERE a.owner_user_id = :owner_user_id
                             AND a.job_id = j.id
                           ORDER BY a.id
                           LIMIT {MAX_RETENTION_DEPENDENT_ITEMS + 1}
                       ) AS application_object_refs,
                       ARRAY(
                           SELECT DISTINCT object_id
                           FROM careertrellis.applications target
                           CROSS JOIN LATERAL pg_catalog.unnest(ARRAY[
                               target.tailored_resume_object_id,
                               target.cover_letter_object_id
                           ]) AS object_ref(object_id)
                           WHERE target.owner_user_id = :owner_user_id
                             AND target.job_id = j.id
                             AND object_id IS NOT NULL
                           ORDER BY object_id
                           LIMIT {MAX_RETENTION_DEPENDENT_ITEMS + 1}
                       ) AS object_ids,
                       ARRAY(
                           SELECT pg_catalog.concat_ws(
                               ':', survivor.id::text,
                               COALESCE(survivor.tailored_resume_object_id::text, ''),
                               COALESCE(survivor.cover_letter_object_id::text, '')
                           )
                           FROM careertrellis.applications survivor
                           WHERE survivor.owner_user_id = :owner_user_id
                             AND survivor.job_id <> j.id
                             AND EXISTS (
                                 SELECT 1
                                 FROM careertrellis.applications target
                                 WHERE target.owner_user_id = :owner_user_id
                                   AND target.job_id = j.id
                                   AND (
                                       survivor.tailored_resume_object_id IN (
                                           target.tailored_resume_object_id,
                                           target.cover_letter_object_id
                                       )
                                       OR survivor.cover_letter_object_id IN (
                                           target.tailored_resume_object_id,
                                           target.cover_letter_object_id
                                       )
                                   )
                           )
                           ORDER BY survivor.id
                           LIMIT {MAX_RETENTION_DEPENDENT_ITEMS + 1}
                       ) AS survivor_object_refs
                FROM careertrellis.jobs j
                WHERE j.owner_user_id = :owner_user_id
                  AND ({_POSTGRES_DEPENDENT_SQL})
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

    def _lock_batch_deletion_graph(
        self,
        connection: Any,
        job_ids: list[int],
    ) -> list[Any]:
        """Lock applications, survivor references, then object metadata."""
        if not job_ids:
            return []
        parameters = {"owner_user_id": self._owner_user_id, "job_ids": job_ids}
        applications = connection.execute(
            text(
                """
                SELECT id, job_id, tailored_resume_object_id,
                       cover_letter_object_id
                FROM careertrellis.applications
                WHERE owner_user_id = :owner_user_id
                  AND job_id = ANY(CAST(:job_ids AS BIGINT[]))
                ORDER BY job_id, id
                FOR UPDATE
                """
            ),
            parameters,
        ).mappings().all()
        object_ids = list(dict.fromkeys(
            object_id
            for application in applications
            for object_id in (
                application["tailored_resume_object_id"],
                application["cover_letter_object_id"],
            )
            if object_id is not None
        ))
        if not object_ids:
            return []
        object_ids.sort(key=str)
        graph_parameters = {**parameters, "object_ids": object_ids}
        survivors = connection.execute(
            text(
                """
                SELECT id, tailored_resume_object_id, cover_letter_object_id
                FROM careertrellis.applications
                WHERE owner_user_id = :owner_user_id
                  AND NOT (job_id = ANY(CAST(:job_ids AS BIGINT[])))
                  AND (
                      tailored_resume_object_id = ANY(CAST(:object_ids AS uuid[]))
                      OR cover_letter_object_id = ANY(CAST(:object_ids AS uuid[]))
                  )
                ORDER BY id
                FOR UPDATE
                """
            ),
            graph_parameters,
        ).mappings().all()
        shared_object_ids = {
            object_id
            for row in survivors
            for object_id in (
                row["tailored_resume_object_id"],
                row["cover_letter_object_id"],
            )
            if object_id in object_ids
        }
        locked_objects = connection.execute(
            text(
                """
                SELECT id FROM careertrellis.stored_objects
                WHERE owner_user_id = :owner_user_id
                  AND id = ANY(CAST(:object_ids AS uuid[]))
                ORDER BY id
                FOR UPDATE
                """
            ),
            graph_parameters,
        ).scalars().all()
        if {str(item) for item in locked_objects} != {str(item) for item in object_ids}:
            raise RuntimeError("Generated-material metadata is unavailable.")
        return [
            object_id for object_id in object_ids if object_id not in shared_object_ids
        ]

    def _delete_locked_rows(
        self,
        connection: Any,
        rows: list[Mapping[str, Any]],
        *,
        exclusive_object_ids: list[Any],
        deletion_source: str,
        single_job_id: int | None = None,
    ) -> bool:
        """Atomically suppress, tombstone, and delete an already locked graph."""
        now = _utc_now()
        for row in rows:
            self._upsert_suppression(
                connection,
                row,
                deleted_at=now,
                deletion_source=deletion_source,
            )
        if exclusive_object_ids:
            tombstoned = connection.execute(
                text(
                    """
                    UPDATE careertrellis.stored_objects
                    SET deleted_at = CURRENT_TIMESTAMP
                    WHERE owner_user_id = :owner_user_id
                      AND id = ANY(CAST(:object_ids AS uuid[]))
                    """
                ),
                {
                    "owner_user_id": self._owner_user_id,
                    "object_ids": exclusive_object_ids,
                },
            ).rowcount
            if tombstoned != len(exclusive_object_ids):
                raise RuntimeError("Generated-material metadata is unavailable.")
        if single_job_id is not None:
            cursor = connection.execute(
                text(
                    "DELETE FROM careertrellis.jobs "
                    "WHERE id = :job_id AND owner_user_id = :owner_user_id"
                ),
                {"job_id": single_job_id, "owner_user_id": self._owner_user_id},
            )
        else:
            cursor = connection.execute(
                text(
                    """
                    DELETE FROM careertrellis.jobs
                    WHERE owner_user_id = :owner_user_id
                      AND id = ANY(CAST(:job_ids AS BIGINT[]))
                    """
                ),
                {
                    "owner_user_id": self._owner_user_id,
                    "job_ids": [int(row["id"]) for row in rows],
                },
            )
        if single_job_id is not None and cursor.rowcount != 1:
            raise JobRetentionNotFound("Job not found.")
        if single_job_id is None and cursor.rowcount != len(rows):
            raise CleanupPreviewChanged("The job list changed during cleanup.")
        return bool(exclusive_object_ids)

    def _reconcile_deleted_objects(self) -> None:
        """Start ordinary grace-preserving blob reconciliation after commit only."""
        try:
            PostgreSQLGeneratedMaterialRepository(
                self._owner_user_id,
                engine=self._engine,
            ).reconcile_deleted_objects()
        except Exception:
            pass

    def cleanup_preview(self) -> dict[str, Any]:
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            action_rows = {
                action: self._candidate_rows(connection, action)
                for action in (
                    "archive",
                    "delete",
                    "restore",
                    DEPENDENT_DELETE_ACTION,
                )
            }
            action_counts = {
                action: self._candidate_count(connection, action)
                for action in (
                    "archive",
                    "delete",
                    "restore",
                    DEPENDENT_DELETE_ACTION,
                )
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
                        **(
                            {"available": True}
                            if action == DEPENDENT_DELETE_ACTION
                            else {}
                        ),
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
        has_material_objects = False
        affected = 0
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            self._set_write_timeouts(connection)
            preliminary = self._candidate_rows(connection, safe_action)
            _require_bounded(preliminary)
            if safe_action == DEPENDENT_DELETE_ACTION:
                _require_dependent_work_bounded(preliminary)
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
            exclusive_object_ids = []
            if safe_action == DEPENDENT_DELETE_ACTION:
                exclusive_object_ids = self._lock_batch_deletion_graph(
                    connection, ids
                )
            rows = self._candidate_rows(connection, safe_action)
            _require_bounded(rows)
            if safe_action == DEPENDENT_DELETE_ACTION:
                _require_dependent_work_bounded(rows)
            if [int(row["id"]) for row in rows] != ids:
                raise CleanupPreviewChanged(
                    "The job list changed after preview. Refresh the preview before continuing."
                )
            _require_current_preview(
                self._mode_owner, safe_action, rows, preview_token
            )
            if not rows:
                affected = 0
            elif safe_action == DEPENDENT_DELETE_ACTION:
                has_material_objects = self._delete_locked_rows(
                    connection,
                    rows,
                    exclusive_object_ids=exclusive_object_ids,
                    deletion_source="dependent_bulk_cleanup",
                )
                affected = len(rows)
            else:
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
                affected = len(ids)
        if has_material_objects:
            self._reconcile_deleted_objects()
        return affected

    def delete_job(self, job_id: int) -> None:
        safe_job_id = _positive_job_id(job_id)
        parameters = {
            "owner_user_id": self._owner_user_id,
            "job_id": safe_job_id,
        }
        has_material_objects = False
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

            application = connection.execute(
                text(
                    """
                    SELECT id, tailored_resume_object_id, cover_letter_object_id
                    FROM careertrellis.applications
                    WHERE job_id = :job_id AND owner_user_id = :owner_user_id
                    FOR UPDATE
                    """
                ),
                parameters,
            ).mappings().one_or_none()
            object_ids = []
            if application is not None:
                object_ids = list(
                    dict.fromkeys(
                        object_id
                        for object_id in (
                            application["tailored_resume_object_id"],
                            application["cover_letter_object_id"],
                        )
                        if object_id is not None
                    )
                )
            exclusive_object_ids = object_ids
            if object_ids:
                referencing_applications = connection.execute(
                    text(
                        """
                        SELECT id, tailored_resume_object_id, cover_letter_object_id
                        FROM careertrellis.applications
                        WHERE owner_user_id = :owner_user_id
                          AND id <> :application_id
                          AND (
                              tailored_resume_object_id = ANY(CAST(:object_ids AS uuid[]))
                              OR cover_letter_object_id = ANY(CAST(:object_ids AS uuid[]))
                          )
                        ORDER BY id
                        FOR UPDATE
                        """
                    ),
                    {
                        **parameters,
                        "application_id": application["id"],
                        "object_ids": object_ids,
                    },
                ).mappings().all()
                shared_object_ids = {
                    object_id
                    for row in referencing_applications
                    for object_id in (
                        row["tailored_resume_object_id"],
                        row["cover_letter_object_id"],
                    )
                    if object_id in object_ids
                }
                exclusive_object_ids = [
                    object_id
                    for object_id in object_ids
                    if object_id not in shared_object_ids
                ]
                locked_objects = connection.execute(
                    text(
                        """
                        SELECT id FROM careertrellis.stored_objects
                        WHERE owner_user_id = :owner_user_id
                          AND id = ANY(CAST(:object_ids AS uuid[]))
                        ORDER BY id
                        FOR UPDATE
                        """
                    ),
                    {**parameters, "object_ids": object_ids},
                ).scalars().all()
                if len(locked_objects) != len(object_ids):
                    raise RuntimeError("Generated-material metadata is unavailable.")
            has_material_objects = self._delete_locked_rows(
                connection,
                [job],
                exclusive_object_ids=exclusive_object_ids,
                deletion_source="manual",
                single_job_id=safe_job_id,
            )

        # The owner-scoped database deletion is already committed. Blob
        # cleanup is best effort: ordinary reconciliation preserves the
        # authorized-download grace period, while any backend/configuration
        # failure leaves durable tombstones for a later pass and must not turn
        # a successful delete into an error response.
        if has_material_objects:
            self._reconcile_deleted_objects()


def runtime_job_retention_repository(
    *,
    sqlite_connection_factory: Callable[[], Any] = get_db_connection,
    engine: Engine | None = None,
) -> JobRetentionRepository:
    """Return the configured repository without caller-selected ownership."""
    if data_store_kind() == LOCAL_DATA_STORE:
        return SQLiteJobRetentionRepository(sqlite_connection_factory)
    return PostgreSQLJobRetentionRepository(current_owner_user_id(), engine=engine)
