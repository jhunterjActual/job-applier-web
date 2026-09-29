"""Inert exact-factor removal coordinator; no routes, SDK or database connections.

Production store/transport integration is deliberately absent. A store must
commit reserve/CAS before returning, validate transitions and current authority
in that transaction, and atomically acquire/transfer the shared security slot
only with independently verified predecessor disposition. It must never steal
an enrollment slot or release uncertainty on expiry. False/unknown COMMIT
acknowledgment authorizes no provider call. Slot ownership survives completion.

Provider observations must be complete and authoritative. Typed results are a
trusted transport contract, not browser assertions or HTTP-status shortcuts.
Claims guarantee at-most-one application invocation, not exactly-once delivery.
"""
from dataclasses import dataclass, replace
import re
import time
from uuid import UUID

from totp_enrollment import EnrollmentActor


class RemovalRejected(Exception):
    def __init__(self):
        super().__init__('Authenticator removal is unavailable for this action.')


class RemovalUncertain(Exception):
    def __init__(self):
        super().__init__('Authenticator removal needs reconciliation. Do not restart it.')


@dataclass(frozen=True, repr=False)
class FactorObservation:
    provider_binding: str
    subject: str
    factor: str
    kind: str | None
    disposition: str
    complete: bool


@dataclass(frozen=True, repr=False)
class RemovalChallenge:
    provider_binding: str
    subject: str
    factor: str
    challenge: str
    expires: int


@dataclass(frozen=True, repr=False)
class RemovalVerification:
    provider_binding: str
    subject: str
    factor: str
    challenge: str
    valid: bool


@dataclass(frozen=True, repr=False)
class RemovalDeletion:
    provider_binding: str
    subject: str
    factor: str
    operation: str
    disposition: str


@dataclass(frozen=True, repr=False)
class RemovalRecord:
    operation: str
    actor: EnrollmentActor
    factor: str
    state: str
    deadline: int
    challenge: str | None = None
    attempts: int = 0
    verified_at: int | None = None
    delete_acknowledged: bool = False

    def __post_init__(self):
        try:
            valid = (type(self.operation) is str and str(UUID(self.operation)) == self.operation
                and isinstance(self.actor, EnrollmentActor)
                and type(self.factor) is str and re.fullmatch(r'auth_factor_[A-Za-z0-9_]{6,160}', self.factor)
                and self.state in ('challenge_claimed','challenge_ready','verification_claimed',
                                   'delete_claimed','delete_recorded','complete')
                and type(self.deadline) is int and self.deadline > 0
                and type(self.attempts) is int and 0 <= self.attempts <= 5
                and type(self.delete_acknowledged) is bool)
            if self.state == 'challenge_claimed':
                valid = valid and self.challenge is None and self.attempts == 0
            else:
                valid = valid and type(self.challenge) is str and re.fullmatch(
                    r'auth_challenge_[A-Za-z0-9_]{6,160}', self.challenge)
            if self.state in ('verification_claimed','delete_claimed','delete_recorded','complete'):
                valid = valid and self.attempts > 0
            if self.state in ('delete_claimed','delete_recorded','complete'):
                valid = valid and type(self.verified_at) is int and 0 < self.verified_at < self.deadline
            else:
                valid = valid and self.verified_at is None
            valid = valid and self.delete_acknowledged == (self.state in ('delete_recorded','complete'))
            if not valid: raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise RemovalRejected() from None


