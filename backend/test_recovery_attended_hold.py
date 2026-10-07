"""Synthetic hold journal tests; no actual credentials/database/provider calls."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import recovery_attended_hold as hold
from recovery_factor_proof import RecoveryProofUncertain


class AttendedHoldTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.calls = []
        self.fail = self.cancel = False

        def write(path, raw):
            with path.open('xb') as target:
                target.write(raw)

        for replacement in (
            patch.object(hold.ops, 'STATE', self.state),
            patch.object(hold.ops, 'protected_parents', lambda path: None),
            patch.object(hold.ops, 'write_protected', write),
            patch.object(hold.ops, 'read_protected', lambda path: path.read_bytes())):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.operation = str(uuid4())
        self.request = dict(account_id=1, generation=str(uuid4()), recovery_operation=self.operation,
            target_binding='a' * 64, target_subject='user_synthetic_fixture',
            until_time=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            owner_authorized=False)

        async def consume(**kwargs):
            self.assertTrue(hold.dispatch_claimed(self.operation))
            self.assertEqual(kwargs['code'], 'synthetic-recovery-code')
            self.calls.append('consume')
            if self.cancel:
                raise asyncio.CancelledError()
            if self.fail:
                raise OSError('private result diagnostic')
            return {'outcome': 'account_held', 'operation': self.operation, 'epoch': 2}

        async def reconcile(**kwargs):
            self.assertNotIn('code', kwargs)
            self.assertEqual(kwargs['generation'], self.request['generation'])
            self.assertEqual(kwargs['until_time'], datetime.fromisoformat(self.request['until_time']))
            self.calls.append('reconcile')
            return {'outcome': 'hold_recorded', 'operation': self.operation, 'epoch': 2}

        self.store = SimpleNamespace(consume_and_hold=consume, reconcile_hold=reconcile)

    async def test_immutable_request_claim_before_hold_and_reconcile_after_restart(self):
        self.assertEqual(len(hold.save_request(self.request)), 64)
        self.assertEqual(hold.load_request(self.operation), self.request)
        self.assertFalse(hold.dispatch_claimed(self.operation))
        result = await hold.consume_or_reconcile(self.store, self.operation, 'synthetic-recovery-code')
        self.assertEqual(result['outcome'], 'account_held')
        result = await hold.consume_or_reconcile(self.store, self.operation)
        self.assertEqual(result['outcome'], 'hold_recorded')
        self.assertEqual(self.calls, ['consume', 'reconcile'])
        self.assertNotIn('synthetic-recovery-code', ''.join(p.read_text() for p in self.state.iterdir()))
        with self.assertRaises(RecoveryProofUncertain):
            hold.save_request(self.request)

    async def test_lost_mutation_response_reconciles_without_second_consume(self):
        hold.save_request(self.request)
        self.fail = True
        with self.assertRaises(RecoveryProofUncertain) as error:
            await hold.consume_or_reconcile(self.store, self.operation, 'synthetic-recovery-code')
        self.assertNotIn('private', str(error.exception))
        await hold.consume_or_reconcile(self.store, self.operation, 'ignored-new-code')
        self.assertEqual(self.calls, ['consume', 'reconcile'])

    async def test_cancellation_retains_dispatch_claim(self):
        hold.save_request(self.request)
        self.cancel = True
        with self.assertRaises(asyncio.CancelledError):
            await hold.consume_or_reconcile(self.store, self.operation, 'synthetic-recovery-code')
        self.assertTrue(hold.dispatch_claimed(self.operation))
        await hold.consume_or_reconcile(self.store, self.operation)
        self.assertEqual(self.calls, ['consume', 'reconcile'])

    async def test_changed_request_cannot_reuse_claim(self):
        hold.save_request(self.request)
        await hold.consume_or_reconcile(self.store, self.operation, 'synthetic-recovery-code')
        path, _ = hold.paths(self.operation)
        altered = {**self.request, 'account_id': 2}
        path.write_bytes(hold.ops.encoded({'version': 1, 'request': altered}))
        with self.assertRaises(RecoveryProofUncertain):
            await hold.consume_or_reconcile(self.store, self.operation)
        self.assertEqual(self.calls, ['consume'])

    async def test_failed_claim_write_never_dispatches(self):
        hold.save_request(self.request)
        with patch.object(hold.ops, 'write_protected', side_effect=OSError('private write failure')):
            with self.assertRaises(RecoveryProofUncertain):
                await hold.consume_or_reconcile(self.store, self.operation, 'synthetic-recovery-code')
        self.assertEqual(self.calls, [])

    async def test_expired_request_or_extra_secret_field_rejected(self):
        for changed in ({**self.request, 'code': 'never-persist'},
                        {**self.request, 'until_time': datetime.now(timezone.utc).isoformat()},
                        {**self.request, 'account_id': True}):
            with self.assertRaises(RecoveryProofUncertain):
                hold.save_request(changed)
        self.assertEqual(list(self.state.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
