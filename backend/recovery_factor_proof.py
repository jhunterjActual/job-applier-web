"""Inert possession proof for recovery credential issuance/replacement.

No routes, credential generation, provider deletion or production store wiring.
The injected store must durably reserve the shared account security slot and
validate every transition and current actor authority in its transaction. The
final consume transaction must bind its eventual credential action to this exact
operation and purpose; the returned status is not a reusable authorization token.
Unknown commit/provider outcomes retain their claims and cannot be retried.
"""
from dataclasses import dataclass, replace
import re
import time
from uuid import UUID

from factor_removal import FactorObservation, RemovalChallenge, RemovalVerification
from totp_enrollment import EnrollmentActor


class RecoveryProofRejected(Exception):
    def __init__(self):
        super().__init__('Recovery credential verification is unavailable.')


class RecoveryProofUncertain(Exception):
    def __init__(self):
        super().__init__('Recovery credential verification needs reconciliation.')


PURPOSES = frozenset(('issue_recovery_credentials', 'replace_recovery_credentials'))


@dataclass(frozen=True, repr=False)
class RecoveryProofRecord:
    operation: str
    actor: EnrollmentActor
    factor: str
    purpose: str
    state: str
    deadline: int
    challenge: str | None = None
    attempts: int = 0
    verified_at: int | None = None

    def __post_init__(self):
        try:
            valid = (type(self.operation) is str and str(UUID(self.operation)) == self.operation
                and isinstance(self.actor, EnrollmentActor)
                and type(self.factor) is str and re.fullmatch(r'auth_factor_[A-Za-z0-9_]{6,160}', self.factor)
                and self.purpose in PURPOSES
                and self.state in ('challenge_claimed', 'challenge_ready', 'verification_claimed', 'consumed')
                and type(self.deadline) is int and self.deadline > 0
                and type(self.attempts) is int and 0 <= self.attempts <= 5)
            if self.state == 'challenge_claimed':
                valid = valid and self.challenge is None and self.attempts == 0
            else:
                valid = valid and type(self.challenge) is str and re.fullmatch(r'auth_challenge_[A-Za-z0-9_]{6,160}', self.challenge)
            if self.state in ('verification_claimed', 'consumed'):
                valid = valid and self.attempts > 0
            valid = valid and (type(self.verified_at) is int and 0 < self.verified_at < self.deadline
                if self.state == 'consumed' else self.verified_at is None)
            if not valid: raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise RecoveryProofRejected() from None


class RecoveryFactorProofCoordinator:
    PROOF_SECONDS = 30

    def __init__(self, store, provider, *, now=time.time):
        self.store, self.provider, self.now = store, provider, now

    async def _call(self, method, *args, **kwargs):
        try:
            return await method(*args, **kwargs)
        except Exception:
            raise RecoveryProofUncertain() from None

    async def _eligible(self, actor):
        if (not isinstance(actor, EnrollmentActor)
                or not 0 <= self.now() - actor.authenticated_at < 300
                or await self._call(self.store.eligible, actor) is not True
                or not 0 <= self.now() - actor.authenticated_at < 300):
            raise RecoveryProofRejected()

    @staticmethod
    def _bound(result, row):
        auth = row.actor.authorization
        return (result.provider_binding == auth.provider_binding
            and result.subject == auth.subject and result.factor == row.factor)

    async def _observe(self, row):
        result = await self._call(self.provider.observe, row.actor.authorization.subject, row.factor)
        if (not isinstance(result, FactorObservation) or not self._bound(result, row)
                or result.complete is not True or result.kind != 'totp'
                or result.disposition != 'present'):
            raise RecoveryProofUncertain()

    async def _cas(self, old, new, actor):
        if await self._call(self.store.cas, old, new, actor=actor) is not True:
            raise RecoveryProofUncertain()

    async def start(self, actor, operation, factor, purpose):
        await self._eligible(actor)
        row = RecoveryProofRecord(operation, actor, factor, purpose, 'challenge_claimed', int(self.now()) + 300)
        await self._observe(row)
        if await self._call(self.store.reserve, row) is not True:
            raise RecoveryProofRejected()
        await self._eligible(actor)
        if self.now() >= row.deadline: raise RecoveryProofRejected()
        challenge = await self._call(self.provider.challenge, actor.authorization.subject, factor)
        if (not isinstance(challenge, RemovalChallenge) or not self._bound(challenge, row)
                or type(challenge.expires) is not int or challenge.expires <= self.now()):
            raise RecoveryProofUncertain()
        try:
            pending = replace(row, state='challenge_ready', challenge=challenge.challenge,
                              deadline=min(row.deadline, challenge.expires))
        except RecoveryProofRejected:
            raise RecoveryProofUncertain() from None
        await self._cas(row, pending, actor)
        return operation

    async def verify(self, actor, operation, factor, purpose, code):
        await self._eligible(actor)
        row = await self._call(self.store.get, actor, operation)
        if (not isinstance(row, RecoveryProofRecord) or row.operation != operation
                or row.actor != actor or row.factor != factor or row.purpose != purpose
                or row.state != 'challenge_ready' or self.now() >= row.deadline
                or row.attempts >= 5 or type(code) is not str
                or re.fullmatch(r'[0-9]{6}', code) is None):
            raise RecoveryProofRejected()
        busy = replace(row, state='verification_claimed', attempts=row.attempts + 1)
        await self._cas(row, busy, actor)
        await self._eligible(actor)
        if self.now() >= busy.deadline: raise RecoveryProofRejected()
        proof = await self._call(self.provider.verify, actor.authorization.subject, factor, row.challenge, code)
        if (not isinstance(proof, RemovalVerification) or not self._bound(proof, row)
                or proof.challenge != row.challenge or type(proof.valid) is not bool):
            raise RecoveryProofUncertain()
        if proof.valid is False:
            await self._cas(busy, replace(busy, state='challenge_ready'), actor)
            return 'code_not_accepted'
        verified_at = int(self.now())
        await self._observe(row)
        await self._eligible(actor)
        if not 0 <= self.now() - verified_at < self.PROOF_SECONDS or self.now() >= row.deadline:
            raise RecoveryProofRejected()
        consumed = replace(busy, state='consumed', verified_at=verified_at)
        # Store rechecks freshness and binds the action atomically. No bearer
        # proof escapes this call; a lost acknowledgment never permits replay.
        if await self._call(self.store.consume, busy, consumed, actor=actor) is not True:
            raise RecoveryProofUncertain()
        return 'proof_consumed'