class FactorRemovalCoordinator:
    PROOF_SECONDS = 30

    def __init__(self, store, provider, *, now=time.time):
        self.store, self.provider, self.now = store, provider, now

    async def _call(self, method, *args, **kwargs):
        try:
            return await method(*args, **kwargs)
        except Exception:
            # Cancellation/BaseException propagates while its committed claim stays.
            raise RemovalUncertain() from None

    async def _eligible(self, actor):
        if (not isinstance(actor, EnrollmentActor)
                or not 0 <= self.now() - actor.authenticated_at < 300
                or await self._call(self.store.eligible, actor) is not True
                or not 0 <= self.now() - actor.authenticated_at < 300):
            raise RemovalRejected()

    async def _row(self, actor, operation, *, inspection=False):
        await self._eligible(actor)
        row = await self._call(self.store.get, actor, operation)
        if (not isinstance(row, RemovalRecord) or row.operation != operation
                or (row.actor.authorization != actor.authorization if inspection else row.actor != actor)):
            raise RemovalRejected()
        return row

    async def _cas(self, old, new, actor):
        if await self._call(self.store.cas, old, new, actor=actor) is not True:
            raise RemovalUncertain()

    @staticmethod
    def _bound(result, row):
        auth = row.actor.authorization
        return (result.provider_binding == auth.provider_binding
                and result.subject == auth.subject and result.factor == row.factor)

    async def _observation(self, row):
        result = await self._call(self.provider.observe, row.actor.authorization.subject, row.factor)
        if (not isinstance(result, FactorObservation) or not self._bound(result, row)
                or result.complete is not True):
            raise RemovalUncertain()
        return result

    async def start(self, actor, operation, factor):
        await self._eligible(actor)
        row = RemovalRecord(operation, actor, factor, 'challenge_claimed', int(self.now()) + 300)
        observation = await self._observation(row)
        if observation.kind != 'totp' or observation.disposition != 'present':
            raise RemovalRejected()
        # Store must bind/transfer security ownership AND claim the challenge in
        # one durable transaction; duplicate UUIDs never dispatch again.
        if await self._call(self.store.reserve, row) is not True:
            raise RemovalRejected()
        await self._eligible(actor)
        if self.now() >= row.deadline: raise RemovalRejected()
        challenge = await self._call(self.provider.challenge, actor.authorization.subject, factor)
        if (not isinstance(challenge, RemovalChallenge) or not self._bound(challenge, row)
                or type(challenge.challenge) is not str
                or not re.fullmatch(r'auth_challenge_[A-Za-z0-9_]{6,160}', challenge.challenge)
                or type(challenge.expires) is not int or challenge.expires <= self.now()):
            raise RemovalUncertain()
        pending = replace(row,state='challenge_ready',challenge=challenge.challenge,
                          deadline=min(row.deadline,challenge.expires))
        await self._cas(row,pending,actor)
        return operation

    async def verify(self, actor, operation, code):
        row = await self._row(actor, operation)
        if (row.state != 'challenge_ready' or self.now() >= row.deadline or row.attempts >= 5
                or type(code) is not str or re.fullmatch(r'[0-9]{6}', code) is None):
            raise RemovalRejected()
        busy = replace(row,state='verification_claimed',attempts=row.attempts+1)
        await self._cas(row,busy,actor)
        await self._eligible(actor)
        if self.now() >= busy.deadline: raise RemovalRejected()
        proof = await self._call(self.provider.verify,actor.authorization.subject,row.factor,row.challenge,code)
        if (not isinstance(proof, RemovalVerification) or not self._bound(proof,row)
                or proof.challenge != row.challenge or type(proof.valid) is not bool):
            raise RemovalUncertain()
        if proof.valid is False:
            await self._cas(busy,replace(busy,state='challenge_ready'),actor)
            return 'code_not_accepted'
        verified_at = int(self.now())
        if self.now() >= row.deadline: raise RemovalRejected()
        # Verify ownership again; this read may block, so proof age is checked
        # again on both sides of the durable deletion claim.
        observation = await self._observation(row)
        if observation.kind != 'totp' or observation.disposition != 'present':
            raise RemovalUncertain()
        await self._eligible(actor)
        if not 0 <= self.now()-verified_at < self.PROOF_SECONDS or self.now() >= row.deadline:
            raise RemovalRejected()
        deleting = replace(busy,state='delete_claimed',verified_at=verified_at)
        # Consumes this exact operation/actor/challenge proof. There is no public
        # resumable verified state and no path that replays delete_claimed.
        await self._cas(busy,deleting,actor)
        await self._eligible(actor)
        if not 0 <= self.now()-verified_at < self.PROOF_SECONDS or self.now() >= row.deadline:
            raise RemovalRejected()
        deleted = await self._call(self.provider.delete,actor.authorization.subject,row.factor,operation)
        if (not isinstance(deleted, RemovalDeletion) or not self._bound(deleted,row)
                or deleted.operation != operation or deleted.disposition != 'deleted'):
            raise RemovalUncertain()
        await self._cas(deleting,replace(deleting,state='delete_recorded',delete_acknowledged=True),actor)
        return await self.reconcile(actor, operation)

    async def reconcile(self, actor, operation):
        row = await self._row(actor, operation, inspection=True)
        if row.state == 'complete': return 'complete'
        # Even authoritative absence cannot attribute a lost deletion response.
        if row.state != 'delete_recorded': return 'unresolved'
        observation = await self._observation(row)
        if observation.disposition != 'absent' or observation.kind is not None:
            return 'unresolved'
        await self._cas(row,replace(row,state='complete'),actor)
        return 'complete'


class SessionEndingRemovalCoordinator(FactorRemovalCoordinator):
    """Schema0020 route contract: every success ends the completing local session."""
    async def reconcile(self, actor, operation):
        row = await self._row(actor, operation, inspection=True)
        if row.state == 'complete':
            if await self._call(self.store.finalize,actor,operation) is not True:
                raise RemovalUncertain()
            return 'complete'
        if row.state != 'delete_recorded': return 'unresolved'
        observation = await self._observation(row)
        if observation.disposition != 'absent' or observation.kind is not None:
            return 'unresolved'
        #0020 commits revocations in the same transaction as this completion.
        await self._cas(row,replace(row,state='complete'),actor)
        return 'complete'
