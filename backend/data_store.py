"""Runtime data-store selection for standalone and shared-host operation.

Standalone installs retain the existing SQLite repository. Shared-host
deployments must select PostgreSQL explicitly, but remain blocked until every
production repository has been converted to an owner-scoped PostgreSQL
transaction.
"""

from __future__ import annotations

import os

import config
from postgres_database import PostgreSQLConfigurationError, database_url_from_environment


DATA_STORE_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_DATA_STORE"
LOCAL_DATA_STORE = "sqlite"
SHARED_DATA_STORE = "postgres"

# Flip only in the final repository-cutover change after every hosted data path
# is owner-scoped and the complete cross-tenant test suite passes.
HOSTED_REPOSITORY_CUTOVER_COMPLETE = False


def data_store_kind() -> str:
    """Return the explicitly safe data store for the current runtime mode."""
    environment = config.get_runtime_environment()
    configured = os.environ.get(DATA_STORE_ENVIRONMENT_VARIABLE, "").strip().lower()
    if environment == "local":
        if configured not in {"", LOCAL_DATA_STORE}:
            raise config.RuntimeConfigurationError(
                f"Local mode requires {DATA_STORE_ENVIRONMENT_VARIABLE} to be unset or "
                f"{LOCAL_DATA_STORE}."
            )
        return LOCAL_DATA_STORE

    if configured != SHARED_DATA_STORE:
        raise config.RuntimeConfigurationError(
            f"Shared-host mode requires {DATA_STORE_ENVIRONMENT_VARIABLE}="
            f"{SHARED_DATA_STORE}."
        )
    try:
        database_url_from_environment()
    except PostgreSQLConfigurationError as exc:
        raise config.RuntimeConfigurationError(
            "Shared-host data storage requires a valid CAREERTRELLIS_DATABASE_URL."
        ) from exc
    return SHARED_DATA_STORE


def validate_data_store_configuration(*, fail_on_incomplete: bool = True) -> str:
    """Validate selection and block shared hosting before repository cutover."""
    selected = data_store_kind()
    if (
        fail_on_incomplete
        and selected == SHARED_DATA_STORE
        and not HOSTED_REPOSITORY_CUTOVER_COMPLETE
    ):
        raise config.RuntimeConfigurationError(
            "Shared-host PostgreSQL repositories are not fully converted."
        )
    return selected
