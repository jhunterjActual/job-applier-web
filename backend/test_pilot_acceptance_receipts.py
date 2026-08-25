import errno
import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pilot_acceptance_receipts as receipts


class SimulatedInterruption(BaseException):
    pass


def valid_document():
    return {
        "schema": receipts.SCHEMA,
        "operation": "two_user_acceptance",
        "result": "ok",
        "release": "a" * 40,
        "build": "20260813.39",
        "started_at": "2026-08-25T18:00:00Z",
        "completed_at": "2026-08-25T18:04:00Z",
        "subject_kind": "pilot",
        "http_status_class": "2xx",
        "checks": {
            "invitation_only": True,
            "cross_tenant_exposed": False,
            "pooled_context_cleared": True,
            "forbidden_field_present": False,
        },
        "counts": {
            "active_owner_count": 1,
            "active_pilot_count": 1,
            "tested_tenant_count": 2,
            "forbidden_field_match_count": 0,
        },
        "notes": ["synthetic_data_only", "basic_auth_retained"],
    }


class ReceiptValidationTests(unittest.TestCase):
    def test_serialization_is_canonical_and_newline_terminated(self):
        first = receipts.serialize_receipt(valid_document())
        reordered = dict(reversed(list(valid_document().items())))
        second = receipts.serialize_receipt(reordered)

        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))
        parsed = json.loads(first)
        self.assertEqual(parsed["notes"], sorted(parsed["notes"]))
        self.assertEqual(list(parsed["checks"]), sorted(parsed["checks"]))

    def test_optional_bounded_fields_may_be_omitted(self):
        document = valid_document()
        for field in ("http_status_class", "counts", "notes"):
            document.pop(field)
        self.assertEqual(receipts.validate_receipt(document)["result"], "ok")

    def test_rejects_unknown_top_level_and_mapping_keys_without_echo(self):
        for mutation in (
            lambda item: item.update({"unreviewed_free_text": "not_allowed"}),
            lambda item: item["checks"].update(
                {"private_identity_identifier": True}
            ),
            lambda item: item["counts"].update({"private_object_key": 1}),
        ):
            document = valid_document()
            mutation(document)
            with self.assertRaises(receipts.ReceiptValidationError) as caught:
                receipts.serialize_receipt(document)
            self.assertEqual(
                str(caught.exception), "Pilot acceptance receipt is invalid."
            )

    def test_rejects_non_boolean_checks_and_non_integer_counts(self):
        invalid_values = (1, 0, "true", None, [], {})
        for value in invalid_values:
            document = valid_document()
            document["checks"]["invitation_only"] = value
            with self.assertRaises(receipts.ReceiptValidationError):
                receipts.validate_receipt(document)

        invalid_counts = (-1, True, 1.5, "1", 1_000_001)
        for value in invalid_counts:
            document = valid_document()
            document["counts"]["tested_tenant_count"] = value
            with self.assertRaises(receipts.ReceiptValidationError):
                receipts.validate_receipt(document)

    def test_rejects_free_text_or_duplicate_notes(self):
        for notes in (
            ["synthetic_data_only", "friend_name"],
            ["synthetic_data_only", "synthetic_data_only"],
            "synthetic_data_only",
            [],
        ):
            document = valid_document()
            document["notes"] = notes
            with self.assertRaises(receipts.ReceiptValidationError):
                receipts.validate_receipt(document)

    def test_rejects_invalid_enums_identifiers_and_times(self):
        cases = {
            "operation": "custom-operation",
            "result": "passed with comment",
            "release": "A" * 40,
            "build": "latest",
            "subject_kind": "person-123",
            "http_status_class": "403",
            "started_at": "2026-08-25T18:00:00+00:00",
        }
        for field, value in cases.items():
            document = valid_document()
            document[field] = value
            with self.assertRaises(receipts.ReceiptValidationError):
                receipts.validate_receipt(document)

        document = valid_document()
        document["completed_at"] = "2026-08-25T17:59:59Z"
        with self.assertRaises(receipts.ReceiptValidationError):
            receipts.validate_receipt(document)

    def test_rejects_non_string_enum_values_with_sanitized_errors(self):
        for field in ("operation", "result", "subject_kind", "http_status_class"):
            for value in ([], {}, 1, True, None):
                document = valid_document()
                document[field] = value
                with self.assertRaises(receipts.ReceiptValidationError) as caught:
                    receipts.validate_receipt(document)
                self.assertEqual(
                    str(caught.exception),
                    "Pilot acceptance receipt is invalid.",
                )

    def test_manifest_commits_exact_canonical_receipt(self):
        receipt_name = (
            "20260825T180000Z-two_user_acceptance-" + "0" * 16 + ".json"
        )
        payload = receipts.serialize_receipt(valid_document())
        manifest = (
            f"{hashlib.sha256(payload).hexdigest()}  {receipt_name}\n"
        ).encode("ascii")

        verified = receipts.verify_receipt(receipt_name, payload, manifest)

        self.assertEqual(verified, receipts.validate_receipt(valid_document()))

    def test_json_without_manifest_is_nonterminal(self):
        receipt_name = (
            "20260825T180000Z-two_user_acceptance-" + "0" * 16 + ".json"
        )
        payload = receipts.serialize_receipt(valid_document())

        with self.assertRaises(receipts.ReceiptVerificationError) as caught:
            receipts.verify_receipt(receipt_name, payload, None)

        self.assertEqual(
            str(caught.exception),
            "Pilot acceptance receipt is not committed or is invalid.",
        )

    def test_manifest_must_bind_exact_name_digest_and_canonical_payload(self):
        receipt_name = (
            "20260825T180000Z-two_user_acceptance-" + "0" * 16 + ".json"
        )
        payload = receipts.serialize_receipt(valid_document())
        other_name = (
            "20260825T180000Z-two_user_acceptance-" + "1" * 16 + ".json"
        )
        noncanonical = json.dumps(valid_document(), indent=2).encode("ascii")
        cases = (
            (payload, f"{'0' * 64}  {receipt_name}\n".encode("ascii")),
            (
                payload,
                f"{hashlib.sha256(payload).hexdigest()}  {other_name}\n".encode(
                    "ascii"
                ),
            ),
            (
                noncanonical,
                f"{hashlib.sha256(noncanonical).hexdigest()}  {receipt_name}\n".encode(
                    "ascii"
                ),
            ),
        )
        for candidate_payload, candidate_manifest in cases:
            with self.assertRaises(receipts.ReceiptVerificationError):
                receipts.verify_receipt(
                    receipt_name, candidate_payload, candidate_manifest
                )


