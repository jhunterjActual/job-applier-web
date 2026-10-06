"""Synthetic effects/fresh authentication tests; no provider or application data."""
import copy
from types import SimpleNamespace
import unittest
from uuid import uuid4

from factor_removal import FactorObservation, RemovalDeletion
from recovery_factor_proof import RecoveryProofUncertain
from recovery_provider import SessionObservation, SessionRevocation, FactorInventory
from recovery_workflow import RecoveryWorkflow, utc
from workos_transport_candidate import VerifiedTokens

BINDING = 'a' * 64
SUBJECT = 'user_recovery_fixture'
FACTOR = 'auth_factor_recovery_fixture'
OLD = 'session_old_fixture'
FRESH = 'session_fresh_fixture'


class Store:
    def __init__(self):
        self.value = dict(operation=str(uuid4()), epoch=2, incarnation=str(uuid4()),
            binding=BINDING, subject=SUBJECT, deadline=utc(2000), started=utc(900),
            plan_digest=None, effects=[], reserved_at=None, state='held')
        self.releases = 0
        self.lose_claim = self.lose_record = False

    async def context(self, operation):
        assert operation == self.value['operation']
        return copy.deepcopy(self.value)

    async def reserve(self, operation, epoch, incarnation, effects, plan_digest):
        assert self.value['plan_digest'] is None
        self.value.update(plan_digest=plan_digest, reserved_at=utc(950),
            effects=[dict(e, state='ready', finished_at=None) for e in effects])

    async def claim(self, operation, effect_id):
        effect = next(e for e in self.value['effects'] if e['effect_id'] == effect_id)
        if effect['state'] != 'ready':
            return False
        effect['state'] = 'claimed'
        if self.lose_claim:
            raise OSError('synthetic lost claim response')
        return True

    async def record(self, operation, effect_id, outcome, receipt_digest):
        effect = next(e for e in self.value['effects'] if e['effect_id'] == effect_id)
        assert effect['state'] == 'claimed'
        effect.update(state=outcome, finished_at=utc(999))
        if self.lose_record:
            raise OSError('synthetic lost record response')
        return True

    async def complete(self, operation, epoch, incarnation, proof):
        self.releases += 1
        self.proof = proof
        return {'state': 'committed', 'epoch': epoch}


class Provider:
    binding = BINDING
    observed_at = 1000

    def __init__(self, store):
        self.store = store
        self.session_ids = [OLD]
        self.factor_ids = [FACTOR]
        self.effects = []
        self.unknown = False

    async def observe(self, subject, factor=None):
        if factor is not None:
            return FactorObservation(BINDING, subject, factor, 'totp', 'present', True)
        return SessionObservation(BINDING, subject, tuple(self.session_ids), self.observed_at)

    async def observe_factors(self, subject):
        return FactorInventory(BINDING, subject, tuple(self.factor_ids), self.observed_at)

    def claimed(self, kind, target):
        assert any(e['kind'] == kind and e['target'] == target and e['state'] == 'claimed'
                   for e in self.store.value['effects'])
        self.effects.append((kind, target))
        if self.unknown:
            raise TimeoutError()

    async def delete(self, subject, factor, operation):
        self.claimed('delete_factor', factor)
        self.factor_ids.remove(factor)
        return RemovalDeletion(BINDING, subject, factor, operation, 'deleted')

    async def revoke(self, subject, sid, operation):
        self.claimed('revoke_session', sid)
        self.session_ids.remove(sid)
        return SessionRevocation(BINDING, subject, sid, operation, 999)


class Authentication:
    settings = SimpleNamespace(binding=BINDING)
    authenticated = 1000

    async def verify_recent(self, access, subject, session_id, maximum_age):
        assert access == 'synthetic-access' and subject == SUBJECT and session_id == FRESH
        return {'auth_time': self.authenticated, 'exp': 1500}


class RecoveryWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store()
        self.provider = Provider(self.store)
        self.auth = Authentication()
        self.flow = RecoveryWorkflow(self.store, self.provider, self.provider, self.auth, now=lambda: 1000)
        self.operation = self.store.value['operation']
        self.tokens = VerifiedTokens(SUBJECT, FRESH, 'synthetic', 'synthetic-access',
                                     'synthetic-refresh', 1500, BINDING)

    async def contain(self):
        result = await self.flow.contain_provider(self.operation, FACTOR)
        self.assertEqual(result, {'outcome': 'provider_contained_account_held'})

    async def test_claimed_effects_and_fresh_proof_release(self):
        await self.contain()
        self.assertEqual(len(self.provider.effects), 2)
        self.provider.session_ids = [FRESH]
        self.assertEqual(await self.flow.release(self.operation, self.tokens), {'state': 'committed', 'epoch': 2})
        self.assertNotIn('synthetic-access', str(self.store.proof))

    async def test_containment_resume_never_repeats_terminal_effects(self):
        await self.contain()
        await self.contain()
        self.assertEqual(len(self.provider.effects), 2)

    async def test_unknown_provider_effect_blocks_all_further_dispatch(self):
        self.provider.unknown = True
        with self.assertRaises(RecoveryProofUncertain):
            await self.contain()
        self.provider.unknown = False
        with self.assertRaises(RecoveryProofUncertain):
            await self.contain()
        self.assertEqual(len(self.provider.effects), 1)

    async def test_lost_claim_ack_never_dispatches_on_resume(self):
        self.store.lose_claim = True
        with self.assertRaises(RecoveryProofUncertain):
            await self.contain()
        self.store.lose_claim = False
        with self.assertRaises(RecoveryProofUncertain):
            await self.contain()
        self.assertEqual(self.provider.effects, [])

    async def test_lost_result_ack_does_not_overwrite_success_or_repeat(self):
        self.store.lose_record = True
        with self.assertRaises(RecoveryProofUncertain):
            await self.contain()
        self.store.lose_record = False
        await self.contain()
        self.assertEqual(len(self.provider.effects), 2)

    async def test_new_or_old_extra_session_prevents_release(self):
        await self.contain()
        self.provider.session_ids = [FRESH, 'session_unexpected_fixture']
        with self.assertRaises(RecoveryProofUncertain):
            await self.flow.release(self.operation, self.tokens)
        self.assertEqual(self.store.releases, 0)

    async def test_authentication_before_revocation_prevents_release(self):
        await self.contain()
        self.provider.session_ids = [FRESH]
        self.auth.authenticated = 998
        with self.assertRaises(RecoveryProofUncertain):
            await self.flow.release(self.operation, self.tokens)
        self.assertEqual(self.store.releases, 0)

    async def test_remaining_factor_is_not_assumed_verified(self):
        await self.contain()
        self.provider.session_ids = [FRESH]
        self.provider.factor_ids = ['auth_factor_other_fixture']
        with self.assertRaises(RecoveryProofUncertain):
            await self.flow.release(self.operation, self.tokens)
        self.assertEqual(self.store.releases, 0)

    async def test_expiry_is_bounded_by_authentication_freshness(self):
        await self.contain()
        self.provider.session_ids = [FRESH]
        self.provider.observed_at = 1250
        self.flow.now = lambda: 1250
        await self.flow.release(self.operation, self.tokens)
        self.assertEqual(self.store.proof['expires_at'], utc(1300))
