"""Synthetic, disconnected identity transition rehearsal; no application DB."""
from dataclasses import replace
import sqlite3
import tempfile
import unittest
from pathlib import Path

from identity_transition_candidate import (
    Evidence, Grant, Identity, IdentityLedger, TransitionRejected, initialize_empty_fixture,
)


OLD = Identity('a'*64, 'user_synthetic_old')
NEW = Identity('b'*64, 'user_synthetic_new')
OP = '00000000-0000-4000-8000-000000000001'
OP2 = '00000000-0000-4000-8000-000000000002'


class IdentityTransitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'fixture.sqlite'
        self.db = self.connect()
        initialize_empty_fixture(self.db)
        self.db.execute("INSERT INTO candidate_accounts(id,role,epoch) VALUES(1,'user',1)")
        self.db.execute("INSERT INTO candidate_identities VALUES(?,?,1,'active')",(OLD.binding,OLD.subject))
        self.db.execute("INSERT INTO candidate_owned_data VALUES(1,'synthetic-profile')")
        self.db.commit()
        self.ledger = IdentityLedger(self.db)
        self.grant = self.ledger.capture(1,OLD)

    def connect(self):
        connection = sqlite3.connect(self.path)
        self.addCleanup(connection.close)
        return connection

    def prepare(self, target=NEW, **kw):
        return self.ledger.prepare(OP,self.grant,target,now=100,deadline=500,**kw)

    def proof(self, target=NEW, **kw):
        return replace(Evidence(OP,1,OLD,target,2,110,400,True,True,True,True),**kw)

    def complete(self, target=NEW):
        self.prepare(target)
        self.ledger.attest(self.proof(target),now=120)
        return self.ledger.activate(OP,now=130)

    def test_preserves_account_profile_role_and_account_count(self):
        self.assertEqual(self.complete(),{'state':'committed','epoch':2})
        fresh = self.ledger.capture(1,NEW)
        self.assertEqual(self.ledger.check(fresh)['role'],'user')
        self.assertEqual(self.db.execute('SELECT count(*) FROM candidate_accounts').fetchone()[0],1)
        self.assertEqual(tuple(self.db.execute('SELECT * FROM candidate_owned_data').fetchone()),(1,'synthetic-profile'))
        with self.assertRaises(TransitionRejected):
            self.ledger.capture(1,OLD)

    def test_hold_and_same_identity_recovery_permanently_deny_old_grant(self):
        self.prepare(OLD)
        with self.assertRaises(TransitionRejected):
            self.ledger.protected_write(self.grant,'stale-request')
        self.ledger.attest(self.proof(OLD),now=120)
        self.ledger.activate(OP,now=130)
        with self.assertRaises(TransitionRejected):
            self.ledger.check(self.grant)
        self.ledger.protected_write(self.ledger.capture(1,OLD),'fresh-request')

    def test_exact_replay_survives_reopen_and_never_clears_later_hold(self):
        self.complete()
        reopened = IdentityLedger(self.connect())
        fresh = reopened.capture(1,NEW)
        reopened.prepare(OP2,fresh,NEW,now=200,deadline=600)
        self.assertEqual(reopened.prepare(OP,self.grant,NEW,now=200,deadline=500),'committed')
        self.assertEqual(reopened.activate(OP,now=210)['epoch'],2)
        self.assertEqual(self.db.execute('SELECT held,epoch FROM candidate_accounts').fetchone()[:],(1,3))
        with self.assertRaises(TransitionRejected):
            reopened.prepare(OP,self.grant,NEW,now=200,deadline=501)

    def test_conflicting_target_rolls_back_without_holding_account(self):
        self.db.execute("INSERT INTO candidate_accounts(id,role,epoch) VALUES(2,'user',1)")
        self.db.execute("INSERT INTO candidate_identities VALUES(?,?,2,'active')",(NEW.binding,NEW.subject))
        self.db.commit()
        with self.assertRaises(TransitionRejected): self.prepare()
        self.ledger.check(self.grant)
        self.assertEqual(self.db.execute('SELECT count(*) FROM candidate_transitions').fetchone()[0],0)

    def test_missing_proof_and_expiry_leave_hold(self):
        self.prepare()
        for field in ('holder_verified','target_authenticated','factors_enrolled','provider_sessions_revoked'):
            with self.subTest(field=field), self.assertRaises(TransitionRejected):
                self.ledger.attest(self.proof(**{field:False}),now=120)
        with self.assertRaises(TransitionRejected): self.ledger.activate(OP,now=130)
        self.ledger.attest(self.proof(),now=120)
        with self.assertRaises(TransitionRejected): self.ledger.activate(OP,now=400)
        with self.assertRaises(TransitionRejected): self.ledger.capture(1,OLD)

    def test_proof_cannot_be_rebound_or_backdated(self):
        self.prepare()
        for change in ({'local_id':True},{'epoch':True},{'old':NEW},{'target':OLD},
                       {'operation':OP2},{'verified_at':99},{'expires':501}):
            with self.subTest(change=change), self.assertRaises(TransitionRejected):
                self.ledger.attest(self.proof(**change),now=120)
        self.ledger.attest(self.proof(),now=120)
        with self.assertRaises(TransitionRejected): self.ledger.activate(OP,now=109)

    def test_disabled_or_erasing_account_cannot_be_reenabled(self):
        self.prepare()
        self.ledger.attest(self.proof(),now=120)
        for field in ('disabled','erasing'):
            self.db.execute('UPDATE candidate_accounts SET disabled=0,erasing=0')
            self.db.execute('UPDATE candidate_accounts SET '+field+'=1')
            self.db.commit()
            with self.assertRaises(TransitionRejected): self.ledger.activate(OP,now=130)
        self.assertEqual(self.db.execute('SELECT held FROM candidate_accounts').fetchone()[0],1)

    def test_owner_requires_separate_authorization_at_both_steps(self):
        self.db.execute("UPDATE candidate_accounts SET role='owner'")
        self.db.commit()
        with self.assertRaises(TransitionRejected): self.prepare()
        self.prepare(owner_authorized=True)
        with self.assertRaises(TransitionRejected): self.ledger.attest(self.proof(),now=120)
        self.ledger.attest(self.proof(owner_authorized=True),now=120)
        self.ledger.activate(OP,now=130)
        self.assertEqual(self.ledger.check(self.ledger.capture(1,NEW))['role'],'owner')

    def test_role_drift_rejected_at_attestation_and_activation(self):
        self.prepare()
        self.db.execute("UPDATE candidate_accounts SET role='owner'")
        self.db.commit()
        with self.assertRaises(TransitionRejected):
            self.ledger.attest(self.proof(owner_authorized=True),now=120)
        self.db.execute("UPDATE candidate_accounts SET role='user'")
        self.db.commit()
        self.ledger.attest(self.proof(),now=120)
        self.db.execute("UPDATE candidate_accounts SET role='owner'")
        self.db.commit()
        with self.assertRaises(TransitionRejected): self.ledger.activate(OP,now=130)

    def test_separate_connection_stale_request_cannot_write_after_hold(self):
        other = IdentityLedger(self.connect())
        stale = other.capture(1,OLD)
        self.prepare()
        with self.assertRaises(TransitionRejected): other.protected_write(stale,'must-not-commit')
        self.assertEqual(self.db.execute('SELECT count(*) FROM candidate_owned_data').fetchone()[0],1)

    def test_failed_activation_rolls_back_retirement(self):
        self.prepare()
        self.ledger.attest(self.proof(),now=120)
        self.db.execute("DELETE FROM candidate_identities WHERE state='pending'")
        self.db.commit()
        with self.assertRaises(TransitionRejected): self.ledger.activate(OP,now=130)
        self.assertEqual(self.db.execute('SELECT state FROM candidate_identities').fetchone()[0],'active')
        self.assertEqual(self.db.execute('SELECT held FROM candidate_accounts').fetchone()[0],1)

    def test_wrong_environment_and_malformed_requests_fail_closed(self):
        with self.assertRaises(TransitionRejected): self.ledger.capture(1,Identity('c'*64,OLD.subject))
        with self.assertRaises(TransitionRejected):
            self.ledger.prepare('malformed',self.grant,NEW,now=100,deadline=500)
        with self.assertRaises(TransitionRejected): self.ledger.check(Grant(1,None,1))
        with self.assertRaises(TransitionRejected): initialize_empty_fixture(self.db)


if __name__ == '__main__':
    unittest.main()
