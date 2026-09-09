"""Alembic environment for the shared-host PostgreSQL schema."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from postgres_database import (
    MIGRATION_DATABASE_URL_ENV,
    MINIMUM_POSTGRES_MAJOR,
    database_url_from_environment,
    postgres_url,
)


ALEMBIC_SCHEMA = "careertrellis"
MIGRATION_LOCK_KEY = "careertrellis-alembic-migrations-v1"
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def migration_url():
    if 'migration_url' in config.attributes:
        return postgres_url(config.attributes['migration_url'])
    return database_url_from_environment(MIGRATION_DATABASE_URL_ENV)


def run_migrations_offline() -> None:
    context.configure(
        url=migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=ALEMBIC_SCHEMA,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(migration_url(), poolclass=NullPool, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            version_number = int(connection.scalar(text("SHOW server_version_num")))
            if version_number < MINIMUM_POSTGRES_MAJOR * 10000:
                raise RuntimeError(
                    f"PostgreSQL {MINIMUM_POSTGRES_MAJOR} or newer is required."
                )
            connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"),
                {"key": MIGRATION_LOCK_KEY},
            )
            try:
                connection.execute(text("SET ROLE careertrellis_schema_owner"))
                connection.commit()
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    include_schemas=True,
                    version_table_schema=ALEMBIC_SCHEMA,
                    transaction_per_migration=True,
                    compare_type=True,
                    compare_server_default=True,
                )
                with context.begin_transaction():
                    context.run_migrations()
            finally:
                connection.execute(text("RESET ROLE"))
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": MIGRATION_LOCK_KEY},
                )
                connection.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
