"""Owner-authorized PostgreSQL metadata and encrypted-object reads."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy import Engine, text

from object_store import (
    ObjectBackend,
    ObjectKeyring,
    StoredObjectMetadata,
    read_object,
    runtime_object_backend,
)
from postgres_database import runtime_postgres_engine, tenant_transaction


class StoredObjectNotFound(LookupError):
    """No active object is visible to the authenticated owner."""


@dataclass(frozen=True)
class StoredObjectDownload:
    content: bytes
    content_type: str
    filename: str


def _positive_owner_user_id(value: object) -> int:
    if isinstance(value, bool):
        raise StoredObjectNotFound("The generated object is unavailable.")
    try:
        owner_user_id = int(value)
    except (TypeError, ValueError) as exc:
        raise StoredObjectNotFound("The generated object is unavailable.") from exc
    if owner_user_id <= 0 or str(owner_user_id) != str(value):
        raise StoredObjectNotFound("The generated object is unavailable.")
    return owner_user_id


def _object_id(value: object) -> uuid.UUID:
    try:
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise StoredObjectNotFound("The generated object is unavailable.") from exc
    if parsed.version != 4:
        raise StoredObjectNotFound("The generated object is unavailable.")
    return parsed


def _metadata_from_row(row: object) -> StoredObjectMetadata:
    try:
        digest = bytes(row["sha256"])
        metadata = StoredObjectMetadata(
            id=_object_id(row["id"]),
            owner_user_id=_positive_owner_user_id(row["owner_user_id"]),
            object_key=str(row["object_key"]),
            content_type=str(row["content_type"]),
            size_bytes=int(row["plaintext_size"]),
            sha256=digest.hex(),
            wrapped_data_key=bytes(row["wrapped_data_key"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StoredObjectNotFound("The generated object is unavailable.") from exc
    if len(digest) != 32:
        raise StoredObjectNotFound("The generated object is unavailable.")
    return metadata


def load_stored_object_metadata(
    owner_user_id: object,
    object_id: object,
    *,
    engine: Engine | None = None,
) -> StoredObjectMetadata:
    """Load one active row through the authenticated owner's forced-RLS scope."""
    owner_id = _positive_owner_user_id(owner_user_id)
    selected_object_id = _object_id(object_id)
    selected_engine = engine or runtime_postgres_engine()
    with tenant_transaction(selected_engine, owner_id) as connection:
        row = connection.execute(
            text(
                """
                SELECT id, owner_user_id, object_key, content_type,
                       plaintext_size, sha256, wrapped_data_key
                FROM careertrellis.stored_objects
                WHERE id = :object_id
                  AND owner_user_id = :owner_user_id
                  AND deleted_at IS NULL
                """
            ),
            {"object_id": selected_object_id, "owner_user_id": owner_id},
        ).mappings().one_or_none()
    if row is None:
        raise StoredObjectNotFound("The generated object is unavailable.")
    return _metadata_from_row(row)


def download_stored_object(
    owner_user_id: object,
    object_id: object,
    *,
    engine: Engine | None = None,
    backend: ObjectBackend | None = None,
    keyring: ObjectKeyring | None = None,
) -> StoredObjectDownload:
    """Authorize metadata before fetching and decrypting private object bytes."""
    owner_id = _positive_owner_user_id(owner_user_id)
    metadata = load_stored_object_metadata(owner_id, object_id, engine=engine)
    content = read_object(
        backend or runtime_object_backend(),
        metadata,
        owner_id,
        keyring=keyring,
    )
    filename = (
        "generated-resume.pdf"
        if metadata.content_type == "application/pdf"
        else "generated-cover-letter.txt"
    )
    return StoredObjectDownload(
        content=content,
        content_type=metadata.content_type,
        filename=filename,
    )
