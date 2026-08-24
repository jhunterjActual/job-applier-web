"""Authenticated identity mapping for standalone and shared-host runtimes."""

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Literal

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
    """The identity is not an active, operator-approved local user."""


@dataclass(frozen=True)
class AuthorizedLocalUser:
    id: int
    role: Literal["owner", "user"]


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


def check_identity_store_ready(expected_owner_workos_user_id: str | None = None) -> None:
    """Verify identity storage and, on shared hosts, the immutable active owner."""
    if identity_store_kind() == "sqlite":
        connection = get_db_connection()
        try:
            connection.execute("SELECT 1 FROM app_users LIMIT 0").fetchone()
        finally:
            connection.close()
        return
    if not expected_owner_workos_user_id:
        raise LocalUserAuthorizationError("The configured staging owner is required.")
    owner = _postgres_user(
        expected_owner_workos_user_id,
        expected_owner_workos_user_id,
        touch=False,
    )
    if owner is None or owner.role != "owner":
        raise LocalUserAuthorizationError("The configured staging owner is unavailable.")


def _postgres_user(
    workos_user_id: str,
    expected_owner_workos_user_id: str,
    *,
    touch: bool,
) -> AuthorizedLocalUser | None:
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
        if row is None or row["disabled_at"] is not None:
            return None
        role = str(row["role"])
        configured_owner = workos_user_id == expected_owner_workos_user_id
        if role not in {"owner", "user"} or configured_owner != (role == "owner"):
            return None
        if touch:
            connection.execute(
                text(
                    "UPDATE careertrellis.app_users "
                    "SET last_seen_at = :last_seen_at WHERE id = :user_id"
                ),
                {"last_seen_at": _utc_timestamp(), "user_id": row["id"]},
            )
        return AuthorizedLocalUser(id=int(row["id"]), role=role)


def authorized_local_user(
    workos_user_id: str,
    expected_owner_workos_user_id: str,
    *,
    touch: bool = False,
) -> AuthorizedLocalUser | None:
    """Resolve one preseeded active user without creating or changing authorization."""
    if identity_store_kind() == "postgres":
        return _postgres_user(
            workos_user_id,
            expected_owner_workos_user_id,
            touch=touch,
        )
    if workos_user_id != expected_owner_workos_user_id:
        return None
    connection = get_db_connection()
    try:
        row = connection.execute(
            """
            SELECT id FROM app_users
            WHERE workos_user_id = ? AND role = 'owner' AND disabled_at IS NULL
            """,
            (workos_user_id,),
        ).fetchone()
        if row is None:
            return None
        if touch:
            connection.execute(
                "UPDATE app_users SET last_seen_at = ? WHERE id = ?",
                (_utc_timestamp().isoformat(timespec="seconds"), row["id"]),
            )
            connection.commit()
        return AuthorizedLocalUser(id=int(row["id"]), role="owner")
    finally:
        connection.close()
