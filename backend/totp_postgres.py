"""Restricted durable enrollment store. No connections or environment at import."""
import asyncio
from dataclasses import asdict
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.exc import SQLAlchemyError

from session_authorization import SessionAuthorization
from totp_enrollment import EnrollmentActor, EnrollmentRecord, EnrollmentUncertain


def encoded(value):
    return json.dumps(asdict(value), separators=(',', ':'), allow_nan=False)


def record(value):
    if value is None:
        return None
    try:
        data = dict(value)
        actor = dict(data.pop('actor'))
        actor['authorization'] = SessionAuthorization(**actor['authorization'])
        data['actor'] = EnrollmentActor(**actor)
        row = EnrollmentRecord(**data)
        if str(UUID(row.operation)) != row.operation:
            raise ValueError()
        return row
    except (ValueError, TypeError, KeyError):
        raise EnrollmentUncertain() from None


class PostgreSQLEnrollmentStore:
    def __init__(self, engine, coordination):
        if not isinstance(engine, AsyncEngine) or engine.url.username != 'careertrellis_runtime':
            raise ValueError('Restricted runtime engine required.')
        self.engine, self.coordination = engine, coordination

    async def _query(self, actor, action, *, operation=None, expected=None, replacement=None):
        if not isinstance(actor, EnrollmentActor):
            raise EnrollmentUncertain()
        # Exact same HMAC domain/fields as Coordination.session_key; never supplied
        # by the browser. Current actor is independent of the stored original actor.
        auth = actor.authorization
        session_key = self.coordination.digest('session', auth.local_id, auth.subject, actor.session_id)
        try:
            async with asyncio.timeout(5):
                async with self.engine.begin() as connection:
                    await connection.exec_driver_sql("SET LOCAL lock_timeout='2s'")
                    await connection.exec_driver_sql("SET LOCAL statement_timeout='3s'")
                    value = await connection.scalar(text('SELECT careertrellis.totp_request('
                        'CAST(:actor AS jsonb),:key,:action,CAST(:op AS uuid),'
                        'CAST(:old AS jsonb),CAST(:new AS jsonb))'),
                        {'actor': encoded(actor), 'key': session_key, 'action': action,
                         'op': operation, 'old': encoded(expected) if expected else None,
                         'new': encoded(replacement) if replacement else None})
                return value  # COMMIT must succeed before a provider call follows.
        except (SQLAlchemyError, OSError, TimeoutError, ValueError):
            raise EnrollmentUncertain() from None

    async def eligible(self, actor):
        return await self._query(actor, 'eligible') is True

    async def reserve(self, row):
        return await self._query(row.actor, 'reserve', operation=row.operation, replacement=row) is True

    async def get(self, actor, operation=None):
        return record(await self._query(actor, 'get', operation=operation))

    async def cas(self, old, new, *, actor):
        return await self._query(actor, 'cas', operation=old.operation, expected=old, replacement=new) is True


async def scrub_expired(engine, *, maximum=100):
    """One bounded batch, aggregate result only; never delete operation slots."""
    if (not isinstance(engine, AsyncEngine) or engine.url.username != 'careertrellis_migration'
            or type(maximum) is not int or not 1 <= maximum <= 1000):
        raise ValueError('Restricted scrub engine and bounded batch required.')
    try:
        async with asyncio.timeout(5):
            async with engine.begin() as connection:
                await connection.exec_driver_sql("SET LOCAL lock_timeout='2s'")
                await connection.exec_driver_sql("SET LOCAL statement_timeout='3s'")
                count = await connection.scalar(text('SELECT careertrellis_admission.totp_scrub_expired(:n)'), {'n': maximum})
            return {'expired_secrets_scrubbed': count}
    except (SQLAlchemyError, OSError, TimeoutError):
        raise EnrollmentUncertain() from None
