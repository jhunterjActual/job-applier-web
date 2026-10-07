"""Synthetic removal protocol against temporary durable SQLite, never app data.

The fixture implements the proposed transactional contract; it is not a
production removal store and cannot prove PostgreSQL grants or provider behavior.
"""
import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from factor_removal import (FactorRemovalCoordinator, FactorObservation,
    RemovalChallenge, RemovalDeletion, RemovalRecord, RemovalRejected,
    RemovalUncertain, RemovalVerification)
from session_authorization import SessionAuthorization
from totp_enrollment import EnrollmentActor


ACTOR = EnrollmentActor(SessionAuthorization(1, 'user_removal_fixture', 'a' * 64, 1,
    '00000000-0000-4000-8000-000000000001'), 'session_removal_fixture', 'b' * 64, 1000)
FACTOR = 'auth_factor_removal_fixture'
CHALLENGE = 'auth_challenge_removal_fixture'


def encoded(value):
    return json.dumps(asdict(value), sort_keys=True, separators=(',', ':'), allow_nan=False)


def decoded(raw):
    value = json.loads(raw)
    actor = value.pop('actor')
    actor['authorization'] = SessionAuthorization(**actor['authorization'])
    return RemovalRecord(actor=EnrollmentActor(**actor), **value)


class DurableRemovalFixture:
    def __init__(self, path, now):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=1)
        self.now = now
        self.fail_before = self.fail_after = self.reject_cas = None
        self.after_commit = None
        self.eligible_calls = 0
        self.eligible_hook = None
        self.db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS authority(account_id INTEGER PRIMARY KEY,
                binding TEXT NOT NULL, enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(operation TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL UNIQUE, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS security_slots(account_id INTEGER PRIMARY KEY,
                operation TEXT NOT NULL UNIQUE REFERENCES operations(operation), binding TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revoked_sessions(session_id TEXT PRIMARY KEY);
        ''')
        self.db.execute('INSERT OR IGNORE INTO authority VALUES(?,?,1)',
            (ACTOR.authorization.local_id, encoded(ACTOR.authorization)))

    def allowed(self, actor):
        row = self.db.execute('SELECT binding,enabled FROM authority WHERE account_id=?',
            (actor.authorization.local_id,)).fetchone()
        return (row == (encoded(actor.authorization), 1)
            and 0 <= self.now() - actor.authenticated_at < 300
            and self.db.execute('SELECT 1 FROM revoked_sessions WHERE session_id=?',
                (actor.session_id,)).fetchone() is None)

    def read(self, operation):
        row = self.db.execute('SELECT body FROM operations WHERE operation=?', (operation,)).fetchone()
        return decoded(row[0]) if row else None

    async def eligible(self, actor):
        result = self.allowed(actor)
        self.eligible_calls += 1
        if self.eligible_hook:
            await self.eligible_hook(self.eligible_calls)
        return result

    async def get(self, actor, operation):
        row = self.read(operation)
        return row if self.allowed(actor) and row and row.actor.authorization == actor.authorization else None

    def finish(self, event):
        self.db.commit()
        if self.after_commit:
            self.after_commit(event)
        if self.fail_after == event:
            raise OSError('Synthetic lost COMMIT acknowledgment')

    async def reserve(self, row):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if (not self.allowed(row.actor) or row.state != 'challenge_claimed'
                    or not self.now() < row.deadline <= self.now() + 300
                    or self.db.execute('SELECT 1 FROM operations WHERE operation=? OR account_id=?',
                        (row.operation, row.actor.authorization.local_id)).fetchone()):
                self.db.rollback()
                return False
            self.db.execute('INSERT INTO operations VALUES(?,?,?)',
                (row.operation, row.actor.authorization.local_id, encoded(row)))
            self.db.execute('INSERT INTO security_slots VALUES(?,?,?)',
                (row.actor.authorization.local_id, row.operation, encoded(row.actor.authorization)))
            if self.fail_before == 'reserve':
                raise OSError('Synthetic pre-COMMIT failure')
            self.finish('reserve')
            return True
        except BaseException:
            self.db.rollback()
            raise

    def transition(self, old, new, actor):
        if (old.operation != new.operation or old.actor != new.actor or old.factor != new.factor
                or actor.authorization != old.actor.authorization):
            return False
        # Only read-only completion can use another fresh session/browser.
        if new.state != 'complete' and actor != old.actor:
            return False
        if old.state == 'challenge_claimed' and new.state == 'challenge_ready':
            return (self.now() < new.deadline <= old.deadline and new.attempts == 0
                and new == replace(old, state='challenge_ready', challenge=new.challenge, deadline=new.deadline))
        if old.state == 'challenge_ready' and new.state == 'verification_claimed':
            return (self.now() < old.deadline and old.attempts < 5
                and new == replace(old, state='verification_claimed', attempts=old.attempts + 1))
        if old.state == 'verification_claimed' and new.state == 'challenge_ready':
            return self.now() < old.deadline and new == replace(old, state='challenge_ready')
        if old.state == 'verification_claimed' and new.state == 'delete_claimed':
            return (self.now() < old.deadline and 0 <= self.now() - new.verified_at < 30
                and new == replace(old, state='delete_claimed', verified_at=new.verified_at))
        if old.state == 'delete_claimed' and new.state == 'delete_recorded':
            return new == replace(old, state='delete_recorded', delete_acknowledged=True)
        if old.state == 'delete_recorded' and new.state == 'complete':
            return new == replace(old, state='complete')
        return False

    async def cas(self, old, new, *, actor):
        event = new.state
        self.db.execute('BEGIN IMMEDIATE')
        try:
            slot = self.db.execute('SELECT operation,binding FROM security_slots WHERE account_id=?',
                (old.actor.authorization.local_id,)).fetchone()
            if (not self.allowed(actor) or self.read(old.operation) != old
                    or slot != (old.operation, encoded(old.actor.authorization))
                    or not self.transition(old, new, actor) or self.reject_cas == event):
                self.db.rollback()
                return False
            self.db.execute('UPDATE operations SET body=? WHERE operation=?', (encoded(new), old.operation))
            if self.fail_before == event:
                raise OSError('Synthetic pre-COMMIT failure')
            self.finish(event)
            return True
        except BaseException:
            self.db.rollback()
            raise


class ProviderFixture:
    def __init__(self, observer, now):
        self.observer, self.now = observer, now
        self.operation = None
        self.calls = []
        self.deleted = False
        self.valid = True
        self.hooks = {}
        self.overrides = {}

    async def effect(self, name, expected):
        # A different SQLite connection must see the committed claim before I/O.
        row = self.observer.read(self.operation)
        if row is None or row.state != expected:
            raise AssertionError('Provider effect preceded a committed exact claim')
        self.calls.append(name)
        await asyncio.sleep(0)
        if name in self.hooks:
            await self.hooks[name]()

    async def observe(self, subject, factor):
        self.calls.append('observe')
        if 'observe' in self.hooks:
            await self.hooks['observe']()
        return self.overrides.get('observe', FactorObservation(ACTOR.authorization.provider_binding,
            subject, factor, None if self.deleted else 'totp', 'absent' if self.deleted else 'present', True))

    async def challenge(self, subject, factor):
        await self.effect('challenge', 'challenge_claimed')
        return self.overrides.get('challenge', RemovalChallenge(ACTOR.authorization.provider_binding,
            subject, factor, CHALLENGE, int(self.now()) + 240))

    async def verify(self, subject, factor, challenge, code):
        await self.effect('verify', 'verification_claimed')
        return self.overrides.get('verify', RemovalVerification(ACTOR.authorization.provider_binding,
            subject, factor, challenge, self.valid))

    async def delete(self, subject, factor, operation):
        await self.effect('delete', 'delete_claimed')
        self.deleted = True
        return self.overrides.get('delete', RemovalDeletion(ACTOR.authorization.provider_binding,
            subject, factor, operation, 'deleted'))


class FactorRemovalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ct-factor-removal-')
        self.path = Path(self.temp.name) / 'fixture.sqlite'
        self.clock = 1000
        self.store = DurableRemovalFixture(self.path, lambda: self.clock)
        self.other = DurableRemovalFixture(self.path, lambda: self.clock)
        self.provider = ProviderFixture(self.other, lambda: self.clock)
        self.coordinator = FactorRemovalCoordinator(self.store, self.provider, now=lambda: self.clock)
        self.operation = str(uuid4())
        self.provider.operation = self.operation

    def tearDown(self):
        self.store.db.close()
        self.other.db.close()
        self.temp.cleanup()

    def reopen(self):
        self.store.db.close()
        self.store = DurableRemovalFixture(self.path, lambda: self.clock)
        self.coordinator = FactorRemovalCoordinator(self.store, self.provider, now=lambda: self.clock)

    def mutations(self):
        return [name for name in self.provider.calls if name != 'observe']

    async def start(self):
        return await self.coordinator.start(ACTOR, self.operation, FACTOR)

    async def test_success_claims_precede_effects_and_completion_retains_slot(self):
        await self.start()
        self.assertEqual('complete', await self.coordinator.verify(ACTOR, self.operation, '123456'))
        self.assertEqual(['challenge', 'verify', 'delete'], self.mutations())
        self.reopen()
        self.assertEqual('complete', await self.coordinator.reconcile(ACTOR, self.operation))
        self.assertEqual(1, self.store.db.execute('SELECT count(*) FROM security_slots').fetchone()[0])
        with self.assertRaises(RemovalRejected):
            await self.coordinator.start(ACTOR, str(uuid4()), FACTOR)
        self.assertEqual(['challenge', 'verify', 'delete'], self.mutations())

    async def test_duplicate_uuid_changed_factor_or_actor_never_dispatches_again(self):
        await self.start()
        for actor, factor in ((ACTOR, FACTOR), (ACTOR, 'auth_factor_other_fixture'),
                (replace(ACTOR, browser='c' * 64), FACTOR),
                (replace(ACTOR, session_id='session_other_fixture'), FACTOR)):
            with self.subTest(actor=actor.session_id, factor=factor):
                with self.assertRaises(RemovalRejected):
                    await self.coordinator.start(actor, self.operation, factor)
        self.assertEqual(['challenge'], self.mutations())

    async def test_wrong_binding_and_changed_session_browser_reject_verification(self):
        await self.start()
        alternatives = [replace(ACTOR, browser='c' * 64), replace(ACTOR, session_id='session_other_fixture')]
        for change in ({'epoch': 2}, {'local_id': 2}, {'subject': 'user_other_fixture'},
                {'provider_binding': 'c' * 64}, {'incarnation': str(uuid4())}):
            alternatives.append(replace(ACTOR, authorization=replace(ACTOR.authorization, **change)))
        for actor in alternatives:
            with self.subTest(actor=actor):
                with self.assertRaises(RemovalRejected):
                    await self.coordinator.verify(actor, self.operation, '123456')
        self.assertEqual(['challenge'], self.mutations())

    async def test_explicit_invalid_budget_survives_reconnect(self):
        await self.start()
        self.provider.valid = False
        for attempt in range(5):
            self.assertEqual('code_not_accepted', await self.coordinator.verify(ACTOR, self.operation, '000000'))
            self.reopen()
            self.assertEqual(attempt + 1, self.store.read(self.operation).attempts)
        with self.assertRaises(RemovalRejected):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertEqual(5, self.mutations().count('verify'))
        self.assertNotIn('delete', self.mutations())

    async def test_malformed_code_does_not_claim_verification(self):
        await self.start()
        for code in ('12345', '1234567', '123\n456', 123456, None):
            with self.assertRaises(RemovalRejected):
                await self.coordinator.verify(ACTOR, self.operation, code)
        self.assertEqual(0, self.store.read(self.operation).attempts)

    async def test_partial_or_mismatched_ownership_cannot_create_challenge(self):
        for change in ({'complete': False}, {'provider_binding': 'c' * 64},
                {'subject': 'user_other_fixture'}, {'factor': 'auth_factor_other_fixture'}):
            self.provider.overrides['observe'] = replace(FactorObservation('a' * 64,
                ACTOR.authorization.subject, FACTOR, 'totp', 'present', True), **change)
            with self.assertRaises(RemovalUncertain): await self.start()
        self.assertEqual([], self.mutations())
        self.assertIsNone(self.store.read(self.operation))

    async def test_pending_factor_cannot_start_normal_removal(self):
        self.provider.overrides['observe'] = FactorObservation('a' * 64,
            ACTOR.authorization.subject, FACTOR, 'totp', 'pending', True)
        with self.assertRaises(RemovalRejected): await self.start()
        self.assertEqual([], self.mutations())

    async def test_mismatched_challenge_keeps_uncertainty_across_restart(self):
        self.provider.overrides['challenge'] = RemovalChallenge('a' * 64,
            ACTOR.authorization.subject, 'auth_factor_other_fixture', CHALLENGE, 1200)
        with self.assertRaises(RemovalUncertain): await self.start()
        self.reopen()
        with self.assertRaises(RemovalRejected): await self.start()
        self.assertEqual('unresolved', await self.coordinator.reconcile(ACTOR, self.operation))
        self.assertEqual(['challenge'], self.mutations())

    async def test_mismatched_verification_never_retries_or_deletes(self):
        await self.start()
        self.provider.overrides['verify'] = RemovalVerification('a' * 64,
            ACTOR.authorization.subject, FACTOR, 'auth_challenge_other_fixture', True)
        with self.assertRaises(RemovalUncertain):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.reopen()
        with self.assertRaises(RemovalRejected):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertEqual(['challenge', 'verify'], self.mutations())

    async def test_nonboolean_verification_is_uncertain(self):
        await self.start()
        self.provider.overrides['verify'] = RemovalVerification('a' * 64,
            ACTOR.authorization.subject, FACTOR, CHALLENGE, 1)
        with self.assertRaises(RemovalUncertain):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertEqual('verification_claimed', self.store.read(self.operation).state)
        self.assertNotIn('delete', self.mutations())

    async def test_wrong_delete_receipt_and_absence_do_not_prove_completion(self):
        await self.start()
        self.provider.overrides['delete'] = RemovalDeletion('a' * 64,
            ACTOR.authorization.subject, FACTOR, str(uuid4()), 'deleted')
        with self.assertRaises(RemovalUncertain):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertTrue(self.provider.deleted)
        self.reopen()
        self.assertEqual('unresolved', await self.coordinator.reconcile(ACTOR, self.operation))
        with self.assertRaises(RemovalRejected):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertEqual(1, self.mutations().count('delete'))

    async def test_positive_receipt_requires_complete_absence_then_fresh_inspection(self):
        await self.start()
        async def after_delete():
            self.provider.overrides['observe'] = FactorObservation('a' * 64,
                ACTOR.authorization.subject, FACTOR, None, 'absent', False)
        self.provider.hooks['delete'] = after_delete
        with self.assertRaises(RemovalUncertain):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertEqual('delete_recorded', self.store.read(self.operation).state)
        self.provider.overrides.pop('observe')
        self.reopen()
        fresh = replace(ACTOR, session_id='session_fresh_fixture', browser='d' * 64)
        self.assertEqual('complete', await self.coordinator.reconcile(fresh, self.operation))
        self.assertEqual(ACTOR, self.store.read(self.operation).actor)
        self.assertEqual(1, self.mutations().count('delete'))

    async def test_reserve_failed_commit_never_dispatches(self):
        self.store.fail_before = 'reserve'
        with self.assertRaises(RemovalUncertain): await self.start()
        self.assertIsNone(self.other.read(self.operation))
        self.assertEqual([], self.mutations())

    async def test_reserve_lost_commit_ack_never_dispatches_after_restart(self):
        self.store.fail_after = 'reserve'
        with self.assertRaises(RemovalUncertain): await self.start()
        self.assertEqual('challenge_claimed', self.other.read(self.operation).state)
        self.reopen()
        with self.assertRaises(RemovalRejected): await self.start()
        self.assertEqual('unresolved', await self.coordinator.reconcile(ACTOR, self.operation))
        self.assertEqual([], self.mutations())

    async def test_delete_claim_lost_ack_never_dispatches_or_replays(self):
        await self.start()
        self.store.fail_after = 'delete_claimed'
        with self.assertRaises(RemovalUncertain):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertEqual('delete_claimed', self.other.read(self.operation).state)
        self.reopen()
        with self.assertRaises(RemovalRejected):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertEqual('unresolved', await self.coordinator.reconcile(ACTOR, self.operation))
        self.assertNotIn('delete', self.mutations())

    async def test_lost_delete_receipt_commit_ack_reconciles_without_second_delete(self):
        await self.start()
        self.store.fail_after = 'delete_recorded'
        with self.assertRaises(RemovalUncertain):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.reopen()
        self.assertEqual('complete', await self.coordinator.reconcile(ACTOR, self.operation))
        self.assertEqual(1, self.mutations().count('delete'))

    async def test_delete_receipt_commit_failure_retains_unattributed_uncertainty(self):
        await self.start()
        self.store.fail_before = 'delete_recorded'
        with self.assertRaises(RemovalUncertain):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.reopen()
        self.assertEqual('unresolved', await self.coordinator.reconcile(ACTOR, self.operation))
        self.assertEqual('delete_claimed', self.store.read(self.operation).state)
        self.assertEqual(1, self.mutations().count('delete'))

    async def test_cas_loss_before_deletion_never_dispatches(self):
        await self.start()
        self.store.reject_cas = 'delete_claimed'
        with self.assertRaises(RemovalUncertain):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertEqual('verification_claimed', self.store.read(self.operation).state)
        self.assertNotIn('delete', self.mutations())

    async def test_cancellation_after_delete_claim_preserves_no_retry_state(self):
        await self.start()
        async def cancel(): raise asyncio.CancelledError()
        self.provider.hooks['delete'] = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.reopen()
        self.assertEqual('delete_claimed', self.store.read(self.operation).state)
        self.assertEqual('unresolved', await self.coordinator.reconcile(ACTOR, self.operation))
        with self.assertRaises(RemovalRejected):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertEqual(1, self.mutations().count('delete'))

    async def test_proof_expiry_during_ownership_read_prevents_delete(self):
        await self.start()
        async def delay(): self.clock += 30
        self.provider.hooks['observe'] = delay
        with self.assertRaises(RemovalRejected):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertNotIn('delete', self.mutations())

    async def test_proof_expiry_after_delete_claim_prevents_dispatch_and_retry(self):
        await self.start()
        def delay(event):
            if event == 'delete_claimed': self.clock += 30
        self.store.after_commit = delay
        with self.assertRaises(RemovalRejected):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertEqual('delete_claimed', self.other.read(self.operation).state)
        self.reopen()
        with self.assertRaises(RemovalRejected):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertNotIn('delete', self.mutations())

    async def test_authority_revoked_while_verification_waits_prevents_delete(self):
        await self.start()
        async def revoke(): self.other.db.execute('UPDATE authority SET enabled=0')
        self.provider.hooks['verify'] = revoke
        with self.assertRaises(RemovalRejected):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertNotIn('delete', self.mutations())

    async def test_session_revoked_after_delete_claim_prevents_dispatch(self):
        await self.start()
        def revoke(event):
            if event == 'delete_claimed':
                self.other.db.execute('INSERT INTO revoked_sessions VALUES(?)', (ACTOR.session_id,))
        self.store.after_commit = revoke
        with self.assertRaises(RemovalRejected):
            await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertNotIn('delete', self.mutations())

    async def delayed_authority_check(self, call_number, forbidden):
        # Cross only signed freshness; challenge and 30-second proof stay live.
        self.clock = ACTOR.authenticated_at + 299
        async def delay(number):
            if number == call_number:
                self.clock = ACTOR.authenticated_at + 300
        self.store.eligible_hook = delay
        if forbidden == 'challenge':
            with self.assertRaises(RemovalRejected): await self.start()
        else:
            await self.start()
            with self.assertRaises(RemovalRejected):
                await self.coordinator.verify(ACTOR, self.operation, '123456')
        self.assertNotIn(forbidden, self.mutations())

    async def test_freshness_expires_during_store_check_before_challenge(self):
        await self.delayed_authority_check(2, 'challenge')

    async def test_freshness_expires_during_store_check_before_verification(self):
        await self.delayed_authority_check(4, 'verify')

    async def test_freshness_expires_during_store_check_before_deletion(self):
        await self.delayed_authority_check(6, 'delete')

    async def test_two_connections_have_one_challenge_and_one_delete(self):
        second = FactorRemovalCoordinator(self.other, self.provider, now=lambda: self.clock)
        results = await asyncio.gather(self.start(),
            second.start(ACTOR, self.operation, FACTOR), return_exceptions=True)
        self.assertEqual(1, sum(value == self.operation for value in results))
        self.assertEqual(1, sum(isinstance(value, RemovalRejected) for value in results))
        results = await asyncio.gather(self.coordinator.verify(ACTOR, self.operation, '123456'),
            second.verify(ACTOR, self.operation, '123456'), return_exceptions=True)
        self.assertEqual(1, results.count('complete'))
        self.assertEqual(1, sum(isinstance(value, RemovalRejected) for value in results))
        self.assertEqual(['challenge', 'verify', 'delete'], self.mutations())

    async def test_store_rejects_forged_binding_and_transition_without_changing_state(self):
        await self.start()
        old = self.store.read(self.operation)
        for new in (replace(old, factor='auth_factor_other_fixture'),
                replace(old, actor=replace(ACTOR, browser='c' * 64)),
                replace(old, attempts=4), replace(old, deadline=old.deadline + 100)):
            self.assertFalse(await self.store.cas(old, new, actor=ACTOR))
            self.assertEqual(old, self.other.read(self.operation))
        self.assertFalse(await self.store.cas(replace(old, attempts=1),
            replace(old, state='verification_claimed', attempts=2), actor=ACTOR))


if __name__ == '__main__':
    unittest.main()
