"""Owner-scoped generated-material persistence for shared PostgreSQL hosting."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any
import uuid

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from data_store import SHARED_DATA_STORE, data_store_kind
from discovery_repository import acquire_owner_job_catalog_lock
from object_store import (
    EncryptedStagedObject,
    ObjectBackend,
    ObjectKeyring,
    StoredObjectMetadata,
    discard_staged_object,
    runtime_object_backend,
    stage_object,
)
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


RECONCILE_LIMIT = 20


class GeneratedMaterialNotFound(LookupError):
    """The authenticated owner has no matching job or material record."""


class GeneratedMaterialChanged(RuntimeError):
    """The generation inputs changed while external work was running."""


class GeneratedMaterialUnavailable(RuntimeError):
    """Generated-material persistence is temporarily unavailable."""


def _positive_id(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive integer.")
    try:
        selected = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer.") from exc
    if selected <= 0 or str(selected) != str(value):
        raise ValueError(f"{label} must be a positive integer.")
    return selected


def _token_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _token_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_token_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _context_token(context: Mapping[str, object]) -> str:
    encoded = json.dumps(
        _token_value(context), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _metadata_values(metadata: StoredObjectMetadata) -> dict[str, object]:
    return {
        "id": metadata.id,
        "owner_user_id": metadata.owner_user_id,
        "object_key": metadata.object_key,
        "content_type": metadata.content_type,
        "plaintext_size": metadata.size_bytes,
        "sha256": bytes.fromhex(metadata.sha256),
        "wrapped_data_key": metadata.wrapped_data_key,
    }


class PostgreSQLGeneratedMaterialRepository:
    """Persist encrypted objects and application references under forced RLS."""

    def __init__(
        self,
        owner_user_id: int,
        *,
        engine: Engine | None = None,
        backend: ObjectBackend | None = None,
        keyring: ObjectKeyring | None = None,
    ) -> None:
        self._owner_user_id = _positive_id(owner_user_id, "Owner identifier")
        self._engine = engine or runtime_postgres_engine()
        self._backend = backend or runtime_object_backend()
        self._keyring = keyring

    def _context_rows(
        self,
        connection: Any,
        job_id: int,
        *,
        lock: bool = False,
    ) -> dict[str, object]:
        lock_clause = " FOR UPDATE" if lock else ""
        job = connection.execute(
            text(
                """
                SELECT id, title, company, description, url, location, source, status
                FROM careertrellis.jobs
                WHERE id = :job_id AND owner_user_id = :owner_user_id
                """ + lock_clause
            ),
            {"job_id": job_id, "owner_user_id": self._owner_user_id},
        ).mappings().one_or_none()
        if job is None:
            raise GeneratedMaterialNotFound("Job not found.")

        profile = connection.execute(
            text(
                """
                SELECT name, ai_provider, ai_model, maps_provider,
                       prefer_us_headquarters, active_base_resume_id
                FROM careertrellis.profiles
                WHERE owner_user_id = :owner_user_id
                """ + lock_clause
            ),
            {"owner_user_id": self._owner_user_id},
        ).mappings().one_or_none()
        if profile is None:
            raise GeneratedMaterialNotFound("Profile settings were not found.")

        resume = None
        version_number = None
        resume_id = profile["active_base_resume_id"]
        if resume_id is not None:
            resume = connection.execute(
                text(
                    """
                    SELECT id, name, resume_mode, content, evidence, updated_at
                    FROM careertrellis.base_resumes
                    WHERE id = :resume_id AND owner_user_id = :owner_user_id
                      AND deleted_at IS NULL
                    """ + lock_clause
                ),
                {"resume_id": resume_id, "owner_user_id": self._owner_user_id},
            ).mappings().one_or_none()
            if resume is not None:
                version_number = connection.scalar(
                    text(
                        """
                        SELECT COALESCE(MAX(version_number), 0)
                        FROM careertrellis.base_resume_versions
                        WHERE base_resume_id = :resume_id
                          AND owner_user_id = :owner_user_id
                        """
                    ),
                    {"resume_id": resume_id, "owner_user_id": self._owner_user_id},
                )

        application = connection.execute(
            text(
                """
                SELECT id, job_id, company, position, us_hq, status,
                       tailored_resume_object_id, cover_letter_object_id,
                       tailored_resume_text, cover_letter, created_at, tailored_at,
                       headquarters_source, headquarters_attribution,
                       base_resume_id, base_resume_name, base_resume_version
                FROM careertrellis.applications
                WHERE job_id = :job_id AND owner_user_id = :owner_user_id
                """ + lock_clause
            ),
            {"job_id": job_id, "owner_user_id": self._owner_user_id},
        ).mappings().one_or_none()
        return {
            "job": dict(job),
            "profile": dict(profile),
            "resume": dict(resume) if resume is not None else None,
            "resume_version": int(version_number or 0) if resume is not None else None,
            "application": dict(application) if application is not None else None,
        }

    def generation_context(self, job_id: object) -> dict[str, object]:
        """Read one consistent owner context, then close before provider work."""
        selected_job_id = _positive_id(job_id, "Job identifier")
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                context = self._context_rows(connection, selected_job_id)
        except GeneratedMaterialNotFound:
            raise
        except SQLAlchemyError as exc:
            raise GeneratedMaterialUnavailable() from exc
        resume = context["resume"]
        if not isinstance(resume, Mapping) or not str(resume.get("content") or "").strip():
            raise GeneratedMaterialNotFound("Base resume text is missing.")
        profile = dict(context["profile"])
        profile.update(
            {
                "base_resume_text": resume["content"],
                "resume_mode": resume["resume_mode"],
            }
        )
        return {
            "context_token": _context_token(context),
            "job": dict(context["job"]),
            "profile": profile,
            "source_resume_id": int(resume["id"]),
            "source_resume_name": str(resume["name"]),
            "source_resume_version": int(context["resume_version"] or 0),
            "source_professional_evidence": dict(resume.get("evidence") or {}),
        }

    def edit_context(self, job_id: object) -> dict[str, object]:
        """Read reviewed material source and profile data outside PDF work."""
        selected_job_id = _positive_id(job_id, "Job identifier")
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                context = self._context_rows(connection, selected_job_id)
        except GeneratedMaterialNotFound:
            raise
        except SQLAlchemyError as exc:
            raise GeneratedMaterialUnavailable() from exc
        application = context["application"]
        if not isinstance(application, Mapping):
            raise GeneratedMaterialNotFound("Generated application materials were not found.")
        resume = context["resume"]
        profile = dict(context["profile"])
        profile["resume_mode"] = (
            str(resume.get("resume_mode") or "general_professional")
            if isinstance(resume, Mapping)
            else "general_professional"
        )
        return {
            "context_token": _context_token(context),
            "application": dict(application),
            "profile": profile,
            "job": dict(context["job"]),
        }

    def materials(self, job_id: object) -> dict[str, object]:
        """Load only owner-authorized material references and display metadata."""
        selected_job_id = _positive_id(job_id, "Job identifier")
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = connection.execute(
                    text(
                        """
                        SELECT a.job_id, a.company, a.position, a.us_hq, a.status,
                               a.tailored_resume_object_id, a.cover_letter_object_id,
                               a.tailored_resume_text, a.cover_letter,
                               a.headquarters_source, a.headquarters_attribution,
                               a.base_resume_id, a.base_resume_name,
                               a.base_resume_version, j.url AS job_url,
                               j.source AS job_source, j.status AS job_status,
                               p.name AS candidate_name
                        FROM careertrellis.applications AS a
                        JOIN careertrellis.jobs AS j
                          ON j.id = a.job_id AND j.owner_user_id = a.owner_user_id
                        JOIN careertrellis.profiles AS p
                          ON p.owner_user_id = a.owner_user_id
                        WHERE a.job_id = :job_id
                          AND a.owner_user_id = :owner_user_id
                        """
                    ),
                    {"job_id": selected_job_id, "owner_user_id": self._owner_user_id},
                ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise GeneratedMaterialUnavailable() from exc
        if row is None:
            raise GeneratedMaterialNotFound("Generated application materials were not found.")
        result = dict(row)
        for key in ("tailored_resume_object_id", "cover_letter_object_id"):
            if result[key] is not None:
                result[key] = str(result[key])
        return result

    @staticmethod
    def _insert_metadata(connection: Any, metadata: StoredObjectMetadata) -> None:
        connection.execute(
            text(
                """
                INSERT INTO careertrellis.stored_objects
                    (id, owner_user_id, object_key, content_type, plaintext_size,
                     sha256, wrapped_data_key)
                VALUES
                    (:id, :owner_user_id, :object_key, :content_type,
                     :plaintext_size, :sha256, :wrapped_data_key)
                """
            ),
            _metadata_values(metadata),
        )

    def _commit_replacement(
        self,
        job_id: int,
        expected_context_token: str,
        resume: EncryptedStagedObject,
        letter: EncryptedStagedObject,
        values: Mapping[str, object],
        *,
        require_application: bool,
    ) -> list[str]:
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL statement_timeout = '30s'"))
            acquire_owner_job_catalog_lock(connection, self._owner_user_id)
            context = self._context_rows(connection, job_id, lock=True)
            if require_application and context["application"] is None:
                raise GeneratedMaterialNotFound(
                    "Generated application materials were not found."
                )
            if _context_token(context) != expected_context_token:
                raise GeneratedMaterialChanged()

            self._insert_metadata(connection, resume.metadata)
            self._insert_metadata(connection, letter.metadata)
            application = context["application"]
            old_ids: list[uuid.UUID] = []
            if isinstance(application, Mapping):
                old_ids = [
                    value
                    for value in (
                        application.get("tailored_resume_object_id"),
                        application.get("cover_letter_object_id"),
                    )
                    if isinstance(value, uuid.UUID)
                ]
                changed = connection.execute(
                    text(
                        """
                        UPDATE careertrellis.applications
                        SET company = :company, position = :position, us_hq = :us_hq,
                            headquarters_source = :headquarters_source,
                            headquarters_attribution = :headquarters_attribution,
                            tailored_resume_object_id = :resume_object_id,
                            cover_letter_object_id = :letter_object_id,
                            tailored_resume_text = :resume_text,
                            cover_letter = :cover_letter,
                            tailored_at = CURRENT_TIMESTAMP,
                            base_resume_id = :base_resume_id,
                            base_resume_name = :base_resume_name,
                            base_resume_version = :base_resume_version,
                            status = CASE WHEN status IN ('matched', 'tailored')
                                          THEN 'tailored' ELSE status END
                        WHERE job_id = :job_id AND owner_user_id = :owner_user_id
                        """
                    ),
                    {
                        **values,
                        "resume_object_id": resume.metadata.id,
                        "letter_object_id": letter.metadata.id,
                        "job_id": job_id,
                        "owner_user_id": self._owner_user_id,
                    },
                ).rowcount
                if changed != 1:
                    raise GeneratedMaterialChanged()
            else:
                connection.execute(
                    text(
                        """
                        INSERT INTO careertrellis.applications
                            (owner_user_id, job_id, company, position, us_hq,
                             headquarters_source, headquarters_attribution,
                             tailored_resume_object_id, cover_letter_object_id,
                             tailored_resume_text, cover_letter, status, created_at,
                             tailored_at, base_resume_id, base_resume_name,
                             base_resume_version)
                        VALUES
                            (:owner_user_id, :job_id, :company, :position, :us_hq,
                             :headquarters_source, :headquarters_attribution,
                             :resume_object_id, :letter_object_id, :resume_text,
                             :cover_letter, 'tailored', CURRENT_TIMESTAMP,
                             CURRENT_TIMESTAMP, :base_resume_id, :base_resume_name,
                             :base_resume_version)
                        """
                    ),
                    {
                        **values,
                        "resume_object_id": resume.metadata.id,
                        "letter_object_id": letter.metadata.id,
                        "job_id": job_id,
                        "owner_user_id": self._owner_user_id,
                    },
                )

            connection.execute(
                text(
                    """
                    UPDATE careertrellis.jobs
                    SET status = CASE WHEN status IN ('matched', 'tailored')
                                      THEN 'tailored' ELSE status END
                    WHERE id = :job_id AND owner_user_id = :owner_user_id
                    """
                ),
                {
                    "job_id": job_id,
                    "owner_user_id": self._owner_user_id,
                },
            )

            if not old_ids:
                return []
            rows = connection.execute(
                text(
                    """
                    UPDATE careertrellis.stored_objects
                    SET deleted_at = CURRENT_TIMESTAMP
                    WHERE id = ANY(:object_ids) AND owner_user_id = :owner_user_id
                      AND id <> :resume_object_id AND id <> :letter_object_id
                    RETURNING object_key
                    """
                ),
                {
                    "object_ids": old_ids,
                    "owner_user_id": self._owner_user_id,
                    "resume_object_id": resume.metadata.id,
                    "letter_object_id": letter.metadata.id,
                },
            ).scalars().all()
            return [str(value) for value in rows]

    def _references_staged(self, job_id: int, staged: tuple[EncryptedStagedObject, ...]) -> bool:
        ids = [item.metadata.id for item in staged]
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            return bool(
                connection.scalar(
                    text(
                        """
                        SELECT EXISTS(
                            SELECT 1 FROM careertrellis.applications
                            WHERE job_id = :job_id AND owner_user_id = :owner_user_id
                              AND tailored_resume_object_id = :resume_object_id
                              AND cover_letter_object_id = :letter_object_id
                        )
                        """
                    ),
                    {
                        "job_id": job_id,
                        "owner_user_id": self._owner_user_id,
                        "resume_object_id": ids[0],
                        "letter_object_id": ids[1],
                    },
                )
            )

    def _discard(self, staged: tuple[EncryptedStagedObject, ...]) -> None:
        for item in staged:
            try:
                discard_staged_object(self._backend, item)
            except Exception:
                pass

    def replace_materials(
        self,
        job_id: object,
        expected_context_token: str,
        resume_pdf: bytes,
        cover_letter: str,
        values: Mapping[str, object],
        *,
        require_application: bool = False,
    ) -> dict[str, str]:
        """Stage two encrypted objects before one atomic metadata/reference swap."""
        selected_job_id = _positive_id(job_id, "Job identifier")
        staged: list[EncryptedStagedObject] = []
        try:
            staged.append(
                stage_object(
                    self._backend,
                    self._owner_user_id,
                    resume_pdf,
                    "application/pdf",
                    keyring=self._keyring,
                )
            )
            staged.append(
                stage_object(
                    self._backend,
                    self._owner_user_id,
                    (cover_letter.strip() + "\n").encode("utf-8"),
                    "text/plain; charset=utf-8",
                    keyring=self._keyring,
                )
            )
        except Exception as exc:
            self._discard(tuple(staged))
            raise GeneratedMaterialUnavailable() from exc

        pair = (staged[0], staged[1])
        try:
            self._commit_replacement(
                selected_job_id,
                str(expected_context_token),
                pair[0],
                pair[1],
                values,
                require_application=require_application,
            )
        except (GeneratedMaterialNotFound, GeneratedMaterialChanged):
            self._discard(pair)
            raise
        except SQLAlchemyError as exc:
            # A commit-time connection failure can be outcome-ambiguous. Delete
            # only after a fresh owner-scoped read proves the new IDs are not
            # authoritative; an orphan is safer than a committed missing blob.
            try:
                committed = self._references_staged(selected_job_id, pair)
            except Exception:
                committed = None
            if committed is False:
                self._discard(pair)
            if committed is not True:
                raise GeneratedMaterialUnavailable() from exc
        except Exception as exc:
            self._discard(pair)
            raise GeneratedMaterialUnavailable() from exc

        try:
            self.reconcile_deleted_objects()
        except Exception:
            pass
        return {
            "tailored_resume_object_id": str(pair[0].metadata.id),
            "cover_letter_object_id": str(pair[1].metadata.id),
        }

    def reconcile_deleted_objects(self, limit: int = RECONCILE_LIMIT) -> int:
        """Retry tombstoned blob deletion; retain metadata until deletion succeeds."""
        selected_limit = max(1, min(int(limit), RECONCILE_LIMIT))
        with tenant_transaction(self._engine, self._owner_user_id) as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT o.id, o.object_key
                    FROM careertrellis.stored_objects AS o
                    WHERE o.owner_user_id = :owner_user_id
                      AND o.deleted_at IS NOT NULL
                      AND o.deleted_at < CURRENT_TIMESTAMP - INTERVAL '30 seconds'
                      AND NOT EXISTS (
                          SELECT 1 FROM careertrellis.applications AS a
                          WHERE a.owner_user_id = o.owner_user_id
                            AND (a.tailored_resume_object_id = o.id
                                 OR a.cover_letter_object_id = o.id)
                      )
                    ORDER BY o.deleted_at, o.id
                    LIMIT :limit
                    """
                ),
                {"owner_user_id": self._owner_user_id, "limit": selected_limit},
            ).mappings().all()
        removed = 0
        for row in rows:
            try:
                self._backend.delete(str(row["object_key"]))
            except Exception:
                continue
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                removed += connection.execute(
                    text(
                        """
                        DELETE FROM careertrellis.stored_objects AS o
                        WHERE o.id = :object_id AND o.owner_user_id = :owner_user_id
                          AND o.deleted_at IS NOT NULL
                          AND o.deleted_at < CURRENT_TIMESTAMP - INTERVAL '30 seconds'
                          AND NOT EXISTS (
                              SELECT 1 FROM careertrellis.applications AS a
                              WHERE a.owner_user_id = o.owner_user_id
                                AND (a.tailored_resume_object_id = o.id
                                     OR a.cover_letter_object_id = o.id)
                          )
                        """
                    ),
                    {"object_id": row["id"], "owner_user_id": self._owner_user_id},
                ).rowcount
        return removed


def runtime_generated_material_repository(
    *,
    engine: Engine | None = None,
    backend: ObjectBackend | None = None,
    keyring: ObjectKeyring | None = None,
) -> PostgreSQLGeneratedMaterialRepository:
    """Select shared storage only; local callers retain the standalone path."""
    if data_store_kind() != SHARED_DATA_STORE:
        raise GeneratedMaterialUnavailable()
    try:
        return PostgreSQLGeneratedMaterialRepository(
            current_owner_user_id(), engine=engine, backend=backend, keyring=keyring
        )
    except GeneratedMaterialUnavailable:
        raise
    except Exception as exc:
        raise GeneratedMaterialUnavailable() from exc
