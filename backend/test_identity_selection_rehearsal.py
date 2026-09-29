"""Disposable SQLite fault rehearsal; no installed selectors or user data."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

import admission_operations as ops
import identity_selection_journal as journal
from identity_selection_rehearsal import Rehearsal
import test_identity_selection_journal as journal_tests


class InjectedInterruption(RuntimeError): pass


class IdentitySelectionRehearsalTests(unittest.TestCase):
    def setUp(self):
        fixture=journal_tests.IdentitySelectionJournalTests()
        fixture.setUp()
        self.record=fixture.record; self.prior=fixture.prior
        self.rehearsal=Rehearsal(self.record,self.prior)
        self.addCleanup(self.rehearsal.close)

    def latest(self): return self.rehearsal.history()[-1]['journal_sha256']

    def publish(self,host='web',**kwargs):
        return self.rehearsal.publish(host,expected_sha256=kwargs.pop('expected_sha256',self.latest()),**kwargs)

    @staticmethod
    def interrupt_at(wanted):
        def fault(stage):
            if stage==wanted: raise InjectedInterruption(wanted)
        return fault

    def assert_consumers_disabled(self):
        with self.assertRaisesRegex(PermissionError,'^Rehearsal consumers remain disabled\\.$'):
            self.rehearsal.consume()
        for record in self.rehearsal.history():
            self.assertIs(record['consumers_enabled'],False)
            self.assertIs(record['execution_authorized'],False)

    def assert_replay_denied(self,host='web'):
        history=self.rehearsal.history(); pointer=self.rehearsal.read(host)
        for digest in (self.record['journal_sha256'],history[-1]['journal_sha256']):
            with self.assertRaises(ValueError): self.publish(host,expected_sha256=digest)
            self.assertEqual(history,self.rehearsal.history())
            self.assertEqual(pointer,self.rehearsal.read(host))

    def test_before_claim_commit_rolls_back_and_unclaimed_attempt_can_proceed(self):
        with self.assertRaises(InjectedInterruption):
            self.publish(fault=self.interrupt_at('before_claim_commit'))
        self.assertEqual([self.record],self.rehearsal.history())
        for host in journal.HOSTS:
            self.assertEqual((self.prior[host],self.record['journal_sha256']),self.rehearsal.read(host))
        self.assertEqual('prepared_blocked',self.rehearsal.assess())
        self.assert_consumers_disabled()
        self.assertEqual('synthetic_pointer_readback_only',self.publish())

    def test_after_claim_commit_is_durable_and_never_replayed(self):
        with self.assertRaises(InjectedInterruption):
            self.publish(fault=self.interrupt_at('after_claim_commit'))
        history=self.rehearsal.history()
        self.assertEqual(2,len(history))
        self.assertEqual('publication_claimed',history[-1]['hosts']['web']['state'])
        self.assertEqual(self.prior['web'],self.rehearsal.read('web')[0])
        self.assert_replay_denied()
        for host in journal.HOSTS: self.rehearsal.deliver(host)
        self.assertEqual('not_selected_blocked_no_replay',self.rehearsal.assess())
        self.assert_consumers_disabled()

    def test_before_pointer_commit_rolls_back_pointer_but_preserves_claim(self):
        with self.assertRaises(InjectedInterruption):
            self.publish(fault=self.interrupt_at('before_pointer_commit'))
        self.assertEqual(2,len(self.rehearsal.history()))
        self.assertEqual((self.prior['web'],self.record['journal_sha256']),self.rehearsal.read('web'))
        self.assert_replay_denied()
        self.assert_consumers_disabled()

    def test_after_pointer_commit_lost_ack_readback_is_durable_without_replay(self):
        with self.assertRaises(InjectedInterruption):
            self.publish(fault=self.interrupt_at('after_pointer_commit'))
        raw,seen=self.rehearsal.read('web')
        latest=self.rehearsal.history()[-1]
        self.assertEqual(latest['hosts']['web']['target_sha256'],journal.sha(raw))
        self.assertEqual(latest['journal_sha256'],seen)
        self.assert_replay_denied()
        self.rehearsal.deliver('database')
        self.assertEqual('partial_selection_blocked',self.rehearsal.assess())
        self.assert_consumers_disabled()

    def test_both_hosts_and_delivery_converge_without_enabling_consumers(self):
        self.assert_consumers_disabled()
        self.publish('web')
        self.assertEqual('journal_disagreement_blocked',self.rehearsal.assess())
        self.rehearsal.deliver('database')
        self.assertEqual('partial_selection_blocked',self.rehearsal.assess())
        self.assert_consumers_disabled()
        self.publish('database')
        self.assertEqual('journal_disagreement_blocked',self.rehearsal.assess())
        pointers={host:self.rehearsal.read(host)[0] for host in journal.HOSTS}
        for host in journal.HOSTS: self.rehearsal.deliver(host)
        self.assertEqual('converged_requires_independent_acceptance',self.rehearsal.assess())
        for host in journal.HOSTS:
            self.assertEqual(pointers[host],self.rehearsal.read(host)[0])
            self.assert_replay_denied(host)
        self.assert_consumers_disabled()

    def test_concurrent_same_host_has_exactly_one_claim_and_publication(self):
        barrier=threading.Barrier(2); lock=threading.Lock(); publications=[]
        expected=self.latest()
        def fault(stage):
            if stage=='after_pointer_commit':
                with lock: publications.append(stage)
        def attempt():
            barrier.wait(timeout=5)
            try: return self.rehearsal.publish('web',expected_sha256=expected,fault=fault)
            except ValueError: return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(attempt) for _ in range(2)]
            outcomes=[future.result(timeout=10) for future in futures]
        self.assertCountEqual(['synthetic_pointer_readback_only','rejected'],outcomes)
        self.assertEqual(['after_pointer_commit'],publications)
        history=self.rehearsal.history()
        self.assertEqual(2,len(history))
        self.assertEqual(history[-1]['hosts']['web']['target_sha256'],journal.sha(self.rehearsal.read('web')[0]))
        self.assert_replay_denied()

    def test_stale_expected_cas_does_not_claim_other_host(self):
        self.publish('web'); before=self.rehearsal.history()
        with self.assertRaises(ValueError):
            self.publish('database',expected_sha256=self.record['journal_sha256'])
        self.assertEqual(before,self.rehearsal.history())
        self.assertEqual(self.prior['database'],self.rehearsal.read('database')[0])
        self.assertEqual('prepared',before[-1]['hosts']['database']['state'])

    def test_pointer_conflict_preserves_foreign_pointer_and_consumes_claim(self):
        old=ops.strict_json(self.prior['web'])
        conflicting=ops.encoded({**old,'sequence':old['sequence']+2})
        with self.rehearsal.connect('web') as db:
            db.execute('UPDATE selection SET pointer=? WHERE id=1',(conflicting,))
        with self.assertRaises(ValueError): self.publish('web')
        self.assertEqual(2,len(self.rehearsal.history()))
        self.assertEqual(conflicting,self.rehearsal.read('web')[0])
        self.assert_replay_denied()
        for host in journal.HOSTS: self.rehearsal.deliver(host)
        self.assertEqual('selection_conflict_blocked',self.rehearsal.assess())

    def test_foreign_seen_journal_cannot_be_delivered_over(self):
        with self.rehearsal.connect('web') as db:
            db.execute('UPDATE selection SET seen=? WHERE id=1',('0'*64,))
        before=self.rehearsal.read('web')
        with self.assertRaises(ValueError): self.rehearsal.deliver('web')
        self.assertEqual(before,self.rehearsal.read('web'))
        self.assertEqual([self.record],self.rehearsal.history())

    def test_foreign_seen_refuses_publication_with_claim_retained(self):
        with self.rehearsal.connect('web') as db:
            db.execute('UPDATE selection SET seen=? WHERE id=1',('0'*64,))
        before=self.rehearsal.read('web')
        with self.assertRaises(ValueError): self.publish('web')
        self.assertEqual(before,self.rehearsal.read('web'))
        self.assertEqual(2,len(self.rehearsal.history()))
        self.assert_replay_denied()

    def test_delayed_publisher_cannot_regress_delivered_journal(self):
        def pause_web(stage):
            if stage=='after_claim_commit':
                self.publish('database')
                self.rehearsal.deliver('web')
                self.assertEqual(2,self.rehearsal.history()[-1]['revision'])
                self.assertEqual(self.latest(),self.rehearsal.read('web')[1])
        self.publish('web',fault=pause_web)
        for host in journal.HOSTS: self.assertEqual(self.latest(),self.rehearsal.read(host)[1])
        self.assertEqual('converged_requires_independent_acceptance',self.rehearsal.assess())
        self.assert_consumers_disabled()

    def test_context_cleanup_removes_only_owned_disposable_directory(self):
        with Rehearsal(self.record,self.prior) as disposable:
            root=disposable.root
            self.assertTrue(root.is_dir())
            self.assertEqual({'journal.sqlite','web.sqlite','database.sqlite'},
                             {path.name for path in root.iterdir()})
            self.assertEqual([self.record],disposable.history())
        self.assertFalse(root.exists())
        self.assertTrue(self.rehearsal.root.exists())
        self.assertEqual([self.record],self.rehearsal.history())


if __name__=='__main__': unittest.main()
