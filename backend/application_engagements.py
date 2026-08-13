"""Local recruiter, referral, networking, and assessment tracking."""

from __future__ import annotations

from datetime import date, datetime
import sqlite3


ENGAGEMENT_TYPES = (
    "recruiter",
    "hiring_manager",
    "referral",
    "networking",
    "assessment",
)
ENGAGEMENT_STATUSES = (
    "planned",
    "contacted",
    "waiting",
    "scheduled",
    "completed",
    "closed",
)
MAX_ENGAGEMENTS_PER_JOB = 250


class EngagementNotFound(LookupError):
    """Raised when a requested job or engagement does not exist."""


class EngagementLimitError(ValueError):
    """Raised when a job has reached the bounded local tracking limit."""


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _iso_date(value: date | str | None) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)).isoformat()


def _require_job(connection: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    job = connection.execute(
        "SELECT id, company, title FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if not job:
        raise EngagementNotFound("Job not found.")
    return job


def _validated_values(values: dict[str, object]) -> dict[str, object]:
    engagement_type = _clean(values.get("engagement_type"), 40).lower()
    status = _clean(values.get("status"), 40).lower()
    name = _clean(values.get("name"), 160)
    if engagement_type not in ENGAGEMENT_TYPES:
        raise ValueError("Unsupported relationship or step type.")
    if status not in ENGAGEMENT_STATUSES:
        raise ValueError("Unsupported relationship or step status.")
    if not name:
        raise ValueError("A person or step name is required.")
    return {
        "engagement_type": engagement_type,
        "name": name,
        "organization": _clean(values.get("organization"), 160),
        "contact_details": _clean(values.get("contact_details"), 320),
        "status": status,
        "activity_on": _iso_date(values.get("activity_on")),
        "next_action_on": _iso_date(values.get("next_action_on")),
        "notes": str(values.get("notes") or "").strip()[:4000],
    }


def validated_engagement_values(values: dict[str, object]) -> dict[str, object]:
    """Return bounded engagement values without opening or mutating a store."""
    return _validated_values(values)


def list_engagements(connection: sqlite3.Connection, job_id: int) -> dict[str, object]:
    """Return bounded local relationship and milestone records for a job."""
    job = _require_job(connection, job_id)
    rows = connection.execute(
        """
        SELECT id, job_id, engagement_type, name, organization, contact_details,
               status, activity_on, next_action_on, notes, created_at, updated_at
        FROM application_engagements
        WHERE job_id = ?
        ORDER BY CASE WHEN next_action_on IS NULL OR next_action_on = '' THEN 1 ELSE 0 END,
                 next_action_on, updated_at DESC, id DESC
        """,
        (job_id,),
    ).fetchall()
    return {
        "job_id": job_id,
        "company": job["company"],
        "position": job["title"],
        "count": len(rows),
        "engagements": [dict(row) for row in rows],
    }


def create_engagement(
    connection: sqlite3.Connection,
    job_id: int,
    values: dict[str, object],
) -> dict[str, object]:
    """Create one local relationship or milestone record."""
    _require_job(connection, job_id)
    count = connection.execute(
        "SELECT COUNT(*) FROM application_engagements WHERE job_id = ?", (job_id,)
    ).fetchone()[0]
    if count >= MAX_ENGAGEMENTS_PER_JOB:
        raise EngagementLimitError(
            f"This job already has the maximum of {MAX_ENGAGEMENTS_PER_JOB} tracked records."
        )
    normalized = _validated_values(values)
    now = datetime.now().isoformat(timespec="seconds")
    cursor = connection.execute(
        """
        INSERT INTO application_engagements (
            job_id, engagement_type, name, organization, contact_details,
            status, activity_on, next_action_on, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            normalized["engagement_type"],
            normalized["name"],
            normalized["organization"],
            normalized["contact_details"],
            normalized["status"],
            normalized["activity_on"],
            normalized["next_action_on"],
            normalized["notes"],
            now,
            now,
        ),
    )
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM application_engagements WHERE id = ?", (cursor.lastrowid,)
    ).fetchone())


def update_engagement(
    connection: sqlite3.Connection,
    job_id: int,
    engagement_id: int,
    values: dict[str, object],
) -> dict[str, object]:
    """Update one record without permitting it to move between jobs."""
    _require_job(connection, job_id)
    existing = connection.execute(
        "SELECT id FROM application_engagements WHERE id = ? AND job_id = ?",
        (engagement_id, job_id),
    ).fetchone()
    if not existing:
        raise EngagementNotFound("Tracked relationship or step not found.")
    normalized = _validated_values(values)
    connection.execute(
        """
        UPDATE application_engagements
        SET engagement_type = ?, name = ?, organization = ?, contact_details = ?,
            status = ?, activity_on = ?, next_action_on = ?, notes = ?, updated_at = ?
        WHERE id = ? AND job_id = ?
        """,
        (
            normalized["engagement_type"],
            normalized["name"],
            normalized["organization"],
            normalized["contact_details"],
            normalized["status"],
            normalized["activity_on"],
            normalized["next_action_on"],
            normalized["notes"],
            datetime.now().isoformat(timespec="seconds"),
            engagement_id,
            job_id,
        ),
    )
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM application_engagements WHERE id = ?", (engagement_id,)
    ).fetchone())


def delete_engagement(connection: sqlite3.Connection, job_id: int, engagement_id: int) -> bool:
    """Delete one explicitly selected local tracking record."""
    _require_job(connection, job_id)
    cursor = connection.execute(
        "DELETE FROM application_engagements WHERE id = ? AND job_id = ?",
        (engagement_id, job_id),
    )
    if cursor.rowcount != 1:
        connection.rollback()
        raise EngagementNotFound("Tracked relationship or step not found.")
    connection.commit()
    return True
