"""Authenticated identity mapping for standalone and shared-host runtimes."""

from datetime import datetime, timezone
import os

from sqlalchemy import text

import config
from database import get_db_connection
from postgres_database import (
    PostgreSQLConfigurationError,
    database_url_from_environment,
    identity_transaction,
    runtime_postgres_engine,
)


IDENTITY_STORE_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_IDENTITY_STORE"


class LocalUserAuthorizationError(PermissionError):
    """The identity is not the active owner of this deployment."""


def _utc_timestamp() -> datetime:
    return datetime.now(timezone.utc)


def identity_store_kind() -> str:
    """Return the explicit identity store, retaining SQLite only for local use."""
    environment = config.get_runtime_environment()
    configured = os.environ.get(IDENTITY_STORE_ENVIRONMENT_VARIABLE, "").strip().lower()
    if environment == "local":
        if configured not in {"", "sqlite"}:
            raise config.RuntimeConfigurationError(
                "Local mode requires CAREERTRELLIS_IDENTITY_STORE to be unset or sqlite."
            )
        return "sqlite"
    if configured != "postgres":
        raise config.RuntimeConfigurationError(
            "Shared-host mode requires CAREERTRELLIS_IDENTITY_STORE=postgres."
        )
    try:
        database_url_from_environment()
    except PostgreSQLConfigurationError as exc:
        raise config.RuntimeConfigurationError(
            "Shared-host identity storage requires a valid CAREERTRELLIS_DATABASE_URL."
        ) from exc
    return "postgres"


def validate_identity_store_configuration() -> str:
    """Validate the selected identity boundary without opening a connection."""
    return identity_store_kind()


def check_identity_store_ready() -> None:
    """Verify that the configured identity table is reachable by the runtime role."""
    if identity_store_kind() == "sqlite":
        connection = get_db_connection()
        try:
            connection.execute("SELECT 1 FROM app_users LIMIT 0").fetchone()
        finally:
            connection.close()
        return
    with runtime_postgres_engine().begin() as connection:
        connection.execute(text("SELECT 1 FROM careertrellis.app_users LIMIT 0"))


def _sqlite_provision_owner(workos_user_id: str) -> int:
    connection = get_db_connection()
    try:
        existing_owner = connection.execute(
            "SELECT id, workos_user_id, disabled_at FROM app_users WHERE role = 'owner' LIMIT 1"
        ).fetchone()
        if existing_owner is not None:
            if existing_owner["workos_user_id"] != workos_user_id or existing_owner["disabled_at"]:
                raise LocalUserAuthorizationError("The staging owner mapping does not match.")
            connection.execute(
                "UPDATE app_users SET last_seen_at = ? WHERE id = ?",
                (_utc_timestamp().isoformat(timespec="seconds"), existing_owner["id"]),
            )
            connection.commit()
            return int(existing_owner["id"])

        timestamp = _utc_timestamp().isoformat(timespec="seconds")
        cursor = connection.execute(
            """
            INSERT INTO app_users (workos_user_id, role, created_at, last_seen_at)
            VALUES (?, 'owner', ?, ?)
            """,
            (workos_user_id, timestamp, timestamp),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


def _postgres_owner(workos_user_id: str, *, touch: bool) -> int | None:
    with identity_transaction(runtime_postgres_engine(), workos_user_id) as connection:
        row = connection.execute(
            text(
                """
                SELECT id, role, disabled_at
                FROM careertrellis.app_users
                WHERE workos_user_id = :workos_user_id
                """
            ),
            {"workos_user_id": workos_user_id},
        ).mappings().one_or_none()
        if row is None or row["role"] != "owner" or row["disabled_at"] is not None:
            return None
        if touch:
            connection.execute(
                text(
                    "UPDATE careertrellis.app_users "
                    "SET last_seen_at = :last_seen_at WHERE id = :user_id"
                ),
                {"last_seen_at": _utc_timestamp(), "user_id": row["id"]},
            )
        return int(row["id"])


def provision_staging_owner(workos_user_id: str) -> int:
    """Resolve the approved owner; shared-host login never creates or elevates it."""
    if identity_store_kind() == "sqlite":
        return _sqlite_provision_owner(workos_user_id)
    user_id = _postgres_owner(workos_user_id, touch=True)
    if user_id is None:
        raise LocalUserAuthorizationError(
            "The PostgreSQL staging owner must be seeded before login."
        )
    return user_id


def authorized_local_owner(workos_user_id: str) -> int | None:
    """Return the active owner ID without ever creating an account implicitly."""
    if identity_store_kind() == "postgres":
        return _postgres_owner(workos_user_id, touch=False)
    connection = get_db_connection()
    try:
        row = connection.execute(
            """
            SELECT id FROM app_users
            WHERE workos_user_id = ? AND role = 'owner' AND disabled_at IS NULL
            """,
            (workos_user_id,),
        ).fetchone()
        return int(row["id"]) if row is not None else None
    finally:
        connection.close()
