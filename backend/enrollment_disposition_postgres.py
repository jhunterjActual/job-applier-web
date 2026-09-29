"""Inert restricted adapter for atomic attributable enrollment completion."""
import asyncio
from dataclasses import replace

from sqlalchemy import text

from totp_enrollment import EnrollmentActor, EnrollmentRecord, EnrollmentUncertain
from totp_postgres import PostgreSQLEnrollmentStore, encoded


class PostgreSQLEnrollmentDispositionStore(PostgreSQLEnrollmentStore):
    async def finish_verified(self, expected, completed, *, actor):
        try:
            if (not isinstance(actor,EnrollmentActor) or not isinstance(expected,EnrollmentRecord)
                    or not isinstance(completed,EnrollmentRecord) or expected.actor != actor
                    or expected.state != 'verifying'
                    or completed != replace(expected,state='complete',sealed_secret=None)):
                raise ValueError()
            auth = actor.authorization
            key = self.coordination.digest('session',auth.local_id,auth.subject,actor.session_id)
            params = {'actor':encoded(actor),'key':key,'old':encoded(expected),'new':encoded(completed)}
            async with asyncio.timeout(5):
                async with self.engine.begin() as connection:
                    await connection.exec_driver_sql("SET LOCAL lock_timeout='2s'")
                    await connection.exec_driver_sql("SET LOCAL statement_timeout='3s'")
                    value = await connection.scalar(text('SELECT careertrellis.totp_finish_verified('
                        'CAST(:actor AS jsonb),:key,CAST(:old AS jsonb),CAST(:new AS jsonb))'),params)
                # Receipt and completion must both commit before success is returned.
                if value is None:
                    return False
                if type(value) is not bool:
                    raise ValueError()
                return value
        except Exception:
            raise EnrollmentUncertain() from None
