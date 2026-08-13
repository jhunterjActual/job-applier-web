"""Dual-mode repositories for application engagement and interview workspaces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
import hashlib
import hmac
import json
import secrets
from typing import Any, Protocol

from sqlalchemy import Engine, text

from application_engagements import (
    EngagementLimitError,
    EngagementNotFound,
    MAX_ENGAGEMENTS_PER_JOB,
    validated_engagement_values,
)
from data_store import LOCAL_DATA_STORE, data_store_kind
from database import get_db_connection
from interview_prep import normalize_interview_prep_content
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


_WORKSPACE_HMAC_KEY = secrets.token_bytes(32)


class ApplicationWorkspaceNotFound(EngagementNotFound):
    """Raised without disclosing whether another tenant owns the selected row."""


class ApplicationWorkspaceChanged(ValueError):
    """Raised when AI-generated content was based on an obsolete workspace."""


class ApplicationWorkspaceRepository(Protocol):
    def list_engagements(self, job_id: int) -> dict[str, Any]: ...
    def create_engagement(
        self, job_id: int, values: dict[str, object]
    ) -> dict[str, Any]: ...
    def update_engagement(
        self, job_id: int, engagement_id: int, values: dict[str, object]
    ) -> dict[str, Any]: ...
    def delete_engagement(self, job_id: int, engagement_id: int) -> None: ...
    def get_interview_workspace(
        self, job_id: int, *, for_generation: bool = False
    ) -> dict[str, Any]: ...
    def save_interview_prep(
        self,
        job_id: int,
        content: object,
        *,
        expected_context_token: str | None = None,
    ) -> dict[str, Any]: ...


def _positive_id(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer.")
    return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _api_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _api_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: _api_value(value) for key, value in dict(row).items()}
    result.pop("owner_user_id", None)
    return result


_CONTEXT_FIELDS = (
    "job_id",
    "company",
    "position",
    "status",
    "notes",
    "tailored_resume_text",
    "interview_prep",
    "interview_prep_updated_at",
    "title",
    "description",
    "match_analysis",
)


def _context_token(mode_owner: str, workspace: Mapping[str, Any]) -> str:
    values = dict(workspace)
    payload = json.dumps(
        {
            "mode_owner": mode_owner,
            "workspace": {
                field: _api_value(values.get(field)) for field in _CONTEXT_FIELDS
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(_WORKSPACE_HMAC_KEY, payload, hashlib.sha256).hexdigest()


def _require_context_token(
    mode_owner: str,
    workspace: Mapping[str, Any],
    supplied_token: object,
) -> None:
    candidate = supplied_token if isinstance(supplied_token, str) else ""
    if not hmac.compare_digest(_context_token(mode_owner, workspace), candidate):
        raise ApplicationWorkspaceChanged(
            "The application workspace changed while interview preparation was generated. "
            "Review the current workspace and generate again."
        )


def _with_optional_token(
    mode_owner: str,
    workspace: Mapping[str, Any],
    *,
    for_generation: bool,
) -> dict[str, Any]:
    result = _api_row(workspace)
    if for_generation:
        result["context_token"] = _context_token(mode_owner, workspace)
    return result


class SQLiteApplicationWorkspaceRepository:
    """Preserve standalone application workspace behavior under SQLite locks."""

    _MODE_OWNER = "sqlite:standalone"

    def __init__(self, connection_factory: Callable[[], Any] = get_db_connection):
        self._connection_factory = connection_factory

    @staticmethod
    def _job(connection: Any, job_id: int) -> Mapping[str, Any]:
        row = connection.execute(
            "SELECT id, company, title FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            raise ApplicationWorkspaceNotFound("Job not found.")
        return row

    @staticmethod
    def _workspace(connection: Any, job_id: int) -> Mapping[str, Any]:
        row = connection.execute(
            """
            SELECT a.job_id, a.company, a.position, a.status, a.notes,
                   a.tailored_resume_text, a.interview_prep,
                   a.interview_prep_updated_at, j.title, j.description,
                   j.match_analysis
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            WHERE a.job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if not row:
            raise ApplicationWorkspaceNotFound("Application record not found.")
        return row

    @staticmethod
    def _engagement(connection: Any, job_id: int, engagement_id: int) -> Mapping[str, Any]:
        row = connection.execute(
            "SELECT * FROM application_engagements WHERE id = ? AND job_id = ?",
            (engagement_id, job_id),
        ).fetchone()
        if not row:
            raise ApplicationWorkspaceNotFound(
                "Tracked relationship or step not found."
            )
        return row

    def list_engagements(self, job_id: int) -> dict[str, Any]:
        safe_job_id = _positive_id(job_id, "Job identifier")
        connection = self._connection_factory()
        try:
            job = self._job(connection, safe_job_id)
            rows = connection.execute(
                """
                SELECT id, job_id, engagement_type, name, organization,
                       contact_details, status, activity_on, next_action_on,
                       notes, created_at, updated_at
                FROM application_engagements WHERE job_id = ?
                ORDER BY CASE WHEN next_action_on IS NULL OR next_action_on = ''
                              THEN 1 ELSE 0 END,
                         next_action_on, updated_at DESC, id DESC
                """,
                (safe_job_id,),
            ).fetchall()
            return {
                "job_id": safe_job_id,
                "company": job["company"],
                "position": job["title"],
                "count": len(rows),
                "engagements": [_api_row(row) for row in rows],
            }
        finally:
            connection.close()

    def create_engagement(
        self, job_id: int, values: dict[str, object]
    ) -> dict[str, Any]:
        safe_job_id = _positive_id(job_id, "Job identifier")
        normalized = validated_engagement_values(values)
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._job(connection, safe_job_id)
            count = int(connection.execute(
                "SELECT COUNT(*) FROM application_engagements WHERE job_id = ?",
                (safe_job_id,),
            ).fetchone()[0])
            if count >= MAX_ENGAGEMENTS_PER_JOB:
                raise EngagementLimitError(
                    f"This job already has the maximum of "
                    f"{MAX_ENGAGEMENTS_PER_JOB} tracked records."
                )
            now = _utc_now().isoformat(timespec="seconds")
            cursor = connection.execute(
                """
                INSERT INTO application_engagements (
                    job_id, engagement_type, name, organization, contact_details,
                    status, activity_on, next_action_on, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe_job_id,
                    normalized["engagement_type"], normalized["name"],
                    normalized["organization"], normalized["contact_details"],
                    normalized["status"], normalized["activity_on"],
                    normalized["next_action_on"], normalized["notes"], now, now,
                ),
            )
            row = self._engagement(connection, safe_job_id, cursor.lastrowid)
            connection.commit()
            return _api_row(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_engagement(
        self, job_id: int, engagement_id: int, values: dict[str, object]
    ) -> dict[str, Any]:
        safe_job_id = _positive_id(job_id, "Job identifier")
        safe_engagement_id = _positive_id(engagement_id, "Engagement identifier")
        normalized = validated_engagement_values(values)
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._job(connection, safe_job_id)
            self._engagement(connection, safe_job_id, safe_engagement_id)
            cursor = connection.execute(
                """
                UPDATE application_engagements
                SET engagement_type = ?, name = ?, organization = ?,
                    contact_details = ?, status = ?, activity_on = ?,
                    next_action_on = ?, notes = ?, updated_at = ?
                WHERE id = ? AND job_id = ?
                """,
                (
                    normalized["engagement_type"], normalized["name"],
                    normalized["organization"], normalized["contact_details"],
                    normalized["status"], normalized["activity_on"],
                    normalized["next_action_on"], normalized["notes"],
                    _utc_now().isoformat(timespec="seconds"),
                    safe_engagement_id, safe_job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ApplicationWorkspaceNotFound(
                    "Tracked relationship or step not found."
                )
            row = self._engagement(connection, safe_job_id, safe_engagement_id)
            connection.commit()
            return _api_row(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_engagement(self, job_id: int, engagement_id: int) -> None:
        safe_job_id = _positive_id(job_id, "Job identifier")
        safe_engagement_id = _positive_id(engagement_id, "Engagement identifier")
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._job(connection, safe_job_id)
            self._engagement(connection, safe_job_id, safe_engagement_id)
            cursor = connection.execute(
                "DELETE FROM application_engagements WHERE id = ? AND job_id = ?",
                (safe_engagement_id, safe_job_id),
            )
            if cursor.rowcount != 1:
                raise ApplicationWorkspaceNotFound(
                    "Tracked relationship or step not found."
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_interview_workspace(
        self, job_id: int, *, for_generation: bool = False
    ) -> dict[str, Any]:
        safe_job_id = _positive_id(job_id, "Job identifier")
        connection = self._connection_factory()
        try:
            workspace = self._workspace(connection, safe_job_id)
            return _with_optional_token(
                self._MODE_OWNER, workspace, for_generation=for_generation
            )
        finally:
            connection.close()

    def save_interview_prep(
        self,
        job_id: int,
        content: object,
        *,
        expected_context_token: str | None = None,
    ) -> dict[str, Any]:
        safe_job_id = _positive_id(job_id, "Job identifier")
        normalized = normalize_interview_prep_content(content)
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            workspace = self._workspace(connection, safe_job_id)
            if expected_context_token is not None:
                _require_context_token(
                    self._MODE_OWNER, workspace, expected_context_token
                )
            updated_at = _utc_now().isoformat(timespec="seconds")
            cursor = connection.execute(
                """
                UPDATE applications
                SET interview_prep = ?, interview_prep_updated_at = ?
                WHERE job_id = ?
                """,
                (normalized, updated_at, safe_job_id),
            )
            if cursor.rowcount != 1:
                raise ApplicationWorkspaceNotFound("Application record not found.")
            connection.commit()
            return {"content": normalized, "updated_at": updated_at}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class PostgreSQLApplicationWorkspaceRepository:
    """Owner-scoped application workspaces under transaction-local forced RLS."""

    def __init__(self, owner_user_id: int, engine: Engine | None = None):
        self._owner_user_id = owner_user_id
        self._engine = engine or runtime_postgres_engine()
        self._mode_owner = f"postgres:{owner_user_id}"

    def _write_timeouts(self, connection: Any) -> None:
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(text("SET LOCAL statement_timeout = '30s'"))

    def _job(
        self, connection: Any, job_id: int, *, for_update: bool = False
    ) -> Mapping[str, Any]:
        row = connection.execute(
            text(
                """
                SELECT id, company, title, description, match_analysis
                FROM careertrellis.jobs
                WHERE id = :job_id AND owner_user_id = :owner_user_id
                """ + (" FOR UPDATE" if for_update else "")
            ),
            {"job_id": job_id, "owner_user_id": self._owner_user_id},
        ).mappings().one_or_none()
        if not row:
            raise ApplicationWorkspaceNotFound("Job not found.")
        return row

    def _application(
        self, connection: Any, job_id: int, *, for_update: bool = False
    ) -> Mapping[str, Any]:
        row = connection.execute(
            text(
                """
                SELECT job_id, company, position, status, notes,
                       tailored_resume_text, interview_prep,
                       interview_prep_updated_at
                FROM careertrellis.applications
                WHERE job_id = :job_id AND owner_user_id = :owner_user_id
                """ + (" FOR UPDATE" if for_update else "")
            ),
            {"job_id": job_id, "owner_user_id": self._owner_user_id},
        ).mappings().one_or_none()
        if not row:
            raise ApplicationWorkspaceNotFound("Application record not found.")
        return row

    @staticmethod
    def _workspace(job: Mapping[str, Any], application: Mapping[str, Any]) -> dict[str, Any]:
        return {
            **dict(application),
            "title": job["title"],
            "description": job["description"],
            "match_analysis": job["match_analysis"],
        }

    def _engagement(
        self,
        connection: Any,
        job_id: int,
        engagement_id: int,
        *,
        for_update: bool = False,
    ) -> Mapping[str, Any]:
        row = connection.execute(
            text(
                """
                SELECT id, job_id, engagement_type, name, organization,
                       contact_details, status, activity_on, next_action_on,
                       notes, created_at, updated_at
                FROM careertrellis.application_engagements
                WHERE id = :engagement_id AND job_id = :job_id
                  AND owner_user_id = :owner_user_id
                """ + (" FOR UPDATE" if for_update else "")
            ),
            {
                "engagement_id": engagement_id,
                "job_id": job_id,
                "owner_user_id": self._owner_user_id,
            },
        ).mappings().one_or_none()
        if not row:
            raise ApplicationWorkspaceNotFound(
                "Tracked relationship or step not found."
            )
        return row

    def list_engagements(self, job_id: int) -> dict[str, Any]:
        safe_job_id = _positive_id(job_id, "Job identifier")
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            job = self._job(connection, safe_job_id)
            rows = connection.execute(
                text(
                    """
                    SELECT id, job_id, engagement_type, name, organization,
                           contact_details, status, activity_on, next_action_on,
                           notes, created_at, updated_at
                    FROM careertrellis.application_engagements
                    WHERE job_id = :job_id AND owner_user_id = :owner_user_id
                    ORDER BY CASE WHEN next_action_on IS NULL THEN 1 ELSE 0 END,
                             next_action_on, updated_at DESC, id DESC
                    """
                ),
                {"job_id": safe_job_id, "owner_user_id": self._owner_user_id},
            ).mappings().all()
            return {
                "job_id": safe_job_id,
                "company": job["company"],
                "position": job["title"],
                "count": len(rows),
                "engagements": [_api_row(row) for row in rows],
            }

    def create_engagement(
        self, job_id: int, values: dict[str, object]
    ) -> dict[str, Any]:
        safe_job_id = _positive_id(job_id, "Job identifier")
        normalized = validated_engagement_values(values)
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            self._write_timeouts(connection)
            self._job(connection, safe_job_id, for_update=True)
            count = int(connection.scalar(
                text(
                    "SELECT COUNT(*) FROM careertrellis.application_engagements "
                    "WHERE job_id = :job_id AND owner_user_id = :owner_user_id"
                ),
                {"job_id": safe_job_id, "owner_user_id": self._owner_user_id},
            ) or 0)
            if count >= MAX_ENGAGEMENTS_PER_JOB:
                raise EngagementLimitError(
                    f"This job already has the maximum of "
                    f"{MAX_ENGAGEMENTS_PER_JOB} tracked records."
                )
            now = _utc_now()
            row = connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.application_engagements (
                        owner_user_id, job_id, engagement_type, name,
                        organization, contact_details, status, activity_on,
                        next_action_on, notes, created_at, updated_at
                    ) VALUES (
                        :owner_user_id, :job_id, :engagement_type, :name,
                        :organization, :contact_details, :status, :activity_on,
                        :next_action_on, :notes, :now, :now
                    ) RETURNING id, job_id, engagement_type, name, organization,
                        contact_details, status, activity_on, next_action_on,
                        notes, created_at, updated_at
                    """
                ),
                {
                    "owner_user_id": self._owner_user_id,
                    "job_id": safe_job_id,
                    **normalized,
                    "now": now,
                },
            ).mappings().one()
            return _api_row(row)

    def update_engagement(
        self, job_id: int, engagement_id: int, values: dict[str, object]
    ) -> dict[str, Any]:
        safe_job_id = _positive_id(job_id, "Job identifier")
        safe_engagement_id = _positive_id(engagement_id, "Engagement identifier")
        normalized = validated_engagement_values(values)
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            self._write_timeouts(connection)
            self._job(connection, safe_job_id, for_update=True)
            self._engagement(
                connection, safe_job_id, safe_engagement_id, for_update=True
            )
            row = connection.execute(
                text(
                    """
                    UPDATE careertrellis.application_engagements
                    SET engagement_type = :engagement_type, name = :name,
                        organization = :organization,
                        contact_details = :contact_details, status = :status,
                        activity_on = :activity_on, next_action_on = :next_action_on,
                        notes = :notes, updated_at = :now
                    WHERE id = :engagement_id AND job_id = :job_id
                      AND owner_user_id = :owner_user_id
                    RETURNING id, job_id, engagement_type, name, organization,
                        contact_details, status, activity_on, next_action_on,
                        notes, created_at, updated_at
                    """
                ),
                {
                    "owner_user_id": self._owner_user_id,
                    "job_id": safe_job_id,
                    "engagement_id": safe_engagement_id,
                    **normalized,
                    "now": _utc_now(),
                },
            ).mappings().one_or_none()
            if not row:
                raise ApplicationWorkspaceNotFound(
                    "Tracked relationship or step not found."
                )
            return _api_row(row)

    def delete_engagement(self, job_id: int, engagement_id: int) -> None:
        safe_job_id = _positive_id(job_id, "Job identifier")
        safe_engagement_id = _positive_id(engagement_id, "Engagement identifier")
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            self._write_timeouts(connection)
            self._job(connection, safe_job_id, for_update=True)
            self._engagement(
                connection, safe_job_id, safe_engagement_id, for_update=True
            )
            cursor = connection.execute(
                text(
                    """
                    DELETE FROM careertrellis.application_engagements
                    WHERE id = :engagement_id AND job_id = :job_id
                      AND owner_user_id = :owner_user_id
                    """
                ),
                {
                    "engagement_id": safe_engagement_id,
                    "job_id": safe_job_id,
                    "owner_user_id": self._owner_user_id,
                },
            )
            if cursor.rowcount != 1:
                raise ApplicationWorkspaceNotFound(
                    "Tracked relationship or step not found."
                )

    def get_interview_workspace(
        self, job_id: int, *, for_generation: bool = False
    ) -> dict[str, Any]:
        safe_job_id = _positive_id(job_id, "Job identifier")
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            job = self._job(connection, safe_job_id)
            workspace = self._workspace(
                job, self._application(connection, safe_job_id)
            )
            return _with_optional_token(
                self._mode_owner, workspace, for_generation=for_generation
            )

    def save_interview_prep(
        self,
        job_id: int,
        content: object,
        *,
        expected_context_token: str | None = None,
    ) -> dict[str, Any]:
        safe_job_id = _positive_id(job_id, "Job identifier")
        normalized = normalize_interview_prep_content(content)
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            self._write_timeouts(connection)
            job = self._job(connection, safe_job_id, for_update=True)
            application = self._application(
                connection, safe_job_id, for_update=True
            )
            workspace = self._workspace(job, application)
            if expected_context_token is not None:
                _require_context_token(
                    self._mode_owner, workspace, expected_context_token
                )
            updated_at = _utc_now()
            cursor = connection.execute(
                text(
                    """
                    UPDATE careertrellis.applications
                    SET interview_prep = :content,
                        interview_prep_updated_at = :updated_at
                    WHERE job_id = :job_id AND owner_user_id = :owner_user_id
                    """
                ),
                {
                    "content": normalized,
                    "updated_at": updated_at,
                    "job_id": safe_job_id,
                    "owner_user_id": self._owner_user_id,
                },
            )
            if cursor.rowcount != 1:
                raise ApplicationWorkspaceNotFound("Application record not found.")
            return {"content": normalized, "updated_at": updated_at.isoformat()}


def runtime_application_workspace_repository(
    *,
    sqlite_connection_factory: Callable[[], Any] = get_db_connection,
    engine: Engine | None = None,
) -> ApplicationWorkspaceRepository:
    """Return the configured repository without caller-selected ownership."""
    if data_store_kind() == LOCAL_DATA_STORE:
        return SQLiteApplicationWorkspaceRepository(sqlite_connection_factory)
    return PostgreSQLApplicationWorkspaceRepository(
        current_owner_user_id(), engine=engine
    )
