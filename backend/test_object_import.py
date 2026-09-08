"""Focused tests for encrypted generated-object workspace imports."""

from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from object_store import (
    FilesystemObjectBackend,
    InMemoryObjectBackend,
    ObjectKeyring,
    ObjectStoreBackendError,
    R2ObjectBackend,
    R2ObjectStoreConfiguration,
    stage_object,
)
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

    def _source_with_search_run(self, status: str) -> sqlite3.Connection:
        source = sqlite3.connect(":memory:")
        source.row_factory = sqlite3.Row
        for table in importer.SOURCE_TABLES:
            if table == "profile":
                source.execute(
                    "CREATE TABLE profile (id INTEGER, gemini_api_key TEXT, "
                    "openai_api_key TEXT, google_maps_api_key TEXT)"
                )
            elif table == "applications":
                source.execute(
                    "CREATE TABLE applications (id INTEGER, job_id INTEGER, "
                    "tailored_resume_path TEXT, cover_letter_path TEXT)"
                )
            else:
                source.execute(f'CREATE TABLE "{table}" (id INTEGER)')
        source.execute(
            "CREATE TABLE app_users (workos_user_id TEXT, role TEXT, disabled_at TEXT)"
        )
        source.execute("CREATE TABLE search_runs (status TEXT)")
        source.execute("INSERT INTO search_runs (status) VALUES (?)", (status,))
        source.commit()
        return source

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

    def test_object_inventory_is_bounded_sorted_and_paginated(self) -> None:
        backend = InMemoryObjectBackend()
        keys = tuple(f"v1/{index:032x}" for index in (3, 1, 2))
        backend.objects.update({key: b"synthetic" for key in keys})

        first = backend.list_object_keys(limit=2)
        second = backend.list_object_keys(cursor=first.next_cursor, limit=2)

        self.assertEqual(tuple(sorted(keys)[:2]), first.object_keys)
        self.assertEqual(first.object_keys[-1], first.next_cursor)
        self.assertEqual((sorted(keys)[-1],), second.object_keys)
        self.assertIsNone(second.next_cursor)

    def test_filesystem_and_r2_inventory_paginate_the_full_bucket(self) -> None:
        keys = tuple(f"v1/{index:032x}" for index in (1, 2, 3))
        with tempfile.TemporaryDirectory() as temporary_directory:
            backend = FilesystemObjectBackend(Path(temporary_directory))
            for key in reversed(keys):
                backend.put(key, b"synthetic")
            first = backend.list_object_keys(limit=2)
            second = backend.list_object_keys(cursor=first.next_cursor, limit=2)
        self.assertEqual(keys[:2], first.object_keys)
        self.assertEqual(first.object_keys[-1], first.next_cursor)
        self.assertEqual(keys[2:], second.object_keys)
        self.assertIsNone(second.next_cursor)

        r2 = R2ObjectBackend(
            R2ObjectStoreConfiguration(
                endpoint="https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com",
                bucket="synthetic-private",
                access_key_id="synthetic-access",
                secret_access_key="synthetic-secret",
            )
        )
        client = Mock()
        r2._s3_client = client
        client.list_objects_v2.side_effect = [
            {
                "Contents": [{"Key": keys[0]}, {"Key": keys[1]}],
                "IsTruncated": True,
                "NextContinuationToken": "opaque-next",
            },
            {"Contents": [{"Key": keys[2]}], "IsTruncated": False},
        ]
        first = r2.list_object_keys(limit=2)
        second = r2.list_object_keys(cursor=first.next_cursor, limit=2)
        self.assertEqual(keys[:2], first.object_keys)
        self.assertEqual(keys[2:], second.object_keys)
        self.assertEqual("", client.list_objects_v2.call_args_list[0].kwargs["Prefix"])
        self.assertEqual(
            "opaque-next",
            client.list_objects_v2.call_args_list[1].kwargs["ContinuationToken"],
        )

    def test_full_bucket_inventory_rejects_top_level_and_nested_noncanonical_keys(self) -> None:
        for key in ("unexpected-object", "legacy/nested-object"):
            with self.subTest(backend="memory", key_shape=key.split("/", 1)[0]):
                backend = InMemoryObjectBackend()
                backend.objects[key] = b"synthetic"
                with self.assertRaises(ObjectStoreBackendError):
                    backend.list_object_keys()

            with self.subTest(backend="filesystem", key_shape=key.split("/", 1)[0]):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    path = root / key
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"synthetic")
                    with self.assertRaises(ObjectStoreBackendError):
                        FilesystemObjectBackend(root).list_object_keys()

            with self.subTest(backend="r2", key_shape=key.split("/", 1)[0]):
                backend = R2ObjectBackend(
                    R2ObjectStoreConfiguration(
                        endpoint="https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com",
                        bucket="synthetic-private",
                        access_key_id="synthetic-access",
                        secret_access_key="synthetic-secret",
                    )
                )
                client = Mock()
                backend._s3_client = client
                client.list_objects_v2.return_value = {
                    "Contents": [{"Key": key}],
                    "IsTruncated": False,
                }
                with self.assertRaises(ObjectStoreBackendError):
                    backend.list_object_keys()
                self.assertEqual("", client.list_objects_v2.call_args.kwargs["Prefix"])

    def test_filesystem_inventory_bounds_total_directory_and_file_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            namespace = root / "v1"
            namespace.mkdir()
            (namespace / "00000000000000000000000000000001").write_bytes(b"synthetic")
            (namespace / "00000000000000000000000000000002").write_bytes(b"synthetic")
            with (
                # One namespace entry plus one object is allowed; the second
                # object must cross this patched work ceiling.
                patch("object_store.MAXIMUM_FILESYSTEM_INVENTORY_ENTRIES", 2),
                self.assertRaisesRegex(
                    ObjectStoreBackendError,
                    "^The private object inventory is unavailable\\.$",
                ) as caught,
            ):
                FilesystemObjectBackend(root).list_object_keys()

        self.assertNotIn(str(root), str(caught.exception))
        self.assertNotIn("00000000000000000000000000000001", str(caught.exception))
        self.assertNotIn("00000000000000000000000000000002", str(caught.exception))

    def test_object_inventory_rejects_unbounded_or_unsafe_arguments(self) -> None:
        backend = InMemoryObjectBackend()
        for arguments in (
            {"prefix": "private/"},
            {"limit": 0},
            {"limit": 1001},
            {"limit": True},
            {"cursor": "x" * 2049},
            {"cursor": 7},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ObjectStoreBackendError):
                    backend.list_object_keys(**arguments)

    def test_active_source_search_run_fails_before_target_or_object_mutation(self) -> None:
        source = self._source_with_search_run("running")
        target_engine = Mock()
        object_backend = Mock()
        with (
            patch.object(importer, "_open_source", return_value=source),
            self.assertRaisesRegex(importer.SQLiteImportError, "active search work"),
        ):
            importer.import_sqlite_workspace(
                Path("synthetic.db"),
                "user_synthetic_owner",
                engine=target_engine,
                object_backend=object_backend,
            )

        target_engine.connect.assert_not_called()
        object_backend.put.assert_not_called()
        object_backend.delete.assert_not_called()
        object_backend.list_object_keys.assert_not_called()

    def test_nonempty_object_inventory_fails_after_maintenance_check_before_upload(self) -> None:
        source = Mock()
        target_engine = MagicMock()
        backend = InMemoryObjectBackend()
        backend.objects["v1/00000000000000000000000000000001"] = b"synthetic"
        manifest = importer.ImportManifest(
            counts={table: 0 for table in importer.SOURCE_TABLES},
            provider_secret_fields=0,
            generated_file_paths=1,
        )
        with (
            patch.object(importer, "_open_source", return_value=source),
            patch.object(importer, "inspect_sqlite_workspace", return_value=manifest),
            patch.object(backend, "put", wraps=backend.put) as backend_put,
            patch.object(backend, "delete", wraps=backend.delete) as backend_delete,
            self.assertRaisesRegex(
                importer.SQLiteImportError, "bucket is not empty"
            ),
        ):
            importer.import_sqlite_workspace(
                Path("synthetic.db"),
                "user_synthetic_owner",
                engine=target_engine,
                object_backend=backend,
            )

        target_engine.connect.assert_called_once_with()
        target_engine.connect.return_value.__enter__.return_value.execute.assert_not_called()
        backend_put.assert_not_called()
        backend_delete.assert_not_called()

    def test_nonempty_bucket_fails_even_when_source_has_no_generated_files(self) -> None:
        source = Mock()
        target_engine = MagicMock()
        backend = InMemoryObjectBackend()
        backend.objects["v1/00000000000000000000000000000001"] = b"synthetic"
        manifest = importer.ImportManifest(
            counts={table: 0 for table in importer.SOURCE_TABLES},
            provider_secret_fields=0,
            generated_file_paths=0,
        )
        before = dict(backend.objects)
        with (
            patch.object(importer, "_open_source", return_value=source),
            patch.object(importer, "inspect_sqlite_workspace", return_value=manifest),
            self.assertRaisesRegex(
                importer.SQLiteImportError, "bucket is not empty"
            ),
        ):
            importer.import_sqlite_workspace(
                Path("synthetic.db"),
                "user_synthetic_owner",
                engine=target_engine,
                object_backend=backend,
            )

        self.assertEqual(before, backend.objects)
        target_engine.connect.assert_called_once_with()
        target_engine.connect.return_value.__enter__.return_value.execute.assert_not_called()

    def test_advisory_lock_precedes_authoritative_bucket_recheck(self) -> None:
        events: list[str] = []
        backend = InMemoryObjectBackend()
        raced_key = "v1/00000000000000000000000000000001"

        class Source:
            def execute(self, _statement):
                return None

            def rollback(self):
                return None

            def close(self):
                return None

        class Transaction:
            is_active = True

            def rollback(self):
                events.append("rollback")
                self.is_active = False

            def commit(self):
                raise AssertionError("A failed authoritative bucket check must not commit.")

        class Target:
            def __init__(self):
                self.transaction = Transaction()

            def begin(self):
                return self.transaction

            def exec_driver_sql(self, statement):
                events.append(str(statement))

            def execute(self, statement, *_args, **_kwargs):
                if "pg_advisory_xact_lock" in str(statement):
                    events.append("advisory_lock")
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
        original_check = importer.require_empty_object_inventory

        def race_bucket_check(selected_backend):
            check_number = sum(event.startswith("bucket_check_") for event in events) + 1
            events.append(f"bucket_check_{check_number}")
            if check_number == 2:
                selected_backend.objects[raced_key] = b"synthetic-racer"
            return original_check(selected_backend)

        seed_owner = Mock(return_value=73)
        target_empty = Mock()
        import_rows = Mock()
        set_context = Mock()
        with (
            patch.object(importer, "_open_source", return_value=Source()),
            patch.object(importer, "inspect_sqlite_workspace", return_value=manifest),
            patch.object(
                importer,
                "require_empty_object_inventory",
                side_effect=race_bucket_check,
            ),
            patch.object(importer, "_seed_owner", new=seed_owner),
            patch.object(importer, "_target_must_be_empty", new=target_empty),
            patch.object(importer, "set_tenant_context", new=set_context),
            patch.object(importer, "_import_rows", new=import_rows),
            patch.object(backend, "put", wraps=backend.put) as backend_put,
            patch.object(backend, "delete", wraps=backend.delete) as backend_delete,
            self.assertRaisesRegex(importer.SQLiteImportError, "bucket is not empty"),
        ):
            importer.import_sqlite_workspace(
                Path("synthetic.db"),
                "user_01SYNTHETICOWNER",
                engine=Engine(),
                object_backend=backend,
                object_keyring=self.keyring,
            )

        self.assertEqual(2, sum(event.startswith("bucket_check_") for event in events))
        self.assertLess(events.index("SELECT careertrellis_admission.assert_import_maintenance()"),events.index("bucket_check_1"))
        self.assertLess(events.index("bucket_check_1"), events.index("advisory_lock"))
        self.assertLess(events.index("advisory_lock"), events.index("bucket_check_2"))
        self.assertIn("rollback", events)
        self.assertEqual({raced_key: b"synthetic-racer"}, backend.objects)
        seed_owner.assert_not_called()
        target_empty.assert_not_called()
        set_context.assert_not_called()
        import_rows.assert_not_called()
        backend_put.assert_not_called()
        backend_delete.assert_not_called()

    def test_non_v1_object_fails_import_preflight_before_target_or_object_mutation(self) -> None:
        for unexpected_key in ("unexpected-object", "legacy/nested-object"):
            with self.subTest(key_shape=unexpected_key.split("/", 1)[0]):
                source = Mock()
                target_engine = MagicMock()
                backend = InMemoryObjectBackend()
                backend.objects[unexpected_key] = b"synthetic"
                manifest = importer.ImportManifest(
                    counts={table: 0 for table in importer.SOURCE_TABLES},
                    provider_secret_fields=0,
                    generated_file_paths=1,
                )
                before = dict(backend.objects)
                with (
                    patch.object(importer, "_open_source", return_value=source),
                    patch.object(
                        importer, "inspect_sqlite_workspace", return_value=manifest
                    ),
                    self.assertRaises((importer.SQLiteImportError, ObjectStoreBackendError)),
                ):
                    importer.import_sqlite_workspace(
                        Path("synthetic.db"),
                        "user_synthetic_owner",
                        engine=target_engine,
                        object_backend=backend,
                    )
                self.assertEqual(before, backend.objects)
                target_engine.connect.assert_called_once_with()
                target_engine.connect.return_value.__enter__.return_value.execute.assert_not_called()

    def test_target_search_run_is_part_of_the_empty_owner_precondition(self) -> None:
        connection = Mock()
        connection.scalar.side_effect = [0] * len(importer.TARGET_TABLES) + [1]

        with self.assertRaisesRegex(importer.SQLiteImportError, "search-run data"):
            importer._target_must_be_empty(connection)

        self.assertEqual(len(importer.TARGET_TABLES) + 1, connection.scalar.call_count)
        connection.execute.assert_not_called()

    def test_import_failure_rolls_back_and_discards_uploaded_objects(self) -> None:
        backend = InMemoryObjectBackend()

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
            staged = stage_object(
                backend,
                73,
                b"%PDF-synthetic",
                "application/pdf",
                keyring=self.keyring,
            )
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
        imported_objects = []

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
            staged = stage_object(
                backend, 73, b"%PDF-synthetic", "application/pdf", keyring=self.keyring
            )
            staged_objects.append(staged)
            imported_objects.append(staged)
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
        self.assertIsNotNone(backend.head(imported_objects[0].metadata.object_key))

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
                if statement in ("SET ROLE careertrellis_schema_owner", "SELECT careertrellis_admission.assert_import_maintenance()"):
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
            staged = stage_object(
                backend, 73, b"%PDF-synthetic", "application/pdf", keyring=self.keyring
            )
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
