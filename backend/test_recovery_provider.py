"""Synthetic transport checks; no real provider credentials or requests."""
import asyncio
import json
import unittest

import httpx

from recovery_provider import WorkOSRecoverySessions, SessionRevocationUncertain
from workos_transport_candidate import ProviderSettings

SUBJECT = 'user_synthetic_owner'
SESSION = 'session_synthetic_owner'
OP = '11111111-1111-4111-8111-111111111111'


def page(ids=(SESSION,), after=None):
    return {'object': 'list', 'data': [
        {'object': 'session', 'id': sid, 'user_id': SUBJECT, 'status': 'active'} for sid in ids],
        'list_metadata': {'after': after}}


def response(value=None, *, status=200, raw=None, headers=None):
    return httpx.Response(status, headers=headers or {'Content-Type': 'application/json'},
                          stream=httpx.ByteStream(raw if raw is not None else json.dumps(value).encode()))


class RecoveryProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests, self.responses = [], []
        async def handler(request):
            self.requests.append(request)
            result = self.responses.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        self.settings = ProviderSettings('client_synthetic', 'synthetic_key',
            'https://api.workos.com/user_management/client_synthetic', 'synthetic_generation')
        self.provider = WorkOSRecoverySessions(self.settings, transport=httpx.MockTransport(handler))

    async def asyncTearDown(self):
        await self.provider.aclose()

    async def test_complete_pagination_and_empty_observation(self):
        other = 'session_synthetic_second'
        self.responses = [response(page(after=SESSION)), response(page((other,))), response(page(()))]
        result = await self.provider.observe(SUBJECT)
        self.assertEqual(result.session_ids, (SESSION, other))
        self.assertEqual(result.provider_binding, self.settings.binding)
        self.assertTrue(result.complete)
        self.assertEqual(self.requests[1].url.params['after'], SESSION)
        self.assertEqual((await self.provider.observe(SUBJECT)).session_ids, ())

    async def test_revoke_is_one_bound_effect(self):
        self.responses = [response(page()), response({})]
        receipt = await self.provider.revoke(SUBJECT, SESSION, OP)
        self.assertEqual((receipt.subject, receipt.session_id, receipt.operation, receipt.status),
                         (SUBJECT, SESSION, OP, 'revoked'))
        self.assertEqual(receipt.provider_binding, self.settings.binding)
        self.assertEqual(self.requests[1].method, 'POST')
        self.assertEqual(self.requests[1].url.path, '/user_management/sessions/revoke')
        self.assertEqual(json.loads(self.requests[1].content), {'session_id': SESSION})
        self.assertEqual(self.requests[1].headers['Authorization'], 'Bearer synthetic_key')

    async def test_other_account_and_missing_owner_fail_before_write(self):
        for owner in ('user_synthetic_other', None):
            value = page(); value['data'][0]['user_id'] = owner
            self.responses = [response(value)]
            with self.assertRaises(SessionRevocationUncertain):
                await self.provider.revoke(SUBJECT, SESSION, OP)
        self.assertTrue(all(request.method == 'GET' for request in self.requests))

    async def test_missing_session_does_not_dispatch_or_claim_success(self):
        self.responses = [response(page(()))]
        with self.assertRaises(SessionRevocationUncertain):
            await self.provider.revoke(SUBJECT, SESSION, OP)
        self.assertEqual(len(self.requests), 1)

    async def test_duplicate_loop_and_unknown_status_fail_closed(self):
        unknown = page(); unknown['data'][0]['status'] = 'unknown'
        cases = [[response(page((SESSION, SESSION)))], [response(unknown)],
                 [response(page(after=SESSION)), response(page((), after=SESSION))]]
        for responses in cases:
            self.responses = responses
            with self.assertRaises(SessionRevocationUncertain):
                await self.provider.observe(SUBJECT)

    async def test_page_limit_is_uncertain_not_truncated_success(self):
        self.responses = [response(page((), after='session_cursor_' + str(i))) for i in range(10)]
        with self.assertRaises(SessionRevocationUncertain):
            await self.provider.observe(SUBJECT)
        self.assertEqual(len(self.requests), 10)

    async def test_lost_response_no_retry_and_sanitized_error(self):
        self.responses = [response(page()), httpx.ReadTimeout('private upstream data')]
        with self.assertRaises(SessionRevocationUncertain) as caught:
            await self.provider.revoke(SUBJECT, SESSION, OP)
        self.assertEqual(str(caught.exception), 'Provider session recovery is uncertain.')
        self.assertEqual(len(self.requests), 2)

    async def test_revoke_unexpected_status_or_payload_is_uncertain(self):
        for value in (response({}, status=204), response({'unexpected': True}),
                      response({}, status=429), response({}, status=302)):
            self.responses = [response(page()), value]
            with self.assertRaises(SessionRevocationUncertain):
                await self.provider.revoke(SUBJECT, SESSION, OP)

    async def test_malformed_and_oversized_responses_fail_closed(self):
        for value in (response(raw=b'{"data":[],"data":[]}'), response(raw=b' ' * 65537),
                      response(page(), headers={'Content-Type': 'text/html'}),
                      response(page(), headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})):
            self.responses = [value]
            with self.assertRaises(SessionRevocationUncertain):
                await self.provider.observe(SUBJECT)

    async def test_invalid_inputs_never_dispatch(self):
        for args in (('../private', SESSION, OP), (SUBJECT, '../private', OP), (SUBJECT, SESSION, 'bad')):
            with self.assertRaises(SessionRevocationUncertain):
                await self.provider.revoke(*args)
        self.assertEqual(self.requests, [])

    async def test_cancellation_propagates_without_retry(self):
        self.responses = [response(page()), asyncio.CancelledError()]
        with self.assertRaises(asyncio.CancelledError):
            await self.provider.revoke(SUBJECT, SESSION, OP)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.provider._inflight, 0)

    async def test_factor_inventory_membership_and_binding(self):
        factor = 'auth_factor_synthetic_owner'
        value = {'object': 'list', 'data': [
            {'object': 'authentication_factor', 'id': factor, 'type': 'totp'}],
            'list_metadata': {'after': None}}
        self.responses = [response(value)]
        result = await self.provider.observe_factors(SUBJECT)
        self.assertEqual(result.factor_ids, (factor,))
        self.assertEqual(result.provider_binding, self.settings.binding)
        self.assertTrue(result.complete)
        self.assertTrue(self.requests[-1].url.path.endswith('/auth_factors'))

    async def test_factor_inventory_rejects_unknown_and_other_owner(self):
        for updates in ({'type': 'unknown'}, {'user_id': 'user_synthetic_other'}):
            value = {'object': 'list', 'data': [
                {'object': 'authentication_factor', 'id': 'auth_factor_synthetic_owner',
                 'type': 'totp', **updates}], 'list_metadata': {'after': None}}
            self.responses = [response(value)]
            with self.assertRaises(SessionRevocationUncertain):
                await self.provider.observe_factors(SUBJECT)

    async def test_factor_inventory_complete_pagination_and_duplicate_rejection(self):
        def factors(factor, after=None):
            return {'object': 'list', 'data': [
                {'object': 'authentication_factor', 'id': factor, 'type': 'totp', 'user_id': SUBJECT}],
                'list_metadata': {'after': after}}
        first, second = 'auth_factor_synthetic_first', 'auth_factor_synthetic_second'
        self.responses = [response(factors(first, first)), response(factors(second))]
        self.assertEqual((await self.provider.observe_factors(SUBJECT)).factor_ids, (first, second))
        self.responses = [response(factors(first, first)), response(factors(first))]
        with self.assertRaises(SessionRevocationUncertain):
            await self.provider.observe_factors(SUBJECT)

    async def test_factor_inventory_complete_pagination_and_duplicate_rejection(self):
        def factors(factor, after=None):
            return {'object': 'list', 'data': [
                {'object': 'authentication_factor', 'id': factor, 'type': 'totp', 'user_id': SUBJECT}],
                'list_metadata': {'after': after}}
        first, second = 'auth_factor_synthetic_first', 'auth_factor_synthetic_second'
        self.responses = [response(factors(first, first)), response(factors(second))]
        self.assertEqual((await self.provider.observe_factors(SUBJECT)).factor_ids, (first, second))
        self.responses = [response(factors(first, first)), response(factors(first))]
        with self.assertRaises(SessionRevocationUncertain):
            await self.provider.observe_factors(SUBJECT)


if __name__ == '__main__':
    unittest.main()
