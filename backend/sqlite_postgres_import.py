"""Atomically import one standalone SQLite workspace into one PostgreSQL owner.

Provider credentials and generated application files are encrypted before they
leave the standalone host. Local filesystem paths are never copied into the
shared schema.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Connection, Engine, text

from postgres_database import (
    MIGRATION_DATABASE_URL_ENV,
    create_postgres_engine,
    database_url_from_environment,
    set_identity_context,
    set_tenant_context,
    validated_workos_user_id,
)
from provider_secrets import encrypt_provider_secret
from object_store import (
    OBJECT_INVENTORY_PAGE_LIMIT,
    OBJECT_INVENTORY_FULL_PREFIX,
    discard_staged_object,
    runtime_object_backend,
    stage_object,
)


OWNER_ID_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_IMPORT_OWNER_WORKOS_USER_ID"
SOURCE_TIME_ZONE_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_IMPORT_SOURCE_TIME_ZONE"
IMPORT_LOCK_NAME = "careertrellis.sqlite-owner-import.v1"
ACTIVE_SEARCH_RUN_STATUSES = ("preparing", "queued", "running", "cancel_requested")
TERMINAL_SEARCH_RUN_STATUSES = ("succeeded", "failed", "cancelled")
SOURCE_TABLES = (
    "profile",
    "base_resumes",
    "base_resume_versions",
    "jobs",
    "saved_searches",
    "applications",
    "application_status_history",
    "application_engagements",
    "job_suppressions",
    "source_diagnostics",
    "headquarters_cache",
)
TARGET_TABLES = (
    "provider_secrets",
    "stored_objects",
    "profiles",
    "base_resumes",
    "base_resume_versions",
    "jobs",
    "saved_searches",
    "applications",
    "application_status_history",
    "application_engagements",
    "job_suppressions",
    "source_diagnostics",
    "headquarters_cache",
)
SOURCE_TO_TARGET = {
    "profile": "profiles",
    "base_resumes": "base_resumes",
    "base_resume_versions": "base_resume_versions",
    "jobs": "jobs",
    "saved_searches": "saved_searches",
    "applications": "applications",
    "application_status_history": "application_status_history",
    "application_engagements": "application_engagements",
    "job_suppressions": "job_suppressions",
    "source_diagnostics": "source_diagnostics",
    "headquarters_cache": "headquarters_cache",
}
SECRET_COLUMNS = ("gemini_api_key", "openai_api_key", "google_maps_api_key")
FILE_PATH_COLUMNS = ("tailored_resume_path", "cover_letter_path")
GENERATED_FILE_LIMIT_BYTES = 10 * 1024 * 1024
GENERATED_FILE_TYPES = {
    "tailored_resume_path": (".pdf", "application/pdf"),
    "cover_letter_path": (".txt", "text/plain; charset=utf-8"),
}


class SQLiteImportError(RuntimeError):
    """The source or target failed a safe import precondition."""


class SQLiteImportReconciliationRequired(SQLiteImportError):
    """The database/object outcome requires operator reconciliation."""


@dataclass(frozen=True)
class GeneratedFileSnapshot:
    path: Path
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class ImportManifest:
    counts: dict[str, int]
    provider_secret_fields: int
    generated_file_paths: int
    nonportable_terminal_search_runs: int = 0
    generated_file_snapshots: dict[tuple[int, str], GeneratedFileSnapshot] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    verified_target_counts: dict[str, int] = field(default_factory=dict)

    def sanitized_summary(self) -> dict[str, Any]:
        summary = {
            "counts": dict(self.counts),
            "provider_secret_fields": self.provider_secret_fields,
            "generated_file_paths": self.generated_file_paths,
            "nonportable_terminal_search_runs": self.nonportable_terminal_search_runs,
        }
        if self.verified_target_counts:
            summary["verified_target_counts"] = dict(self.verified_target_counts)
        return summary


def open_sqlite_snapshot(path: Path) -> sqlite3.Connection:
    """Open a caller-owned, immutable SQLite source connection."""
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise SQLiteImportError("The SQLite source must be a regular file.")
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _open_source(path: Path) -> sqlite3.Connection:
    return open_sqlite_snapshot(path)


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _generated_output_root(source_path: Path) -> Path:
    source = source_path.expanduser().resolve(strict=True)
    candidate = source.parent / "output"
    if candidate.is_symlink():
        raise SQLiteImportError("The generated-file output directory must not be a symlink.")
    try:
        root = candidate.resolve(strict=True)
    except OSError as exc:
        raise SQLiteImportError("The generated-file output directory does not exist.") from exc
    if not root.is_dir():
        raise SQLiteImportError("The generated-file output location must be a directory.")
    return root


def _validated_generated_file(
    source_path: Path,
    raw_path: Any,
    column: str,
) -> Path:
    """Resolve one legacy material without allowing traversal or link following."""
    value = str(raw_path or "").strip()
    if not value:
        raise SQLiteImportError("A generated-file path was unexpectedly blank.")
    expected_suffix, _content_type = GENERATED_FILE_TYPES[column]
    output_root = _generated_output_root(source_path)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = source_path.expanduser().resolve(strict=True).parent / candidate
    if candidate.is_symlink():
        raise SQLiteImportError("Generated-file paths must not be symlinks.")
    try:
        resolved = candidate.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise SQLiteImportError("A referenced generated file is unavailable.") from exc
    if resolved.parent != output_root:
        raise SQLiteImportError(
            "Every generated file must be a direct child of the workspace output directory."
        )
    if resolved.suffix != expected_suffix:
        raise SQLiteImportError(
            f"{column} must reference a file with the exact {expected_suffix} extension."
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise SQLiteImportError("Every generated-file path must reference a regular file.")
    if not 1 <= metadata.st_size <= GENERATED_FILE_LIMIT_BYTES:
        raise SQLiteImportError("A generated file is empty or exceeds the 10 MiB import limit.")
    return resolved


def _file_snapshot(path: Path, metadata: os.stat_result) -> GeneratedFileSnapshot:
    return GeneratedFileSnapshot(
        path=path,
        device=int(metadata.st_dev),
        inode=int(metadata.st_ino),
        size=int(metadata.st_size),
        modified_ns=int(metadata.st_mtime_ns),
        # Windows reports creation/change timestamps inconsistently between
        # stat(path) and fstat(fd). Device/inode/size/mtime plus both before- and
        # after-read path checks retain the stable snapshot boundary there.
        changed_ns=0 if os.name == "nt" else int(metadata.st_ctime_ns),
    )


def _read_generated_file(
    source_path: Path,
    raw_path: Any,
    column: str,
    expected: GeneratedFileSnapshot,
) -> bytes:
    """Read exactly the file inode captured during the SQLite snapshot preflight."""
    path = _validated_generated_file(source_path, raw_path, column)
    if path != expected.path:
        raise SQLiteImportError("A generated file changed after import preflight.")
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SQLiteImportError("A generated file could not be opened safely.") from exc
    try:
        metadata = os.fstat(descriptor)
        opened_snapshot = _file_snapshot(path, metadata)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or opened_snapshot != expected
        ):
            raise SQLiteImportError("Every generated-file path must reference a regular file.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(GENERATED_FILE_LIMIT_BYTES + 1)
        if _file_snapshot(path, os.fstat(descriptor)) != expected:
            raise SQLiteImportError("A generated file changed while it was being read.")
        try:
            if _file_snapshot(path, path.stat()) != expected:
                raise SQLiteImportError("A generated file changed while it was being read.")
        except OSError as exc:
            raise SQLiteImportError("A generated file changed while it was being read.") from exc
    finally:
        os.close(descriptor)
    if not 1 <= len(content) <= GENERATED_FILE_LIMIT_BYTES:
        raise SQLiteImportError("A generated file is empty or exceeds the 10 MiB import limit.")
    return content


def read_generated_snapshot(
    source_path: Path,
    raw_path: Any,
    column: str,
    expected: GeneratedFileSnapshot,
) -> bytes:
    """Read one preflight-captured generated file without following links."""
    return _read_generated_file(source_path, raw_path, column, expected)


def _validate_generated_files(
    source_path: Path,
    connection: sqlite3.Connection,
) -> dict[tuple[int, str], GeneratedFileSnapshot]:
    snapshots: dict[tuple[int, str], GeneratedFileSnapshot] = {}
    for row in connection.execute(
        "SELECT id, tailored_resume_path, cover_letter_path FROM applications ORDER BY rowid"
    ):
        for column in FILE_PATH_COLUMNS:
            if str(row[column] or "").strip():
                path = _validated_generated_file(source_path, row[column], column)
                metadata = path.stat()
                if metadata.st_nlink != 1:
                    raise SQLiteImportError("Generated files must not have hard links.")
                snapshots[(int(row["id"]), column)] = _file_snapshot(path, metadata)
    return snapshots


def inspect_sqlite_workspace(
    path: Path,
    owner_workos_user_id: str,
    *,
    validate_generated_files: bool = False,
    _connection: sqlite3.Connection | None = None,
) -> ImportManifest:
    """Return bounded counts after validating the current SQLite schema and links."""
    owner_workos_user_id = validated_workos_user_id(owner_workos_user_id)
    owns_connection = _connection is None
    connection = _connection or _open_source(path)
    try:
        missing = [
            table
            for table in (*SOURCE_TABLES, "app_users", "search_runs")
            if not _table_columns(connection, table)
        ]
        if missing:
            raise SQLiteImportError(
                "The SQLite source is missing required tables: " + ", ".join(missing)
            )
        profile_columns = _table_columns(connection, "profile")
        application_columns = _table_columns(connection, "applications")
        if not set(SECRET_COLUMNS).issubset(profile_columns):
            raise SQLiteImportError("The SQLite profile schema is not current.")
        if not set(FILE_PATH_COLUMNS).issubset(application_columns):
            raise SQLiteImportError("The SQLite applications schema is not current.")

        owner_rows = connection.execute(
            "SELECT workos_user_id, disabled_at FROM app_users WHERE role = 'owner'"
        ).fetchall()
        if len(owner_rows) > 1:
            raise SQLiteImportError("The SQLite source contains multiple owner mappings.")
        if owner_rows and (
            owner_rows[0]["workos_user_id"] != owner_workos_user_id
            or owner_rows[0]["disabled_at"] is not None
        ):
            raise SQLiteImportError("The SQLite owner does not match the requested identity.")
        if connection.execute("SELECT COUNT(*) FROM profile").fetchone()[0] > 1:
            raise SQLiteImportError("The SQLite source contains multiple profile rows.")

        active_search_runs = int(
            connection.execute(
                "SELECT COUNT(*) FROM search_runs WHERE status IN (?, ?, ?, ?)",
                ACTIVE_SEARCH_RUN_STATUSES,
            ).fetchone()[0]
        )
        if active_search_runs:
            raise SQLiteImportError(
                "The SQLite source contains active search work; finish or cancel it before import."
            )
        terminal_search_runs = int(
            connection.execute(
                "SELECT COUNT(*) FROM search_runs WHERE status IN (?, ?, ?)",
                TERMINAL_SEARCH_RUN_STATUSES,
            ).fetchone()[0]
        )
        invalid_search_runs = int(
            connection.execute(
                "SELECT COUNT(*) FROM search_runs WHERE status NOT IN (?, ?, ?, ?, ?, ?, ?)",
                (*ACTIVE_SEARCH_RUN_STATUSES, *TERMINAL_SEARCH_RUN_STATUSES),
            ).fetchone()[0]
        )
        if invalid_search_runs:
            raise SQLiteImportError("The SQLite source contains invalid search-run state.")

        orphan_checks = {
            "applications": "SELECT COUNT(*) FROM applications a LEFT JOIN jobs j ON j.id = a.job_id WHERE a.job_id IS NULL OR j.id IS NULL",
            "application_base_resumes": "SELECT COUNT(*) FROM applications a LEFT JOIN base_resumes r ON r.id = a.base_resume_id WHERE a.base_resume_id IS NOT NULL AND r.id IS NULL",
            "application_status_history": "SELECT COUNT(*) FROM application_status_history h LEFT JOIN jobs j ON j.id = h.job_id WHERE j.id IS NULL",
            "application_engagements": "SELECT COUNT(*) FROM application_engagements e LEFT JOIN jobs j ON j.id = e.job_id WHERE j.id IS NULL",
            "base_resume_versions": "SELECT COUNT(*) FROM base_resume_versions v LEFT JOIN base_resumes r ON r.id = v.base_resume_id WHERE r.id IS NULL",
            "profile_active_resume": "SELECT COUNT(*) FROM profile p LEFT JOIN base_resumes r ON r.id = p.active_base_resume_id WHERE p.active_base_resume_id IS NOT NULL AND r.id IS NULL",
        }
        invalid_links = [
            name for name, query in orphan_checks.items()
            if connection.execute(query).fetchone()[0]
        ]
        if invalid_links:
            raise SQLiteImportError(
                "The SQLite source contains orphaned relationships in: "
                + ", ".join(invalid_links)
            )
        if connection.execute(
            "SELECT COUNT(*) FROM (SELECT job_id FROM applications GROUP BY job_id HAVING COUNT(*) > 1)"
        ).fetchone()[0]:
            raise SQLiteImportError("The SQLite source contains duplicate job applications.")

        null_job_urls = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE url IS NULL OR TRIM(url) = ''"
        ).fetchone()[0]
        if null_job_urls:
            raise SQLiteImportError("Every imported job must have a URL.")

        for table, column in (
            ("base_resumes", "evidence_json"),
            ("base_resume_versions", "evidence_json"),
            ("source_diagnostics", "counters_json"),
        ):
            for row in connection.execute(f'SELECT "{column}" FROM "{table}"'):
                try:
                    value = json.loads(row[0] or "{}")
                except (TypeError, json.JSONDecodeError) as exc:
                    raise SQLiteImportError(
                        f"The SQLite source contains invalid JSON in {table}.{column}."
                    ) from exc
                if not isinstance(value, dict):
                    raise SQLiteImportError(
                        f"The SQLite source requires JSON objects in {table}.{column}."
                    )

        counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in SOURCE_TABLES
        }
        secret_expression = " + ".join(
            f"CASE WHEN TRIM(COALESCE({column}, '')) <> '' THEN 1 ELSE 0 END"
            for column in SECRET_COLUMNS
        )
        provider_secret_fields = int(
            connection.execute(f"SELECT COALESCE(SUM({secret_expression}), 0) FROM profile").fetchone()[0]
        )
        path_expression = " + ".join(
            f"CASE WHEN TRIM(COALESCE({column}, '')) <> '' THEN 1 ELSE 0 END"
            for column in FILE_PATH_COLUMNS
        )
        generated_file_paths = int(
            connection.execute(
                f"SELECT COALESCE(SUM({path_expression}), 0) FROM applications"
            ).fetchone()[0]
        )
        snapshots = (
            _validate_generated_files(path, connection)
            if generated_file_paths and validate_generated_files
            else {}
        )
        return ImportManifest(
            counts,
            provider_secret_fields,
            generated_file_paths,
            nonportable_terminal_search_runs=terminal_search_runs,
            generated_file_snapshots=snapshots,
        )
    finally:
        if owns_connection:
            connection.close()


def _source_zone() -> ZoneInfo:
    name = os.environ.get(SOURCE_TIME_ZONE_ENVIRONMENT_VARIABLE, "America/New_York").strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise SQLiteImportError(
            f"{SOURCE_TIME_ZONE_ENVIRONMENT_VARIABLE} must name an installed IANA time zone."
        ) from exc


def source_time_zone() -> ZoneInfo:
    """Return the configured legacy timestamp zone used by import and reconciliation."""
    return _source_zone()


def _timestamp(value: Any, source_zone: ZoneInfo) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise SQLiteImportError("The SQLite source contains an invalid timestamp.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=source_zone)
    return parsed.astimezone(timezone.utc)


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise SQLiteImportError("The SQLite source contains an invalid date.") from exc


def _json_object(value: Any) -> str:
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise SQLiteImportError("The SQLite source contains a non-object JSON value.")
    return json.dumps(parsed, separators=(",", ":"), sort_keys=True)


def normalize_import_timestamp(value: Any, source_zone: ZoneInfo) -> datetime | None:
    return _timestamp(value, source_zone)


def normalize_import_date(value: Any) -> date | None:
    return _date(value)


def normalize_import_json_object(value: Any) -> dict[str, Any]:
    return json.loads(_json_object(value))


def _target_must_be_empty(connection: Connection) -> None:
    populated = []
    for table in TARGET_TABLES:
        count = connection.scalar(text(f"SELECT COUNT(*) FROM careertrellis.{table}"))
        if count:
            populated.append(table)
    if populated:
        raise SQLiteImportError(
            "The target owner already has data in: " + ", ".join(populated)
        )
    if connection.scalar(text("SELECT COUNT(*) FROM careertrellis.search_runs")):
        raise SQLiteImportError("The target owner already has search-run data.")


def require_empty_object_inventory(object_backend: Any) -> None:
    """Fail before staging when the dedicated private bucket is not empty."""
    try:
        page = object_backend.list_object_keys(
            prefix=OBJECT_INVENTORY_FULL_PREFIX,
            limit=OBJECT_INVENTORY_PAGE_LIMIT,
        )
    except Exception:
        raise SQLiteImportError(
            "The target private object bucket could not be verified."
        ) from None
    if page.object_keys or page.next_cursor is not None:
        raise SQLiteImportError("The target private object bucket is not empty.")


def _seed_owner(connection: Connection, workos_user_id: str) -> int:
    set_identity_context(connection, workos_user_id)
    row = connection.execute(
        text(
            "SELECT id, role, disabled_at FROM careertrellis.app_users "
            "WHERE workos_user_id = :workos_user_id"
        ),
        {"workos_user_id": workos_user_id},
    ).mappings().one_or_none()
    if row is None:
        user_id = connection.scalar(
            text(
                "INSERT INTO careertrellis.app_users (workos_user_id, role) "
                "VALUES (:workos_user_id, 'owner') RETURNING id"
            ),
            {"workos_user_id": workos_user_id},
        )
        if user_id is None:
            raise SQLiteImportError("The PostgreSQL owner could not be created.")
        return int(user_id)
    if row["role"] != "owner" or row["disabled_at"] is not None:
        raise SQLiteImportError("The PostgreSQL identity is not an active owner.")
    return int(row["id"])


def _returning_id(connection: Connection, statement: str, values: dict[str, Any]) -> int:
    value = connection.scalar(text(statement), values)
    if value is None:
        raise SQLiteImportError("PostgreSQL did not return an imported row identifier.")
    return int(value)


def _rows(source: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return source.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()


def sqlite_table_rows(source: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    """Return stable source rows for one allowlisted imported table."""
    if table not in SOURCE_TABLES and table not in {"app_users", "search_runs"}:
        raise SQLiteImportError("The requested SQLite snapshot table is not importable.")
    return _rows(source, table)


def _insert_stored_object(target: Connection, staged: Any) -> None:
    metadata = staged.metadata
    target.execute(
        text(
            """
            INSERT INTO careertrellis.stored_objects
                (id, owner_user_id, object_key, content_type, plaintext_size,
                 sha256, wrapped_data_key)
            VALUES
                (:id, :owner, :object_key, :content_type, :plaintext_size,
                 :sha256, :wrapped_data_key)
            """
        ),
        {
            "id": metadata.id,
            "owner": metadata.owner_user_id,
            "object_key": metadata.object_key,
            "content_type": metadata.content_type,
            "plaintext_size": metadata.size_bytes,
            "sha256": bytes.fromhex(metadata.sha256),
            "wrapped_data_key": metadata.wrapped_data_key,
        },
    )


def _stage_application_objects(
    source_path: Path,
    row: sqlite3.Row,
    target: Connection,
    owner_id: int,
    *,
    object_backend: Any,
    object_keyring: Any | None,
    staged_objects: list[Any],
    generated_file_snapshots: dict[tuple[int, str], GeneratedFileSnapshot],
) -> dict[str, Any | None]:
    references: dict[str, Any | None] = {
        "tailored_resume_object_id": None,
        "cover_letter_object_id": None,
    }
    for column, reference_column in (
        ("tailored_resume_path", "tailored_resume_object_id"),
        ("cover_letter_path", "cover_letter_object_id"),
    ):
        if not str(row[column] or "").strip():
            continue
        _suffix, content_type = GENERATED_FILE_TYPES[column]
        snapshot = generated_file_snapshots.get((int(row["id"]), column))
        if snapshot is None:
            raise SQLiteImportError("A generated file was not captured during import preflight.")
        plaintext = _read_generated_file(
            source_path,
            row[column],
            column,
            snapshot,
        )
        staged = stage_object(
            object_backend,
            owner_id,
            plaintext,
            content_type,
            keyring=object_keyring,
        )
        staged_objects.append(staged)
        _insert_stored_object(target, staged)
        references[reference_column] = staged.metadata.id
    return references


def _discard_staged_objects(object_backend: Any, staged_objects: list[Any]) -> int:
    failures = 0
    for staged in reversed(staged_objects):
        deleted = False
        for _attempt in range(3):
            try:
                discard_staged_object(object_backend, staged)
                deleted = True
                break
            except Exception:
                continue
        if not deleted:
            failures += 1
    return failures


def _import_rows(
    source: sqlite3.Connection,
    target: Connection,
    owner_id: int,
    source_zone: ZoneInfo,
    *,
    omit_provider_secrets: bool,
    omit_generated_files: bool,
    source_path: Path,
    object_backend: Any | None,
    object_keyring: Any | None,
    staged_objects: list[Any],
    generated_file_snapshots: dict[tuple[int, str], GeneratedFileSnapshot],
) -> dict[str, int]:
    resume_ids: dict[int, int] = {}
    job_ids: dict[int, int] = {}

    if not omit_provider_secrets:
        for row in _rows(source, "profile"):
            for column, provider in (
                ("gemini_api_key", "gemini"),
                ("openai_api_key", "openai"),
                ("google_maps_api_key", "google_maps"),
            ):
                plaintext = str(row[column] or "").strip()
                if not plaintext:
                    continue
                key_id, ciphertext = encrypt_provider_secret(owner_id, provider, plaintext)
                target.execute(
                    text(
                        """
                        INSERT INTO careertrellis.provider_secrets
                            (owner_user_id, provider, key_id, ciphertext)
                        VALUES (:owner, :provider, :key_id, :ciphertext)
                        """
                    ),
                    {
                        "owner": owner_id,
                        "provider": provider,
                        "key_id": key_id,
                        "ciphertext": ciphertext,
                    },
                )

    for row in _rows(source, "base_resumes"):
        resume_ids[int(row["id"])] = _returning_id(
            target,
            """
            INSERT INTO careertrellis.base_resumes
                (owner_user_id, name, resume_mode, content, evidence, created_at, updated_at)
            VALUES
                (:owner, :name, :mode, :content, CAST(:evidence AS jsonb), :created, :updated)
            RETURNING id
            """,
            {
                "owner": owner_id, "name": row["name"],
                "mode": row["resume_mode"] or "general_professional",
                "content": row["content"] or "", "evidence": _json_object(row["evidence_json"]),
                "created": _timestamp(row["created_at"], source_zone),
                "updated": _timestamp(row["updated_at"], source_zone),
            },
        )

    for row in _rows(source, "base_resume_versions"):
        target.execute(
            text(
                """
                INSERT INTO careertrellis.base_resume_versions
                    (owner_user_id, base_resume_id, version_number, name, resume_mode,
                     content, evidence, created_at)
                VALUES
                    (:owner, :resume, :version, :name, :mode, :content,
                     CAST(:evidence AS jsonb), :created)
                """
            ),
            {
                "owner": owner_id, "resume": resume_ids[int(row["base_resume_id"])],
                "version": row["version_number"], "name": row["name"],
                "mode": row["resume_mode"], "content": row["content"] or "",
                "evidence": _json_object(row["evidence_json"]),
                "created": _timestamp(row["created_at"], source_zone),
            },
        )

    for row in _rows(source, "profile"):
        active_resume = row["active_base_resume_id"]
        target.execute(
            text(
                """
                INSERT INTO careertrellis.profiles
                    (owner_user_id, name, email, phone, github, linkedin, website,
                     suggested_keywords, ai_provider, ai_model, maps_provider,
                     prefer_us_headquarters, interface_language, active_base_resume_id)
                VALUES
                    (:owner, :name, :email, :phone, :github, :linkedin, :website,
                     :keywords, :ai_provider, :ai_model, :maps_provider,
                     :prefer_us, :language, :active_resume)
                """
            ),
            {
                "owner": owner_id, "name": row["name"], "email": row["email"],
                "phone": row["phone"], "github": row["github"],
                "linkedin": row["linkedin"], "website": row["website"],
                "keywords": row["suggested_keywords"] or "",
                "ai_provider": row["ai_provider"] or "gemini",
                "ai_model": row["ai_model"] or "gemini-2.5-flash",
                "maps_provider": row["maps_provider"] or "openstreetmap",
                "prefer_us": bool(row["prefer_us_headquarters"]),
                "language": row["interface_language"] or "en",
                "active_resume": resume_ids.get(int(active_resume)) if active_resume else None,
            },
        )

    for row in _rows(source, "jobs"):
        job_ids[int(row["id"])] = _returning_id(
            target,
            """
            INSERT INTO careertrellis.jobs
                (owner_user_id, title, company, description, url, match_score,
                 match_analysis, date_found, status, archived_at, archived_from_status,
                 last_checked_at, is_expired, expiration_reason, location,
                 work_arrangement, employment_type, compensation, source)
            VALUES
                (:owner, :title, :company, :description, :url, :score,
                 :analysis, :date_found, :status, :archived_at, :archived_from_status,
                 :last_checked_at, :is_expired, :expiration_reason, :location,
                 :work_arrangement, :employment_type, :compensation, :source)
            RETURNING id
            """,
            {
                "owner": owner_id, "title": row["title"], "company": row["company"],
                "description": row["description"], "url": row["url"],
                "score": row["match_score"], "analysis": row["match_analysis"],
                "date_found": _date(row["date_found"]), "status": row["status"] or "matched",
                "archived_at": _timestamp(row["archived_at"], source_zone),
                "archived_from_status": row["archived_from_status"],
                "last_checked_at": _timestamp(row["last_checked_at"], source_zone),
                "is_expired": bool(row["is_expired"]),
                "expiration_reason": row["expiration_reason"], "location": row["location"],
                "work_arrangement": row["work_arrangement"],
                "employment_type": row["employment_type"],
                "compensation": row["compensation"], "source": row["source"],
            },
        )

    for row in _rows(source, "saved_searches"):
        target.execute(
            text(
                """
                INSERT INTO careertrellis.saved_searches
                    (owner_user_id, name, keywords, location, created_at, last_run_at,
                     schedule_frequency, next_alert_at, enabled)
                VALUES
                    (:owner, :name, :keywords, :location, :created, :last_run,
                     :frequency, :next_alert, :enabled)
                """
            ),
            {
                "owner": owner_id, "name": row["name"], "keywords": row["keywords"],
                "location": row["location"] or "",
                "created": _timestamp(row["created_at"], source_zone),
                "last_run": _timestamp(row["last_run_at"], source_zone),
                "frequency": row["schedule_frequency"] or "none",
                "next_alert": _timestamp(row["next_alert_at"], source_zone),
                "enabled": bool(row["enabled"]),
            },
        )

    for row in _rows(source, "applications"):
        base_resume = row["base_resume_id"]
        object_references = {
            "tailored_resume_object_id": None,
            "cover_letter_object_id": None,
        }
        has_generated_files = any(str(row[column] or "").strip() for column in FILE_PATH_COLUMNS)
        if not omit_generated_files and has_generated_files:
            if object_backend is None:
                raise SQLiteImportError("Private object storage is required for generated files.")
            object_references = _stage_application_objects(
                source_path,
                row,
                target,
                owner_id,
                object_backend=object_backend,
                object_keyring=object_keyring,
                staged_objects=staged_objects,
                generated_file_snapshots=generated_file_snapshots,
            )
        target.execute(
            text(
                """
                INSERT INTO careertrellis.applications
                    (owner_user_id, job_id, company, position, date_applied, us_hq,
                     tailored_resume_object_id, cover_letter_object_id,
                     tailored_resume_text, cover_letter, status, created_at,
                     tailored_at, form_filled_at, submitted_at, confirmed_at,
                     application_method, submission_evidence, notes, follow_up_date,
                     headquarters_source, headquarters_attribution, base_resume_id,
                     base_resume_name, base_resume_version, interview_prep,
                     interview_prep_updated_at)
                VALUES
                    (:owner, :job, :company, :position, :date_applied, :us_hq,
                     :resume_object, :letter_object, :resume_text, :cover_letter,
                     :status, :created, :tailored, :form_filled, :submitted,
                     :confirmed, :method, :evidence, :notes, :follow_up,
                     :hq_source, :hq_attribution, :base_resume, :base_resume_name,
                     :base_resume_version, :interview_prep, :interview_prep_updated)
                """
            ),
            {
                "owner": owner_id, "job": job_ids[int(row["job_id"])],
                "company": row["company"], "position": row["position"],
                "date_applied": _date(row["date_applied"]), "us_hq": row["us_hq"],
                "resume_object": object_references["tailored_resume_object_id"],
                "letter_object": object_references["cover_letter_object_id"],
                "resume_text": row["tailored_resume_text"], "cover_letter": row["cover_letter"],
                "status": row["status"] or "tailored",
                "created": _timestamp(row["created_at"], source_zone),
                "tailored": _timestamp(row["tailored_at"], source_zone),
                "form_filled": _timestamp(row["form_filled_at"], source_zone),
                "submitted": _timestamp(row["submitted_at"], source_zone),
                "confirmed": _timestamp(row["confirmed_at"], source_zone),
                "method": row["application_method"], "evidence": row["submission_evidence"],
                "notes": row["notes"], "follow_up": _date(row["follow_up_date"]),
                "hq_source": row["headquarters_source"],
                "hq_attribution": row["headquarters_attribution"],
                "base_resume": resume_ids.get(int(base_resume)) if base_resume else None,
                "base_resume_name": row["base_resume_name"],
                "base_resume_version": row["base_resume_version"],
                "interview_prep": row["interview_prep"],
                "interview_prep_updated": _timestamp(row["interview_prep_updated_at"], source_zone),
            },
        )

    for source_table, target_table, columns, conversions in (
        (
            "application_status_history", "application_status_history",
            ("from_status", "to_status", "source", "notes"),
            {"changed_at": "timestamp", "undone_at": "timestamp"},
        ),
        (
            "application_engagements", "application_engagements",
            ("engagement_type", "name", "organization", "contact_details", "status", "notes"),
            {"activity_on": "date", "next_action_on": "date", "created_at": "timestamp", "updated_at": "timestamp"},
        ),
    ):
        for row in _rows(source, source_table):
            values: dict[str, Any] = {
                "owner_user_id": owner_id,
                "job_id": job_ids[int(row["job_id"])],
            }
            values.update({column: row[column] for column in columns})
            for column, conversion in conversions.items():
                values[column] = (
                    _timestamp(row[column], source_zone)
                    if conversion == "timestamp" else _date(row[column])
                )
            names = tuple(values)
            target.execute(
                text(
                    f"INSERT INTO careertrellis.{target_table} "
                    f"({', '.join(names)}) VALUES ({', '.join(':' + name for name in names)})"
                ),
                values,
            )

    for row in _rows(source, "job_suppressions"):
        target.execute(
            text(
                """
                INSERT INTO careertrellis.job_suppressions
                    (owner_user_id, url_fingerprint, hostname, company, title,
                     deleted_at, deletion_source)
                VALUES (:owner, :fingerprint, :hostname, :company, :title,
                        :deleted_at, :source)
                """
            ),
            {
                "owner": owner_id, "fingerprint": row["url_fingerprint"],
                "hostname": row["hostname"], "company": row["company"] or "",
                "title": row["title"] or "",
                "deleted_at": _timestamp(row["deleted_at"], source_zone),
                "source": row["deletion_source"],
            },
        )

    for row in _rows(source, "source_diagnostics"):
        target.execute(
            text(
                """
                INSERT INTO careertrellis.source_diagnostics
                    (owner_user_id, recorded_at, provider, diagnostic_code, counters)
                VALUES (:owner, :recorded, :provider, :code, CAST(:counters AS jsonb))
                """
            ),
            {
                "owner": owner_id, "recorded": _timestamp(row["recorded_at"], source_zone),
                "provider": row["provider"], "code": row["diagnostic_code"],
                "counters": _json_object(row["counters_json"]),
            },
        )

    for row in _rows(source, "headquarters_cache"):
        target.execute(
            text(
                """
                INSERT INTO careertrellis.headquarters_cache
                    (owner_user_id, cache_key, provider, address, country_code,
                     attribution, resolved_at)
                VALUES (:owner, :cache_key, :provider, :address, :country_code,
                        :attribution, :resolved_at)
                """
            ),
            {
                "owner": owner_id, "cache_key": row["cache_key"],
                "provider": row["provider"], "address": row["address"],
                "country_code": row["country_code"] or "",
                "attribution": row["attribution"] or "",
                "resolved_at": _timestamp(row["resolved_at"], source_zone),
            },
        )

    return {
        target_table: int(target.scalar(text(f"SELECT COUNT(*) FROM careertrellis.{target_table}")))
        for target_table in TARGET_TABLES
    }


def _expected_target_counts(
    manifest: ImportManifest,
    *,
    omit_provider_secrets: bool,
    omit_generated_files: bool,
) -> dict[str, int]:
    expected = {
        target_table: manifest.counts[source_table]
        for source_table, target_table in SOURCE_TO_TARGET.items()
    }
    expected["provider_secrets"] = (
        0 if omit_provider_secrets else manifest.provider_secret_fields
    )
    expected["stored_objects"] = (
        0 if omit_generated_files else manifest.generated_file_paths
    )
    return {table: expected[table] for table in TARGET_TABLES}


def expected_target_counts(
    manifest: ImportManifest,
    *,
    omit_provider_secrets: bool,
    omit_generated_files: bool,
) -> dict[str, int]:
    """Return the exact owner-scoped target counts expected after import."""
    return _expected_target_counts(
        manifest,
        omit_provider_secrets=omit_provider_secrets,
        omit_generated_files=omit_generated_files,
    )


def import_sqlite_workspace(
    source_path: Path,
    owner_workos_user_id: str,
    *,
    omit_provider_secrets: bool = False,
    omit_generated_files: bool = False,
    engine: Engine | None = None,
    object_backend: Any | None = None,
    object_keyring: Any | None = None,
) -> ImportManifest:
    """Import into an empty owner scope and commit only after exact count checks."""
    owned_engine = False
    target_engine: Engine | None = None
    staged_objects: list[Any] = []
    resolved_object_backend = object_backend
    source = _open_source(source_path)
    try:
        source.execute("BEGIN")
        manifest = inspect_sqlite_workspace(
            source_path,
            owner_workos_user_id,
            validate_generated_files=not omit_generated_files,
            _connection=source,
        )
        owned_engine = engine is None
        target_engine = engine or create_postgres_engine(
            database_url_from_environment(MIGRATION_DATABASE_URL_ENV)
        )
        with target_engine.connect() as target:
            transaction = target.begin()
            commit_started = False
            try:
                target.exec_driver_sql("SET ROLE careertrellis_schema_owner")
                # The durable maintenance gate and shared import lock precede
                # every provider operation. They do not hold the admission lock.
                target.exec_driver_sql("SELECT careertrellis_admission.assert_import_maintenance()")
                if not omit_generated_files:
                    resolved_object_backend = resolved_object_backend or runtime_object_backend()
                    require_empty_object_inventory(resolved_object_backend)
                target.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"),
                    {"name": IMPORT_LOCK_NAME},
                )
                if not omit_generated_files:
                    # Closed maintenance is established before provider setup.
                    # The import lock makes this inventory check authoritative
                    # before an owner, object or imported row can be created.
                    require_empty_object_inventory(resolved_object_backend)
                owner_id = _seed_owner(target, owner_workos_user_id)
                set_tenant_context(target, owner_id, owner_workos_user_id)
                _target_must_be_empty(target)
                imported_counts = _import_rows(
                    source,
                    target,
                    owner_id,
                    _source_zone(),
                    omit_provider_secrets=omit_provider_secrets,
                    omit_generated_files=omit_generated_files,
                    source_path=source_path,
                    object_backend=resolved_object_backend,
                    object_keyring=object_keyring,
                    staged_objects=staged_objects,
                    generated_file_snapshots=manifest.generated_file_snapshots,
                )
                expected_counts = _expected_target_counts(
                    manifest,
                    omit_provider_secrets=omit_provider_secrets,
                    omit_generated_files=omit_generated_files,
                )
                if imported_counts != expected_counts:
                    raise SQLiteImportError(
                        "PostgreSQL row counts did not match the SQLite source."
                    )
                target.exec_driver_sql("RESET ROLE")
                commit_started = True
                transaction.commit()
            except BaseException as exc:
                if commit_started:
                    try:
                        target.invalidate()
                    except Exception:
                        pass
                    raise SQLiteImportReconciliationRequired(
                        "The PostgreSQL commit outcome is indeterminate; encrypted objects "
                        f"were retained and {len(staged_objects)} staged object(s) require "
                        "database/object reconciliation before retrying."
                    ) from exc
                rollback_error: BaseException | None = None
                try:
                    if transaction.is_active:
                        transaction.rollback()
                except BaseException as rollback_exc:
                    rollback_error = rollback_exc
                cleanup_failures = (
                    _discard_staged_objects(resolved_object_backend, staged_objects)
                    if resolved_object_backend is not None
                    else 0
                )
                if rollback_error is not None:
                    try:
                        target.invalidate()
                    except Exception:
                        pass
                    if cleanup_failures:
                        raise SQLiteImportReconciliationRequired(
                            f"The failed import could not confirm PostgreSQL rollback and "
                            f"{cleanup_failures} encrypted object(s) could not be removed; "
                            "database/object reconciliation is required."
                        ) from rollback_error
                    raise SQLiteImportReconciliationRequired(
                        "The failed import could not confirm PostgreSQL rollback; no encrypted "
                        "objects remain staged, but database reconciliation is required."
                    ) from rollback_error
                try:
                    target.exec_driver_sql("RESET ROLE")
                    target.commit()
                except Exception:
                    target.invalidate()
                if cleanup_failures:
                    raise SQLiteImportReconciliationRequired(
                        f"The import failed and {cleanup_failures} encrypted object(s) could "
                        "not be removed after three attempts; database/object reconciliation "
                        "is required."
                    ) from exc
                raise
        return replace(manifest, verified_target_counts=imported_counts)
    finally:
        source.rollback()
        source.close()
        if owned_engine and target_engine is not None:
            target_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Path to the SQLite workspace database")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--omit-provider-secrets", action="store_true")
    parser.add_argument("--omit-generated-files", action="store_true")
    arguments = parser.parse_args()
    owner_workos_user_id = os.environ.get(OWNER_ID_ENVIRONMENT_VARIABLE, "").strip()
    if not owner_workos_user_id:
        raise SQLiteImportError(f"{OWNER_ID_ENVIRONMENT_VARIABLE} is required.")
    manifest = inspect_sqlite_workspace(
        arguments.source,
        owner_workos_user_id,
        validate_generated_files=not arguments.omit_generated_files,
    )
    if not arguments.preflight_only:
        manifest = import_sqlite_workspace(
            arguments.source,
            owner_workos_user_id,
            omit_provider_secrets=arguments.omit_provider_secrets,
            omit_generated_files=arguments.omit_generated_files,
        )
    print(json.dumps(manifest.sanitized_summary(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("SQLite/PostgreSQL import failed.", file=sys.stderr)
        raise SystemExit(1)
