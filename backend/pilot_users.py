"""Root-operated, privacy-safe pilot identity provisioning.

The installed wrapper supplies only the operation name.  The target immutable
WorkOS identity is read from an interactive, non-echoed terminal prompt and is
never accepted through argv or the process environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import sys

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import NullPool

from postgres_database import postgres_url, set_identity_context, set_tenant_context, validated_workos_user_id


MIGRATION_ENVIRONMENT_FILE = Path("/etc/careertrellis/postgres-migration.env")
STAGING_ENVIRONMENT_FILE = Path("/etc/careertrellis/staging.env")
MIGRATION_DATABASE_URL_KEY = "CAREERTRELLIS_MIGRATION_DATABASE_URL"
OWNER_ID_KEY = "CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID"
PILOT_USER_LOCK_NAME = "careertrellis-pilot-user-v1"
WORKOS_USER_ID_PATTERN = re.compile(r"^user_[A-Za-z0-9_]{6,120}$")
OPERATIONS = frozenset({"seed", "inspect", "disable", "reinvite"})
FIXED_ERROR = "The pilot user operation did not complete. Inspect before retrying."


class PilotUserOperationError(RuntimeError):
    """One privacy-safe operator-boundary failure."""


@dataclass(frozen=True)
class PilotUserResult:
    result: str
    present: bool
    role: str | None
    active: bool
    changed: bool
    exit_code: int = 0


def _protected_setting(path: Path, key: str) -> str:
    """Read one exact unquoted assignment without evaluating shell syntax."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PilotUserOperationError(FIXED_ERROR) from exc
    prefix = f"{key}="
    values = [line[len(prefix) :] for line in lines if line.startswith(prefix)]
    if len(values) != 1 or not values[0] or values[0] != values[0].strip():
        raise PilotUserOperationError(FIXED_ERROR)
    value = values[0]
    if any(character.isspace() for character in value):
        raise PilotUserOperationError(FIXED_ERROR)
    return value


def _operator_engine() -> Engine:
    migration_url = _protected_setting(
        MIGRATION_ENVIRONMENT_FILE, MIGRATION_DATABASE_URL_KEY
    )
    url = postgres_url(migration_url)
    if url.username != "careertrellis_migration" or url.database != "careertrellis":
        raise PilotUserOperationError(FIXED_ERROR)
    return create_engine(
        url,
        poolclass=NullPool,
        pool_pre_ping=True,
        echo=False,
        hide_parameters=True,
    )


def _mapping(connection, workos_user_id: str, *, for_update: bool = False):
    set_identity_context(connection, workos_user_id)
    lock_clause = " FOR UPDATE" if for_update else ""
    return connection.execute(
        text(
            "SELECT id, role, disabled_at "
            "FROM careertrellis.app_users "
            "WHERE workos_user_id = :workos_user_id"
            + lock_clause
        ),
        {"workos_user_id": workos_user_id},
    ).mappings().one_or_none()


def _result_for_row(
    result: str, row, *, changed: bool, exit_code: int = 0
) -> PilotUserResult:
    if row is None:
        return PilotUserResult(
            result=result,
            present=False,
            role=None,
            active=False,
            changed=changed,
            exit_code=exit_code,
        )
    role = str(row["role"])
    if role not in {"owner", "user"}:
        raise PilotUserOperationError(FIXED_ERROR)
    return PilotUserResult(
        result=result,
        present=True,
        role=role,
        active=row["disabled_at"] is None,
        changed=changed,
        exit_code=exit_code,
    )


def _require_user_state(row, *, active: bool) -> None:
    if (
        row is None
        or row["role"] != "user"
        or (row["disabled_at"] is None) is not active
    ):
        raise PilotUserOperationError(FIXED_ERROR)


def _ensure_profile(connection, row) -> bool:
    """Provision/repair only the authorized active target, never its content."""
    _require_user_state(row, active=True)
    set_tenant_context(connection, row["id"])
    inserted = connection.execute(text(
        "INSERT INTO careertrellis.profiles (owner_user_id) VALUES (:owner_user_id) "
        "ON CONFLICT (owner_user_id) DO NOTHING"
    ), {"owner_user_id": int(row["id"])})
    return inserted.rowcount == 1


