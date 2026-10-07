"""Protected recovery-credential consumption and account-hold adapter.

This adapter is intentionally separate from the web-runtime credential store.
It requires the restricted migration connection, reads the active set inside
the same transaction as the consume-and-hold call, and never sends plaintext
credentials to PostgreSQL or provider code.
"""
import asyncio
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from recovery_credentials import credential_hash
from recovery_credentials_postgres import actor_record
from recovery_factor_proof import RecoveryProofRejected, RecoveryProofUncertain


class PostgreSQLRecoveryHoldStore:
    def __init__(self, engine):
        if not isinstance(engine, AsyncEngine) or engine.url.username != 'careertrellis_migration':
            raise ValueError('Restricted migration engine required.')
        self.engine = engine

    async def reconcile_hold(self, account_id, generation, recovery_operation,
                             target_binding, target_subject, until_time,
                             owner_authorized):
        """Read the exact committed request receipt; never replay a mutation."""
        try:
            async with asyncio.timeout(8):
                async with self.engine.begin() as connection:
                    await connection.exec_driver_sql('SET TRANSACTION READ ONLY')
                    await connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
                    result = await connection.scalar(text(
                        'SELECT careertrellis_admission.recovery_credentials_hold_receipt('
                        ':account_id,CAST(:generation AS uuid),CAST(:recovery_operation AS uuid),'
                        ':target_binding,:target_subject,:until_time,:owner_authorized)'), {
                            'account_id': account_id, 'generation': generation,
                            'recovery_operation': recovery_operation,
                            'target_binding': target_binding, 'target_subject': target_subject,
                            'until_time': until_time, 'owner_authorized': owner_authorized})
                    if (type(result) is not dict or result.get('outcome') != 'hold_recorded'
                            or result.get('operation') != recovery_operation
                            or type(result.get('epoch')) is not int):
                        raise RecoveryProofUncertain()
                    return result
        except (RecoveryProofUncertain, asyncio.CancelledError):
            raise
        except Exception:
            raise RecoveryProofUncertain() from None

    async def consume_and_hold(self, account_id, generation, recovery_operation,
                               target_binding, target_subject, until_time,
                               owner_authorized, code):
        try:
            valid = (type(account_id) is int and account_id > 0
                and type(generation) is str and str(UUID(generation)) == generation
                and type(recovery_operation) is str
                and str(UUID(recovery_operation)) == recovery_operation
                and type(target_binding) is str and len(target_binding) == 64
                and all(c in '0123456789abcdef' for c in target_binding)
                and type(target_subject) is str and target_subject.startswith('user_')
                and type(owner_authorized) is bool and isinstance(until_time, datetime)
                and until_time.tzinfo is not None and type(code) is str)
        except Exception:
            valid = False
        if not valid:
            raise RecoveryProofUncertain()
        try:
            async with asyncio.timeout(8):
                async with self.engine.begin() as connection:
                    await connection.exec_driver_sql("SET LOCAL lock_timeout='2s'")
                    await connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
                    context = await connection.scalar(text(
                        'SELECT careertrellis_admission.recovery_credentials_hold_context('
                        ':account_id,CAST(:generation AS uuid))'), {
                            'account_id': account_id, 'generation': generation})
                    if type(context) is not dict:
                        raise RecoveryProofUncertain()
                    actor = actor_record(context.get('actor'))
                    if actor.authorization.local_id != account_id:
                        raise RecoveryProofUncertain()
                    operation = context.get('operation')
                    purpose = context.get('purpose')
                    stored_generation = context.get('generation')
                    if (type(operation) is not str or str(UUID(operation)) != operation
                            or type(purpose) is not str
                            or type(stored_generation) is not str
                            or stored_generation != generation
                            or type(context.get('hashes')) is not list):
                        raise RecoveryProofUncertain()
                    try:
                        digest = credential_hash(actor, operation, purpose, generation, code)
                    except RecoveryProofRejected:
                        # Charge malformed credential attempts in the same durable
                        # budget as well-formed incorrect credentials.
                        digest = None
                    result = await connection.scalar(text(
                        'SELECT careertrellis_admission.recovery_credentials_consume_hold('
                        ':account_id,CAST(:generation AS uuid),CAST(:recovery_operation AS uuid),'
                        ':target_binding,:target_subject,:until_time,:owner_authorized,:submitted)'), {
                            'account_id': account_id, 'generation': generation,
                            'recovery_operation': recovery_operation,
                            'target_binding': target_binding, 'target_subject': target_subject,
                            'until_time': until_time, 'owner_authorized': owner_authorized,
                            'submitted': digest})
                    if type(result) is not dict or result.get('outcome') not in (
                            'account_held', 'code_not_accepted'):
                        raise RecoveryProofUncertain()
                    return result
        except (RecoveryProofUncertain, asyncio.CancelledError):
            raise
        except Exception:
            raise RecoveryProofUncertain() from None
