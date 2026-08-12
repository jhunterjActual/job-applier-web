"""Focused tests for encrypted generated-object workspace imports."""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from object_store import InMemoryObjectBackend, ObjectKeyring, stage_object
import sqlite_postgres_import as importer


class GeneratedObjectImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.keyring = ObjectKeyring("object-test-1", {"object-test-1": b"k" * 32})

    def _workspace(self, root: str) -> tuple[Path, Path]:
        database_path = Path(root) / "workspace.db"
        database_path.touch()
        output_path = Path(root) / "output"
        output_path.mkdir()
        return database_path, output_path

    def test_generated_paths_are_direct_bounded_typed_output_children(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path, output_path = self._workspace(temporary_directory)
            resume_path = output_path / "resume.pdf"
            resume_path.write_bytes(b"%PDF-synthetic")
            self.assertEqual(
                resume_path.resolve(),
                importer._validated_generated_file(
                    database_path, resume_path, "tailored_resume_path"
                ),
            )
            self.assertEqual(
                resume_path.resolve(),
                importer._validated_generated_file(
                    database_path, Path("output") / "resume.pdf", "tailored_resume_path"
                ),
            )

            nested_path = output_path / "nested" / "resume.pdf"
            nested_path.parent.mkdir()
            nested_path.write_bytes(b"%PDF-synthetic")
            with self.assertRaises(importer.SQLiteImportError):
                importer._validated_generated_file(
                    database_path, nested_path, "tailored_resume_path"
                )

            wrong_type = output_path / "resume.txt"
            wrong_type.write_bytes(b"%PDF-synthetic")
            with self.assertRaises(importer.SQLiteImportError):
                importer._validated_generated_file(
                    database_path, wrong_type, "tailored_resume_path"
                )

            outside_path = Path(temporary_directory) / "outside.pdf"
            outside_path.write_bytes(b"%PDF-synthetic")
            with self.assertRaises(importer.SQLiteImportError):
                importer._validated_generated_file(
                    database_path, outside_path, "tailored_resume_path"
                )

    def test_application_files_are_encrypted_staged_and_mapped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path, output_path = self._workspace(temporary_directory)
            resume_path = output_path / "resume.pdf"
            letter_path = output_path / "letter.txt"
            resume_path.write_bytes(b"%PDF-private synthetic resume")
            letter_path.write_text("Private synthetic letter", encoding="utf-8")
            backend = InMemoryObjectBackend()
            executions: list[dict[str, object]] = []
            target = SimpleNamespace(
                execute=lambda _statement, values: executions.append(values)
            )
            staged: list[object] = []
            snapshots = {
                # Production preflight snapshots the canonical path returned by
                # _validated_generated_file(). Windows runners can expand the
                # temporary-directory path while resolving it, so the fixture
                # must preserve that same boundary.
                (1, column): importer._file_snapshot(path.resolve(), path.stat())
                for column, path in (
                    ("tailored_resume_path", resume_path),
                    ("cover_letter_path", letter_path),
                )
            }

            references = importer._stage_application_objects(
                database_path,
                {
                    "id": 1,
                    "tailored_resume_path": str(resume_path),
                    "cover_letter_path": str(letter_path),
                },
                target,
                41,
                object_backend=backend,
                object_keyring=self.keyring,
                staged_objects=staged,
                generated_file_snapshots=snapshots,
            )

            self.assertEqual(2, len(staged))
            self.assertEqual(2, len(backend.objects))
            self.assertEqual(staged[0].metadata.id, references["tailored_resume_object_id"])
            self.assertEqual(staged[1].metadata.id, references["cover_letter_object_id"])
            self.assertEqual(41, executions[0]["owner"])
            self.assertEqual(32, len(executions[0]["sha256"]))
            self.assertNotIn(str(resume_path), repr(executions))
            self.assertNotIn(str(letter_path), repr(executions))

            importer._discard_staged_objects(backend, staged)
            self.assertEqual({}, backend.objects)

    def test_changed_file_snapshot_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path, output_path = self._workspace(temporary_directory)
            resume_path = output_path / "resume.pdf"
            resume_path.write_bytes(b"%PDF-original")
            snapshot = importer._file_snapshot(resume_path.resolve(), resume_path.stat())
            replacement = output_path / "replacement.pdf"
            replacement.write_bytes(b"%PDF-replaced")
            replacement.replace(resume_path)
            with self.assertRaisesRegex(importer.SQLiteImportError, "changed|regular file"):
                importer._read_generated_file(
                    database_path,
                    resume_path,
                    "tailored_resume_path",
                    snapshot,
                )

    def test_windows_snapshot_ignores_inconsistent_creation_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "object.pdf"
            path.write_bytes(b"%PDF-synthetic")
            metadata = path.stat()
            with patch.object(importer.os, "name", "nt"):
                snapshot = importer._file_snapshot(path.resolve(), metadata)
            self.assertEqual(0, snapshot.changed_ns)
            self.assertEqual(metadata.st_mtime_ns, snapshot.modified_ns)

    def test_expected_counts_include_objects_and_explicit_omission(self) -> None:
        manifest = importer.ImportManifest(
            counts={table: index + 1 for index, table in enumerate(importer.SOURCE_TABLES)},
            provider_secret_fields=3,
            generated_file_paths=2,
        )
        imported = importer._expected_target_counts(
            manifest,
            omit_provider_secrets=False,
            omit_generated_files=False,
        )
        omitted = importer._expected_target_counts(
            manifest,
            omit_provider_secrets=True,
            omit_generated_files=True,
        )
        self.assertEqual(2, imported["stored_objects"])
        self.assertEqual(3, imported["provider_secrets"])
        self.assertEqual(0, omitted["stored_objects"])
        self.assertEqual(0, omitted["provider_secrets"])
        for source_table, target_table in importer.SOURCE_TO_TARGET.items():
            self.assertEqual(manifest.counts[source_table], imported[target_table])

    def test_import_failure_rolls_back_and_discards_uploaded_objects(self) -> None:
        backend = InMemoryObjectBackend()
        staged = stage_object(
            backend,
            73,
            b"%PDF-synthetic",
            "application/pdf",
            keyring=self.keyring,
        )

        class Source:
            def execute(self, _statement):
                return None

            def rollback(self):
                return None

            def close(self):
                return None

        class Transaction:
            is_active = True
            rolled_back = False

            def rollback(self):
                self.rolled_back = True
                self.is_active = False

            def commit(self):
                raise AssertionError("The failing import must not commit.")

        transaction = Transaction()

        class Target:
            def begin(self):
                return transaction

            def exec_driver_sql(self, _statement):
                return None

            def execute(self, *_args, **_kwargs):
                return None

            def commit(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Engine:
            def connect(self):
                return Target()

        manifest = importer.ImportManifest(
            counts={table: 0 for table in importer.SOURCE_TABLES},
            provider_secret_fields=0,
            generated_file_paths=0,
        )

        def fail_after_upload(*_args, staged_objects, **_kwargs):
            staged_objects.append(staged)
            raise RuntimeError("synthetic database failure")

        with (
            patch.object(importer, "_open_source", return_value=Source()),
            patch.object(importer, "inspect_sqlite_workspace", return_value=manifest),
            patch.object(importer, "_seed_owner", return_value=73),
            patch.object(importer, "_target_must_be_empty"),
            patch.object(importer, "set_tenant_context"),
            patch.object(importer, "_import_rows", side_effect=fail_after_upload),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic database failure"):
                importer.import_sqlite_workspace(
                    Path("synthetic.db"),
                    "user_01SYNTHETICOWNER",
                    engine=Engine(),
                    object_backend=backend,
                    object_keyring=self.keyring,
                )

        self.assertTrue(transaction.rolled_back)
        self.assertEqual({}, backend.objects)

    def test_ambiguous_commit_retains_objects_for_reconciliation(self) -> None:
        backend = InMemoryObjectBackend()
        staged = stage_object(
            backend, 73, b"%PDF-synthetic", "application/pdf", keyring=self.keyring
        )

        class Source:
            def execute(self, _statement): return None
            def rollback(self): return None
            def close(self): return None

        class Transaction:
            is_active = True
            def rollback(self): self.is_active = False
            def commit(self):
                self.is_active = False
                raise ConnectionError("synthetic lost commit acknowledgement")

        class Target:
            def __init__(self): self.transaction = Transaction()
            def begin(self): return self.transaction
            def exec_driver_sql(self, _statement): return None
            def execute(self, *_args, **_kwargs): return None
            def invalidate(self): return None
            def __enter__(self): return self
            def __exit__(self, *_args): return False

        class Engine:
            def connect(self): return Target()

        manifest = importer.ImportManifest(
            counts={table: 0 for table in importer.SOURCE_TABLES},
            provider_secret_fields=0,
            generated_file_paths=0,
        )

        def import_one(*_args, staged_objects, **_kwargs):
            staged_objects.append(staged)
            return importer._expected_target_counts(
                manifest,
                omit_provider_secrets=False,
                omit_generated_files=False,
            )

        with (
            patch.object(importer, "_open_source", return_value=Source()),
            patch.object(importer, "inspect_sqlite_workspace", return_value=manifest),
            patch.object(importer, "_seed_owner", return_value=73),
            patch.object(importer, "_target_must_be_empty"),
            patch.object(importer, "set_tenant_context"),
            patch.object(importer, "_import_rows", side_effect=import_one),
        ):
            with self.assertRaisesRegex(
                importer.SQLiteImportReconciliationRequired,
                "indeterminate",
            ):
                importer.import_sqlite_workspace(
                    Path("synthetic.db"),
                    "user_01SYNTHETICOWNER",
                    engine=Engine(),
                    object_backend=backend,
                    object_keyring=self.keyring,
                )
        self.assertIsNotNone(backend.head(staged.metadata.object_key))

    def test_failed_compensation_is_reported_without_object_keys(self) -> None:
        class FailingDeleteBackend(InMemoryObjectBackend):
            def delete(self, object_key):
                raise RuntimeError("synthetic delete failure")

        backend = FailingDeleteBackend()
        staged = stage_object(
            backend, 73, b"%PDF-synthetic", "application/pdf", keyring=self.keyring
        )
        self.assertEqual(1, importer._discard_staged_objects(backend, [staged]))

    def test_rollback_and_cleanup_failure_require_reconciliation(self) -> None:
        backend = InMemoryObjectBackend()
        staged = stage_object(
            backend, 73, b"%PDF-synthetic", "application/pdf", keyring=self.keyring
        )
        backend.delete = lambda _key: (_ for _ in ()).throw(RuntimeError("delete failed"))

        class Source:
            def execute(self, _statement): return None
            def rollback(self): return None
            def close(self): return None

        class Transaction:
            is_active = True
            def rollback(self): raise ConnectionError("rollback acknowledgement lost")

        class Target:
            def begin(self): return Transaction()
            def exec_driver_sql(self, statement):
                if statement == "SET ROLE careertrellis_schema_owner":
                    return None
                raise AssertionError("An indeterminate rollback connection must not be reused.")
            def execute(self, *_args, **_kwargs): return None
            def commit(self):
                raise AssertionError("An indeterminate rollback connection must not be committed.")
            def invalidate(self): return None
            def __enter__(self): return self
            def __exit__(self, *_args): return False

        class Engine:
            def connect(self): return Target()

        manifest = importer.ImportManifest(
            counts={table: 0 for table in importer.SOURCE_TABLES},
            provider_secret_fields=0,
            generated_file_paths=0,
        )

        def fail_after_upload(*_args, staged_objects, **_kwargs):
            staged_objects.append(staged)
            raise RuntimeError("synthetic database failure")

        with (
            patch.object(importer, "_open_source", return_value=Source()),
            patch.object(importer, "inspect_sqlite_workspace", return_value=manifest),
            patch.object(importer, "_seed_owner", return_value=73),
            patch.object(importer, "_target_must_be_empty"),
            patch.object(importer, "set_tenant_context"),
            patch.object(importer, "_import_rows", side_effect=fail_after_upload),
        ):
            with self.assertRaisesRegex(
                importer.SQLiteImportReconciliationRequired,
                "1 encrypted object.*reconciliation",
            ):
                importer.import_sqlite_workspace(
                    Path("synthetic.db"),
                    "user_01SYNTHETICOWNER",
                    engine=Engine(),
                    object_backend=backend,
                    object_keyring=self.keyring,
                )


if __name__ == "__main__":
    unittest.main()
