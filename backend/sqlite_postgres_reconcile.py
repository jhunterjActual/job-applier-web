"""Read-only, exact reconciliation of one SQLite-to-PostgreSQL import.

The command emits counts only. It never repairs database rows, deletes private
objects, or writes provider credentials to logs or artifacts.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable
import uuid

from sqlalchemy import Connection, Engine, text

from object_store import (
    OBJECT_INVENTORY_PAGE_LIMIT,
    OBJECT_INVENTORY_FULL_PREFIX,
    OBJECT_KEY_PREFIX,
    ObjectBackend,
    ObjectKeyring,
    StoredObjectMetadata,
    read_object,
    runtime_object_backend,
    validated_object_key,
)
from postgres_database import (
    MIGRATION_DATABASE_URL_ENV,
    create_postgres_engine,
    database_url_from_environment,
    set_identity_context,
    set_tenant_context,
    validated_workos_user_id,
)
from provider_secrets import ProviderSecretKeyring, decrypt_provider_secret
from sqlite_postgres_import import (
    GENERATED_FILE_TYPES,
    OWNER_ID_ENVIRONMENT_VARIABLE,
    TARGET_TABLES,
    ImportManifest,
    expected_target_counts,
    inspect_sqlite_workspace,
    normalize_import_date,
    normalize_import_json_object,
    normalize_import_timestamp,
    open_sqlite_snapshot,
    read_generated_snapshot,
    source_time_zone,
    sqlite_table_rows,
)


MAXIMUM_INVENTORY_OBJECTS = 100_000
RECONCILIATION_ERROR_MESSAGE = "SQLite/PostgreSQL reconciliation failed."


class SQLitePostgresReconciliationError(RuntimeError):
    """A read-only comparison could not prove an exact import."""


@dataclass(frozen=True)
class ReconciliationSummary:
    verified_counts: dict[str, int]
    verified_objects: int
    nonportable_terminal_search_runs: int

    def sanitized_summary(self) -> dict[str, Any]:
        return {
            "verified_counts": dict(self.verified_counts),
            "verified_objects": self.verified_objects,
            "nonportable_terminal_search_runs": self.nonportable_terminal_search_runs,
        }


def _mismatch() -> SQLitePostgresReconciliationError:
    return SQLitePostgresReconciliationError(RECONCILIATION_ERROR_MESSAGE)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, memoryview):
        return bytes(value)
    return value


def _assert_fields(expected: dict[str, Any], actual: Any) -> None:
    for field, value in expected.items():
        if _freeze(actual[field]) != _freeze(value):
            raise _mismatch()


def _target_rows(
    connection: Connection,
    owner_id: int,
    table: str,
    columns: Iterable[str],
    *,
    order_by: str = "id",
) -> list[Any]:
    if table not in (*TARGET_TABLES, "search_runs"):
        raise _mismatch()
    selected = ", ".join(columns)
    return list(
        connection.execute(
            text(
                f"SELECT {selected} FROM careertrellis.{table} "
                f"WHERE owner_user_id = :owner ORDER BY {order_by}"
            ),
            {"owner": owner_id},
        ).mappings()
    )


def _pair_rows(
    source_rows: list[Any],
    target_rows: list[Any],
    expected_values: Any,
) -> dict[int, int]:
    if len(source_rows) != len(target_rows):
        raise _mismatch()
    identifiers: dict[int, int] = {}
    for source, target in zip(source_rows, target_rows):
        _assert_fields(expected_values(source), target)
        if "id" in source.keys():
            identifiers[int(source["id"])] = int(target["id"])
    return identifiers


def _resolve_owner(connection: Connection, workos_user_id: str) -> int:
    set_identity_context(connection, workos_user_id)
    row = connection.execute(
        text(
            "SELECT id, role, disabled_at FROM careertrellis.app_users "
            "WHERE workos_user_id = :workos_user_id"
        ),
        {"workos_user_id": workos_user_id},
    ).mappings().one_or_none()
    if row is None or row["role"] != "owner" or row["disabled_at"] is not None:
        raise _mismatch()
    return int(row["id"])


def _verify_counts(
    connection: Connection,
    owner_id: int,
    manifest: ImportManifest,
    *,
    omit_provider_secrets: bool,
    omit_generated_files: bool,
) -> dict[str, int]:
    expected = expected_target_counts(
        manifest,
        omit_provider_secrets=omit_provider_secrets,
        omit_generated_files=omit_generated_files,
    )
    actual = {
        table: int(
            connection.scalar(
                text(
                    f"SELECT COUNT(*) FROM careertrellis.{table} "
                    "WHERE owner_user_id = :owner"
                ),
                {"owner": owner_id},
            )
            or 0
        )
        for table in TARGET_TABLES
    }
    if actual != expected:
        raise _mismatch()
    search_runs = int(
        connection.scalar(
            text(
                "SELECT COUNT(*) FROM careertrellis.search_runs "
                "WHERE owner_user_id = :owner"
            ),
            {"owner": owner_id},
        )
        or 0
    )
    if search_runs:
        raise _mismatch()
    return actual


def _verify_relational_rows(
    source: Any,
    target: Connection,
    owner_id: int,
) -> tuple[dict[int, int], dict[int, int], list[Any], list[Any]]:
    zone = source_time_zone()

    source_resumes = sqlite_table_rows(source, "base_resumes")
    target_resumes = _target_rows(
        target,
        owner_id,
        "base_resumes",
        ("id", "name", "resume_mode", "content", "evidence", "created_at", "updated_at"),
    )
    resume_ids = _pair_rows(
        source_resumes,
        target_resumes,
        lambda row: {
            "name": row["name"],
            "resume_mode": row["resume_mode"] or "general_professional",
            "content": row["content"] or "",
            "evidence": normalize_import_json_object(row["evidence_json"]),
            "created_at": normalize_import_timestamp(row["created_at"], zone),
            "updated_at": normalize_import_timestamp(row["updated_at"], zone),
        },
    )

    source_versions = sqlite_table_rows(source, "base_resume_versions")
    target_versions = _target_rows(
        target,
        owner_id,
        "base_resume_versions",
        ("id", "base_resume_id", "version_number", "name", "resume_mode", "content", "evidence", "created_at"),
    )
    _pair_rows(
        source_versions,
        target_versions,
        lambda row: {
            "base_resume_id": resume_ids[int(row["base_resume_id"])],
            "version_number": row["version_number"],
            "name": row["name"],
            "resume_mode": row["resume_mode"],
            "content": row["content"] or "",
            "evidence": normalize_import_json_object(row["evidence_json"]),
            "created_at": normalize_import_timestamp(row["created_at"], zone),
        },
    )

    source_profiles = sqlite_table_rows(source, "profile")
    target_profiles = _target_rows(
        target,
        owner_id,
        "profiles",
        (
            "id", "name", "email", "phone", "github", "linkedin", "website",
            "suggested_keywords", "ai_provider", "ai_model", "maps_provider",
            "prefer_us_headquarters", "interface_language", "active_base_resume_id",
        ),
    )
    _pair_rows(
        source_profiles,
        target_profiles,
        lambda row: {
            "name": row["name"], "email": row["email"], "phone": row["phone"],
            "github": row["github"], "linkedin": row["linkedin"], "website": row["website"],
            "suggested_keywords": row["suggested_keywords"] or "",
            "ai_provider": row["ai_provider"] or "gemini",
            "ai_model": row["ai_model"] or "gemini-2.5-flash",
            "maps_provider": row["maps_provider"] or "openstreetmap",
            "prefer_us_headquarters": bool(row["prefer_us_headquarters"]),
            "interface_language": row["interface_language"] or "en",
            "active_base_resume_id": (
                resume_ids[int(row["active_base_resume_id"])]
                if row["active_base_resume_id"] else None
            ),
        },
    )

    source_jobs = sqlite_table_rows(source, "jobs")
    target_jobs = _target_rows(
        target,
        owner_id,
        "jobs",
        (
            "id", "title", "company", "description", "url", "match_score",
            "match_analysis", "date_found", "status", "archived_at",
            "archived_from_status", "last_checked_at", "is_expired",
            "expiration_reason", "location", "work_arrangement", "employment_type",
            "compensation", "source",
        ),
    )
    job_ids = _pair_rows(
        source_jobs,
        target_jobs,
        lambda row: {
            "title": row["title"], "company": row["company"],
            "description": row["description"], "url": row["url"],
            "match_score": row["match_score"], "match_analysis": row["match_analysis"],
            "date_found": normalize_import_date(row["date_found"]),
            "status": row["status"] or "matched",
            "archived_at": normalize_import_timestamp(row["archived_at"], zone),
            "archived_from_status": row["archived_from_status"],
            "last_checked_at": normalize_import_timestamp(row["last_checked_at"], zone),
            "is_expired": bool(row["is_expired"]),
            "expiration_reason": row["expiration_reason"], "location": row["location"],
            "work_arrangement": row["work_arrangement"],
            "employment_type": row["employment_type"],
            "compensation": row["compensation"], "source": row["source"],
        },
    )

    source_searches = sqlite_table_rows(source, "saved_searches")
    target_searches = _target_rows(
        target,
        owner_id,
        "saved_searches",
        ("id", "name", "keywords", "location", "created_at", "last_run_at", "schedule_frequency", "next_alert_at", "enabled"),
    )
    _pair_rows(
        source_searches,
        target_searches,
        lambda row: {
            "name": row["name"], "keywords": row["keywords"],
            "location": row["location"] or "",
            "created_at": normalize_import_timestamp(row["created_at"], zone),
            "last_run_at": normalize_import_timestamp(row["last_run_at"], zone),
            "schedule_frequency": row["schedule_frequency"] or "none",
            "next_alert_at": normalize_import_timestamp(row["next_alert_at"], zone),
            "enabled": bool(row["enabled"]),
        },
    )

    source_apps = sqlite_table_rows(source, "applications")
    target_apps = _target_rows(
        target,
        owner_id,
        "applications",
        (
            "id", "job_id", "company", "position", "date_applied", "us_hq",
            "tailored_resume_object_id", "cover_letter_object_id",
            "tailored_resume_text", "cover_letter", "status", "created_at",
            "tailored_at", "form_filled_at", "submitted_at", "confirmed_at",
            "application_method", "submission_evidence", "notes", "follow_up_date",
            "headquarters_source", "headquarters_attribution", "base_resume_id",
            "base_resume_name", "base_resume_version", "interview_prep",
            "interview_prep_updated_at",
        ),
    )
    _pair_rows(
        source_apps,
        target_apps,
        lambda row: {
            "job_id": job_ids[int(row["job_id"])], "company": row["company"],
            "position": row["position"], "date_applied": normalize_import_date(row["date_applied"]),
            "us_hq": row["us_hq"], "tailored_resume_text": row["tailored_resume_text"],
            "cover_letter": row["cover_letter"], "status": row["status"] or "tailored",
            "created_at": normalize_import_timestamp(row["created_at"], zone),
            "tailored_at": normalize_import_timestamp(row["tailored_at"], zone),
            "form_filled_at": normalize_import_timestamp(row["form_filled_at"], zone),
            "submitted_at": normalize_import_timestamp(row["submitted_at"], zone),
            "confirmed_at": normalize_import_timestamp(row["confirmed_at"], zone),
            "application_method": row["application_method"],
            "submission_evidence": row["submission_evidence"], "notes": row["notes"],
            "follow_up_date": normalize_import_date(row["follow_up_date"]),
            "headquarters_source": row["headquarters_source"],
            "headquarters_attribution": row["headquarters_attribution"],
            "base_resume_id": (
                resume_ids[int(row["base_resume_id"])] if row["base_resume_id"] else None
            ),
            "base_resume_name": row["base_resume_name"],
            "base_resume_version": row["base_resume_version"],
            "interview_prep": row["interview_prep"],
            "interview_prep_updated_at": normalize_import_timestamp(
                row["interview_prep_updated_at"], zone
            ),
        },
    )

    return resume_ids, job_ids, source_apps, target_apps


def _verify_dependent_rows(source: Any, target: Connection, owner_id: int, job_ids: dict[int, int]) -> None:
    zone = source_time_zone()
    specifications = (
        (
            "application_status_history",
            ("id", "job_id", "from_status", "to_status", "changed_at", "source", "notes", "undone_at"),
            lambda row: {
                "job_id": job_ids[int(row["job_id"])], "from_status": row["from_status"],
                "to_status": row["to_status"],
                "changed_at": normalize_import_timestamp(row["changed_at"], zone),
                "source": row["source"], "notes": row["notes"],
                "undone_at": normalize_import_timestamp(row["undone_at"], zone),
            },
        ),
        (
            "application_engagements",
            ("id", "job_id", "engagement_type", "name", "organization", "contact_details", "status", "activity_on", "next_action_on", "notes", "created_at", "updated_at"),
            lambda row: {
                "job_id": job_ids[int(row["job_id"])],
                "engagement_type": row["engagement_type"], "name": row["name"],
                "organization": row["organization"], "contact_details": row["contact_details"],
                "status": row["status"], "activity_on": normalize_import_date(row["activity_on"]),
                "next_action_on": normalize_import_date(row["next_action_on"]),
                "notes": row["notes"],
                "created_at": normalize_import_timestamp(row["created_at"], zone),
                "updated_at": normalize_import_timestamp(row["updated_at"], zone),
            },
        ),
        (
            "job_suppressions",
            ("id", "url_fingerprint", "hostname", "company", "title", "deleted_at", "deletion_source"),
            lambda row: {
                "url_fingerprint": row["url_fingerprint"], "hostname": row["hostname"],
                "company": row["company"] or "", "title": row["title"] or "",
                "deleted_at": normalize_import_timestamp(row["deleted_at"], zone),
                "deletion_source": row["deletion_source"],
            },
        ),
        (
            "source_diagnostics",
            ("id", "recorded_at", "provider", "diagnostic_code", "counters"),
            lambda row: {
                "recorded_at": normalize_import_timestamp(row["recorded_at"], zone),
                "provider": row["provider"], "diagnostic_code": row["diagnostic_code"],
                "counters": normalize_import_json_object(row["counters_json"]),
            },
        ),
        (
            "headquarters_cache",
            ("id", "cache_key", "provider", "address", "country_code", "attribution", "resolved_at"),
            lambda row: {
                "cache_key": row["cache_key"], "provider": row["provider"],
                "address": row["address"], "country_code": row["country_code"] or "",
                "attribution": row["attribution"] or "",
                "resolved_at": normalize_import_timestamp(row["resolved_at"], zone),
            },
        ),
    )
    for table, columns, expected_values in specifications:
        _pair_rows(
            sqlite_table_rows(source, table),
            _target_rows(target, owner_id, table, columns),
            expected_values,
        )


def _verify_provider_secrets(
    source: Any,
    target: Connection,
    owner_id: int,
    *,
    omit_provider_secrets: bool,
    provider_keyring: ProviderSecretKeyring | None,
) -> None:
    rows = _target_rows(
        target,
        owner_id,
        "provider_secrets",
        ("id", "provider", "key_id", "ciphertext", "revoked_at"),
    )
    expected: dict[str, str] = {}
    if not omit_provider_secrets:
        for row in sqlite_table_rows(source, "profile"):
            for column, provider in (
                ("gemini_api_key", "gemini"),
                ("openai_api_key", "openai"),
                ("google_maps_api_key", "google_maps"),
            ):
                plaintext = str(row[column] or "").strip()
                if plaintext:
                    expected[provider] = plaintext
    if len(rows) != len(expected) or {str(row["provider"]) for row in rows} != set(expected):
        raise _mismatch()
    for row in rows:
        if row["revoked_at"] is not None:
            raise _mismatch()
        provider = str(row["provider"])
        plaintext = decrypt_provider_secret(
            owner_id,
            provider,
            row["key_id"],
            row["ciphertext"],
            keyring=provider_keyring,
        )
        if not hmac.compare_digest(plaintext, expected[provider]):
            raise _mismatch()
        del plaintext
    expected.clear()


def _inventory_object_keys(
    backend: ObjectBackend,
    *,
    prefix: str,
    page_size: int = OBJECT_INVENTORY_PAGE_LIMIT,
    maximum_objects: int = MAXIMUM_INVENTORY_OBJECTS,
) -> tuple[str, ...]:
    """Read a bounded, paginated inventory without exposing keys in output."""
    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not 1 <= page_size <= OBJECT_INVENTORY_PAGE_LIMIT
    ):
        raise _mismatch()
    if (
        not isinstance(maximum_objects, int)
        or isinstance(maximum_objects, bool)
        or not 1 <= maximum_objects <= MAXIMUM_INVENTORY_OBJECTS
    ):
        raise _mismatch()
    cursor: str | None = None
    seen_cursors: set[str] = set()
    keys: list[str] = []
    while True:
        page = backend.list_object_keys(
            prefix=prefix,
            cursor=cursor,
            limit=page_size,
        )
        if len(page.object_keys) > page_size:
            raise _mismatch()
        keys.extend(validated_object_key(key) for key in page.object_keys)
        if len(keys) > maximum_objects:
            raise _mismatch()
        if page.next_cursor is None:
            return tuple(keys)
        if (
            not isinstance(page.next_cursor, str)
            or not 1 <= len(page.next_cursor) <= 2048
            or page.next_cursor in seen_cursors
        ):
            raise _mismatch()
        seen_cursors.add(page.next_cursor)
        cursor = page.next_cursor


def inventory_object_keys(
    backend: ObjectBackend,
    *,
    page_size: int = OBJECT_INVENTORY_PAGE_LIMIT,
    maximum_objects: int = MAXIMUM_INVENTORY_OBJECTS,
) -> tuple[str, ...]:
    """Read every key in the dedicated bucket through bounded pages."""
    return _inventory_object_keys(
        backend,
        prefix=OBJECT_INVENTORY_FULL_PREFIX,
        page_size=page_size,
        maximum_objects=maximum_objects,
    )


def inventory_v1_object_keys(
    backend: ObjectBackend,
    *,
    page_size: int = OBJECT_INVENTORY_PAGE_LIMIT,
    maximum_objects: int = MAXIMUM_INVENTORY_OBJECTS,
) -> tuple[str, ...]:
    """Compatibility view limited to the canonical v1 namespace."""
    return _inventory_object_keys(
        backend,
        prefix=OBJECT_KEY_PREFIX,
        page_size=page_size,
        maximum_objects=maximum_objects,
    )


def _verify_objects(
    source_path: Path,
    source_apps: list[Any],
    target_apps: list[Any],
    target: Connection,
    owner_id: int,
    manifest: ImportManifest,
    backend: ObjectBackend | None,
    *,
    omit_generated_files: bool,
    object_keyring: ObjectKeyring | None,
) -> int:
    metadata_rows = _target_rows(
        target,
        owner_id,
        "stored_objects",
        ("id", "object_key", "content_type", "plaintext_size", "sha256", "wrapped_data_key", "deleted_at"),
    )
    metadata_by_id = {row["id"]: row for row in metadata_rows}
    if len(metadata_by_id) != len(metadata_rows):
        raise _mismatch()
    referenced_ids: list[Any] = []
    if len(source_apps) != len(target_apps):
        raise _mismatch()
    for source_row, target_row in zip(source_apps, target_apps):
        for path_column, object_column in (
            ("tailored_resume_path", "tailored_resume_object_id"),
            ("cover_letter_path", "cover_letter_object_id"),
        ):
            has_file = bool(str(source_row[path_column] or "").strip())
            object_id = target_row[object_column]
            if omit_generated_files or not has_file:
                if object_id is not None:
                    raise _mismatch()
                continue
            if backend is None:
                raise _mismatch()
            if object_id is None or object_id not in metadata_by_id:
                raise _mismatch()
            referenced_ids.append(object_id)
            metadata_row = metadata_by_id[object_id]
            snapshot = manifest.generated_file_snapshots.get((int(source_row["id"]), path_column))
            if snapshot is None:
                raise _mismatch()
            plaintext = read_generated_snapshot(
                source_path,
                source_row[path_column],
                path_column,
                snapshot,
            )
            expected_type = GENERATED_FILE_TYPES[path_column][1]
            digest = hashlib.sha256(plaintext).digest()
            if (
                metadata_row["deleted_at"] is not None
                or metadata_row["content_type"] != expected_type
                or int(metadata_row["plaintext_size"]) != len(plaintext)
                or not hmac.compare_digest(bytes(metadata_row["sha256"]), digest)
            ):
                raise _mismatch()
            metadata = StoredObjectMetadata(
                id=uuid.UUID(str(metadata_row["id"])),
                owner_user_id=owner_id,
                object_key=str(metadata_row["object_key"]),
                content_type=str(metadata_row["content_type"]),
                size_bytes=int(metadata_row["plaintext_size"]),
                sha256=digest.hex(),
                wrapped_data_key=bytes(metadata_row["wrapped_data_key"]),
            )
            restored = read_object(backend, metadata, owner_id, keyring=object_keyring)
            if not hmac.compare_digest(restored, plaintext):
                raise _mismatch()
            del plaintext, restored
    if len(referenced_ids) != len(set(referenced_ids)):
        raise _mismatch()
    if set(referenced_ids) != set(metadata_by_id):
        raise _mismatch()
    if omit_generated_files:
        return 0
    metadata_keys = [str(row["object_key"]) for row in metadata_rows]
    if len(metadata_keys) != len(set(metadata_keys)):
        raise _mismatch()
    if backend is None:
        raise _mismatch()
    inventory_keys = inventory_object_keys(backend)
    if len(inventory_keys) != len(set(inventory_keys)):
        raise _mismatch()
    if set(inventory_keys) != set(metadata_keys):
        raise _mismatch()
    return len(metadata_rows)


def reconcile_sqlite_postgres_import(
    source_path: Path,
    owner_workos_user_id: str,
    *,
    omit_provider_secrets: bool = False,
    omit_generated_files: bool = False,
    engine: Engine | None = None,
    object_backend: ObjectBackend | None = None,
    provider_keyring: ProviderSecretKeyring | None = None,
    object_keyring: ObjectKeyring | None = None,
) -> ReconciliationSummary:
    """Prove an exact import using immutable SQLite and read-only PostgreSQL snapshots."""
    source = None
    target_engine: Engine | None = None
    owned_engine = engine is None
    try:
        owner_identity = validated_workos_user_id(owner_workos_user_id)
        source = open_sqlite_snapshot(source_path)
        source.execute("BEGIN")
        manifest = inspect_sqlite_workspace(
            source_path,
            owner_identity,
            validate_generated_files=not omit_generated_files,
            _connection=source,
        )
        backend = (
            None
            if omit_generated_files
            else (object_backend or runtime_object_backend())
        )
        target_engine = engine or create_postgres_engine(
            database_url_from_environment(MIGRATION_DATABASE_URL_ENV)
        )
        with target_engine.connect() as target:
            transaction = target.begin()
            try:
                target.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                target.exec_driver_sql("SET ROLE careertrellis_schema_owner")
                owner_id = _resolve_owner(target, owner_identity)
                set_tenant_context(target, owner_id, owner_identity)
                counts = _verify_counts(
                    target,
                    owner_id,
                    manifest,
                    omit_provider_secrets=omit_provider_secrets,
                    omit_generated_files=omit_generated_files,
                )
                _resume_ids, job_ids, source_apps, target_apps = _verify_relational_rows(
                    source, target, owner_id
                )
                _verify_dependent_rows(source, target, owner_id, job_ids)
                _verify_provider_secrets(
                    source,
                    target,
                    owner_id,
                    omit_provider_secrets=omit_provider_secrets,
                    provider_keyring=provider_keyring,
                )
                objects = _verify_objects(
                    source_path,
                    source_apps,
                    target_apps,
                    target,
                    owner_id,
                    manifest,
                    backend,
                    omit_generated_files=omit_generated_files,
                    object_keyring=object_keyring,
                )
                target.exec_driver_sql("RESET ROLE")
                transaction.rollback()
            except BaseException:
                if transaction.is_active:
                    transaction.rollback()
                raise
        return ReconciliationSummary(
            verified_counts=counts,
            verified_objects=objects,
            nonportable_terminal_search_runs=manifest.nonportable_terminal_search_runs,
        )
    except SQLitePostgresReconciliationError:
        raise
    except Exception:
        raise _mismatch() from None
    finally:
        if source is not None:
            try:
                source.rollback()
            except Exception:
                pass
            try:
                source.close()
            except Exception:
                pass
        if owned_engine and target_engine is not None:
            try:
                target_engine.dispose()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Path to the SQLite workspace database")
    parser.add_argument("--omit-provider-secrets", action="store_true")
    parser.add_argument("--omit-generated-files", action="store_true")
    arguments = parser.parse_args()
    owner_workos_user_id = os.environ.get(OWNER_ID_ENVIRONMENT_VARIABLE, "").strip()
    if not owner_workos_user_id:
        raise _mismatch()
    summary = reconcile_sqlite_postgres_import(
        arguments.source,
        owner_workos_user_id,
        omit_provider_secrets=arguments.omit_provider_secrets,
        omit_generated_files=arguments.omit_generated_files,
    )
    print(json.dumps(summary.sanitized_summary(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(RECONCILIATION_ERROR_MESSAGE, file=sys.stderr)
        raise SystemExit(1)
