"""Private, owner-bound encrypted object storage.

The browser never receives object-store credentials or direct object URLs.  A
random data-encryption key protects each object before the backend sees it;
that key is wrapped with an independently managed application key-encryption
key.  PostgreSQL persists only :class:`StoredObjectMetadata`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import config


OBJECT_STORE_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_OBJECT_STORE"
ENDPOINT_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_OBJECT_STORE_ENDPOINT"
BUCKET_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_OBJECT_STORE_BUCKET"
ACCESS_KEY_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_OBJECT_STORE_ACCESS_KEY_ID"
SECRET_KEY_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_OBJECT_STORE_SECRET_ACCESS_KEY"
ACTIVE_KEY_ID_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_OBJECT_STORE_ACTIVE_KEY_ID"
KEYRING_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_OBJECT_STORE_KEYRING_B64"

LOCAL_OBJECT_STORE = "filesystem"
SHARED_OBJECT_STORE = "r2_encrypted"
OBJECT_FORMAT_VERSION = 1
MAXIMUM_OBJECT_BYTES = 10 * 1024 * 1024
MAXIMUM_ENCRYPTED_BYTES = MAXIMUM_OBJECT_BYTES + 64
MAXIMUM_WRAPPED_KEY_BYTES = 192
NONCE_BYTES = 12
KEY_BYTES = 32
OBJECT_MAGIC = b"CTOB\x01"
WRAPPED_KEY_MAGIC = b"CTWK\x01"
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
BUCKET_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
R2_HOST_PATTERN = re.compile(r"^[0-9a-f]{32}\.r2\.cloudflarestorage\.com$")
ALLOWED_CONTENT_TYPES = frozenset({"application/pdf", "text/plain; charset=utf-8"})


class ObjectStoreConfigurationError(config.RuntimeConfigurationError):
    """The encrypted object store is missing or unsafe."""


class ObjectStoreIntegrityError(RuntimeError):
    """An object could not be authenticated or decrypted."""


class ObjectStoreBackendError(RuntimeError):
    """The private object backend could not complete an operation."""


@dataclass(frozen=True)
class ObjectKeyring:
    active_key_id: str
    keys: dict[str, bytes] = field(repr=False)


@dataclass(frozen=True)
class StoredObjectMetadata:
    """The immutable owner-scoped record persisted outside object storage."""

    id: uuid.UUID
    owner_user_id: int
    object_key: str
    content_type: str
    size_bytes: int
    sha256: str
    wrapped_data_key: bytes

    @property
    def object_id(self) -> uuid.UUID:
        """Readable alias for call sites that do not use the database column name."""
        return self.id


@dataclass(frozen=True)
class EncryptedStagedObject:
    metadata: StoredObjectMetadata
    encrypted_payload: bytes


@dataclass(frozen=True)
class R2ObjectStoreConfiguration:
    endpoint: str
    bucket: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)


@runtime_checkable
class ObjectBackend(Protocol):
    """Minimal private-blob interface; implementations must not create URLs."""

    def put(self, object_key: str, payload: bytes) -> None: ...

    def get(self, object_key: str) -> bytes: ...

    def delete(self, object_key: str) -> None: ...

    def head(self, object_key: str) -> int | None: ...

    def check_ready(self) -> None: ...


def _configuration_error(message: str) -> ObjectStoreConfigurationError:
    return ObjectStoreConfigurationError(message)


def _integrity_error() -> ObjectStoreIntegrityError:
    return ObjectStoreIntegrityError("The stored object is unavailable.")


def _positive_owner_user_id(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("An authenticated owner is required.")
    try:
        owner_user_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("An authenticated owner is required.") from exc
    if owner_user_id <= 0 or str(owner_user_id) != str(value):
        raise ValueError("An authenticated owner is required.")
    return owner_user_id


def _urlsafe_decode(value: str, label: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise _configuration_error(f"{label} is not valid URL-safe base64.") from exc


def parse_object_keyring(
    *,
    active_key_id: str | None = None,
    encoded_keyring: str | None = None,
) -> ObjectKeyring:
    """Parse the bounded base64url-encoded JSON KEK map from configuration."""
    selected_key_id = (
        os.environ.get(ACTIVE_KEY_ID_ENVIRONMENT_VARIABLE, "")
        if active_key_id is None
        else active_key_id
    ).strip()
    selected_keyring = (
        os.environ.get(KEYRING_ENVIRONMENT_VARIABLE, "")
        if encoded_keyring is None
        else encoded_keyring
    ).strip()
    if not KEY_ID_PATTERN.fullmatch(selected_key_id):
        raise _configuration_error(
            f"{ACTIVE_KEY_ID_ENVIRONMENT_VARIABLE} must be a bounded key identifier."
        )
    if not selected_keyring or len(selected_keyring) > 16384:
        raise _configuration_error(f"{KEYRING_ENVIRONMENT_VARIABLE} is required and bounded.")
    try:
        document = json.loads(_urlsafe_decode(selected_keyring, KEYRING_ENVIRONMENT_VARIABLE))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _configuration_error("The object-encryption keyring is invalid.") from exc
    if not isinstance(document, dict) or not document or len(document) > 32:
        raise _configuration_error("The object-encryption keyring must be a bounded JSON object.")

    keys: dict[str, bytes] = {}
    for key_id, encoded_key in document.items():
        if not isinstance(key_id, str) or not KEY_ID_PATTERN.fullmatch(key_id):
            raise _configuration_error("The object-encryption keyring has an invalid key ID.")
        if not isinstance(encoded_key, str) or len(encoded_key) > 128:
            raise _configuration_error("The object-encryption keyring has an invalid key.")
        key = _urlsafe_decode(encoded_key, "An object-encryption key")
        if len(key) != KEY_BYTES:
            raise _configuration_error(
                "Every object-encryption key must contain exactly 32 bytes."
            )
        keys[key_id] = key
    if selected_key_id not in keys:
        raise _configuration_error(
            "The active object-encryption key ID is not present in the keyring."
        )
    return ObjectKeyring(active_key_id=selected_key_id, keys=keys)


def _normalize_content_type(value: object, plaintext: bytes | None = None) -> str:
    content_type = str(value or "").strip().lower()
    if content_type == "text/plain":
        content_type = "text/plain; charset=utf-8"
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError("Stored objects must be PDF or UTF-8 plain text.")
    if plaintext is not None and content_type == "text/plain; charset=utf-8":
        try:
            plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Stored text objects must be valid UTF-8.") from exc
    return content_type


def _validate_plaintext(value: object, content_type: object) -> tuple[bytes, str]:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("Stored object content must be bytes.")
    plaintext = bytes(value)
    if not plaintext or len(plaintext) > MAXIMUM_OBJECT_BYTES:
        raise ValueError("Stored object content is empty or exceeds 10 MiB.")
    return plaintext, _normalize_content_type(content_type, plaintext)


def _metadata_aad(metadata: StoredObjectMetadata) -> bytes:
    document = {
        "content_type": metadata.content_type,
        "format": OBJECT_FORMAT_VERSION,
        "object_id": str(metadata.id),
        "object_key": metadata.object_key,
        "owner_user_id": metadata.owner_user_id,
        "sha256": metadata.sha256,
        "size_bytes": metadata.size_bytes,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")


def _validate_metadata(metadata: object, owner_user_id: object) -> StoredObjectMetadata:
    try:
        owner_id = _positive_owner_user_id(owner_user_id)
    except ValueError as exc:
        raise _integrity_error() from exc
    if not isinstance(metadata, StoredObjectMetadata) or metadata.owner_user_id != owner_id:
        raise _integrity_error()
    if not isinstance(metadata.id, uuid.UUID) or metadata.object_key != f"v1/{metadata.id.hex}":
        raise _integrity_error()
    if metadata.content_type not in ALLOWED_CONTENT_TYPES:
        raise _integrity_error()
    if not 1 <= metadata.size_bytes <= MAXIMUM_OBJECT_BYTES:
        raise _integrity_error()
    if not re.fullmatch(r"[0-9a-f]{64}", metadata.sha256):
        raise _integrity_error()
    if not isinstance(metadata.wrapped_data_key, bytes):
        raise _integrity_error()
    if not 1 <= len(metadata.wrapped_data_key) <= MAXIMUM_WRAPPED_KEY_BYTES:
        raise _integrity_error()
    return metadata


def _wrap_data_key(metadata: StoredObjectMetadata, data_key: bytes, keyring: ObjectKeyring) -> bytes:
    key_id = keyring.active_key_id
    encoded_key_id = key_id.encode("ascii")
    nonce = secrets.token_bytes(NONCE_BYTES)
    wrapped = AESGCM(keyring.keys[key_id]).encrypt(
        nonce,
        data_key,
        _metadata_aad(metadata) + b"\x00kek:" + encoded_key_id,
    )
    document = WRAPPED_KEY_MAGIC + bytes((len(encoded_key_id),)) + encoded_key_id + nonce + wrapped
    if len(document) > MAXIMUM_WRAPPED_KEY_BYTES:
        raise _integrity_error()
    return document


def _unwrap_data_key(metadata: StoredObjectMetadata, keyring: ObjectKeyring) -> bytes:
    raw = metadata.wrapped_data_key
    minimum = len(WRAPPED_KEY_MAGIC) + 1 + 1 + NONCE_BYTES + KEY_BYTES + 16
    if len(raw) < minimum or not raw.startswith(WRAPPED_KEY_MAGIC):
        raise _integrity_error()
    offset = len(WRAPPED_KEY_MAGIC)
    key_id_length = raw[offset]
    offset += 1
    if not 1 <= key_id_length <= 80 or len(raw) != offset + key_id_length + NONCE_BYTES + KEY_BYTES + 16:
        raise _integrity_error()
    encoded_key_id = raw[offset : offset + key_id_length]
    offset += key_id_length
    try:
        key_id = encoded_key_id.decode("ascii")
    except UnicodeDecodeError as exc:
        raise _integrity_error() from exc
    if not KEY_ID_PATTERN.fullmatch(key_id):
        raise _integrity_error()
    wrapping_key = keyring.keys.get(key_id)
    if wrapping_key is None:
        raise _integrity_error()
    nonce = raw[offset : offset + NONCE_BYTES]
    encrypted_key = raw[offset + NONCE_BYTES :]
    try:
        data_key = AESGCM(wrapping_key).decrypt(
            nonce,
            encrypted_key,
            _metadata_aad(metadata) + b"\x00kek:" + encoded_key_id,
        )
    except InvalidTag as exc:
        raise _integrity_error() from exc
    if len(data_key) != KEY_BYTES:
        raise _integrity_error()
    return data_key


def encrypt_object(
    owner_user_id: object,
    plaintext: object,
    content_type: object,
    *,
    keyring: ObjectKeyring | None = None,
) -> EncryptedStagedObject:
    """Envelope-encrypt one new opaque object entirely in application memory."""
    owner_id = _positive_owner_user_id(owner_user_id)
    content, normalized_content_type = _validate_plaintext(plaintext, content_type)
    selected_keyring = keyring or parse_object_keyring()
    object_id = uuid.uuid4()
    data_key = secrets.token_bytes(KEY_BYTES)
    metadata_without_wrapped_key = StoredObjectMetadata(
        id=object_id,
        owner_user_id=owner_id,
        object_key=f"v1/{object_id.hex}",
        content_type=normalized_content_type,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        wrapped_data_key=b"pending",
    )
    wrapped_data_key = _wrap_data_key(metadata_without_wrapped_key, data_key, selected_keyring)
    metadata = StoredObjectMetadata(
        id=metadata_without_wrapped_key.id,
        owner_user_id=metadata_without_wrapped_key.owner_user_id,
        object_key=metadata_without_wrapped_key.object_key,
        content_type=metadata_without_wrapped_key.content_type,
        size_bytes=metadata_without_wrapped_key.size_bytes,
        sha256=metadata_without_wrapped_key.sha256,
        wrapped_data_key=wrapped_data_key,
    )
    nonce = secrets.token_bytes(NONCE_BYTES)
    encrypted = AESGCM(data_key).encrypt(nonce, content, _metadata_aad(metadata))
    return EncryptedStagedObject(
        metadata=metadata,
        encrypted_payload=OBJECT_MAGIC + nonce + encrypted,
    )


def decrypt_object(
    metadata: object,
    encrypted_payload: object,
    owner_user_id: object,
    *,
    keyring: ObjectKeyring | None = None,
) -> bytes:
    """Authenticate owner, metadata, wrapped DEK, payload, size, and digest."""
    selected_metadata = _validate_metadata(metadata, owner_user_id)
    if not isinstance(encrypted_payload, (bytes, bytearray, memoryview)):
        raise _integrity_error()
    raw = bytes(encrypted_payload)
    minimum = len(OBJECT_MAGIC) + NONCE_BYTES + 16 + 1
    if len(raw) < minimum or len(raw) > MAXIMUM_ENCRYPTED_BYTES or not raw.startswith(OBJECT_MAGIC):
        raise _integrity_error()
    selected_keyring = keyring or parse_object_keyring()
    data_key = _unwrap_data_key(selected_metadata, selected_keyring)
    offset = len(OBJECT_MAGIC)
    try:
        plaintext = AESGCM(data_key).decrypt(
            raw[offset : offset + NONCE_BYTES],
            raw[offset + NONCE_BYTES :],
            _metadata_aad(selected_metadata),
        )
    except InvalidTag as exc:
        raise _integrity_error() from exc
    if (
        len(plaintext) != selected_metadata.size_bytes
        or hashlib.sha256(plaintext).hexdigest() != selected_metadata.sha256
    ):
        raise _integrity_error()
    if selected_metadata.content_type == "text/plain; charset=utf-8":
        try:
            plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _integrity_error() from exc
    return plaintext


class InMemoryObjectBackend:
    """Deterministic private backend for unit tests and bounded rehearsals."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, object_key: str, payload: bytes) -> None:
        self.objects[object_key] = bytes(payload)

    def get(self, object_key: str) -> bytes:
        try:
            return self.objects[object_key]
        except KeyError as exc:
            raise ObjectStoreBackendError("The private object is unavailable.") from exc

    def delete(self, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def head(self, object_key: str) -> int | None:
        value = self.objects.get(object_key)
        return None if value is None else len(value)

    def check_ready(self) -> None:
        return None


def _safe_object_key(object_key: str) -> str:
    if not re.fullmatch(r"v1/[0-9a-f]{32}", object_key):
        raise ObjectStoreBackendError("The private object key is invalid.")
    return object_key


class FilesystemObjectBackend:
    """Local-only backend retaining the standalone app's filesystem behavior."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or (config.OUTPUT_DIR / ".encrypted-objects")).resolve()

    def _path(self, object_key: str) -> Path:
        relative = _safe_object_key(object_key)
        path = (self.root / relative).resolve()
        if self.root not in path.parents:
            raise ObjectStoreBackendError("The private object key is invalid.")
        return path

    def put(self, object_key: str, payload: bytes) -> None:
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            temporary.write_bytes(bytes(payload))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, object_key: str) -> bytes:
        path = self._path(object_key)
        try:
            if path.stat().st_size > MAXIMUM_ENCRYPTED_BYTES:
                raise ObjectStoreBackendError("The private object is unavailable.")
            return path.read_bytes()
        except (FileNotFoundError, OSError) as exc:
            raise ObjectStoreBackendError("The private object is unavailable.") from exc

    def delete(self, object_key: str) -> None:
        self._path(object_key).unlink(missing_ok=True)

    def head(self, object_key: str) -> int | None:
        try:
            return self._path(object_key).stat().st_size
        except FileNotFoundError:
            return None

    def check_ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ObjectStoreBackendError("The local private object store is unavailable.")


class R2ObjectBackend:
    """Lazy S3-compatible client for one private Cloudflare R2 bucket."""

    def __init__(self, configuration: R2ObjectStoreConfiguration) -> None:
        self.configuration = configuration
        self._s3_client = None

    def _client(self):
        if self._s3_client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:
                raise ObjectStoreConfigurationError(
                    "The shared object store requires the reviewed boto3 dependency."
                ) from exc
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.configuration.endpoint,
                aws_access_key_id=self.configuration.access_key_id,
                aws_secret_access_key=self.configuration.secret_access_key,
                region_name="auto",
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
        return self._s3_client

    def put(self, object_key: str, payload: bytes) -> None:
        try:
            self._client().put_object(
                Bucket=self.configuration.bucket,
                Key=_safe_object_key(object_key),
                Body=bytes(payload),
                ContentLength=len(payload),
                ContentType="application/octet-stream",
            )
        except ObjectStoreConfigurationError:
            raise
        except Exception as exc:
            raise ObjectStoreBackendError("The private object upload failed.") from exc

    def get(self, object_key: str) -> bytes:
        try:
            response = self._client().get_object(
                Bucket=self.configuration.bucket,
                Key=_safe_object_key(object_key),
            )
            if int(response.get("ContentLength", MAXIMUM_ENCRYPTED_BYTES + 1)) > MAXIMUM_ENCRYPTED_BYTES:
                raise ObjectStoreBackendError("The private object is unavailable.")
            body = response["Body"]
            try:
                payload = body.read(MAXIMUM_ENCRYPTED_BYTES + 1)
            finally:
                body.close()
            if len(payload) > MAXIMUM_ENCRYPTED_BYTES:
                raise ObjectStoreBackendError("The private object is unavailable.")
            return bytes(payload)
        except (ObjectStoreConfigurationError, ObjectStoreBackendError):
            raise
        except Exception as exc:
            raise ObjectStoreBackendError("The private object is unavailable.") from exc

    def delete(self, object_key: str) -> None:
        try:
            self._client().delete_object(
                Bucket=self.configuration.bucket,
                Key=_safe_object_key(object_key),
            )
        except ObjectStoreConfigurationError:
            raise
        except Exception as exc:
            raise ObjectStoreBackendError("The private object deletion failed.") from exc

    def head(self, object_key: str) -> int | None:
        try:
            response = self._client().head_object(
                Bucket=self.configuration.bucket,
                Key=_safe_object_key(object_key),
            )
            return int(response["ContentLength"])
        except ObjectStoreConfigurationError:
            raise
        except Exception as exc:
            response = getattr(exc, "response", {})
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return None
            raise ObjectStoreBackendError("The private object is unavailable.") from exc

    def check_ready(self) -> None:
        try:
            self._client().head_bucket(Bucket=self.configuration.bucket)
        except ObjectStoreConfigurationError:
            raise
        except Exception as exc:
            raise ObjectStoreBackendError("The private object store is unavailable.") from exc


def stage_object(
    backend: ObjectBackend,
    owner_user_id: object,
    plaintext: object,
    content_type: object,
    *,
    keyring: ObjectKeyring | None = None,
) -> EncryptedStagedObject:
    """Upload, read back, authenticate, and return metadata for a new object."""
    staged = encrypt_object(owner_user_id, plaintext, content_type, keyring=keyring)
    uploaded = False
    try:
        backend.put(staged.metadata.object_key, staged.encrypted_payload)
        uploaded = True
        size = backend.head(staged.metadata.object_key)
        if size != len(staged.encrypted_payload):
            raise _integrity_error()
        readback = backend.get(staged.metadata.object_key)
        verified = decrypt_object(
            staged.metadata,
            readback,
            staged.metadata.owner_user_id,
            keyring=keyring,
        )
        if verified != bytes(plaintext):
            raise _integrity_error()
        return staged
    except Exception:
        if uploaded:
            try:
                backend.delete(staged.metadata.object_key)
            except Exception:
                pass
        raise


def read_object(
    backend: ObjectBackend,
    metadata: StoredObjectMetadata,
    owner_user_id: object,
    *,
    keyring: ObjectKeyring | None = None,
) -> bytes:
    """Fetch and decrypt an object only for the metadata's authenticated owner."""
    selected_metadata = _validate_metadata(metadata, owner_user_id)
    try:
        payload = backend.get(selected_metadata.object_key)
    except ObjectStoreBackendError:
        raise
    return decrypt_object(
        selected_metadata,
        payload,
        owner_user_id,
        keyring=keyring,
    )


def discard_staged_object(backend: ObjectBackend, staged: EncryptedStagedObject) -> None:
    """Compensate for a metadata/database failure after a successful upload."""
    if not isinstance(staged, EncryptedStagedObject):
        raise ValueError("A staged object is required.")
    backend.delete(staged.metadata.object_key)


def _bounded_credential(name: str, minimum: int, maximum: int) -> str:
    value = os.environ.get(name, "").strip()
    if (
        not minimum <= len(value) <= maximum
        or value.lower().startswith("replace-")
        or any(character.isspace() for character in value)
    ):
        raise _configuration_error(f"{name} is missing or invalid.")
    return value


def r2_configuration_from_environment() -> R2ObjectStoreConfiguration:
    endpoint = os.environ.get(ENDPOINT_ENVIRONMENT_VARIABLE, "").strip()
    try:
        parsed = urlsplit(endpoint)
    except ValueError as exc:
        raise _configuration_error(f"{ENDPOINT_ENVIRONMENT_VARIABLE} is invalid.") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not R2_HOST_PATTERN.fullmatch((parsed.hostname or "").lower())
    ):
        raise _configuration_error(
            f"{ENDPOINT_ENVIRONMENT_VARIABLE} must be an HTTPS Cloudflare R2 account endpoint."
        )
    bucket = os.environ.get(BUCKET_ENVIRONMENT_VARIABLE, "").strip()
    if not BUCKET_PATTERN.fullmatch(bucket) or "--" in bucket:
        raise _configuration_error(f"{BUCKET_ENVIRONMENT_VARIABLE} is invalid.")
    return R2ObjectStoreConfiguration(
        endpoint=f"https://{parsed.hostname.lower()}",
        bucket=bucket,
        access_key_id=_bounded_credential(ACCESS_KEY_ENVIRONMENT_VARIABLE, 16, 128),
        secret_access_key=_bounded_credential(SECRET_KEY_ENVIRONMENT_VARIABLE, 32, 256),
    )


