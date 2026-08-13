"""Focused generated-material persistence and compensation tests."""

import unittest
from unittest.mock import Mock, patch

from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError

import generated_material_repository as repository_module
import object_store


def keyring() -> object_store.ObjectKeyring:
    return object_store.ObjectKeyring(
        active_key_id="material-test-kek",
        keys={"material-test-kek": b"m" * 32},
    )


class HarnessRepository(repository_module.PostgreSQLGeneratedMaterialRepository):
    def __init__(self, *, outcome="success", reference_result=False):
        self.backend = object_store.InMemoryObjectBackend()
        super().__init__(
            7,
            engine=Mock(),
            backend=self.backend,
            keyring=keyring(),
        )
        self.outcome = outcome
        self.reference_result = reference_result
        self.commit_saw_two_verified_objects = False

    def _commit_replacement(self, job_id, expected_context_token, resume, letter, values, *, require_application):
        self.commit_saw_two_verified_objects = all(
            self.backend.head(item.metadata.object_key) == len(item.encrypted_payload)
            for item in (resume, letter)
        )
        if self.outcome == "changed":
            raise repository_module.GeneratedMaterialChanged()
        if self.outcome == "database":
            raise SQLAlchemyError("synthetic database failure")
        return []

    def _references_staged(self, job_id, staged):
        if self.reference_result == "unknown":
            raise SQLAlchemyError("synthetic verification failure")
        return bool(self.reference_result)

    def reconcile_deleted_objects(self, limit=repository_module.RECONCILE_LIMIT):
        return 0


class FailingSecondUploadBackend(object_store.InMemoryObjectBackend):
    def __init__(self):
        super().__init__()
        self.put_count = 0

    def put(self, object_key, payload):
        self.put_count += 1
        if self.put_count == 2:
            raise object_store.ObjectStoreBackendError("synthetic upload failure")
        return super().put(object_key, payload)


class GeneratedMaterialCompensationTests(unittest.TestCase):
    def values(self):
        return {
            "company": "Synthetic Employer",
            "position": "Synthetic Role",
            "us_hq": "Synthetic HQ",
            "headquarters_source": "openstreetmap",
            "headquarters_attribution": "Synthetic attribution",
            "resume_text": "# Synthetic resume",
            "cover_letter": "Synthetic letter",
            "base_resume_id": 3,
            "base_resume_name": "Primary Resume",
            "base_resume_version": 1,
        }

    def test_uploads_are_verified_before_authoritative_commit(self):
        repository = HarnessRepository()
        result = repository.replace_materials(
            11, "a" * 64, b"%PDF-1.7\nsynthetic", "Synthetic letter", self.values()
        )
        self.assertTrue(repository.commit_saw_two_verified_objects)
        self.assertEqual(2, len(repository.backend.objects))
        self.assertTrue(result["tailored_resume_object_id"])
        self.assertTrue(result["cover_letter_object_id"])

    def test_stale_context_discards_both_uncommitted_objects(self):
        repository = HarnessRepository(outcome="changed")
        with self.assertRaises(repository_module.GeneratedMaterialChanged):
            repository.replace_materials(
                11, "a" * 64, b"%PDF-1.7\nsynthetic", "Synthetic letter", self.values()
            )
        self.assertEqual({}, repository.backend.objects)

    def test_proven_database_rollback_discards_staged_objects(self):
        repository = HarnessRepository(outcome="database", reference_result=False)
        with self.assertRaises(repository_module.GeneratedMaterialUnavailable):
            repository.replace_materials(
                11, "a" * 64, b"%PDF-1.7\nsynthetic", "Synthetic letter", self.values()
            )
        self.assertEqual({}, repository.backend.objects)

    def test_ambiguous_commit_never_deletes_possibly_authoritative_objects(self):
        repository = HarnessRepository(outcome="database", reference_result="unknown")
        with self.assertRaises(repository_module.GeneratedMaterialUnavailable):
            repository.replace_materials(
                11, "a" * 64, b"%PDF-1.7\nsynthetic", "Synthetic letter", self.values()
            )
        self.assertEqual(2, len(repository.backend.objects))

    def test_committed_but_disconnected_result_is_treated_as_success(self):
        repository = HarnessRepository(outcome="database", reference_result=True)
        result = repository.replace_materials(
            11, "a" * 64, b"%PDF-1.7\nsynthetic", "Synthetic letter", self.values()
        )
        self.assertEqual(2, len(repository.backend.objects))
        self.assertTrue(result["tailored_resume_object_id"])

    def test_second_upload_failure_never_reaches_commit_and_cleans_first(self):
        backend = FailingSecondUploadBackend()
        repository = HarnessRepository()
        repository._backend = backend
        with self.assertRaises(repository_module.GeneratedMaterialUnavailable):
            repository.replace_materials(
                11, "a" * 64, b"%PDF-1.7\nsynthetic", "Synthetic letter", self.values()
            )
        self.assertFalse(repository.commit_saw_two_verified_objects)
        self.assertEqual({}, backend.objects)

    def test_runtime_factory_has_no_local_sqlite_fallback(self):
        with patch.object(repository_module, "data_store_kind", return_value="sqlite"):
            with self.assertRaises(repository_module.GeneratedMaterialUnavailable):
                repository_module.runtime_generated_material_repository(
                    engine=Mock(), backend=object_store.InMemoryObjectBackend(), keyring=keyring()
                )

    def test_runtime_factory_redacts_object_store_configuration_failure(self):
        with (
            patch.object(repository_module, "data_store_kind", return_value="postgres"),
            patch.object(repository_module, "current_owner_user_id", return_value=7),
            patch.object(
                repository_module,
                "runtime_object_backend",
                side_effect=object_store.ObjectStoreConfigurationError(
                    "private object-store configuration detail"
                ),
            ),
        ):
            with self.assertRaises(
                repository_module.GeneratedMaterialUnavailable
            ) as raised:
                repository_module.runtime_generated_material_repository(engine=Mock())
        self.assertNotIn("private", str(raised.exception))


