"""Inert positive-response enrollment receipts; never infer proof from membership.

Injected provider.verify must validate exact challenge/factor IDs before returning
a literal boolean, as WorkOSTOTP does. is_verified is used ONLY as complete user
factor membership readback. The atomic store seals the receipt to the claimed
operation; no browser input or reconciliation can manufacture one.
"""
from dataclasses import replace
import re

from totp_enrollment import EnrollmentActor, EnrollmentCoordinator, EnrollmentRejected, EnrollmentUncertain


class AttributableEnrollmentCoordinator(EnrollmentCoordinator):
    async def _call(self, method, *args, **kwargs):
        try:
            return await method(*args, **kwargs)
        except Exception:
            raise EnrollmentUncertain() from None

    async def _record(self, actor, operation, *, recovery=False):
        try:
            return await super()._record(actor,operation,recovery=recovery)
        except (EnrollmentRejected,EnrollmentUncertain):
            raise
        except Exception:
            raise EnrollmentUncertain() from None

    async def _eligible(self, actor):
        if (not isinstance(actor, EnrollmentActor)
                or not 0 <= self.now()-actor.authenticated_at < 300
                or await self._call(self.store.eligible,actor) is not True
                or not 0 <= self.now()-actor.authenticated_at < 300):
            raise EnrollmentRejected()

    async def _fresh(self, actor, row):
        await self._eligible(actor)
        if row.deadline <= self.now():
            raise EnrollmentRejected()

    async def verify(self, actor, operation, code):
        row = await self._record(actor,operation)
        if (row.state != 'pending' or type(row.attempts) is not int or not 0 <= row.attempts < 5
                or type(code) is not str or re.fullmatch(r'[0-9]{6}',code) is None
                or type(row.factor) is not str or re.fullmatch(r'auth_factor_[A-Za-z0-9_]{6,160}',row.factor) is None
                or type(row.challenge) is not str or re.fullmatch(r'auth_challenge_[A-Za-z0-9_]{6,160}',row.challenge) is None):
            raise EnrollmentRejected()
        await self._fresh(actor,row)
        busy = replace(row,state='verifying',attempts=row.attempts+1)
        if await self._call(self.store.cas,row,busy,actor=actor) is not True:
            raise EnrollmentUncertain()
        await self._fresh(actor,busy)
        valid = await self._call(self.provider.verify,row.challenge,row.factor,code)
        if valid is False:
            await self._fresh(actor,busy)
            if await self._call(self.store.cas,busy,replace(busy,state='pending'),actor=actor) is not True:
                raise EnrollmentUncertain()
            return 'code_not_accepted'
        if valid is not True:
            raise EnrollmentUncertain()
        await self._fresh(actor,busy)
        if await self._call(self.provider.is_verified,actor.authorization.subject,row.factor) is not True:
            raise EnrollmentUncertain()
        await self._fresh(actor,busy)
        completed = replace(busy,state='complete',sealed_secret=None)
        if await self._call(self.store.finish_verified,busy,completed,actor=actor) is not True:
            raise EnrollmentUncertain()
        return 'complete'

    async def reconcile(self, actor, operation):
        # A legacy complete row may be reported but can never gain a receipt.
        # An ambiguous verification remains claimed even when its factor is listed.
        row = await self._record(actor,operation,recovery=True)
        return 'complete' if row.state == 'complete' else 'unresolved'
