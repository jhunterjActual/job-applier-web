import base64
from dataclasses import replace
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import object_store


def keyring(key_id="object-kek-1", key=b"k" * 32):
    return object_store.ObjectKeyring(active_key_id=key_id, keys={key_id: key})


def encoded_keyring(key_id="object-kek-1", key=b"k" * 32):
    document = {key_id: base64.urlsafe_b64encode(key).decode("ascii")}
    return base64.urlsafe_b64encode(
        json.dumps(document, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


class CorruptingBackend(object_store.InMemoryObjectBackend):
    def get(self, object_key):
        value = bytearray(super().get(object_key))
        value[-1] ^= 1
        return bytes(value)


class ObjectEncryptionTests(unittest.TestCase):
    def test_pdf_and_utf8_text_round_trip(self):
        for content, content_type in (
            (b"%PDF-1.7\nsynthetic", "application/pdf"),
            ("private r\N{LATIN SMALL LETTER E WITH ACUTE}sum\N{LATIN SMALL LETTER E WITH ACUTE}".encode(), "text/plain"),
        ):
            with self.subTest(content_type=content_type):
                staged = object_store.encrypt_object(7, content, content_type, keyring=keyring())
                self.assertEqual(
                    object_store.decrypt_object(staged.metadata, staged.encrypted_payload, 7, keyring=keyring()),
                    content,
                )
                self.assertEqual(staged.metadata.object_key, f"v1/{staged.metadata.id.hex}")

    def test_each_encryption_randomizes_key_and_ciphertext(self):
        first = object_store.encrypt_object(7, b"same", "text/plain", keyring=keyring())
        second = object_store.encrypt_object(7, b"same", "text/plain", keyring=keyring())
        self.assertNotEqual(first.metadata.id, second.metadata.id)
        self.assertNotEqual(first.metadata.object_key, second.metadata.object_key)
        self.assertNotEqual(first.metadata.wrapped_data_key, second.metadata.wrapped_data_key)
        self.assertNotEqual(first.encrypted_payload, second.encrypted_payload)

    def test_wrong_owner_metadata_tamper_and_payload_tamper_are_generic(self):
        staged = object_store.encrypt_object(7, b"private", "text/plain", keyring=keyring())
        tampered_payload = bytearray(staged.encrypted_payload)
        tampered_payload[-1] ^= 1
        cases = (
            (staged.metadata, staged.encrypted_payload, 8),
            (replace(staged.metadata, size_bytes=6), staged.encrypted_payload, 7),
            (replace(staged.metadata, sha256="0" * 64), staged.encrypted_payload, 7),
            (staged.metadata, bytes(tampered_payload), 7),
        )
        messages = set()
        for metadata, payload, owner in cases:
            with self.assertRaises(object_store.ObjectStoreIntegrityError) as raised:
                object_store.decrypt_object(metadata, payload, owner, keyring=keyring())
            messages.add(str(raised.exception))
        self.assertEqual(messages, {"The stored object is unavailable."})

    def test_unknown_wrapping_key_is_generic(self):
        staged = object_store.encrypt_object(7, b"private", "text/plain", keyring=keyring())
        unavailable = object_store.ObjectKeyring(active_key_id="new", keys={"new": b"n" * 32})
        with self.assertRaisesRegex(object_store.ObjectStoreIntegrityError, "stored object is unavailable"):
            object_store.decrypt_object(staged.metadata, staged.encrypted_payload, 7, keyring=unavailable)

    def test_content_and_size_bounds(self):
        invalid = (
            (b"", "text/plain"),
            (b"x" * (object_store.MAXIMUM_OBJECT_BYTES + 1), "text/plain"),
            (b"\xff", "text/plain"),
            (b"doc", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        )
        for content, content_type in invalid:
            with self.subTest(content_type=content_type, size=len(content)):
                with self.assertRaises(ValueError):
                    object_store.encrypt_object(7, content, content_type, keyring=keyring())


class StagingTests(unittest.TestCase):
    def test_upload_readback_read_and_discard(self):
        backend = object_store.InMemoryObjectBackend()
        staged = object_store.stage_object(
            backend, 9, b"owner data", "text/plain", keyring=keyring()
        )
        self.assertEqual(backend.head(staged.metadata.object_key), len(staged.encrypted_payload))
        self.assertEqual(
            object_store.read_object(backend, staged.metadata, 9, keyring=keyring()),
            b"owner data",
        )
        object_store.discard_staged_object(backend, staged)
        self.assertIsNone(backend.head(staged.metadata.object_key))

    def test_failed_readback_is_cleaned_up(self):
        backend = CorruptingBackend()
        with self.assertRaises(object_store.ObjectStoreIntegrityError):
            object_store.stage_object(backend, 9, b"owner data", "text/plain", keyring=keyring())
        self.assertEqual(backend.objects, {})

    def test_filesystem_backend_uses_only_opaque_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            backend = object_store.FilesystemObjectBackend(object_store.Path(temporary))
            staged = object_store.stage_object(
                backend, 9, b"owner data", "text/plain", keyring=keyring()
            )
            self.assertEqual(
                object_store.read_object(backend, staged.metadata, 9, keyring=keyring()),
                b"owner data",
            )
            with self.assertRaises(object_store.ObjectStoreBackendError):
                backend.put("../escape", b"bad")


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            object_store.OBJECT_STORE_ENVIRONMENT_VARIABLE: object_store.SHARED_OBJECT_STORE,
            object_store.ENDPOINT_ENVIRONMENT_VARIABLE: "https://" + "a" * 32 + ".r2.cloudflarestorage.com",
            object_store.BUCKET_ENVIRONMENT_VARIABLE: "careertrellis-staging-objects",
            object_store.ACCESS_KEY_ENVIRONMENT_VARIABLE: "A" * 32,
            object_store.SECRET_KEY_ENVIRONMENT_VARIABLE: "s" * 64,
            object_store.ACTIVE_KEY_ID_ENVIRONMENT_VARIABLE: "object-kek-1",
            object_store.KEYRING_ENVIRONMENT_VARIABLE: encoded_keyring(),
        }

    def test_local_is_filesystem_only(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(object_store.config, "get_runtime_environment", return_value="local"):
                self.assertEqual(object_store.validate_object_store_configuration(), "filesystem")
            with patch.dict(
                os.environ,
                {object_store.OBJECT_STORE_ENVIRONMENT_VARIABLE: "r2_encrypted"},
            ):
                with patch.object(object_store.config, "get_runtime_environment", return_value="local"):
                    with self.assertRaises(object_store.ObjectStoreConfigurationError):
                        object_store.validate_object_store_configuration()

    def test_shared_validates_r2_and_separate_keyring(self):
        with patch.dict(os.environ, self.environment, clear=True):
            with patch.object(object_store.config, "get_runtime_environment", return_value="staging"):
                self.assertEqual(
                    object_store.validate_object_store_configuration(),
                    object_store.SHARED_OBJECT_STORE,
                )
                configuration = object_store.r2_configuration_from_environment()
                self.assertEqual(configuration.bucket, "careertrellis-staging-objects")
                self.assertNotIn(self.environment[object_store.ACCESS_KEY_ENVIRONMENT_VARIABLE], repr(configuration))
                self.assertNotIn(self.environment[object_store.SECRET_KEY_ENVIRONMENT_VARIABLE], repr(configuration))

    def test_shared_fails_closed_for_missing_or_unsafe_values(self):
        mutations = (
            (object_store.OBJECT_STORE_ENVIRONMENT_VARIABLE, ""),
            (object_store.ENDPOINT_ENVIRONMENT_VARIABLE, "http://" + "a" * 32 + ".r2.cloudflarestorage.com"),
            (object_store.ENDPOINT_ENVIRONMENT_VARIABLE, "https://example.com"),
            (object_store.BUCKET_ENVIRONMENT_VARIABLE, "Invalid_Bucket"),
            (object_store.ACCESS_KEY_ENVIRONMENT_VARIABLE, "short"),
            (object_store.SECRET_KEY_ENVIRONMENT_VARIABLE, "replace-with-secret"),
            (object_store.ACTIVE_KEY_ID_ENVIRONMENT_VARIABLE, "missing"),
        )
        for name, value in mutations:
            environment = dict(self.environment)
            environment[name] = value
            with self.subTest(name=name, value=value):
                with patch.dict(os.environ, environment, clear=True):
                    with patch.object(object_store.config, "get_runtime_environment", return_value="staging"):
                        with self.assertRaises(object_store.ObjectStoreConfigurationError):
                            object_store.validate_object_store_configuration()

    def test_keyring_rejects_wrong_key_length(self):
        with self.assertRaises(object_store.ObjectStoreConfigurationError):
            object_store.parse_object_keyring(
                active_key_id="object-kek-1",
                encoded_keyring=encoded_keyring(key=b"short"),
            )


if __name__ == "__main__":
    unittest.main()
