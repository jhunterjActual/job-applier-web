"""Voluntary enrollment coordinator; no routes, configuration reads or clients.

Store implementations must commit reserve/CAS before returning, enforce a unique
account/incarnation/provider slot across processes, and recheck live eligibility
inside each transaction. Ambiguous operations are never automatically released.
The store is intentionally injected; the application cannot enable this module
until its PostgreSQL authorization boundary has been implemented and rehearsed.
"""
from dataclasses import dataclass, replace
import json
import re
import time
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from session_authorization import SessionAuthorization


class EnrollmentRejected(Exception):
    def __init__(self):
        super().__init__('Authenticator setup is unavailable for this action.')


class EnrollmentUncertain(Exception):
    def __init__(self):
        super().__init__('Authenticator setup needs reconciliation. Do not restart setup.')


@dataclass(frozen=True, repr=False)
class EnrollmentActor:
    authorization: SessionAuthorization
    session_id: str
    browser: str
    authenticated_at: int

    def __post_init__(self):
        if (not isinstance(self.authorization, SessionAuthorization)
                or not isinstance(self.session_id, str)
                or not re.fullmatch(r'session_[A-Za-z0-9_]{6,160}', self.session_id)
                or not isinstance(self.browser, str)
                or not re.fullmatch(r'[a-f0-9]{64}', self.browser)
                or type(self.authenticated_at) is not int):
            raise EnrollmentRejected()


@dataclass(frozen=True, repr=False)
class EnrollmentRecord:
    operation: str
    actor: EnrollmentActor
    state: str
    deadline: int
    factor: str | None = None
    challenge: str | None = None
    sealed_secret: str | None = None
    attempts: int = 0


@dataclass(frozen=True, repr=False)
class EnrollmentSetup:
    factor: str
    challenge: str
    secret: str
    expires: int


class EnrollmentCoordinator:
    def __init__(self, store, provider, key, *, now=time.time):
        self.store, self.provider = store, provider
        self.seal, self.now = Fernet(key), now

    async def _eligible(self, actor):
        now = int(self.now())
        if (not isinstance(actor, EnrollmentActor)
                or not 0 <= now - actor.authenticated_at < 300
                or await self.store.eligible(actor) is not True):
            raise EnrollmentRejected()

    async def _record(self, actor, operation, *, recovery=False):
        await self._eligible(actor)
        row = await self.store.get(actor, operation)
        if (not isinstance(row, EnrollmentRecord) or row.operation != operation
                or (row.actor.authorization != actor.authorization if recovery else row.actor != actor)):
            raise EnrollmentRejected()
        return row

    async def start(self, actor):
        await self._eligible(actor)
        row = EnrollmentRecord(str(uuid4()), actor, 'creating', int(self.now()) + 300)
        # Commit uncertainty BEFORE network I/O, including cancellation/crash.
        if await self.store.reserve(row) is not True:
            raise EnrollmentRejected()
        await self._eligible(actor)
        setup = await self.provider.enroll(actor.authorization.subject)
        if (not isinstance(setup, EnrollmentSetup)
                or not isinstance(setup.factor, str)
                or not re.fullmatch(r'auth_factor_[A-Za-z0-9_]{6,160}', setup.factor)
                or not isinstance(setup.challenge, str)
                or not re.fullmatch(r'auth_challenge_[A-Za-z0-9_]{6,160}', setup.challenge)
                or not isinstance(setup.secret, str)
                or not re.fullmatch(r'[A-Z2-7]{16,128}', setup.secret)
                or type(setup.expires) is not int or setup.expires <= self.now()):
            raise EnrollmentUncertain()
        deadline = min(row.deadline, setup.expires)
        payload = {'operation': row.operation, 'actor': self._binding(actor),
                   'secret': setup.secret, 'deadline': deadline}
        sealed = self.seal.encrypt(json.dumps(payload, separators=(',', ':')).encode()).decode()
        pending = replace(row, state='pending', deadline=deadline, factor=setup.factor,
                          challenge=setup.challenge, sealed_secret=sealed)
        # A failed write leaves creating, never a second create opportunity.
        if await self.store.cas(row, pending, actor=actor) is not True:
            raise EnrollmentUncertain()
        await self._eligible(actor)
        return pending.operation

    @staticmethod
    def _binding(actor):
        a = actor.authorization
        return [a.local_id, a.subject, a.provider_binding, a.epoch, a.incarnation,
                actor.session_id, actor.browser, actor.authenticated_at]

    async def setup_secret(self, actor, operation):
        row = await self._record(actor, operation)
        if row.state != 'pending' or row.deadline <= self.now() or not row.sealed_secret:
            raise EnrollmentRejected()
        try:
            data = json.loads(self.seal.decrypt(row.sealed_secret.encode()))
            if (data['operation'] != operation or data['actor'] != self._binding(actor)
                    or data['deadline'] != row.deadline
                    or not re.fullmatch(r'[A-Z2-7]{16,128}', data['secret'])):
                raise ValueError()
            return data['secret']
        except (InvalidToken, ValueError, TypeError, KeyError):
            raise EnrollmentRejected() from None

    async def verify(self, actor, operation, code):
        row = await self._record(actor, operation)
        if (row.state != 'pending' or row.deadline <= self.now() or row.attempts >= 5
                or not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code)):
            raise EnrollmentRejected()
        busy = replace(row, state='verifying', attempts=row.attempts + 1)
        if await self.store.cas(row, busy, actor=actor) is not True:
            raise EnrollmentRejected()
        await self._eligible(actor)
        if row.deadline <= self.now():
            raise EnrollmentRejected()
        valid = await self.provider.verify(row.challenge, row.factor, code)
        # Only an explicit invalid response permits a bounded new code attempt.
        if valid is False:
            pending = replace(busy, state='pending')
            if await self.store.cas(busy, pending, actor=actor) is not True:
                raise EnrollmentUncertain()
            return 'code_not_accepted'
        if valid is not True:
            raise EnrollmentUncertain()
        return await self.reconcile(actor, operation)

    async def reconcile(self, actor, operation):
        # Fresh authentication may inspect completion after the original action
        # expires. It cannot reveal its setup secret or retry its mutations.
        row = await self._record(actor, operation, recovery=True)
        if row.state == 'complete':
            return 'complete'
        if not row.factor:
            return 'unresolved'
        # Absence NEVER authorizes retry or deletion, even after local expiry.
        if await self.provider.is_verified(actor.authorization.subject, row.factor) is not True:
            return 'unresolved'
        complete = replace(row, state='complete', sealed_secret=None)
        if await self.store.cas(row, complete, actor=actor) is not True:
            raise EnrollmentUncertain()
        return 'complete'

    async def close(self, actor, operation):
        row = await self._record(actor, operation, recovery=True)
        if row.state == 'complete':
            return 'complete'
        # Preserve uncertainty and IDs. No provider delete/cancel is implemented.
        closed = replace(row, state='closed', sealed_secret=None)
        if await self.store.cas(row, closed, actor=actor) is not True:
            raise EnrollmentUncertain()
        return 'closed_outcome_unresolved'
