from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

import object_store
import stored_objects
import app as app_module


class CountingBackend(object_store.InMemoryObjectBackend):
    def __init__(self):
        super().__init__()
        self.get_calls = 0

    def get(self, object_key):
        self.get_calls += 1
        return super().get(object_key)


class StoredObjectTests(unittest.TestCase):
    def setUp(self):
        self.keyring = object_store.ObjectKeyring(
            active_key_id="object-kek-test",
            keys={"object-kek-test": b"k" * 32},
        )

    def test_load_uses_owner_scoped_transaction_and_active_row_filter(self):
        object_id = uuid.uuid4()
        row = {
            "id": object_id,
            "owner_user_id": 7,
            "object_key": f"v1/{object_id.hex}",
            "content_type": "application/pdf",
            "plaintext_size": 12,
            "sha256": b"d" * 32,
            "wrapped_data_key": b"wrapped",
        }
        result = Mock()
        result.mappings.return_value.one_or_none.return_value = row
        connection = Mock()
        connection.execute.return_value = result

        @contextmanager
        def transaction(engine, owner_user_id):
            self.assertEqual("engine", engine)
            self.assertEqual(7, owner_user_id)
            yield connection

        with patch.object(stored_objects, "tenant_transaction", transaction):
            metadata = stored_objects.load_stored_object_metadata(
                7,
                str(object_id),
                engine="engine",
            )
        self.assertEqual(object_id, metadata.id)
        self.assertEqual((b"d" * 32).hex(), metadata.sha256)
        statement = str(connection.execute.call_args.args[0])
        self.assertIn("owner_user_id = :owner_user_id", statement)
        self.assertIn("deleted_at IS NULL", statement)

    def test_missing_or_invalid_id_is_generic(self):
        with self.assertRaisesRegex(stored_objects.StoredObjectNotFound, "unavailable"):
            stored_objects.load_stored_object_metadata(7, "not-a-uuid", engine=Mock())

    def test_owner_mismatch_is_rejected_before_backend_fetch(self):
        backend = CountingBackend()
        staged = object_store.stage_object(
            backend,
            7,
            b"private",
            "text/plain",
            keyring=self.keyring,
        )
        with (
            patch.object(
                stored_objects,
                "load_stored_object_metadata",
                return_value=replace(staged.metadata, owner_user_id=7),
            ),
            self.assertRaises(object_store.ObjectStoreIntegrityError),
        ):
            stored_objects.download_stored_object(
                8,
                staged.metadata.id,
                backend=backend,
                keyring=self.keyring,
            )
        self.assertEqual(1, backend.get_calls)  # stage readback only

    def test_authorized_download_has_fixed_nonidentifying_filename(self):
        backend = CountingBackend()
        staged = object_store.stage_object(
            backend,
            7,
            b"%PDF synthetic",
            "application/pdf",
            keyring=self.keyring,
        )
        with patch.object(
            stored_objects,
            "load_stored_object_metadata",
            return_value=staged.metadata,
        ):
            download = stored_objects.download_stored_object(
                7,
                staged.metadata.id,
                backend=backend,
                keyring=self.keyring,
            )
        self.assertEqual(b"%PDF synthetic", download.content)
        self.assertEqual("generated-resume.pdf", download.filename)

    def test_http_download_is_private_and_never_uses_an_object_url(self):
        download = stored_objects.StoredObjectDownload(
            content=b"%PDF synthetic",
            content_type="application/pdf",
            filename="generated-resume.pdf",
        )
        request = SimpleNamespace(state=SimpleNamespace(local_user_id=7))
        with patch.object(app_module, "download_stored_object", return_value=download):
            response = app_module.download_private_object(str(uuid.uuid4()), request)
        self.assertEqual(b"%PDF synthetic", response.body)
        self.assertEqual("private, no-store, max-age=0", response.headers["cache-control"])
        self.assertEqual(
            'attachment; filename="generated-resume.pdf"',
            response.headers["content-disposition"],
        )
        self.assertNotIn("http", response.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
