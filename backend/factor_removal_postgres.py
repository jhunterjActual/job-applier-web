"""Restricted durable removal store; no connections or configuration at import."""
import asyncio
from dataclasses import asdict, fields
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from session_authorization import SessionAuthorization
from totp_enrollment import EnrollmentActor
from factor_removal import RemovalRecord, RemovalUncertain


def encoded(value):
    return json.dumps(asdict(value), separators=(',', ':'), allow_nan=False)


def record(value):
    if value is None:
        return None
    try:
        # No coercion from strings, sequences, missing fields or future schemas.
        if type(value) is not dict or set(value) != {f.name for f in fields(RemovalRecord)}:
            raise ValueError()
        data = value.copy()
        actor = data['actor']
        if type(actor) is not dict or type(actor.get('authorization')) is not dict:
            raise ValueError()
        actor = actor.copy()
        actor['authorization'] = SessionAuthorization(**actor['authorization'])
        data['actor'] = EnrollmentActor(**actor)
        return RemovalRecord(**data)
    except Exception:
        raise RemovalUncertain() from None


class PostgreSQLRemovalStore:
    def __init__(self, engine, coordination):
        if not isinstance(engine, AsyncEngine) or engine.url.username != 'careertrellis_runtime':
            raise ValueError('Restricted runtime engine required.')
        self.engine, self.coordination = engine, coordination

    async def _query(self, actor, action, *, operation=None, expected=None, replacement=None):
        try:
            if not isinstance(actor, EnrollmentActor) or action not in ('eligible','get','reserve','cas','finalize'):
                raise ValueError()
            if action != 'eligible' and not (action == 'get' and operation is None) and (type(operation) is not str or str(UUID(operation)) != operation):
                raise ValueError()
            auth = actor.authorization
            # Same server-owned domain and fields as Coordination.session_key.
            session_key = self.coordination.digest('session', auth.local_id, auth.subject, actor.session_id)
            params = {'actor': encoded(actor), 'key': session_key, 'action': action,
                      'op': operation, 'old': encoded(expected) if expected is not None else None,
                      'new': encoded(replacement) if replacement is not None else None}
            async with asyncio.timeout(5):
                async with self.engine.begin() as connection:
                    await connection.exec_driver_sql("SET LOCAL lock_timeout='2s'")
                    await connection.exec_driver_sql("SET LOCAL statement_timeout='3s'")
                    value = await connection.scalar(text('SELECT careertrellis.factor_removal_request('
                        'CAST(:actor AS jsonb),:key,:action,CAST(:op AS uuid),'
                        'CAST(:old AS jsonb),CAST(:new AS jsonb))'), params)
                return value  # A failed or uncertain COMMIT must never authorize dispatch.
        except Exception:
            # CancelledError/BaseException propagate; no retries, logging or raw errors.
            raise RemovalUncertain() from None

    @staticmethod
    def _boolean(value):
        if value is None:
            return False  # SQL deliberately returns NULL for denied authority.
        if type(value) is not bool:
            raise RemovalUncertain()
        return value

    async def eligible(self, actor):
        return self._boolean(await self._query(actor, 'eligible'))

    async def reserve(self, row):
        if not isinstance(row, RemovalRecord):
            raise RemovalUncertain()
        return self._boolean(await self._query(row.actor, 'reserve', operation=row.operation, replacement=row))

    async def get(self, actor, operation=None):
        row = record(await self._query(actor, 'get', operation=operation))
        if row is not None and ((operation is not None and row.operation != operation) or row.actor.authorization != actor.authorization):
            raise RemovalUncertain()
        return row

    async def finalize(self, actor, operation):
        return self._boolean(await self._query(actor, 'finalize', operation=operation))

    async def cas(self, old, new, *, actor):
        if (not isinstance(old, RemovalRecord) or not isinstance(new, RemovalRecord)
                or not isinstance(actor, EnrollmentActor) or old.operation != new.operation
                or old.actor != new.actor or old.factor != new.factor
                or actor.authorization != old.actor.authorization):
            raise RemovalUncertain()
        # SQL revalidates state transitions, live authority and the shared slot.
        return self._boolean(await self._query(actor, 'cas', operation=old.operation, expected=old, replacement=new))
