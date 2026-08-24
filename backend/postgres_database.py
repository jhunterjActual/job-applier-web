"""PostgreSQL connection and tenant-context primitives for shared hosting.

The current standalone application continues to use ``database.py`` and
SQLite. This module is the fail-closed boundary for the staged PostgreSQL
conversion: callers must open an explicit transaction and set trusted identity
context before protected rows become visible through PostgreSQL RLS.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from threading import Lock
from typing import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection, URL, make_url


DATABASE_URL_ENV = "CAREERTRELLIS_DATABASE_URL"
MIGRATION_DATABASE_URL_ENV = "CAREERTRELLIS_MIGRATION_DATABASE_URL"
MINIMUM_POSTGRES_MAJOR = 18


class PostgreSQLConfigurationError(RuntimeError):
    """A PostgreSQL connection setting is missing or unsafe."""


def postgres_url(value: str | URL) -> URL:
    """Return a psycopg SQLAlchemy URL without ever logging its credentials."""
    try:
        url = make_url(value)
    except Exception as exc:
        raise PostgreSQLConfigurationError("The PostgreSQL database URL is invalid.") from exc
    if url.get_backend_name() != "postgresql":
        raise PostgreSQLConfigurationError("Shared database URLs must use PostgreSQL.")
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise PostgreSQLConfigurationError("PostgreSQL connections must use the psycopg 3 driver.")
    if not url.database:
        raise PostgreSQLConfigurationError("The PostgreSQL database URL must name a database.")
    return url.set(drivername="postgresql+psycopg")


def database_url_from_environment(variable: str = DATABASE_URL_ENV) -> URL:
    """Load one required database URL from the process environment."""
    value = os.environ.get(variable, "").strip()
    if not value:
        raise PostgreSQLConfigurationError(f"{variable} is required.")
    return postgres_url(value)


def create_postgres_engine(value: str | URL | None = None) -> Engine:
    """Create a bounded synchronous pool that rolls back on every return."""
    url = postgres_url(value) if value is not None else database_url_from_environment()
    return create_engine(
        url,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_reset_on_return="rollback",
        pool_size=5,
        max_overflow=5,
        pool_timeout=10,
    )


def runtime_postgres_engine() -> Engine:
    """Return the shared-host runtime pool configured for the current process."""
    url = database_url_from_environment()
    with _ENGINE_REGISTRY_LOCK:
        engine = _ENGINE_REGISTRY.get(url)
        if engine is None:
            engine = create_postgres_engine(url)
            _ENGINE_REGISTRY[url] = engine
        return engine


def check_postgres_ready(engine: Engine | None = None) -> None:
    """Verify runtime connectivity without requiring or setting tenant context."""
    selected = engine or runtime_postgres_engine()
    with selected.connect() as connection:
        if connection.scalar(text("SELECT 1")) != 1:
            raise RuntimeError("The PostgreSQL runtime database is not ready.")


def dispose_postgres_engines() -> None:
    """Dispose cached pools during a clean process shutdown."""
    with _ENGINE_REGISTRY_LOCK:
        engines = tuple(_ENGINE_REGISTRY.values())
        _ENGINE_REGISTRY.clear()
    for engine in engines:
        engine.dispose()


_ENGINE_REGISTRY: dict[URL, Engine] = {}
_ENGINE_REGISTRY_LOCK = Lock()


def _positive_user_id(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("The authenticated user ID must be a positive integer.")
    if isinstance(value, int):
        user_id = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        user_id = int(value)
    else:
        raise ValueError("The authenticated user ID must be a positive integer.")
    if user_id <= 0:
        raise ValueError("The authenticated user ID must be a positive integer.")
    return user_id


def _workos_user_id(value: object) -> str:
    workos_user_id = str(value or "").strip()
    if len(workos_user_id) <= len("user_") or len(workos_user_id) > 255:
        raise ValueError("The authenticated WorkOS user ID is invalid.")
    if not workos_user_id.startswith("user_") or any(
        character.isspace() for character in workos_user_id
    ):
        raise ValueError("The authenticated WorkOS user ID is invalid.")
    return workos_user_id


def validated_workos_user_id(value: object) -> str:
    """Return one bounded immutable WorkOS identity string."""
    return _workos_user_id(value)


def set_identity_context(connection: Connection, workos_user_id: object) -> None:
    """Set transaction-local immutable identity context for user lookup."""
    connection.execute(
        text("SELECT set_config('careertrellis.workos_user_id', :identity, true)"),
        {"identity": _workos_user_id(workos_user_id)},
    )


def set_tenant_context(
    connection: Connection,
    user_id: object,
    workos_user_id: object | None = None,
) -> None:
    """Set transaction-local RLS context from an already authorized session."""
    connection.execute(
        text("SELECT set_config('careertrellis.user_id', :user_id, true)"),
        {"user_id": str(_positive_user_id(user_id))},
    )
    if workos_user_id is not None:
        set_identity_context(connection, workos_user_id)


@contextmanager
def identity_transaction(engine: Engine, workos_user_id: object) -> Iterator[Connection]:
    """Open an identity-scoped transaction for mapping a verified WorkOS user."""
    with engine.begin() as connection:
        set_identity_context(connection, workos_user_id)
        yield connection


@contextmanager
def tenant_transaction(
    engine: Engine,
    user_id: object,
    workos_user_id: object | None = None,
) -> Iterator[Connection]:
    """Open a transaction whose protected queries are constrained by RLS."""
    with engine.begin() as connection:
        set_tenant_context(connection, user_id, workos_user_id)
        yield connection
