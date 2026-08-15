"""Build and verify bounded manifests for encrypted generated-object snapshots.

The manifest intentionally contains opaque object keys and ciphertext digests so
an isolated recovery copy can be verified without an object-encryption keyring.
CLI receipts never expose those per-object values or local paths.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Sequence


MANIFEST_FORMAT = "careertrellis-generated-object-recovery"
MANIFEST_FORMAT_VERSION = 1
MANIFEST_ERROR_MESSAGE = "Generated-object recovery manifest failed."
MAXIMUM_OBJECTS = 100_000
MAXIMUM_CIPHERTEXT_BYTES = 10 * 1024 * 1024 + 64
MAXIMUM_TOTAL_CIPHERTEXT_BYTES = MAXIMUM_OBJECTS * MAXIMUM_CIPHERTEXT_BYTES
MAXIMUM_MANIFEST_BYTES = 32 * 1024 * 1024

OBJECT_KEY_PATTERN = re.compile(r"^v1/[0-9a-f]{32}$")
OBJECT_FILENAME_PATTERN = re.compile(r"^[0-9a-f]{32}$")
RELEASE_PATTERN = re.compile(r"^[0-9a-f]{40}$")
BACKUP_LABEL_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}F$")
SNAPSHOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class GeneratedObjectRecoveryError(RuntimeError):
    """A snapshot inventory or manifest failed closed."""


def _failure() -> GeneratedObjectRecoveryError:
    return GeneratedObjectRecoveryError(MANIFEST_ERROR_MESSAGE)


@dataclass(frozen=True)
class CiphertextManifestEntry:
    object_key: str = field(repr=False)
    size_bytes: int
    sha256: str = field(repr=False)

    def document(self) -> dict[str, Any]:
        return {
            "object_key": self.object_key,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class GeneratedObjectManifest:
    release: str
    backup_label: str
    snapshot_id: str
    object_count: int
    ciphertext_bytes: int
    objects: tuple[CiphertextManifestEntry, ...] = field(repr=False)

    def document(self) -> dict[str, Any]:
        return {
            "backup_label": self.backup_label,
            "ciphertext_bytes": self.ciphertext_bytes,
            "format": MANIFEST_FORMAT,
            "format_version": MANIFEST_FORMAT_VERSION,
            "object_count": self.object_count,
            "objects": [entry.document() for entry in self.objects],
            "release": self.release,
            "snapshot_id": self.snapshot_id,
        }

    def serialized(self) -> bytes:
        return json.dumps(
            self.document(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")

    def digest(self) -> str:
        return hashlib.sha256(self.serialized()).hexdigest()

    def sanitized_summary(self) -> dict[str, Any]:
        return {
            "backup_label": self.backup_label,
            "ciphertext_bytes": self.ciphertext_bytes,
            "manifest_sha256": self.digest(),
            "manifest_verified": True,
            "object_count": self.object_count,
            "release": self.release,
            "result": "pass",
            "snapshot_id": self.snapshot_id,
        }


def _bounded_integer(value: object, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise _failure()
    return value


def _validate_bounds(maximum_objects: int, maximum_bytes: int) -> tuple[int, int]:
    return (
        _bounded_integer(maximum_objects, 1, MAXIMUM_OBJECTS),
        _bounded_integer(maximum_bytes, 1, MAXIMUM_TOTAL_CIPHERTEXT_BYTES),
    )


def _validate_identity(release: str, backup_label: str, snapshot_id: str) -> None:
    if (
        not isinstance(release, str)
        or RELEASE_PATTERN.fullmatch(release) is None
        or not isinstance(backup_label, str)
        or BACKUP_LABEL_PATTERN.fullmatch(backup_label) is None
        or not isinstance(snapshot_id, str)
        or SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id) is None
    ):
        raise _failure()


def _regular_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _failure() from exc
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise _failure()


def _regular_file_metadata(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _failure() from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise _failure()
    return metadata


def _sha256_file(path: Path, expected_metadata: os.stat_result) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            opened_metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or opened_metadata.st_dev != expected_metadata.st_dev
                or opened_metadata.st_ino != expected_metadata.st_ino
                or opened_metadata.st_size != expected_metadata.st_size
            ):
                raise _failure()
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
            final_metadata = os.fstat(source.fileno())
        current_metadata = path.lstat()
    except GeneratedObjectRecoveryError:
        raise
    except OSError as exc:
        raise _failure() from exc
    for actual in (final_metadata, current_metadata):
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_dev != opened_metadata.st_dev
            or actual.st_ino != opened_metadata.st_ino
            or actual.st_size != opened_metadata.st_size
            or actual.st_mtime_ns != opened_metadata.st_mtime_ns
        ):
            raise _failure()
    return digest.hexdigest()


def _read_bounded_regular_file(path: Path, maximum_bytes: int) -> bytes:
    expected_metadata = _regular_file_metadata(path)
    if not 1 <= expected_metadata.st_size <= maximum_bytes:
        raise _failure()
    try:
        with path.open("rb") as source:
            opened_metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or opened_metadata.st_dev != expected_metadata.st_dev
                or opened_metadata.st_ino != expected_metadata.st_ino
                or opened_metadata.st_size != expected_metadata.st_size
            ):
                raise _failure()
            serialized = source.read(maximum_bytes + 1)
            final_metadata = os.fstat(source.fileno())
        current_metadata = path.lstat()
    except GeneratedObjectRecoveryError:
        raise
    except OSError as exc:
        raise _failure() from exc
    if len(serialized) != opened_metadata.st_size:
        raise _failure()
    for actual in (final_metadata, current_metadata):
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_dev != opened_metadata.st_dev
            or actual.st_ino != opened_metadata.st_ino
            or actual.st_size != opened_metadata.st_size
            or actual.st_mtime_ns != opened_metadata.st_mtime_ns
        ):
            raise _failure()
    return serialized


def _inventory(
    root: Path,
    *,
    maximum_objects: int,
    maximum_bytes: int,
) -> tuple[CiphertextManifestEntry, ...]:
    maximum_objects, maximum_bytes = _validate_bounds(maximum_objects, maximum_bytes)
    root = Path(root)
    _regular_directory(root)
    try:
        root_entries = list(os.scandir(root))
    except OSError as exc:
        raise _failure() from exc
    if any(entry.name != "v1" for entry in root_entries):
        raise _failure()
    if not root_entries:
        return ()

    version_directory = root / "v1"
    _regular_directory(version_directory)
    try:
        candidates = list(os.scandir(version_directory))
    except OSError as exc:
        raise _failure() from exc
    if len(candidates) > maximum_objects:
        raise _failure()

    entries: list[CiphertextManifestEntry] = []
    total_bytes = 0
    seen_keys: set[str] = set()
    for candidate in sorted(candidates, key=lambda item: item.name):
        if OBJECT_FILENAME_PATTERN.fullmatch(candidate.name) is None:
            raise _failure()
        object_key = f"v1/{candidate.name}"
        if object_key in seen_keys:
            raise _failure()
        seen_keys.add(object_key)
        path = version_directory / candidate.name
        metadata = _regular_file_metadata(path)
        size_bytes = _bounded_integer(
            metadata.st_size,
            1,
            MAXIMUM_CIPHERTEXT_BYTES,
        )
        total_bytes += size_bytes
        if total_bytes > maximum_bytes:
            raise _failure()
        entries.append(
            CiphertextManifestEntry(
                object_key=object_key,
                size_bytes=size_bytes,
                sha256=_sha256_file(path, metadata),
            )
        )
    return tuple(entries)


def _new_manifest(
    root: Path,
    release: str,
    backup_label: str,
    snapshot_id: str,
    *,
    maximum_objects: int,
    maximum_bytes: int,
) -> GeneratedObjectManifest:
    _validate_identity(release, backup_label, snapshot_id)
    objects = _inventory(
        root,
        maximum_objects=maximum_objects,
        maximum_bytes=maximum_bytes,
    )
    return GeneratedObjectManifest(
        release=release,
        backup_label=backup_label,
        snapshot_id=snapshot_id,
        object_count=len(objects),
        ciphertext_bytes=sum(entry.size_bytes for entry in objects),
        objects=objects,
    )


def create_manifest(
    source_root: Path,
    release: str,
    backup_label: str,
    snapshot_id: str,
    manifest_path: Path,
    *,
    maximum_objects: int = MAXIMUM_OBJECTS,
    maximum_bytes: int = MAXIMUM_TOTAL_CIPHERTEXT_BYTES,
) -> GeneratedObjectManifest:
    """Create one exclusive, canonical manifest for a local ciphertext tree."""
    try:
        manifest = _new_manifest(
            Path(source_root),
            release,
            backup_label,
            snapshot_id,
            maximum_objects=maximum_objects,
            maximum_bytes=maximum_bytes,
        )
        serialized = manifest.serialized()
        if len(serialized) > MAXIMUM_MANIFEST_BYTES:
            raise _failure()
        target = Path(manifest_path)
        _regular_directory(target.parent)
        created = False
        try:
            with target.open("xb") as destination:
                created = True
                destination.write(serialized)
                destination.flush()
                os.fsync(destination.fileno())
        except BaseException:
            if created:
                try:
                    target.unlink()
                except OSError:
                    pass
            raise
        return manifest
    except GeneratedObjectRecoveryError:
        raise
    except Exception as exc:
        raise _failure() from exc


def _manifest_from_document(document: object) -> GeneratedObjectManifest:
    expected_fields = {
        "backup_label",
        "ciphertext_bytes",
        "format",
        "format_version",
        "object_count",
        "objects",
        "release",
        "snapshot_id",
    }
    if not isinstance(document, dict) or set(document) != expected_fields:
        raise _failure()
    if (
        document["format"] != MANIFEST_FORMAT
        or document["format_version"] != MANIFEST_FORMAT_VERSION
    ):
        raise _failure()
    release = document["release"]
    backup_label = document["backup_label"]
    snapshot_id = document["snapshot_id"]
    _validate_identity(release, backup_label, snapshot_id)
    object_count = _bounded_integer(document["object_count"], 0, MAXIMUM_OBJECTS)
    ciphertext_bytes = _bounded_integer(
        document["ciphertext_bytes"],
        0,
        MAXIMUM_TOTAL_CIPHERTEXT_BYTES,
    )
    raw_objects = document["objects"]
    if not isinstance(raw_objects, list) or len(raw_objects) != object_count:
        raise _failure()

    objects: list[CiphertextManifestEntry] = []
    seen_keys: set[str] = set()
    prior_key: str | None = None
    total_bytes = 0
    for raw_entry in raw_objects:
        if not isinstance(raw_entry, dict) or set(raw_entry) != {
            "object_key",
            "sha256",
            "size_bytes",
        }:
            raise _failure()
        object_key = raw_entry["object_key"]
        sha256 = raw_entry["sha256"]
        if (
            not isinstance(object_key, str)
            or OBJECT_KEY_PATTERN.fullmatch(object_key) is None
            or object_key in seen_keys
            or (prior_key is not None and object_key <= prior_key)
            or not isinstance(sha256, str)
            or DIGEST_PATTERN.fullmatch(sha256) is None
        ):
            raise _failure()
        size_bytes = _bounded_integer(
            raw_entry["size_bytes"],
            1,
            MAXIMUM_CIPHERTEXT_BYTES,
        )
        total_bytes += size_bytes
        if total_bytes > MAXIMUM_TOTAL_CIPHERTEXT_BYTES:
            raise _failure()
        seen_keys.add(object_key)
        prior_key = object_key
        objects.append(CiphertextManifestEntry(object_key, size_bytes, sha256))
    if total_bytes != ciphertext_bytes:
        raise _failure()
    return GeneratedObjectManifest(
        release=release,
        backup_label=backup_label,
        snapshot_id=snapshot_id,
        object_count=object_count,
        ciphertext_bytes=ciphertext_bytes,
        objects=tuple(objects),
    )


def _read_manifest(path: Path) -> tuple[GeneratedObjectManifest, bytes]:
    serialized = _read_bounded_regular_file(path, MAXIMUM_MANIFEST_BYTES)
    try:
        document = json.loads(serialized)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _failure() from exc
    manifest = _manifest_from_document(document)
    if not hmac.compare_digest(serialized, manifest.serialized()):
        raise _failure()
    return manifest, serialized


def verify_manifest(
    source_root: Path,
    manifest_path: Path,
    expected_sha256: str,
    *,
    maximum_objects: int = MAXIMUM_OBJECTS,
    maximum_bytes: int = MAXIMUM_TOTAL_CIPHERTEXT_BYTES,
) -> GeneratedObjectManifest:
    """Verify a manifest digest and exact local ciphertext inventory."""
    try:
        maximum_objects, maximum_bytes = _validate_bounds(
            maximum_objects,
            maximum_bytes,
        )
        if (
            not isinstance(expected_sha256, str)
            or DIGEST_PATTERN.fullmatch(expected_sha256) is None
        ):
            raise _failure()
        manifest, serialized = _read_manifest(Path(manifest_path))
        if not hmac.compare_digest(
            hashlib.sha256(serialized).hexdigest(),
            expected_sha256,
        ):
            raise _failure()
        if (
            manifest.object_count > maximum_objects
            or manifest.ciphertext_bytes > maximum_bytes
        ):
            raise _failure()
        actual = _inventory(
            Path(source_root),
            maximum_objects=maximum_objects,
            maximum_bytes=maximum_bytes,
        )
        if len(actual) != len(manifest.objects):
            raise _failure()
        for expected, found in zip(manifest.objects, actual):
            if (
                not hmac.compare_digest(expected.object_key, found.object_key)
                or expected.size_bytes != found.size_bytes
                or not hmac.compare_digest(expected.sha256, found.sha256)
            ):
                raise _failure()
        return manifest
    except GeneratedObjectRecoveryError:
        raise
    except Exception as exc:
        raise _failure() from exc


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise _failure()


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_ArgumentParser,
    )
    create = commands.add_parser("create-manifest")
    create.add_argument("source_root", type=Path)
    create.add_argument("release")
    create.add_argument("backup_label")
    create.add_argument("snapshot_id")
    create.add_argument("manifest_path", type=Path)
    verify = commands.add_parser("verify-manifest")
    verify.add_argument("source_root", type=Path)
    verify.add_argument("manifest_path", type=Path)
    verify.add_argument("expected_sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "create-manifest":
            manifest = create_manifest(
                arguments.source_root,
                arguments.release,
                arguments.backup_label,
                arguments.snapshot_id,
                arguments.manifest_path,
            )
        elif arguments.command == "verify-manifest":
            manifest = verify_manifest(
                arguments.source_root,
                arguments.manifest_path,
                arguments.expected_sha256,
            )
        else:
            raise _failure()
        print(
            json.dumps(
                manifest.sanitized_summary(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except Exception:
        print(MANIFEST_ERROR_MESSAGE, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
