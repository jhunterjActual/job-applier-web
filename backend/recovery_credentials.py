"""Inert recovery credential lifecycle, without routes or production storage.

The backing store owns the shared security slot, actor eligibility and proof
transitions. propose consumes an exact verification_claimed proof and creates
its hashed pending set in ONE transaction; confirm checks custody, actor,
deadline and expected active generation before activating/revoking in ONE
transaction. Unknown acknowledgments never authorize redisplay or reissuance.
Only an acknowledged proposed set is returned, once, to the trusted caller.
Custody comparison must use constant-time digest comparisons over the fixed set
and durably count wrong/malformed attempts; no other account may be revoked.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import secrets
import time
from uuid import uuid4

from recovery_factor_proof import (RecoveryFactorProofCoordinator,
    RecoveryProofRejected, RecoveryProofUncertain)


@dataclass(frozen=True, repr=False)
class ProposedCredentials:
    operation: str
    actor: object
    purpose: str
    generation: str
    hashes: tuple
    deadline: int


@dataclass(frozen=True, repr=False)
class CredentialDisplay:
    """Sensitive one-time output: never serialize, log or place in a URL."""
    operation: str
    codes: tuple


def credential_hash(actor, operation, purpose, generation, code):
    """Bind even a copied hash to the exact issuance context.

    Future lost-factor verification must use the protected stored issuance actor,
    not the actor/session at recovery time. Account/incarnation authorization for
    recovery remains a separate check; this module does not implement it.
    """
    if type(code) is not str or re.fullmatch(r'[A-Za-z0-9_-]{43}', code) is None:
        raise RecoveryProofRejected()
    context = json.dumps([asdict(actor), operation, purpose, generation, code],
                         sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(context).hexdigest()


class _ProofStore:
    def __init__(self, backing, now):
        self.backing, self.now = backing, now
        self.display = None

    async def eligible(self, actor):
        return await self.backing.eligible(actor)

    async def reserve(self, row):
        return await self.backing.reserve(row)

    async def get(self, actor, operation):
        return await self.backing.get(actor, operation)

    async def cas(self, old, new, *, actor):
        return await self.backing.cas(old, new, actor=actor)

    async def consume(self, old, new, *, actor):
        # Trusted randomness only. A caller cannot supply credentials or claim
        # that a previous string/boolean result is possession evidence.
        codes = tuple(secrets.token_urlsafe(32) for _ in range(8))
        generation = str(uuid4())
        proposed = ProposedCredentials(old.operation, actor, old.purpose, generation,
            tuple(credential_hash(actor, old.operation, old.purpose, generation, code)
                  for code in codes), min(old.deadline, int(self.now()) + 300))
        try:
            accepted = await self.backing.propose(old, new, proposed, actor=actor)
            if accepted is not True:
                return False
            self.display = CredentialDisplay(old.operation, codes)
            return True
        finally:
            # Python immutable strings cannot promise memory zeroization. No
            # plaintext is persisted, and failure leaves no retrievable output.
            codes = ()


class RecoveryCredentialCoordinator:
    def __init__(self, store, provider, *, now=time.time):
        self.store, self.provider, self.now = store, provider, now

    async def start(self, actor, operation, factor, purpose):
        return await RecoveryFactorProofCoordinator(
            self.store, self.provider, now=self.now).start(actor, operation, factor, purpose)

    async def verify(self, actor, operation, factor, purpose, code):
        # A per-call adapter avoids sharing transient plaintext between requests.
        adapter = _ProofStore(self.store, self.now)
        try:
            result = await RecoveryFactorProofCoordinator(adapter, self.provider,
                now=self.now).verify(actor, operation, factor, purpose, code)
            if adapter.display is None:
                if result == 'code_not_accepted': return result
                raise RecoveryProofUncertain()
            return adapter.display
        finally:
            adapter.display = None

    async def confirm_custody(self, actor, operation, purpose, code):
        # The store derives the generation from protected pending state. It
        # compares a hash, never receives the submitted plaintext credential.
        try:
            if (not 0 <= self.now() - actor.authenticated_at < 300
                    or await self.store.eligible(actor) is not True):
                raise RecoveryProofRejected()
            pending = await self.store.pending(actor, operation)
            if (not isinstance(pending, ProposedCredentials) or pending.actor != actor
                    or pending.operation != operation or pending.purpose != purpose
                    or self.now() >= pending.deadline):
                raise RecoveryProofRejected()
            try:
                digest = credential_hash(actor, operation, purpose, pending.generation, code)
            except RecoveryProofRejected:
                # None is an invalid digest. The store must charge this rejected
                # attempt durably, just like a well-formed incorrect code.
                digest = None
            if await self.store.confirm(pending, digest, actor=actor) is not True:
                raise RecoveryProofRejected()
            return 'credentials_activated'
        except RecoveryProofRejected:
            raise
        except Exception:
            raise RecoveryProofUncertain() from None
