"""Privacy-safe evidence receipts for the invitation-only pilot.

The receipt format is deliberately closed: callers can supply only bounded
enumerations, booleans, and small aggregate counts.  Validation errors never
echo caller-controlled field names or values.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "careertrellis.pilot-auth-acceptance/v1"
RECEIPT_ROOT = Path("/var/backups/careertrellis/pilot-auth-acceptance")

OPERATIONS = frozenset(
    {
        "baseline_negative_auth",
        "owner_login",
        "pilot_login",
        "unlisted_denial",
        "disabled_denial",
        "missing_context",
        "pool_reset",
        "tenant_isolation",
        "object_isolation",
        "worker_isolation",
        "export_isolation",
        "erasure_isolation",
        "privacy_log_review",
        "two_user_acceptance",
        "invitation_smoke",
    }
)
RESULTS = frozenset({"ok", "failed", "blocked"})
SUBJECT_KINDS = frozenset(
    {"owner", "pilot", "unlisted", "disabled", "missing_context"}
)
HTTP_STATUS_CLASSES = frozenset({"2xx", "3xx", "4xx", "5xx"})

# The keys are an allowlist, rather than merely a spelling constraint, so an
# identity or secret cannot be smuggled into a receipt as a mapping key.
CHECK_KEYS = frozenset(
    {
        "invitation_only",
        "email_verification_required",
        "password_login_allowed",
        "mfa_required",
        "mapping_present",
        "role_expected",
        "active",
        "session_issued",
        "local_row_created",
        "login_succeeded",
        "logout_succeeded",
        "recovery_succeeded",
        "owner_immutable",
        "cross_tenant_exposed",
        "data_isolated",
        "object_isolated",
        "worker_isolated",
        "provider_secret_isolated",
        "search_queue_isolated",
        "export_isolated",
        "erasure_isolated",
        "download_isolated",
        "missing_context_exposed",
        "pooled_context_cleared",
        "disabled_work_contained",
        "auth_routes_logged",
        "forbidden_field_present",
        "synthetic_data_removed",
    }
)
COUNT_KEYS = frozenset(
    {
        "active_owner_count",
        "active_pilot_count",
        "tested_tenant_count",
        "tested_surface_count",
        "unlisted_denial_count",
        "disabled_denial_count",
        "forbidden_field_match_count",
        "auth_route_log_count",
        "queued_search_count",
        "queued_erasure_count",
    }
)
NOTE_VALUES = frozenset(
    {
        "basic_auth_retained",
        "mfa_optional",
        "synthetic_data_only",
        "manual_browser_step",
        "automated_database_step",
        "no_real_pilot_data",
    }
)

_RELEASE_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
_BUILD_PATTERN = re.compile(r"[0-9]{8}\.[0-9]{1,6}\Z")
_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z\Z"
)
_OPERATION_FILENAME_PATTERN = "|".join(
    re.escape(operation) for operation in sorted(OPERATIONS)
)
_RECEIPT_NAME_PATTERN = re.compile(
    rf"[0-9]{{8}}T[0-9]{{6}}Z-(?:{_OPERATION_FILENAME_PATTERN})-"
    r"[0-9a-f]{16}\.json\Z"
)
_MANIFEST_NAME_PATTERN = re.compile(
    rf"[0-9]{{8}}T[0-9]{{6}}Z-(?:{_OPERATION_FILENAME_PATTERN})-"
    r"[0-9a-f]{16}\.sha256\Z"
)
_MAX_AGGREGATE_COUNT = 1_000_000
_MAX_WRITE_ATTEMPTS = 8
_MAX_RECEIPT_BYTES = 16 * 1024
_MAX_MANIFEST_BYTES = 512
_VALIDATION_ERROR = "Pilot acceptance receipt is invalid."
_PERSISTENCE_ERROR = "Pilot acceptance receipt could not be retained safely."
_VERIFICATION_ERROR = "Pilot acceptance receipt is not committed or is invalid."


class ReceiptValidationError(ValueError):
    """The proposed receipt did not match the closed privacy-safe schema."""


class ReceiptPersistenceError(RuntimeError):
    """The receipt root or exclusive write contract could not be verified."""


class ReceiptVerificationError(ValueError):
    """A manifest-committed receipt could not be verified safely."""


@dataclass(frozen=True)
class ReceiptFiles:
    """Sanitized filenames created for one receipt and its digest manifest."""

    receipt_name: str
    manifest_name: str


def _invalid() -> ReceiptValidationError:
    return ReceiptValidationError(_VALIDATION_ERROR)


def _allowed_string(value: object, allowed: frozenset[str]) -> bool:
    return isinstance(value, str) and value in allowed


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise _invalid()
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        raise _invalid() from None


def _validate_boolean_map(value: object) -> dict[str, bool]:
    if not isinstance(value, dict) or not value or len(value) > len(CHECK_KEYS):
        raise _invalid()
    if not set(value).issubset(CHECK_KEYS):
        raise _invalid()
    if any(type(item) is not bool for item in value.values()):
        raise _invalid()
    return {key: value[key] for key in sorted(value)}


def _validate_count_map(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or not value or len(value) > len(COUNT_KEYS):
        raise _invalid()
    if not set(value).issubset(COUNT_KEYS):
        raise _invalid()
    if any(
        type(item) is not int or not 0 <= item <= _MAX_AGGREGATE_COUNT
        for item in value.values()
    ):
        raise _invalid()
    return {key: value[key] for key in sorted(value)}


def _validate_notes(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > len(NOTE_VALUES)
        or any(not isinstance(item, str) for item in value)
        or len(set(value)) != len(value)
        or not set(value).issubset(NOTE_VALUES)
    ):
        raise _invalid()
    return sorted(value)


def validate_receipt(document: object) -> dict[str, object]:
    """Return a canonical validated copy of a privacy-safe receipt document.

    No validation error contains caller-controlled data.  This is important
    because callers may send errors to an operator journal or CI output.
    """

    required = {
        "schema",
        "operation",
        "result",
        "release",
        "build",
        "started_at",
        "completed_at",
        "subject_kind",
        "checks",
    }
    optional = {"http_status_class", "counts", "notes"}
    if not isinstance(document, dict):
        raise _invalid()
    if set(document) - required - optional or required - set(document):
        raise _invalid()
    if document.get("schema") != SCHEMA:
        raise _invalid()
    if not _allowed_string(document.get("operation"), OPERATIONS):
        raise _invalid()
    if not _allowed_string(document.get("result"), RESULTS):
        raise _invalid()
    release = document.get("release")
    build = document.get("build")
    if not isinstance(release, str) or _RELEASE_PATTERN.fullmatch(release) is None:
        raise _invalid()
    if not isinstance(build, str) or _BUILD_PATTERN.fullmatch(build) is None:
        raise _invalid()
    if not _allowed_string(document.get("subject_kind"), SUBJECT_KINDS):
        raise _invalid()

    started_at = _parse_timestamp(document.get("started_at"))
    completed_at = _parse_timestamp(document.get("completed_at"))
    if completed_at < started_at:
        raise _invalid()

    normalized: dict[str, object] = {
        "schema": SCHEMA,
        "operation": document["operation"],
        "result": document["result"],
        "release": release,
        "build": build,
        "started_at": document["started_at"],
        "completed_at": document["completed_at"],
        "subject_kind": document["subject_kind"],
    }
    if "http_status_class" in document:
        if not _allowed_string(
            document["http_status_class"], HTTP_STATUS_CLASSES
        ):
            raise _invalid()
        normalized["http_status_class"] = document["http_status_class"]
    normalized["checks"] = _validate_boolean_map(document["checks"])
    if "counts" in document:
        normalized["counts"] = _validate_count_map(document["counts"])
    if "notes" in document:
        normalized["notes"] = _validate_notes(document["notes"])
    return normalized


def serialize_receipt(document: object) -> bytes:
    """Validate and serialize a receipt as stable UTF-8 JSON."""

    normalized = validate_receipt(document)
    return (
        json.dumps(
            normalized,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _unverified() -> ReceiptVerificationError:
    return ReceiptVerificationError(_VERIFICATION_ERROR)


def verify_receipt(
    receipt_name: object,
    receipt_payload: object,
    manifest_payload: object,
) -> dict[str, object]:
    """Verify one canonical receipt through its required commit manifest.

    Receipt JSON without the exact adjacent manifest is nonterminal evidence and
    is never accepted.  This verifier intentionally has no JSON-only mode.
    """

    if (
        not isinstance(receipt_name, str)
        or _RECEIPT_NAME_PATTERN.fullmatch(receipt_name) is None
        or type(receipt_payload) is not bytes
        or type(manifest_payload) is not bytes
        or not 0 < len(receipt_payload) <= _MAX_RECEIPT_BYTES
        or not 0 < len(manifest_payload) <= _MAX_MANIFEST_BYTES
    ):
        raise _unverified()
    digest = hashlib.sha256(receipt_payload).hexdigest()
    expected_manifest = f"{digest}  {receipt_name}\n".encode("ascii")
    if not secrets.compare_digest(manifest_payload, expected_manifest):
        raise _unverified()
    try:
        decoded = json.loads(receipt_payload.decode("ascii"))
        normalized = validate_receipt(decoded)
        canonical = serialize_receipt(normalized)
    except (UnicodeDecodeError, json.JSONDecodeError, ReceiptValidationError):
        raise _unverified() from None
    if not secrets.compare_digest(receipt_payload, canonical):
        raise _unverified()
    return normalized


def _effective_owner(root: Path) -> tuple[int, int]:
    required_constants = ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW")
    if (
        os.name != "posix"
        or not hasattr(os, "geteuid")
        or not hasattr(os, "getegid")
        or any(not hasattr(os, name) for name in required_constants)
    ):
        raise ReceiptPersistenceError(_PERSISTENCE_ERROR)
    effective = (os.geteuid(), os.getegid())
    if root == RECEIPT_ROOT and effective != (0, 0):
        raise ReceiptPersistenceError(_PERSISTENCE_ERROR)
    return (0, 0) if root == RECEIPT_ROOT else effective


def _validate_directory(path: Path, expected_owner: tuple[int, int]) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError:
        raise ReceiptPersistenceError(_PERSISTENCE_ERROR) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or (metadata.st_uid, metadata.st_gid) != expected_owner
    ):
        raise ReceiptPersistenceError(_PERSISTENCE_ERROR)
    return metadata


def _reject_symlink_components(path: Path, *, final_may_be_missing: bool) -> None:
    current = Path(path.anchor)
    for index, component in enumerate(path.parts[1:], start=1):
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if final_may_be_missing and index == len(path.parts) - 1:
                return
            raise ReceiptPersistenceError(_PERSISTENCE_ERROR)
        except OSError:
            raise ReceiptPersistenceError(_PERSISTENCE_ERROR) from None
        if stat.S_ISLNK(metadata.st_mode):
            raise ReceiptPersistenceError(_PERSISTENCE_ERROR)


def _ensure_root(
    root: Path, expected_owner: tuple[int, int]
) -> os.stat_result:
    if not root.is_absolute():
        raise ReceiptPersistenceError(_PERSISTENCE_ERROR)
    _reject_symlink_components(root, final_may_be_missing=True)
    created = False
    try:
        os.mkdir(root, 0o700)
        created = True
    except FileExistsError:
        pass
    except OSError:
        raise ReceiptPersistenceError(_PERSISTENCE_ERROR) from None
    if created:
        directory_fd = -1
        try:
            directory_fd = os.open(
                root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            os.fchmod(directory_fd, 0o700)
            os.fchown(directory_fd, expected_owner[0], expected_owner[1])
            os.fsync(directory_fd)
        except OSError:
            raise ReceiptPersistenceError(_PERSISTENCE_ERROR) from None
        finally:
            if directory_fd >= 0:
                os.close(directory_fd)
    _reject_symlink_components(root, final_may_be_missing=False)
    return _validate_directory(root, expected_owner)


def _open_verified_directory(
    root: Path,
    expected_owner: tuple[int, int],
    expected_metadata: os.stat_result,
) -> int:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory_fd = -1
    try:
        directory_fd = os.open(root, directory_flags)
        opened = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o700
            or (opened.st_uid, opened.st_gid) != expected_owner
            or (opened.st_dev, opened.st_ino)
            != (expected_metadata.st_dev, expected_metadata.st_ino)
        ):
            raise OSError(errno.EPERM, "unsafe receipt directory")
        return directory_fd
    except OSError:
        if directory_fd >= 0:
            os.close(directory_fd)
        raise ReceiptPersistenceError(_PERSISTENCE_ERROR) from None


def _read_protected_file(
    directory_fd: int,
    name: str,
    expected_owner: tuple[int, int],
    maximum_size: int,
) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or (metadata.st_uid, metadata.st_gid) != expected_owner
            or not 0 < metadata.st_size <= maximum_size
        ):
            raise OSError(errno.EPERM, "unsafe receipt file")
        remaining = metadata.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                raise OSError(errno.EIO, "short receipt read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError(errno.EIO, "receipt changed during read")
        return b"".join(chunks)
    except OSError:
        raise _unverified() from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_receipt(
    manifest_name: object, *, root: Path | str = RECEIPT_ROOT
) -> dict[str, object]:
    """Read a committed receipt by manifest name after protected verification.

    A receipt filename is not an accepted entry point.  The adjacent manifest is
    the final commit marker and must bind the exact canonical JSON payload.
    """

    if (
        not isinstance(manifest_name, str)
        or _MANIFEST_NAME_PATTERN.fullmatch(manifest_name) is None
    ):
        raise _unverified()
    receipt_name = f"{manifest_name[:-len('.sha256')]}.json"
    selected_root = Path(root)
    expected_owner = _effective_owner(selected_root)
    if not selected_root.is_absolute():
        raise ReceiptPersistenceError(_PERSISTENCE_ERROR)
    _reject_symlink_components(selected_root, final_may_be_missing=False)
    expected_metadata = _validate_directory(selected_root, expected_owner)
    directory_fd = _open_verified_directory(
        selected_root, expected_owner, expected_metadata
    )
    try:
        manifest_payload = _read_protected_file(
            directory_fd,
            manifest_name,
            expected_owner,
            _MAX_MANIFEST_BYTES,
        )
        receipt_payload = _read_protected_file(
            directory_fd,
            receipt_name,
            expected_owner,
            _MAX_RECEIPT_BYTES,
        )
        return verify_receipt(receipt_name, receipt_payload, manifest_payload)
    finally:
        os.close(directory_fd)


def _write_staged_file(
    directory_fd: int,
    name: str,
    payload: bytes,
    expected_owner: tuple[int, int],
) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        os.fchmod(descriptor, 0o600)
        if hasattr(os, "fchown"):
            os.fchown(descriptor, expected_owner[0], expected_owner[1])
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or (metadata.st_uid, metadata.st_gid) != expected_owner
        ):
            raise OSError(errno.EPERM, "unsafe receipt metadata")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            offset += written
        os.fsync(descriptor)
    except OSError:
        os.close(descriptor)
        descriptor = -1
        _unlink_quietly(directory_fd, name)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _unlink_quietly(directory_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except OSError:
        pass


def _link_exclusive(directory_fd: int, source: str, target: str) -> None:
    os.link(
        source,
        target,
        src_dir_fd=directory_fd,
        dst_dir_fd=directory_fd,
        follow_symlinks=False,
    )


def write_receipt(
    document: object, *, root: Path | str = RECEIPT_ROOT
) -> ReceiptFiles:
    """Exclusively retain a receipt and adjacent SHA-256 manifest.

    The production default requires a root process and a root-owned mode-0700
    directory.  A different absolute root may be injected by isolated tests;
    it must be owned by that test process and have the same restrictive mode.
    Neither final filename is ever replaced.
    """

    payload = serialize_receipt(document)
    selected_root = Path(root)
    expected_owner = _effective_owner(selected_root)
    expected_metadata = _ensure_root(selected_root, expected_owner)

    directory_fd = _open_verified_directory(
        selected_root, expected_owner, expected_metadata
    )

    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        operation = validate_receipt(document)["operation"]
        digest = hashlib.sha256(payload).hexdigest()
        for _ in range(_MAX_WRITE_ATTEMPTS):
            attempt = secrets.token_hex(8)
            stem = f"{timestamp}-{operation}-{attempt}"
            receipt_name = f"{stem}.json"
            manifest_name = f"{stem}.sha256"
            receipt_stage = f".{stem}.receipt.pending"
            manifest_stage = f".{stem}.manifest.pending"
            manifest = f"{digest}  {receipt_name}\n".encode("ascii")
            staged_receipt = False
            staged_manifest = False
            linked_receipt = False
            linked_manifest = False
            try:
                _write_staged_file(
                    directory_fd, receipt_stage, payload, expected_owner
                )
                staged_receipt = True
                _write_staged_file(
                    directory_fd, manifest_stage, manifest, expected_owner
                )
                staged_manifest = True
                _link_exclusive(directory_fd, receipt_stage, receipt_name)
                linked_receipt = True
                os.fsync(directory_fd)
                _link_exclusive(directory_fd, manifest_stage, manifest_name)
                linked_manifest = True
                os.fsync(directory_fd)
                return ReceiptFiles(receipt_name, manifest_name)
            except FileExistsError:
                if linked_manifest:
                    _unlink_quietly(directory_fd, manifest_name)
                if linked_receipt:
                    _unlink_quietly(directory_fd, receipt_name)
            except OSError:
                if linked_manifest:
                    _unlink_quietly(directory_fd, manifest_name)
                if linked_receipt:
                    _unlink_quietly(directory_fd, receipt_name)
                raise ReceiptPersistenceError(_PERSISTENCE_ERROR) from None
            finally:
                if staged_receipt:
                    _unlink_quietly(directory_fd, receipt_stage)
                if staged_manifest:
                    _unlink_quietly(directory_fd, manifest_stage)
        raise ReceiptPersistenceError(_PERSISTENCE_ERROR)
    finally:
        os.close(directory_fd)
