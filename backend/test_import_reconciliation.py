"""Focused, synthetic tests for read-only SQLite/PostgreSQL reconciliation."""

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, call, patch

import object_store
import sqlite_postgres_import as importer
import sqlite_postgres_reconcile as reconciler


class ImportReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = 41
        self.keyring = object_store.ObjectKeyring(
            "reconcile-test", {"reconcile-test": b"r" * 32}
        )

    def _object_fixture(self, *, staged_owner: int | None = None):
        plaintext = b"%PDF-synthetic reconciliation material"
        backend = object_store.InMemoryObjectBackend()
        staged = object_store.stage_object(
            backend,
            staged_owner or self.owner_id,
            plaintext,
            "application/pdf",
            keyring=self.keyring,
        )
        metadata = staged.metadata
        metadata_row = {
            "id": metadata.id,
            "object_key": metadata.object_key,
            "content_type": metadata.content_type,
            "plaintext_size": metadata.size_bytes,
            "sha256": bytes.fromhex(metadata.sha256),
            "wrapped_data_key": metadata.wrapped_data_key,
            "deleted_at": None,
        }
        source_apps = [
            {
                "id": 1,
                "tailored_resume_path": "output/synthetic.pdf",
                "cover_letter_path": "",
            }
        ]
        target_apps = [
            {
                "tailored_resume_object_id": metadata.id,
                "cover_letter_object_id": None,
            }
        ]
        manifest = importer.ImportManifest(
            counts={table: 0 for table in importer.SOURCE_TABLES},
            provider_secret_fields=0,
            generated_file_paths=1,
            generated_file_snapshots={(1, "tailored_resume_path"): SimpleNamespace()},
        )
        return backend, staged, metadata_row, source_apps, target_apps, manifest, plaintext

    def _verify_objects(
        self,
        backend,
        metadata_rows,
        source_apps,
        target_apps,
        manifest,
        plaintext,
    ):
        with (
            patch.object(reconciler, "_target_rows", return_value=metadata_rows),
            patch.object(reconciler, "read_generated_snapshot", return_value=plaintext),
            patch.object(backend, "put", wraps=backend.put) as put_object,
            patch.object(backend, "delete", wraps=backend.delete) as delete_object,
        ):
            try:
                return reconciler._verify_objects(
                    Path("synthetic.db"),
                    source_apps,
                    target_apps,
                    Mock(),
                    self.owner_id,
                    manifest,
                    backend,
                    omit_generated_files=False,
                    object_keyring=self.keyring,
                )
            finally:
                put_object.assert_not_called()
                delete_object.assert_not_called()

    def _call_entrypoint(
        self,
        failed_stage: str | None = None,
        *,
        omit_generated_files: bool = False,
        pass_object_backend: bool = True,
        object_backend=None,
        exercise_object_inventory: bool = False,
    ):
        source = Mock()
        target = Mock()
        transaction = Mock()
        transaction.is_active = True
        target.begin.return_value = transaction
        engine = Mock()
        engine.connect.return_value = MagicMock()
        engine.connect.return_value.__enter__.return_value = target
        backend = object_backend if object_backend is not None else Mock()
        runtime_backend = Mock()
        manifest = importer.ImportManifest(
            counts={table: 0 for table in importer.SOURCE_TABLES},
            provider_secret_fields=0,
            generated_file_paths=0,
            nonportable_terminal_search_runs=2,
        )
        helpers = {
            "_resolve_owner": Mock(return_value=self.owner_id),
            "_verify_counts": Mock(return_value={"jobs": 1, "stored_objects": 0}),
            "_verify_relational_rows": Mock(return_value=({}, {}, [], [])),
            "_verify_dependent_rows": Mock(),
            "_verify_provider_secrets": Mock(),
            "_verify_objects": Mock(return_value=0),
        }
        private_detail = "user_private_123/v1/private-object/source/private.pdf"
        if failed_stage is not None:
            helpers[failed_stage].side_effect = RuntimeError(private_detail)
        if exercise_object_inventory:
            helpers["_verify_objects"].side_effect = (
                lambda *_args, **_kwargs: len(
                    reconciler.inventory_object_keys(_args[6])
                )
            )

        result = error = None
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(reconciler, "open_sqlite_snapshot", return_value=source)
            )
            stack.enter_context(
                patch.object(reconciler, "inspect_sqlite_workspace", return_value=manifest)
            )
            stack.enter_context(
                patch.object(reconciler, "runtime_object_backend", new=runtime_backend)
            )
            stack.enter_context(patch.object(reconciler, "set_tenant_context"))
            for name, helper in helpers.items():
                stack.enter_context(patch.object(reconciler, name, new=helper))
            try:
                result = reconciler.reconcile_sqlite_postgres_import(
                    Path("synthetic.db"),
                    "user_synthetic_owner",
                    omit_generated_files=omit_generated_files,
                    engine=engine,
                    object_backend=backend if pass_object_backend else None,
                    provider_keyring=Mock(),
                    object_keyring=self.keyring,
                )
            except Exception as exc:  # The caller asserts the public classification.
                error = exc
        return (
            result,
            error,
            source,
            target,
            transaction,
            engine,
            backend,
            helpers,
            runtime_backend,
        )

    def test_exact_snapshot_returns_only_a_sanitized_summary_and_closes_transactions(self):
        result, error, source, target, transaction, engine, backend, helpers, runtime_backend = (
            self._call_entrypoint()
        )

        self.assertIsNone(error)
        self.assertEqual(
            {
                "verified_counts": {"jobs": 1, "stored_objects": 0},
                "verified_objects": 0,
                "nonportable_terminal_search_runs": 2,
            },
            result.sanitized_summary(),
        )
        self.assertNotIn("user_", json.dumps(result.sanitized_summary(), sort_keys=True))
        self.assertEqual(
            [
                call("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"),
                call("SET ROLE careertrellis_schema_owner"),
                call("RESET ROLE"),
            ],
            target.exec_driver_sql.call_args_list,
        )
        transaction.rollback.assert_called_once_with()
        source.rollback.assert_called_once_with()
        source.close.assert_called_once_with()
        engine.dispose.assert_not_called()
        backend.put.assert_not_called()
        backend.delete.assert_not_called()
        runtime_backend.assert_not_called()
        for helper in helpers.values():
            helper.assert_called_once()

    def test_ambiguous_outcome_is_classified_from_exact_read_only_state(self):
        successful, success_error, *_ = self._call_entrypoint()
        self.assertIsNone(success_error)
        self.assertIsInstance(successful, reconciler.ReconciliationSummary)

        for failed_stage in (
            "_resolve_owner",
            "_verify_counts",
            "_verify_relational_rows",
            "_verify_provider_secrets",
            "_verify_objects",
        ):
            with self.subTest(failed_stage=failed_stage):
                (
                    result,
                    error,
                    source,
                    _target,
                    transaction,
                    _engine,
                    backend,
                    _helpers,
                    _runtime_backend,
                ) = self._call_entrypoint(failed_stage)
                self.assertIsNone(result)
                self.assertIsInstance(error, reconciler.SQLitePostgresReconciliationError)
                self.assertEqual(reconciler.RECONCILIATION_ERROR_MESSAGE, str(error))
                self.assertNotIn("private", str(error))
                transaction.rollback.assert_called_once_with()
                source.rollback.assert_called_once_with()
                source.close.assert_called_once_with()
                backend.put.assert_not_called()
                backend.delete.assert_not_called()

    def test_omit_generated_files_never_loads_a_backend_and_requires_empty_refs(self):
        result, error, *_rest, helpers, runtime_backend = self._call_entrypoint(
            omit_generated_files=True,
            pass_object_backend=False,
        )
        self.assertIsNone(error)
        self.assertEqual(0, result.verified_objects)
        runtime_backend.assert_not_called()
        self.assertIsNone(helpers["_verify_objects"].call_args.args[6])
        self.assertTrue(helpers["_verify_objects"].call_args.kwargs["omit_generated_files"])

        source_apps = [
            {"id": 1, "tailored_resume_path": "", "cover_letter_path": ""}
        ]
        target_apps = [
            {
                "tailored_resume_object_id": "00000000-0000-0000-0000-000000000001",
                "cover_letter_object_id": None,
            }
        ]
        manifest = importer.ImportManifest(
            counts={table: 0 for table in importer.SOURCE_TABLES},
            provider_secret_fields=0,
            generated_file_paths=0,
        )
        with (
            patch.object(reconciler, "_target_rows", return_value=[]),
            self.assertRaises(reconciler.SQLitePostgresReconciliationError),
        ):
            reconciler._verify_objects(
                Path("synthetic.db"),
                source_apps,
                target_apps,
                Mock(),
                self.owner_id,
                manifest,
                None,
                omit_generated_files=True,
                object_keyring=None,
            )

    def test_exact_object_match_authenticates_blob_without_mutation(self):
        fixture = self._object_fixture()
        backend, _staged, metadata, source_apps, target_apps, manifest, plaintext = fixture
        self.assertEqual(
            1,
            self._verify_objects(
                backend,
                [metadata],
                source_apps,
                target_apps,
                manifest,
                plaintext,
            ),
        )

    def test_missing_row_blob_or_orphan_key_fails_closed_without_mutation(self):
        for defect in ("missing_row", "missing_blob", "orphan_key"):
            with self.subTest(defect=defect):
                fixture = self._object_fixture()
                backend, staged, metadata, source_apps, target_apps, manifest, plaintext = fixture
                metadata_rows = [metadata]
                if defect == "missing_row":
                    metadata_rows = []
                elif defect == "missing_blob":
                    backend.objects.pop(staged.metadata.object_key)
                else:
                    backend.objects["v1/ffffffffffffffffffffffffffffffff"] = b"orphan"
                with self.assertRaises(Exception) as caught:
                    self._verify_objects(
                        backend,
                        metadata_rows,
                        source_apps,
                        target_apps,
                        manifest,
                        plaintext,
                    )
                self.assertNotIsInstance(caught.exception, AssertionError)

    def test_duplicate_reference_wrong_owner_and_integrity_tampering_fail_closed(self):
        for defect in ("duplicate_reference", "wrong_owner", "metadata", "ciphertext"):
            with self.subTest(defect=defect):
                fixture = self._object_fixture(
                    staged_owner=self.owner_id + 1 if defect == "wrong_owner" else None
                )
                backend, staged, metadata, source_apps, target_apps, manifest, plaintext = fixture
                if defect == "duplicate_reference":
                    source_apps.append(dict(source_apps[0], id=2))
                    target_apps.append(dict(target_apps[0]))
                    manifest = importer.ImportManifest(
                        counts=manifest.counts,
                        provider_secret_fields=0,
                        generated_file_paths=2,
                        generated_file_snapshots={
                            (1, "tailored_resume_path"): SimpleNamespace(),
                            (2, "tailored_resume_path"): SimpleNamespace(),
                        },
                    )
                elif defect == "metadata":
                    metadata = dict(metadata, plaintext_size=len(plaintext) + 1)
                elif defect == "ciphertext":
                    payload = backend.objects[staged.metadata.object_key]
                    backend.objects[staged.metadata.object_key] = payload[:-1] + bytes(
                        (payload[-1] ^ 1,)
                    )
                with self.assertRaises(Exception) as caught:
                    self._verify_objects(
                        backend,
                        [metadata],
                        source_apps,
                        target_apps,
                        manifest,
                        plaintext,
                    )
                self.assertNotIsInstance(caught.exception, AssertionError)

    def test_changed_generated_file_and_tampered_provider_ciphertext_are_rejected(self):
        fixture = self._object_fixture()
        backend, _staged, metadata, source_apps, target_apps, manifest, plaintext = fixture
        with (
            patch.object(reconciler, "_target_rows", return_value=[metadata]),
            patch.object(
                reconciler,
                "read_generated_snapshot",
                side_effect=importer.SQLiteImportError("private changed file path"),
            ),
            patch.object(backend, "put", wraps=backend.put) as put_object,
            patch.object(backend, "delete", wraps=backend.delete) as delete_object,
            self.assertRaises(importer.SQLiteImportError),
        ):
            reconciler._verify_objects(
                Path("synthetic.db"),
                source_apps,
                target_apps,
                Mock(),
                self.owner_id,
                manifest,
                backend,
                omit_generated_files=False,
                object_keyring=self.keyring,
            )
        put_object.assert_not_called()
        delete_object.assert_not_called()

        source_secret = {
            "gemini_api_key": "synthetic-secret",
            "openai_api_key": "",
            "google_maps_api_key": "",
        }
        target_secret = {
            "id": 1,
            "provider": "gemini",
            "key_id": "test-key",
            "ciphertext": b"tampered",
            "revoked_at": None,
        }
        with (
            patch.object(reconciler, "_target_rows", return_value=[target_secret]),
            patch.object(reconciler, "sqlite_table_rows", return_value=[source_secret]),
            patch.object(
                reconciler,
                "decrypt_provider_secret",
                side_effect=ValueError("synthetic ciphertext failure"),
            ) as decrypt,
            self.assertRaises(ValueError),
        ):
            reconciler._verify_provider_secrets(
                Mock(),
                Mock(),
                self.owner_id,
                omit_provider_secrets=False,
                provider_keyring=Mock(),
            )
        decrypt.assert_called_once()

    def test_inventory_aggregator_paginates_and_rejects_cycles_or_overflow(self):
        backend = Mock()
        backend.list_object_keys.side_effect = [
            object_store.ObjectInventoryPage(
                ("v1/00000000000000000000000000000001",), "next-page"
            ),
            object_store.ObjectInventoryPage(
                ("v1/00000000000000000000000000000002",), None
            ),
        ]
        self.assertEqual(
            (
                "v1/00000000000000000000000000000001",
                "v1/00000000000000000000000000000002",
            ),
            reconciler.inventory_object_keys(backend, page_size=1, maximum_objects=2),
        )
        self.assertEqual(
            [
                call(prefix="", cursor=None, limit=1),
                call(prefix="", cursor="next-page", limit=1),
            ],
            backend.list_object_keys.call_args_list,
        )

        cyclic = Mock()
        cyclic.list_object_keys.side_effect = [
            object_store.ObjectInventoryPage((), "same"),
            object_store.ObjectInventoryPage((), "same"),
        ]
        with self.assertRaisesRegex(
            reconciler.SQLitePostgresReconciliationError,
            f"^{reconciler.RECONCILIATION_ERROR_MESSAGE}$",
        ):
            reconciler.inventory_object_keys(cyclic)

        overflowing = Mock()
        overflowing.list_object_keys.return_value = object_store.ObjectInventoryPage(
            (
                "v1/00000000000000000000000000000001",
                "v1/00000000000000000000000000000002",
            )
        )
        with self.assertRaises(reconciler.SQLitePostgresReconciliationError):
            reconciler.inventory_object_keys(overflowing, maximum_objects=1)
        for invalid_bound in (0, True, reconciler.MAXIMUM_INVENTORY_OBJECTS + 1):
            with self.subTest(invalid_bound=invalid_bound):
                with self.assertRaises(reconciler.SQLitePostgresReconciliationError):
                    reconciler.inventory_object_keys(Mock(), maximum_objects=invalid_bound)

    def test_non_v1_bucket_key_makes_full_reconciliation_fail_with_fixed_error(self):
        for unexpected_key in ("unexpected-object", "legacy/nested-object"):
            with self.subTest(key_shape=unexpected_key.split("/", 1)[0]):
                backend = object_store.InMemoryObjectBackend()
                backend.objects[unexpected_key] = b"synthetic"
                before = dict(backend.objects)
                (
                    result,
                    error,
                    source,
                    _target,
                    transaction,
                    _engine,
                    _backend,
                    _helpers,
                    _runtime_backend,
                ) = self._call_entrypoint(
                    object_backend=backend,
                    exercise_object_inventory=True,
                )

                self.assertIsNone(result)
                self.assertIsInstance(
                    error, reconciler.SQLitePostgresReconciliationError
                )
                self.assertEqual(reconciler.RECONCILIATION_ERROR_MESSAGE, str(error))
                self.assertNotIn(unexpected_key, str(error))
                self.assertEqual(before, backend.objects)
                transaction.rollback.assert_called_once_with()
                source.rollback.assert_called_once_with()
                source.close.assert_called_once_with()

    def test_cli_receipt_and_root_wrapper_are_privacy_safe_and_fail_closed(self):
        summary = reconciler.ReconciliationSummary(
            verified_counts={"jobs": 1},
            verified_objects=1,
            nonportable_terminal_search_runs=0,
        )
        with (
            patch.dict(
                reconciler.os.environ,
                {importer.OWNER_ID_ENVIRONMENT_VARIABLE: "user_synthetic_owner"},
                clear=True,
            ),
            patch.object(
                reconciler.sys,
                "argv",
                ["sqlite_postgres_reconcile.py", "synthetic.db"],
            ),
            patch.object(
                reconciler,
                "reconcile_sqlite_postgres_import",
                return_value=summary,
            ) as reconcile,
            patch("builtins.print") as output,
        ):
            self.assertEqual(0, reconciler.main())
        reconcile.assert_called_once()
        receipt = output.call_args.args[0]
        self.assertEqual(summary.sanitized_summary(), json.loads(receipt))
        self.assertNotIn("user_synthetic_owner", receipt)
        self.assertNotIn("synthetic.db", receipt)

        with (
            patch.dict(reconciler.os.environ, {}, clear=True),
            patch.object(
                reconciler.sys,
                "argv",
                ["sqlite_postgres_reconcile.py", "private/source.db"],
            ),
            patch.object(reconciler, "reconcile_sqlite_postgres_import") as reconcile,
            self.assertRaisesRegex(
                reconciler.SQLitePostgresReconciliationError,
                f"^{reconciler.RECONCILIATION_ERROR_MESSAGE}$",
            ),
        ):
            reconciler.main()
        reconcile.assert_not_called()

        wrapper = (
            Path(__file__).parents[1]
            / "deploy"
            / "scripts"
            / "reconcile-sqlite-postgres-import.sh"
        ).read_text(encoding="utf-8")
        maintenance_helper = (
            Path(__file__).parents[1]
            / "deploy"
            / "scripts"
            / "maintenance-lock.sh"
        ).read_text(encoding="utf-8")
        for invariant in (
            "maintenance-lock.sh",
            "acquire_careertrellis_maintenance_lock",
            "flock -n 9",
            "careertrellis-web.service careertrellis-worker.service",
            "assert-import-allowed",
            "sqlite_postgres_reconcile.py",
        ):
            with self.subTest(invariant=invariant):
                self.assertIn(invariant, wrapper)
        self.assertIn("flock -n 8", maintenance_helper)


if __name__ == "__main__":
    unittest.main()
