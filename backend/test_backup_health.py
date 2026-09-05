"""Synthetic tests for the privacy-safe backup-health evidence core."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

import backup_health


UTC = timezone.utc
OBSERVED = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)
FULL_LABEL = "20260824-030000F"
LATEST_LABEL = "20260824-030000F_20260825-030000D"


def timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def operation_receipt(
    operation: str,
    *,
    completed: datetime | None = None,
    result: str = "ok",
) -> dict[str, object]:
    completed = completed or (OBSERVED - timedelta(hours=1))
    common: dict[str, object] = {
        "schema": backup_health.RECEIPT_SCHEMA,
        "operation": operation,
        "result": result,
        "started_at": timestamp(completed - timedelta(minutes=10)),
        "completed_at": timestamp(completed),
    }
    fields: dict[str, dict[str, object]] = {
        "full_backup": {
            "backup_label": FULL_LABEL,
            "elapsed_seconds": 600,
        },
        "differential_backup": {
            "backup_label": LATEST_LABEL,
            "elapsed_seconds": 600,
        },
        "pgbackrest_mirror": {
            "backup_label": LATEST_LABEL,
            "mirrored_backup_label": LATEST_LABEL,
            "object_count": 100,
            "byte_count": 200,
            "elapsed_seconds": 600,
        },
        "pgbackrest_inventory": {
            "backup_label": LATEST_LABEL,
            "source_object_count": 100,
            "source_byte_count": 200,
            "destination_object_count": 100,
            "destination_byte_count": 200,
            "divergence_count": 0,
            "elapsed_seconds": 600,
        },
        "pgbackrest_restore": {
            "backup_label": LATEST_LABEL,
            "elapsed_seconds": 600,
            "rpo_seconds": 3600,
            "rto_seconds": 500,
            "promotion_verified": True,
            "checksum_verified": True,
            "cleanup_verified": True,
        },
        "generated_object_mirror": {
            "object_count": 30,
            "byte_count": 60,
            "divergence_count": 0,
            "elapsed_seconds": 300,
        },
        "generated_object_restore": {
            "object_count": 30,
            "byte_count": 60,
            "elapsed_seconds": 300,
            "manifest_verified": True,
            "ciphertext_verified": True,
            "target_left_intact": True,
        },
    }
    common.update(fields[operation])
    return common


def snapshot(surface: str) -> dict[str, object]:
    operations = backup_health.SURFACE_OPERATIONS[surface]
    document: dict[str, object] = {
        "schema": backup_health.SNAPSHOT_SCHEMA,
        "surface": surface,
        "observed_at": timestamp(OBSERVED),
        "units": {
            operation: {"last_result": "success", "missed": False}
            for operation in operations
        },
    }
    if surface == "database":
        document.update(
            {
                "latest_backup_label": LATEST_LABEL,
                "wal": {
                    "failed_count": 0,
                    "previous_failed_count": 0,
                    "segment_lag": 1,
                    "repository_archive_max_behind_database": False,
                },
            }
        )
    return document


def chmod_file(path: Path, mode: int = 0o600) -> None:
    if os.name == "posix":
        os.chmod(path, mode)


class TemporaryRoots(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.parent = Path(self.temporary.name).resolve()
        self.receipts = self.parent / "receipts"
        self.status_root = self.parent / "status"
        self.alert_root = self.parent / "alerts"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def publish_database_receipt(self, operation, at):
        document = operation_receipt(operation, completed=at)
        if operation in {"pgbackrest_mirror", "pgbackrest_inventory"}:
            # The evaluator verifies opaque bytes, not private provider paths.
            payload = json.dumps({"synthetic": operation, "at": timestamp(at)}).encode("ascii")
            digest = hashlib.sha256(payload).hexdigest()
            owner = backup_health._verify_root(self.receipts, create=True)
            backup_health._exclusive_file(
                self.receipts / f"pgbackrest-generation-{digest}.json", payload, owner
            )
            document.update(schema=backup_health.GENERATION_RECEIPT_SCHEMA, generation_sha256=digest)
        backup_health.publish_receipt(document, root=self.receipts, now=at)

    def publish_healthy_database(self) -> None:
        completed = {
            "full_backup": OBSERVED - timedelta(days=1),
            "differential_backup": OBSERVED - timedelta(hours=2),
            "pgbackrest_mirror": OBSERVED - timedelta(hours=1),
            "pgbackrest_inventory": OBSERVED - timedelta(days=1),
            "pgbackrest_restore": OBSERVED - timedelta(days=10),
        }
        for operation, at in completed.items():
            self.publish_database_receipt(operation, at)

    def publish_healthy_objects(self) -> None:
        for operation, at in {
            "generated_object_mirror": OBSERVED - timedelta(days=1),
            "generated_object_restore": OBSERVED - timedelta(days=10),
        }.items():
            backup_health.publish_receipt(
                operation_receipt(operation, completed=at),
                root=self.receipts,
                now=at,
            )


class SchemaTests(unittest.TestCase):
    def test_every_operation_has_a_strict_schema(self) -> None:
        for operation in backup_health.OPERATIONS:
            with self.subTest(operation=operation):
                normalized = backup_health.validate_operation_receipt(
                    operation_receipt(operation)
                )
                self.assertEqual(operation, normalized["operation"])

    def test_forbidden_fields_are_rejected_without_echo(self) -> None:
        for field in (
            "identity",
            "email",
            "path",
            "endpoint",
            "object_key",
            "token",
            "cookie",
        ):
            document = operation_receipt("full_backup")
            document[field] = "sensitive-value"
            with self.subTest(field=field):
                with self.assertRaises(backup_health.BackupHealthValidationError) as raised:
                    backup_health.validate_operation_receipt(document)
                self.assertEqual("Backup-health document is invalid.", str(raised.exception))
                self.assertNotIn(field, str(raised.exception))
                self.assertNotIn("sensitive-value", str(raised.exception))

    def test_free_form_and_non_timestamp_labels_are_rejected(self) -> None:
        document = operation_receipt("full_backup")
        for value in ("label", "../label", "https-label", "20260824-030000X"):
            document["backup_label"] = value
            with self.subTest(value=value):
                with self.assertRaises(backup_health.BackupHealthValidationError):
                    backup_health.validate_operation_receipt(document)

        full = operation_receipt("full_backup")
        full["backup_label"] = LATEST_LABEL
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_operation_receipt(full)
        differential = operation_receipt("differential_backup")
        differential["backup_label"] = FULL_LABEL
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_operation_receipt(differential)

    def test_duplicate_json_keys_and_nonfinite_numbers_are_rejected(self) -> None:
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health._strict_json_bytes(b'{"schema":1,"schema":2}', 100)
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health._strict_json_bytes(b'{"value":NaN}', 100)

    def test_non_string_enum_values_use_fixed_validation_errors(self) -> None:
        receipt = operation_receipt("full_backup")
        receipt["result"] = []
        with self.assertRaises(backup_health.BackupHealthValidationError) as raised:
            backup_health.validate_operation_receipt(receipt)
        self.assertEqual("Backup-health document is invalid.", str(raised.exception))

        document = snapshot("objects")
        document["units"]["generated_object_mirror"]["last_result"] = []
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_snapshot(document)

    def test_clock_and_integer_bounds(self) -> None:
        document = operation_receipt("full_backup")
        document["started_at"] = timestamp(OBSERVED)
        document["completed_at"] = timestamp(OBSERVED - timedelta(seconds=1))
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_operation_receipt(document)

        document = operation_receipt("full_backup")
        document["started_at"] = timestamp(OBSERVED - timedelta(days=8))
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_operation_receipt(document)

        document = operation_receipt("pgbackrest_restore")
        document["rto_seconds"] = document["elapsed_seconds"] + 1
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_operation_receipt(document)

    def test_ok_receipts_cannot_claim_failed_checks(self) -> None:
        restore = operation_receipt("pgbackrest_restore")
        restore["checksum_verified"] = False
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_operation_receipt(restore)
        inventory = operation_receipt("pgbackrest_inventory")
        inventory["divergence_count"] = 1
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_operation_receipt(inventory)
        object_restore = operation_receipt("generated_object_restore")
        object_restore["target_left_intact"] = False
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_operation_receipt(object_restore)

    def test_append_only_inventory_may_have_more_destination_history(self) -> None:
        inventory = operation_receipt("pgbackrest_inventory")
        inventory["destination_object_count"] = 120
        inventory["destination_byte_count"] = 240
        normalized = backup_health.validate_operation_receipt(inventory)
        self.assertEqual(0, normalized["divergence_count"])

        inventory["destination_object_count"] = 99
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_operation_receipt(inventory)

    def test_surface_schemas_are_separate_and_exact(self) -> None:
        database = snapshot("database")
        objects = snapshot("objects")
        self.assertEqual(
            "database", backup_health.validate_snapshot(database)["surface"]
        )
        self.assertEqual("objects", backup_health.validate_snapshot(objects)["surface"])

        objects["wal"] = database["wal"]
        objects["latest_backup_label"] = LATEST_LABEL
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_snapshot(objects)

        database = snapshot("database")
        del database["units"]["full_backup"]
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_snapshot(database)

        objects = snapshot("objects")
        objects["units"]["full_backup"] = {
            "last_result": "success",
            "missed": False,
        }
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_snapshot(objects)


class ReceiptPersistenceTests(TemporaryRoots):
    def test_publish_read_and_latest_are_manifest_verified(self) -> None:
        files = backup_health.publish_receipt(
            operation_receipt("full_backup"),
            root=self.receipts,
            now=OBSERVED,
        )
        self.assertEqual(
            "full_backup",
            backup_health.read_receipt(
                files.manifest_name, root=self.receipts
            )["operation"],
        )
        self.assertEqual(
            FULL_LABEL,
            backup_health.latest_verified_receipt(
                "full_backup", root=self.receipts
            )["backup_label"],
        )

    def test_json_without_manifest_is_nonterminal(self) -> None:
        self.receipts.mkdir(mode=0o700)
        payload = backup_health.serialize_operation_receipt(
            operation_receipt("full_backup")
        )
        name = "20260825T120000Z-full_backup-0000000000000001.json"
        path = self.receipts / name
        path.write_bytes(payload)
        chmod_file(path)
        inventory = backup_health.scan_receipts(root=self.receipts)
        self.assertFalse(inventory.receipts["full_backup"])
        self.assertFalse(inventory.invalid_operations)

    def test_manifest_without_json_is_invalid_committed_evidence(self) -> None:
        self.receipts.mkdir(mode=0o700)
        receipt_name = "20260825T120000Z-full_backup-0000000000000002.json"
        manifest_name = receipt_name[:-5] + ".sha256"
        payload = f"{'0' * 64}  {receipt_name}\n".encode("ascii")
        path = self.receipts / manifest_name
        path.write_bytes(payload)
        chmod_file(path)
        inventory = backup_health.scan_receipts(root=self.receipts)
        self.assertIn("full_backup", inventory.invalid_operations)

    def test_manifest_publish_failure_never_leaves_committed_json(self) -> None:
        original_link = backup_health._link_exclusive

        def fail_manifest(source: Path, target: Path) -> None:
            if target.suffix == ".sha256":
                raise backup_health.BackupHealthPersistenceError(
                    "Backup-health evidence could not be retained safely."
                )
            original_link(source, target)

        with mock.patch.object(
            backup_health, "_link_exclusive", side_effect=fail_manifest
        ):
            with self.assertRaises(backup_health.BackupHealthPersistenceError):
                backup_health.publish_receipt(
                    operation_receipt("full_backup"),
                    root=self.receipts,
                    now=OBSERVED,
                )
        self.assertFalse(list(self.receipts.glob("*.sha256")))
        self.assertFalse(list(self.receipts.glob("*.json")))

    def test_exclusive_create_collision_does_not_remove_existing_file(self) -> None:
        self.receipts.mkdir(mode=0o700)
        existing = self.receipts / "collision"
        existing.write_bytes(b"retained")
        chmod_file(existing)
        with self.assertRaises(backup_health.BackupHealthPersistenceError):
            backup_health._exclusive_file(existing, b"replacement", None)
        self.assertEqual(b"retained", existing.read_bytes())

    def test_tamper_is_detected_and_latest_fails_closed(self) -> None:
        files = backup_health.publish_receipt(
            operation_receipt("full_backup"), root=self.receipts, now=OBSERVED
        )
        receipt_path = self.receipts / files.receipt_name
        receipt_path.write_bytes(b"{}\n")
        chmod_file(receipt_path)
        with self.assertRaises(backup_health.BackupHealthVerificationError):
            backup_health.read_receipt(files.manifest_name, root=self.receipts)
        with self.assertRaises(backup_health.BackupHealthVerificationError):
            backup_health.latest_verified_receipt(
                "full_backup", root=self.receipts
            )

    def test_noncanonical_json_is_rejected_even_with_matching_digest(self) -> None:
        self.receipts.mkdir(mode=0o700)
        document = operation_receipt("full_backup")
        payload = json.dumps(document, indent=2).encode("ascii")
        receipt_name = "20260825T120000Z-full_backup-0000000000000003.json"
        manifest_name = receipt_name[:-5] + ".sha256"
        receipt_path = self.receipts / receipt_name
        manifest_path = self.receipts / manifest_name
        receipt_path.write_bytes(payload)
        manifest_path.write_bytes(
            f"{hashlib.sha256(payload).hexdigest()}  {receipt_name}\n".encode(
                "ascii"
            )
        )
        chmod_file(receipt_path)
        chmod_file(manifest_path)
        with self.assertRaises(backup_health.BackupHealthVerificationError):
            backup_health.read_receipt(manifest_name, root=self.receipts)

    def test_history_is_pruned_to_32_valid_pairs_per_operation(self) -> None:
        for index in range(35):
            completed = OBSERVED - timedelta(minutes=35 - index)
            backup_health.publish_receipt(
                operation_receipt("full_backup", completed=completed),
                root=self.receipts,
                now=completed,
            )
        manifests = list(self.receipts.glob("*-full_backup-*.sha256"))
        receipts = list(self.receipts.glob("*-full_backup-*.json"))
        self.assertEqual(backup_health.MAX_RECEIPTS_PER_OPERATION, len(manifests))
        self.assertEqual(backup_health.MAX_RECEIPTS_PER_OPERATION, len(receipts))
        self.assertEqual(
            backup_health.MAX_RECEIPTS_PER_OPERATION,
            len(backup_health.scan_receipts(root=self.receipts).receipts["full_backup"]),
        )

    def test_pruning_never_removes_invalid_committed_pair(self) -> None:
        files = backup_health.publish_receipt(
            operation_receipt("full_backup"), root=self.receipts, now=OBSERVED
        )
        (self.receipts / files.receipt_name).write_bytes(b"{}\n")
        chmod_file(self.receipts / files.receipt_name)
        for index in range(33):
            completed = OBSERVED + timedelta(minutes=index + 1)
            backup_health.publish_receipt(
                operation_receipt("full_backup", completed=OBSERVED),
                root=self.receipts,
                now=completed,
            )
        self.assertTrue((self.receipts / files.manifest_name).exists())
        self.assertTrue((self.receipts / files.receipt_name).exists())
        self.assertEqual(
            32,
            len(backup_health.scan_receipts(root=self.receipts).receipts["full_backup"]),
        )

    @unittest.skipUnless(os.name == "posix", "POSIX modes are not portable")
    def test_root_and_files_have_restrictive_modes(self) -> None:
        files = backup_health.publish_receipt(
            operation_receipt("full_backup"), root=self.receipts, now=OBSERVED
        )
        self.assertEqual(0o700, stat.S_IMODE(os.stat(self.receipts).st_mode))
        self.assertEqual(
            0o600,
            stat.S_IMODE(os.stat(self.receipts / files.receipt_name).st_mode),
        )
        self.assertEqual(
            0o600,
            stat.S_IMODE(os.stat(self.receipts / files.manifest_name).st_mode),
        )

    def test_symlink_receipt_root_is_rejected_where_supported(self) -> None:
        target = self.parent / "target"
        target.mkdir(mode=0o700)
        try:
            self.receipts.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable")
        with self.assertRaises(backup_health.BackupHealthPersistenceError):
            backup_health.publish_receipt(
                operation_receipt("full_backup"), root=self.receipts
            )


class EvaluationTests(TemporaryRoots):
    def test_legacy_database_receipts_are_readable_but_not_generation_health(self):
        for operation in backup_health.SURFACE_OPERATIONS["database"]:
            backup_health.publish_receipt(operation_receipt(operation), root=self.receipts)
        status = backup_health.evaluate_backup_health(snapshot("database"), receipt_root=self.receipts)
        self.assertIn("pgbackrest_mirror_receipt_missing", status["failure_codes"])
        self.assertIn("pgbackrest_inventory_receipt_missing", status["failure_codes"])
        self.assertFalse(backup_health.scan_receipts(root=self.receipts).invalid_operations)

    def test_missing_or_tampered_mirror_and_weekly_manifests_fail_health(self):
        self.publish_healthy_database()
        for operation in ("pgbackrest_mirror", "pgbackrest_inventory"):
            receipt = backup_health.latest_verified_receipt(operation, root=self.receipts)
            path = self.receipts / f"pgbackrest-generation-{receipt['generation_sha256']}.json"
            original = path.read_bytes()
            for missing in (False, True):
                with self.subTest(operation=operation, missing=missing):
                    if missing:
                        path.unlink()
                    else:
                        path.write_bytes(b"tampered private manifest")
                    status = backup_health.evaluate_backup_health(snapshot("database"), receipt_root=self.receipts)
                    self.assertIn(f"{operation}_receipt_invalid", status["failure_codes"])
                    self.assertEqual("alert", status["result"])
                    path.write_bytes(original)
                    chmod_file(path)

    def test_healthy_database_and_objects_surfaces_pass_independently(self) -> None:
        self.publish_healthy_database()
        database = backup_health.evaluate_backup_health(
            snapshot("database"), receipt_root=self.receipts
        )
        self.assertEqual("ok", database["result"])
        self.assertEqual("database", database["surface"])
        self.assertEqual(set(backup_health.AGE_KEYS_BY_SURFACE["database"]), set(database["ages_seconds"]))

        # Missing object evidence does not taint database health, but it does
        # fail the independently evaluated objects surface.
        objects = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        self.assertEqual("alert", objects["result"])
        self.assertIn("generated_object_mirror_receipt_missing", objects["failure_codes"])

        self.publish_healthy_objects()
        objects = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        self.assertEqual("ok", objects["result"])
        self.assertEqual("objects", objects["surface"])

    def test_database_thresholds_are_strictly_greater_than_policy_age(self) -> None:
        completed = {
            "full_backup": OBSERVED - timedelta(days=8),
            "differential_backup": OBSERVED - timedelta(days=2),
            "pgbackrest_mirror": OBSERVED - timedelta(days=2),
            "pgbackrest_inventory": OBSERVED - timedelta(days=8),
            "pgbackrest_restore": OBSERVED - timedelta(days=35),
        }
        for operation, at in completed.items():
            self.publish_database_receipt(operation, at)
        status = backup_health.evaluate_backup_health(
            snapshot("database"), receipt_root=self.receipts
        )
        self.assertEqual("ok", status["result"])

    def test_failed_missed_wal_stale_and_label_codes_are_allowlisted(self) -> None:
        self.publish_healthy_database()
        document = snapshot("database")
        document["units"]["full_backup"] = {
            "last_result": "failed",
            "missed": True,
        }
        document["wal"] = {
            "failed_count": 2,
            "previous_failed_count": 1,
            "segment_lag": 2,
            "repository_archive_max_behind_database": True,
        }
        document["latest_backup_label"] = "20260825-110000F"
        document["observed_at"] = timestamp(OBSERVED + timedelta(days=9))
        status = backup_health.evaluate_backup_health(
            document, receipt_root=self.receipts
        )
        expected = {
            "full_backup_unit_failed",
            "full_backup_unit_missed",
            "wal_failed_count_growth",
            "wal_segment_lag_exceeded",
            "wal_repository_archive_behind",
            "full_backup_stale",
            "latest_backup_stale",
            "latest_backup_label_mismatch",
            "pgbackrest_mirror_stale",
            "pgbackrest_mirror_label_mismatch",
            "pgbackrest_inventory_stale",
        }
        self.assertTrue(expected.issubset(status["failure_codes"]))
        self.assertTrue(set(status["failure_codes"]).issubset(backup_health.FAILURE_CODES))

    def test_snapshot_latest_label_must_match_latest_accepted_backup(self) -> None:
        self.publish_healthy_database()
        document = snapshot("database")
        document["latest_backup_label"] = "20260825-110000F"
        status = backup_health.evaluate_backup_health(
            document, receipt_root=self.receipts
        )
        self.assertIn("latest_backup_label_mismatch", status["failure_codes"])

    def test_failed_receipts_expose_only_fixed_failure_codes(self) -> None:
        self.publish_healthy_database()
        failed_inventory = operation_receipt(
            "pgbackrest_inventory", completed=OBSERVED
        )
        failed_inventory.update(
            {
                "result": "failed",
                "destination_object_count": 99,
                "divergence_count": 1,
            }
        )
        failed_restore = operation_receipt(
            "pgbackrest_restore", completed=OBSERVED
        )
        failed_restore.update(
            {
                "result": "failed",
                "promotion_verified": False,
                "checksum_verified": False,
                "cleanup_verified": False,
            }
        )
        for document in (failed_inventory, failed_restore):
            backup_health.publish_receipt(document, root=self.receipts, now=OBSERVED)
        status = backup_health.evaluate_backup_health(
            snapshot("database"), receipt_root=self.receipts
        )
        for code in (
            "pgbackrest_inventory_failed",
            "pgbackrest_inventory_divergence",
            "pgbackrest_restore_failed",
            "pgbackrest_restore_promotion_failed",
            "pgbackrest_restore_checksum_failed",
            "pgbackrest_restore_cleanup_failed",
        ):
            self.assertIn(code, status["failure_codes"])

    def test_same_timestamp_failure_outweighs_committed_success(self) -> None:
        self.publish_healthy_objects()
        completed = OBSERVED
        backup_health.publish_receipt(
            operation_receipt("generated_object_mirror", completed=completed),
            root=self.receipts,
            now=completed,
        )
        failed = operation_receipt(
            "generated_object_mirror", completed=completed, result="failed"
        )
        failed["divergence_count"] = 1
        backup_health.publish_receipt(
            failed,
            root=self.receipts,
            now=completed,
        )
        status = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        self.assertIn("generated_object_mirror_failed", status["failure_codes"])
        self.assertIn(
            "generated_object_mirror_divergence", status["failure_codes"]
        )

    def test_future_receipt_beyond_clock_skew_alerts(self) -> None:
        self.publish_healthy_objects()
        future = OBSERVED + timedelta(seconds=backup_health.MAX_FUTURE_SKEW_SECONDS + 1)
        backup_health.publish_receipt(
            operation_receipt("generated_object_mirror", completed=future),
            root=self.receipts,
            now=future,
        )
        status = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        self.assertIn(
            "generated_object_mirror_receipt_future", status["failure_codes"]
        )

    def test_object_freshness_thresholds_alert_only_after_limits(self) -> None:
        mirror_at = OBSERVED - timedelta(
            seconds=backup_health.GENERATED_MIRROR_MAX_AGE_SECONDS + 1
        )
        restore_at = OBSERVED - timedelta(
            seconds=backup_health.GENERATED_RESTORE_MAX_AGE_SECONDS + 1
        )
        backup_health.publish_receipt(
            operation_receipt("generated_object_mirror", completed=mirror_at),
            root=self.receipts,
            now=mirror_at,
        )
        backup_health.publish_receipt(
            operation_receipt("generated_object_restore", completed=restore_at),
            root=self.receipts,
            now=restore_at,
        )
        document = snapshot("objects")
        document["units"]["generated_object_restore"] = {
            "last_result": "never",
            "missed": False,
        }
        status = backup_health.evaluate_backup_health(
            document, receipt_root=self.receipts
        )
        self.assertIn("generated_object_mirror_stale", status["failure_codes"])
        self.assertIn("generated_object_restore_stale", status["failure_codes"])
        self.assertIn(
            "generated_object_restore_unit_never", status["failure_codes"]
        )

    def test_failed_object_restore_uses_only_proven_property_codes(self) -> None:
        self.publish_healthy_objects()
        failed = operation_receipt(
            "generated_object_restore", completed=OBSERVED, result="failed"
        )
        failed.update(
            {
                "manifest_verified": False,
                "ciphertext_verified": False,
                "target_left_intact": False,
            }
        )
        backup_health.publish_receipt(
            failed, root=self.receipts, now=OBSERVED
        )
        status = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        for code in (
            "generated_object_restore_failed",
            "generated_object_restore_manifest_failed",
            "generated_object_restore_ciphertext_failed",
            "generated_object_restore_target_not_retained",
        ):
            self.assertIn(code, status["failure_codes"])
        serialized = json.dumps(status, sort_keys=True)
        self.assertNotIn("decrypt", serialized)
        self.assertNotIn("reconcil", serialized)

    def test_tampered_commit_marker_becomes_integrity_alert(self) -> None:
        self.publish_healthy_objects()
        manifest = next(self.receipts.glob("*-generated_object_mirror-*.sha256"))
        manifest.write_bytes(b"0" * 64 + b"  invalid.json\n")
        chmod_file(manifest)
        status = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        self.assertIn(
            "generated_object_mirror_receipt_invalid", status["failure_codes"]
        )


class StatusAndAlertTests(TemporaryRoots):
    def setUp(self) -> None:
        super().setUp()
        self.publish_healthy_objects()

    def test_status_is_canonical_atomic_and_surface_bound(self) -> None:
        status = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        backup_health.write_status(status, root=self.status_root)
        readback = backup_health.read_status(
            root=self.status_root, expected_surface="objects"
        )
        self.assertEqual(status, readback)
        with self.assertRaises(backup_health.BackupHealthVerificationError):
            backup_health.read_status(
                root=self.status_root, expected_surface="database"
            )
        if os.name == "posix":
            self.assertEqual(0o700, stat.S_IMODE(os.stat(self.status_root).st_mode))
            self.assertEqual(
                0o600,
                stat.S_IMODE(
                    os.stat(
                        self.status_root / backup_health.STATUS_NAMES["objects"]
                    ).st_mode
                ),
            )

    def test_ok_status_cannot_carry_missing_or_stale_ages(self) -> None:
        status = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        missing = json.loads(json.dumps(status))
        missing["ages_seconds"]["generated_object_mirror"] = None
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_status(missing)

        stale = json.loads(json.dumps(status))
        stale["ages_seconds"]["generated_object_mirror"] = (
            backup_health.GENERATED_MIRROR_MAX_AGE_SECONDS + 1
        )
        with self.assertRaises(backup_health.BackupHealthValidationError):
            backup_health.validate_status(stale)

    def test_alert_state_fires_is_idempotent_and_clears(self) -> None:
        missing = backup_health.evaluate_backup_health(
            snapshot("database"), receipt_root=self.receipts
        )
        first = backup_health.persist_alert_state(
            missing, root=self.alert_root, checked_at=OBSERVED
        )
        self.assertEqual("firing", first["state"])
        self.assertEqual("database", first["surface"])
        self.assertEqual(
            first,
            backup_health.persist_alert_state(
                missing, root=self.alert_root, checked_at=OBSERVED
            ),
        )

        later = dict(missing)
        later["evaluated_at"] = timestamp(OBSERVED + timedelta(minutes=1))
        second = backup_health.persist_alert_state(
            later,
            root=self.alert_root,
            checked_at=OBSERVED + timedelta(minutes=1),
        )
        self.assertEqual(2, second["consecutive_alert_evaluations"])
        self.assertEqual(first["first_detected_at"], second["first_detected_at"])

        clear = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        clear["evaluated_at"] = timestamp(OBSERVED + timedelta(minutes=2))
        # Surface-named alert files coexist without overwriting one another.
        cleared = backup_health.persist_alert_state(
            clear,
            root=self.alert_root,
            checked_at=OBSERVED + timedelta(minutes=2),
        )
        self.assertEqual("clear", cleared["state"])
        self.assertEqual([], cleared["failure_codes"])
        self.assertTrue(
            (self.alert_root / backup_health.ALERT_NAMES["database"]).exists()
        )
        self.assertTrue(
            (self.alert_root / backup_health.ALERT_NAMES["objects"]).exists()
        )

    def test_old_and_excessively_future_statuses_synthesize_stale_alert(self) -> None:
        status = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        backup_health.write_status(status, root=self.status_root)
        exact = backup_health.checked_status(
            root=self.status_root,
            surface="objects",
            now=OBSERVED + timedelta(seconds=backup_health.MAX_STATUS_AGE_SECONDS),
        )
        self.assertEqual("ok", exact["result"])
        fractional_stale = backup_health.checked_status(
            root=self.status_root,
            surface="objects",
            now=OBSERVED
            + timedelta(
                seconds=backup_health.MAX_STATUS_AGE_SECONDS,
                microseconds=1,
            ),
        )
        self.assertIn(
            "health_status_stale", fractional_stale["failure_codes"]
        )
        stale = backup_health.checked_status(
            root=self.status_root,
            surface="objects",
            now=OBSERVED
            + timedelta(seconds=backup_health.MAX_STATUS_AGE_SECONDS + 1),
        )
        self.assertEqual("alert", stale["result"])
        self.assertIn("health_status_stale", stale["failure_codes"])

        future_status = dict(status)
        future_status["evaluated_at"] = timestamp(
            OBSERVED + timedelta(seconds=backup_health.MAX_FUTURE_SKEW_SECONDS + 1)
        )
        backup_health.write_status(future_status, root=self.status_root)
        future = backup_health.checked_status(
            root=self.status_root, surface="objects", now=OBSERVED
        )
        self.assertIn("health_status_stale", future["failure_codes"])

    def test_stale_status_drives_persistent_alert(self) -> None:
        status = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        backup_health.write_status(status, root=self.status_root)
        stale = backup_health.checked_status(
            root=self.status_root,
            surface="objects",
            now=OBSERVED
            + timedelta(seconds=backup_health.MAX_STATUS_AGE_SECONDS + 1),
        )
        state = backup_health.persist_alert_state(
            stale,
            root=self.alert_root,
            checked_at=OBSERVED
            + timedelta(seconds=backup_health.MAX_STATUS_AGE_SECONDS + 1),
        )
        self.assertEqual("firing", state["state"])
        self.assertIn("health_status_stale", state["failure_codes"])

    def test_tampered_status_and_alert_state_fail_closed(self) -> None:
        status = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        backup_health.write_status(status, root=self.status_root)
        status_path = self.status_root / backup_health.STATUS_NAMES["objects"]
        status_path.write_bytes(b"{}\n")
        chmod_file(status_path)
        with self.assertRaises(backup_health.BackupHealthVerificationError):
            backup_health.read_status(
                root=self.status_root, expected_surface="objects"
            )

        state = backup_health.persist_alert_state(
            status, root=self.alert_root, checked_at=OBSERVED
        )
        self.assertEqual("clear", state["state"])
        alert_path = self.alert_root / backup_health.ALERT_NAMES["objects"]
        alert_path.write_bytes(b"{}\n")
        chmod_file(alert_path)
        with self.assertRaises(backup_health.BackupHealthVerificationError):
            backup_health.read_alert_state(
                root=self.alert_root, expected_surface="objects"
            )


class CliTests(TemporaryRoots):
    def run_cli(
        self, *arguments: str, document: dict[str, object] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(Path(backup_health.__file__).resolve()), *arguments],
            input=None if document is None else json.dumps(document),
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )

    def test_publish_latest_evaluate_status_and_alert_cli(self) -> None:
        cli_now = datetime.now(UTC).replace(microsecond=0)
        for operation in backup_health.SURFACE_OPERATIONS["objects"]:
            published = self.run_cli(
                "publish",
                "--receipt-root",
                str(self.receipts),
                document=operation_receipt(operation, completed=cli_now),
            )
            self.assertEqual(0, published.returncode, published.stderr)
            output = json.loads(published.stdout)
            self.assertEqual(backup_health.PUBLISH_RESULT_SCHEMA, output["schema"])
            self.assertNotIn("backup_label", output)

        latest = self.run_cli(
            "latest",
            "--operation",
            "generated_object_mirror",
            "--receipt-root",
            str(self.receipts),
        )
        self.assertEqual(0, latest.returncode, latest.stderr)
        self.assertEqual(
            "generated_object_mirror", json.loads(latest.stdout)["operation"]
        )

        evaluated = self.run_cli(
            "evaluate",
            "--receipt-root",
            str(self.receipts),
            "--status-root",
            str(self.status_root),
            document={
                **snapshot("objects"),
                "observed_at": timestamp(cli_now),
            },
        )
        self.assertEqual(0, evaluated.returncode, evaluated.stderr)
        self.assertEqual("ok", json.loads(evaluated.stdout)["result"])

        status = self.run_cli(
            "status",
            "--surface",
            "objects",
            "--status-root",
            str(self.status_root),
        )
        self.assertEqual(0, status.returncode, status.stderr)
        alert = self.run_cli(
            "alert",
            "--surface",
            "objects",
            "--status-root",
            str(self.status_root),
            "--alert-root",
            str(self.alert_root),
        )
        self.assertEqual(0, alert.returncode, alert.stderr)
        self.assertEqual("clear", json.loads(alert.stdout)["state"])

    def test_cli_alert_exit_and_surface_mismatch(self) -> None:
        self.receipts.mkdir(mode=0o700)
        evaluated = self.run_cli(
            "evaluate",
            "--receipt-root",
            str(self.receipts),
            "--status-root",
            str(self.status_root),
            document={
                **snapshot("objects"),
                "observed_at": timestamp(datetime.now(UTC)),
            },
        )
        self.assertEqual(1, evaluated.returncode)
        status = self.run_cli(
            "status",
            "--surface",
            "objects",
            "--status-root",
            str(self.status_root),
        )
        self.assertEqual(1, status.returncode)
        mismatch = self.run_cli(
            "status",
            "--surface",
            "database",
            "--status-root",
            str(self.status_root),
        )
        self.assertEqual(2, mismatch.returncode)
        self.assertEqual(
            "Backup-health evidence is not committed or is invalid.",
            mismatch.stderr.strip(),
        )

    def test_cli_surface_named_status_prevents_surface_confusion(self) -> None:
        self.receipts.mkdir(mode=0o700)
        evaluated = self.run_cli(
            "evaluate",
            "--receipt-root",
            str(self.receipts),
            "--status-root",
            str(self.status_root),
            document=snapshot("objects"),
        )
        self.assertEqual(1, evaluated.returncode)
        self.assertTrue(
            (self.status_root / backup_health.STATUS_NAMES["objects"]).exists()
        )
        self.assertFalse(
            (self.status_root / backup_health.STATUS_NAMES["database"]).exists()
        )

    def test_latest_distinguishes_absent_from_invalid(self) -> None:
        self.receipts.mkdir(mode=0o700)
        absent = self.run_cli(
            "latest",
            "--operation",
            "full_backup",
            "--receipt-root",
            str(self.receipts),
        )
        self.assertEqual(1, absent.returncode)
        self.assertEqual("Backup-health evidence is absent.", absent.stderr.strip())

        receipt_name = "20260825T120000Z-full_backup-0000000000000004.json"
        manifest = self.receipts / (receipt_name[:-5] + ".sha256")
        manifest.write_bytes(f"{'0' * 64}  {receipt_name}\n".encode("ascii"))
        chmod_file(manifest)
        invalid = self.run_cli(
            "latest",
            "--operation",
            "full_backup",
            "--receipt-root",
            str(self.receipts),
        )
        self.assertEqual(2, invalid.returncode)

    def test_cli_status_and_alert_reject_old_green_status(self) -> None:
        self.publish_healthy_objects()
        status_document = backup_health.evaluate_backup_health(
            snapshot("objects"), receipt_root=self.receipts
        )
        status_document["evaluated_at"] = timestamp(
            datetime.now(UTC)
            - timedelta(seconds=backup_health.MAX_STATUS_AGE_SECONDS + 2)
        )
        backup_health.write_status(status_document, root=self.status_root)

        status_result = self.run_cli(
            "status",
            "--surface",
            "objects",
            "--status-root",
            str(self.status_root),
        )
        self.assertEqual(1, status_result.returncode, status_result.stderr)
        self.assertIn(
            "health_status_stale", json.loads(status_result.stdout)["failure_codes"]
        )
        alert_result = self.run_cli(
            "alert",
            "--surface",
            "objects",
            "--status-root",
            str(self.status_root),
            "--alert-root",
            str(self.alert_root),
        )
        self.assertEqual(1, alert_result.returncode, alert_result.stderr)
        alert = json.loads(alert_result.stdout)
        self.assertEqual("firing", alert["state"])
        self.assertIn("health_status_stale", alert["failure_codes"])

    def test_cli_validation_error_never_echoes_rejected_input(self) -> None:
        document = operation_receipt("full_backup")
        document["token"] = "sensitive-value"
        rejected = self.run_cli(
            "publish",
            "--receipt-root",
            str(self.receipts),
            document=document,
        )
        self.assertEqual(2, rejected.returncode)
        self.assertEqual("Backup-health document is invalid.", rejected.stderr.strip())
        self.assertNotIn("token", rejected.stderr)
        self.assertNotIn("sensitive-value", rejected.stderr)

        bad_choice = self.run_cli(
            "latest",
            "--operation",
            "sensitive-value",
            "--receipt-root",
            str(self.receipts),
        )
        self.assertEqual(2, bad_choice.returncode)
        self.assertEqual("Backup-health input is invalid.", bad_choice.stderr.strip())
        self.assertNotIn("sensitive-value", bad_choice.stderr)


if __name__ == "__main__":
    unittest.main()
