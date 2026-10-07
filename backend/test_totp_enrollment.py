"""Synthetic durable-store rehearsal. SQLite here is a fixture, not app storage."""
import asyncio
from dataclasses import replace
import json
import pickle
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from cryptography.fernet import Fernet
import httpx

from session_authorization import SessionAuthorization
from totp_enrollment import (EnrollmentActor, EnrollmentCoordinator, EnrollmentRecord,
    EnrollmentSetup, EnrollmentRejected, EnrollmentUncertain)
from workos_totp import WorkOSTOTP


ACTOR = EnrollmentActor(SessionAuthorization(1, 'user_synthetic_owner', 'a' * 64, 1,
    '00000000-0000-4000-8000-000000000001'), 'session_synthetic_owner', 'b' * 64, 1000)
SECRET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
FACTOR = 'auth_factor_synthetic_first'
CHALLENGE = 'auth_challenge_synthetic_first'


class DurableFixture:
    # Pickle is confined to self-generated test records in a temporary directory.
    # It is not an example of a production serialization boundary.
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS attempts (slot TEXT PRIMARY KEY, body BLOB)')
        self.allowed = True
        self.fail_write = False

    @staticmethod
    def slot(actor):
        a = actor.authorization
        return str((a.local_id, a.incarnation, a.provider_binding))

    async def eligible(self, actor):
        return self.allowed

    async def reserve(self, row):
        if not self.allowed:
            return False
        try:
            with self.db:
                self.db.execute('INSERT INTO attempts VALUES (?, ?)',
                                (self.slot(row.actor), pickle.dumps(row)))
            return True
        except sqlite3.IntegrityError:
            return False

    async def get(self, actor, operation=None):
        value = self.db.execute('SELECT body FROM attempts WHERE slot=?', (self.slot(actor),)).fetchone()
        return pickle.loads(value[0]) if value else None

    async def cas(self, old, new, *, actor=None):
        if self.fail_write:
            raise OSError('Synthetic write interruption')
        if not self.allowed:
            return False
        with self.db:
            cursor = self.db.execute('UPDATE attempts SET body=? WHERE slot=? AND body=?',
                (pickle.dumps(new), self.slot(old.actor), pickle.dumps(old)))
        return cursor.rowcount == 1