def perform_operation(
    engine: Engine,
    operation: str,
    owner_workos_user_id: str,
    target_workos_user_id: str,
) -> PilotUserResult:
    """Perform one bounded operation without returning an external identity."""
    if operation not in OPERATIONS:
        raise PilotUserOperationError(FIXED_ERROR)
    owner_identity = validated_workos_user_id(owner_workos_user_id)
    target_identity = validated_workos_user_id(target_workos_user_id)
    if (
        not WORKOS_USER_ID_PATTERN.fullmatch(owner_identity)
        or not WORKOS_USER_ID_PATTERN.fullmatch(target_identity)
    ):
        raise PilotUserOperationError(FIXED_ERROR)

    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL log_parameter_max_length = 0")
        connection.exec_driver_sql("SET LOCAL log_parameter_max_length_on_error = 0")
        connection.exec_driver_sql("SET ROLE careertrellis_schema_owner")
        if operation != "inspect" and connection.scalar(
            text("SELECT careertrellis_admission.operator_allowed(:operation, :subject)"),
            {"operation": operation, "subject": target_identity},
        ) is not True:
            raise PilotUserOperationError(FIXED_ERROR)
        if connection.scalar(
            text(
                "SELECT pg_catalog.pg_try_advisory_xact_lock("
                "pg_catalog.hashtextextended(:name, 0))"
            ),
            {"name": PILOT_USER_LOCK_NAME},
        ) is not True:
            raise PilotUserOperationError(FIXED_ERROR)

        owner_row = _mapping(connection, owner_identity)
        if (
            owner_row is None
            or owner_row["role"] != "owner"
            or owner_row["disabled_at"] is not None
        ):
            raise PilotUserOperationError(FIXED_ERROR)

        if target_identity == owner_identity:
            if operation == "inspect":
                connection.exec_driver_sql("RESET ROLE")
                return _result_for_row("ok", owner_row, changed=False)
            connection.exec_driver_sql("RESET ROLE")
            return _result_for_row(
                "owner_protected", owner_row, changed=False, exit_code=3
            )

        row = _mapping(
            connection, target_identity, for_update=operation != "inspect"
        )
        if row is not None and row["role"] == "owner":
            connection.exec_driver_sql("RESET ROLE")
            if operation == "inspect":
                return _result_for_row("unexpected_owner", row, changed=False, exit_code=3)
            return _result_for_row("owner_protected", row, changed=False, exit_code=3)

        if operation == "inspect":
            connection.exec_driver_sql("RESET ROLE")
            return _result_for_row("ok" if row is not None else "absent", row, changed=False)

        if operation == "seed":
            if row is None:
                inserted = connection.execute(
                    text(
                        "INSERT INTO careertrellis.app_users (workos_user_id, role) "
                        "VALUES (:workos_user_id, 'user')"
                    ),
                    {"workos_user_id": target_identity},
                )
                if inserted.rowcount != 1:
                    raise PilotUserOperationError(FIXED_ERROR)
                row = _mapping(connection, target_identity)
                _require_user_state(row, active=True)
                _ensure_profile(connection, row)
                connection.exec_driver_sql("RESET ROLE")
                return _result_for_row("created", row, changed=True)
            if row["disabled_at"] is not None:
                connection.exec_driver_sql("RESET ROLE")
                return _result_for_row(
                    "disabled_requires_reinvite", row, changed=False, exit_code=4
                )
            repaired = _ensure_profile(connection, row)
            connection.exec_driver_sql("RESET ROLE")
            return _result_for_row("already_active", row, changed=repaired)

        if operation == "disable":
            if row is None:
                connection.exec_driver_sql("RESET ROLE")
                return _result_for_row("absent", None, changed=False)
            if row["disabled_at"] is not None:
                connection.exec_driver_sql("RESET ROLE")
                return _result_for_row("already_disabled", row, changed=False)
            updated = connection.execute(
                text(
                    "UPDATE careertrellis.app_users "
                    "SET disabled_at = CURRENT_TIMESTAMP "
                    "WHERE workos_user_id = :workos_user_id "
                    "AND role = 'user' AND disabled_at IS NULL"
                ),
                {"workos_user_id": target_identity},
            )
            if updated.rowcount != 1:
                raise PilotUserOperationError(FIXED_ERROR)
            connection.exec_driver_sql("SELECT careertrellis_admission.invalidate_flows()")
            row = _mapping(connection, target_identity)
            _require_user_state(row, active=False)
            connection.exec_driver_sql("RESET ROLE")
            return _result_for_row("disabled", row, changed=True)

        if row is None:
            connection.exec_driver_sql("RESET ROLE")
            return _result_for_row(
                "absent_requires_seed", None, changed=False, exit_code=4
            )
        if row["disabled_at"] is None:
            repaired = _ensure_profile(connection, row)
            connection.exec_driver_sql("RESET ROLE")
            return _result_for_row("already_active", row, changed=repaired)
        if connection.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM careertrellis.account_erasure_requests "
                "WHERE owner_user_id = :owner_user_id)"
            ),
            {"owner_user_id": int(row["id"])},
        ) is True:
            connection.exec_driver_sql("RESET ROLE")
            return _result_for_row(
                "erasure_pending", row, changed=False, exit_code=5
            )
        updated = connection.execute(
            text(
                "UPDATE careertrellis.app_users "
                "SET disabled_at = NULL "
                "WHERE workos_user_id = :workos_user_id "
                "AND role = 'user' AND disabled_at IS NOT NULL"
            ),
            {"workos_user_id": target_identity},
        )
        if updated.rowcount != 1:
            raise PilotUserOperationError(FIXED_ERROR)
        connection.exec_driver_sql("SELECT careertrellis_admission.invalidate_flows()")
        row = _mapping(connection, target_identity)
        _require_user_state(row, active=True)
        _ensure_profile(connection, row)
        connection.exec_driver_sql("RESET ROLE")
        return _result_for_row("reactivated", row, changed=True)


