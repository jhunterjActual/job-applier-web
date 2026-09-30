"""Synthetic credential bridge tests: no live credentials or provider actions."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import patch, MagicMock
from uuid import uuid4

import owner_recovery as recovery
import owner_recovery_bridge as bridge


def view():
    return {'binding': 'a' * 64, 'selection': 'b' * 64, 'schema': '20260915_0016',
        'context': {'operation': str(uuid4()), 'account_id': 7, 'incarnation': str(uuid4()),
        'epoch': 2, 'old_binding': 'c' * 64, 'old_subject': 'user_synthetic_source',
        'target_binding': 'd' * 64, 'target_subject': 'user_synthetic_target',
        'expected_role': 'owner', 'owner_authorized': True, 'predecessor': None,
        'started': datetime.fromtimestamp(1005, timezone.utc).isoformat(),
        'deadline': datetime.fromtimestamp(1400, timezone.utc).isoformat()}}


class InstalledSourceTests(unittest.TestCase):
    def test_0021_receipt_exact_context_and_credential_binding(self):
        observed=view(); observed['schema']='20260929_0021'
        credential={'id':'c'*64,'binding':observed['binding'],'created':1000,'confirmed':1001}
        receipt={'operation':observed['context']['operation'],'scope':recovery.TRANSITION_SCOPE,
            'binding':credential['binding'],'issued':1010,'expires':1100,
            'context':observed['context'],'selection':observed['selection'],
            'schema':observed['schema'],'credential':credential['id']}
        self.assertEqual(receipt,recovery.transition_receipt(receipt,credential))
        for changes in ({'schema':'20260929_0020'},{'schema':'20260930_0022'},
                {'selection':'invalid'},{'credential':'d'*64},{'operation':str(uuid4())},
                {'expires':1401}):
            with self.assertRaises(ValueError):
                recovery.transition_receipt({**receipt,**changes},credential)

    def test_mismatched_installed_source_refuses_before_database_or_input(self):
        pair={'provider': {'operations': 'a'*40}, 'activation': {'operations': 'a'*40}}
        with patch.object(bridge.ops,'host_locks',side_effect=nullcontext), \
                patch.object(bridge.plans,'load',return_value=(b'selection',pair)), \
                patch.object(bridge.operator,'secure_connection') as connection:
            with self.assertRaises(ValueError):
                with bridge.live_view(MagicMock(),str(uuid4()),source='b'*40):
                    self.fail('mismatched source accepted')
            connection.assert_not_called()


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'requires isolated Linux root fixture')
class BridgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ct-bridge-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.store = recovery.Store(Path(self.temp.name))
        self.view = view()
        with self.store.locked():
            self.code = self.store.issue(self.view['binding'], 1000)
            self.store.authenticate(self.code, self.view['binding'], 1001, confirm=True)

    def test_consumption_is_atomic_with_exact_context_receipt(self):
        with self.store.locked():
            receipt = bridge.consume(self.store, self.code, self.view, 1006)
            self.assertEqual(receipt, bridge.verify(self.store, self.view, 1007))
            self.assertEqual(receipt, recovery.Store(self.store.root).read()['receipt'])
            self.assertEqual(2, self.store.read()['version'])
            with self.assertRaises(ValueError):
                bridge.consume(self.store, self.code, self.view, 1008)
            with self.assertRaises(ValueError):
                self.store.receipt(self.view['binding'], self.view['context']['operation'], 1008)

    def test_every_context_and_selection_change_rejects_receipt(self):
        with self.store.locked():
            bridge.consume(self.store, self.code, self.view, 1006)
            mutations = {'operation': str(uuid4()), 'incarnation': str(uuid4()), 'account_id': 8,
                'epoch': 3, 'old_binding': 'e' * 64, 'old_subject': 'user_other_source',
                'target_binding': 'f' * 64, 'target_subject': 'user_other_target',
                'predecessor': str(uuid4()), 'owner_authorized': False, 'expected_role': 'user',
                'started': datetime.fromtimestamp(1004, timezone.utc).isoformat(),
                'deadline': datetime.fromtimestamp(1399, timezone.utc).isoformat()}
            for key, val in mutations.items():
                changed = deepcopy(self.view); changed['context'][key] = val
                with self.subTest(key=key), self.assertRaises(ValueError):
                    bridge.verify(self.store, changed, 1007)
            for key, val in (('binding', 'e' * 64), ('selection', 'f' * 64), ('schema', '20260909_0015')):
                with self.assertRaises(ValueError):
                    bridge.verify(self.store, {**self.view, key: val}, 1007)

    def test_proof_expiry_capped_by_operation_and_not_extended_on_retry(self):
        self.view['context']['deadline'] = datetime.fromtimestamp(1100, timezone.utc).isoformat()
        with self.store.locked():
            receipt = bridge.consume(self.store, self.code, self.view, 1006)
            self.assertEqual(1100, receipt['expires'])
            self.assertEqual(receipt, bridge.verify(self.store, self.view, 1099))
            for now in (1005, 1100, 1400):
                with self.assertRaises(ValueError):
                    bridge.verify(self.store, self.view, now)
            self.assertEqual('consumed', self.store.read()['state'])

    def test_credential_must_predate_transition_and_source_binding_match(self):
        original = self.store.path.read_bytes()
        bad = deepcopy(self.view)
        bad['context']['started'] = datetime.fromtimestamp(999, timezone.utc).isoformat()
        for changed in (bad, {**self.view, 'binding': 'e' * 64}):
            with self.store.locked(), self.assertRaises(ValueError):
                bridge.consume(self.store, self.code, changed, 1006)
            self.assertEqual(original, self.store.path.read_bytes())

    def test_wrong_and_malformed_attempts_persist_and_lock_out(self):
        with self.store.locked():
            for n in range(5):
                with self.assertRaises(ValueError):
                    bridge.consume(self.store, 'invalid-code', self.view, 1006 + n * 30)
                self.assertEqual(n + 1, recovery.Store(self.store.root).read()['attempts'])
            with self.assertRaises(ValueError):
                bridge.consume(self.store, self.code, self.view, 1190)
            self.assertEqual('active', self.store.read()['state'])

    def test_failed_consumption_write_keeps_charged_attempt(self):
        original = self.store.write
        def write(value):
            if value['state'] == 'consumed':
                raise OSError('synthetic-write-failure')
            original(value)
        with self.store.locked(), patch.object(self.store, 'write', side_effect=write):
            with self.assertRaises(OSError):
                bridge.consume(self.store, self.code, self.view, 1006)
        self.assertEqual('active', self.store.read()['state'])
        self.assertEqual(1, self.store.read()['attempts'])

    def test_lost_consumption_ack_is_retrievable_without_second_effect(self):
        original = self.store.write
        def write(value):
            original(value)
            if value['state'] == 'consumed':
                raise OSError('synthetic-lost-ack')
        with self.store.locked(), patch.object(self.store, 'write', side_effect=write):
            with self.assertRaises(OSError):
                bridge.consume(self.store, self.code, self.view, 1006)
        with self.store.locked():
            self.assertEqual(self.view['context'], bridge.verify(self.store, self.view, 1007)['context'])

    def test_changed_state_during_private_input_does_not_consume(self):
        calls = [self.view, {**self.view, 'selection': 'f' * 64}]
        @contextmanager
        def live(*_, **kwargs):
            yield calls.pop(0)
        terminal = MagicMock(); terminal.ask.side_effect = ['OWNER-TRANSITION', self.code]
        with patch.object(bridge, 'live_view', live), self.assertRaises(ValueError):
            bridge.run('consume', self.view['context']['operation'], object(), terminal,
                       store=self.store, now=lambda: 1006)
        self.assertEqual('active', self.store.read()['state'])
        self.assertEqual(0, self.store.read()['attempts'])
        terminal.show.assert_not_called()

    def test_operator_summary_never_contains_secret_or_identity(self):
        @contextmanager
        def live(*_, **kwargs):
            yield self.view
        terminal = MagicMock(); terminal.ask.side_effect = ['OWNER-TRANSITION', self.code]
        with patch.object(bridge, 'live_view', live):
            bridge.run('consume', self.view['context']['operation'], object(), terminal,
                       store=self.store, now=lambda: 1006)
        summary = terminal.show.call_args.args[0]
        for private in (self.code, self.view['context']['old_subject'], self.view['context']['target_subject']):
            self.assertNotIn(private, summary)
        self.assertIn('1970-01-01T00:16:46+00:00', summary)

    def test_web_manifest_preserves_consumption_and_caps_expiry(self):
        import identity_evidence
        with self.store.locked():
            bridge.consume(self.store, self.code, self.view, 1006)
        @contextmanager
        def live(*_, **kwargs):
            yield deepcopy(self.view)
        path, journal = self.store.root / 'manifest.json', self.store.root / 'journal.json'
        terminal = MagicMock(); terminal.ask.side_effect = ReceiptEvidenceBuilderTests.answers(self)
        with patch.object(bridge, 'live_view', live), patch.object(bridge.operator, 'locations', return_value=(path, journal)):
            result = bridge.enter(self.view['context']['operation'], object(), terminal, store=self.store, now=lambda: 1010)
            self.assertEqual('web_manifest_saved_not_transferred_or_attested', result['outcome'])
            with self.assertRaises(ValueError):
                bridge.enter(self.view['context']['operation'], object(), terminal, store=self.store, now=lambda: 1010)
        packet = identity_evidence.validate(path.read_bytes())
        self.assertEqual(1306, datetime.fromisoformat(packet['expires_at']).timestamp())
        self.assertEqual('consumed', self.store.read()['state'])
        self.assertNotIn(self.code.encode(), path.read_bytes())
        self.assertFalse(journal.exists())

    def test_manifest_expiry_during_input_leaves_no_file_and_keeps_consumed(self):
        with self.store.locked():
            bridge.consume(self.store, self.code, self.view, 1006)
        clock = [1010]
        @contextmanager
        def live(*_, **kwargs):
            yield deepcopy(self.view)
        path, journal = self.store.root / 'manifest.json', self.store.root / 'journal.json'
        answers = iter(ReceiptEvidenceBuilderTests.answers(self))
        def ask(_):
            answer = next(answers)
            if answer == 'SAVE':
                clock[0] = 1306
            return answer
        terminal = MagicMock(); terminal.ask.side_effect = ask
        with patch.object(bridge, 'live_view', live), patch.object(bridge.operator, 'locations', return_value=(path, journal)):
            with self.assertRaises(ValueError):
                bridge.enter(self.view['context']['operation'], object(), terminal, store=self.store, now=lambda: clock[0])
        self.assertFalse(path.exists())
        self.assertEqual('consumed', self.store.read()['state'])


class SourceDigestTests(unittest.TestCase):
    def test_exact_legacy_preimage_and_recreated_account_detection(self):
        row = {'id': 7, 'created_at': datetime(2026, 9, 1, tzinfo=timezone.utc)}
        database = ['127.0.0.1', 5432, 'synthetic', '1234']
        expected = {'host': recovery.HOST, 'database': database, 'client': 'client_synthetic',
            'subject': 'user_synthetic', 'account': 7, 'created': row['created_at'].isoformat(),
            'role': 'owner', 'schema': '20260907_0014'}
        import hashlib
        original = bridge.source_digest(database, row, 'client_synthetic', 'user_synthetic')
        self.assertEqual(hashlib.sha256(recovery.encoded(expected)).hexdigest(), original)
        for db, selected, client, subject in (
            (database[:-1] + ['1235'], row, 'client_synthetic', 'user_synthetic'),
            (database, {**row, 'id': 8}, 'client_synthetic', 'user_synthetic'),
            (database, {**row, 'created_at': datetime(2026, 9, 2, tzinfo=timezone.utc)}, 'client_synthetic', 'user_synthetic'),
            (database, row, 'client_other', 'user_synthetic'),
            (database, row, 'client_synthetic', 'user_other')):
            self.assertNotEqual(original, bridge.source_digest(db, selected, client, subject))


class ReceiptEvidenceBuilderTests(unittest.TestCase):
    def setUp(self):
        self.view = view()
        self.receipt = {'scope': recovery.TRANSITION_SCOPE, 'schema': self.view['schema'],
                       'context': self.view['context'], 'issued': 1006, 'expires': 1100}

    def answers(self, revoked=1009):
        c = self.view['context']
        iso = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        return ['VISIBLE'] + [str(c[k]) for k in ('account_id','incarnation','epoch','old_binding',
            'old_subject','target_binding','target_subject')] + ['AUTHENTICATED',iso(1008),'google',
            'EMAIL-VERIFIED','none_enrolled','REVOKED',iso(revoked),'OWNER-AUTHORIZED',iso(1009),'SAVE']

    def build(self, verifier=None, answers=None, now=1010):
        import identity_entry
        values = iter(self.answers() if answers is None else answers)
        return identity_entry.build_with_verified_holder_receipt(self.view['context'], lambda _: next(values),
            verify_holder=verifier or (lambda: self.receipt),
            now=lambda: datetime.fromtimestamp(now, timezone.utc))

    def test_holder_time_fixed_and_packet_expiry_capped(self):
        import identity_evidence
        packet = identity_evidence.validate(self.build())
        self.assertEqual(datetime.fromtimestamp(1006, timezone.utc).isoformat(), packet['holder']['completed_at'])
        self.assertEqual(datetime.fromtimestamp(1100, timezone.utc).isoformat(), packet['expires_at'])
        self.assertEqual(2, packet['version'])  # Operator evidence, not machine-authenticated receipt transfer.

    def test_expired_or_changed_receipt_refuses_after_human_input(self):
        import admission_operations
        changed = {**self.receipt, 'expires': 1099}
        receipts = iter([self.receipt, changed])
        with self.assertRaises(admission_operations.OperationError):
            self.build(verifier=lambda: next(receipts))
        with self.assertRaises(admission_operations.OperationError):
            self.build(now=1100)

    def test_wrong_context_and_revocation_before_holder_refuse(self):
        import admission_operations
        import identity_evidence
        wrong = {**self.receipt, 'context': {**self.receipt['context'], 'epoch': 99}}
        with self.assertRaises((admission_operations.OperationError, identity_evidence.EvidenceRejected)):
            self.build(verifier=lambda: wrong)
        with self.assertRaises(identity_evidence.EvidenceRejected):
            self.build(answers=self.answers(revoked=1005))


if __name__ == '__main__':
    unittest.main()
