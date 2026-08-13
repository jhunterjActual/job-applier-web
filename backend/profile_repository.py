"""Dual-mode profile and base-resume repositories.

Standalone installations keep their existing SQLite workspace.  The hosted
implementation uses transaction-local PostgreSQL tenant context and never
accepts an owner identifier from request payloads.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import json
from typing import Any, Protocol

from sqlalchemy import Engine, text

from base_resumes import (
    BaseResumeNotFound,
    LastBaseResumeError,
    activate_base_resume,
    delete_base_resume,
    get_base_resume,
    get_version,
    list_base_resumes,
    list_versions,
    normalize_professional_evidence,
    restore_version,
    save_base_resume,
)
from data_store import LOCAL_DATA_STORE, data_store_kind
from database import get_db_connection
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


class ProfileRepository(Protocol):
    def get_profile(self) -> dict[str, Any]: ...
    def update_profile(self, values: Mapping[str, Any]) -> dict[str, Any]: ...
    def cache_suggested_keywords_if_empty(self, keywords: str) -> bool: ...
    def list_base_resumes(self) -> list[dict[str, Any]]: ...
    def get_base_resume(self, resume_id: int) -> dict[str, Any]: ...
    def activate_base_resume(self, resume_id: int) -> dict[str, Any]: ...
    def list_versions(self, resume_id: int) -> list[dict[str, Any]]: ...
    def get_version(self, resume_id: int, version_number: int) -> dict[str, Any]: ...
    def restore_version(self, resume_id: int, version_number: int) -> dict[str, Any]: ...
    def delete_base_resume(self, resume_id: int) -> dict[str, Any] | None: ...


MAX_SUGGESTED_KEYWORDS_CHARS = 500


def _suggested_keywords(value: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > MAX_SUGGESTED_KEYWORDS_CHARS:
        raise ValueError(
            f"Suggested search keywords must be {MAX_SUGGESTED_KEYWORDS_CHARS} characters or fewer."
        )
    return normalized


def _iso_timestamp(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _json_evidence(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = {}
    return normalize_professional_evidence(value if isinstance(value, Mapping) else {})


def _resume_result(row: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: _iso_timestamp(value) for key, value in row.items()}
    result.pop("owner_user_id", None)
    result["professional_evidence"] = _json_evidence(result.pop("evidence", {}))
    return result


def _validated_resume(
    name: Any,
    resume_mode: Any,
    content: Any,
) -> tuple[str, str, str]:
    from base_resumes import MAX_BASE_RESUME_CHARS, MAX_BASE_RESUME_NAME, RESUME_MODES

    normalized_name = " ".join(str(name or "").split())
    if not normalized_name:
        raise ValueError("Resume name is required.")
    if len(normalized_name) > MAX_BASE_RESUME_NAME:
        raise ValueError(f"Resume names must be {MAX_BASE_RESUME_NAME} characters or fewer.")
    normalized_mode = str(resume_mode or "")
    if normalized_mode not in RESUME_MODES:
        raise ValueError("Resume mode is not supported.")
    normalized_content = str(content or "").strip()
    if len(normalized_content) > MAX_BASE_RESUME_CHARS:
        raise ValueError("Base resume content is too large.")
    return normalized_name, normalized_mode, normalized_content


class SQLiteProfileRepository:
    """Preserve the standalone SQLite behavior behind the repository contract."""

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

    def get_profile(self) -> dict[str, Any]:
        row = self._run(lambda connection: connection.execute("SELECT * FROM profile LIMIT 1").fetchone())
        return dict(row) if row else {}

    def cache_suggested_keywords_if_empty(self, keywords: str) -> bool:
        normalized = _suggested_keywords(keywords)
        if not normalized:
            return False

        def cache(connection: Any) -> bool:
            updated = connection.execute(
                """
                UPDATE profile
                SET suggested_keywords = ?
                WHERE id = 1 AND COALESCE(TRIM(suggested_keywords), '') = ''
                """,
                (normalized,),
            )
            return updated.rowcount == 1

        return self._run(cache, write=True)

    def update_profile(self, values: Mapping[str, Any]) -> dict[str, Any]:
        def update(connection: Any) -> dict[str, Any]:
            resume = None
            resume_id = values.get("base_resume_id")
            if resume_id is None and not values.get("base_resume_id_provided"):
                active = connection.execute(
                    "SELECT active_base_resume_id FROM profile WHERE id = 1"
                ).fetchone()
                resume_id = active["active_base_resume_id"] if active else None
            if resume_id is not None or str(values.get("base_resume_text") or "").strip():
                resume = save_base_resume(
                    connection,
                    resume_id,
                    str(values.get("base_resume_name") or ""),
                    str(values.get("resume_mode") or ""),
                    str(values.get("base_resume_text") or ""),
                    values.get("professional_evidence"),
                )
                activate_base_resume(connection, resume["id"])
            connection.execute(
                """
                UPDATE profile
                SET name = ?, email = ?, phone = ?, github = ?, linkedin = ?, website = ?,
                    base_resume_text = ?, resume_mode = ?, ai_provider = ?, ai_model = ?,
                    maps_provider = ?, prefer_us_headquarters = ?, interface_language = ?,
                    suggested_keywords = ''
                WHERE id = 1
                """,
                (
                    values.get("name"), values.get("email"), values.get("phone"),
                    values.get("github"), values.get("linkedin"), values.get("website"),
                    str(values.get("base_resume_text") or "").strip(), values.get("resume_mode"),
                    values.get("ai_provider"), values.get("ai_model"), values.get("maps_provider"),
                    int(bool(values.get("prefer_us_headquarters"))), values.get("interface_language"),
                ),
            )
            return {
                "base_resume_id": resume["id"] if resume else None,
                "base_resume_version": resume["version_number"] if resume else None,
                "base_resume_version_created": resume["version_created"] if resume else False,
            }

        return self._run(update, write=True)

    def list_base_resumes(self) -> list[dict[str, Any]]:
        return self._run(list_base_resumes)

    def get_base_resume(self, resume_id: int) -> dict[str, Any]:
        return self._run(lambda connection: get_base_resume(connection, resume_id))

    def activate_base_resume(self, resume_id: int) -> dict[str, Any]:
        return self._run(lambda connection: activate_base_resume(connection, resume_id), write=True)

    def list_versions(self, resume_id: int) -> list[dict[str, Any]]:
        return self._run(lambda connection: list_versions(connection, resume_id))

    def get_version(self, resume_id: int, version_number: int) -> dict[str, Any]:
        return self._run(lambda connection: get_version(connection, resume_id, version_number))

    def restore_version(self, resume_id: int, version_number: int) -> dict[str, Any]:
        return self._run(
            lambda connection: restore_version(connection, resume_id, version_number), write=True
        )

    def delete_base_resume(self, resume_id: int) -> dict[str, Any] | None:
        return self._run(lambda connection: delete_base_resume(connection, resume_id), write=True)


class PostgreSQLProfileRepository:
    """Owner-scoped PostgreSQL profile and resume persistence under forced RLS."""

    def __init__(self, owner_user_id: int, engine: Engine | None = None):
        self._owner_user_id = owner_user_id
        self._engine = engine or runtime_postgres_engine()

    def _run(self, action: Callable[[Any], Any]) -> Any:
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            return action(connection)

    def _profile_row(self, connection: Any, *, lock: bool = False) -> Mapping[str, Any] | None:
        row = connection.execute(
            text(
                """
                SELECT p.id, p.name, p.email, p.phone, p.github,
                       p.linkedin, p.website, p.suggested_keywords, p.ai_provider,
                       p.ai_model, p.maps_provider, p.prefer_us_headquarters,
                       p.interface_language, p.active_base_resume_id,
                       COALESCE(r.content, '') AS base_resume_text,
                       COALESCE(r.resume_mode, 'general_professional') AS resume_mode,
                       '' AS gemini_api_key, '' AS openai_api_key, '' AS google_maps_api_key
                FROM careertrellis.profiles AS p
                LEFT JOIN careertrellis.base_resumes AS r
                  ON r.id = p.active_base_resume_id
                 AND r.owner_user_id = p.owner_user_id
                 AND r.deleted_at IS NULL
                WHERE p.owner_user_id = :owner_user_id
                """ + (" FOR UPDATE OF p" if lock else "")
            ),
            {"owner_user_id": self._owner_user_id},
        ).mappings().one_or_none()
        return row

    def _resume_row(self, connection: Any, resume_id: int, *, lock: bool = False) -> Mapping[str, Any]:
        row = connection.execute(
            text(
                """
                SELECT id, name, resume_mode, content, evidence,
                       created_at, updated_at
                FROM careertrellis.base_resumes
                WHERE id = :resume_id
                  AND owner_user_id = :owner_user_id
                  AND deleted_at IS NULL
                """ + (" FOR UPDATE" if lock else "")
            ),
            {"resume_id": resume_id, "owner_user_id": self._owner_user_id},
        ).mappings().one_or_none()
        if row is None:
            raise BaseResumeNotFound("Base resume was not found.")
        return row

    def _version_number(self, connection: Any, resume_id: int) -> int:
        return int(
            connection.scalar(
                text(
                    """
                    SELECT COALESCE(MAX(version_number), 0)
                    FROM careertrellis.base_resume_versions
                    WHERE owner_user_id = :owner_user_id AND base_resume_id = :resume_id
                    """
                ),
                {"owner_user_id": self._owner_user_id, "resume_id": resume_id},
            )
            or 0
        )

    def _insert_version(
        self,
        connection: Any,
        resume_id: int,
        name: str,
        resume_mode: str,
        content: str,
        evidence: Mapping[str, str],
    ) -> int:
        version_number = self._version_number(connection, resume_id) + 1
        connection.execute(
            text(
                """
                INSERT INTO careertrellis.base_resume_versions
                    (owner_user_id, base_resume_id, version_number, name,
                     resume_mode, content, evidence)
                VALUES
                    (:owner_user_id, :resume_id, :version_number, :name,
                     :resume_mode, :content, CAST(:evidence AS jsonb))
                """
            ),
            {
                "owner_user_id": self._owner_user_id,
                "resume_id": resume_id,
                "version_number": version_number,
                "name": name,
                "resume_mode": resume_mode,
                "content": content,
                "evidence": json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            },
        )
        return version_number

    def _save_resume(
        self,
        connection: Any,
        resume_id: int | None,
        name: Any,
        resume_mode: Any,
        content: Any,
        professional_evidence: Any,
    ) -> dict[str, Any]:
        name, resume_mode, content = _validated_resume(name, resume_mode, content)
        changed = True
        if resume_id is None:
            evidence = normalize_professional_evidence(
                professional_evidence if isinstance(professional_evidence, Mapping) else {}
            )
            resume_id = int(
                connection.scalar(
                    text(
                        """
                        INSERT INTO careertrellis.base_resumes
                            (owner_user_id, name, resume_mode, content, evidence)
                        VALUES
                            (:owner_user_id, :name, :resume_mode, :content, CAST(:evidence AS jsonb))
                        RETURNING id
                        """
                    ),
                    {
                        "owner_user_id": self._owner_user_id,
                        "name": name,
                        "resume_mode": resume_mode,
                        "content": content,
                        "evidence": json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                    },
                )
            )
            version_number = self._insert_version(
                connection, resume_id, name, resume_mode, content, evidence
            )
        else:
            current = self._resume_row(connection, int(resume_id), lock=True)
            evidence = (
                _json_evidence(current["evidence"])
                if professional_evidence is None
                else normalize_professional_evidence(
                    professional_evidence if isinstance(professional_evidence, Mapping) else {}
                )
            )
            changed = any(
                (
                    current["name"] != name,
                    current["resume_mode"] != resume_mode,
                    current["content"] != content,
                    _json_evidence(current["evidence"]) != evidence,
                )
            )
            if changed:
                connection.execute(
                    text(
                        """
                        UPDATE careertrellis.base_resumes
                        SET name = :name, resume_mode = :resume_mode, content = :content,
                            evidence = CAST(:evidence AS jsonb), updated_at = CURRENT_TIMESTAMP
                        WHERE id = :resume_id AND owner_user_id = :owner_user_id
                          AND deleted_at IS NULL
                        """
                    ),
                    {
                        "name": name,
                        "resume_mode": resume_mode,
                        "content": content,
                        "evidence": json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                        "resume_id": resume_id,
                        "owner_user_id": self._owner_user_id,
                    },
                )
                version_number = self._insert_version(
                    connection, int(resume_id), name, resume_mode, content, evidence
                )
            else:
                version_number = self._version_number(connection, int(resume_id))
        result = _resume_result(self._resume_row(connection, int(resume_id)))
        result["version_number"] = version_number
        result["version_created"] = changed
        return result

    def _activate(self, connection: Any, resume_id: int) -> dict[str, Any]:
        if self._profile_row(connection, lock=True) is None:
            raise BaseResumeNotFound("The profile was not found.")
        row = self._resume_row(connection, resume_id, lock=True)
        updated = connection.execute(
            text(
                """
                UPDATE careertrellis.profiles
                SET active_base_resume_id = :resume_id, suggested_keywords = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE owner_user_id = :owner_user_id
                """
            ),
            {"resume_id": resume_id, "owner_user_id": self._owner_user_id},
        )
        if updated.rowcount != 1:
            raise BaseResumeNotFound("The profile was not found.")
        result = _resume_result(row)
        result["version_number"] = self._version_number(connection, resume_id)
        return result

    def get_profile(self) -> dict[str, Any]:
        row = self._run(self._profile_row)
        return {key: _iso_timestamp(value) for key, value in row.items()} if row else {}

    def cache_suggested_keywords_if_empty(self, keywords: str) -> bool:
        normalized = _suggested_keywords(keywords)
        if not normalized:
            return False

        def cache(connection: Any) -> bool:
            updated = connection.execute(
                text(
                    """
                    UPDATE careertrellis.profiles
                    SET suggested_keywords = :keywords, updated_at = CURRENT_TIMESTAMP
                    WHERE owner_user_id = :owner_user_id
                      AND COALESCE(BTRIM(suggested_keywords), '') = ''
                    """
                ),
                {"keywords": normalized, "owner_user_id": self._owner_user_id},
            )
            return updated.rowcount == 1

        return self._run(cache)

    def update_profile(self, values: Mapping[str, Any]) -> dict[str, Any]:
        def update(connection: Any) -> dict[str, Any]:
            current = self._profile_row(connection, lock=True)
            if current is None:
                raise BaseResumeNotFound("The profile was not found.")
            resume = None
            resume_id = values.get("base_resume_id")
            if resume_id is None and not values.get("base_resume_id_provided"):
                resume_id = current["active_base_resume_id"]
            if resume_id is not None or str(values.get("base_resume_text") or "").strip():
                resume = self._save_resume(
                    connection,
                    int(resume_id) if resume_id is not None else None,
                    values.get("base_resume_name"),
                    values.get("resume_mode"),
                    values.get("base_resume_text"),
                    values.get("professional_evidence"),
                )
                self._activate(connection, int(resume["id"]))
            connection.execute(
                text(
                    """
                    UPDATE careertrellis.profiles
                    SET name = :name, email = :email, phone = :phone, github = :github,
                        linkedin = :linkedin, website = :website,
                        ai_provider = :ai_provider, ai_model = :ai_model,
                        maps_provider = :maps_provider,
                        prefer_us_headquarters = :prefer_us_headquarters,
                        interface_language = :interface_language, suggested_keywords = '',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE owner_user_id = :owner_user_id
                    """
                ),
                {
                    "owner_user_id": self._owner_user_id,
                    "name": values.get("name"), "email": values.get("email"),
                    "phone": values.get("phone"), "github": values.get("github"),
                    "linkedin": values.get("linkedin"), "website": values.get("website"),
                    "ai_provider": values.get("ai_provider"), "ai_model": values.get("ai_model"),
                    "maps_provider": values.get("maps_provider"),
                    "prefer_us_headquarters": bool(values.get("prefer_us_headquarters")),
                    "interface_language": values.get("interface_language"),
                },
            )
            return {
                "base_resume_id": resume["id"] if resume else None,
                "base_resume_version": resume["version_number"] if resume else None,
                "base_resume_version_created": resume["version_created"] if resume else False,
            }

        return self._run(update)

    def list_base_resumes(self) -> list[dict[str, Any]]:
        def load(connection: Any) -> list[dict[str, Any]]:
            rows = connection.execute(
                text(
                    """
                    SELECT r.id, r.name, r.resume_mode, r.created_at, r.updated_at,
                           COUNT(v.id) AS version_count,
                           (p.active_base_resume_id = r.id) AS active
                    FROM careertrellis.base_resumes AS r
                    JOIN careertrellis.profiles AS p ON p.owner_user_id = r.owner_user_id
                    LEFT JOIN careertrellis.base_resume_versions AS v
                      ON v.base_resume_id = r.id AND v.owner_user_id = r.owner_user_id
                    WHERE r.owner_user_id = :owner_user_id AND r.deleted_at IS NULL
                    GROUP BY r.id, p.active_base_resume_id
                    ORDER BY active DESC, r.updated_at DESC, r.id DESC
                    """
                ),
                {"owner_user_id": self._owner_user_id},
            ).mappings().all()
            return [
                {key: _iso_timestamp(value) for key, value in row.items()}
                for row in rows
            ]

        return self._run(load)

    def get_base_resume(self, resume_id: int) -> dict[str, Any]:
        def load(connection: Any) -> dict[str, Any]:
            result = _resume_result(self._resume_row(connection, resume_id))
            result["version_number"] = self._version_number(connection, resume_id)
            profile = self._profile_row(connection)
            result["active"] = bool(profile and profile["active_base_resume_id"] == resume_id)
            return result

        return self._run(load)

    def activate_base_resume(self, resume_id: int) -> dict[str, Any]:
        return self._run(lambda connection: self._activate(connection, resume_id))

    def list_versions(self, resume_id: int) -> list[dict[str, Any]]:
        def load(connection: Any) -> list[dict[str, Any]]:
            self._resume_row(connection, resume_id)
            rows = connection.execute(
                text(
                    """
                    SELECT id, base_resume_id, version_number, name, resume_mode,
                           created_at, char_length(content) AS character_count, evidence
                    FROM careertrellis.base_resume_versions
                    WHERE owner_user_id = :owner_user_id AND base_resume_id = :resume_id
                    ORDER BY version_number DESC
                    """
                ),
                {"owner_user_id": self._owner_user_id, "resume_id": resume_id},
            ).mappings().all()
            results = []
            for row in rows:
                result = {key: _iso_timestamp(value) for key, value in row.items()}
                evidence = _json_evidence(result.pop("evidence", {}))
                result["evidence_section_count"] = sum(bool(value) for value in evidence.values())
                results.append(result)
            return results

        return self._run(load)

    def get_version(self, resume_id: int, version_number: int) -> dict[str, Any]:
        def load(connection: Any) -> dict[str, Any]:
            self._resume_row(connection, resume_id)
            row = connection.execute(
                text(
                    """
                    SELECT id, base_resume_id, version_number, name,
                           resume_mode, content, evidence, created_at
                    FROM careertrellis.base_resume_versions
                    WHERE owner_user_id = :owner_user_id AND base_resume_id = :resume_id
                      AND version_number = :version_number
                    """
                ),
                {
                    "owner_user_id": self._owner_user_id,
                    "resume_id": resume_id,
                    "version_number": version_number,
                },
            ).mappings().one_or_none()
            if row is None:
                raise BaseResumeNotFound("Base resume version was not found.")
            return _resume_result(row)

        return self._run(load)

    def restore_version(self, resume_id: int, version_number: int) -> dict[str, Any]:
        def restore(connection: Any) -> dict[str, Any]:
            if self._profile_row(connection, lock=True) is None:
                raise BaseResumeNotFound("The profile was not found.")
            self._resume_row(connection, resume_id, lock=True)
            row = connection.execute(
                text(
                    """
                    SELECT name, resume_mode, content, evidence
                    FROM careertrellis.base_resume_versions
                    WHERE owner_user_id = :owner_user_id AND base_resume_id = :resume_id
                      AND version_number = :version_number
                    """
                ),
                {
                    "owner_user_id": self._owner_user_id,
                    "resume_id": resume_id,
                    "version_number": version_number,
                },
            ).mappings().one_or_none()
            if row is None:
                raise BaseResumeNotFound("Base resume version was not found.")
            evidence = _json_evidence(row["evidence"])
            connection.execute(
                text(
                    """
                    UPDATE careertrellis.base_resumes
                    SET name = :name, resume_mode = :resume_mode, content = :content,
                        evidence = CAST(:evidence AS jsonb), updated_at = CURRENT_TIMESTAMP
                    WHERE id = :resume_id AND owner_user_id = :owner_user_id
                      AND deleted_at IS NULL
                    """
                ),
                {
                    "name": row["name"], "resume_mode": row["resume_mode"],
                    "content": row["content"],
                    "evidence": json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                    "resume_id": resume_id, "owner_user_id": self._owner_user_id,
                },
            )
            new_version = self._insert_version(
                connection, resume_id, row["name"], row["resume_mode"], row["content"], evidence
            )
            restored = _resume_result(self._resume_row(connection, resume_id))
            restored["version_number"] = new_version
            restored["version_created"] = True
            restored["restored_from_version"] = version_number
            return restored

        return self._run(restore)

    def delete_base_resume(self, resume_id: int) -> dict[str, Any] | None:
        def remove(connection: Any) -> dict[str, Any] | None:
            profile = self._profile_row(connection, lock=True)
            if profile is None:
                raise BaseResumeNotFound("The profile was not found.")
            active_rows = connection.execute(
                text(
                    """
                    SELECT id
                    FROM careertrellis.base_resumes
                    WHERE owner_user_id = :owner_user_id AND deleted_at IS NULL
                    ORDER BY updated_at DESC, id DESC
                    FOR UPDATE
                    """
                ),
                {"owner_user_id": self._owner_user_id},
            ).scalars().all()
            if resume_id not in active_rows:
                raise BaseResumeNotFound("Base resume was not found.")
            if len(active_rows) <= 1:
                raise LastBaseResumeError(
                    "Keep at least one base resume. Create another resume before deleting this one."
                )
            replacement_id = next(identifier for identifier in active_rows if identifier != resume_id)
            replacement = None
            if profile and profile["active_base_resume_id"] == resume_id:
                replacement = self._activate(connection, int(replacement_id))
            connection.execute(
                text(
                    """
                    UPDATE careertrellis.base_resumes
                    SET deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :resume_id AND owner_user_id = :owner_user_id
                      AND deleted_at IS NULL
                    """
                ),
                {"resume_id": resume_id, "owner_user_id": self._owner_user_id},
            )
            return replacement

        return self._run(remove)


def runtime_profile_repository(
    *,
    sqlite_connection_factory: Callable[[], Any] = get_db_connection,
    engine: Engine | None = None,
) -> ProfileRepository:
    """Return the configured repository without allowing payload-selected ownership."""
    if data_store_kind() == LOCAL_DATA_STORE:
        return SQLiteProfileRepository(sqlite_connection_factory)
    return PostgreSQLProfileRepository(current_owner_user_id(), engine=engine)
