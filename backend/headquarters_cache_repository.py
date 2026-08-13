"""Dual-mode, owner-scoped persistence for OpenStreetMap headquarters results."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import sqlite3
from typing import Any, Protocol

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from data_store import LOCAL_DATA_STORE, data_store_kind
from database import get_db_connection
from maps_providers import HeadquartersResult, normalize_maps_provider
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


OPENSTREETMAP_PROVIDER = "openstreetmap"
MAX_HEADQUARTERS_CACHE_KEY_CHARS = 128
MAX_HEADQUARTERS_ADDRESS_CHARS = 250
MAX_HEADQUARTERS_COUNTRY_CODE_CHARS = 3
MAX_HEADQUARTERS_ATTRIBUTION_CHARS = 250


class HeadquartersCacheUnavailable(RuntimeError):
    """Fixed, privacy-safe persistence failure for the optional lookup cache."""

    def __init__(self):
        super().__init__("The headquarters cache is temporarily unavailable.")


class HeadquartersCacheRepository(Protocol):
    def get(self, cache_key: str) -> HeadquartersResult | None: ...

    def cache_openstreetmap_result(
        self,
        cache_key: str,
        result: HeadquartersResult,
        selected_provider: str,
    ) -> bool: ...


def _bounded_value(
    value: Any,
    *,
    field: str,
    maximum: int,
    required: bool = False,
) -> str:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise ValueError(f"Headquarters cache {field} is required.")
    if len(normalized) > maximum:
        raise ValueError(
            f"Headquarters cache {field} must be {maximum} characters or fewer."
        )
    return normalized


def _cache_key(value: str) -> str:
    return _bounded_value(
        value,
        field="key",
        maximum=MAX_HEADQUARTERS_CACHE_KEY_CHARS,
        required=True,
    )


def _cache_values(
    result: HeadquartersResult,
    selected_provider: str,
) -> tuple[str, str, str, str] | None:
    provider = normalize_maps_provider(selected_provider)
    source = str(result.source or "").strip().lower()
    address = str(result.address or "").strip()
    if (
        provider != OPENSTREETMAP_PROVIDER
        or source != OPENSTREETMAP_PROVIDER
        or not address
        or address == "Unknown"
    ):
        return None
    return (
        provider,
        _bounded_value(
            address,
            field="address",
            maximum=MAX_HEADQUARTERS_ADDRESS_CHARS,
            required=True,
        ),
        _bounded_value(
            result.country_code,
            field="country code",
            maximum=MAX_HEADQUARTERS_COUNTRY_CODE_CHARS,
        ).upper(),
        _bounded_value(
            result.attribution,
            field="attribution",
            maximum=MAX_HEADQUARTERS_ATTRIBUTION_CHARS,
        ),
    )


def _cached_result(row: Mapping[str, Any] | None) -> HeadquartersResult | None:
    if row is None or str(row.get("provider") or "").strip().lower() != OPENSTREETMAP_PROVIDER:
        return None
    try:
        address = _bounded_value(
            row.get("address"),
            field="address",
            maximum=MAX_HEADQUARTERS_ADDRESS_CHARS,
            required=True,
        )
        country_code = _bounded_value(
            row.get("country_code"),
            field="country code",
            maximum=MAX_HEADQUARTERS_COUNTRY_CODE_CHARS,
        ).upper()
        attribution = _bounded_value(
            row.get("attribution"),
            field="attribution",
            maximum=MAX_HEADQUARTERS_ATTRIBUTION_CHARS,
        )
    except ValueError:
        return None
    return HeadquartersResult(
        address=address,
        source=OPENSTREETMAP_PROVIDER,
        attribution=attribution,
        country_code=country_code,
    )


class SQLiteHeadquartersCacheRepository:
    """Preserve the standalone SQLite cache without retaining lookup inputs."""

    def __init__(self, connection_factory: Callable[[], Any] = get_db_connection):
        self._connection_factory = connection_factory

    def get(self, cache_key: str) -> HeadquartersResult | None:
        key = _cache_key(cache_key)
        connection = self._connection_factory()
        try:
            row = connection.execute(
                """
                SELECT provider, address, country_code, attribution
                FROM headquarters_cache
                WHERE cache_key = ? AND provider = ?
                """,
                (key, OPENSTREETMAP_PROVIDER),
            ).fetchone()
            return _cached_result(dict(row) if row else None)
        except sqlite3.OperationalError:
            return None
        except sqlite3.DatabaseError as exc:
            raise HeadquartersCacheUnavailable() from exc
        finally:
            connection.close()

    def cache_openstreetmap_result(
        self,
        cache_key: str,
        result: HeadquartersResult,
        selected_provider: str,
    ) -> bool:
        values = _cache_values(result, selected_provider)
        if values is None:
            return False
        key = _cache_key(cache_key)
        provider, address, country_code, attribution = values
        connection = self._connection_factory()
        try:
            connection.execute(
                """
                INSERT INTO headquarters_cache (
                    cache_key, provider, address, country_code, attribution, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    provider = excluded.provider,
                    address = excluded.address,
                    country_code = excluded.country_code,
                    attribution = excluded.attribution,
                    resolved_at = excluded.resolved_at
                """,
                (
                    key,
                    provider,
                    address,
                    country_code,
                    attribution,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            connection.commit()
            return True
        except sqlite3.OperationalError:
            connection.rollback()
            return False
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise HeadquartersCacheUnavailable() from exc
        finally:
            connection.close()


class PostgreSQLHeadquartersCacheRepository:
    """Owner-scoped PostgreSQL cache access under forced RLS."""

    def __init__(self, owner_user_id: int, engine: Engine | None = None):
        self._owner_user_id = owner_user_id
        self._engine = engine or runtime_postgres_engine()

    def get(self, cache_key: str) -> HeadquartersResult | None:
        key = _cache_key(cache_key)
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = connection.execute(
                    text(
                        """
                        SELECT provider, address, country_code, attribution
                        FROM careertrellis.headquarters_cache
                        WHERE owner_user_id = :owner_user_id
                          AND cache_key = :cache_key
                          AND provider = :provider
                        """
                    ),
                    {
                        "owner_user_id": self._owner_user_id,
                        "cache_key": key,
                        "provider": OPENSTREETMAP_PROVIDER,
                    },
                ).mappings().first()
            return _cached_result(row)
        except SQLAlchemyError as exc:
            raise HeadquartersCacheUnavailable() from exc

    def cache_openstreetmap_result(
        self,
        cache_key: str,
        result: HeadquartersResult,
        selected_provider: str,
    ) -> bool:
        values = _cache_values(result, selected_provider)
        if values is None:
            return False
        key = _cache_key(cache_key)
        provider, address, country_code, attribution = values
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO careertrellis.headquarters_cache (
                            owner_user_id, cache_key, provider, address,
                            country_code, attribution, resolved_at
                        ) VALUES (
                            :owner_user_id, :cache_key, :provider, :address,
                            :country_code, :attribution, CURRENT_TIMESTAMP
                        )
                        ON CONFLICT (owner_user_id, cache_key) DO UPDATE SET
                            provider = EXCLUDED.provider,
                            address = EXCLUDED.address,
                            country_code = EXCLUDED.country_code,
                            attribution = EXCLUDED.attribution,
                            resolved_at = CURRENT_TIMESTAMP
                        WHERE careertrellis.headquarters_cache.owner_user_id = :owner_user_id
                        """
                    ),
                    {
                        "owner_user_id": self._owner_user_id,
                        "cache_key": key,
                        "provider": provider,
                        "address": address,
                        "country_code": country_code,
                        "attribution": attribution,
                    },
                )
            return True
        except SQLAlchemyError as exc:
            raise HeadquartersCacheUnavailable() from exc


def runtime_headquarters_cache_repository(
    *,
    sqlite_connection_factory: Callable[[], Any] = get_db_connection,
    engine: Engine | None = None,
) -> HeadquartersCacheRepository:
    """Return the configured cache without payload-selected ownership or fallback."""
    if data_store_kind() == LOCAL_DATA_STORE:
        return SQLiteHeadquartersCacheRepository(sqlite_connection_factory)
    return PostgreSQLHeadquartersCacheRepository(
        current_owner_user_id(), engine=engine
    )