class ProviderFixture:
    def __init__(self):
        self.creates = self.verifications = 0
        self.verified = False
        self.valid = True
        self.lost_create = self.lost_verify = False
        self.hook = None

    async def enroll(self, subject):
        self.creates += 1
        if self.hook:
            await self.hook()
        if self.lost_create:
            raise EnrollmentUncertain()
        return EnrollmentSetup(FACTOR, CHALLENGE, SECRET, 1600)

    async def verify(self, challenge, factor, code):
        self.verifications += 1
        self.verified = self.valid
        if self.lost_verify:
            raise EnrollmentUncertain()
        return self.valid

    async def is_verified(self, subject, factor):
        return self.verified


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'synthetic.sqlite'
        self.store = DurableFixture(self.path)
        self.provider = ProviderFixture()
        self.key = Fernet.generate_key()
        self.clock = 1000
        self.flow = EnrollmentCoordinator(self.store, self.provider, self.key, now=lambda: self.clock)

    async def asyncTearDown(self):
        self.store.db.close()
        self.directory.cleanup()

    async def test_success_encrypts_and_erases_secret(self):
        operation = await self.flow.start(ACTOR)
        self.assertNotIn(SECRET.encode(), self.path.read_bytes())
        self.assertEqual(await self.flow.setup_secret(ACTOR, operation), SECRET)
        self.assertEqual(await self.flow.verify(ACTOR, operation, '123456'), 'complete')
        self.assertIsNone((await self.store.get(ACTOR, operation)).sealed_secret)
        with self.assertRaises(EnrollmentRejected):
            await self.flow.setup_secret(ACTOR, operation)

    async def test_lost_create_survives_restart_and_blocks_duplicate(self):
        self.provider.lost_create = True
        with self.assertRaises(EnrollmentUncertain):
            await self.flow.start(ACTOR)
        self.store.db.close()
        self.store = DurableFixture(self.path)
        self.flow.store = self.store
        with self.assertRaises(EnrollmentRejected):
            await self.flow.start(ACTOR)
        self.assertEqual(self.provider.creates, 1)
        row = await self.store.get(ACTOR, 'unused')
        self.assertEqual(await self.flow.reconcile(ACTOR, row.operation), 'unresolved')

    async def test_lost_durable_write_blocks_duplicate(self):
        self.store.fail_write = True
        with self.assertRaises(OSError):
            await self.flow.start(ACTOR)
        self.store.fail_write = False
        with self.assertRaises(EnrollmentRejected):
            await self.flow.start(ACTOR)
        self.assertEqual(self.provider.creates, 1)

    async def test_lost_verify_reconciles_exact_factor(self):
        operation = await self.flow.start(ACTOR)
        self.provider.lost_verify = True
        with self.assertRaises(EnrollmentUncertain):
            await self.flow.verify(ACTOR, operation, '123456')
        with self.assertRaises(EnrollmentRejected):
            await self.flow.verify(ACTOR, operation, '123456')
        self.assertEqual(await self.flow.reconcile(ACTOR, operation), 'complete')
        self.assertEqual(self.provider.verifications, 1)

    async def test_absent_after_lost_verify_is_not_retry_authority(self):
        operation = await self.flow.start(ACTOR)
        self.provider.lost_verify = True
        self.provider.valid = False
        with self.assertRaises(EnrollmentUncertain):
            await self.flow.verify(ACTOR, operation, '123456')
        self.assertEqual(await self.flow.reconcile(ACTOR, operation), 'unresolved')
        with self.assertRaises(EnrollmentRejected):
            await self.flow.verify(ACTOR, operation, '654321')

    async def test_wrong_code_bounded_and_expired_denied(self):
        operation = await self.flow.start(ACTOR)
        self.provider.valid = False
        for _ in range(5):
            self.assertEqual(await self.flow.verify(ACTOR, operation, '123456'), 'code_not_accepted')
        with self.assertRaises(EnrollmentRejected):
            await self.flow.verify(ACTOR, operation, '123456')
        self.clock = 1300
        with self.assertRaises(EnrollmentRejected):
            await self.flow.setup_secret(ACTOR, operation)

    async def test_other_browser_epoch_and_session_denied(self):
        operation = await self.flow.start(ACTOR)
        for other in (replace(ACTOR, browser='c' * 64),
                      replace(ACTOR, session_id='session_synthetic_other'),
                      replace(ACTOR, authorization=replace(ACTOR.authorization, epoch=2))):
            with self.assertRaises(EnrollmentRejected):
                await self.flow.setup_secret(other, operation)

    async def test_disable_after_create_prevents_secret_delivery(self):
        async def disable():
            self.store.allowed = False
        self.provider.hook = disable
        with self.assertRaises(EnrollmentUncertain):
            await self.flow.start(ACTOR)

    async def test_two_connections_only_one_create(self):
        other = DurableFixture(self.path)
        flow = EnrollmentCoordinator(other, self.provider, self.key, now=lambda: self.clock)
        entered, release = asyncio.Event(), asyncio.Event()
        async def pause():
            entered.set()
            await release.wait()
        self.provider.hook = pause
        try:
            first = asyncio.create_task(self.flow.start(ACTOR))
            await entered.wait()
            with self.assertRaises(EnrollmentRejected):
                await flow.start(ACTOR)
            release.set()
            self.assertIsInstance(await first, str)
            self.assertEqual(self.provider.creates, 1)
        finally:
            other.db.close()

    async def test_expired_action_fresh_auth_allows_reconciliation_only(self):
        operation = await self.flow.start(ACTOR)
        self.clock = 1400
        fresh = replace(ACTOR, authenticated_at=1400, session_id='session_synthetic_fresh')
        with self.assertRaises(EnrollmentRejected):
            await self.flow.setup_secret(fresh, operation)
        with self.assertRaises(EnrollmentRejected):
            await self.flow.verify(fresh, operation, '123456')
        self.assertEqual(await self.flow.reconcile(fresh, operation), 'unresolved')
        self.provider.verified = True
        self.assertEqual(await self.flow.reconcile(fresh, operation), 'complete')
        self.assertIsNone((await self.store.get(fresh, operation)).sealed_secret)

    async def test_expired_action_cannot_be_recovered_by_changed_epoch(self):
        operation = await self.flow.start(ACTOR)
        self.clock = 1400
        fresh = replace(ACTOR, authenticated_at=1400, authorization=replace(ACTOR.authorization, epoch=2))
        with self.assertRaises(EnrollmentRejected):
            await self.flow.reconcile(fresh, operation)

    async def test_close_during_verification_never_deletes_or_retries(self):
        operation = await self.flow.start(ACTOR)
        self.provider.lost_verify = True
        with self.assertRaises(EnrollmentUncertain):
            await self.flow.verify(ACTOR, operation, '123456')
        self.assertEqual(await self.flow.close(ACTOR, operation), 'closed_outcome_unresolved')
        self.assertEqual(await self.flow.reconcile(ACTOR, operation), 'complete')
        self.assertEqual(self.provider.verifications, 1)

    async def test_encrypted_state_cannot_be_transplanted(self):
        operation = await self.flow.start(ACTOR)
        row = await self.store.get(ACTOR, operation)
        await self.store.cas(row, replace(row, operation='different'))
        with self.assertRaises(EnrollmentRejected):
            await self.flow.setup_secret(ACTOR, 'different')

    async def test_invalid_code_does_not_consume_attempt(self):
        operation = await self.flow.start(ACTOR)
        for code in ('１２３４５６', '12345', 123456, '123456\n'):
            with self.assertRaises(EnrollmentRejected):
                await self.flow.verify(ACTOR, operation, code)
        self.assertEqual(self.provider.verifications, 0)

    async def test_deadline_rechecked_after_claim(self):
        operation = await self.flow.start(ACTOR)
        row = await self.store.get(ACTOR, operation)
        await self.store.cas(row, replace(row, deadline=1001))
        original = self.store.cas
        async def delayed(old, new, **kwargs):
            result = await original(old, new, **kwargs)
            self.clock = 1002
            return result
        self.store.cas = delayed
        with self.assertRaises(EnrollmentRejected):
            await self.flow.verify(ACTOR, operation, '123456')
        self.assertEqual(self.provider.verifications, 0)

    async def test_close_while_provider_verify_is_in_flight(self):
        operation = await self.flow.start(ACTOR)
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args):
            entered.set()
            await release.wait()
            self.provider.verified = True
            return True
        self.provider.verify = delayed
        task = asyncio.create_task(self.flow.verify(ACTOR, operation, '123456'))
        await entered.wait()
        self.assertEqual(await self.flow.close(ACTOR, operation), 'closed_outcome_unresolved')
        release.set()
        self.assertEqual(await task, 'complete')
        self.assertIsNone((await self.store.get(ACTOR, operation)).sealed_secret)


