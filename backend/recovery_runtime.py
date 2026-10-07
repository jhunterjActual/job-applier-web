"""Explicit protected recovery construction; no startup or provider requests.

Uses the existing installed credentials, never arguments or environment fallback.
The caller owns attended authentication and recovery-code entry. Construction
checks current configuration and schema; database wrappers recheck each operation.
"""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import admission_operations as ops
import identity_operator
from postgres_database import postgres_url
from recovery_credentials_hold_postgres import PostgreSQLRecoveryHoldStore
from recovery_release_postgres import PostgreSQLRecoveryReleaseStore
from recovery_provider import WorkOSRecoverySessions
from recovery_workflow import RecoveryWorkflow
from recovery_factor_proof import RecoveryProofUncertain
from workos_removal import WorkOSRemoval
from workos_transport_candidate import ProviderSettings, WorkOSTransportCandidate


FILES = ('staging.env', 'auth-v2.env', 'admission-web.json',
         'postgres-migration.env', 'postgres-runtime.env',
         'object-storage.env', 'provider-secrets.env')
PREFIX = 'CAREERTRELLIS_AUTH_V2_'


def require(value):
    if not value:
        raise RecoveryProofUncertain()


def configuration():
    require(os.name == 'posix' and os.geteuid() == 0)
    raw = {name: ops.read_protected(ops.ROOT / name) for name in FILES}
    staging = ops.assignments(raw['staging.env'])
    auth = ops.assignments(raw['auth-v2.env'])
    metadata = ops.strict_json(raw['admission-web.json'])
    require(staging.get('CAREERTRELLIS_AUTH_MODE') == 'workos'
            and staging.get('CAREERTRELLIS_IDENTITY_STORE') == 'postgres'
            and auth.get(PREFIX + 'ENABLED') == '1'
            and auth.get(PREFIX + 'SESSION_SECONDS') == '3600')
    require(not any(k.startswith(PREFIX) or k == 'CAREERTRELLIS_ADMISSION_DATABASE_URL'
                    for k in staging))
    for name in ('object-storage.env', 'provider-secrets.env'):
        require(not any(k.startswith(('WORKOS_', PREFIX)) or k in (
            'CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID', 'CAREERTRELLIS_AUTH_MODE',
            'CAREERTRELLIS_IDENTITY_STORE', 'CAREERTRELLIS_ADMISSION_DATABASE_URL')
            for k in ops.assignments(raw[name])))
    expected = dict(issuer=auth[PREFIX + 'ISSUER'], generation=auth[PREFIX + 'GENERATION'],
                    key_id=auth[PREFIX + 'KEY_ID'], session_seconds=3600)
    if PREFIX + 'AUDIENCE' in auth:
        expected['audience'] = auth[PREFIX + 'AUDIENCE']
    require(metadata == expected)
    settings = ProviderSettings(staging['WORKOS_CLIENT_ID'], staging['WORKOS_API_KEY'],
                                expected['issuer'], expected['generation'], expected.get('audience'))
    url = ops.migration_connection_url(identity_operator.DATABASE)
    broker = postgres_url(auth['CAREERTRELLIS_ADMISSION_DATABASE_URL'])
    require(broker.username == ops.BROKER and bool(broker.password)
            and set(broker.query) <= {'sslmode', 'sslrootcert'}
            and (broker.host, broker.port or 5432, broker.database, dict(broker.query))
            == (url.host, url.port or 5432, url.database, dict(url.query)))
    require(all(ops.read_protected(ops.ROOT / name) == value for name, value in raw.items()))
    return settings, url, raw


async def preflight(engine, binding):
    require(not engine.echo and not engine.pool.echo and engine.sync_engine.hide_parameters
            and not engine.sync_engine.logger.isEnabledFor(logging.DEBUG))
    async with asyncio.timeout(8):
        async with engine.begin() as connection:
            await connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            await connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
            await connection.exec_driver_sql('SET LOCAL log_parameter_max_length=0')
            await connection.exec_driver_sql('SET LOCAL log_parameter_max_length_on_error=0')
            require(await connection.scalar(text(
                "SELECT session_user='careertrellis_migration' AND current_database()=:db "
                "AND current_setting('server_version_num')::int BETWEEN 180000 AND 189999 "
                "AND current_setting('transaction_isolation')='read committed'"),
                {'db': identity_operator.DATABASE}) is True)
            await connection.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
            require(await connection.scalar(text(
                'SELECT version_num FROM careertrellis.alembic_version')) == '20261005_0023')
            await connection.exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
            require(await connection.scalar(text(
                'SELECT binding FROM careertrellis_admission.identity_provider')) == binding)
            require(await connection.scalar(text(
                'SELECT minimum_session_version=2 AND adapter_ready '
                'FROM careertrellis_admission.policy')) is True)


@dataclass(repr=False)
class RecoveryRuntime:
    hold: PostgreSQLRecoveryHoldStore
    store: PostgreSQLRecoveryReleaseStore
    workflow: RecoveryWorkflow


@asynccontextmanager
async def open_recovery_runtime():
    """Yield the protected hold/store/workflow and close all owned resources."""
    try:
        settings, url, raw = configuration()
        async with AsyncExitStack() as stack:
            engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True,
                echo=False, echo_pool=False, connect_args={'connect_timeout': 5,
                    'options': '-c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0'})
            stack.push_async_callback(engine.dispose)
            await preflight(engine, settings.binding)
            clients = []
            for cls in (WorkOSRecoverySessions, WorkOSRemoval, WorkOSTransportCandidate):
                client = cls(settings)
                stack.push_async_callback(client.aclose)
                clients.append(client)
            require(all(ops.read_protected(ops.ROOT / name) == value for name, value in raw.items()))
            hold = PostgreSQLRecoveryHoldStore(engine)
            store = PostgreSQLRecoveryReleaseStore(engine)
            yield RecoveryRuntime(hold, store, RecoveryWorkflow(store, *clients))
    except (asyncio.CancelledError, RecoveryProofUncertain):
        raise
    except Exception:
        raise RecoveryProofUncertain() from None
