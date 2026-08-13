"""Dual-mode repositories for owner-scoped job-posting verification writes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import Engine, text

from data_store import LOCAL_DATA_STORE, data_store_kind
from database import get_db_connection
from discovery_repository import (
    MAX_JOB_ATTRIBUTE_CHARS,
    MAX_JOB_COMPANY_CHARS,
    MAX_JOB_DESCRIPTION_CHARS,
    MAX_JOB_LOCATION_CHARS,
    MAX_JOB_SOURCE_CHARS,
    MAX_JOB_TITLE_CHARS,
    _job_checked_at,
    _job_text,
    _sqlite_job_timestamp,
)
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


PRE_APPLICATION_STATUSES = frozenset({
    "matched", "tailored", "form_filled", "submitted",
})
EXPIRATION_REASON = "Source reports posting is no longer published"


class VerificationTargetChanged(RuntimeError):
    """Raised when the saved posting changed while its source was inspected."""


class VerificationRepository(Protocol):
    def get_verification_target(self, job_id: int) -> dict[str, Any] | None: ...

    def apply_verification(
        self,
        job_id: int,
        *,
        expected_url: str,
        details: Mapping[str, Any] | None,
        checked_at: datetime,
    ) -> dict[str, Any] | None: ...


def _positive_job_id(job_id: object) -> int:
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
        raise ValueError("Job identifier must be a positive integer.")
    return job_id


def _expected_url(url: object) -> str:
    if not isinstance(url, str) or not url:
        raise ValueError("The saved verification target is invalid.")
    return url


def _verification_details(details: Mapping[str, Any] | None) -> dict[str, str] | None:
    if details is None:
        return None
    if not isinstance(details, Mapping):
        raise TypeError("Verified job details must be a mapping.")
    return {
        "title": _job_text(
            details.get("title"),
            field="title",
            maximum=MAX_JOB_TITLE_CHARS,
            required=True,
        ),
        "company": _job_text(
            details.get("company"),
            field="company",
            maximum=MAX_JOB_COMPANY_CHARS,
            required=True,
        ),
        "description": _job_text(
            details.get("description"),
            field="description",
            maximum=MAX_JOB_DESCRIPTION_CHARS,
            required=True,
        ),
        "location": _job_text(
            details.get("location"),
            field="location",
            maximum=MAX_JOB_LOCATION_CHARS,
        ),
        "work_arrangement": _job_text(
            details.get("work_arrangement"),
            field="work arrangement",
            maximum=MAX_JOB_ATTRIBUTE_CHARS,
        ),
        "employment_type": _job_text(
            details.get("employment_type"),
            field="employment type",
            maximum=MAX_JOB_ATTRIBUTE_CHARS,
        ),
        "compensation": _job_text(
            details.get("compensation"),
            field="compensation",
            maximum=MAX_JOB_ATTRIBUTE_CHARS,
        ),
        "source": _job_text(
            details.get("source"),
            field="source",
            maximum=MAX_JOB_SOURCE_CHARS,
        ),
    }


def _active_result() -> dict[str, Any]:
    return {
        "success": True,
        "expired": False,
        "message": "Listing is still active.",
    }


def _stale_result() -> dict[str, Any]:
    return {
        "success": True,
        "expired": True,
        "message": "Listing appears closed or unavailable.",
    }


class SQLiteVerificationRepository:
    """Preserve standalone verification behavior behind repository transactions."""

    def __init__(self, connection_factory: Callable[[], Any] = get_db_connection):
        self._connection_factory = connection_factory

    def get_verification_target(self, job_id: int) -> dict[str, Any] | None:
        safe_job_id = _positive_job_id(job_id)
        connection = self._connection_factory()
        try:
            row = connection.execute(
                "SELECT id, url FROM jobs WHERE id = ?", (safe_job_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    def apply_verification(
        self,
        job_id: int,
        *,
        expected_url: str,
        details: Mapping[str, Any] | None,
        checked_at: datetime,
    ) -> dict[str, Any] | None:
        safe_job_id = _positive_job_id(job_id)
        safe_url = _expected_url(expected_url)
        safe_details = _verification_details(details)
        checked_value = _sqlite_job_timestamp(_job_checked_at(checked_at))
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT id, url, status FROM jobs WHERE id = ?", (safe_job_id,)
            ).fetchone()
            if not job:
                connection.rollback()
                return None
            if job["url"] != safe_url:
                raise VerificationTargetChanged(
                    "The saved posting changed while verification was running."
                )
            if safe_details is not None:
                connection.execute(
                    """
                    UPDATE jobs SET title = ?, company = ?, description = ?,
                        location = ?, work_arrangement = ?, employment_type = ?,
                        compensation = ?, source = ?, last_checked_at = ?,
                        is_expired = 0, expiration_reason = NULL
                    WHERE id = ?
                    """,
                    (
                        safe_details["title"], safe_details["company"],
                        safe_details["description"], safe_details["location"],
                        safe_details["work_arrangement"],
                        safe_details["employment_type"],
                        safe_details["compensation"], safe_details["source"],
                        checked_value, safe_job_id,
                    ),
                )
                result = _active_result()
            else:
                next_status = (
                    "closed" if job["status"] in PRE_APPLICATION_STATUSES
                    else job["status"]
                )
                connection.execute(
                    """
                    UPDATE jobs SET status = ?, last_checked_at = ?,
                        is_expired = 1, expiration_reason = ?
                    WHERE id = ?
                    """,
                    (next_status, checked_value, EXPIRATION_REASON, safe_job_id),
                )
                if next_status == "closed":
                    connection.execute(
                        "UPDATE applications SET status = 'closed' WHERE job_id = ?",
                        (safe_job_id,),
                    )
                result = _stale_result()
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class PostgreSQLVerificationRepository:
    """Owner-scoped verification writes under transaction-local forced RLS."""

    def __init__(self, owner_user_id: int, engine: Engine | None = None):
        self._owner_user_id = owner_user_id
        self._engine = engine or runtime_postgres_engine()

    def get_verification_target(self, job_id: int) -> dict[str, Any] | None:
        safe_job_id = _positive_job_id(job_id)
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, url FROM careertrellis.jobs
                    WHERE id = :job_id AND owner_user_id = :owner_user_id
                    """
                ),
                {"job_id": safe_job_id, "owner_user_id": self._owner_user_id},
            ).mappings().one_or_none()
            return dict(row) if row else None

    def apply_verification(
        self,
        job_id: int,
        *,
        expected_url: str,
        details: Mapping[str, Any] | None,
        checked_at: datetime,
    ) -> dict[str, Any] | None:
        safe_job_id = _positive_job_id(job_id)
        safe_url = _expected_url(expected_url)
        safe_details = _verification_details(details)
        checked_value = _job_checked_at(checked_at)
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            job = connection.execute(
                text(
                    """
                    SELECT id, url, status FROM careertrellis.jobs
                    WHERE id = :job_id AND owner_user_id = :owner_user_id
                    FOR UPDATE
                    """
                ),
                {"job_id": safe_job_id, "owner_user_id": self._owner_user_id},
            ).mappings().one_or_none()
            if not job:
                return None
            if job["url"] != safe_url:
                raise VerificationTargetChanged(
                    "The saved posting changed while verification was running."
                )
            parameters = {
                "job_id": safe_job_id,
                "owner_user_id": self._owner_user_id,
                "checked_at": checked_value,
            }
            if safe_details is not None:
                connection.execute(
                    text(
                        """
                        UPDATE careertrellis.jobs
                        SET title = :title, company = :company,
                            description = :description, location = :location,
                            work_arrangement = :work_arrangement,
                            employment_type = :employment_type,
                            compensation = :compensation, source = :source,
                            last_checked_at = :checked_at, is_expired = FALSE,
                            expiration_reason = NULL
                        WHERE id = :job_id AND owner_user_id = :owner_user_id
                        """
                    ),
                    {**parameters, **safe_details},
                )
                return _active_result()

            next_status = (
                "closed" if job["status"] in PRE_APPLICATION_STATUSES
                else job["status"]
            )
            connection.execute(
                text(
                    """
                    UPDATE careertrellis.jobs
                    SET status = :status, last_checked_at = :checked_at,
                        is_expired = TRUE, expiration_reason = :reason
                    WHERE id = :job_id AND owner_user_id = :owner_user_id
                    """
                ),
                {**parameters, "status": next_status, "reason": EXPIRATION_REASON},
            )
            if next_status == "closed":
                connection.execute(
                    text(
                        """
                        UPDATE careertrellis.applications SET status = 'closed'
                        WHERE job_id = :job_id AND owner_user_id = :owner_user_id
                        """
                    ),
                    parameters,
                )
            return _stale_result()


def runtime_verification_repository(
    *,
    sqlite_connection_factory: Callable[[], Any] = get_db_connection,
    engine: Engine | None = None,
) -> VerificationRepository:
    """Return the configured repository without caller-selected ownership."""
    if data_store_kind() == LOCAL_DATA_STORE:
        return SQLiteVerificationRepository(sqlite_connection_factory)
    return PostgreSQLVerificationRepository(current_owner_user_id(), engine=engine)