def _interactive_identity() -> str:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise PilotUserOperationError(FIXED_ERROR)
    terminal_attributes = None
    try:
        import termios

        terminal_fd = sys.stdin.fileno()
        terminal_attributes = termios.tcgetattr(terminal_fd)
        hidden_attributes = terminal_attributes.copy()
        hidden_attributes[3] &= ~termios.ECHO
        termios.tcsetattr(terminal_fd, termios.TCSAFLUSH, hidden_attributes)
        print("Immutable WorkOS user ID: ", end="", file=sys.stderr, flush=True)
        value = sys.stdin.readline(257)
    except (EOFError, KeyboardInterrupt, OSError, ValueError) as exc:
        raise PilotUserOperationError(FIXED_ERROR) from exc
    finally:
        if terminal_attributes is not None:
            termios.tcsetattr(terminal_fd, termios.TCSAFLUSH, terminal_attributes)
            print(file=sys.stderr, flush=True)
    if not value.endswith("\n") or len(value) > 256:
        raise PilotUserOperationError(FIXED_ERROR)
    return validated_workos_user_id(value[:-1])


def _print_result(operation: str, result: PilotUserResult) -> None:
    print(f"operation={operation}")
    print(f"result={result.result}")
    print(f"present={'yes' if result.present else 'no'}")
    print(f"role={result.role or 'none'}")
    print(f"active={'yes' if result.active else 'no'}")
    print(f"changed={'yes' if result.changed else 'no'}")
    print("identity_disclosed=no")


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if os.geteuid() != 0 or len(arguments) != 1 or arguments[0] not in OPERATIONS:
        print(FIXED_ERROR, file=sys.stderr)
        return 1
    operation = arguments[0]
    engine = None
    try:
        target_identity = _interactive_identity()
        owner_identity = validated_workos_user_id(
            _protected_setting(STAGING_ENVIRONMENT_FILE, OWNER_ID_KEY)
        )
        engine = _operator_engine()
        result = perform_operation(engine, operation, owner_identity, target_identity)
    except (Exception, KeyboardInterrupt):
        print(FIXED_ERROR, file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass
    _print_result(operation, result)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
