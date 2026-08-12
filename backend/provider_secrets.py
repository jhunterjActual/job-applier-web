"""Per-user encrypted provider credentials for shared-host runtimes.

Standalone local mode intentionally retains the existing SQLite behavior. In a
shared-host process, provider credentials live only in the PostgreSQL
``provider_secrets`` table and are encrypted before they reach the database.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
import json
import os
import re
import secrets
from typing import Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

import config
from postgres_database import (
    PostgreSQLConfigurationError,
    database_url_from_environment,
    runtime_postgres_engine,
    tenant_transaction,
)


SECRET_STORE_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_PROVIDER_SECRET_STORE"
ACTIVE_KEY_ID_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_PROVIDER_SECRET_ACTIVE_KEY_ID"
KEYRING_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_PROVIDER_SECRET_KEYRING_B64"
SHARED_SECRET_STORE = "postgres_encrypted"
ALLOWED_PROVIDERS = frozenset({"gemini", "openai", "google_maps"})
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
CIPHERTEXT_VERSION = 1
NONCE_BYTES = 12
MAXIMUM_SECRET_BYTES = 4096


class ProviderSecretConfigurationError(config.RuntimeConfigurationError):
    """The shared-host credential store is missing or unsafe."""


class ProviderSecretAuthorizationError(PermissionError):
    """No authenticated owner is available for a credential operation."""


class ProviderSecretIntegrityError(RuntimeError):
    """A stored credential could not be authenticated or decrypted."""


@dataclass(frozen=True)
class ProviderSecretKeyring:
    active_key_id: str
    keys: dict[str, bytes]


_current_owner_user_id: ContextVar[int | None] = ContextVar(
    "careertrellis_current_owner_user_id",
    default=None,
)


def _positive_user_id(value: object) -> int:
    if isinstance(value, bool):
        raise ProviderSecretAuthorizationError("An authenticated user is required.")
    try:
        user_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderSecretAuthorizationError("An authenticated user is required.") from exc
    if user_id <= 0 or str(user_id) != str(value):
        raise ProviderSecretAuthorizationError("An authenticated user is required.")
    return user_id


def _provider(value: object) -> str:
    provider = str(value or "").strip().lower()
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("The provider credential type is not supported.")
    return provider


def _urlsafe_decode(value: str, label: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ProviderSecretConfigurationError(f"{label} is not valid URL-safe base64.") from exc


def _parse_keyring() -> ProviderSecretKeyring:
    active_key_id = os.environ.get(ACTIVE_KEY_ID_ENVIRONMENT_VARIABLE, "").strip()
    encoded_keyring = os.environ.get(KEYRING_ENVIRONMENT_VARIABLE, "").strip()
    if not KEY_ID_PATTERN.fullmatch(active_key_id):
        raise ProviderSecretConfigurationError(
            f"{ACTIVE_KEY_ID_ENVIRONMENT_VARIABLE} must be a bounded key identifier."
        )
    if not encoded_keyring:
        raise ProviderSecretConfigurationError(f"{KEYRING_ENVIRONMENT_VARIABLE} is required.")
    try:
        document = json.loads(_urlsafe_decode(encoded_keyring, KEYRING_ENVIRONMENT_VARIABLE))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderSecretConfigurationError("The provider-secret keyring is invalid.") from exc
    if not isinstance(document, dict) or not document:
        raise ProviderSecretConfigurationError("The provider-secret keyring must be a JSON object.")

    keys: dict[str, bytes] = {}
    for key_id, encoded_key in document.items():
        if not isinstance(key_id, str) or not KEY_ID_PATTERN.fullmatch(key_id):
            raise ProviderSecretConfigurationError("The provider-secret keyring has an invalid key ID.")
        if not isinstance(encoded_key, str):
            raise ProviderSecretConfigurationError("The provider-secret keyring has an invalid key.")
        key = _urlsafe_decode(encoded_key, "A provider-secret encryption key")
        if len(key) != 32:
            raise ProviderSecretConfigurationError(
                "Every provider-secret encryption key must contain exactly 32 bytes."
            )
        keys[key_id] = key
    if active_key_id not in keys:
        raise ProviderSecretConfigurationError(
            "The active provider-secret key ID is not present in the keyring."
        )
    return ProviderSecretKeyring(active_key_id=active_key_id, keys=keys)


def provider_secret_store_kind() -> str:
    """Return the explicit store, retaining plaintext SQLite only for local use."""
    configured = os.environ.get(SECRET_STORE_ENVIRONMENT_VARIABLE, "").strip().lower()
    if config.get_runtime_environment() == "local":
        if configured not in {"", "sqlite"}:
            raise ProviderSecretConfigurationError(
                f"Local mode requires {SECRET_STORE_ENVIRONMENT_VARIABLE} to be unset or sqlite."
            )
        return "sqlite"
    if configured != SHARED_SECRET_STORE:
        raise ProviderSecretConfigurationError(
            f"Shared-host mode requires {SECRET_STORE_ENVIRONMENT_VARIABLE}={SHARED_SECRET_STORE}."
        )
    try:
        database_url_from_environment()
    except PostgreSQLConfigurationError as exc:
        raise ProviderSecretConfigurationError(
            "Shared-host provider secrets require a valid CAREERTRELLIS_DATABASE_URL."
        ) from exc
    _parse_keyring()
    return SHARED_SECRET_STORE


def validate_provider_secret_store_configuration() -> str:
    """Validate the store and keyring without opening a database connection."""
    return provider_secret_store_kind()


def check_provider_secret_store_ready() -> None:
    """Verify the encrypted table is reachable without exposing any owner row."""
    if provider_secret_store_kind() == "sqlite":
        return
    with runtime_postgres_engine().begin() as connection:
        connection.execute(text("SELECT 1 FROM careertrellis.provider_secrets LIMIT 0"))


def set_current_owner_user_id(user_id: object) -> Token:
    """Bind a previously authorized local user to the current request context."""
    return _current_owner_user_id.set(_positive_user_id(user_id))


def reset_current_owner_user_id(token: Token) -> None:
    _current_owner_user_id.reset(token)


@contextmanager
def owner_user_scope(user_id: object) -> Iterator[int]:
    token = set_current_owner_user_id(user_id)
    try:
        yield _positive_user_id(user_id)
    finally:
        reset_current_owner_user_id(token)


def current_owner_user_id() -> int:
    value = _current_owner_user_id.get()
    if value is None:
        raise ProviderSecretAuthorizationError("An authenticated user is required.")
    return _positive_user_id(value)


def _associated_data(owner_user_id: int, provider: str, key_id: str) -> bytes:
    return (
        f"careertrellis-provider-secret:{CIPHERTEXT_VERSION}:"
        f"{owner_user_id}:{provider}:{key_id}"
    ).encode("ascii")


def encrypt_provider_secret(
    owner_user_id: object,
    provider: object,
    plaintext: str,
    *,
    keyring: ProviderSecretKeyring | None = None,
) -> tuple[str, bytes]:
    """Encrypt one credential with tenant/provider-bound authenticated data."""
    user_id = _positive_user_id(owner_user_id)
    normalized_provider = _provider(provider)
    if not isinstance(plaintext, str):
        raise ValueError("The provider credential must be text.")
    encoded = plaintext.strip().encode("utf-8")
    if not encoded or len(encoded) > MAXIMUM_SECRET_BYTES:
        raise ValueError("The provider credential is empty or too long.")
    selected = keyring or _parse_keyring()
    nonce = secrets.token_bytes(NONCE_BYTES)
    encrypted = AESGCM(selected.keys[selected.active_key_id]).encrypt(
        nonce,
        encoded,
        _associated_data(user_id, normalized_provider, selected.active_key_id),
    )
    return selected.active_key_id, bytes((CIPHERTEXT_VERSION,)) + nonce + encrypted


def decrypt_provider_secret(
    owner_user_id: object,
    provider: object,
    key_id: object,
    ciphertext: object,
    *,
    keyring: ProviderSecretKeyring | None = None,
) -> str:
    """Authenticate and decrypt a credential without exposing failure details."""
    user_id = _positive_user_id(owner_user_id)
    normalized_provider = _provider(provider)
    normalized_key_id = str(key_id or "")
    selected = keyring or _parse_keyring()
    key = selected.keys.get(normalized_key_id)
    raw = bytes(ciphertext or b"")
    if (
        key is None
        or len(raw) <= 1 + NONCE_BYTES + 16
        or raw[0] != CIPHERTEXT_VERSION
    ):
        raise ProviderSecretIntegrityError("The stored provider credential is unavailable.")
    nonce = raw[1 : 1 + NONCE_BYTES]
    encrypted = raw[1 + NONCE_BYTES :]
    try:
        plaintext = AESGCM(key).decrypt(
            nonce,
            encrypted,
            _associated_data(user_id, normalized_provider, normalized_key_id),
        ).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise ProviderSecretIntegrityError(
            "The stored provider credential is unavailable."
        ) from exc
    if not plaintext or len(plaintext.encode("utf-8")) > MAXIMUM_SECRET_BYTES:
        raise ProviderSecretIntegrityError("The stored provider credential is unavailable.")
    return plaintext


def _revocation_ciphertext(
    owner_user_id: int,
    provider: str,
    keyring: ProviderSecretKeyring,
) -> bytes:
    nonce = secrets.token_bytes(NONCE_BYTES)
    return bytes((CIPHERTEXT_VERSION,)) + nonce + AESGCM(
        keyring.keys[keyring.active_key_id]
    ).encrypt(
        nonce,
        b"",
        _associated_data(owner_user_id, provider, keyring.active_key_id),
    )


def update_provider_secrets(owner_user_id: object, updates: dict[object, str]) -> None:
    """Atomically replace or revoke every explicitly supplied owner credential."""
    user_id = _positive_user_id(owner_user_id)
    keyring = _parse_keyring()
    prepared: list[dict[str, object]] = []
    for provider, plaintext in updates.items():
        normalized_provider = _provider(provider)
        if not isinstance(plaintext, str):
            raise ValueError("The provider credential must be text.")
        if plaintext.strip():
            key_id, ciphertext = encrypt_provider_secret(
                user_id,
                normalized_provider,
                plaintext,
                keyring=keyring,
            )
            revoked = False
        else:
            key_id = keyring.active_key_id
            ciphertext = _revocation_ciphertext(user_id, normalized_provider, keyring)
            revoked = True
        prepared.append(
            {
                "owner_user_id": user_id,
                "provider": normalized_provider,
                "key_id": key_id,
                "ciphertext": ciphertext,
                "revoked": revoked,
            }
        )
    if not prepared:
        return
    with tenant_transaction(runtime_postgres_engine(), user_id) as connection:
        for values in prepared:
            connection.execute(
                text(
                    """
                    INSERT INTO careertrellis.provider_secrets (
                        owner_user_id, provider, key_id, ciphertext, revoked_at
                    ) VALUES (
                        :owner_user_id, :provider, :key_id, :ciphertext,
                        CASE WHEN :revoked THEN CURRENT_TIMESTAMP ELSE NULL END
                    )
                    ON CONFLICT (owner_user_id, provider) DO UPDATE SET
                        key_id = EXCLUDED.key_id,
                        ciphertext = EXCLUDED.ciphertext,
                        updated_at = CURRENT_TIMESTAMP,
                        revoked_at = EXCLUDED.revoked_at
                    """
                ),
                values,
            )


def save_provider_secret(owner_user_id: object, provider: object, plaintext: str) -> None:
    """Insert or atomically replace one encrypted, owner-scoped credential."""
    update_provider_secrets(owner_user_id, {provider: plaintext})


def revoke_provider_secret(owner_user_id: object, provider: object) -> None:
    """Cryptographically erase a stored value while retaining a revocation row."""
    update_provider_secrets(owner_user_id, {provider: ""})


def load_provider_secret(owner_user_id: object, provider: object) -> str:
    """Return one decrypted credential visible through the owner's RLS scope."""
    user_id = _positive_user_id(owner_user_id)
    normalized_provider = _provider(provider)
    with tenant_transaction(runtime_postgres_engine(), user_id) as connection:
        row = connection.execute(
            text(
                """
                SELECT key_id, ciphertext
                FROM careertrellis.provider_secrets
                WHERE owner_user_id = :owner_user_id
                  AND provider = :provider
                  AND revoked_at IS NULL
                """
            ),
            {"owner_user_id": user_id, "provider": normalized_provider},
        ).mappings().one_or_none()
    if row is None:
        return ""
    return decrypt_provider_secret(
        user_id,
        normalized_provider,
        row["key_id"],
        row["ciphertext"],
    )


