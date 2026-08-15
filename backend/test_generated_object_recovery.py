import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import generated_object_recovery as recovery


RELEASE = "a" * 40
BACKUP_LABEL = "20260815-120000F"
SNAPSHOT_ID = f"{RELEASE}-{BACKUP_LABEL}"


class GeneratedObjectRecoveryTests(unittest.TestCase):
    def _source(self, parent: Path, objects: dict[str, bytes]) -> Path:
        root = parent / "objects"
        version_directory = root / "v1"
        version_directory.mkdir(parents=True)
        for filename, ciphertext in objects.items():
            (version_directory / filename).write_bytes(ciphertext)
        return root

    def _create(
        self,
        parent: Path,
        objects: dict[str, bytes] | None = None,
        **bounds: int,
    ) -> tuple[Path, Path, recovery.GeneratedObjectManifest]:
        if objects is None:
            objects = {
                "00000000000000000000000000000002": b"ciphertext-two",
                "00000000000000000000000000000001": b"ciphertext-one",
            }
        root = self._source(parent, objects)
        manifest_path = parent / "snapshot-manifest.json"
        manifest = recovery.create_manifest(
            root,
            RELEASE,
            BACKUP_LABEL,
            SNAPSHOT_ID,
            manifest_path,
            **bounds,
        )
        return root, manifest_path, manifest

    @staticmethod
    def _write_document(path: Path, document: object) -> str:
        serialized = json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        path.write_bytes(serialized)
        return hashlib.sha256(serialized).hexdigest()

    def _assert_failure(self, callable_object, *args, **kwargs) -> None:
        with self.assertRaises(recovery.GeneratedObjectRecoveryError) as raised:
            callable_object(*args, **kwargs)
        self.assertEqual(str(raised.exception), recovery.MANIFEST_ERROR_MESSAGE)

    def test_create_is_deterministic_canonical_and_verify_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root, manifest_path, manifest = self._create(parent)
            second_path = parent / "second-manifest.json"
            second = recovery.create_manifest(
                root,
                RELEASE,
                BACKUP_LABEL,
                SNAPSHOT_ID,
                second_path,
            )

            self.assertEqual(manifest_path.read_bytes(), second_path.read_bytes())
            self.assertEqual(manifest.digest(), second.digest())
            self.assertEqual(manifest.object_count, 2)
            self.assertEqual(manifest.ciphertext_bytes, 28)
            document = json.loads(manifest_path.read_bytes())
            self.assertEqual(
                [entry["object_key"] for entry in document["objects"]],
                [
                    "v1/00000000000000000000000000000001",
                    "v1/00000000000000000000000000000002",
                ],
            )
            self.assertEqual(
                document["objects"][0]["sha256"],
                hashlib.sha256(b"ciphertext-one").hexdigest(),
            )
            self.assertEqual(
                recovery.verify_manifest(root, manifest_path, manifest.digest()),
                manifest,
            )

    def test_empty_inventory_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "empty"
            root.mkdir()
            manifest_path = parent / "empty-manifest.json"
            manifest = recovery.create_manifest(
                root,
                RELEASE,
                BACKUP_LABEL,
                SNAPSHOT_ID,
                manifest_path,
            )

            self.assertEqual(manifest.object_count, 0)
            self.assertEqual(manifest.ciphertext_bytes, 0)
            self.assertEqual(
                recovery.verify_manifest(root, manifest_path, manifest.digest()),
                manifest,
            )

    def test_existing_manifest_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = self._source(
                parent,
                {"0" * 32: b"ciphertext"},
            )
            manifest_path = parent / "snapshot-manifest.json"
            manifest_path.write_bytes(b"preserve-existing")

            self._assert_failure(
                recovery.create_manifest,
                root,
                RELEASE,
                BACKUP_LABEL,
                SNAPSHOT_ID,
                manifest_path,
            )
            self.assertEqual(manifest_path.read_bytes(), b"preserve-existing")

    def test_identity_fields_are_strict(self) -> None:
        invalid_values = (
            ("A" * 40, BACKUP_LABEL, SNAPSHOT_ID),
            (RELEASE, "20260815-120000D", SNAPSHOT_ID),
            (RELEASE, BACKUP_LABEL, "contains/slash"),
            (RELEASE, BACKUP_LABEL, ""),
        )
        for index, (release, backup_label, snapshot_id) in enumerate(invalid_values):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary)
                root = self._source(parent, {"0" * 32: b"ciphertext"})
                self._assert_failure(
                    recovery.create_manifest,
                    root,
                    release,
                    backup_label,
                    snapshot_id,
                    parent / "manifest.json",
                )

    def test_inventory_rejects_noncanonical_and_nested_content(self) -> None:
        invalid_names = (
            "A" * 32,
            "0" * 31,
            "0" * 32 + ".tmp",
            "nested",
        )
        for name in invalid_names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary)
                root = self._source(parent, {})
                candidate = root / "v1" / name
                if name == "nested":
                    candidate.mkdir()
                else:
                    candidate.write_bytes(b"ciphertext")
                self._assert_failure(
                    recovery.create_manifest,
                    root,
                    RELEASE,
                    BACKUP_LABEL,
                    SNAPSHOT_ID,
                    parent / "manifest.json",
                )

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = self._source(parent, {"0" * 32: b"ciphertext"})
            (root / "unexpected").write_bytes(b"not-an-object")
            self._assert_failure(
                recovery.create_manifest,
                root,
                RELEASE,
                BACKUP_LABEL,
                SNAPSHOT_ID,
                parent / "manifest.json",
            )

    def test_inventory_rejects_zero_length_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = self._source(parent, {"0" * 32: b""})
            self._assert_failure(
                recovery.create_manifest,
                root,
                RELEASE,
                BACKUP_LABEL,
                SNAPSHOT_ID,
                parent / "manifest.json",
            )

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = self._source(parent, {})
            oversized = root / "v1" / ("0" * 32)
            with oversized.open("wb") as destination:
                destination.truncate(recovery.MAXIMUM_CIPHERTEXT_BYTES + 1)
            self._assert_failure(
                recovery.create_manifest,
                root,
                RELEASE,
                BACKUP_LABEL,
                SNAPSHOT_ID,
                parent / "manifest.json",
            )

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = self._source(
                parent,
                {"0" * 32: b"one", "1" * 32: b"two"},
            )
            self._assert_failure(
                recovery.create_manifest,
                root,
                RELEASE,
                BACKUP_LABEL,
                SNAPSHOT_ID,
                parent / "manifest.json",
                maximum_objects=1,
            )

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = self._source(parent, {"0" * 32: b"ciphertext"})
            self._assert_failure(
                recovery.create_manifest,
                root,
                RELEASE,
                BACKUP_LABEL,
                SNAPSHOT_ID,
                parent / "manifest.json",
                maximum_bytes=9,
            )

    def test_verify_rejects_missing_extra_and_corrupt_objects(self) -> None:
        operations = ("missing", "extra", "corrupt")
        for operation in operations:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary)
                root, manifest_path, manifest = self._create(
                    parent,
                    {"0" * 32: b"ciphertext"},
                )
                if operation == "missing":
                    (root / "v1" / ("0" * 32)).unlink()
                elif operation == "extra":
                    (root / "v1" / ("1" * 32)).write_bytes(b"extra")
                else:
                    (root / "v1" / ("0" * 32)).write_bytes(b"corrupttex")
                self._assert_failure(
                    recovery.verify_manifest,
                    root,
                    manifest_path,
                    manifest.digest(),
                )

    def test_verify_rejects_traversal_duplicate_and_noncanonical_manifest(self) -> None:
        mutations = ("traversal", "duplicate", "whitespace")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary)
                root, manifest_path, _manifest = self._create(
                    parent,
                    {"0" * 32: b"ciphertext"},
                )
                document = json.loads(manifest_path.read_bytes())
                if mutation == "traversal":
                    document["objects"][0]["object_key"] = "v1/../ciphertext"
                    digest = self._write_document(manifest_path, document)
                elif mutation == "duplicate":
                    document["objects"].append(dict(document["objects"][0]))
                    document["object_count"] = 2
                    document["ciphertext_bytes"] *= 2
                    digest = self._write_document(manifest_path, document)
                else:
                    serialized = json.dumps(document, indent=2).encode("ascii")
                    manifest_path.write_bytes(serialized)
                    digest = hashlib.sha256(serialized).hexdigest()
                self._assert_failure(
                    recovery.verify_manifest,
                    root,
                    manifest_path,
                    digest,
                )

    def test_verify_rejects_wrong_digest_and_caller_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, manifest_path, manifest = self._create(Path(temporary))
            self._assert_failure(
                recovery.verify_manifest,
                root,
                manifest_path,
                "0" * 64,
            )
            self._assert_failure(
                recovery.verify_manifest,
                root,
                manifest_path,
                manifest.digest(),
                maximum_objects=1,
            )
            self._assert_failure(
                recovery.verify_manifest,
                root,
                manifest_path,
                manifest.digest(),
                maximum_bytes=1,
            )

            oversized_manifest = Path(temporary) / "oversized-manifest.json"
            with oversized_manifest.open("wb") as destination:
                destination.truncate(recovery.MAXIMUM_MANIFEST_BYTES + 1)
            self._assert_failure(
                recovery.verify_manifest,
                root,
                oversized_manifest,
                manifest.digest(),
            )

    def test_verify_rejects_manifest_replacement_between_lstat_and_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root, manifest_path, manifest = self._create(parent)
            replacement = parent / "replacement.json"
            replacement.write_bytes(manifest_path.read_bytes())
            displaced = parent / "displaced.json"
            original_metadata_reader = recovery._regular_file_metadata
            replaced = False

            def replace_after_lstat(path: Path):
                nonlocal replaced
                metadata = original_metadata_reader(path)
                if path == manifest_path and not replaced:
                    manifest_path.rename(displaced)
                    replacement.rename(manifest_path)
                    replaced = True
                return metadata

            with mock.patch.object(
                recovery,
                "_regular_file_metadata",
                side_effect=replace_after_lstat,
            ):
                self._assert_failure(
                    recovery.verify_manifest,
                    root,
                    manifest_path,
                    manifest.digest(),
                )
            self.assertTrue(replaced)

    def test_symlinks_are_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            real_root = self._source(parent, {"0" * 32: b"ciphertext"})
            linked_root = parent / "linked-root"
            try:
                os.symlink(real_root, linked_root, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("creating symlinks is unavailable")
            self._assert_failure(
                recovery.create_manifest,
                linked_root,
                RELEASE,
                BACKUP_LABEL,
                SNAPSHOT_ID,
                parent / "manifest.json",
            )

            linked_object = real_root / "v1" / ("1" * 32)
            os.symlink(real_root / "v1" / ("0" * 32), linked_object)
            self._assert_failure(
                recovery.create_manifest,
                real_root,
                RELEASE,
                BACKUP_LABEL,
                SNAPSHOT_ID,
                parent / "object-link-manifest.json",
            )
            linked_object.unlink()

            real_manifest = parent / "real-manifest.json"
            manifest = recovery.create_manifest(
                real_root,
                RELEASE,
                BACKUP_LABEL,
                SNAPSHOT_ID,
                real_manifest,
            )
            linked_manifest = parent / "linked-manifest.json"
            os.symlink(real_manifest, linked_manifest)
            self._assert_failure(
                recovery.verify_manifest,
                real_root,
                linked_manifest,
                manifest.digest(),
            )

    def test_cli_emits_sanitized_one_line_receipts_and_fixed_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            object_name = "f" * 32
            payload = b"highly-sensitive-ciphertext-marker"
            root = self._source(parent, {object_name: payload})
            manifest_path = parent / "snapshot-manifest.json"

            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                create_result = recovery.main(
                    [
                        "create-manifest",
                        str(root),
                        RELEASE,
                        BACKUP_LABEL,
                        SNAPSHOT_ID,
                        str(manifest_path),
                    ]
                )
            self.assertEqual(create_result, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(stdout.getvalue().count("\n"), 1)
            create_receipt = json.loads(stdout.getvalue())
            self.assertEqual(
                set(create_receipt),
                {
                    "backup_label",
                    "ciphertext_bytes",
                    "manifest_sha256",
                    "manifest_verified",
                    "object_count",
                    "release",
                    "result",
                    "snapshot_id",
                },
            )
            self.assertNotIn(object_name, stdout.getvalue())
            self.assertNotIn(str(root), stdout.getvalue())
            self.assertNotIn(payload.decode("ascii"), stdout.getvalue())

            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                verify_result = recovery.main(
                    [
                        "verify-manifest",
                        str(root),
                        str(manifest_path),
                        create_receipt["manifest_sha256"],
                    ]
                )
            self.assertEqual(verify_result, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue()), create_receipt)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                failure_result = recovery.main(
                    [
                        "verify-manifest",
                        str(root),
                        str(manifest_path),
                        "0" * 64,
                    ]
                )
            self.assertEqual(failure_result, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(),
                recovery.MANIFEST_ERROR_MESSAGE + "\n",
            )
            self.assertNotIn(str(root), stderr.getvalue())
            self.assertNotIn(object_name, stderr.getvalue())

            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                malformed_result = recovery.main(["create-manifest"])
            self.assertEqual(malformed_result, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(),
                recovery.MANIFEST_ERROR_MESSAGE + "\n",
            )


if __name__ == "__main__":
    unittest.main()
