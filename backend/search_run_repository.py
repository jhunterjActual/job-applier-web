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


ACTIVE_STATUSES = ("preparing", "queued", "running", "cancel_requested")
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
MATCH_PROVIDER_FAILURE_MESSAGES = {
    "keywords_unavailable": "Search terms could not be prepared. Enter a short job title and location, then try again.",
    "storage_error": "Some matched postings could not be saved. Any earlier saved matches were kept; try again later.",
    "authentication": "The saved AI provider credential could not be used to score the discovered postings. Check it in Profile & Resume, then try again.",
    "missing_key": "An AI provider key is required to score discovered postings. Save one in Profile & Resume, then try again.",
    "model_unavailable": "The selected AI model could not score the discovered postings. Choose an available model, then try again.",
    "permission": "The selected AI provider denied access while scoring discovered postings. Check the key and model, then try again.",
    "rate_limit": "The AI match provider remained rate-limited after bounded retries. Source diagnostics and any earlier saved match batches were kept; try again later.",
    "invalid_response": "The AI match provider repeatedly returned output that could not be validated. Source diagnostics and any earlier saved match batches were kept.",
    "empty_response": "The AI match provider repeatedly returned no usable scoring result. Source diagnostics and any earlier saved match batches were kept.",
    "network": "The AI match provider could not be reached after bounded retries. Source diagnostics and any earlier saved match batches were kept.",
    "oversized_response": "The AI match provider repeatedly returned an oversized scoring result. Source diagnostics and any earlier saved match batches were kept.",
    "provider_error": "The AI match provider could not score the discovered postings after bounded retries. Source diagnostics and any earlier saved match batches were kept.",
    "timeout": "The AI match provider timed out after bounded retries. Source diagnostics and any earlier saved match batches were kept.",
    "unsupported_request": "The AI match provider rejected the scoring request. Check the selected provider and model, then try again.",
    "overloaded": "The AI match provider remained busy after bounded retries. Any earlier saved matches were kept; try again later.",
    "refusal": "The AI match provider declined the scoring request. Any earlier saved matches were kept.",
    "truncated": "The AI scoring result exceeded the selected model's output or context limit. Any earlier saved matches were kept.",
    "invalid_input": "The scoring request exceeds the selected AI provider's supported limits. Review the resume and job input before trying again.",
    "redirect_rejected": "The AI provider returned an unexpected redirect. The request was stopped without following it.",
    "unsupported_provider": "The selected AI provider is not supported. Review the selection in Profile & Resume.",
    "unsupported_operation": "The selected AI provider does not support this scoring operation.",
    "unsupported_storage": "The selected AI provider requires supported encrypted credential storage.",
    "invalid_rate": "The scoring cost configuration does not support the selected AI provider and model.",
    "receipt_limit": "The scoring request limit was reached. Any earlier saved matches were kept.",
}
MATCH_PROVIDER_FAILURE_CODES = frozenset(MATCH_PROVIDER_FAILURE_MESSAGES)
ERROR_CODES = frozenset({"search_failed", "provider_search_failed", "attempts_exhausted"})
SEARCH_PHASE_MESSAGES = {
    "preparing": "Preparing the search request.",
    "queued": "Waiting for the search worker.",
    "preparing_keywords": "Preparing search keywords.",
    "searching_sources": "Searching job sources.",
    "inspecting_postings": "Inspecting discovered postings.",
    "analyzing_matches": "Analyzing resume matches.",
    "retrying_match_analysis": "The AI match provider was interrupted. Retrying this match batch.",
    "saving_matches": "Saving matched postings.",
    "cancel_requested": "Stopping safely after the current search step.",
}
SEARCH_PROGRESS_PHASES = frozenset(SEARCH_PHASE_MESSAGES) - {"cancel_requested"}


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

    def publish_prepared(self, run_id: object, keywords: object, location: object) -> None: ...

    def abandon_prepared(self, run_id: object) -> None: ...

    def status(self) -> dict[str, object]: ...

    def has_active(self) -> bool: ...

    def request_cancel(self, operation_id: object) -> bool: ...

    def request_cancel_active(self) -> bool: ...

    def claim(self, run_id: object, worker_id: object, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> SearchRunClaim | None: ...

    def heartbeat(self, claim: SearchRunClaim, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None: ...

    def progress(
        self,
        claim: SearchRunClaim,
        phase: str,
        completed: int = 0,
        total: int | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None: ...

    def succeed(self, claim: SearchRunClaim, result: Mapping[str, object]) -> None: ...

    def fail(
        self,
        claim: SearchRunClaim,
        error_code: str = "search_failed",
        result: Mapping[str, object] | None = None,
    ) -> None: ...

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


def _minimized_search_result(result: Mapping[str, object]) -> dict[str, object]:
    """Keep only bounded UI state; provider responses and credentials stay out."""
    outcome = result.get("outcome")
    messages = {
        "source_unavailable": "Job sources did not return usable postings. Try again later or import a specific job URL.",
        "no_results": "The job sources returned no postings for this search. Try different keywords or locations.",
        "no_new_matches": "No new matches were saved. Discovered postings were already known, unavailable, or did not qualify.",
        "matches_saved": "New matches were saved.",
        "partial_results": "Search was only partially completed. Any saved matches were kept; some sources or postings could not be processed.",
    }
    if isinstance(outcome, str) and outcome in messages:
        def count(value):
            return max(0, min(value, 100_000)) if type(value) is int else 0

        counts = result.get("counts")
        counts = counts if isinstance(counts, Mapping) else {}
        payload = {
            "success": bool(result.get("success")) and outcome != "source_unavailable",
            "outcome": outcome,
            "jobs_added": count(result.get("jobs_added")),
            "counts": {name: count(counts.get(name)) for name in (
                "discovered", "inspected", "accepted", "known", "filtered", "saved"
            )},
            "provider_alerts": [],
        }
        # Reconstruct notices from enums only, including on subsequent readback.
        from source_diagnostics import ALLOWED_CODES, ALLOWED_PROVIDERS
        notice_messages = {
            "url_format_drift": "Some discovered links did not identify supported job postings.",
            "content_format_drift": "Some postings could not be read in a supported format.",
            "access_challenge": "Some job sources blocked automated reading.",
            "provider_error": "Some job sources could not be reached or returned unusable results.",
            "stale_postings": "Unavailable postings were skipped.",
            "partial_results": "Some source results were incomplete or required a fallback.",
        }
        alerts = result.get("provider_alerts")
        seen = set()
        for alert in (alerts[:20] if isinstance(alerts, list) else []):
            if not isinstance(alert, Mapping):
                continue
            provider, code = alert.get("provider"), alert.get("code")
            if not isinstance(provider, str) or not isinstance(code, str):
                continue
            if provider not in ALLOWED_PROVIDERS or code not in notice_messages or (provider, code) in seen:
                continue
            seen.add((provider, code))
            payload["provider_alerts"].append({
                "provider": provider, "code": code,
                "message": f"{provider.title()}: {notice_messages[code]}",
            })
        message = messages[outcome]
        if outcome == "matches_saved":
            message = f"Saved {payload['jobs_added']} new match(es)."
        if payload["success"]:
            payload["message"] = message
            payload["partial"] = outcome == "partial_results"
        else:
            failure_code = result.get("failure_code")
            if isinstance(failure_code, str) and failure_code in MATCH_PROVIDER_FAILURE_CODES:
                payload["failure_code"] = failure_code
                payload["error"] = MATCH_PROVIDER_FAILURE_MESSAGES[failure_code]
            else:
                payload["error"] = message if outcome == "source_unavailable" else SEARCH_FAILED_MESSAGE
        return payload
    if not bool(result.get("success")):
        failure_code = str(result.get("failure_code") or "").strip().lower()
        if failure_code not in MATCH_PROVIDER_FAILURE_CODES:
            return {
                "success": False,
                "error": SEARCH_FAILED_MESSAGE,
                "provider_alerts": [],
            }
        return {
            "success": False,
            "error": MATCH_PROVIDER_FAILURE_MESSAGES[failure_code],
            "failure_code": failure_code,
            "provider_alerts": [],
        }
    jobs_added = result.get("jobs_added", 0)
    if isinstance(jobs_added, bool) or not isinstance(jobs_added, int):
        jobs_added = 0
    payload = {
        "success": True,
        "jobs_added": max(0, min(jobs_added, 100_000)),
        "provider_alerts": [],
    }
    if result.get("partial") is True:
        payload.update({
            "partial": True,
            "message": "Some matched postings were saved before the search provider stopped responding.",
        })
    return payload


def minimized_search_result(result: Mapping[str, object]) -> dict[str, object]:
    """Retain measured aggregates on failures too, without filling old history."""
    payload = _minimized_search_result(result)
    from search_review import role_filter
    plan = result.get('role_filter')
    if isinstance(plan, dict):
        payload['role_filter'] = role_filter(plan.get('titles'), plan.get('mode'))
    counts = result.get("counts")
    if isinstance(counts, Mapping):
        safe_counts = payload.setdefault("counts", {})
        for name in ("discovered", "inspected", "accepted", "known", "filtered", "saved",
                     "scored", "meeting_minimum"):
            if type(counts.get(name)) is int:
                safe_counts[name] = max(0, min(100_000, counts[name]))
    source_counts = result.get("source_counts")
    if isinstance(source_counts, Mapping):
        payload["source_counts"] = {
            name: max(0, min(100_000, source_counts[name]))
            for name in ("confirmed_empty", "failed", "unusable", "limited")
            if type(source_counts.get(name)) is int
        }
    if type(result.get("minimum_match_score")) is int:
        payload["minimum_match_score"] = max(0, min(100, result["minimum_match_score"]))
    return payload


def _result_json(result: Mapping[str, object]) -> str:
    encoded = json.dumps(minimized_search_result(result), separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_RESULT_JSON_BYTES:
        raise ValueError("Search result summary is too large.")
    return encoded


def _error_code(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in ERROR_CODES else "search_failed"


def _progress_values(phase: object, completed: object, total: object) -> tuple[str, int, int | None]:
    normalized_phase = str(phase or "").strip().lower()
    if normalized_phase not in SEARCH_PROGRESS_PHASES:
        raise ValueError("Search progress phase is invalid.")
    if isinstance(completed, bool) or not isinstance(completed, int) or completed < 0:
        raise ValueError("Search progress count is invalid.")
    if total is None:
        normalized_total = None
    elif isinstance(total, bool) or not isinstance(total, int) or total < completed:
        raise ValueError("Search progress total is invalid.")
    else:
        normalized_total = total
    return normalized_phase, completed, normalized_total


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
    if status == "failed" and result_json is None:
        return {"success": False, "error": SEARCH_FAILED_MESSAGE, "provider_alerts": []}
    if isinstance(result_json, Mapping):
        parsed = dict(result_json)
    else:
        try:
            parsed = json.loads(str(result_json or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"success": False, "error": SEARCH_FAILED_MESSAGE, "provider_alerts": []}
    return minimized_search_result(parsed if isinstance(parsed, dict) else {})


def _public_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _status(row: Mapping[str, Any] | None) -> dict[str, object]:
    if row is None:
        return {"searching": False, "active_search": None, "last_result": None}
    run_status = str(row["status"])
    active = run_status in ACTIVE_STATUSES
    active_search = None
    if active:
        progress_phase = str(row.get("progress_phase") or run_status)
        if run_status == "cancel_requested":
            progress_phase = run_status
        elif progress_phase not in SEARCH_PHASE_MESSAGES:
            progress_phase = (
                "preparing" if run_status == "preparing"
                else "queued" if run_status == "queued"
                else "preparing_keywords"
            )
        progress_at = row.get("progress_at") or row.get("started_at") or row.get("created_at")
        active_search = {
            "phase": progress_phase,
            "message": SEARCH_PHASE_MESSAGES[progress_phase],
            "started_at": _public_timestamp(
                row.get("started_at") or row.get("created_at")
            ),
            "last_activity_at": _public_timestamp(progress_at),
            "last_progress_at": _public_timestamp(progress_at),
            "last_heartbeat_at": _public_timestamp(
                row.get("lease_heartbeat_at") or row.get("updated_at")
            ),
            "completed": max(0, int(row.get("progress_completed") or 0)),
            "total": (
                max(0, int(row["progress_total"]))
                if row.get("progress_total") is not None
                else None
            ),
            "attempt": max(0, int(row.get("attempts") or 0)),
            "max_attempts": max(1, int(row.get("max_attempts") or MAX_ATTEMPTS)),
            "stop_requested": run_status == "cancel_requested",
        }
    return {
        "searching": active,
        "active_search": active_search,
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
                ) VALUES (?, ?, ?, ?, ?, 'preparing', 0, ?, ?, ?, ?)
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
        except SearchRunUnavailable:
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    def publish_prepared(self, run_id: object, keywords: object, location: object) -> None:
        query, place = _request(keywords, location)
        self._finish_preparation(_uuid(run_id, "run ID"), query, place, abandon=False)

    def abandon_prepared(self, run_id: object) -> None:
        self._finish_preparation(_uuid(run_id, "run ID"), "", "", abandon=True)

    def _finish_preparation(self, run_id: str, keywords: str, location: str, *, abandon: bool) -> None:
        connection = self._connection_factory()
        now = datetime.now(timezone.utc).isoformat()
        try:
            if abandon:
                connection.execute(
                    """DELETE FROM search_runs
                       WHERE id = ? AND owner_user_id = ? AND status = 'preparing'""",
                    (run_id, self._owner_user_id),
                )
            else:
                cursor = connection.execute(
                    """UPDATE search_runs SET keywords = ?, location = ?, status = 'queued',
                       progress_phase='queued', progress_completed=0, progress_total=NULL,
                       progress_at=?, updated_at=?
                       WHERE id = ? AND owner_user_id = ? AND status = 'preparing'""",
                    (keywords, location, now, now, run_id, self._owner_user_id),
                )
                if cursor.rowcount != 1:
                    raise SearchRunUnavailable()
            connection.commit()
        except SearchRunUnavailable:
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    def status(self) -> dict[str, object]:
        connection = self._connection_factory()
        try:
            row = connection.execute(
                """SELECT status, result_json, created_at, started_at, updated_at,
                          progress_phase, progress_completed, progress_total, progress_at,
                          lease_heartbeat_at, attempts, max_attempts
                   FROM search_runs WHERE owner_user_id = ?
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
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
                "SELECT 1 FROM search_runs WHERE owner_user_id = ? AND status IN ('preparing','queued','running','cancel_requested') LIMIT 1",
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
                "SELECT id, status, lease_expires_at FROM search_runs WHERE owner_user_id = ? AND operation_id = ?",
                (self._owner_user_id, operation),
            ).fetchone()
            if row is None or row["status"] not in ACTIVE_STATUSES:
                connection.commit()
                return False
            self._cancel_row(connection, row, now)
            connection.commit()
            return True
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    def request_cancel_active(self) -> bool:
        connection = self._connection_factory()
        now = datetime.now(timezone.utc).isoformat()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT id, status, lease_expires_at FROM search_runs
                   WHERE owner_user_id = ?
                     AND status IN ('preparing','queued','running','cancel_requested')
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (self._owner_user_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return False
            self._cancel_row(connection, row, now)
            connection.commit()
            return True
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise SearchRunUnavailable() from exc
        finally:
            connection.close()

    @staticmethod
    def _cancel_row(connection: Any, row: Mapping[str, Any], now: str) -> None:
        lease_expires_at = row["lease_expires_at"]
        terminal = row["status"] in ("preparing", "queued")
        if lease_expires_at:
            try:
                terminal = terminal or datetime.fromisoformat(
                    lease_expires_at
                ) <= datetime.fromisoformat(now)
            except (TypeError, ValueError):
                terminal = True
        elif row["status"] in ("running", "cancel_requested"):
            terminal = True
        if terminal:
            connection.execute(
                """UPDATE search_runs SET status='cancelled', completed_at=?,
                   lease_expires_at=NULL, lease_token=NULL, worker_id=NULL, updated_at=?
                   WHERE id=?""",
                (now, now, row["id"]),
            )
        else:
            connection.execute(
                "UPDATE search_runs SET status='cancel_requested', updated_at=? WHERE id=?",
                (now, row["id"]),
            )

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
                   lease_expires_at=?, started_at=COALESCE(started_at, ?), updated_at=?,
                   progress_phase='preparing_keywords', progress_completed=0, progress_total=1,
                   progress_at=?, lease_heartbeat_at=?,
                   last_attempt_error_code=NULL WHERE id=?""",
                (attempt, worker, lease_token, lease_expires, now.isoformat(), now.isoformat(),
                 now.isoformat(), now.isoformat(), run),
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
                """UPDATE search_runs SET lease_expires_at=?, lease_heartbeat_at=?, updated_at=?
                   WHERE id=? AND lease_token=?""",
                ((now + timedelta(seconds=seconds)).isoformat(), now.isoformat(),
                 now.isoformat(), claim.run_id, claim.lease_token),
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

    def progress(
        self,
        claim: SearchRunClaim,
        phase: str,
        completed: int = 0,
        total: int | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        normalized_phase, normalized_completed, normalized_total = _progress_values(
            phase, completed, total
        )
        seconds = _lease_seconds(lease_seconds)
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT status, lease_expires_at FROM search_runs
                   WHERE id=? AND owner_user_id=? AND lease_token=?""",
                (claim.run_id, self._owner_user_id, claim.lease_token),
            ).fetchone()
            if row is None:
                raise SearchRunLeaseLost()
            if row["status"] == "cancel_requested":
                raise OperationCancelled("The search was stopped by the user.")
            now_value = datetime.now(timezone.utc)
            lease_expires_at = row["lease_expires_at"]
            if (
                row["status"] != "running"
                or not lease_expires_at
                or datetime.fromisoformat(lease_expires_at) <= now_value
            ):
                raise SearchRunLeaseLost()
            now = now_value.isoformat()
            changed = connection.execute(
                """UPDATE search_runs SET progress_phase=?, progress_completed=?,
                   progress_total=?, progress_at=?, lease_expires_at=?,
                   lease_heartbeat_at=?, updated_at=?
                   WHERE id=? AND owner_user_id=? AND lease_token=? AND status='running'""",
                (normalized_phase, normalized_completed, normalized_total, now,
                 (now_value + timedelta(seconds=seconds)).isoformat(), now, now,
                 claim.run_id, self._owner_user_id, claim.lease_token),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise SearchRunLeaseLost()
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
                   lease_expires_at=NULL, lease_token=NULL, worker_id=NULL, updated_at=?,
                   last_attempt_error_code=?
                   WHERE id=? AND owner_user_id=? AND lease_token=?
                     AND lease_expires_at > ? AND status IN ('running','cancel_requested')""",
                (status, result_json, error_code, now, now, error_code, claim.run_id,
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

    def fail(
        self,
        claim: SearchRunClaim,
        error_code: str = "search_failed",
        result: Mapping[str, object] | None = None,
    ) -> None:
        self._finish(
            claim,
            "failed",
            _result_json(result) if result is not None else None,
            _error_code(error_code),
        )

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
                        (owner_user_id, operation_id, keywords, location, status)
                    VALUES (:owner, CAST(:operation AS uuid), :keywords, :location, 'preparing')
                    RETURNING id
                """), {"owner": self._owner_user_id, "operation": operation, "keywords": query, "location": place}))
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise SearchRunAlreadyActive() from exc
            raise SearchRunUnavailable() from exc
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def publish_prepared(self, run_id: object, keywords: object, location: object) -> None:
        query, place = _request(keywords, location)
        self._finish_preparation(_uuid(run_id, "run ID"), query, place, abandon=False)

    def abandon_prepared(self, run_id: object) -> None:
        self._finish_preparation(_uuid(run_id, "run ID"), "", "", abandon=True)

    def _finish_preparation(self, run_id: str, keywords: str, location: str, *, abandon: bool) -> None:
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                if abandon:
                    connection.execute(text("""DELETE FROM careertrellis.search_runs
                        WHERE id=CAST(:run_id AS uuid) AND owner_user_id=:owner
                          AND status='preparing'"""), {"run_id": run_id, "owner": self._owner_user_id})
                else:
                    changed = connection.execute(text("""UPDATE careertrellis.search_runs
                        SET keywords=:keywords, location=:location, status='queued',
                            progress_phase='queued', progress_completed=0, progress_total=NULL,
                            progress_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                        WHERE id=CAST(:run_id AS uuid) AND owner_user_id=:owner AND status='preparing'"""), {"run_id": run_id, "owner": self._owner_user_id, "keywords": keywords, "location": location}).rowcount
                    if changed != 1:
                        raise SearchRunUnavailable()
        except SearchRunUnavailable:
            raise
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def status(self) -> dict[str, object]:
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = connection.execute(text("""SELECT status, result_json,
                        created_at, started_at, updated_at, progress_phase,
                        progress_completed, progress_total, progress_at,
                        lease_heartbeat_at, attempts, max_attempts
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
                    AND status IN ('preparing','queued','running','cancel_requested'))"""), {"owner": self._owner_user_id}) is True
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def request_cancel(self, operation_id: object) -> bool:
        operation = normalized_operation_id(operation_id)
        if operation is None:
            return False
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = connection.execute(text("""SELECT id, status, lease_expires_at,
                        (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                            AS lease_expired
                    FROM careertrellis.search_runs
                    WHERE owner_user_id=:owner AND operation_id=CAST(:operation AS uuid) FOR UPDATE"""),
                    {"owner": self._owner_user_id, "operation": operation}).mappings().first()
                if row is None or row["status"] not in ACTIVE_STATUSES:
                    return False
                self._cancel_row(connection, row)
                return True
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def request_cancel_active(self) -> bool:
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = connection.execute(text("""SELECT id, status, lease_expires_at,
                        (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                            AS lease_expired
                    FROM careertrellis.search_runs
                    WHERE owner_user_id=:owner
                      AND status IN ('preparing','queued','running','cancel_requested')
                    ORDER BY created_at DESC, id DESC LIMIT 1 FOR UPDATE"""),
                    {"owner": self._owner_user_id}).mappings().first()
                if row is None:
                    return False
                self._cancel_row(connection, row)
                return True
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def _cancel_row(self, connection: Any, row: Mapping[str, Any]) -> None:
        terminal = row["status"] in ("preparing", "queued")
        if row["status"] in ("running", "cancel_requested"):
            terminal = terminal or row["lease_expired"] is True
        connection.execute(text("""UPDATE careertrellis.search_runs SET
            status=:status,
            completed_at=CASE WHEN :terminal THEN CURRENT_TIMESTAMP ELSE completed_at END,
            lease_expires_at=CASE WHEN :terminal THEN NULL ELSE lease_expires_at END,
            lease_token=CASE WHEN :terminal THEN NULL ELSE lease_token END,
            worker_id=CASE WHEN :terminal THEN NULL ELSE worker_id END,
            updated_at=CURRENT_TIMESTAMP
            WHERE id=:run_id AND owner_user_id=:owner"""),
            {"status": "cancelled" if terminal else "cancel_requested", "terminal": terminal,
             "run_id": row["id"], "owner": self._owner_user_id})

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
                        started_at=COALESCE(started_at, CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP,
                        progress_phase='preparing_keywords', progress_completed=0, progress_total=1,
                        progress_at=CURRENT_TIMESTAMP, lease_heartbeat_at=CURRENT_TIMESTAMP,
                        last_attempt_error_code=NULL
                    WHERE id=CAST(:run AS uuid) AND owner_user_id=:owner AND status='queued'
                        AND available_at <= CURRENT_TIMESTAMP AND attempts < max_attempts
                    RETURNING id, owner_user_id, operation_id, keywords, location, lease_token, attempts
                """), {"worker": worker, "seconds": seconds, "run": run, "owner": self._owner_user_id}).mappings().first()
            return _claim(row)
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def _locked_claim_state(
        self, connection: Any, claim: SearchRunClaim
    ) -> Mapping[str, Any]:
        row = connection.execute(
            text(
                """SELECT run_status, owner_disabled
                FROM careertrellis.lock_search_run_claim(
                    CAST(:run AS uuid), :owner, CAST(:lease AS uuid)
                )"""
            ),
            {
                "run": claim.run_id,
                "owner": self._owner_user_id,
                "lease": claim.lease_token,
            },
        ).mappings().first()
        if row is None:
            raise SearchRunLeaseLost()
        return row

    def heartbeat(self, claim: SearchRunClaim, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
        seconds = _lease_seconds(lease_seconds)
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = self._locked_claim_state(connection, claim)
                if row["run_status"] == "cancel_requested":
                    raise OperationCancelled("The search was stopped by the user.")
                if row["run_status"] != "running" or row["owner_disabled"] is True:
                    raise SearchRunLeaseLost()
                connection.execute(text("""UPDATE careertrellis.search_runs
                    SET lease_expires_at=CURRENT_TIMESTAMP + make_interval(secs => :seconds),
                        lease_heartbeat_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                    WHERE id=CAST(:run AS uuid) AND owner_user_id=:owner AND lease_token=CAST(:lease AS uuid)"""),
                    {"seconds": seconds, "run": claim.run_id, "owner": self._owner_user_id, "lease": claim.lease_token})
        except (OperationCancelled, SearchRunLeaseLost):
            raise
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def progress(
        self,
        claim: SearchRunClaim,
        phase: str,
        completed: int = 0,
        total: int | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        normalized_phase, normalized_completed, normalized_total = _progress_values(
            phase, completed, total
        )
        seconds = _lease_seconds(lease_seconds)
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = self._locked_claim_state(connection, claim)
                if row["run_status"] == "cancel_requested":
                    raise OperationCancelled("The search was stopped by the user.")
                if row["run_status"] != "running" or row["owner_disabled"] is True:
                    raise SearchRunLeaseLost()
                connection.execute(text("""UPDATE careertrellis.search_runs SET
                    progress_phase=:phase, progress_completed=:completed,
                    progress_total=:total, progress_at=CURRENT_TIMESTAMP,
                    lease_expires_at=CURRENT_TIMESTAMP + make_interval(secs => :seconds),
                    lease_heartbeat_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                    WHERE id=CAST(:run AS uuid) AND owner_user_id=:owner
                      AND lease_token=CAST(:lease AS uuid)"""),
                    {"phase": normalized_phase, "completed": normalized_completed,
                     "total": normalized_total, "seconds": seconds,
                     "run": claim.run_id, "owner": self._owner_user_id,
                     "lease": claim.lease_token})
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
                # Older deployed schemas retain their original completion path.
                # Identity-aware schemas expose only a boolean terminal-cleanup
                # broker; ordinary disabled-owner rows remain hidden by RLS.
                cleanup_available = connection.scalar(text(
                    "SELECT to_regprocedure("
                    "'careertrellis.cancel_disabled_search_run(uuid,bigint,uuid)'"
                    ") IS NOT NULL"
                ))
                if cleanup_available is True and connection.scalar(text(
                    "SELECT careertrellis.cancel_disabled_search_run("
                    "CAST(:run AS uuid), :owner, CAST(:lease AS uuid))"
                ), {"run": claim.run_id, "owner": self._owner_user_id,
                    "lease": claim.lease_token}) is True:
                    return
                locked = self._locked_claim_state(connection, claim)
                changed = connection.execute(text("""UPDATE careertrellis.search_runs AS run SET
                    status=CASE
                        WHEN :current_status='cancel_requested' THEN 'cancelled'
                        WHEN :owner_disabled THEN 'cancelled'
                        ELSE :status
                    END,
                    result_json=CASE
                        WHEN :current_status='cancel_requested'
                          OR :owner_disabled THEN NULL
                        ELSE CAST(:result AS jsonb)
                    END,
                    error_code=CASE
                        WHEN :current_status='cancel_requested' THEN NULL
                        WHEN :owner_disabled THEN 'owner_disabled'
                        ELSE :error_code
                    END,
                    completed_at=CURRENT_TIMESTAMP, lease_expires_at=NULL, lease_token=NULL,
                    worker_id=NULL, updated_at=CURRENT_TIMESTAMP,
                    last_attempt_error_code=:attempt_error
                    WHERE run.id=CAST(:run AS uuid) AND run.owner_user_id=:owner
                        AND run.lease_token=CAST(:lease AS uuid)
                        AND run.lease_expires_at > CURRENT_TIMESTAMP
                        AND run.status IN ('running','cancel_requested')"""),
                    {"status": status, "result": json.dumps(payload) if payload is not None else None,
                     "error_code": str(error_code)[:64] if error_code else None,
                     "attempt_error": str(error_code)[:64] if error_code else None,
                     "current_status": locked["run_status"],
                     "owner_disabled": locked["owner_disabled"] is True,
                     "run": claim.run_id, "owner": self._owner_user_id, "lease": claim.lease_token}).rowcount
                if changed != 1:
                    raise SearchRunLeaseLost()
        except SearchRunLeaseLost:
            raise
        except SQLAlchemyError as exc:
            raise SearchRunUnavailable() from exc

    def succeed(self, claim: SearchRunClaim, result: Mapping[str, object]) -> None:
        self._finish(claim, "succeeded", result=result)

    def fail(
        self,
        claim: SearchRunClaim,
        error_code: str = "search_failed",
        result: Mapping[str, object] | None = None,
    ) -> None:
        self._finish(
            claim,
            "failed",
            result=result,
            error_code=_error_code(error_code),
        )

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
