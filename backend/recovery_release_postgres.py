"""Restricted attended recovery persistence, never a browser assertion API.

The operator coordinator must verify complete provider observations, exact
subject/factor ownership and signed fresh authentication before `complete`.
Possession of an arbitrary evidence dictionary is not authorization. The engine
is the existing protected migration connection, never the application engine.
Every call commits before returning; a lost result must be read through context,
never retried as another provider effect. Unknown/failed effects retain the hold.
"""
import asyncio
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from recovery_factor_proof import RecoveryProofUncertain


class PostgreSQLRecoveryReleaseStore:
    def __init__(self, engine):
        if not isinstance(engine, AsyncEngine) or engine.url.username != 'careertrellis_migration':
            raise ValueError('Restricted migration engine required.')
        self.engine = engine

    async def _request(self, operation, action, payload=None):
        try:
            async with asyncio.timeout(8):
                async with self.engine.begin() as connection:
                    await connection.exec_driver_sql("SET LOCAL lock_timeout='2s'")
                    await connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
                    return await connection.scalar(text(
                        'SELECT careertrellis_admission.recovery_release_request('
                        'CAST(:operation AS uuid),:action,CAST(:payload AS jsonb))'), {
                            'operation': operation, 'action': action,
                            'payload': json.dumps(payload or {}, allow_nan=False)})
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RecoveryProofUncertain() from None

    async def context(self, operation):
        result = await self._request(operation, 'context')
        if type(result) is not dict or result.get('operation') != operation:
            raise RecoveryProofUncertain()
        return result

    async def reserve(self, operation, epoch, incarnation, effects, plan_digest):
        result = await self._request(operation, 'reserve', dict(
            epoch=epoch, incarnation=incarnation, effects=effects, plan_digest=plan_digest))
        if result is not True:
            raise RecoveryProofUncertain()
        return True

    async def claim(self, operation, effect_id):
        result = await self._request(operation, 'claim', {'effect_id': effect_id})
        if type(result) is not bool:
            raise RecoveryProofUncertain()
        return result

    async def record(self, operation, effect_id, outcome, receipt_digest):
        result = await self._request(operation, 'record', dict(
            effect_id=effect_id, outcome=outcome, receipt_digest=receipt_digest))
        if result is not True:
            raise RecoveryProofUncertain()
        return True

    async def complete(self, operation, epoch, incarnation, evidence):
        if type(evidence) is not dict or 'epoch' in evidence or 'incarnation' in evidence:
            raise RecoveryProofUncertain()
        result = await self._request(operation, 'complete', {
            **evidence, 'epoch': epoch, 'incarnation': incarnation})
        if type(result) is not dict or result != {'state': 'committed', 'epoch': epoch}:
            raise RecoveryProofUncertain()
        return result
