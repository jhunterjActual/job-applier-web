"""Synthetic generation checks; no live repositories, credentials or API calls."""

import copy
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import backup_health as health
import pgbackrest_inventory as inventory


LABEL = "20260905-030000F"
PAYLOAD = f"backup/careertrellis/{LABEL}/backup.manifest"
WAL = "archive/careertrellis/18-1/00000001/segment-old"
NEW_WAL = "archive/careertrellis/18-1/00000001/segment-new"


class FakeRemote:
    def __init__(self):
        self.source = {path: b"metadata" for path in inventory.REQUIRED_METADATA}
        self.source.update({PAYLOAD: b"manifest", WAL: b"wal"})
        self.destination = dict(self.source)
        self.omit_match = False
        self.comparisons = 0

    def list(self, remote, files=None):
        values = self.source if remote == inventory.SOURCE else self.destination
        wanted = files.read_text().splitlines() if files is not None else values
        return {path: len(data) for path, data in values.items() if path in wanted}

    def digest(self, remote, path, size):
        values = self.source if remote == inventory.SOURCE else self.destination
        inventory.require(path in values and len(values[path]) == size)
        return hashlib.sha256(values[path]).hexdigest()

    def compare(self, files, matches):
        self.comparisons += 1
        paths = files.read_text().splitlines()
        if self.omit_match:
            paths.pop()
        for path in paths:
            inventory.require(path in self.source and path in self.destination)
            inventory.require(self.source[path] == self.destination[path])
        matches.write_text("".join(path + "\n" for path in paths))


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.scratch = Path(self.temporary.name).resolve()
        self.root = self.scratch / "receipts"
        self.root.mkdir(mode=0o700)
        self.remote = FakeRemote()
        self.started = health._timestamp(datetime.now(timezone.utc) - timedelta(seconds=2))
        self.document = inventory.prepare(self.remote, LABEL, self.started)

    def receipt(self):
        return {"schema": health.RECEIPT_SCHEMA, "operation": "pgbackrest_mirror", "result": "ok",
                "started_at": self.started, "completed_at": self.started, "backup_label": LABEL,
                "mirrored_backup_label": LABEL, "object_count": 0, "byte_count": 0, "elapsed_seconds": 0}

    def seal(self):
        inventory.seal(self.remote, self.document, self.receipt(), self.root)
        return health.latest_verified_receipt("pgbackrest_mirror", root=self.root)

    def test_wal_added_during_copy_and_after_mirror_is_not_required(self):
        self.remote.source[NEW_WAL] = b"new WAL"
        mirror = self.seal()
        self.assertEqual(len(self.document["files"]), mirror["object_count"])
        inventory.inventory(self.remote, self.root, self.scratch)
        result = health.latest_verified_receipt("pgbackrest_inventory", root=self.root)
        self.assertEqual(mirror["generation_sha256"], result["generation_sha256"])
        self.assertEqual(1, self.remote.comparisons)
        self.assertEqual(mirror["object_count"], result["source_object_count"])

    def test_destination_history_is_allowed(self):
        self.remote.destination[NEW_WAL] = b"retained old history"
        self.seal()
        inventory.inventory(self.remote, self.root, self.scratch)

    def test_missing_required_copy_cannot_commit_mirror(self):
        del self.remote.destination[WAL]
        with self.assertRaises(inventory.InventoryError):
            self.seal()
        self.assertFalse(list(self.root.iterdir()))

    def test_metadata_changes_during_copy_cannot_commit_mirror(self):
        path = next(iter(inventory.REQUIRED_METADATA))
        self.remote.source[path] = self.remote.destination[path] = b"changed!"
        with self.assertRaises(inventory.InventoryError):
            self.seal()

    def test_later_source_metadata_does_not_change_generation(self):
        self.seal()
        for path in inventory.REQUIRED_METADATA:
            self.remote.source[path] = b"later metadata"
        inventory.inventory(self.remote, self.root, self.scratch)

    def test_missing_or_same_size_corrupt_destination_fails(self):
        for path in (WAL, PAYLOAD, *inventory.REQUIRED_METADATA):
            for replacement in (None, b"x" * len(self.remote.destination[path])):
                with self.subTest(path=path, missing=replacement is None):
                    original = self.remote.destination[path]
                    if replacement is None:
                        del self.remote.destination[path]
                    else:
                        self.remote.destination[path] = replacement
                    with self.assertRaises(inventory.InventoryError):
                        inventory.verify(self.remote, self.document, self.scratch)
                    self.remote.destination[path] = original

    def test_missing_source_even_when_absent_both_sides_fails(self):
        del self.remote.source[WAL]
        del self.remote.destination[WAL]
        with self.assertRaises(inventory.InventoryError):
            inventory.verify(self.remote, self.document, self.scratch)

    def test_success_exit_without_exact_download_coverage_fails(self):
        self.remote.omit_match = True
        with self.assertRaises(inventory.InventoryError):
            inventory.verify(self.remote, self.document, self.scratch)

    def test_manifest_tamper_or_missing_does_not_fall_back(self):
        mirror = self.seal()
        path = inventory.generation_path(self.root, mirror["generation_sha256"])
        path.write_bytes(b"{}\n")
        with self.assertRaises(inventory.InventoryError):
            inventory.load_generation(mirror, self.root)
        path.unlink()
        with self.assertRaises(health.BackupHealthVerificationError):
            inventory.load_generation(mirror, self.root)
        legacy = dict(mirror)
        legacy.pop("generation_sha256")
        with self.assertRaises(inventory.InventoryError):
            inventory.load_generation(legacy, self.root)

    def test_wrong_label_or_counts_cannot_bind_manifest(self):
        mirror = self.seal()
        for field, value in (("backup_label", "20260904-030000F"), ("object_count", 999), ("byte_count", 999)):
            with self.subTest(field=field), self.assertRaises(inventory.InventoryError):
                inventory.load_generation(dict(mirror, **{field: value}), self.root)

    def test_failed_or_stale_mirror_cannot_publish_inventory(self):
        mirror = self.seal()
        for invalid in (dict(mirror, result="failed"), dict(mirror, completed_at="2020-01-01T00:00:00Z")):
            with mock.patch.object(health, "latest_verified_receipt", return_value=invalid):
                with self.assertRaises((inventory.InventoryError, health.BackupHealthValidationError)):
                    inventory.inventory(self.remote, self.root, self.scratch)
        self.assertFalse(health.scan_receipts(root=self.root).receipts["pgbackrest_inventory"])

    def test_missing_metadata_and_manifests_rejected(self):
        for path in (PAYLOAD, *inventory.REQUIRED_METADATA):
            invalid = copy.deepcopy(self.document)
            invalid["files"].pop(path)
            invalid["metadata_sha256"].pop(path, None)
            with self.assertRaises(inventory.InventoryError):
                inventory.validate(invalid)

    def test_unsafe_paths_duplicate_entries_and_bad_sizes_rejected(self):
        for path in ("../secret", "/absolute", "backup/careertrellis/../bad", "backup/careertrellis/a\nb", "backup/careertrellis/a\\b"):
            self.assertFalse(inventory.valid_path(path))
        for rows in ([], [{"Path": WAL, "Size": True, "IsDir": False}],
                     [{"Path": WAL, "Size": 1, "IsDir": False}] * 2):
            with self.assertRaises(inventory.InventoryError):
                inventory.listing(json.dumps(rows).encode())

    def test_generation_receipt_is_versioned_and_closed(self):
        for operation in ("pgbackrest_mirror", "pgbackrest_inventory"):
            from test_backup_health import operation_receipt
            receipt = operation_receipt(operation)
            self.assertEqual(receipt, health.validate_operation_receipt(receipt))
            receipt["generation_sha256"] = "a" * 64
            with self.assertRaises(health.BackupHealthValidationError):
                health.validate_operation_receipt(receipt)
            receipt["schema"] = health.GENERATION_RECEIPT_SCHEMA
            self.assertEqual(receipt, health.validate_operation_receipt(receipt))
            for value in ("../secret", "a" * 63, "A" * 64, None, 1):
                with self.assertRaises(health.BackupHealthValidationError):
                    health.validate_operation_receipt(dict(receipt, generation_sha256=value))
            receipt.pop("generation_sha256")
            with self.assertRaises(health.BackupHealthValidationError):
                health.validate_operation_receipt(receipt)

    def test_remote_commands_require_download_and_literal_file_list(self):
        with mock.patch.object(inventory.Remote, "run", return_value=b"") as run:
            inventory.Remote().compare(self.scratch / "files", self.scratch / "matches")
        args = run.call_args.args
        self.assertIn("--download", args)
        self.assertIn("--files-from-raw", args)
        self.assertIn("--match", args)
        self.assertNotIn("--size-only", args)

    def test_listing_skips_unused_metadata_for_full_and_filtered_sets(self):
        payload = json.dumps([{"Path": WAL, "Size": 3, "IsDir": False}]).encode()
        for files in (None, self.scratch / "files"):
            with mock.patch.object(inventory.Remote, "run", return_value=payload) as run:
                result = inventory.Remote().list(inventory.SOURCE, files)
            self.assertEqual({WAL: 3}, result)
            args = run.call_args.args
            self.assertIn("--no-modtime", args)
            self.assertIn("--no-mimetype", args)
            if files is not None:
                self.assertIn("--files-from-raw", args)
            self.assertNotIn("--size-only", args)

    def test_listing_flags_do_not_weaken_metadata_download(self):
        with mock.patch.object(inventory.Remote, "run", return_value=b"metadata") as run:
            digest = inventory.Remote().digest(inventory.DESTINATION, next(iter(inventory.REQUIRED_METADATA)), 8)
        self.assertEqual(hashlib.sha256(b"metadata").hexdigest(), digest)
        self.assertEqual("cat", run.call_args.args[0])
        self.assertNotIn("--no-modtime", run.call_args.args)
        self.assertNotIn("--no-mimetype", run.call_args.args)

    def test_provider_failure_is_sanitized(self):
        process = mock.MagicMock()
        process.__enter__.return_value = process
        process.stdout = io.BytesIO(b"private provider failure")
        process.wait.return_value = process.poll.return_value = 1
        with mock.patch.object(subprocess, "Popen", return_value=process) as run:
            with self.assertRaises(inventory.InventoryError) as error:
                inventory.Remote().run("lsjson", inventory.SOURCE)
        self.assertEqual("", str(error.exception))
        self.assertEqual(subprocess.DEVNULL, run.call_args.kwargs["stderr"])

    def test_oversized_metadata_is_rejected_before_download(self):
        with mock.patch.object(inventory.Remote, "run") as run:
            with self.assertRaises(inventory.InventoryError):
                inventory.Remote().digest(inventory.SOURCE, next(iter(inventory.METADATA)), inventory.MAX_BYTES + 1)
            run.assert_not_called()

    def test_oversized_provider_output_is_terminated(self):
        process = mock.MagicMock()
        process.__enter__.return_value = process
        process.stdout = io.BytesIO(b"too large")
        process.poll.return_value = None
        with mock.patch.object(subprocess, "Popen", return_value=process), mock.patch.object(inventory, "MAX_BYTES", 4):
            with self.assertRaises(inventory.InventoryError):
                inventory.Remote().run("lsjson", inventory.SOURCE)
        process.kill.assert_called_once()
        process.wait.assert_called_once()

    def test_receipt_publish_failure_does_not_commit_inventory(self):
        with mock.patch.object(health, "publish_receipt", side_effect=health.BackupHealthPersistenceError("test")):
            with self.assertRaises(health.BackupHealthPersistenceError):
                self.seal()
        self.assertFalse(health.scan_receipts(root=self.root).receipts["pgbackrest_mirror"])

    def test_optional_latest_pointer_is_verified_when_present(self):
        path = "backup/careertrellis/latest"
        self.remote.source[path] = self.remote.destination[path] = b"pointer"
        self.document = inventory.prepare(self.remote, LABEL, self.started)
        self.seal()
        self.remote.destination[path] = b"changed"
        with self.assertRaises(inventory.InventoryError):
            inventory.inventory(self.remote, self.root, self.scratch)

    def test_generation_pruning_unlink_error_is_typed_after_commit(self):
        first = self.seal()
        first_path = inventory.generation_path(self.root, first["generation_sha256"])
        real_unlink = os.unlink

        def unlink(path, *args, **kwargs):
            if Path(path) == first_path:
                raise OSError("private path must not escape")
            return real_unlink(path, *args, **kwargs)

        self.remote.source[NEW_WAL] = self.remote.destination[NEW_WAL] = b"new"
        self.document = inventory.prepare(self.remote, LABEL, self.started)
        with mock.patch.object(health, "MAX_RECEIPTS_PER_OPERATION", 1), mock.patch.object(os, "unlink", side_effect=unlink), mock.patch.object(inventory, "datetime") as clock:
            clock.now.return_value = datetime.now(timezone.utc) + timedelta(seconds=1)
            with self.assertRaises(health.BackupHealthPersistenceError):
                self.seal()
        self.assertTrue(first_path.exists())
        self.assertEqual(1, len(health.scan_receipts(root=self.root).receipts["pgbackrest_mirror"]))

    def test_new_daily_mirror_does_not_invalidate_existing_weekly_receipt(self):
        first = self.seal()
        inventory.inventory(self.remote, self.root, self.scratch)
        self.remote.source[NEW_WAL] = self.remote.destination[NEW_WAL] = b"new"
        self.document = inventory.prepare(self.remote, LABEL, self.started)
        with mock.patch.object(inventory, "datetime") as clock:
            clock.now.return_value = datetime.now(timezone.utc) + timedelta(seconds=1)
            second = self.seal()
        self.assertNotEqual(first["generation_sha256"], second["generation_sha256"])
        weekly = health.latest_verified_receipt("pgbackrest_inventory", root=self.root)
        self.assertEqual(first["generation_sha256"], weekly["generation_sha256"])

    def test_pruning_preserves_inventory_references_then_retires_unreferenced_manifest(self):
        with mock.patch.object(health, "MAX_RECEIPTS_PER_OPERATION", 1):
            first = self.seal()
            inventory.inventory(self.remote, self.root, self.scratch)
            first_path = inventory.generation_path(self.root, first["generation_sha256"])
            later = datetime.now(timezone.utc) + timedelta(seconds=1)
            self.started = health._timestamp(later - timedelta(seconds=1))
            self.remote.source[NEW_WAL] = self.remote.destination[NEW_WAL] = b"new"
            self.document = inventory.prepare(self.remote, LABEL, self.started)
            with mock.patch.object(inventory, "datetime") as clock:
                clock.now.return_value = later
                second = self.seal()
                self.assertTrue(first_path.exists())
                inventory.inventory(self.remote, self.root, self.scratch)
            self.assertFalse(first_path.exists())
            self.assertTrue(inventory.generation_path(self.root, second["generation_sha256"]).exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX ownership and mode checks")
    def test_unsafe_manifest_permissions_and_symlink_fail(self):
        mirror = self.seal()
        path = inventory.generation_path(self.root, mirror["generation_sha256"])
        path.chmod(0o644)
        with self.assertRaises(health.BackupHealthVerificationError):
            inventory.load_generation(mirror, self.root)
        path.chmod(0o600)
        original = path.with_suffix(".original")
        path.rename(original)
        path.symlink_to(original)
        with self.assertRaises(health.BackupHealthVerificationError):
            inventory.load_generation(mirror, self.root)


if __name__ == "__main__":
    unittest.main()