def object_store_kind() -> str:
    """Retain local filesystem behavior and fail closed on every shared host."""
    configured = os.environ.get(OBJECT_STORE_ENVIRONMENT_VARIABLE, "").strip().lower()
    if config.get_runtime_environment() == "local":
        if configured not in {"", LOCAL_OBJECT_STORE}:
            raise _configuration_error(
                f"Local mode requires {OBJECT_STORE_ENVIRONMENT_VARIABLE} to be unset or {LOCAL_OBJECT_STORE}."
            )
        return LOCAL_OBJECT_STORE
    if configured != SHARED_OBJECT_STORE:
        raise _configuration_error(
            f"Shared-host mode requires {OBJECT_STORE_ENVIRONMENT_VARIABLE}={SHARED_OBJECT_STORE}."
        )
    r2_configuration_from_environment()
    parse_object_keyring()
    return SHARED_OBJECT_STORE


def validate_object_store_configuration() -> str:
    """Validate store selection, credentials, endpoint, bucket, and keyring."""
    return object_store_kind()


def runtime_object_backend() -> ObjectBackend:
    kind = object_store_kind()
    if kind == LOCAL_OBJECT_STORE:
        return FilesystemObjectBackend()
    return R2ObjectBackend(r2_configuration_from_environment())


def check_object_store_ready(backend: ObjectBackend | None = None) -> None:
    """Check reachability without uploading content or generating a public URL."""
    selected = backend or runtime_object_backend()
    selected.check_ready()
