"""Temporary transactional SQLite contract; no real credentials or app data."""
from dataclasses import asdict, replace
import json
import hmac
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from recovery_credentials import (CredentialDisplay, ProposedCredentials,
    RecoveryCredentialCoordinator, credential_hash)
from recovery_factor_proof import RecoveryProofRejected, RecoveryProofUncertain
from session_authorization import SessionAuthorization
from totp_enrollment import EnrollmentActor
from test_recovery_factor_proof import ACTOR, FACTOR, Provider, Store, encode


def decode_pending(raw):
    value = json.loads(raw)
    value['actor']['authorization'] = SessionAuthorization(**value['actor']['authorization'])
    value['actor'] = EnrollmentActor(**value['actor'])
    value['hashes'] = tuple(value['hashes'])
    return ProposedCredentials(**value)


class CredentialStore(Store):
    def __init__(self, path, now):
        # Retain all operations; security_slots provides the separate current
        # owner instead of deleting completed proof evidence for replacement.
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS proof(operation TEXT PRIMARY KEY, account INTEGER, body TEXT);
            CREATE TABLE IF NOT EXISTS slots(account INTEGER PRIMARY KEY, operation TEXT);
            CREATE TABLE IF NOT EXISTS pending(operation TEXT PRIMARY KEY, body TEXT,
                predecessor TEXT, state TEXT, attempts INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS active(account INTEGER PRIMARY KEY, generation TEXT, body TEXT);
        ''')
        self.now, self.fail, self.allowed = now, None, True

    async def reserve(self, row):
        if not await self.eligible(row.actor): return False
        account = row.actor.authorization.local_id
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old = self.db.execute('SELECT operation FROM slots WHERE account=?', (account,)).fetchone()
            if old:
                state = self.db.execute('SELECT state FROM pending WHERE operation=?', old).fetchone()
                if state != ('active',): return False
            if self.db.execute('SELECT 1 FROM proof WHERE operation=?', (row.operation,)).fetchone(): return False
            active = self.db.execute('SELECT generation FROM active WHERE account=?', (account,)).fetchone()
            if (row.purpose == 'issue_recovery_credentials') != (active is None): return False
            self.db.execute('INSERT INTO proof VALUES(?,?,?)', (row.operation, account, encode(row)))
            self.db.execute('INSERT OR REPLACE INTO slots VALUES(?,?)', (account, row.operation))
        return True

    async def propose(self, old, new, pending, *, actor):
        if not await self.eligible(actor): return False
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.db.execute('SELECT body FROM proof WHERE operation=?', (old.operation,)).fetchone()
            slot = self.db.execute('SELECT operation FROM slots WHERE account=?', (actor.authorization.local_id,)).fetchone()
            if (current != (encode(old),) or slot != (old.operation,) or old.actor != actor
                    or old.state != 'verification_claimed'
                    or new != replace(old, state='consumed', verified_at=new.verified_at)
                    or not 0 <= self.now() - new.verified_at < 30 or self.now() >= old.deadline
                    or pending.actor != actor or pending.operation != old.operation
                    or pending.purpose != old.purpose or pending.deadline > old.deadline
                    or len(pending.hashes) != 8 or len(set(pending.hashes)) != 8): return False
            predecessor = self.db.execute('SELECT generation FROM active WHERE account=?', (actor.authorization.local_id,)).fetchone()
            if (old.purpose == 'issue_recovery_credentials') != (predecessor is None): return False
            self.db.execute('UPDATE proof SET body=? WHERE operation=?', (encode(new), old.operation))
            self.db.execute('INSERT INTO pending VALUES(?,?,?,?,0)', (pending.operation,
                json.dumps(asdict(pending), sort_keys=True), predecessor[0] if predecessor else None, 'pending'))
        if self.fail == 'propose': raise OSError()
        return True

    async def pending(self, actor, operation):
        row = self.db.execute('SELECT body FROM pending WHERE operation=? AND state=?', (operation, 'pending')).fetchone()
        return decode_pending(row[0]) if row else None

    async def confirm(self, pending, digest, *, actor):
        if not await self.eligible(actor): return False
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT body,predecessor,state,attempts FROM pending WHERE operation=?', (pending.operation,)).fetchone()
            active = self.db.execute('SELECT generation FROM active WHERE account=?', (actor.authorization.local_id,)).fetchone()
            if (not row or decode_pending(row[0]) != pending or row[2] != 'pending'
                    or actor != pending.actor or self.now() >= pending.deadline or row[3] >= 5
                    or row[1] != (active[0] if active else None)): return False
            self.db.execute('UPDATE pending SET attempts=attempts+1 WHERE operation=?', (pending.operation,))
            matches = [hmac.compare_digest(digest if type(digest) is str else '', stored)
                       for stored in pending.hashes]
            if not any(matches): return False
            if active:
                previous = self.db.execute('SELECT body FROM active WHERE account=?',
                    (actor.authorization.local_id,)).fetchone()
                previous_operation = decode_pending(previous[0]).operation
                self.db.execute("UPDATE pending SET state='revoked' WHERE operation=? AND state='active'",
                    (previous_operation,))
            self.db.execute('INSERT OR REPLACE INTO active VALUES(?,?,?)',
                (actor.authorization.local_id, pending.generation, row[0]))
            self.db.execute("UPDATE pending SET state='active' WHERE operation=?", (pending.operation,))
        if self.fail == 'confirm': raise OSError()
        return True


class RecoveryCredentialsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = self.temp.name + '/credentials.sqlite'
        self.time = 1000
        self.store = CredentialStore(self.path, lambda: self.time)
        self.provider = Provider()
        self.coordinator = RecoveryCredentialCoordinator(self.store, self.provider, now=lambda: self.time)
        self.operation = str(uuid4())
        self.purpose = 'issue_recovery_credentials'

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    async def issue(self):
        await self.coordinator.start(ACTOR, self.operation, FACTOR, self.purpose)
        return await self.coordinator.verify(ACTOR, self.operation, FACTOR, self.purpose, '123456')

    async def confirm(self, code, **changes):
        values = dict(actor=ACTOR, operation=self.operation, purpose=self.purpose, code=code)
        values.update(changes)
        return await self.coordinator.confirm_custody(**values)

    async def test_codes_are_one_time_display_hashes_only_and_custody_activates(self):
        display = await self.issue()
        self.assertIsInstance(display, CredentialDisplay)
        self.assertEqual(8, len(set(display.codes)))
        dump = '\n'.join(self.store.db.iterdump())
        for code in display.codes:
            self.assertEqual(43, len(code))
            self.assertNotIn(code, dump)
            self.assertNotIn(code, repr(display))
        self.assertEqual([], self.store.db.execute('SELECT * FROM active').fetchall())
        self.assertEqual('credentials_activated', await self.confirm(display.codes[0]))
        with self.assertRaises(RecoveryProofRejected):
            await self.coordinator.verify(ACTOR, self.operation, FACTOR, self.purpose, '123456')
        with self.assertRaises(RecoveryProofRejected): await self.confirm(display.codes[0])

    async def test_replacement_keeps_previous_until_confirm_then_revokes(self):
        old = await self.issue()
        await self.confirm(old.codes[0])
        prior = self.store.db.execute('SELECT generation FROM active').fetchone()
        sentinel_operation = str(uuid4())
        sentinel_actor = replace(ACTOR, authorization=replace(ACTOR.authorization, local_id=2))
        sentinel = ProposedCredentials(sentinel_operation, sentinel_actor, self.purpose,
            str(uuid4()), ('f' * 64,) * 8, 1300)
        sentinel_body = json.dumps(asdict(sentinel), sort_keys=True)
        with self.store.db:
            self.store.db.execute('INSERT INTO pending VALUES(?,?,?,?,0)',
                (sentinel_operation, sentinel_body, None, 'active'))
            self.store.db.execute('INSERT INTO active VALUES(?,?,?)', (2, sentinel.generation, sentinel_body))
        self.operation, self.purpose = str(uuid4()), 'replace_recovery_credentials'
        new = await self.issue()
        self.assertEqual(prior, self.store.db.execute('SELECT generation FROM active WHERE account=1').fetchone())
        with self.assertRaises(RecoveryProofRejected): await self.confirm(old.codes[0])
        await self.confirm(new.codes[0])
        self.assertNotEqual(prior, self.store.db.execute('SELECT generation FROM active WHERE account=1').fetchone())
        self.assertEqual(('active',), self.store.db.execute('SELECT state FROM pending WHERE operation=?',
            (sentinel_operation,)).fetchone())
        self.assertEqual((sentinel.generation, sentinel_body), self.store.db.execute(
            'SELECT generation,body FROM active WHERE account=2').fetchone())
        self.assertEqual(1, self.store.db.execute("SELECT count(*) FROM pending WHERE state='revoked'").fetchone()[0])
        self.assertEqual(2, self.store.db.execute('SELECT count(*) FROM proof').fetchone()[0])

    async def test_lost_proposal_ack_never_redisplays_or_duplicates_after_reopen(self):
        self.store.fail = 'propose'
        with self.assertRaises(RecoveryProofUncertain): await self.issue()
        self.store.db.close()
        self.store = CredentialStore(self.path, lambda: self.time)
        self.coordinator.store = self.store
        with self.assertRaises(RecoveryProofRejected):
            await self.coordinator.verify(ACTOR, self.operation, FACTOR, self.purpose, '123456')
        self.operation = str(uuid4())
        with self.assertRaises(RecoveryProofRejected): await self.issue()
        self.assertEqual(1, self.store.db.execute('SELECT count(*) FROM pending').fetchone()[0])
        self.assertEqual(1, self.provider.calls.count('verify'))

    async def test_lost_activation_ack_is_not_replayed(self):
        display = await self.issue()
        self.store.fail = 'confirm'
        with self.assertRaises(RecoveryProofUncertain): await self.confirm(display.codes[0])
        with self.assertRaises(RecoveryProofRejected): await self.confirm(display.codes[0])
        self.assertEqual(1, self.store.db.execute('SELECT count(*) FROM active').fetchone()[0])

    async def test_custody_substitution_and_fake_proof_denied(self):
        display = await self.issue()
        for change in ({'purpose': 'replace_recovery_credentials'}, {'operation': str(uuid4())},
                {'actor': replace(ACTOR, browser='f' * 64)}, {'code': 'proof_consumed'}, {'code': True}):
            with self.subTest(fields=list(change)):
                values = dict(change)
                code = values.pop('code', display.codes[0])
                with self.assertRaises(RecoveryProofRejected): await self.confirm(code, **values)
        self.assertEqual([], self.store.db.execute('SELECT * FROM active').fetchall())

    async def test_expired_unconfirmed_set_does_not_activate(self):
        display = await self.issue()
        self.time = 1300
        with self.assertRaises(RecoveryProofRejected): await self.confirm(display.codes[0])
        self.assertEqual([], self.store.db.execute('SELECT * FROM active').fetchall())

    async def test_custody_attempts_are_bounded(self):
        display = await self.issue()
        for _ in range(5):
            with self.assertRaises(RecoveryProofRejected): await self.confirm('x' * 43)
        with self.assertRaises(RecoveryProofRejected): await self.confirm(display.codes[0])

    async def test_malformed_custody_attempts_are_charged(self):
        display = await self.issue()
        for code in ('', True, 'proof_consumed', 'x' * 44, 'a b'):
            with self.assertRaises(RecoveryProofRejected): await self.confirm(code)
        self.assertEqual(5, self.store.db.execute('SELECT attempts FROM pending').fetchone()[0])
        with self.assertRaises(RecoveryProofRejected): await self.confirm(display.codes[0])

    async def test_hash_binding_includes_exact_actor_operation_and_purpose(self):
        display = await self.issue()
        pending = await self.store.pending(ACTOR, self.operation)
        original = credential_hash(ACTOR, self.operation, self.purpose, pending.generation, display.codes[0])
        for actor, operation, purpose in ((replace(ACTOR, browser='e' * 64), self.operation, self.purpose),
                (ACTOR, str(uuid4()), self.purpose), (ACTOR, self.operation, 'replace_recovery_credentials')):
            self.assertNotEqual(original, credential_hash(actor, operation, purpose, pending.generation, display.codes[0]))


if __name__ == '__main__': unittest.main()