@unittest.skipUnless(os.name == "posix", "POSIX descriptor semantics required")
class ReceiptPersistenceTests(unittest.TestCase):
    def test_exclusive_committed_receipt_and_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "receipts"
            created = receipts.write_receipt(valid_document(), root=root)

            receipt_path = root / created.receipt_name
            manifest_path = root / created.manifest_name
            payload = receipt_path.read_bytes()
            expected_manifest = (
                f"{hashlib.sha256(payload).hexdigest()}  {created.receipt_name}\n"
            ).encode("ascii")

            self.assertEqual(manifest_path.read_bytes(), expected_manifest)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o600)
            self.assertEqual(
                {path.name for path in root.iterdir()},
                {created.receipt_name, created.manifest_name},
            )
            self.assertEqual(
                receipts.read_receipt(created.manifest_name, root=root),
                receipts.validate_receipt(valid_document()),
            )
            with self.assertRaises(receipts.ReceiptVerificationError):
                receipts.read_receipt(created.receipt_name, root=root)

    def test_directory_sync_orders_receipt_before_manifest_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "receipts"
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            events = []
            original_link = receipts._link_exclusive
            original_fsync = receipts.os.fsync

            def record_link(directory_fd, source, target):
                original_link(directory_fd, source, target)
                if target.endswith(".json"):
                    events.append("receipt_published")
                elif target.endswith(".sha256"):
                    events.append("manifest_published")

            def record_fsync(descriptor):
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    events.append("directory_synced")
                return original_fsync(descriptor)

            with mock.patch.object(
                receipts, "_link_exclusive", side_effect=record_link
            ), mock.patch.object(receipts.os, "fsync", side_effect=record_fsync):
                receipts.write_receipt(valid_document(), root=root)

            self.assertEqual(
                events,
                [
                    "receipt_published",
                    "directory_synced",
                    "manifest_published",
                    "directory_synced",
                ],
            )

    def test_name_collision_never_overwrites_existing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "receipts"
            real_datetime = receipts.datetime
            fixed_time = mock.Mock(wraps=real_datetime)
            fixed_time.now.return_value = real_datetime(
                2026, 8, 25, 18, 0, 0, tzinfo=receipts.timezone.utc
            )
            with mock.patch.object(receipts, "datetime", fixed_time), mock.patch.object(
                receipts.secrets, "token_hex", return_value="0" * 16
            ):
                first = receipts.write_receipt(valid_document(), root=root)
                before_receipt = (root / first.receipt_name).read_bytes()
                before_manifest = (root / first.manifest_name).read_bytes()
                with self.assertRaises(receipts.ReceiptPersistenceError):
                    receipts.write_receipt(valid_document(), root=root)

            self.assertEqual((root / first.receipt_name).read_bytes(), before_receipt)
            self.assertEqual((root / first.manifest_name).read_bytes(), before_manifest)

    def test_rejects_symlink_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            real = parent / "real"
            real.mkdir(mode=0o700)
            link = parent / "receipts"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(receipts.ReceiptPersistenceError):
                receipts.write_receipt(valid_document(), root=link)

    def test_manifest_failure_does_not_leave_a_partial_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "receipts"
            original_link = receipts._link_exclusive
            call_count = 0

            def fail_second_link(directory_fd, source, target):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError(errno.EIO, "synthetic failure")
                return original_link(directory_fd, source, target)

            with mock.patch.object(
                receipts, "_link_exclusive", side_effect=fail_second_link
            ):
                with self.assertRaises(receipts.ReceiptPersistenceError):
                    receipts.write_receipt(valid_document(), root=root)
            self.assertEqual(list(root.iterdir()), [])

    def test_interruption_after_receipt_publication_is_nonterminal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "receipts"
            real_datetime = receipts.datetime
            fixed_time = mock.Mock(wraps=real_datetime)
            fixed_time.now.return_value = real_datetime(
                2026, 8, 25, 18, 0, 0, tzinfo=receipts.timezone.utc
            )
            original_link = receipts._link_exclusive
            call_count = 0

            def interrupt_second_link(directory_fd, source, target):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise SimulatedInterruption()
                return original_link(directory_fd, source, target)

            with mock.patch.object(receipts, "datetime", fixed_time), mock.patch.object(
                receipts.secrets, "token_hex", return_value="0" * 16
            ), mock.patch.object(
                receipts, "_link_exclusive", side_effect=interrupt_second_link
            ):
                with self.assertRaises(SimulatedInterruption):
                    receipts.write_receipt(valid_document(), root=root)

            stem = "20260825T180000Z-two_user_acceptance-" + "0" * 16
            self.assertEqual(
                {path.name for path in root.iterdir()},
                {f"{stem}.json"},
            )
            with self.assertRaises(receipts.ReceiptVerificationError):
                receipts.read_receipt(f"{stem}.sha256", root=root)

    def test_rejects_permissive_existing_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "receipts"
            root.mkdir(mode=0o755)
            root.chmod(0o755)
            with self.assertRaises(receipts.ReceiptPersistenceError):
                receipts.write_receipt(valid_document(), root=root)


if __name__ == "__main__":
    unittest.main()
