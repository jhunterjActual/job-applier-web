"""Privacy-safe backup-health evidence and alert evaluation.

This module deliberately accepts only closed, aggregate schemas.  It never
accepts an identity, address, path, endpoint, object key, credential, or
free-form note.  Operation JSON is committed by publishing the canonical JSON
first and its adjacent SHA-256 manifest last.  A JSON file without that final
manifest is not evidence.

The implementation uses only the Python standard library so the installed
copy can run from a minimal, root-owned host location.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


RECEIPT_SCHEMA = "careertrellis.backup-health-operation/v1"
SNAPSHOT_SCHEMA = "careertrellis.backup-health-snapshot/v1"
STATUS_SCHEMA = "careertrellis.backup-health-status/v1"
ALERT_SCHEMA = "careertrellis.backup-health-alert/v1"
PUBLISH_RESULT_SCHEMA = "careertrellis.backup-health-publish-result/v1"

RECEIPT_ROOT = Path("/var/backups/careertrellis/backup-health/operations")
STATUS_ROOT = Path("/var/lib/careertrellis/backup-health")
ALERT_ROOT = STATUS_ROOT
STATUS_NAMES = {
    "database": "database-status.json",
    "objects": "objects-status.json",
}
ALERT_NAMES = {
    "database": "database-alert-state.json",
    "objects": "objects-alert-state.json",
}

OPERATIONS = (
    "full_backup",
    "differential_backup",
    "pgbackrest_mirror",
    "pgbackrest_inventory",
    "pgbackrest_restore",
    "generated_object_mirror",
    "generated_object_restore",
)
OPERATION_SET = frozenset(OPERATIONS)
SURFACE_OPERATIONS = {
    "database": (
        "full_backup",
        "differential_backup",
        "pgbackrest_mirror",
        "pgbackrest_inventory",
        "pgbackrest_restore",
    ),
    "objects": ("generated_object_mirror", "generated_object_restore"),
}
SURFACES = frozenset(SURFACE_OPERATIONS)
RESULTS = frozenset({"ok", "failed"})
UNIT_RESULTS = frozenset({"success", "failed", "never"})

MAX_RECEIPTS_PER_OPERATION = 32
MAX_DOCUMENT_BYTES = 32 * 1024
MAX_RECEIPT_BYTES = 8 * 1024
MAX_MANIFEST_BYTES = 256
MAX_STATUS_BYTES = 16 * 1024
MAX_ALERT_BYTES = 16 * 1024
MAX_INTEGER = (1 << 63) - 1
MAX_ELAPSED_SECONDS = 7 * 24 * 60 * 60
MAX_RPO_SECONDS = 366 * 24 * 60 * 60
MAX_OPERATION_SPAN = timedelta(days=7)
MAX_FUTURE_SKEW_SECONDS = 5 * 60
MAX_STATUS_AGE_SECONDS = 3 * 60 * 60
MAX_WRITE_ATTEMPTS = 16

FULL_MAX_AGE_SECONDS = 8 * 24 * 60 * 60
BACKUP_MAX_AGE_SECONDS = 2 * 24 * 60 * 60
MIRROR_MAX_AGE_SECONDS = 2 * 24 * 60 * 60
INVENTORY_MAX_AGE_SECONDS = 8 * 24 * 60 * 60
RESTORE_MAX_AGE_SECONDS = 35 * 24 * 60 * 60
GENERATED_MIRROR_MAX_AGE_SECONDS = 8 * 24 * 60 * 60
GENERATED_RESTORE_MAX_AGE_SECONDS = 35 * 24 * 60 * 60

_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z\Z"
)
_BACKUP_LABEL_PATTERN = re.compile(
    r"[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?\Z"
)
_FULL_BACKUP_LABEL_PATTERN = re.compile(r"[0-9]{8}-[0-9]{6}F\Z")
_DIFFERENTIAL_BACKUP_LABEL_PATTERN = re.compile(
    r"[0-9]{8}-[0-9]{6}F_[0-9]{8}-[0-9]{6}D\Z"
)
_OPERATION_PATTERN = "|".join(re.escape(item) for item in OPERATIONS)
_RECEIPT_NAME_PATTERN = re.compile(
    rf"[0-9]{{8}}T[0-9]{{6}}Z-(?:{_OPERATION_PATTERN})-[0-9a-f]{{16}}\.json\Z"
)
_MANIFEST_NAME_PATTERN = re.compile(
    rf"[0-9]{{8}}T[0-9]{{6}}Z-(?:{_OPERATION_PATTERN})-[0-9a-f]{{16}}\.sha256\Z"
)

_RECEIPT_FIELDS = {
    "full_backup": frozenset({"backup_label", "elapsed_seconds"}),
    "differential_backup": frozenset({"backup_label", "elapsed_seconds"}),
    "pgbackrest_mirror": frozenset(
        {
            "backup_label",
            "mirrored_backup_label",
            "object_count",
            "byte_count",
            "elapsed_seconds",
        }
    ),
    "pgbackrest_inventory": frozenset(
        {
            "backup_label",
            "source_object_count",
            "source_byte_count",
            "destination_object_count",
            "destination_byte_count",
            "divergence_count",
            "elapsed_seconds",
        }
    ),
    "pgbackrest_restore": frozenset(
        {
            "backup_label",
            "elapsed_seconds",
            "rpo_seconds",
            "rto_seconds",
            "promotion_verified",
            "checksum_verified",
            "cleanup_verified",
        }
    ),
    "generated_object_mirror": frozenset(
        {"object_count", "byte_count", "divergence_count", "elapsed_seconds"}
    ),
    "generated_object_restore": frozenset(
        {
            "object_count",
            "byte_count",
            "elapsed_seconds",
            "manifest_verified",
            "ciphertext_verified",
            "target_left_intact",
        }
    ),
}

_COMMON_RECEIPT_FIELDS = frozenset(
    {"schema", "operation", "result", "started_at", "completed_at"}
)
_BOOLEAN_RECEIPT_FIELDS = frozenset(
    {
        "promotion_verified",
        "checksum_verified",
        "cleanup_verified",
        "manifest_verified",
        "ciphertext_verified",
        "target_left_intact",
    }
)
_LABEL_RECEIPT_FIELDS = frozenset({"backup_label", "mirrored_backup_label"})
_INTEGER_RECEIPT_FIELDS = frozenset(
    {
        "object_count",
        "byte_count",
        "source_object_count",
        "source_byte_count",
        "destination_object_count",
        "destination_byte_count",
        "divergence_count",
        "elapsed_seconds",
        "rpo_seconds",
        "rto_seconds",
    }
)

_UNIT_FAILURE_CODES = frozenset(
    f"{operation}_unit_{suffix}"
    for operation in OPERATIONS
    for suffix in ("failed", "missed", "never")
)
_INTEGRITY_CODES = frozenset(
    f"{operation}_receipt_invalid" for operation in OPERATIONS
)
_FAILED_OPERATION_CODES = frozenset(
    f"{operation}_failed" for operation in OPERATIONS
)
_FUTURE_CODES = frozenset(
    f"{operation}_receipt_future" for operation in OPERATIONS
)
_POLICY_CODES = frozenset(
    {
        "full_backup_receipt_missing",
        "latest_backup_receipt_missing",
        "pgbackrest_mirror_receipt_missing",
        "pgbackrest_inventory_receipt_missing",
        "pgbackrest_restore_receipt_missing",
        "generated_object_mirror_receipt_missing",
        "generated_object_restore_receipt_missing",
        "full_backup_stale",
        "latest_backup_stale",
        "latest_backup_label_mismatch",
        "wal_failed_count_growth",
        "wal_segment_lag_exceeded",
        "wal_repository_archive_behind",
        "pgbackrest_mirror_stale",
        "pgbackrest_mirror_label_mismatch",
        "pgbackrest_inventory_stale",
        "pgbackrest_inventory_divergence",
        "pgbackrest_restore_stale",
        "pgbackrest_restore_promotion_failed",
        "pgbackrest_restore_checksum_failed",
        "pgbackrest_restore_cleanup_failed",
        "generated_object_mirror_stale",
        "generated_object_mirror_divergence",
        "generated_object_restore_stale",
        "generated_object_restore_manifest_failed",
        "generated_object_restore_ciphertext_failed",
        "generated_object_restore_target_not_retained",
    }
)
FAILURE_CODES = frozenset(
    _UNIT_FAILURE_CODES
    | _INTEGRITY_CODES
    | _FAILED_OPERATION_CODES
    | _FUTURE_CODES
    | _POLICY_CODES
) | frozenset({"health_status_stale"})


def _operation_codes(operations: Iterable[str]) -> frozenset[str]:
    return frozenset(
        code
        for operation in operations
        for code in (
            f"{operation}_unit_failed",
            f"{operation}_unit_missed",
            f"{operation}_unit_never",
            f"{operation}_receipt_invalid",
            f"{operation}_failed",
            f"{operation}_receipt_future",
        )
    )


_DATABASE_POLICY_CODES = frozenset(
    code
    for code in _POLICY_CODES
    if not code.startswith("generated_object_")
)
_OBJECT_POLICY_CODES = _POLICY_CODES - _DATABASE_POLICY_CODES
FAILURE_CODES_BY_SURFACE = {
    "database": _operation_codes(SURFACE_OPERATIONS["database"])
    | _DATABASE_POLICY_CODES
    | frozenset({"health_status_stale"}),
    "objects": _operation_codes(SURFACE_OPERATIONS["objects"])
    | _OBJECT_POLICY_CODES
    | frozenset({"health_status_stale"}),
}

AGE_KEYS = (
    "full_backup",
    "latest_backup",
    "pgbackrest_mirror",
    "pgbackrest_inventory",
    "pgbackrest_restore",
    "generated_object_mirror",
    "generated_object_restore",
)
AGE_KEYS_BY_SURFACE = {
    "database": AGE_KEYS[:5],
    "objects": AGE_KEYS[5:],
}
STATUS_AGE_LIMITS = {
    "full_backup": FULL_MAX_AGE_SECONDS,
    "latest_backup": BACKUP_MAX_AGE_SECONDS,
    "pgbackrest_mirror": MIRROR_MAX_AGE_SECONDS,
    "pgbackrest_inventory": INVENTORY_MAX_AGE_SECONDS,
    "pgbackrest_restore": RESTORE_MAX_AGE_SECONDS,
    "generated_object_mirror": GENERATED_MIRROR_MAX_AGE_SECONDS,
    "generated_object_restore": GENERATED_RESTORE_MAX_AGE_SECONDS,
}

_VALIDATION_ERROR = "Backup-health document is invalid."
_PERSISTENCE_ERROR = "Backup-health evidence could not be retained safely."
_VERIFICATION_ERROR = "Backup-health evidence is not committed or is invalid."
_INPUT_ERROR = "Backup-health input is invalid."
_ABSENT_ERROR = "Backup-health evidence is absent."


class BackupHealthValidationError(ValueError):
    """A document did not match a closed backup-health schema."""


class BackupHealthPersistenceError(RuntimeError):
    """Protected evidence could not be read or written safely."""


class BackupHealthVerificationError(ValueError):
    """Committed evidence failed canonical or digest verification."""


class BackupHealthAbsentError(LookupError):
    """No committed, verified receipt exists for the selected operation."""


@dataclass(frozen=True)
class ReceiptFiles:
    receipt_name: str
    manifest_name: str


@dataclass(frozen=True)
class ReceiptInventory:
    receipts: dict[str, tuple[dict[str, object], ...]]
    invalid_operations: frozenset[str]


def _invalid() -> BackupHealthValidationError:
    return BackupHealthValidationError(_VALIDATION_ERROR)


def _unverified() -> BackupHealthVerificationError:
    return BackupHealthVerificationError(_VERIFICATION_ERROR)


def _persistence() -> BackupHealthPersistenceError:
    return BackupHealthPersistenceError(_PERSISTENCE_ERROR)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise _invalid()
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        raise _invalid() from None


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_integer(
    value: object, *, maximum: int = MAX_INTEGER
) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise _invalid()
    return value


def _validate_label(value: object) -> str:
    if not isinstance(value, str) or _BACKUP_LABEL_PATTERN.fullmatch(value) is None:
        raise _invalid()
    return value


def validate_operation_receipt(document: object) -> dict[str, object]:
    """Return a canonical copy of one operation receipt."""

    if not isinstance(document, dict):
        raise _invalid()
    operation = document.get("operation")
    if not isinstance(operation, str) or operation not in OPERATION_SET:
        raise _invalid()
    expected = _COMMON_RECEIPT_FIELDS | _RECEIPT_FIELDS[operation]
    if set(document) != expected:
        raise _invalid()
    result = document.get("result")
    if (
        document.get("schema") != RECEIPT_SCHEMA
        or not isinstance(result, str)
        or result not in RESULTS
    ):
        raise _invalid()

    started = _parse_timestamp(document.get("started_at"))
    completed = _parse_timestamp(document.get("completed_at"))
    if completed < started or completed - started > MAX_OPERATION_SPAN:
        raise _invalid()

    normalized: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "operation": operation,
        "result": document["result"],
        "started_at": document["started_at"],
        "completed_at": document["completed_at"],
    }
    for field in sorted(_RECEIPT_FIELDS[operation]):
        value = document[field]
        if field in _LABEL_RECEIPT_FIELDS:
            normalized[field] = _validate_label(value)
        elif field in _BOOLEAN_RECEIPT_FIELDS:
            if type(value) is not bool:
                raise _invalid()
            normalized[field] = value
        elif field in _INTEGER_RECEIPT_FIELDS:
            maximum = MAX_INTEGER
            if field == "elapsed_seconds":
                maximum = MAX_ELAPSED_SECONDS
            elif field in {"rpo_seconds", "rto_seconds"}:
                maximum = MAX_RPO_SECONDS
            normalized[field] = _validate_integer(value, maximum=maximum)
        else:  # pragma: no cover - guarded by fixed module constants
            raise _invalid()

    if document["result"] == "ok":
        if operation == "pgbackrest_mirror" and (
            normalized["backup_label"] != normalized["mirrored_backup_label"]
        ):
            raise _invalid()
        if operation == "pgbackrest_inventory" and (
            normalized["divergence_count"] != 0
            or normalized["source_object_count"]
            > normalized["destination_object_count"]
            or normalized["source_byte_count"]
            > normalized["destination_byte_count"]
        ):
            raise _invalid()
        if operation == "generated_object_mirror" and normalized["divergence_count"] != 0:
            raise _invalid()
        if operation == "pgbackrest_restore" and not all(
            normalized[field]
            for field in (
                "promotion_verified",
                "checksum_verified",
                "cleanup_verified",
            )
        ):
            raise _invalid()
        if operation == "generated_object_restore" and not all(
            normalized[field]
            for field in (
                "manifest_verified",
                "ciphertext_verified",
                "target_left_intact",
            )
        ):
            raise _invalid()
    if operation == "full_backup" and (
        _FULL_BACKUP_LABEL_PATTERN.fullmatch(str(normalized["backup_label"]))
        is None
    ):
        raise _invalid()
    if operation == "differential_backup" and (
        _DIFFERENTIAL_BACKUP_LABEL_PATTERN.fullmatch(
            str(normalized["backup_label"])
        )
        is None
    ):
        raise _invalid()
    if operation == "pgbackrest_restore" and (
        normalized["rto_seconds"] > normalized["elapsed_seconds"]
    ):
        raise _invalid()
    return normalized


def serialize_operation_receipt(document: object) -> bytes:
    return _canonical_bytes(validate_operation_receipt(document))


def _canonical_bytes(document: object) -> bytes:
    try:
        return (
            json.dumps(
                document,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise _invalid() from None


def verify_receipt(
    receipt_name: object, receipt_payload: object, manifest_payload: object
) -> dict[str, object]:
    """Verify canonical JSON through its exact adjacent manifest."""

    if (
        not isinstance(receipt_name, str)
        or _RECEIPT_NAME_PATTERN.fullmatch(receipt_name) is None
        or type(receipt_payload) is not bytes
        or type(manifest_payload) is not bytes
        or not 0 < len(receipt_payload) <= MAX_RECEIPT_BYTES
        or not 0 < len(manifest_payload) <= MAX_MANIFEST_BYTES
    ):
        raise _unverified()
    digest = hashlib.sha256(receipt_payload).hexdigest()
    expected = f"{digest}  {receipt_name}\n".encode("ascii")
    if not secrets.compare_digest(manifest_payload, expected):
        raise _unverified()
    try:
        decoded = _strict_json_bytes(receipt_payload, MAX_RECEIPT_BYTES)
        normalized = validate_operation_receipt(decoded)
        canonical = serialize_operation_receipt(normalized)
    except (BackupHealthValidationError, ValueError, UnicodeError):
        raise _unverified() from None
    if not secrets.compare_digest(receipt_payload, canonical):
        raise _unverified()
    return normalized


def _strict_json_bytes(payload: bytes, maximum: int) -> object:
    if not payload or len(payload) > maximum:
        raise _invalid()

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _invalid()
            result[key] = value
        return result

    try:
        return json.loads(
            payload.decode("ascii"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(_invalid()),
        )
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise _invalid() from None


def _is_default_root(root: Path) -> bool:
    return root in {RECEIPT_ROOT, STATUS_ROOT, ALERT_ROOT}


def _expected_owner(root: Path) -> tuple[int, int] | None:
    if os.name != "posix" or not hasattr(os, "geteuid"):
        return None
    effective = (os.geteuid(), os.getegid())
    if _is_default_root(root):
        if effective != (0, 0):
            raise _persistence()
        return (0, 0)
    return effective


def _reject_symlink_components(path: Path, *, final_may_be_missing: bool) -> None:
    if not path.is_absolute():
        raise _persistence()
    current = Path(path.anchor)
    parts = path.parts[1:]
    for index, component in enumerate(parts):
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if final_may_be_missing and index == len(parts) - 1:
                return
            raise _persistence() from None
        except OSError:
            raise _persistence() from None
        if stat.S_ISLNK(metadata.st_mode):
            raise _persistence()


def _verify_root(root: Path, *, create: bool) -> tuple[int, int] | None:
    owner = _expected_owner(root)
    _reject_symlink_components(root, final_may_be_missing=create)
    created = False
    if create:
        try:
            os.mkdir(root, 0o700)
            created = True
        except FileExistsError:
            pass
        except OSError:
            raise _persistence() from None
    if created and os.name == "posix":
        try:
            os.chmod(root, 0o700, follow_symlinks=False)
            if owner is not None:
                os.chown(root, owner[0], owner[1], follow_symlinks=False)
            _fsync_directory(root)
        except OSError:
            raise _persistence() from None
    _reject_symlink_components(root, final_may_be_missing=False)
    try:
        metadata = os.lstat(root)
    except OSError:
        raise _persistence() from None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise _persistence()
    if os.name == "posix":
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise _persistence()
        if owner is not None and (metadata.st_uid, metadata.st_gid) != owner:
            raise _persistence()
    return owner


def _fsync_directory(root: Path) -> None:
    if os.name != "posix":
        return
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(root, flags)
        os.fsync(descriptor)
    except OSError:
        raise _persistence() from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_file_metadata(
    path: Path, owner: tuple[int, int] | None, maximum: int
) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError:
        raise _unverified() from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or not 0 < metadata.st_size <= maximum
    ):
        raise _unverified()
    if os.name == "posix":
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise _unverified()
        if owner is not None and (metadata.st_uid, metadata.st_gid) != owner:
            raise _unverified()
    return metadata


def _read_protected(path: Path, owner: tuple[int, int] | None, maximum: int) -> bytes:
    expected = _verify_file_metadata(path, owner, maximum)
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size != expected.st_size
            or (
                os.name == "posix"
                and (opened.st_dev, opened.st_ino)
                != (expected.st_dev, expected.st_ino)
            )
        ):
            raise OSError()
        remaining = opened.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                raise OSError()
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError()
        return b"".join(chunks)
    except OSError:
        raise _unverified() from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _exclusive_file(path: Path, payload: bytes, owner: tuple[int, int] | None) -> None:
    descriptor = -1
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        created = True
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
            if owner is not None:
                os.fchown(descriptor, owner[0], owner[1])
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError()
            offset += written
        os.fsync(descriptor)
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if created:
            _unlink_quietly(path)
        raise _persistence() from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _link_exclusive(source: Path, target: Path) -> None:
    try:
        os.link(source, target, follow_symlinks=False)
    except (OSError, NotImplementedError):
        raise _persistence() from None


def _unlink_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def read_receipt(
    manifest_name: object, *, root: Path | str = RECEIPT_ROOT
) -> dict[str, object]:
    """Read only a manifest-committed, canonical operation receipt."""

    if not isinstance(manifest_name, str) or _MANIFEST_NAME_PATTERN.fullmatch(manifest_name) is None:
        raise _unverified()
    selected = Path(root)
    owner = _verify_root(selected, create=False)
    receipt_name = f"{manifest_name[:-7]}.json"
    manifest = _read_protected(selected / manifest_name, owner, MAX_MANIFEST_BYTES)
    receipt = _read_protected(selected / receipt_name, owner, MAX_RECEIPT_BYTES)
    return verify_receipt(receipt_name, receipt, manifest)


def _operation_from_manifest(name: str) -> str | None:
    if _MANIFEST_NAME_PATTERN.fullmatch(name) is None:
        return None
    middle = name[17:-24]
    return middle if middle in OPERATION_SET else None


def scan_receipts(*, root: Path | str = RECEIPT_ROOT) -> ReceiptInventory:
    """Return all valid committed receipts and flag invalid commit markers."""

    selected = Path(root)
    _verify_root(selected, create=False)
    receipts: dict[str, list[dict[str, object]]] = {item: [] for item in OPERATIONS}
    invalid: set[str] = set()
    try:
        names = sorted(item.name for item in selected.iterdir())
    except OSError:
        raise _persistence() from None
    for name in names:
        operation = _operation_from_manifest(name)
        if operation is None:
            continue
        try:
            document = read_receipt(name, root=selected)
        except (BackupHealthPersistenceError, BackupHealthVerificationError):
            invalid.add(operation)
            continue
        if document["operation"] != operation:
            invalid.add(operation)
            continue
        receipts[operation].append(document)
    for operation in OPERATIONS:
        receipts[operation].sort(
            key=lambda item: (str(item["completed_at"]), str(item["started_at"]))
        )
    return ReceiptInventory(
        receipts={key: tuple(value) for key, value in receipts.items()},
        invalid_operations=frozenset(invalid),
    )


def latest_verified_receipt(
    operation: str,
    *,
    root: Path | str = RECEIPT_ROOT,
    require_ok: bool = True,
) -> dict[str, object]:
    """Return the latest canonical receipt without exposing raw files.

    By default, "latest" means the latest accepted (result=ok) receipt.  A
    committed invalid marker for the selected operation fails the read closed.
    """

    if (
        not isinstance(operation, str)
        or operation not in OPERATION_SET
        or type(require_ok) is not bool
    ):
        raise _invalid()
    inventory = scan_receipts(root=root)
    if operation in inventory.invalid_operations:
        raise _unverified()
    selected = _latest(inventory.receipts[operation], successful=require_ok)
    if selected is None:
        raise BackupHealthAbsentError(_ABSENT_ERROR)
    return dict(selected)


def _prune_receipts(root: Path) -> None:
    for operation in OPERATIONS:
        valid_names: list[tuple[str, str]] = []
        for item in sorted(root.iterdir(), key=lambda value: value.name):
            name = item.name
            if _operation_from_manifest(name) != operation:
                continue
            try:
                document = read_receipt(name, root=root)
            except (BackupHealthPersistenceError, BackupHealthVerificationError):
                continue
            valid_names.append((str(document["completed_at"]), name))
        valid_names.sort()
        excess = len(valid_names) - MAX_RECEIPTS_PER_OPERATION
        for _completed, manifest_name in valid_names[: max(0, excess)]:
            receipt_name = f"{manifest_name[:-7]}.json"
            # Removing the commit marker first makes an interrupted prune fail
            # closed.  Invalid or unpaired files are never selected above.
            try:
                os.unlink(root / manifest_name)
                _fsync_directory(root)
                os.unlink(root / receipt_name)
                _fsync_directory(root)
            except OSError:
                raise _persistence() from None


def publish_receipt(
    document: object,
    *,
    root: Path | str = RECEIPT_ROOT,
    now: datetime | None = None,
) -> ReceiptFiles:
    """Publish immutable JSON, then its adjacent manifest commit marker."""

    payload = serialize_operation_receipt(document)
    normalized = validate_operation_receipt(document)
    selected = Path(root)
    owner = _verify_root(selected, create=True)
    publication_time = now or datetime.now(timezone.utc)
    if publication_time.tzinfo is None:
        raise _invalid()
    prefix = publication_time.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(payload).hexdigest()

    for _ in range(MAX_WRITE_ATTEMPTS):
        stem = f"{prefix}-{normalized['operation']}-{secrets.token_hex(8)}"
        receipt_name = f"{stem}.json"
        manifest_name = f"{stem}.sha256"
        receipt_stage = selected / f".{stem}.receipt.pending"
        manifest_stage = selected / f".{stem}.manifest.pending"
        receipt_path = selected / receipt_name
        manifest_path = selected / manifest_name
        manifest_payload = f"{digest}  {receipt_name}\n".encode("ascii")
        receipt_linked = False
        manifest_linked = False
        try:
            _exclusive_file(receipt_stage, payload, owner)
            _link_exclusive(receipt_stage, receipt_path)
            receipt_linked = True
            _unlink_quietly(receipt_stage)
            _fsync_directory(selected)
            _exclusive_file(manifest_stage, manifest_payload, owner)
            _link_exclusive(manifest_stage, manifest_path)
            manifest_linked = True
            _unlink_quietly(manifest_stage)
            _fsync_directory(selected)
            files = ReceiptFiles(receipt_name, manifest_name)
            _prune_receipts(selected)
            return files
        except BackupHealthPersistenceError:
            _unlink_quietly(receipt_stage)
            _unlink_quietly(manifest_stage)
            # A final manifest means the publish committed; never remove or
            # retry that ambiguous mutation.
            if manifest_linked or manifest_path.exists():
                raise
            if receipt_linked:
                _unlink_quietly(receipt_path)
                _fsync_directory(selected)
    raise _persistence()


def validate_snapshot(document: object) -> dict[str, object]:
    if not isinstance(document, dict):
        raise _invalid()
    proposed_surface = document.get("surface")
    if not isinstance(proposed_surface, str) or proposed_surface not in SURFACES:
        raise _invalid()
    surface = proposed_surface
    required = {"schema", "surface", "observed_at", "units"}
    if surface == "database":
        required |= {"latest_backup_label", "wal"}
    if set(document) != required:
        raise _invalid()
    if document.get("schema") != SNAPSHOT_SCHEMA:
        raise _invalid()
    _parse_timestamp(document.get("observed_at"))
    units = document.get("units")
    applicable_operations = SURFACE_OPERATIONS[surface]
    if not isinstance(units, dict) or set(units) != set(applicable_operations):
        raise _invalid()
    normalized_units: dict[str, dict[str, object]] = {}
    for operation in applicable_operations:
        unit = units[operation]
        if not isinstance(unit, dict) or set(unit) != {"last_result", "missed"}:
            raise _invalid()
        if (
            not isinstance(unit["last_result"], str)
            or unit["last_result"] not in UNIT_RESULTS
            or type(unit["missed"]) is not bool
        ):
            raise _invalid()
        normalized_units[operation] = {
            "last_result": unit["last_result"],
            "missed": unit["missed"],
        }
    normalized: dict[str, object] = {
        "schema": SNAPSHOT_SCHEMA,
        "surface": surface,
        "observed_at": document["observed_at"],
        "units": normalized_units,
    }
    if surface == "database":
        normalized["latest_backup_label"] = _validate_label(
            document.get("latest_backup_label")
        )
        wal = document.get("wal")
        wal_fields = {
            "failed_count",
            "previous_failed_count",
            "segment_lag",
            "repository_archive_max_behind_database",
        }
        if not isinstance(wal, dict) or set(wal) != wal_fields:
            raise _invalid()
        normalized_wal = {
            "failed_count": _validate_integer(wal["failed_count"]),
            "previous_failed_count": _validate_integer(wal["previous_failed_count"]),
            "segment_lag": _validate_integer(wal["segment_lag"]),
            "repository_archive_max_behind_database": wal[
                "repository_archive_max_behind_database"
            ],
        }
        if type(normalized_wal["repository_archive_max_behind_database"]) is not bool:
            raise _invalid()
        normalized["wal"] = normalized_wal
    return normalized


def _latest(
    documents: Iterable[dict[str, object]], *, successful: bool = False
) -> dict[str, object] | None:
    selected = [
        item for item in documents if not successful or item["result"] == "ok"
    ]
    if not selected:
        return None
    # A wrapper can retain a committed success and then publish a failure when
    # later evidence persistence is ambiguous.  Whole-second timestamps may be
    # identical, so failure must win that tie and canonical bytes provide a
    # deterministic order for otherwise equivalent terminal evidence.
    return max(
        selected,
        key=lambda item: (
            str(item["completed_at"]),
            str(item["started_at"]),
            item["result"] == "failed",
            serialize_operation_receipt(item),
        ),
    )


def _age(
    document: dict[str, object] | None,
    observed: datetime,
    operation: str,
    codes: set[str],
) -> int | None:
    if document is None:
        return None
    completed = _parse_timestamp(document["completed_at"])
    difference = int((observed - completed).total_seconds())
    if difference < -MAX_FUTURE_SKEW_SECONDS:
        codes.add(f"{operation}_receipt_future")
        return None
    return max(0, difference)


def evaluate_backup_health(
    snapshot: object, *, receipt_root: Path | str = RECEIPT_ROOT
) -> dict[str, object]:
    """Evaluate the strict snapshot against all latest verified receipts."""

    normalized = validate_snapshot(snapshot)
    inventory = scan_receipts(root=receipt_root)
    observed = _parse_timestamp(normalized["observed_at"])
    surface = str(normalized["surface"])
    operations = SURFACE_OPERATIONS[surface]
    codes: set[str] = set()

    for operation in inventory.invalid_operations.intersection(operations):
        codes.add(f"{operation}_receipt_invalid")
    for operation in operations:
        unit = normalized["units"][operation]
        if unit["last_result"] == "failed":
            codes.add(f"{operation}_unit_failed")
        elif unit["last_result"] == "never":
            codes.add(f"{operation}_unit_never")
        if unit["missed"]:
            codes.add(f"{operation}_unit_missed")
        latest = _latest(inventory.receipts[operation])
        if latest is not None and latest["result"] == "failed":
            codes.add(f"{operation}_failed")

    ages: dict[str, int | None]
    if surface == "database":
        full = _latest(inventory.receipts["full_backup"], successful=True)
        differential = _latest(
            inventory.receipts["differential_backup"], successful=True
        )
        latest_backup = _latest(
            tuple(item for item in (full, differential) if item is not None)
        )
        mirror = _latest(
            inventory.receipts["pgbackrest_mirror"], successful=True
        )
        pg_inventory = _latest(
            inventory.receipts["pgbackrest_inventory"], successful=True
        )
        pg_restore = _latest(
            inventory.receipts["pgbackrest_restore"], successful=True
        )
        latest_inventory = _latest(inventory.receipts["pgbackrest_inventory"])
        latest_restore = _latest(inventory.receipts["pgbackrest_restore"])
        ages = {
            "full_backup": _age(full, observed, "full_backup", codes),
            "latest_backup": _age(
                latest_backup,
                observed,
                str(latest_backup["operation"]) if latest_backup else "full_backup",
                codes,
            ),
            "pgbackrest_mirror": _age(
                mirror, observed, "pgbackrest_mirror", codes
            ),
            "pgbackrest_inventory": _age(
                pg_inventory, observed, "pgbackrest_inventory", codes
            ),
            "pgbackrest_restore": _age(
                pg_restore, observed, "pgbackrest_restore", codes
            ),
        }

        if full is None:
            codes.add("full_backup_receipt_missing")
        elif ages["full_backup"] is not None and ages["full_backup"] > FULL_MAX_AGE_SECONDS:
            codes.add("full_backup_stale")
        if latest_backup is None:
            codes.add("latest_backup_receipt_missing")
        else:
            if ages["latest_backup"] is not None and ages["latest_backup"] > BACKUP_MAX_AGE_SECONDS:
                codes.add("latest_backup_stale")
            if latest_backup["backup_label"] != normalized["latest_backup_label"]:
                codes.add("latest_backup_label_mismatch")

        wal = normalized["wal"]
        if wal["failed_count"] > wal["previous_failed_count"]:
            codes.add("wal_failed_count_growth")
        if wal["segment_lag"] > 1:
            codes.add("wal_segment_lag_exceeded")
        if wal["repository_archive_max_behind_database"]:
            codes.add("wal_repository_archive_behind")

        if mirror is None:
            codes.add("pgbackrest_mirror_receipt_missing")
        else:
            if ages["pgbackrest_mirror"] is not None and ages["pgbackrest_mirror"] > MIRROR_MAX_AGE_SECONDS:
                codes.add("pgbackrest_mirror_stale")
            if mirror["mirrored_backup_label"] != normalized["latest_backup_label"]:
                codes.add("pgbackrest_mirror_label_mismatch")

        if pg_inventory is None:
            codes.add("pgbackrest_inventory_receipt_missing")
        elif ages["pgbackrest_inventory"] is not None and ages["pgbackrest_inventory"] > INVENTORY_MAX_AGE_SECONDS:
            codes.add("pgbackrest_inventory_stale")
        if latest_inventory is not None and latest_inventory["divergence_count"] != 0:
            codes.add("pgbackrest_inventory_divergence")

        if pg_restore is None:
            codes.add("pgbackrest_restore_receipt_missing")
        elif ages["pgbackrest_restore"] is not None and ages["pgbackrest_restore"] > RESTORE_MAX_AGE_SECONDS:
            codes.add("pgbackrest_restore_stale")
        if latest_restore is not None:
            if not latest_restore["promotion_verified"]:
                codes.add("pgbackrest_restore_promotion_failed")
            if not latest_restore["checksum_verified"]:
                codes.add("pgbackrest_restore_checksum_failed")
            if not latest_restore["cleanup_verified"]:
                codes.add("pgbackrest_restore_cleanup_failed")
    else:
        generated_mirror = _latest(
            inventory.receipts["generated_object_mirror"], successful=True
        )
        generated_restore = _latest(
            inventory.receipts["generated_object_restore"], successful=True
        )
        latest_generated_mirror = _latest(
            inventory.receipts["generated_object_mirror"]
        )
        latest_generated_restore = _latest(
            inventory.receipts["generated_object_restore"]
        )
        ages = {
            "generated_object_mirror": _age(
                generated_mirror, observed, "generated_object_mirror", codes
            ),
            "generated_object_restore": _age(
                generated_restore, observed, "generated_object_restore", codes
            ),
        }
        if generated_mirror is None:
            codes.add("generated_object_mirror_receipt_missing")
        elif ages["generated_object_mirror"] is not None and ages["generated_object_mirror"] > GENERATED_MIRROR_MAX_AGE_SECONDS:
            codes.add("generated_object_mirror_stale")
        if (
            latest_generated_mirror is not None
            and latest_generated_mirror["divergence_count"] != 0
        ):
            codes.add("generated_object_mirror_divergence")

        if generated_restore is None:
            codes.add("generated_object_restore_receipt_missing")
        elif ages["generated_object_restore"] is not None and ages["generated_object_restore"] > GENERATED_RESTORE_MAX_AGE_SECONDS:
            codes.add("generated_object_restore_stale")
        if latest_generated_restore is not None:
            if not latest_generated_restore["manifest_verified"]:
                codes.add("generated_object_restore_manifest_failed")
            if not latest_generated_restore["ciphertext_verified"]:
                codes.add("generated_object_restore_ciphertext_failed")
            if not latest_generated_restore["target_left_intact"]:
                codes.add("generated_object_restore_target_not_retained")

    if not codes.issubset(FAILURE_CODES):  # pragma: no cover - invariant guard
        raise _invalid()
    ordered_codes = sorted(codes)
    return {
        "schema": STATUS_SCHEMA,
        "surface": surface,
        "evaluated_at": normalized["observed_at"],
        "result": "alert" if ordered_codes else "ok",
        "failure_codes": ordered_codes,
        "failure_count": len(ordered_codes),
        "ages_seconds": {
            key: ages[key] for key in AGE_KEYS_BY_SURFACE[surface]
        },
    }


def validate_status(document: object) -> dict[str, object]:
    expected = {
        "schema",
        "surface",
        "evaluated_at",
        "result",
        "failure_codes",
        "failure_count",
        "ages_seconds",
    }
    if not isinstance(document, dict) or set(document) != expected:
        raise _invalid()
    surface = document.get("surface")
    result = document.get("result")
    if (
        document.get("schema") != STATUS_SCHEMA
        or not isinstance(surface, str)
        or surface not in SURFACES
        or not isinstance(result, str)
        or result not in {"ok", "alert"}
    ):
        raise _invalid()
    _parse_timestamp(document.get("evaluated_at"))
    codes = document.get("failure_codes")
    if (
        not isinstance(codes, list)
        or any(
            not isinstance(item, str)
            or item not in FAILURE_CODES_BY_SURFACE[surface]
            for item in codes
        )
        or len(codes) != len(set(codes))
        or codes != sorted(codes)
    ):
        raise _invalid()
    count = _validate_integer(document.get("failure_count"), maximum=len(FAILURE_CODES))
    if count != len(codes) or (document["result"] == "ok") != (count == 0):
        raise _invalid()
    ages = document.get("ages_seconds")
    expected_age_keys = AGE_KEYS_BY_SURFACE[surface]
    if not isinstance(ages, dict) or set(ages) != set(expected_age_keys):
        raise _invalid()
    normalized_ages: dict[str, int | None] = {}
    for key in expected_age_keys:
        value = ages[key]
        if value is None:
            normalized_ages[key] = None
        else:
            normalized_ages[key] = _validate_integer(value)
    if document["result"] == "ok" and any(
        normalized_ages[key] is None
        or int(normalized_ages[key]) > STATUS_AGE_LIMITS[key]
        for key in expected_age_keys
    ):
        raise _invalid()
    return {
        "schema": STATUS_SCHEMA,
        "surface": surface,
        "evaluated_at": document["evaluated_at"],
        "result": document["result"],
        "failure_codes": list(codes),
        "failure_count": count,
        "ages_seconds": normalized_ages,
    }


def _atomic_write(
    root: Path,
    name: str,
    payload: bytes,
    *,
    maximum: int,
) -> None:
    if not payload or len(payload) > maximum:
        raise _persistence()
    owner = _verify_root(root, create=True)
    for _ in range(MAX_WRITE_ATTEMPTS):
        temporary = root / f".{name}.{secrets.token_hex(8)}.pending"
        try:
            _exclusive_file(temporary, payload, owner)
            os.replace(temporary, root / name)
            _fsync_directory(root)
            return
        except BackupHealthPersistenceError:
            _unlink_quietly(temporary)
        except OSError:
            _unlink_quietly(temporary)
            raise _persistence() from None
    raise _persistence()


def write_status(
    document: object, *, root: Path | str = STATUS_ROOT
) -> dict[str, object]:
    normalized = validate_status(document)
    name = STATUS_NAMES[str(normalized["surface"])]
    _atomic_write(Path(root), name, _canonical_bytes(normalized), maximum=MAX_STATUS_BYTES)
    return normalized


def read_status(
    *, root: Path | str = STATUS_ROOT, expected_surface: str
) -> dict[str, object]:
    if not isinstance(expected_surface, str) or expected_surface not in SURFACES:
        raise _invalid()
    selected = Path(root)
    owner = _verify_root(selected, create=False)
    payload = _read_protected(
        selected / STATUS_NAMES[expected_surface], owner, MAX_STATUS_BYTES
    )
    try:
        normalized = validate_status(_strict_json_bytes(payload, MAX_STATUS_BYTES))
    except BackupHealthValidationError:
        raise _unverified() from None
    if not secrets.compare_digest(payload, _canonical_bytes(normalized)):
        raise _unverified()
    if normalized["surface"] != expected_surface:
        raise _unverified()
    return normalized


def checked_status(
    *,
    root: Path | str = STATUS_ROOT,
    surface: str,
    now: datetime | None = None,
) -> dict[str, object]:
    """Read status and synthesize a fixed alert when it is stale or future."""

    status_document = read_status(root=root, expected_surface=surface)
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        raise _invalid()
    evaluated_at = _parse_timestamp(status_document["evaluated_at"])
    age = (
        checked_at.astimezone(timezone.utc) - evaluated_at
    ).total_seconds()
    if age > MAX_STATUS_AGE_SECONDS or age < -MAX_FUTURE_SKEW_SECONDS:
        codes = sorted(
            set(status_document["failure_codes"]) | {"health_status_stale"}
        )
        status_document = {
            **status_document,
            "result": "alert",
            "failure_codes": codes,
            "failure_count": len(codes),
        }
    return validate_status(status_document)


def validate_alert_state(document: object) -> dict[str, object]:
    expected = {
        "schema",
        "surface",
        "state",
        "first_detected_at",
        "last_evaluated_at",
        "consecutive_alert_evaluations",
        "failure_codes",
    }
    if not isinstance(document, dict) or set(document) != expected:
        raise _invalid()
    surface = document.get("surface")
    state_value = document.get("state")
    if (
        document.get("schema") != ALERT_SCHEMA
        or not isinstance(surface, str)
        or surface not in SURFACES
        or not isinstance(state_value, str)
        or state_value not in {"clear", "firing"}
    ):
        raise _invalid()
    last = _parse_timestamp(document.get("last_evaluated_at"))
    first_value = document.get("first_detected_at")
    first = None if first_value is None else _parse_timestamp(first_value)
    codes = document.get("failure_codes")
    if (
        not isinstance(codes, list)
        or any(
            not isinstance(item, str)
            or item not in FAILURE_CODES_BY_SURFACE[surface]
            for item in codes
        )
        or codes != sorted(codes)
        or len(codes) != len(set(codes))
    ):
        raise _invalid()
    consecutive = _validate_integer(
        document.get("consecutive_alert_evaluations"), maximum=MAX_INTEGER
    )
    if document["state"] == "clear":
        if first is not None or codes or consecutive != 0:
            raise _invalid()
    else:
        if first is None or first > last or not codes or consecutive < 1:
            raise _invalid()
    return {
        "schema": ALERT_SCHEMA,
        "surface": surface,
        "state": document["state"],
        "first_detected_at": first_value,
        "last_evaluated_at": document["last_evaluated_at"],
        "consecutive_alert_evaluations": consecutive,
        "failure_codes": list(codes),
    }


def read_alert_state(
    *, root: Path | str = ALERT_ROOT, expected_surface: str
) -> dict[str, object]:
    if not isinstance(expected_surface, str) or expected_surface not in SURFACES:
        raise _invalid()
    selected = Path(root)
    owner = _verify_root(selected, create=False)
    payload = _read_protected(
        selected / ALERT_NAMES[expected_surface], owner, MAX_ALERT_BYTES
    )
    try:
        normalized = validate_alert_state(_strict_json_bytes(payload, MAX_ALERT_BYTES))
    except BackupHealthValidationError:
        raise _unverified() from None
    if not secrets.compare_digest(payload, _canonical_bytes(normalized)):
        raise _unverified()
    if normalized["surface"] != expected_surface:
        raise _unverified()
    return normalized


def persist_alert_state(
    status: object,
    *,
    root: Path | str = ALERT_ROOT,
    checked_at: datetime | None = None,
) -> dict[str, object]:
    normalized = validate_status(status)
    selected = Path(root)
    alert_checked_at = checked_at or datetime.now(timezone.utc)
    if alert_checked_at.tzinfo is None:
        raise _invalid()
    alert_checked_at = alert_checked_at.astimezone(timezone.utc).replace(
        microsecond=0
    )
    alert_checked_text = _timestamp(alert_checked_at)
    previous: dict[str, object] | None = None
    try:
        previous = read_alert_state(
            root=selected, expected_surface=str(normalized["surface"])
        )
    except BackupHealthVerificationError:
        alert_path = selected / ALERT_NAMES[str(normalized["surface"])]
        if alert_path.exists() or alert_path.is_symlink():
            raise
    except BackupHealthPersistenceError:
        if selected.exists():
            raise

    if previous is not None:
        prior_time = _parse_timestamp(previous["last_evaluated_at"])
        if alert_checked_at < prior_time:
            raise _invalid()
        if (
            alert_checked_at == prior_time
            and normalized["failure_codes"] == previous["failure_codes"]
            and ((normalized["result"] == "alert") == (previous["state"] == "firing"))
        ):
            return previous

    if normalized["result"] == "ok":
        state = {
            "schema": ALERT_SCHEMA,
            "surface": normalized["surface"],
            "state": "clear",
            "first_detected_at": None,
            "last_evaluated_at": alert_checked_text,
            "consecutive_alert_evaluations": 0,
            "failure_codes": [],
        }
    else:
        continuing = previous is not None and previous["state"] == "firing"
        first_detected = (
            previous["first_detected_at"] if continuing else alert_checked_text
        )
        consecutive = (
            min(MAX_INTEGER, int(previous["consecutive_alert_evaluations"]) + 1)
            if continuing
            else 1
        )
        state = {
            "schema": ALERT_SCHEMA,
            "surface": normalized["surface"],
            "state": "firing",
            "first_detected_at": first_detected,
            "last_evaluated_at": alert_checked_text,
            "consecutive_alert_evaluations": consecutive,
            "failure_codes": list(normalized["failure_codes"]),
        }
    state = validate_alert_state(state)
    _atomic_write(
        selected,
        ALERT_NAMES[str(normalized["surface"])],
        _canonical_bytes(state),
        maximum=MAX_ALERT_BYTES,
    )
    return state


def _read_stdin_document() -> object:
    payload = sys.stdin.buffer.read(MAX_DOCUMENT_BYTES + 1)
    if not payload or len(payload) > MAX_DOCUMENT_BYTES:
        raise BackupHealthValidationError(_INPUT_ERROR)
    try:
        return _strict_json_bytes(payload, MAX_DOCUMENT_BYTES)
    except BackupHealthValidationError:
        raise BackupHealthValidationError(_INPUT_ERROR) from None


def _emit(document: object) -> None:
    sys.stdout.buffer.write(_canonical_bytes(document))


class _SafeArgumentParser(argparse.ArgumentParser):
    """Argument parser whose errors never repeat caller-controlled text."""

    def error(self, _message: str) -> None:
        self.exit(2, f"{_INPUT_ERROR}\n")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        description="CareerTrellis backup-health evidence"
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=_SafeArgumentParser
    )

    publish = subparsers.add_parser("publish")
    publish.add_argument("--receipt-root", type=Path, default=RECEIPT_ROOT)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--receipt-root", type=Path, default=RECEIPT_ROOT)
    evaluate.add_argument("--status-root", type=Path, default=STATUS_ROOT)

    status = subparsers.add_parser("status")
    status.add_argument("--status-root", type=Path, default=STATUS_ROOT)
    status.add_argument("--surface", choices=sorted(SURFACES), required=True)

    alert = subparsers.add_parser("alert")
    alert.add_argument("--status-root", type=Path, default=STATUS_ROOT)
    alert.add_argument("--alert-root", type=Path, default=ALERT_ROOT)
    alert.add_argument("--surface", choices=sorted(SURFACES), required=True)

    latest = subparsers.add_parser("latest")
    latest.add_argument("--receipt-root", type=Path, default=RECEIPT_ROOT)
    latest.add_argument("--operation", choices=OPERATIONS, required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "publish":
            document = validate_operation_receipt(_read_stdin_document())
            files = publish_receipt(document, root=args.receipt_root)
            _emit(
                {
                    "schema": PUBLISH_RESULT_SCHEMA,
                    "operation": document["operation"],
                    "result": document["result"],
                    "manifest_name": files.manifest_name,
                }
            )
            return 0
        if args.command == "evaluate":
            snapshot = validate_snapshot(_read_stdin_document())
            status = evaluate_backup_health(snapshot, receipt_root=args.receipt_root)
            write_status(status, root=args.status_root)
            _emit(status)
            return 1 if status["result"] == "alert" else 0
        if args.command == "status":
            status = checked_status(
                root=args.status_root, surface=args.surface
            )
            _emit(status)
            return 1 if status["result"] == "alert" else 0
        if args.command == "alert":
            alert_checked_at = datetime.now(timezone.utc)
            status = checked_status(
                root=args.status_root,
                surface=args.surface,
                now=alert_checked_at,
            )
            state = persist_alert_state(
                status,
                root=args.alert_root,
                checked_at=alert_checked_at,
            )
            _emit(state)
            return 1 if state["state"] == "firing" else 0
        if args.command == "latest":
            _emit(
                latest_verified_receipt(
                    args.operation, root=args.receipt_root, require_ok=True
                )
            )
            return 0
        raise BackupHealthValidationError(_INPUT_ERROR)  # pragma: no cover
    except BackupHealthAbsentError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (
        BackupHealthValidationError,
        BackupHealthPersistenceError,
        BackupHealthVerificationError,
    ) as error:
        message = str(error)
        if message not in {
            _VALIDATION_ERROR,
            _PERSISTENCE_ERROR,
            _VERIFICATION_ERROR,
            _INPUT_ERROR,
            _ABSENT_ERROR,
        }:
            message = _INPUT_ERROR
        print(message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
