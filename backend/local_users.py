from datetime import datetime, timezone

from database import get_db_connection


class LocalUserAuthorizationError(PermissionError):
    """The identity is not the active local owner of this staging workspace."""


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def provision_staging_owner(workos_user_id: str) -> int:
    """Create the single staging owner mapping without storing profile attributes."""
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
                (_utc_timestamp(), existing_owner["id"]),
            )
            connection.commit()
            return int(existing_owner["id"])

        timestamp = _utc_timestamp()
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


def authorized_local_owner(workos_user_id: str) -> int | None:
    """Return the active owner ID for a WorkOS identity, never creating it implicitly."""
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
