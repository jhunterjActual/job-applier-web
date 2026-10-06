"""Restricted PostgreSQL adapter for the inert recovery credential coordinators."""
import asyncio
from dataclasses import asdict, fields
import json
import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from recovery_credentials import ProposedCredentials
from recovery_factor_proof import RecoveryProofRecord, RecoveryProofRejected, RecoveryProofUncertain
from session_authorization import SessionAuthorization
from totp_enrollment import EnrollmentActor


def encoded(value):
    return json.dumps(asdict(value), sort_keys=True, separators=(',', ':'), allow_nan=False)


def actor_record(value):
    if type(value) is not dict or set(value) != {f.name for f in fields(EnrollmentActor)}:
        raise ValueError()
    result = dict(value)
    auth = result.get('authorization')
    if type(auth) is not dict:
        raise ValueError()
    result['authorization'] = SessionAuthorization(**auth)
    return EnrollmentActor(**result)


def proof_record(value):
    if value is None:
        return None
    if type(value) is not dict or set(value) != {f.name for f in fields(RecoveryProofRecord)}:
        raise ValueError()
    result = dict(value)
    result['actor'] = actor_record(result['actor'])
    return RecoveryProofRecord(**result)


def proposed_record(value):
    if value is None:
        return None
    if type(value) is not dict or set(value) != {f.name for f in fields(ProposedCredentials)}:
        raise ValueError()
    result = dict(value)
    result['actor'] = actor_record(result['actor'])
    result['hashes'] = tuple(result['hashes'])
    return ProposedCredentials(**result)


class PostgreSQLRecoveryCredentialStore:
    """Runtime-only adapter; SQL owns the operation linearization points."""

    def __init__(self, engine, coordination):
        if not isinstance(engine, AsyncEngine) or engine.url.username != 'careertrellis_runtime':
            raise ValueError('Restricted runtime engine required.')
        self.engine, self.coordination = engine, coordination

    async def _query(self, actor, action, *, operation=None, expected=None,
                     replacement=None, proposed=None, submitted=None):
        try:
            if not isinstance(actor, EnrollmentActor) or action not in (
                    'eligible', 'factor', 'get', 'reserve', 'cas', 'propose', 'pending', 'confirm'):
                raise ValueError()
            if action not in ('eligible', 'factor') and (type(operation) is not str or str(UUID(operation)) != operation):
                raise ValueError()
            auth = actor.authorization
            session_key = self.coordination.digest('session', auth.local_id, auth.subject, actor.session_id)
            params = {
                'actor': encoded(actor), 'key': session_key, 'action': action,
                'op': operation, 'old': encoded(expected) if expected is not None else None,
                'new': encoded(replacement) if replacement is not None else None,
                'proposed': encoded(proposed) if proposed is not None else None,
                'submitted': submitted,
            }
            async with asyncio.timeout(5):
                async with self.engine.begin() as connection:
                    await connection.exec_driver_sql("SET LOCAL lock_timeout='2s'")
                    await connection.exec_driver_sql("SET LOCAL statement_timeout='3s'")
                    value = await connection.scalar(text(
                        'SELECT careertrellis.recovery_credentials_request('
                        'CAST(:actor AS jsonb),:key,:action,CAST(:op AS uuid),'
                        'CAST(:old AS jsonb),CAST(:new AS jsonb),'
                        'CAST(:proposed AS jsonb),:submitted)'), params)
                return value
        except (RecoveryProofRejected, RecoveryProofUncertain):
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RecoveryProofUncertain() from None

    @staticmethod
    def _boolean(value):
        if type(value) is not bool:
            raise RecoveryProofUncertain()
        return value

    async def eligible(self, actor):
        return self._boolean(await self._query(actor, 'eligible'))

    async def enrolled_factor(self, actor):
        value = await self._query(actor, 'factor')
        if value is None:
            raise RecoveryProofRejected()
        if type(value) is not str or re.fullmatch(r'auth_factor_[A-Za-z0-9_]{6,160}', value) is None:
            raise RecoveryProofUncertain()
        return value

    async def get(self, actor, operation):
        try:
            row = proof_record(await self._query(actor, 'get', operation=operation))
        except Exception:
            raise RecoveryProofUncertain() from None
        if row is not None and (row.operation != operation or row.actor.authorization != actor.authorization):
            raise RecoveryProofUncertain()
        return row

    async def reserve(self, row):
        if not isinstance(row, RecoveryProofRecord):
            raise RecoveryProofUncertain()
        return self._boolean(await self._query(row.actor, 'reserve', operation=row.operation, replacement=row))

    async def cas(self, old, new, *, actor):
        if (not isinstance(old, RecoveryProofRecord) or not isinstance(new, RecoveryProofRecord)
                or old.operation != new.operation or old.actor != new.actor
                or actor.authorization != old.actor.authorization):
            raise RecoveryProofUncertain()
        return self._boolean(await self._query(actor, 'cas', operation=old.operation,
                                               expected=old, replacement=new))

    async def propose(self, old, new, proposed, *, actor):
        if (not isinstance(old, RecoveryProofRecord) or not isinstance(new, RecoveryProofRecord)
                or not isinstance(proposed, ProposedCredentials)
                or old.operation != new.operation or old.operation != proposed.operation
                or actor != old.actor or proposed.actor != actor):
            raise RecoveryProofUncertain()
        return self._boolean(await self._query(actor, 'propose', operation=old.operation,
                                               expected=old, replacement=new, proposed=proposed))

    async def pending(self, actor, operation):
        try:
            return proposed_record(await self._query(actor, 'pending', operation=operation))
        except Exception:
            raise RecoveryProofUncertain() from None

    async def confirm(self, pending, digest, *, actor):
        if not isinstance(pending, ProposedCredentials) or actor != pending.actor:
            raise RecoveryProofUncertain()
        return self._boolean(await self._query(actor, 'confirm', operation=pending.operation,
                                               proposed=pending, submitted=digest))