class GeneratedMaterialRouteBoundaryTests(unittest.TestCase):
    def test_reviewed_material_request_fields_are_bounded(self):
        import app as app_module

        with self.assertRaises(ValidationError):
            app_module.MaterialsUpdateRequest(
                tailored_resume="r" * (app_module.MAX_BASE_RESUME_CHARS + 1),
                cover_letter="Synthetic letter",
            )

    def test_shared_material_lookup_never_opens_sqlite(self):
        import app as app_module

        expected = {"job_id": 11}
        repository = Mock()
        repository.materials.return_value = expected
        with (
            patch.object(app_module, "data_store_kind", return_value="postgres"),
            patch.object(
                app_module,
                "runtime_generated_material_repository",
                return_value=repository,
            ),
            patch.object(app_module, "get_db_connection") as sqlite_connection,
        ):
            self.assertIs(expected, app_module._application_materials(11))
        repository.materials.assert_called_once_with(11)
        sqlite_connection.assert_not_called()

    def test_shared_download_authorizes_reference_before_object_fetch(self):
        import app as app_module

        events = []
        material = {
            "tailored_resume_object_id": "6ac16be9-a693-4a84-af44-600389653c01",
            "company": "Synthetic Employer",
            "position": "Synthetic Role",
            "candidate_name": "Synthetic Candidate",
            "job_source": "manual",
        }
        download = Mock(content=b"%PDF-1.7\nsynthetic")

        def authorized_materials(job_id):
            events.append("metadata")
            return material

        def fetch(owner_id, object_id):
            events.append("object")
            self.assertEqual(7, owner_id)
            self.assertEqual(material["tailored_resume_object_id"], object_id)
            return download

        with (
            patch.object(app_module, "data_store_kind", return_value="postgres"),
            patch.object(app_module, "_application_materials", side_effect=authorized_materials),
            patch.object(app_module, "current_owner_user_id", return_value=7),
            patch.object(app_module, "download_stored_object", side_effect=fetch),
            patch.object(app_module, "capture_event"),
        ):
            response = app_module.download_tailored_resume(11)
        self.assertEqual(["metadata", "object"], events)
        self.assertEqual(b"%PDF-1.7\nsynthetic", response.body)
        self.assertEqual("private, no-store, max-age=0", response.headers["cache-control"])

    def test_shared_download_redacts_database_failure(self):
        import app as app_module

        material = {
            "tailored_resume_object_id": "6ac16be9-a693-4a84-af44-600389653c01",
            "company": "Synthetic Employer",
            "position": "Synthetic Role",
            "candidate_name": "Synthetic Candidate",
            "job_source": "manual",
        }
        with (
            patch.object(app_module, "data_store_kind", return_value="postgres"),
            patch.object(app_module, "_application_materials", return_value=material),
            patch.object(app_module, "current_owner_user_id", return_value=7),
            patch.object(
                app_module,
                "download_stored_object",
                side_effect=SQLAlchemyError("private database detail"),
            ),
        ):
            with self.assertRaises(app_module.HTTPException) as raised:
                app_module.download_tailored_resume(11)
        self.assertEqual(503, raised.exception.status_code)
        self.assertEqual(
            "The generated resume PDF is temporarily unavailable.",
            raised.exception.detail,
        )

    def test_shared_docx_uses_postgres_material_key_contract(self):
        import app as app_module

        material = {
            "tailored_resume_text": "# Synthetic Candidate\n\n## Experience\n\n- Result.",
            "company": "Synthetic Employer",
            "position": "Synthetic Role",
            "candidate_name": "Synthetic Candidate",
            "job_source": "manual",
        }
        with (
            patch.object(app_module, "data_store_kind", return_value="postgres"),
            patch.object(app_module, "_application_materials", return_value=material),
            patch.object(
                app_module,
                "build_accessible_resume_docx",
                return_value=b"synthetic docx",
            ) as builder,
            patch.object(app_module, "capture_event"),
        ):
            response = app_module.download_tailored_resume_docx(11)
        self.assertEqual(b"synthetic docx", response.body)
        self.assertEqual(
            "Synthetic Candidate - Synthetic Role Resume",
            builder.call_args.kwargs["title"],
        )
        self.assertIn(
            "synthetic-candidate-synthetic-employer-synthetic-role-resume-accessible.docx",
            response.headers["content-disposition"],
        )

    def test_shared_detail_route_redacts_object_store_configuration_failure(self):
        import app as app_module

        with (
            patch.object(app_module, "data_store_kind", return_value="postgres"),
            patch.object(repository_module, "data_store_kind", return_value="postgres"),
            patch.object(repository_module, "current_owner_user_id", return_value=7),
            patch.object(repository_module, "runtime_postgres_engine", return_value=Mock()),
            patch.object(
                repository_module,
                "runtime_object_backend",
                side_effect=object_store.ObjectStoreConfigurationError(
                    "private object-store configuration detail"
                ),
            ),
        ):
            with self.assertRaises(app_module.HTTPException) as raised:
                app_module.get_tailored_details(11)
        self.assertEqual(503, raised.exception.status_code)
        self.assertEqual(
            "Generated application materials are temporarily unavailable.",
            raised.exception.detail,
        )
        self.assertNotIn("private", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()
