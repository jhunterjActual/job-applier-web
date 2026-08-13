"""Dual-mode repositories for owner-scoped job/application lifecycle writes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
import json
from typing import Any, Protocol

from sqlalchemy import Engine, text

from data_store import LOCAL_DATA_STORE, data_store_kind
from database import get_db_connection
from lifecycle import APPLIED_OR_LATER, PIPELINE_STATUSES, undo_latest_lifecycle_change, update_lifecycle
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


MAX_LIFECYCLE_NOTES_CHARS = 10_000
APPLICATION_METHODS = frozenset({
    "company_site", "job_board", "email", "recruiter", "referral", "other",
})


class LifecycleRepository(Protocol):
    def update_lifecycle(
        self,
        job_id: int,
        status: str,
        *,
        applied_on: date | None = None,
        method: str | None = None,
        notes: str | None = None,
        follow_up_on: date | None = None,
    ) -> dict[str, Any]: ...

    def undo_latest_lifecycle_change(self, job_id: int) -> dict[str, Any]: ...


def _positive_job_id(job_id: object) -> int:
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
        raise LookupError("Job not found.")
    return job_id


def _lifecycle_date(value: date | None, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValueError(f"{field} must be a date.")
    return value


def _lifecycle_values(
    job_id: int,
    status: str,
    *,
    applied_on: date | None,
    method: str | None,
    notes: str | None,
    follow_up_on: date | None,
) -> dict[str, Any]:
    safe_job_id = _positive_job_id(job_id)
    if status not in PIPELINE_STATUSES:
        raise ValueError(f"Unsupported lifecycle status: {status}")
    if method is not None and method not in APPLICATION_METHODS:
        raise ValueError("Unsupported application method.")
    if notes is not None and not isinstance(notes, str):
        raise ValueError("Lifecycle notes must be text.")
    if notes is not None and len(notes) > MAX_LIFECYCLE_NOTES_CHARS:
        raise ValueError("Lifecycle notes are too long.")
    return {
        "job_id": safe_job_id,
        "status": status,
        "applied_on": _lifecycle_date(applied_on, "Application date"),
        "method": method,
        "notes": notes,
        "follow_up_on": _lifecycle_date(follow_up_on, "Follow-up date"),
    }


def _api_result(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key, value in result.items()
        if key != "owner_user_id"
    }


class SQLiteLifecycleRepository:
    """Preserve standalone lifecycle behavior behind one transaction boundary."""

    def __init__(self, connection_factory: Callable[[], Any] = get_db_connection):
        self._connection_factory = connection_factory

    def _write(self, action: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = action(connection)
            connection.commit()
            return _api_result(result)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_lifecycle(
        self,
        job_id: int,
        status: str,
        *,
        applied_on: date | None = None,
        method: str | None = None,
        notes: str | None = None,
        follow_up_on: date | None = None,
    ) -> dict[str, Any]:
        values = _lifecycle_values(
            job_id,
            status,
            applied_on=applied_on,
            method=method,
            notes=notes,
            follow_up_on=follow_up_on,
        )
        return self._write(
            lambda connection: update_lifecycle(
                connection,
                values["job_id"],
                values["status"],
                applied_on=values["applied_on"],
                method=values["method"],
                notes=values["notes"],
                follow_up_on=values["follow_up_on"],
                source="manual",
            )
        )

    def undo_latest_lifecycle_change(self, job_id: int) -> dict[str, Any]:
        safe_job_id = _positive_job_id(job_id)
        return self._write(
            lambda connection: undo_latest_lifecycle_change(connection, safe_job_id)
        )


class PostgreSQLLifecycleRepository:
    """Owner-scoped lifecycle writes under transaction-local forced RLS."""

    def __init__(self, owner_user_id: int, engine: Engine | None = None):
        self._owner_user_id = owner_user_id
        self._engine = engine or runtime_postgres_engine()

    def _run(self, action: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            return _api_result(action(connection))

    def _locked_job(self, connection: Any, job_id: int) -> Mapping[str, Any]:
        row = connection.execute(
            text(
                """
                SELECT id, company, title, status
                FROM careertrellis.jobs
                WHERE id = :job_id AND owner_user_id = :owner_user_id
                FOR UPDATE
                """
            ),
            {"job_id": job_id, "owner_user_id": self._owner_user_id},
        ).mappings().one_or_none()
        if not row:
            raise LookupError("Job not found.")
        return row

    def _locked_application(self, connection: Any, job_id: int) -> Mapping[str, Any] | None:
        return connection.execute(
            text(
                """
                SELECT id, date_applied, status, application_method,
                       submission_evidence, notes, follow_up_date,
                       tailored_resume_object_id, cover_letter_object_id,
                       tailored_resume_text, cover_letter
                FROM careertrellis.applications
                WHERE job_id = :job_id AND owner_user_id = :owner_user_id
                FOR UPDATE
                """
            ),
            {"job_id": job_id, "owner_user_id": self._owner_user_id},
        ).mappings().one_or_none()

    def _apply(
        self,
        connection: Any,
        job: Mapping[str, Any],
        status: str,
        *,
        applied_on: date | None,
        method: str | None,
        notes: str | None,
        follow_up_on: date | None,
        source: str,
        record_history: bool,
        now: datetime,
    ) -> dict[str, Any]:
        application = self._locked_application(connection, int(job["id"]))
        previous_status = application["status"] if application else job["status"]
        existing_applied_on = application["date_applied"] if application else None
        effective_applied_on = applied_on or existing_applied_on
        if status in APPLIED_OR_LATER and not effective_applied_on:
            effective_applied_on = now.date()
        if status not in APPLIED_OR_LATER:
            effective_applied_on = None

        evidence = (
            json.dumps({"source": source, "confirmed_by_user": True}, separators=(",", ":"))
            if source == "manual"
            else None
        )
        supplied_method = f"manual:{method}" if source == "manual" and method else method
        application_method = supplied_method or (
            application["application_method"] if application else None
        )
        submission_evidence = evidence or (
            application["submission_evidence"] if application else None
        )
        effective_notes = notes if notes is not None else (
            application["notes"] if application else None
        )

        parameters = {
            "owner_user_id": self._owner_user_id,
            "job_id": int(job["id"]),
            "company": job["company"],
            "position": job["title"],
            "date_applied": effective_applied_on,
            "status": status,
            "now": now,
            "application_method": application_method,
            "submission_evidence": submission_evidence,
            "notes": effective_notes,
            "follow_up_date": follow_up_on,
        }
        if application:
            connection.execute(
                text(
                    """
                    UPDATE careertrellis.applications
                    SET date_applied = :date_applied,
                        status = :status,
                        application_method = :application_method,
                        submission_evidence = :submission_evidence,
                        notes = :notes,
                        follow_up_date = :follow_up_date,
                        submitted_at = CASE
                          WHEN :status IN ('applied','interview','offer','rejected','withdrawn','closed')
                            THEN COALESCE(submitted_at, :now)
                          WHEN :status IN ('matched','tailored','form_filled') THEN NULL
                          ELSE submitted_at END,
                        confirmed_at = CASE
                          WHEN :status IN ('applied','interview','offer','rejected','withdrawn','closed')
                            THEN COALESCE(confirmed_at, :now)
                          ELSE NULL END
                    WHERE job_id = :job_id AND owner_user_id = :owner_user_id
                    """
                ),
                parameters,
            )
        else:
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.applications (
                        owner_user_id, job_id, company, position, date_applied,
                        status, created_at, submitted_at, confirmed_at,
                        application_method, submission_evidence, notes, follow_up_date
                    ) VALUES (
                        :owner_user_id, :job_id, :company, :position, :date_applied,
                        :status, :now,
                        CASE WHEN :status IN ('applied','interview','offer','rejected','withdrawn','closed') THEN :now END,
                        CASE WHEN :status IN ('applied','interview','offer','rejected','withdrawn','closed') THEN :now END,
                        :application_method, :submission_evidence, :notes, :follow_up_date
                    )
                    """
                ),
                parameters,
            )

        connection.execute(
            text(
                """
                UPDATE careertrellis.jobs SET status = :status
                WHERE id = :job_id AND owner_user_id = :owner_user_id
                """
            ),
            parameters,
        )
        if record_history and previous_status != status:
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.application_status_history (
                        owner_user_id, job_id, from_status, to_status,
                        changed_at, source, notes
                    ) VALUES (
                        :owner_user_id, :job_id, :previous_status, :status,
                        :now, :source, :notes
                    )
                    """
                ),
                {
                    **parameters,
                    "previous_status": previous_status,
                    "source": source,
                },
            )
        return {
            "previous_status": previous_status,
            "status": status,
            "date_applied": effective_applied_on,
        }

    def update_lifecycle(
        self,
        job_id: int,
        status: str,
        *,
        applied_on: date | None = None,
        method: str | None = None,
        notes: str | None = None,
        follow_up_on: date | None = None,
    ) -> dict[str, Any]:
        values = _lifecycle_values(
            job_id,
            status,
            applied_on=applied_on,
            method=method,
            notes=notes,
            follow_up_on=follow_up_on,
        )

        def apply(connection: Any) -> dict[str, Any]:
            job = self._locked_job(connection, values["job_id"])
            return self._apply(
                connection,
                job,
                values["status"],
                applied_on=values["applied_on"],
                method=values["method"],
                notes=values["notes"],
                follow_up_on=values["follow_up_on"],
                source="manual",
                record_history=True,
                now=datetime.now(timezone.utc),
            )

        return self._run(apply)

    def undo_latest_lifecycle_change(self, job_id: int) -> dict[str, Any]:
        safe_job_id = _positive_job_id(job_id)

        def undo(connection: Any) -> dict[str, Any]:
            job = self._locked_job(connection, safe_job_id)
            history = connection.execute(
                text(
                    """
                    SELECT id, from_status
                    FROM careertrellis.application_status_history
                    WHERE job_id = :job_id
                      AND owner_user_id = :owner_user_id
                      AND undone_at IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"job_id": safe_job_id, "owner_user_id": self._owner_user_id},
            ).mappings().one_or_none()
            if not history:
                raise LookupError("No lifecycle change is available to undo.")
            if history["from_status"] not in PIPELINE_STATUSES:
                raise ValueError("The recorded lifecycle change cannot be undone safely.")
            now = datetime.now(timezone.utc)
            result = self._apply(
                connection,
                job,
                history["from_status"],
                applied_on=None,
                method=None,
                notes=None,
                follow_up_on=None,
                source="undo",
                record_history=False,
                now=now,
            )
            if history["from_status"] == "matched":
                application = self._locked_application(connection, safe_job_id)
                has_materials = application and any((
                    application["tailored_resume_object_id"],
                    application["cover_letter_object_id"],
                    application["tailored_resume_text"],
                    application["cover_letter"],
                ))
                if application and not has_materials:
                    connection.execute(
                        text(
                            """
                            DELETE FROM careertrellis.applications
                            WHERE job_id = :job_id AND owner_user_id = :owner_user_id
                            """
                        ),
                        {"job_id": safe_job_id, "owner_user_id": self._owner_user_id},
                    )
            connection.execute(
                text(
                    """
                    UPDATE careertrellis.application_status_history
                    SET undone_at = :undone_at
                    WHERE id = :history_id AND owner_user_id = :owner_user_id
                    """
                ),
                {
                    "history_id": history["id"],
                    "owner_user_id": self._owner_user_id,
                    "undone_at": now,
                },
            )
            return result

        return self._run(undo)


def runtime_lifecycle_repository(
    *,
    sqlite_connection_factory: Callable[[], Any] = get_db_connection,
    engine: Engine | None = None,
) -> LifecycleRepository:
    """Return the configured lifecycle repository without caller-selected ownership."""
    if data_store_kind() == LOCAL_DATA_STORE:
        return SQLiteLifecycleRepository(sqlite_connection_factory)
    return PostgreSQLLifecycleRepository(current_owner_user_id(), engine=engine)
