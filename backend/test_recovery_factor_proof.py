"""Synthetic durable proof store; no application data or provider calls."""
from dataclasses import asdict, replace
import json
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from factor_removal import FactorObservation, RemovalChallenge, RemovalVerification
from recovery_factor_proof import (RecoveryFactorProofCoordinator, RecoveryProofRecord,
    RecoveryProofRejected, RecoveryProofUncertain)
from session_authorization import SessionAuthorization
from totp_enrollment import EnrollmentActor


ACTOR = EnrollmentActor(SessionAuthorization(1, 'user_recovery_fixture', 'a' * 64,
    1, '00000000-0000-4000-8000-000000000001'), 'session_recovery_fixture', 'b' * 64, 1000)
FACTOR = 'auth_factor_recovery_fixture'
PURPOSE = 'replace_recovery_credentials'
CHALLENGE = 'auth_challenge_recovery_fixture'


def encode(row):
    return json.dumps(asdict(row), sort_keys=True)


class Store:
    def __init__(self, path, now):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS proof(operation TEXT PRIMARY KEY, account INTEGER UNIQUE, body TEXT)')
        self.now = now
        self.fail = None
        self.allowed = True

    async def eligible(self, actor):
        return self.allowed and actor == ACTOR and 0 <= self.now() - actor.authenticated_at < 300

    async def get(self, actor, operation):
        row = self.db.execute('SELECT body FROM proof WHERE operation=?', (operation,)).fetchone()
        if not row: return None
        value = json.loads(row[0])
        value['actor']['authorization'] = SessionAuthorization(**value['actor']['authorization'])
        value['actor'] = EnrollmentActor(**value['actor'])
        return RecoveryProofRecord(**value)

    async def reserve(self, row):
        if not await self.eligible(row.actor): return False
        try:
            with self.db:
                self.db.execute('INSERT INTO proof VALUES(?,?,?)',
                    (row.operation, row.actor.authorization.local_id, encode(row)))
        except sqlite3.IntegrityError:
            return False
        if self.fail == 'reserve': raise OSError()
        return True

    async def cas(self, old, new, *, actor):
        if not await self.eligible(actor): return False
        valid = (new == replace(old, state='challenge_ready', challenge=new.challenge, deadline=new.deadline)
            and old.state == 'challenge_claimed' and self.now() < new.deadline <= old.deadline
            or old.state == 'challenge_ready' and old.attempts < 5 and new == replace(old,
                state='verification_claimed', attempts=old.attempts + 1)
            or old.state == 'verification_claimed' and new == replace(old, state='challenge_ready'))
        if not valid or self.now() >= old.deadline: return False
        return self.write(old, new)

    def write(self, old, new):
        with self.db:
            changed = self.db.execute('UPDATE proof SET body=? WHERE operation=? AND body=?',
                (encode(new), old.operation, encode(old))).rowcount
        if self.fail == new.state: raise OSError()
        return changed == 1

    async def consume(self, old, new, *, actor):
        if (not await self.eligible(actor) or actor != old.actor
                or old.state != 'verification_claimed'
                or new != replace(old, state='consumed', verified_at=new.verified_at)
                or not 0 <= self.now() - new.verified_at < 30 or self.now() >= old.deadline):
            return False
        return self.write(old, new)


class Provider:
    def __init__(self):
        self.calls = []
        self.valid = True
        self.fail = None
        self.wrong = None
        self.observe_hook = None

    async def observe(self, subject, factor):
        self.calls.append('observe')
        if self.observe_hook: self.observe_hook()
        return FactorObservation('a' * 64, subject, factor, 'totp', 'present', True)

    async def challenge(self, subject, factor):
        self.calls.append('challenge')
        if self.fail == 'challenge': raise OSError()
        return RemovalChallenge('a' * 64, subject, factor, CHALLENGE, 1300)

    async def verify(self, subject, factor, challenge, code):
        self.calls.append('verify')
        if self.fail == 'verify': raise OSError()
        value = RemovalVerification('a' * 64, subject, factor, challenge, self.valid)
        return replace(value, **self.wrong) if self.wrong else value

    async def delete(self, *args):
        raise AssertionError('Credential proof must never delete an authenticator')


class RecoveryFactorProofTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.time = 1000
        self.path = self.temp.name + '/proof.sqlite'
        self.store = Store(self.path, lambda: self.time)
        self.provider = Provider()
        self.coordinator = RecoveryFactorProofCoordinator(self.store, self.provider, now=lambda: self.time)
        self.op = str(uuid4())

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    async def start(self):
        return await self.coordinator.start(ACTOR, self.op, FACTOR, PURPOSE)

    async def verify(self, **changes):
        args = dict(actor=ACTOR, operation=self.op, factor=FACTOR, purpose=PURPOSE, code='123456')
        args.update(changes)
        return await self.coordinator.verify(**args)

    async def test_success_consumed_without_deleting_factor_and_replay_denied(self):
        await self.start()
        self.assertEqual('proof_consumed', await self.verify())
        self.assertEqual(['observe', 'challenge', 'verify', 'observe'], self.provider.calls)
        with self.assertRaises(RecoveryProofRejected): await self.verify()
        self.assertEqual(1, self.provider.calls.count('verify'))

    async def test_issue_purpose_success_preserves_authenticator(self):
        purpose = 'issue_recovery_credentials'
        await self.coordinator.start(ACTOR, self.op, FACTOR, purpose)
        self.assertEqual('proof_consumed', await self.verify(purpose=purpose))
        self.assertEqual(purpose, (await self.store.get(ACTOR, self.op)).purpose)
        self.assertEqual(['observe', 'challenge', 'verify', 'observe'], self.provider.calls)

    async def test_purpose_factor_operation_session_and_browser_substitution_denied(self):
        await self.start()
        for changes in ({'purpose': 'issue_recovery_credentials'}, {'factor': FACTOR + 'other'},
                {'operation': str(uuid4())}, {'actor': replace(ACTOR, browser='c' * 64)},
                {'actor': replace(ACTOR, session_id='session_other_fixture')}):
            with self.subTest(changes=list(changes)):
                with self.assertRaises(RecoveryProofRejected): await self.verify(**changes)
        self.assertNotIn('verify', self.provider.calls)

    async def test_wrong_provider_challenge_remains_claimed(self):
        await self.start()
        self.provider.wrong = {'challenge': CHALLENGE + 'other'}
        with self.assertRaises(RecoveryProofUncertain): await self.verify()
        with self.assertRaises(RecoveryProofRejected): await self.verify()

    async def test_provider_binding_subject_and_factor_substitutions_are_uncertain(self):
        for field, value in (('provider_binding', 'd' * 64),
                ('subject', 'user_other_fixture'), ('factor', FACTOR + 'other')):
            with self.subTest(field=field):
                # Separate synthetic account-slot lifetime for each refusal.
                with self.store.db:
                    self.store.db.execute('DELETE FROM proof')
                self.op = str(uuid4())
                await self.start()
                self.provider.wrong = {field: value}
                with self.assertRaises(RecoveryProofUncertain): await self.verify()
                self.assertEqual('verification_claimed', (await self.store.get(ACTOR, self.op)).state)
                with self.assertRaises(RecoveryProofRejected): await self.verify()

    async def test_lost_verification_claim_ack_never_verifies_with_provider(self):
        await self.start()
        self.store.fail = 'verification_claimed'
        with self.assertRaises(RecoveryProofUncertain): await self.verify()
        self.assertNotIn('verify', self.provider.calls)
        self.assertEqual('verification_claimed', (await self.store.get(ACTOR, self.op)).state)
        with self.assertRaises(RecoveryProofRejected): await self.verify()

    async def test_ambiguous_provider_verification_cannot_retry(self):
        await self.start()
        self.provider.fail = 'verify'
        with self.assertRaises(RecoveryProofUncertain): await self.verify()
        with self.assertRaises(RecoveryProofRejected): await self.verify()
        self.assertEqual(1, self.provider.calls.count('verify'))

    async def test_ambiguous_challenge_cannot_restart_after_store_reopen(self):
        self.provider.fail = 'challenge'
        with self.assertRaises(RecoveryProofUncertain): await self.start()
        self.store.db.close()
        self.store = Store(self.path, lambda: self.time)
        self.coordinator.store = self.store
        with self.assertRaises(RecoveryProofRejected): await self.start()
        self.assertEqual(1, self.provider.calls.count('challenge'))

    async def test_lost_reserve_ack_never_dispatches(self):
        self.store.fail = 'reserve'
        with self.assertRaises(RecoveryProofUncertain): await self.start()
        self.assertNotIn('challenge', self.provider.calls)

    async def test_lost_consumption_ack_retains_consumed_state(self):
        await self.start()
        self.store.fail = 'consumed'
        with self.assertRaises(RecoveryProofUncertain): await self.verify()
        self.assertEqual('consumed', (await self.store.get(ACTOR, self.op)).state)
        with self.assertRaises(RecoveryProofRejected): await self.verify()

    async def test_explicit_wrong_codes_are_bounded(self):
        await self.start()
        self.provider.valid = False
        for _ in range(5): self.assertEqual('code_not_accepted', await self.verify())
        with self.assertRaises(RecoveryProofRejected): await self.verify()
        self.assertEqual(5, self.provider.calls.count('verify'))

    async def test_proof_expiring_during_ownership_check_is_not_consumed(self):
        await self.start()
        self.provider.observe_hook = lambda: setattr(self, 'time', 1031)
        with self.assertRaises(RecoveryProofRejected): await self.verify()
        self.assertEqual('verification_claimed', (await self.store.get(ACTOR, self.op)).state)

    async def test_revocation_after_verification_denies_consumption(self):
        await self.start()
        self.provider.observe_hook = lambda: setattr(self.store, 'allowed', False)
        with self.assertRaises(RecoveryProofRejected): await self.verify()
        self.assertEqual('verification_claimed', (await self.store.get(ACTOR, self.op)).state)

    async def test_unknown_purpose_refused_before_provider_call(self):
        with self.assertRaises(RecoveryProofRejected):
            await self.coordinator.start(ACTOR, self.op, FACTOR, 'remove_factor')
        self.assertEqual([], self.provider.calls)


if __name__ == '__main__':
    unittest.main()
