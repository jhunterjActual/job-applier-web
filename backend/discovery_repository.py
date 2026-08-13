"""Dual-mode repositories for job discovery and minimized diagnostics.

Standalone installs retain SQLite.  The hosted implementation derives its
owner only from authenticated server context and uses transaction-local
PostgreSQL RLS for every operation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
import json
from typing import Any, Protocol

from sqlalchemy import Engine, text

from data_store import LOCAL_DATA_STORE, data_store_kind
from database import get_db_connection
from job_suppressions import job_url_fingerprint
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id
from source_diagnostics import (
    ALLOWED_CODES,
    ALLOWED_PROVIDERS,
    DEFAULT_HISTORY_LIMIT,
    INFORMATIONAL_CODES,
    MAX_ALERTS_PER_RUN,
    MAX_COUNTER_VALUE,
    MAX_HISTORY_LIMIT,
    MAX_HISTORY_RECORDS,
    STORED_COUNTERS,
    list_source_diagnostics,
    persist_source_diagnostics,
    safe_provider_counters,
)


class DiscoveryRepository(Protocol):
    def list_jobs(self, include_archived: bool = False) -> list[dict[str, Any]]: ...
    def job_identity(self, canonical_url: str) -> dict[str, Any]: ...
    def list_job_suppressions(self) -> dict[str, Any]: ...
    def clear_job_suppression(self, suppression_id: int) -> int: ...
    def clear_all_job_suppressions(self) -> int: ...
    def list_saved_searches(self) -> list[dict[str, Any]]: ...
    def list_due_saved_searches(self, now: datetime) -> list[dict[str, Any]]: ...
    def upsert_saved_search(
        self,
        *,
        name: str,
        keywords: str,
        location: str,
        schedule_frequency: str,
        run_at: datetime,
    ) -> dict[str, Any]: ...
    def advance_saved_search_run(
        self, search_id: int, run_at: datetime
    ) -> dict[str, Any] | None: ...
    def delete_saved_search(self, search_id: int) -> bool: ...
    def persist_source_diagnostics(self, search_result: dict | None, recorded_at: datetime) -> int: ...
    def list_source_diagnostics(self, limit: int = DEFAULT_HISTORY_LIMIT) -> dict[str, Any]: ...
    def clear_source_diagnostics(self) -> int: ...


def _api_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _api_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: _api_value(value) for key, value in row.items()}
    result.pop("owner_user_id", None)
    for boolean_name in ("enabled", "is_expired", "has_materials"):
        if boolean_name in result:
            result[boolean_name] = int(bool(result[boolean_name]))
    return result


SAVED_SEARCH_FREQUENCIES = frozenset({"none", "daily", "weekly"})
MAX_SAVED_SEARCH_NAME_CHARS = 80
MAX_SAVED_SEARCH_KEYWORDS_CHARS = 500
MAX_SAVED_SEARCH_LOCATION_CHARS = 240


def _saved_search_run_at(run_at: datetime) -> datetime:
    if run_at.tzinfo is None or run_at.utcoffset() is None:
        raise ValueError("Saved-search timestamps must include a timezone.")
    return run_at.astimezone(timezone.utc)


def _sqlite_saved_search_timestamp(value: datetime) -> str:
    """Preserve the local, timezone-naive timestamp format used by existing SQLite rows."""
    return (
        _saved_search_run_at(value)
        .astimezone()
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )


def _saved_search_frequency(schedule_frequency: str) -> str:
    if schedule_frequency not in SAVED_SEARCH_FREQUENCIES:
        raise ValueError("Unsupported saved-search schedule frequency.")
    return schedule_frequency


def _saved_search_text(value: str, *, field: str, maximum: int, required: bool) -> str:
    normalized = (value or "").strip()
    if required and not normalized:
        raise ValueError(f"Saved-search {field} is required.")
    if len(normalized) > maximum:
        raise ValueError(f"Saved-search {field} is too long.")
    return normalized


def _next_saved_search_alert(run_at: datetime, schedule_frequency: str) -> datetime | None:
    if schedule_frequency == "daily":
        return run_at + timedelta(days=1)
    if schedule_frequency == "weekly":
        return run_at + timedelta(days=7)
    return None


def _saved_search_values(
    *,
    name: str,
    keywords: str,
    location: str,
    schedule_frequency: str,
    run_at: datetime,
) -> dict[str, Any]:
    normalized_run_at = _saved_search_run_at(run_at)
    frequency = _saved_search_frequency(schedule_frequency)
    return {
        "name": _saved_search_text(
            name, field="name", maximum=MAX_SAVED_SEARCH_NAME_CHARS, required=True
        ),
        "keywords": _saved_search_text(
            keywords,
            field="keywords",
            maximum=MAX_SAVED_SEARCH_KEYWORDS_CHARS,
            required=False,
        ),
        "location": _saved_search_text(
            location,
            field="location",
            maximum=MAX_SAVED_SEARCH_LOCATION_CHARS,
            required=False,
        ),
        "schedule_frequency": frequency,
        "run_at": normalized_run_at,
        "next_alert_at": _next_saved_search_alert(normalized_run_at, frequency),
    }


def _bounded_counter(value: object) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_COUNTER_VALUE, numeric))


def _diagnostic_history(count: int, rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    items = []
    for row in rows:
        stored = row.get("counters", {})
        if isinstance(stored, str):
            try:
                stored = json.loads(stored)
            except (TypeError, ValueError):
                stored = {}
        stored = stored if isinstance(stored, Mapping) else {}
        counters = {name: _bounded_counter(stored.get(name)) for name in STORED_COUNTERS}
        counters["candidate_budget_exhausted"] = int(
            bool(counters["candidate_budget_exhausted"])
        )
        code = str(row.get("diagnostic_code") or "")
        items.append(
            {
                "id": row["id"],
                "recorded_at": _api_value(row["recorded_at"]),
                "provider": row["provider"],
                "code": code,
                "level": "note" if code in INFORMATIONAL_CODES else "attention",
                "counters": counters,
            }
        )
    return {"count": int(count), "items": items}


class SQLiteDiscoveryRepository:
    """Preserve the standalone SQLite discovery behavior."""

    def __init__(self, connection_factory: Callable[[], Any] = get_db_connection):
        self._connection_factory = connection_factory

    def _run(self, action: Callable[[Any], Any], *, write: bool = False) -> Any:
        connection = self._connection_factory()
        try:
            result = action(connection)
            if write:
                connection.commit()
            return result
        except Exception:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def list_jobs(self, include_archived: bool = False) -> list[dict[str, Any]]:
        where_clause = "" if include_archived else "WHERE j.status != 'archived'"
        rows = self._run(
            lambda connection: connection.execute(
                f"""
                SELECT j.*, a.date_applied, a.application_method, a.notes, a.follow_up_date,
                       CASE WHEN a.tailored_resume_path IS NOT NULL OR a.cover_letter IS NOT NULL
                            THEN 1 ELSE 0 END AS has_materials
                FROM jobs j
                LEFT JOIN applications a ON a.job_id = j.id
                {where_clause}
                ORDER BY j.match_score DESC
                """
            ).fetchall()
        )
        return [_api_row(dict(row)) for row in rows]

    def job_identity(self, canonical_url: str) -> dict[str, Any]:
        def load(connection: Any) -> dict[str, Any]:
            suppressed = connection.execute(
                "SELECT 1 FROM job_suppressions WHERE url_fingerprint = ?",
                (job_url_fingerprint(canonical_url),),
            ).fetchone()
            existing = connection.execute(
                "SELECT id, title, company, status FROM jobs WHERE url = ?",
                (canonical_url,),
            ).fetchone()
            return {
                "suppressed": suppressed is not None,
                "existing": dict(existing) if existing else None,
            }

        return self._run(load)

    def list_job_suppressions(self) -> dict[str, Any]:
        def load(connection: Any) -> dict[str, Any]:
            count = connection.execute("SELECT COUNT(*) FROM job_suppressions").fetchone()[0]
            rows = connection.execute(
                """
                SELECT id, hostname, company, title, deleted_at, deletion_source
                FROM job_suppressions
                ORDER BY deleted_at DESC, id DESC
                LIMIT 100
                """
            ).fetchall()
            return {"count": int(count), "items": [_api_row(dict(row)) for row in rows]}

        return self._run(load)

    def clear_job_suppression(self, suppression_id: int) -> int:
        return self._run(
            lambda connection: connection.execute(
                "DELETE FROM job_suppressions WHERE id = ?", (suppression_id,)
            ).rowcount,
            write=True,
        )

    def clear_all_job_suppressions(self) -> int:
        return self._run(
            lambda connection: connection.execute("DELETE FROM job_suppressions").rowcount,
            write=True,
        )

    def list_saved_searches(self) -> list[dict[str, Any]]:
        rows = self._run(
            lambda connection: connection.execute(
                "SELECT * FROM saved_searches WHERE enabled = 1 ORDER BY name COLLATE NOCASE"
            ).fetchall()
        )
        return [_api_row(dict(row)) for row in rows]

    def list_due_saved_searches(self, now: datetime) -> list[dict[str, Any]]:
        rows = self._run(
            lambda connection: connection.execute(
                """
                SELECT id, name, keywords, location, schedule_frequency, next_alert_at
                FROM saved_searches
                WHERE enabled = 1 AND schedule_frequency != 'none'
                  AND next_alert_at IS NOT NULL AND next_alert_at <= ?
                ORDER BY next_alert_at
                """,
                (_sqlite_saved_search_timestamp(now),),
            ).fetchall()
        )
        return [_api_row(dict(row)) for row in rows]

    def upsert_saved_search(
        self,
        *,
        name: str,
        keywords: str,
        location: str,
        schedule_frequency: str,
        run_at: datetime,
    ) -> dict[str, Any]:
        values = _saved_search_values(
            name=name,
            keywords=keywords,
            location=location,
            schedule_frequency=schedule_frequency,
            run_at=run_at,
        )

        def upsert(connection: Any) -> dict[str, Any]:
            run_at = _sqlite_saved_search_timestamp(values["run_at"])
            next_alert_at = (
                _sqlite_saved_search_timestamp(values["next_alert_at"])
                if values["next_alert_at"]
                else None
            )
            connection.execute(
                """
                INSERT INTO saved_searches (
                    name, keywords, location, created_at, last_run_at,
                    schedule_frequency, next_alert_at, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(keywords, location) DO UPDATE SET
                    name = excluded.name,
                    last_run_at = excluded.last_run_at,
                    schedule_frequency = excluded.schedule_frequency,
                    next_alert_at = excluded.next_alert_at,
                    enabled = 1
                """,
                (
                    values["name"],
                    values["keywords"],
                    values["location"],
                    run_at,
                    run_at,
                    values["schedule_frequency"],
                    next_alert_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM saved_searches WHERE keywords = ? AND location = ?",
                (values["keywords"], values["location"]),
            ).fetchone()
            return _api_row(dict(row))

        return self._run(upsert, write=True)

    def advance_saved_search_run(
        self, search_id: int, run_at: datetime
    ) -> dict[str, Any] | None:
        if search_id <= 0:
            return None
        normalized_run_at = _saved_search_run_at(run_at)

        def advance(connection: Any) -> dict[str, Any] | None:
            row = connection.execute(
                "SELECT * FROM saved_searches WHERE id = ? AND enabled = 1",
                (search_id,),
            ).fetchone()
            if not row:
                return None
            frequency = _saved_search_frequency(row["schedule_frequency"] or "none")
            next_alert_at = _next_saved_search_alert(normalized_run_at, frequency)
            connection.execute(
                """
                UPDATE saved_searches
                SET last_run_at = ?, next_alert_at = ?
                WHERE id = ? AND enabled = 1
                """,
                (
                    _sqlite_saved_search_timestamp(normalized_run_at),
                    _sqlite_saved_search_timestamp(next_alert_at) if next_alert_at else None,
                    search_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM saved_searches WHERE id = ? AND enabled = 1",
                (search_id,),
            ).fetchone()
            return _api_row(dict(updated)) if updated else None

        return self._run(advance, write=True)

    def delete_saved_search(self, search_id: int) -> bool:
        return bool(
            self._run(
                lambda connection: connection.execute(
                    "DELETE FROM saved_searches WHERE id = ?", (search_id,)
                ).rowcount,
                write=True,
            )
        )

    def persist_source_diagnostics(self, search_result: dict | None, recorded_at: datetime) -> int:
        return self._run(
            lambda connection: persist_source_diagnostics(
                connection, search_result, recorded_at.isoformat(timespec="seconds")
            ),
            write=True,
        )

    def list_source_diagnostics(self, limit: int = DEFAULT_HISTORY_LIMIT) -> dict[str, Any]:
        return self._run(lambda connection: list_source_diagnostics(connection, limit))

    def clear_source_diagnostics(self) -> int:
        return self._run(
            lambda connection: connection.execute("DELETE FROM source_diagnostics").rowcount,
            write=True,
        )


class PostgreSQLDiscoveryRepository:
    """Owner-scoped PostgreSQL discovery operations under forced RLS."""

    def __init__(self, owner_user_id: int, engine: Engine | None = None):
        self._owner_user_id = owner_user_id
        self._engine = engine or runtime_postgres_engine()

    def _run(self, action: Callable[[Any], Any]) -> Any:
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            return action(connection)

    def list_jobs(self, include_archived: bool = False) -> list[dict[str, Any]]:
        def load(connection: Any) -> list[dict[str, Any]]:
            rows = connection.execute(
                text(
                    """
                    SELECT j.id, j.title, j.company, j.description, j.url,
                           j.match_score, j.match_analysis, j.date_found, j.status,
                           j.archived_at, j.archived_from_status, j.last_checked_at,
                           j.is_expired, j.expiration_reason, j.location,
                           j.work_arrangement, j.employment_type, j.compensation, j.source,
                           a.date_applied, a.application_method, a.notes, a.follow_up_date,
                           CASE WHEN a.tailored_resume_object_id IS NOT NULL
                                  OR a.cover_letter_object_id IS NOT NULL
                                  OR NULLIF(a.tailored_resume_text, '') IS NOT NULL
                                  OR NULLIF(a.cover_letter, '') IS NOT NULL
                                THEN TRUE ELSE FALSE END AS has_materials
                    FROM careertrellis.jobs AS j
                    LEFT JOIN careertrellis.applications AS a
                      ON a.job_id = j.id AND a.owner_user_id = j.owner_user_id
                    WHERE j.owner_user_id = :owner_user_id
                      AND (:include_archived OR j.status != 'archived')
                    ORDER BY j.match_score DESC NULLS LAST, j.id DESC
                    """
                ),
                {
                    "owner_user_id": self._owner_user_id,
                    "include_archived": bool(include_archived),
                },
            ).mappings().all()
            return [_api_row(row) for row in rows]

        return self._run(load)

    def job_identity(self, canonical_url: str) -> dict[str, Any]:
        def load(connection: Any) -> dict[str, Any]:
            suppressed = connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM careertrellis.job_suppressions
                        WHERE owner_user_id = :owner_user_id
                          AND url_fingerprint = :fingerprint
                    )
                    """
                ),
                {
                    "owner_user_id": self._owner_user_id,
                    "fingerprint": job_url_fingerprint(canonical_url),
                },
            )
            existing = connection.execute(
                text(
                    """
                    SELECT id, title, company, status
                    FROM careertrellis.jobs
                    WHERE owner_user_id = :owner_user_id AND url = :url
                    """
                ),
                {"owner_user_id": self._owner_user_id, "url": canonical_url},
            ).mappings().one_or_none()
            return {
                "suppressed": bool(suppressed),
                "existing": _api_row(existing) if existing else None,
            }

        return self._run(load)

    def list_job_suppressions(self) -> dict[str, Any]:
        def load(connection: Any) -> dict[str, Any]:
            count = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM careertrellis.job_suppressions "
                    "WHERE owner_user_id = :owner_user_id"
                ),
                {"owner_user_id": self._owner_user_id},
            )
            rows = connection.execute(
                text(
                    """
                    SELECT id, hostname, company, title, deleted_at, deletion_source
                    FROM careertrellis.job_suppressions
                    WHERE owner_user_id = :owner_user_id
                    ORDER BY deleted_at DESC, id DESC
                    LIMIT 100
                    """
                ),
                {"owner_user_id": self._owner_user_id},
            ).mappings().all()
            return {"count": int(count or 0), "items": [_api_row(row) for row in rows]}

        return self._run(load)

    def clear_job_suppression(self, suppression_id: int) -> int:
        return self._run(
            lambda connection: connection.execute(
                text(
                    "DELETE FROM careertrellis.job_suppressions "
                    "WHERE id = :suppression_id AND owner_user_id = :owner_user_id"
                ),
                {"suppression_id": suppression_id, "owner_user_id": self._owner_user_id},
            ).rowcount
        )

    def clear_all_job_suppressions(self) -> int:
        return self._run(
            lambda connection: connection.execute(
                text(
                    "DELETE FROM careertrellis.job_suppressions "
                    "WHERE owner_user_id = :owner_user_id"
                ),
                {"owner_user_id": self._owner_user_id},
            ).rowcount
        )

    def list_saved_searches(self) -> list[dict[str, Any]]:
        def load(connection: Any) -> list[dict[str, Any]]:
            rows = connection.execute(
                text(
                    """
                    SELECT id, name, keywords, location, created_at, last_run_at,
                           schedule_frequency, next_alert_at, enabled
                    FROM careertrellis.saved_searches
                    WHERE owner_user_id = :owner_user_id AND enabled IS TRUE
                    ORDER BY lower(name), id
                    """
                ),
                {"owner_user_id": self._owner_user_id},
            ).mappings().all()
            return [_api_row(row) for row in rows]

        return self._run(load)

    def list_due_saved_searches(self, now: datetime) -> list[dict[str, Any]]:
        now = _saved_search_run_at(now)
        def load(connection: Any) -> list[dict[str, Any]]:
            rows = connection.execute(
                text(
                    """
                    SELECT id, name, keywords, location, schedule_frequency, next_alert_at
                    FROM careertrellis.saved_searches
                    WHERE owner_user_id = :owner_user_id AND enabled IS TRUE
                      AND schedule_frequency != 'none'
                      AND next_alert_at IS NOT NULL AND next_alert_at <= :now
                    ORDER BY next_alert_at, id
                    """
                ),
                {"owner_user_id": self._owner_user_id, "now": now},
            ).mappings().all()
            return [_api_row(row) for row in rows]

        return self._run(load)

    def upsert_saved_search(
        self,
        *,
        name: str,
        keywords: str,
        location: str,
        schedule_frequency: str,
        run_at: datetime,
    ) -> dict[str, Any]:
        values = _saved_search_values(
            name=name,
            keywords=keywords,
            location=location,
            schedule_frequency=schedule_frequency,
            run_at=run_at,
        )

        def upsert(connection: Any) -> dict[str, Any]:
            row = connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.saved_searches AS saved (
                        owner_user_id, name, keywords, location, last_run_at,
                        schedule_frequency, next_alert_at, enabled
                    ) VALUES (
                        :owner_user_id, :name, :keywords, :location, :run_at,
                        :schedule_frequency, :next_alert_at, TRUE
                    )
                    ON CONFLICT (owner_user_id, keywords, location) DO UPDATE
                    SET name = CASE
                            WHEN saved.last_run_at IS NULL
                              OR EXCLUDED.last_run_at >= saved.last_run_at
                            THEN EXCLUDED.name
                            ELSE saved.name
                        END,
                        last_run_at = GREATEST(
                            COALESCE(saved.last_run_at, '-infinity'::timestamptz),
                            EXCLUDED.last_run_at
                        ),
                        schedule_frequency = CASE
                            WHEN saved.last_run_at IS NULL
                              OR EXCLUDED.last_run_at >= saved.last_run_at
                            THEN EXCLUDED.schedule_frequency
                            ELSE saved.schedule_frequency
                        END,
                        next_alert_at = CASE
                            WHEN saved.last_run_at IS NULL
                              OR EXCLUDED.last_run_at >= saved.last_run_at
                            THEN EXCLUDED.next_alert_at
                            ELSE saved.next_alert_at
                        END,
                        enabled = TRUE
                    WHERE saved.owner_user_id = :owner_user_id
                    RETURNING id, name, keywords, location, created_at, last_run_at,
                              schedule_frequency, next_alert_at, enabled
                    """
                ),
                {"owner_user_id": self._owner_user_id, **values},
            ).mappings().one()
            return _api_row(row)

        return self._run(upsert)

    def advance_saved_search_run(
        self, search_id: int, run_at: datetime
    ) -> dict[str, Any] | None:
        if search_id <= 0:
            return None
        normalized_run_at = _saved_search_run_at(run_at)

        def advance(connection: Any) -> dict[str, Any] | None:
            row = connection.execute(
                text(
                    """
                    UPDATE careertrellis.saved_searches AS saved
                    SET last_run_at = GREATEST(
                            COALESCE(saved.last_run_at, '-infinity'::timestamptz),
                            :run_at
                        ),
                        next_alert_at = CASE saved.schedule_frequency
                            WHEN 'daily' THEN GREATEST(
                                COALESCE(saved.last_run_at, '-infinity'::timestamptz),
                                :run_at
                            ) + INTERVAL '1 day'
                            WHEN 'weekly' THEN GREATEST(
                                COALESCE(saved.last_run_at, '-infinity'::timestamptz),
                                :run_at
                            ) + INTERVAL '7 days'
                            ELSE NULL
                        END
                    WHERE saved.id = :search_id
                      AND saved.owner_user_id = :owner_user_id
                      AND saved.enabled IS TRUE
                    RETURNING id, name, keywords, location, created_at, last_run_at,
                              schedule_frequency, next_alert_at, enabled
                    """
                ),
                {
                    "search_id": search_id,
                    "owner_user_id": self._owner_user_id,
                    "run_at": normalized_run_at,
                },
            ).mappings().one_or_none()
            return _api_row(row) if row else None

        return self._run(advance)

    def delete_saved_search(self, search_id: int) -> bool:
        return bool(
            self._run(
                lambda connection: connection.execute(
                    text(
                        "DELETE FROM careertrellis.saved_searches "
                        "WHERE id = :search_id AND owner_user_id = :owner_user_id"
                    ),
                    {"search_id": search_id, "owner_user_id": self._owner_user_id},
                ).rowcount
            )
        )

    def persist_source_diagnostics(self, search_result: dict | None, recorded_at: datetime) -> int:
        if not isinstance(search_result, dict):
            return 0
        alerts = search_result.get("provider_alerts")
        health_by_provider = search_result.get("provider_health")
        if not isinstance(alerts, list) or not isinstance(health_by_provider, dict):
            return 0

        def persist(connection: Any) -> int:
            # Serialize one owner's retention decision so concurrent completed
            # searches cannot collectively exceed the privacy-bounded history.
            connection.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:diagnostic_lock_name, 0))"
                ),
                {
                    "diagnostic_lock_name": (
                        f"careertrellis-source-diagnostics:{self._owner_user_id}"
                    )
                },
            )
            inserted = 0
            for alert in alerts[:MAX_ALERTS_PER_RUN]:
                if not isinstance(alert, dict):
                    continue
                provider = str(alert.get("provider") or "").lower()
                code = str(alert.get("code") or "").lower()
                if provider not in ALLOWED_PROVIDERS or code not in ALLOWED_CODES:
                    continue
                counters = safe_provider_counters(health_by_provider.get(provider))
                connection.execute(
                    text(
                        """
                        INSERT INTO careertrellis.source_diagnostics
                            (owner_user_id, recorded_at, provider, diagnostic_code, counters)
                        VALUES
                            (:owner_user_id, :recorded_at, :provider, :code,
                             CAST(:counters AS jsonb))
                        """
                    ),
                    {
                        "owner_user_id": self._owner_user_id,
                        "recorded_at": recorded_at,
                        "provider": provider,
                        "code": code,
                        "counters": json.dumps(counters, sort_keys=True, separators=(",", ":")),
                    },
                )
                inserted += 1
            if inserted:
                connection.execute(
                    text(
                        """
                        DELETE FROM careertrellis.source_diagnostics
                        WHERE owner_user_id = :owner_user_id
                          AND id NOT IN (
                              SELECT id FROM careertrellis.source_diagnostics
                              WHERE owner_user_id = :owner_user_id
                              ORDER BY recorded_at DESC, id DESC
                              LIMIT :retention_limit
                          )
                        """
                    ),
                    {
                        "owner_user_id": self._owner_user_id,
                        "retention_limit": MAX_HISTORY_RECORDS,
                    },
                )
            return inserted

        return self._run(persist)

    def list_source_diagnostics(self, limit: int = DEFAULT_HISTORY_LIMIT) -> dict[str, Any]:
        safe_limit = max(1, min(MAX_HISTORY_LIMIT, int(limit)))

        def load(connection: Any) -> dict[str, Any]:
            count = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM careertrellis.source_diagnostics "
                    "WHERE owner_user_id = :owner_user_id"
                ),
                {"owner_user_id": self._owner_user_id},
            )
            rows = connection.execute(
                text(
                    """
                    SELECT id, recorded_at, provider, diagnostic_code, counters
                    FROM careertrellis.source_diagnostics
                    WHERE owner_user_id = :owner_user_id
                    ORDER BY recorded_at DESC, id DESC
                    LIMIT :history_limit
                    """
                ),
                {
                    "owner_user_id": self._owner_user_id,
                    "history_limit": safe_limit,
                },
            ).mappings().all()
            return _diagnostic_history(int(count or 0), list(rows))

        return self._run(load)

    def clear_source_diagnostics(self) -> int:
        return self._run(
            lambda connection: connection.execute(
                text(
                    "DELETE FROM careertrellis.source_diagnostics "
                    "WHERE owner_user_id = :owner_user_id"
                ),
                {"owner_user_id": self._owner_user_id},
            ).rowcount
        )


def runtime_discovery_repository(
    *,
    sqlite_connection_factory: Callable[[], Any] = get_db_connection,
    engine: Engine | None = None,
) -> DiscoveryRepository:
    """Return the configured repository without request-selected ownership."""
    if data_store_kind() == LOCAL_DATA_STORE:
        return SQLiteDiscoveryRepository(sqlite_connection_factory)
    return PostgreSQLDiscoveryRepository(current_owner_user_id(), engine=engine)
