"""Synthetic attended PKCE tests; no browser, tokens or provider networking."""
import asyncio
import base64
import copy
import hashlib
import json
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from recovery_attended_auth import AttendedRecoveryAuthentication
from recovery_factor_proof import RecoveryProofUncertain
from recovery_workflow import utc
from workos_transport_candidate import ProviderSettings, VerifiedTokens


class AttendedAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = ProviderSettings('client_fixture123', 'synthetic-secret',
            'https://api.workos.com/user_management/client_fixture123', 'synthetic-generation')
        self.context = dict(operation=str(uuid4()), account_id=1, incarnation=str(uuid4()),
            epoch=2, binding=self.settings.binding, subject='user_fixture123',
            deadline=utc(1500), state='held', plan_digest='a' * 64,
            effects=[{'state': 'succeeded'}])
        self.events = []
        self.clock = 1000
        self.exchange_error = self.claim_error = False
        self.change_after_exchange = False

        async def context(operation):
            self.assertEqual(operation, self.context['operation'])
            self.events.append('context')
            return copy.deepcopy(self.context)

        async def claim(scope, state_digest):
            self.events.append('claim')
            self.assertEqual(scope['incarnation'], self.context['incarnation'])
            self.assertEqual(len(state_digest), 64)
            if self.claim_error:
                raise OSError('private claim diagnostic')
            return True

        async def exchange(code, verifier):
            self.events.append('exchange')
            self.assertEqual(code, 'synthetic-code')
            self.assertIn('claim', self.events)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            self.assertEqual(challenge, self.query['code_challenge'][0])
            if self.exchange_error:
                raise OSError('private exchange diagnostic')
            if self.change_after_exchange:
                self.context['epoch'] += 1
            return VerifiedTokens('user_fixture123', 'session_fixture123', 'synthetic-generation',
                                  'synthetic-access', 'synthetic-refresh', 1400, self.settings.binding)

        async def release(operation, tokens):
            self.events.append('release')
            return {'state': 'committed', 'epoch': 2}

        self.auth = AttendedRecoveryAuthentication(SimpleNamespace(context=context),
            SimpleNamespace(settings=self.settings, exchange=exchange),
            SimpleNamespace(release=release), claim, now=lambda: self.clock)

    def begin(self):
        url = self.auth.begin(self.context, 'https://app.example.test/auth/callback', self.settings)
        self.query = parse_qs(urlsplit(url).query)
        return url

    def response(self):
        return json.dumps({'code': 'synthetic-code', 'state': self.query['state'][0]})

    async def test_correlated_pkce_claim_before_exchange_and_release(self):
        url = self.begin()
        self.assertEqual(urlsplit(url).netloc, 'api.workos.com')
        self.assertEqual(len(self.query['state'][0]), 43)
        self.assertTrue(self.query['state'][0].startswith('r1_'))
        self.assertEqual(self.query['prompt'], ['login'])
        self.assertEqual(self.query['max_age'], ['0'])
        self.assertEqual(self.query['code_challenge_method'], ['S256'])
        self.assertEqual(await self.auth.finish(self.response()), {'state': 'committed', 'epoch': 2})
        self.assertEqual(self.events, ['context', 'claim', 'context', 'exchange', 'context', 'release'])
        self.assertIsNone(self.auth._verifier)
        with self.assertRaises(RecoveryProofUncertain):
            await self.auth.finish(self.response())

    async def test_duplicate_json_member_and_wrong_state_never_dispatch(self):
        for response in ('{"code":"a","code":"b","state":"x"}',
                         '{"code":"synthetic-code","state":"wrong"}',
                         '{"code":"a","state":"x","extra":true}', 'x' * 4097):
            self.setUp()
            self.begin()
            with self.assertRaises(RecoveryProofUncertain):
                await self.auth.finish(response)
            self.assertEqual(self.events, [])
            self.assertIsNone(self.auth._verifier)

    async def test_changed_scope_before_claim_denied(self):
        self.begin()
        self.context['incarnation'] = str(uuid4())
        with self.assertRaises(RecoveryProofUncertain):
            await self.auth.finish(self.response())
        self.assertNotIn('claim', self.events)

    async def test_changed_scope_after_exchange_denies_release(self):
        self.begin()
        self.change_after_exchange = True
        with self.assertRaises(RecoveryProofUncertain):
            await self.auth.finish(self.response())
        self.assertNotIn('release', self.events)

    async def test_lost_claim_or_exchange_never_retried_and_sanitized(self):
        for attribute in ('claim_error', 'exchange_error'):
            self.setUp()
            self.begin()
            setattr(self, attribute, True)
            with self.assertRaises(RecoveryProofUncertain) as error:
                await self.auth.finish(self.response())
            self.assertNotIn('private', str(error.exception))
            count = self.events.count('exchange')
            with self.assertRaises(RecoveryProofUncertain):
                await self.auth.finish(self.response())
            self.assertEqual(self.events.count('exchange'), count)
            self.assertIsNone(self.auth._verifier)

    async def test_ten_minute_bound_even_when_hold_lasts_longer(self):
        self.context['deadline'] = utc(1800)
        self.begin()
        self.clock = 1600
        with self.assertRaises(RecoveryProofUncertain):
            await self.auth.finish(self.response())
        self.assertNotIn('claim', self.events)

    async def test_browser_handoff_can_take_more_than_five_minutes(self):
        self.context['deadline'] = utc(1800)
        self.begin()
        self.clock = 1350
        self.assertEqual(await self.auth.finish(self.response()), {'state': 'committed', 'epoch': 2})

    async def test_concurrent_finish_dispatches_once(self):
        self.begin()
        results = await asyncio.gather(self.auth.finish(self.response()),
            self.auth.finish(self.response()), return_exceptions=True)
        self.assertEqual(self.events.count('exchange'), 1)
        self.assertEqual(sum(isinstance(r, RecoveryProofUncertain) for r in results), 1)

    async def test_cancellation_after_claim_clears_verifier_without_retry(self):
        self.begin()

        async def cancelled(code, verifier):
            self.events.append('exchange')
            raise asyncio.CancelledError()

        self.auth.authentication.exchange = cancelled
        with self.assertRaises(asyncio.CancelledError):
            await self.auth.finish(self.response())
        self.assertIn('claim', self.events)
        self.assertIsNone(self.auth._verifier)
        with self.assertRaises(RecoveryProofUncertain):
            await self.auth.finish(self.response())
        self.assertEqual(self.events.count('exchange'), 1)

    async def test_hold_deadline_shortens_authentication_window(self):
        self.context['deadline'] = utc(1020)
        self.begin()
        self.clock = 1020
        with self.assertRaises(RecoveryProofUncertain):
            await self.auth.finish(self.response())
        self.assertNotIn('claim', self.events)

    async def test_insecure_or_altered_callback_rejected(self):
        for url in ('http://app.example.test/auth/callback',
                    'https://app.example.test/other', 'https://user@app.example.test/auth/callback',
                    'https://app.example.test/auth/callback?x=1'):
            self.setUp()
            with self.assertRaises(RecoveryProofUncertain):
                self.auth.begin(self.context, url, self.settings)
            self.assertIsNone(self.auth._verifier)


if __name__ == '__main__':
    unittest.main()