class StreamingMock(httpx.MockTransport):
    async def handle_async_request(self, request):
        response = await super().handle_async_request(request)
        return httpx.Response(response.status_code, headers=response.headers,
                              stream=httpx.ByteStream(response.content))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, handler, method, *args):
        provider = WorkOSTOTP('synthetic-key', transport=StreamingMock(handler))
        try:
            return await getattr(provider, method)(*args)
        finally:
            await provider.aclose()

    async def test_enrollment_uses_fixed_origin_no_secret_import(self):
        def handler(request):
            self.assertEqual(request.url.host, 'api.workos.com')
            self.assertEqual(json.loads(request.content), {'type': 'totp',
                'totp_issuer': 'CareerTrellis', 'totp_user': ACTOR.authorization.subject})
            return httpx.Response(201, json={'authentication_factor': {
                'id': FACTOR, 'type': 'totp', 'totp': {'secret': SECRET, 'qr_code': 'ignored'}},
                'authentication_challenge': {'id': CHALLENGE, 'authentication_factor_id': FACTOR,
                                             'expires_at': '2026-09-16T12:00:00Z'}})
        result = await self.call(handler, 'enroll', ACTOR.authorization.subject)
        self.assertEqual(result.secret, SECRET)
        self.assertNotIn(SECRET, repr(result))

    async def test_paginated_readback(self):
        calls = []
        def handler(request):
            calls.append(request)
            second = 'after' in request.url.params
            return httpx.Response(200, json={'data': [{'id': FACTOR, 'type': 'totp'}] if second else [],
                'list_metadata': {'after': None if second else 'auth_factor_synthetic_cursor'}})
        self.assertTrue(await self.call(handler, 'is_verified', ACTOR.authorization.subject, FACTOR))
        self.assertEqual(len(calls), 2)

    async def test_looping_cursor_is_uncertain_not_absent(self):
        def handler(request):
            return httpx.Response(200, json={'data': [], 'list_metadata': {'after': FACTOR}})
        with self.assertRaises(EnrollmentUncertain):
            await self.call(handler, 'is_verified', ACTOR.authorization.subject, FACTOR)

    async def test_redirect_throttle_malformed_and_timeout_never_retry(self):
        for status in (302, 429, 500):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, headers={'Location': 'https://example.invalid'}, json={})
            with self.assertRaises(EnrollmentUncertain):
                await self.call(handler, 'enroll', ACTOR.authorization.subject)
            self.assertEqual(len(calls), 1)
        with self.assertRaises(EnrollmentUncertain):
            await self.call(lambda r: httpx.Response(200, content=b'{"valid":true,"valid":false}',
                headers={'Content-Type': 'application/json'}), 'verify', CHALLENGE, FACTOR, '123456')

    async def test_verification_requires_exact_challenge_factor_and_boolean(self):
        for changed in ({'valid': 'true'}, {'challenge': {'id': CHALLENGE, 'authentication_factor_id': 'wrong'}}):
            value = {'valid': True, 'challenge': {'id': CHALLENGE, 'authentication_factor_id': FACTOR}}
            value.update(changed)
            with self.assertRaises(EnrollmentUncertain):
                await self.call(lambda r: httpx.Response(200, json=value), 'verify', CHALLENGE, FACTOR, '123456')

    async def test_network_timeout_and_aggregate_timeout_are_uncertain(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout('synthetic timeout')
        with self.assertRaises(EnrollmentUncertain):
            await self.call(handler, 'enroll', ACTOR.authorization.subject)
        self.assertEqual(len(calls), 1)
        provider = WorkOSTOTP('synthetic-key', transport=StreamingMock(handler))
        try:
            with patch.object(provider, '_verified_pages', AsyncMock(side_effect=TimeoutError)):
                with self.assertRaises(EnrollmentUncertain):
                    await provider.is_verified(ACTOR.authorization.subject, FACTOR)
        finally:
            await provider.aclose()


if __name__ == '__main__':
    unittest.main()
