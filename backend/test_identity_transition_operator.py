"""Synthetic operator request and lost-ack boundaries; no local app data."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import admission_operations as ops
import identity_transition_operator as target


class TransitionOperatorTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = b'{"version":1}\n'
        self.r = {'version': 1, 'action': 'prepare', 'selection': self.snapshot.decode(),
            'operation': '11111111-1111-4111-8111-111111111111', 'previous': None,
            'account_id': 1, 'incarnation': '22222222-2222-4222-8222-222222222222',
            'epoch': 1, 'old_binding': 'a'*64, 'old_subject': 'user_source1',
            'target_binding': 'b'*64, 'target_subject': 'user_target1',
            'deadline': '2026-09-10T12:00:00+00:00', 'owner_authorized': False}
        self.p = {'mode': 'switch_environment', 'operation': '33333333-3333-4333-8333-333333333333',
            'baseline_binding': 'a'*64, 'target_binding': 'b'*64, 'transitions': [self.r['operation']],
            'quarantined_accounts': [], 'owner_proof': 'c'*64}
        self.pair = {'provider': self.p, 'activation': {}}

    def test_strict_request(self):
        self.assertEqual(target.validate(ops.encoded(self.r)), self.r)
        for key, value in [('epoch', True), ('account_id', 0), ('previous', self.r['operation']),
                ('target_subject', 'email@example.invalid'), ('extra', True), ('owner_authorized', 1)]:
            with self.subTest(key=key):
                changed = {**self.r, key: value}
                with self.assertRaises(Exception): target.validate(ops.encoded(changed))

    def test_scope_rejects_binding_selection_and_unplanned_operation(self):
        target.scope(self.r, self.snapshot, self.pair)
        for key, value in [('selection', '{}'), ('target_binding', 'c'*64),
                ('operation', self.p['operation']), ('owner_authorized', True)]:
            with self.subTest(key=key), self.assertRaises(Exception):
                target.scope({**self.r, key: value}, self.snapshot, self.pair)

    def test_resume_new_uuid_outside_old_plan(self):
        r = {**self.r, 'action': 'resume', 'previous': self.r['operation'],
            'operation': '44444444-4444-4444-8444-444444444444'}
        target.scope(r, self.snapshot, self.pair)
        with self.assertRaises(Exception):
            target.scope({**r, 'previous': r['operation']}, self.snapshot, self.pair)

    def test_exact_historical_retry_requires_all_original_fields(self):
        value = {**self.r, 'epoch': 2, 'deadline': datetime(2026, 9, 10, 12, tzinfo=timezone.utc),
            'predecessor': None, 'state': 'held'}
        c = Mock()
        with patch.object(target, 'row', return_value=value):
            self.assertEqual(target.mutate(c, self.r, self.p), {'outcome': 'historical_request_recorded'})
            c.execute.assert_not_called()
            with self.assertRaises(Exception): target.mutate(c, {**self.r, 'epoch': 2}, self.p)

    def test_journal_written_before_mutation_and_retained_on_lost_ack(self):
        order = []
        path = Mock(); path.exists.return_value = False; path.is_symlink.return_value = False
        c = Mock()
        with patch.object(ops, 'host_locks', return_value=nullcontext()), \
                patch.object(target,'current_release'), \
                patch.object(target.store, 'require_current', side_effect=lambda _: order.append('selected')), \
                patch.object(ops, 'stopped_services'), patch.object(target.transition, 'migration_receipt'), \
                patch.object(target.operator, 'secure_connection', return_value=nullcontext(c)), \
                patch.object(target, 'guard', side_effect=lambda *_: order.append('guard')), \
                patch.object(target, 'location', return_value=path), \
                patch.object(ops, 'write_protected', side_effect=lambda *_: order.append('journal')), \
                patch.object(target, 'mutate', side_effect=RuntimeError('synthetic lost ack')):
            with self.assertRaises(RuntimeError): target.execute(self.r, self.snapshot, self.pair, Mock())
        self.assertEqual(order, ['selected', 'guard', 'journal'])
        path.unlink.assert_not_called()

    def test_stale_selection_never_enters_database(self):
        with patch.object(ops, 'host_locks', return_value=nullcontext()), \
                patch.object(target.store, 'require_current', side_effect=ops.OperationError(ops.ERROR)), \
                patch.object(target.operator, 'secure_connection') as connect:
            with self.assertRaises(Exception): target.execute(self.r, self.snapshot, self.pair, Mock())
            connect.assert_not_called()

    def test_saved_request_retry_does_not_reprompt_identity_or_change_deadline(self):
        path = Mock(); path.exists.return_value = True
        ask = Mock(side_effect=['VISIBLE', self.r['operation'], 'RETRY'])
        with patch.object(target, 'location', return_value=path), \
                patch.object(ops, 'read_protected', return_value=ops.encoded(self.r)):
            self.assertEqual(target.capture('prepare', self.snapshot, self.pair, ask), self.r)
        self.assertEqual(ask.call_count, 3)

    def test_failed_guard_prevents_journal(self):
        with patch.object(ops, 'host_locks', return_value=nullcontext()), \
                patch.object(target,'current_release'), \
                patch.object(target.store, 'require_current'), patch.object(ops, 'stopped_services'), \
                patch.object(target.transition, 'migration_receipt'), \
                patch.object(target.operator, 'secure_connection', return_value=nullcontext(Mock())), \
                patch.object(target, 'guard', side_effect=ops.OperationError(ops.ERROR)), \
                patch.object(ops, 'write_protected') as write:
            with self.assertRaises(Exception): target.execute(self.r, self.snapshot, self.pair, Mock())
            write.assert_not_called()


if __name__ == '__main__': unittest.main()
