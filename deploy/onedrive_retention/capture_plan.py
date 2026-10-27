"""Pure plans only; no provider calls, filesystem access or runtime execution.

An executor must hold the canonical repository lock, validate protected config
and pinned executable bytes, create exclusive no-follow root-owned private task
paths, and retain subprocess output privately. It must capture and compare source
metadata before and after hydration against the generation. A separate reviewed
native local-repository restore adapter is still required before retirement.
"""
import datetime as dt
import hashlib
import json
import re


SOURCE = "r2:job-applier-staging-db-backups/job-applier/staging/postgresql"
DESTINATION = "onedrive:job-applier-staging/postgresql-bundles"
ROOT = "/var/lib/careertrellis/onedrive-bundles"
CONFIG = "/etc/careertrellis/rclone.conf"
BINARY = "/usr/local/bin/rclone"
BINARY_SHA256 = "f3f9aff817f9766029e50adf9a7963c169e475b8f10c7927823568a0d9443db7"
LOCK = "/run/lock/careertrellis-pgbackrest-repository.lock"
METADATA = frozenset(("archive/careertrellis/archive.info",
    "archive/careertrellis/archive.info.copy", "backup/careertrellis/backup.info",
    "backup/careertrellis/backup.info.copy", "backup/careertrellis/latest"))
LABEL = re.compile(r"[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")


class CapturePlanError(ValueError):
    pass


def need(value):
    if not value:
        raise CapturePlanError("repository_capture_plan_refused")


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("ascii")


def validate_generation(value):
    need(type(value) is dict and set(value) == {"schema", "backup_label",
        "started_at", "files", "metadata_sha256"})
    need(value["schema"] == "careertrellis.pgbackrest-file-set/v1")
    label = value["backup_label"]
    need(type(label) is str and LABEL.fullmatch(label))
    stamp = value["started_at"]
    need(type(stamp) is str and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp))
    try:
        dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        need(False)
    files, metadata = value["files"], value["metadata_sha256"]
    need(type(files) is dict and 0 < len(files) <= 100000 and type(metadata) is dict)
    for path, size in files.items():
        need(type(path) is str and len(path) <= 240
            and re.fullmatch(r"(?:archive|backup)/careertrellis/[A-Za-z0-9_./-]+", path)
            and all(part not in ("", ".", "..") for part in path.split("/")))
        need(type(size) is int and 0 <= size <= 512 * 1024**2)
        if path in METADATA:
            need(size <= 32 * 1024**2)
    need(sum(files.values()) <= 512 * 1024**2)
    need(set(metadata) == set(files) & METADATA
         and METADATA - {"backup/careertrellis/latest"} <= set(metadata))
    need(all(type(digest) is str and HEX.fullmatch(digest) for digest in metadata.values()))
    for selected in {label, label.split("_")[0]}:
        need("backup/careertrellis/" + selected + "/backup.manifest" in files)
    return value


def plan_capture(generation, generation_sha256, transaction, archive_sha256,
                 archive_size):
    """Return commands and mandatory gates; the returned plan grants no execution."""
    validate_generation(generation)
    need(type(generation_sha256) is str and HEX.fullmatch(generation_sha256)
         and hashlib.sha256(canonical(generation)).hexdigest() == generation_sha256)
    need(type(transaction) is str and re.fullmatch(r"[0-9a-f]{32}", transaction))
    need(type(archive_sha256) is str and HEX.fullmatch(archive_sha256))
    need(type(archive_size) is int and 0 < archive_size <= 512 * 1024**2)
    root = ROOT + "/" + transaction
    listing, source = root + "/files.txt", root + "/source"
    archive, downloaded = root + "/repository.tar", root + "/downloaded.tar"
    remote = DESTINATION + "/" + archive_sha256 + "/repository.tar"
    fixed = ["--config", CONFIG, "--log-level", "ERROR", "--stats", "0"]
    return {"schema": "careertrellis.onedrive-capture-plan/v1",
        "execution_authorized": False, "transaction": transaction,
        "generation_sha256": generation_sha256, "backup_label": generation["backup_label"],
        "archive_sha256": archive_sha256, "archive_size": archive_size,
        "root": root, "source_root": source, "archive_path": archive,
        "download_path": downloaded, "destination": remote,
        "files_from_path": listing,
        "files_from_text": "".join(path + "\n" for path in sorted(generation["files"])),
        "metadata_sha256": dict(generation["metadata_sha256"]),
        "commands": {
            "hydrate": [BINARY, "copy", SOURCE, source, "--files-from-raw", listing,
                        "--immutable", *fixed],
            "verify_hydration": [BINARY, "check", SOURCE, source, "--files-from-raw",
                        listing, "--download", "--one-way", *fixed],
            "upload": [BINARY, "copyto", archive, remote, "--immutable",
                        "--onedrive-hard-delete=false", *fixed],
            "download": [BINARY, "copyto", remote, downloaded, "--immutable", *fixed]},
        "requirements": {"canonical_lock_held": LOCK,
            "protected_config": {"path": CONFIG, "uid": 0, "mode": "0600",
                                 "no_follow": True, "unchanged_after": True},
            "pinned_binary": {"path": BINARY, "sha256": BINARY_SHA256},
            "private_subprocess_outputs": True, "exclusive_owned_task_root": True,
            "fresh_complete_source_generation": True,
            "metadata_snapshot_before_after": True,
            "exact_hydrated_membership_sizes_and_metadata": True,
            "archive_and_download_digest_verification": True,
            "quota_and_namespace_budget_gate_before_upload": True,
            "native_local_repository_restore_before_retirement": True}}
