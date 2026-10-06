"""Synthetic attended continuation; no terminal, credentials or provider calls."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import recovery_attended_entry as entry


OP = '12345678-1234-4234-8234-123456789abc'
SCOPE = dict(operation=OP, account_id=1, incarnation=OP, epoch=2,
             binding='b' * 64, subject='user_synthetic')


class EntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepared_wrong_code_stops_without_new_operation(self):
        terminal = SimpleNamespace(ask=Mock(side_effect=[
            'VISIBLE', OP, 'PREPARED', 'HOLD', 'synthetic-wrong-code']), fd=1)
        recovery = SimpleNamespace(hold=object(), workflow=SimpleNamespace(contain_provider=AsyncMock()))
        with patch.object(entry, 'require_unclaimed'), patch.object(entry.os, 'write'), \
                patch.object(entry.held, 'load_request', return_value={'recovery_operation': OP}), \
                patch.object(entry.held, 'dispatch_claimed', return_value=False), \
                patch.object(entry.held, 'consume_or_reconcile', new=AsyncMock(
                    return_value={'outcome': 'code_not_accepted'})) as consume:
            with self.assertRaises(entry.RecoveryProofUncertain):
                await entry.continue_held(terminal, recovery)
            consume.assert_awaited_once_with(recovery.hold, OP, 'synthetic-wrong-code')
        recovery.workflow.contain_provider.assert_not_awaited()

    async def test_prepared_claim_reconciles_without_asking_code(self):
        terminal = SimpleNamespace(ask=Mock(side_effect=[
            'VISIBLE', OP, 'PREPARED', 'HOLD']), fd=1)
        recovery = SimpleNamespace(hold=object())
        with patch.object(entry, 'require_unclaimed'), patch.object(entry.os, 'write'), \
                patch.object(entry.held, 'load_request', return_value={'recovery_operation': OP}), \
                patch.object(entry.held, 'dispatch_claimed', return_value=True), \
                patch.object(entry.held, 'consume_or_reconcile', new=AsyncMock(
                    side_effect=entry.RecoveryProofUncertain)) as consume:
            with self.assertRaises(entry.RecoveryProofUncertain):
                await entry.continue_held(terminal, recovery)
            consume.assert_awaited_once_with(recovery.hold, OP, None)
        self.assertEqual(4, terminal.ask.call_count)

    async def test_claim_is_exclusive_and_retains_scope_only(self):
        saved = {}
        def write(path, raw):
            if path in saved:
                raise entry.RecoveryProofUncertain()
            saved[path] = raw
        with patch.object(entry.ops, 'write_protected', side_effect=write), \
                patch.object(entry.ops, 'read_protected', side_effect=lambda path: saved[path]):
            self.assertTrue(await entry.claim_exchange(SCOPE, 'a' * 64))
            with self.assertRaises(entry.RecoveryProofUncertain):
                await entry.claim_exchange(SCOPE, 'c' * 64)
        record = entry.ops.strict_json(next(iter(saved.values())))
        self.assertEqual({'version', 'phase', 'scope', 'state_digest'}, set(record))
        self.assertEqual(SCOPE, record['scope'])

    async def test_existing_claim_stops_before_provider_effects(self):
        terminal = SimpleNamespace(ask=Mock(side_effect=['VISIBLE', OP]), fd=1)
        recovery = SimpleNamespace(workflow=SimpleNamespace(contain_provider=AsyncMock()),
                                   store=SimpleNamespace(context=AsyncMock()))
        with patch.object(entry, 'require_unclaimed', side_effect=entry.RecoveryProofUncertain):
            with self.assertRaises(entry.RecoveryProofUncertain):
                await entry.continue_held(terminal, recovery)
        recovery.workflow.contain_provider.assert_not_awaited()
        recovery.store.context.assert_not_awaited()

    async def test_complete_path_and_private_response_lifecycle(self):
        terminal = SimpleNamespace(ask=Mock(side_effect=[
            'VISIBLE', OP, 'HELD', 'auth_factor_synthetic', 'CONTINUE', 'synthetic-response']), fd=1)
        settings = SimpleNamespace(client_id='client_synthetic', client_secret='synthetic-key')
        context = dict(SCOPE, state='held')
        recovery = SimpleNamespace(store=SimpleNamespace(context=AsyncMock(return_value=context)),
            workflow=SimpleNamespace(authentication=SimpleNamespace(settings=settings),
                contain_provider=AsyncMock(return_value={'outcome': 'provider_contained_account_held'})))
        flow = SimpleNamespace(begin=Mock(return_value='https://api.workos.com/synthetic'),
                               finish=AsyncMock(return_value={'state': 'committed', 'epoch': 2}), close=Mock())
        config = b'WORKOS_CLIENT_ID=client_synthetic\nWORKOS_API_KEY=synthetic-key\nWORKOS_REDIRECT_URI=https://example.test/auth/callback\n'
        with patch.object(entry, 'require_unclaimed'), patch.object(entry.os, 'write'), \
                patch.object(entry.ops, 'read_protected', return_value=config), \
                patch.object(entry, 'display_authorization') as display, \
                patch.object(entry, 'AttendedRecoveryAuthentication', return_value=flow):
            result = await entry.continue_held(terminal, recovery)
        self.assertEqual({'outcome': 'recovery_released_fresh_sign_in_required'}, result)
        recovery.workflow.contain_provider.assert_awaited_once_with(OP, 'auth_factor_synthetic')
        display.assert_called_once()
        flow.finish.assert_awaited_once_with('synthetic-response')
        flow.close.assert_called_once()
        self.assertEqual(4096, terminal.ask.call_args.kwargs['limit'])

    async def test_uncertain_containment_never_starts_fresh_auth(self):
        terminal = SimpleNamespace(ask=Mock(side_effect=[
            'VISIBLE', OP, 'HELD', 'auth_factor_synthetic', 'CONTINUE']), fd=1)
        recovery = SimpleNamespace(store=SimpleNamespace(context=AsyncMock(return_value=dict(SCOPE, state='held'))),
            workflow=SimpleNamespace(contain_provider=AsyncMock(side_effect=entry.RecoveryProofUncertain)))
        with patch.object(entry, 'require_unclaimed'), patch.object(entry.os, 'write'), \
                patch.object(entry, 'AttendedRecoveryAuthentication') as flow:
            with self.assertRaises(entry.RecoveryProofUncertain):
                await entry.continue_held(terminal, recovery)
            flow.assert_not_called()


if __name__ == '__main__':
    unittest.main()
