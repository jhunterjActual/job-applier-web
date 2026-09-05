"""Private, receipt-bound mirror inventories; never print provider output/keys.

The root shell wrappers own the repository lock for this entire workflow.
This is a file-set boundary, not a new backup, expiry policy or restore proof.
Immutable payloads still require a full source/destination download comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import backup_health as health


SOURCE = "r2:job-applier-staging-db-backups/job-applier/staging/postgresql"
DESTINATION = "onedrive:job-applier-staging/postgresql"
RCLONE = "/usr/local/bin/rclone"
CONFIG = "/etc/careertrellis/rclone.conf"
SCHEMA = "careertrellis.pgbackrest-file-set/v1"
MAX_BYTES = 32 * 1024 * 1024
METADATA = frozenset({
    "archive/careertrellis/archive.info",
    "archive/careertrellis/archive.info.copy",
    "backup/careertrellis/backup.info",
    "backup/careertrellis/backup.info.copy",
    "backup/careertrellis/latest",
})
REQUIRED_METADATA = METADATA - {"backup/careertrellis/latest"}


class InventoryError(Exception):
    """An intentionally non-descriptive private-evidence failure."""


def require(condition: bool) -> None:
    if not condition:
        raise InventoryError()


def valid_path(path: object) -> bool:
    return (
        isinstance(path, str)
        and re.fullmatch(r"(?:archive|backup)/careertrellis/[A-Za-z0-9_./-]+", path) is not None
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    )


def encode(document: dict) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def listing(payload: bytes) -> dict[str, int]:
    rows = json.loads(payload)
    require(isinstance(rows, list) and bool(rows))
    result = {}
    for row in rows:
        require(isinstance(row, dict))
        path, size = row.get("Path"), row.get("Size")
        require(valid_path(path) and path not in result)
        require(type(size) is int and 0 <= size <= health.MAX_INTEGER)
        require(row.get("IsDir") is False)
        result[path] = size
    return result


class Remote:
    def run(self, *args: str) -> bytes:
        # Never let rclone's stderr, stdout, exception or object paths escape.
        # Bound stdout while it is produced, not after an unbounded disk spool.
        with subprocess.Popen(
                [RCLONE, *args, "--config", CONFIG, "--log-level", "ERROR"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        ) as process:
            try:
                payload = process.stdout.read(MAX_BYTES + 1)
                require(len(payload) <= MAX_BYTES)
                require(process.wait() == 0)
                return payload
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def list(self, remote: str, files: Path | None = None) -> dict[str, int]:
        args = ["lsjson", remote, "--recursive", "--files-only"]
        if files is not None:
            args += ["--files-from-raw", str(files)]
        return listing(self.run(*args))

    def digest(self, remote: str, path: str, size: int) -> str:
        require(type(size) is int and 0 <= size <= MAX_BYTES)
        payload = self.run("cat", f"{remote}/{path}")
        require(len(payload) == size)
        return hashlib.sha256(payload).hexdigest()

    def compare(self, files: Path, matches: Path) -> None:
        self.run(
            "check", SOURCE, DESTINATION, "--files-from-raw", str(files),
            "--download", "--one-way", "--checkers", "4", "--match", str(matches),
        )


def validate(document: object) -> dict:
    require(isinstance(document, dict) and set(document) == {
        "schema", "backup_label", "started_at", "files", "metadata_sha256",
    })
    require(document["schema"] == SCHEMA)
    health._validate_label(document["backup_label"])
    health._parse_timestamp(document["started_at"])
    files, metadata = document["files"], document["metadata_sha256"]
    require(isinstance(files, dict) and bool(files) and isinstance(metadata, dict))
    for path, size in files.items():
        require(valid_path(path) and type(size) is int and 0 <= size <= health.MAX_INTEGER)
        if path in METADATA:
            require(size <= MAX_BYTES)
    require(set(metadata) == set(files) & METADATA)
    require(REQUIRED_METADATA <= set(metadata))
    for digest in metadata.values():
        require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None)
    # The full ancestor and selected backup must both be represented. The
    # snapshot includes ALL listed source files, not a guessed WAL age cutoff.
    for label in {document["backup_label"], document["backup_label"].split("_")[0]}:
        require(f"backup/careertrellis/{label}/backup.manifest" in files)
    return document


def prepare(remote: Remote, label: str, started_at: str) -> dict:
    files = remote.list(SOURCE)
    metadata = {path: remote.digest(SOURCE, path, files[path]) for path in sorted(set(files) & METADATA)}
    return validate({"schema": SCHEMA, "backup_label": label, "started_at": started_at,
                     "files": files, "metadata_sha256": metadata})


def require_files(expected: dict[str, int], actual: dict[str, int]) -> None:
    require(all(actual.get(path) == size for path, size in expected.items()))


def verify_metadata(remote: Remote, document: dict, target: str) -> None:
    for path, digest in document["metadata_sha256"].items():
        require(remote.digest(target, path, document["files"][path]) == digest)


def generation_path(root: Path, digest: str) -> Path:
    require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None)
    return root / f"pgbackrest-generation-{digest}.json"


def seal(remote: Remote, document: dict, receipt: dict, root: Path) -> None:
    validate(document)
    health.validate_operation_receipt(receipt)
    require(receipt["operation"] == "pgbackrest_mirror" and receipt["result"] == "ok")
    require(receipt["backup_label"] == document["backup_label"])
    require(receipt["started_at"] == document["started_at"])
    # New WAL can appear freely; every file in the pre-copy source snapshot
    # must exist on BOTH sides. Metadata must still match its pre-copy bytes.
    require_files(document["files"], remote.list(SOURCE))
    require_files(document["files"], remote.list(DESTINATION))
    verify_metadata(remote, document, SOURCE)
    verify_metadata(remote, document, DESTINATION)
    payload = encode(document)
    require(len(payload) <= MAX_BYTES)
    digest = hashlib.sha256(payload).hexdigest()
    owner = health._verify_root(root, create=False)
    path = generation_path(root, digest)
    if path.exists():
        require(health._read_protected(path, owner, MAX_BYTES) == payload)
    else:
        health._exclusive_file(path, payload, owner)
        health._fsync_directory(root)
    # The receipt is the final commit marker. A crash before it cannot create
    # accepted inventory evidence. Counts describe this set, not later WAL.
    receipt = dict(receipt, schema=health.GENERATION_RECEIPT_SCHEMA, generation_sha256=digest,
                   object_count=len(document["files"]), byte_count=sum(document["files"].values()))
    completed = datetime.now(timezone.utc)
    receipt["completed_at"] = health._timestamp(completed)
    receipt["elapsed_seconds"] = int((completed - health._parse_timestamp(document["started_at"])).total_seconds())
    health.publish_receipt(receipt, root=root)


def load_generation(receipt: dict, root: Path) -> dict:
    require(receipt.get("schema") == health.GENERATION_RECEIPT_SCHEMA)
    digest = receipt.get("generation_sha256")
    require(isinstance(digest, str))  # No fallback to a mutable whole-source check.
    owner = health._verify_root(root, create=False)
    payload = health._read_protected(generation_path(root, digest), owner, MAX_BYTES)
    require(hashlib.sha256(payload).hexdigest() == digest)
    document = validate(health._strict_json_bytes(payload, MAX_BYTES))
    require(document["backup_label"] == receipt["backup_label"])
    require(document["started_at"] == receipt["started_at"])
    require(len(document["files"]) == receipt["object_count"])
    require(sum(document["files"].values()) == receipt["byte_count"])
    return document


def verify(remote: Remote, document: dict, scratch: Path) -> None:
    validate(document)
    payloads = {path: size for path, size in document["files"].items() if path not in METADATA}
    files, matches = scratch / "files", scratch / "matches"
    files.write_text("".join(path + "\n" for path in sorted(payloads)), encoding="ascii")
    # A filtered rclone check alone can silently omit missing source entries.
    require_files(payloads, remote.list(SOURCE, files))
    remote.compare(files, matches)
    compared = matches.read_text(encoding="ascii").splitlines()
    require(len(compared) == len(payloads) and set(compared) == set(payloads))
    verify_metadata(remote, document, DESTINATION)


def inventory(remote: Remote, root: Path, scratch: Path) -> None:
    started = datetime.now(timezone.utc)
    # Reject a newer failed mirror; never silently verify an older generation.
    mirror = health.latest_verified_receipt("pgbackrest_mirror", root=root, require_ok=False)
    require(mirror["result"] == "ok")
    age = (started - health._parse_timestamp(mirror["completed_at"])).total_seconds()
    require(0 <= age <= health.MIRROR_MAX_AGE_SECONDS)
    document = load_generation(mirror, root)
    verify(remote, document, scratch)
    completed = datetime.now(timezone.utc)
    count, size = len(document["files"]), sum(document["files"].values())
    health.publish_receipt({
        "schema": health.GENERATION_RECEIPT_SCHEMA, "operation": "pgbackrest_inventory", "result": "ok",
        "started_at": health._timestamp(started), "completed_at": health._timestamp(completed),
        "backup_label": document["backup_label"], "generation_sha256": mirror["generation_sha256"],
        "source_object_count": count, "source_byte_count": size,
        "destination_object_count": count, "destination_byte_count": size,
        "divergence_count": 0, "elapsed_seconds": int((completed - started).total_seconds()),
    }, root=root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Private mirror generation verification")
    parser.add_argument("operation", choices=("prepare", "seal", "inventory"))
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--backup-label")
    parser.add_argument("--started-at")
    args = parser.parse_args()
    try:
        require(os.geteuid() == 0 and os.getegid() == 0)
        os.umask(0o077)
        remote = Remote()
        if args.operation == "prepare":
            require(args.snapshot is not None)
            document = prepare(remote, args.backup_label, args.started_at)
            # This private scratch directory belongs to the locking wrapper.
            health._verify_root(args.snapshot.parent, create=False)
            health._exclusive_file(args.snapshot, encode(document), (0, 0))
        elif args.operation == "seal":
            payload = health._read_protected(args.snapshot, (0, 0), MAX_BYTES)
            document = health._strict_json_bytes(payload, MAX_BYTES)
            receipt = health._read_stdin_document()
            seal(remote, document, receipt, health.RECEIPT_ROOT)
        else:
            with tempfile.TemporaryDirectory(prefix="careertrellis-inventory-", dir="/run") as scratch:
                inventory(remote, health.RECEIPT_ROOT, Path(scratch))
        print('{"operation":"pgbackrest_generation","result":"ok"}')
        return 0
    except Exception:
        print("Mirror generation evidence was missing, stale, incomplete or divergent; no verification success was reported.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