def provider_secret_configured(owner_user_id: object, provider: object) -> bool:
    """Return presence only after proving the ciphertext is still decryptable."""
    return bool(load_provider_secret(owner_user_id, provider))


def rotate_provider_secrets(owner_user_id: object) -> int:
    """Re-encrypt every active owner credential with the configured active key."""
    user_id = _positive_user_id(owner_user_id)
    keyring = _parse_keyring()
    with tenant_transaction(runtime_postgres_engine(), user_id) as connection:
        rows = connection.execute(
            text(
                """
                SELECT provider, key_id, ciphertext
                FROM careertrellis.provider_secrets
                WHERE owner_user_id = :owner_user_id AND revoked_at IS NULL
                ORDER BY provider
                FOR UPDATE
                """
            ),
            {"owner_user_id": user_id},
        ).mappings().all()
        for row in rows:
            plaintext = decrypt_provider_secret(
                user_id,
                row["provider"],
                row["key_id"],
                row["ciphertext"],
                keyring=keyring,
            )
            key_id, ciphertext = encrypt_provider_secret(
                user_id,
                row["provider"],
                plaintext,
                keyring=keyring,
            )
            connection.execute(
                text(
                    """
                    UPDATE careertrellis.provider_secrets
                    SET key_id = :key_id,
                        ciphertext = :ciphertext,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE owner_user_id = :owner_user_id AND provider = :provider
                    """
                ),
                {
                    "owner_user_id": user_id,
                    "provider": row["provider"],
                    "key_id": key_id,
                    "ciphertext": ciphertext,
                },
            )
    return len(rows)


def current_provider_secret(provider: object) -> str:
    return load_provider_secret(current_owner_user_id(), provider)


def current_provider_secret_configured(provider: object) -> bool:
    return provider_secret_configured(current_owner_user_id(), provider)
