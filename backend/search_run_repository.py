"""Durable owner-scoped search execution state for SQLite and PostgreSQL."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from data_store import LOCAL_DATA_STORE, data_store_kind
from database import get_db_connection
from operations import OperationCancelled, normalized_operation_id
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


ACTIVE_STATUSES = ("queued", "running", "cancel_requested")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")
MAX_KEYWORDS_CHARS = 500
MAX_LOCATION_CHARS = 240
MAX_RESULT_JSON_BYTES = 16 * 1024
MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 300
MIN_LEASE_SECONDS = 5
MAX_LEASE_SECONDS = 900
SEARCH_FAILED_MESSAGE = "The job search could not be completed."
SEARCH_CANCELLED_MESSAGE = (
    "Search stopped. Any jobs committed before the stop request were kept."
)
ERROR_CODES = frozenset({"search_failed", "provider_search_failed", "attempts_exhausted"})


class SearchRunUnavailable(RuntimeError):
    """Fixed persistence error that does not reveal connection details."""

    def __init__(self):
        super().__init__("Search status is temporarily unavailable.")


class SearchRunAlreadyActive(RuntimeError):
    """Only one active search may exist for an owner."""


class SearchRunLeaseLost(RuntimeError):
    """The claim is no longer owned by this worker."""


@dataclass(frozen=True)
class SearchRunClaim:
    run_id: str
    owner_user_id: int
    operation_id: str
    keywords: str
    location: str
    lease_token: str
    attempt: int


class SearchRunRepository(Protocol):
    def enqueue(self, operation_id: object, keywords: object, location: object) -> str: ...

    def update_queued_request(self, run_id: object, keywords: object, location: object) -> None: ...

    def abandon_queued(self, run_id: object) -> None: ...

    def status(self) -> dict[str, object]: ...

    def has_active(self) -> bool: ...

    def request_cancel(self, operation_id: object) -> bool: ...

    def claim(self, run_id: object, worker_id: object, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> SearchRunClaim | None: ...

    def heartbeat(self, claim: SearchRunClaim, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None: ...

    def succeed(self, claim: SearchRunClaim, result: Mapping[str, object]) -> None: ...

    def fail(self, claim: SearchRunClaim, error_code: str = "search_failed") -> None: ...

    def cancel(self, claim: SearchRunClaim) -> None: ...


def _uuid(value: object, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Search {label} must be a UUID.") from exc


def _operation_id(value: object) -> str:
    normalized = normalized_operation_id(value)
    return normalized or str(uuid4())


def _request(keywords: object, location: object) -> tuple[str, str]:
    normalized_keywords = str(keywords or "").strip()
    normalized_location = str(location or "").strip()
    if len(normalized_keywords) > MAX_KEYWORDS_CHARS:
        raise ValueError(f"Search keywords must be {MAX_KEYWORDS_CHARS} characters or fewer.")
    if len(normalized_location) > MAX_LOCATION_CHARS:
        raise ValueError(f"Search location must be {MAX_LOCATION_CHARS} characters or fewer.")
    return normalized_keywords, normalized_location


def _worker_id(value: object) -> str:
    return _uuid(value, "worker ID")


def _lease_seconds(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Search lease duration is invalid.")
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Search lease duration is invalid.") from exc
    if seconds < MIN_LEASE_SECONDS or seconds > MAX_LEASE_SECONDS:
        raise ValueError(
            f"Search lease duration must be {MIN_LEASE_SECONDS} through {MAX_LEASE_SECONDS} seconds."
        )
    return seconds


def minimized_search_result(result: Mapping[str, object]) -> dict[str, object]:
    """Keep only bounded UI state; provider responses and credentials stay out."""
    if not bool(result.get("success")):
        return {"success": False, "error": SEARCH_FAILED_MESSAGE, "provider_alerts": []}
    jobs_added = result.get("jobs_added", 0)
    if isinstance(jobs_added, bool) or not isinstance(jobs_added, int):
        jobs_added = 0
    return {
        "success": True,
        "jobs_added": max(0, min(jobs_added, 100_000)),
        "provider_alerts": [],
    }


def _result_json(result: Mapping[str, object]) -> str:
    encoded = json.dumps(minimized_search_result(result), separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_RESULT_JSON_BYTES:
        raise ValueError("Search result summary is too large.")
    return encoded


def _error_code(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in ERROR_CODES else "search_failed"


def _public_result(status: str, result_json: object) -> dict[str, object] | None:
    if status in ACTIVE_STATUSES:
        return None
    if status == "cancelled":
        return {
            "success": True,
            "cancelled": True,
            "partial": True,
            "message": SEARCH_CANCELLED_MESSAGE,
            "provider_alerts": [],
        }
    if status == "failed":
        return {"success": False, "error": SEARCH_FAILED_MESSAGE, "provider_alerts": []}
    if isinstance(result_json, Mapping):
        parsed = dict(result_json)
    else:
        try:
            parsed = json.loads(str(result_json or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"success": False, "error": SEARCH_FAILED_MESSAGE, "provider_alerts": []}
    return minimized_search_result(parsed if isinstance(parsed, dict) else {})


def _status(row: Mapping[str, Any] | None) -> dict[str, object]:
    if row is None:
        return {"searching": False, "last_result": None}
    run_status = str(row["status"])
    return {
        "searching": run_status in ACTIVE_STATUSES,
        "last_result": _public_result(run_status, row.get("result_json")),
    }


class SQLiteSearchRunRepository:
    """Durable standalone state; owner IDs also make isolation testable locally."""

    def __init__(self, owner_user_id: int = 1, connection_factory: Callable[[], Any] = get_db_connection):
        if isinstance(owner_user_id, bool) or int(owner_user_id) <= 0:
            raise ValueError("Search owner ID must be positive.")
        self._owner_user_id = int(owner_user_id)
        self._connection_factory = connection_factory

    def enqueue(self, operation_id: object, keywords: object, location: object) -> str:
        operation = _operation_id(operation_id)
        query, place = _request(keywords, location)
        run_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM search_runs WHERE owner_user_id = ? AND status IN ('succeeded','failed','cancelled')",
                (self._owner_user_id,),
            )
            connection.execute(
                """
                INSERT INTO search_runs (
                    id, owner_user_id, operation_id, keywords, location, status,
                    attempts, max_attempts, available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                """,
                (run_id, self._owner_user_id, operation, query, place, MAX_ATTEMPTS, now, now, now),
            )
            connection.commit()
            return run_id
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            if "search_runs.owner_user_id" in str(exc) or "idx_search_runs_owner_active" in str(exc):
                raise SearchRunAlreadyActive() from exc
            raise SearchRunUnavailable() from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    def update_queued_request(self, run_id: object, keywords: object, location: object) -> None:
        query, place = _request(keywords, location)
        self._update_queued(_uuid(run_id, "run ID"), query, place, abandon=False)

    def abandon_queued(self, run_id: object) -> None:
        self._update_queued(_uuid(run_id, "run ID"), "", "", abandon=True)

    def _update_queued(self, run_id: str, keywords: str, location: str, *, abandon: bool) -> None:
        connection = self._connection_factory()
        now = datetime.now(timezone.utc).isoformat()
        try:
            if abandon:
                connection.execute(
                    """DELETE FROM search_runs
                       WHERE id = ? AND owner_user_id = ? AND status = 'queued'""",
                    (run_id, self._owner_user_id),
                )
            else:
                connection.execute(
                    """UPDATE search_runs SET keywords = ?, location = ?, updated_at = ?
                       WHERE id = ? AND owner_user_id = ? AND status = 'queued'""",
                    (keywords, location, now, run_id, self._owner_user_id),
                )
            connection.commit()
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    def status(self) -> dict[str, object]:
        connection = self._connection_factory()
        try:
            row = connection.execute(
                """SELECT status, result_json FROM search_runs
                   WHERE owner_user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (self._owner_user_id,),
            ).fetchone()
            return _status(dict(row) if row else None)
        except sqlite3.DatabaseError as exc:
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    def has_active(self) -> bool:
        connection = self._connection_factory()
        try:
            return connection.execute(
                "SELECT 1 FROM search_runs WHERE owner_user_id = ? AND status IN ('queued','running','cancel_requested') LIMIT 1",
                (self._owner_user_id,),
            ).fetchone() is not None
        except sqlite3.DatabaseError as exc:
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    def request_cancel(self, operation_id: object) -> bool:
        operation = normalized_operation_id(operation_id)
        if operation is None:
            return False
        connection = self._connection_factory()
        now = datetime.now(timezone.utc).isoformat()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id, status FROM search_runs WHERE owner_user_id = ? AND operation_id = ?",
                (self._owner_user_id, operation),
            ).fetchone()
            if row is None or row["status"] not in ACTIVE_STATUSES:
                connection.commit()
                return False
            if row["status"] == "queued":
                connection.execute(
                    "UPDATE search_runs SET status='cancelled', completed_at=?, updated_at=? WHERE id=?",
                    (now, now, row["id"]),
                )
            else:
                connection.execute(
                    "UPDATE search_runs SET status='cancel_requested', updated_at=? WHERE id=?",
                    (now, row["id"]),
                )
            connection.commit()
            return True
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    def claim(self, run_id: object, worker_id: object, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> SearchRunClaim | None:
        run = _uuid(run_id, "run ID")
        worker = _worker_id(worker_id)
        seconds = _lease_seconds(lease_seconds)
        now = datetime.now(timezone.utc)
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM search_runs WHERE id = ? AND owner_user_id = ?",
                (run, self._owner_user_id),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            if row["status"] == "cancel_requested":
                connection.execute(
                    "UPDATE search_runs SET status='cancelled', completed_at=?, updated_at=? WHERE id=?",
                    (now.isoformat(), now.isoformat(), run),
                )
                connection.commit()
                return None
            if row["status"] == "running" and row["lease_expires_at"] and datetime.fromisoformat(row["lease_expires_at"]) > now:
                connection.commit()
                return None
            if row["status"] not in ("queued", "running") or datetime.fromisoformat(row["available_at"]) > now:
                connection.commit()
                return None
            if int(row["attempts"]) >= int(row["max_attempts"]):
                connection.execute(
                    """UPDATE search_runs SET status='failed', error_code='attempts_exhausted',
                       completed_at=?, lease_expires_at=NULL, lease_token=NULL,
                       worker_id=NULL, updated_at=? WHERE id=?""",
                    (now.isoformat(), now.isoformat(), run),
                )
                connection.commit()
                return None
            lease_token = str(uuid4())
            lease_expires = (now + timedelta(seconds=seconds)).isoformat()
            attempt = int(row["attempts"]) + 1
            connection.execute(
                """UPDATE search_runs SET status='running', attempts=?, worker_id=?, lease_token=?,
                   lease_expires_at=?, started_at=COALESCE(started_at, ?), updated_at=? WHERE id=?""",
                (attempt, worker, lease_token, lease_expires, now.isoformat(), now.isoformat(), run),
            )
            connection.commit()
            return SearchRunClaim(run, self._owner_user_id, row["operation_id"], row["keywords"], row["location"], lease_token, attempt)
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    def heartbeat(self, claim: SearchRunClaim, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
        seconds = _lease_seconds(lease_seconds)
        connection = self._connection_factory()
        try:
            row = connection.execute(
                "SELECT status FROM search_runs WHERE id=? AND owner_user_id=? AND lease_token=?",
                (claim.run_id, self._owner_user_id, claim.lease_token),
            ).fetchone()
            if row is None:
                raise SearchRunLeaseLost()
            if row["status"] == "cancel_requested":
                raise OperationCancelled("The search was stopped by the user.")
            if row["status"] != "running":
                raise SearchRunLeaseLost()
            now = datetime.now(timezone.utc)
            lease_expires_at = connection.execute(
                "SELECT lease_expires_at FROM search_runs WHERE id=? AND lease_token=?",
                (claim.run_id, claim.lease_token),
            ).fetchone()["lease_expires_at"]
            if not lease_expires_at or datetime.fromisoformat(lease_expires_at) <= now:
                raise SearchRunLeaseLost()
            connection.execute(
                "UPDATE search_runs SET lease_expires_at=?, updated_at=? WHERE id=? AND lease_token=?",
                ((now + timedelta(seconds=seconds)).isoformat(), now.isoformat(), claim.run_id, claim.lease_token),
            )
            connection.commit()
        except (OperationCancelled, SearchRunLeaseLost):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    def _finish(self, claim: SearchRunClaim, status: str, result_json: str | None = None, error_code: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        connection = self._connection_factory()
        try:
            cursor = connection.execute(
                """UPDATE search_runs SET
                   status=CASE WHEN status='cancel_requested' THEN 'cancelled' ELSE ? END,
                   result_json=CASE WHEN status='cancel_requested' THEN NULL ELSE ? END,
                   error_code=CASE WHEN status='cancel_requested' THEN NULL ELSE ? END,
                   completed_at=?,
                   lease_expires_at=NULL, lease_token=NULL, worker_id=NULL, updated_at=?
                   WHERE id=? AND owner_user_id=? AND lease_token=?
                     AND lease_expires_at > ? AND status IN ('running','cancel_requested')""",
                (status, result_json, error_code, now, now, claim.run_id,
                 self._owner_user_id, claim.lease_token, now),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise SearchRunLeaseLost()
            connection.commit()
        except SearchRunLeaseLost:
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    def succeed(self, claim: SearchRunClaim, result: Mapping[str, object]) -> None:
        self._finish(claim, "succeeded", _result_json(result))

    def fail(self, claim: SearchRunClaim, error_code: str = "search_failed") -> None:
        self._finish(claim, "failed", error_code=_error_code(error_code))

    def cancel(self, claim: SearchRunClaim) -> None:
        self._finish(claim, "cancelled")


class PostgreSQLSearchRunRepository:
    """Owner-scoped PostgreSQL state under forced RLS."""

    def __init__(self, owner_user_id: int, engine: Engine | None = None):
        self._owner_user_id = int(owner_user_id)
        self._engine = engine or runtime_postgres_engine()

    def enqueue(self, operation_id: object, keywords: object, location: object) -> str:
        operation = _operation_id(operation_id)
        query, place = _request(keywords, location)
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                connection.execute(
                    text("""DELETE FROM careertrellis.search_runs
                        WHERE owner_user_id=:owner
                          AND status IN ('succeeded','failed','cancelled')"""),
                    {"owner": self._owner_user_id},
                )
                return str(connection.scalar(text("""
                    INSERT INTO careertrellis.search_runs
                        (owner_user_id, operation_id, keywords, location)
                    VALUES (:owner, CAST(:operation AS uuid), :keywords, :location)
                    RETURNING id
                """), {"owner": self._owner_user_id, "operation": operation, "keywords": query, "location": place}))
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise SearchRunAlreadyActive() from exc
            raise SearchRunUnavailable() from exc
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def update_queued_request(self, run_id: object, keywords: object, location: object) -> None:
        query, place = _request(keywords, location)
        self._update_queued(_uuid(run_id, "run ID"), query, place, abandon=False)

    def abandon_queued(self, run_id: object) -> None:
        self._update_queued(_uuid(run_id, "run ID"), "", "", abandon=True)

    def _update_queued(self, run_id: str, keywords: str, location: str, *, abandon: bool) -> None:
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                if abandon:
                    connection.execute(text("""DELETE FROM careertrellis.search_runs
                        WHERE id=CAST(:run_id AS uuid) AND owner_user_id=:owner
                          AND status='queued'"""), {"run_id": run_id, "owner": self._owner_user_id})
                else:
                    connection.execute(text("""UPDATE careertrellis.search_runs
                        SET keywords=:keywords, location=:location, updated_at=CURRENT_TIMESTAMP
                        WHERE id=CAST(:run_id AS uuid) AND owner_user_id=:owner AND status='queued'"""), {"run_id": run_id, "owner": self._owner_user_id, "keywords": keywords, "location": location})
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def status(self) -> dict[str, object]:
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = connection.execute(text("""SELECT status, result_json
                    FROM careertrellis.search_runs WHERE owner_user_id=:owner
                    ORDER BY created_at DESC, id DESC LIMIT 1"""), {"owner": self._owner_user_id}).mappings().first()
            return _status(row)
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def has_active(self) -> bool:
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                return connection.scalar(text("""SELECT EXISTS (
                    SELECT 1 FROM careertrellis.search_runs WHERE owner_user_id=:owner
                    AND status IN ('queued','running','cancel_requested'))"""), {"owner": self._owner_user_id}) is True
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def request_cancel(self, operation_id: object) -> bool:
        operation = normalized_operation_id(operation_id)
        if operation is None:
            return False
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = connection.execute(text("""SELECT id, status FROM careertrellis.search_runs
                    WHERE owner_user_id=:owner AND operation_id=CAST(:operation AS uuid) FOR UPDATE"""),
                    {"owner": self._owner_user_id, "operation": operation}).mappings().first()
                if row is None or row["status"] not in ACTIVE_STATUSES:
                    return False
                terminal = row["status"] == "queued"
                connection.execute(text("""UPDATE careertrellis.search_runs SET
                    status=:status, completed_at=CASE WHEN :terminal THEN CURRENT_TIMESTAMP ELSE completed_at END,
                    updated_at=CURRENT_TIMESTAMP WHERE id=:run_id AND owner_user_id=:owner"""),
                    {"status": "cancelled" if terminal else "cancel_requested", "terminal": terminal,
                     "run_id": row["id"], "owner": self._owner_user_id})
                return True
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def claim(self, run_id: object, worker_id: object, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> SearchRunClaim | None:
        run = _uuid(run_id, "run ID")
        worker = _worker_id(worker_id)
        seconds = _lease_seconds(lease_seconds)
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = connection.execute(text("""
                    UPDATE careertrellis.search_runs SET status='running', attempts=attempts+1,
                        worker_id=CAST(:worker AS uuid), lease_token=gen_random_uuid(),
                        lease_expires_at=CURRENT_TIMESTAMP + make_interval(secs => :seconds),
                        started_at=COALESCE(started_at, CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP
                    WHERE id=CAST(:run AS uuid) AND owner_user_id=:owner AND status='queued'
                        AND available_at <= CURRENT_TIMESTAMP AND attempts < max_attempts
                    RETURNING id, owner_user_id, operation_id, keywords, location, lease_token, attempts
                """), {"worker": worker, "seconds": seconds, "run": run, "owner": self._owner_user_id}).mappings().first()
            return _claim(row)
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def heartbeat(self, claim: SearchRunClaim, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
        seconds = _lease_seconds(lease_seconds)
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = connection.execute(text("""SELECT status FROM careertrellis.search_runs
                    WHERE id=CAST(:run AS uuid) AND owner_user_id=:owner
                        AND lease_token=CAST(:lease AS uuid)
                        AND lease_expires_at > CURRENT_TIMESTAMP FOR UPDATE"""),
                    {"run": claim.run_id, "owner": self._owner_user_id, "lease": claim.lease_token}).mappings().first()
                if row is None:
                    raise SearchRunLeaseLost()
                if row["status"] == "cancel_requested":
                    raise OperationCancelled("The search was stopped by the user.")
                if row["status"] != "running":
                    raise SearchRunLeaseLost()
                connection.execute(text("""UPDATE careertrellis.search_runs
                    SET lease_expires_at=CURRENT_TIMESTAMP + make_interval(secs => :seconds), updated_at=CURRENT_TIMESTAMP
                    WHERE id=CAST(:run AS uuid) AND owner_user_id=:owner AND lease_token=CAST(:lease AS uuid)"""),
                    {"seconds": seconds, "run": claim.run_id, "owner": self._owner_user_id, "lease": claim.lease_token})
        except (OperationCancelled, SearchRunLeaseLost):
            raise
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def _finish(self, claim: SearchRunClaim, status: str, result: Mapping[str, object] | None = None, error_code: str | None = None) -> None:
        payload = minimized_search_result(result) if result is not None else None
        if payload is not None and len(json.dumps(payload).encode("utf-8")) > MAX_RESULT_JSON_BYTES:
            raise ValueError("Search result summary is too large.")
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                changed = connection.execute(text("""UPDATE careertrellis.search_runs SET
                    status=CASE WHEN status='cancel_requested' THEN 'cancelled' ELSE :status END,
                    result_json=CASE WHEN status='cancel_requested' THEN NULL ELSE CAST(:result AS jsonb) END,
                    error_code=CASE WHEN status='cancel_requested' THEN NULL ELSE :error_code END,
                    completed_at=CURRENT_TIMESTAMP, lease_expires_at=NULL, lease_token=NULL,
                    worker_id=NULL, updated_at=CURRENT_TIMESTAMP
                    WHERE id=CAST(:run AS uuid) AND owner_user_id=:owner
                        AND lease_token=CAST(:lease AS uuid)
                        AND lease_expires_at > CURRENT_TIMESTAMP
                        AND status IN ('running','cancel_requested')"""),
                    {"status": status, "result": json.dumps(payload) if payload is not None else None,
                     "error_code": str(error_code)[:64] if error_code else None,
                     "run": claim.run_id, "owner": self._owner_user_id, "lease": claim.lease_token}).rowcount
                if changed != 1:
                    raise SearchRunLeaseLost()
        except SearchRunLeaseLost:
            raise
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def succeed(self, claim: SearchRunClaim, result: Mapping[str, object]) -> None:
        self._finish(claim, "succeeded", result=result)

    def fail(self, claim: SearchRunClaim, error_code: str = "search_failed") -> None:
        self._finish(claim, "failed", error_code=_error_code(error_code))

    def cancel(self, claim: SearchRunClaim) -> None:
        self._finish(claim, "cancelled")


def _claim(row: Mapping[str, Any] | None) -> SearchRunClaim | None:
    if row is None:
        return None
    return SearchRunClaim(
        run_id=str(row["id"]), owner_user_id=int(row["owner_user_id"]),
        operation_id=str(row["operation_id"]), keywords=str(row["keywords"]),
        location=str(row["location"]), lease_token=str(row["lease_token"]),
        attempt=int(row["attempts"]),
    )


def claim_next_postgres_search_run(
    worker_id: object,
    *,
    engine: Engine | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> SearchRunClaim | None:
    """Invoke the worker-only broker; its transaction closes before provider calls."""
    worker = _worker_id(worker_id)
    seconds = _lease_seconds(lease_seconds)
    selected = engine or runtime_postgres_engine()
    try:
        with selected.begin() as connection:
            row = connection.execute(text("""SELECT * FROM careertrellis.claim_next_search_run(
                CAST(:worker AS uuid), :seconds)"""), {"worker": worker, "seconds": seconds}).mappings().first()
        return _claim(row)
    except SQLAlchemyError as exc:
        raise SearchRunUnavailable() from exc


def runtime_search_run_repository(
    *,
    sqlite_connection_factory: Callable[[], Any] = get_db_connection,
    engine: Engine | None = None,
) -> SearchRunRepository:
    """Select exactly one store and derive ownership only from trusted context."""
    if data_store_kind() == LOCAL_DATA_STORE:
        return SQLiteSearchRunRepository(1, sqlite_connection_factory)
    return PostgreSQLSearchRunRepository(current_owner_user_id(), engine=engine)
