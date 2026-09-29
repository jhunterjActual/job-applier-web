"""Synthetic transaction-boundary tests; no database, configuration or provider."""
import asyncio
from dataclasses import asdict, replace
import json
import unittest
from unittest.mock import AsyncMock, Mock

from sqlalchemy.ext.asyncio import AsyncEngine

from factor_removal import RemovalRecord, RemovalUncertain
from factor_removal_postgres import PostgreSQLRemovalStore, record
from session_authorization import SessionAuthorization
from totp_enrollment import EnrollmentActor


class RemovalStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor = EnrollmentActor(SessionAuthorization(1,'user_synthetic','a'*64,1,
            '11111111-1111-4111-8111-111111111111'), 'session_synthetic','b'*64,1000)
        self.row = RemovalRecord('22222222-2222-4222-8222-222222222222',self.actor,
            'auth_factor_synthetic','challenge_claimed',1300)
        self.engine = Mock(spec=AsyncEngine)
        self.engine.url = Mock(username='careertrellis_runtime')
        self.connection = Mock()
        self.connection.exec_driver_sql = AsyncMock()
        self.connection.scalar = AsyncMock(return_value=True)
        self.transaction = Mock()
        self.transaction.__aenter__ = AsyncMock(return_value=self.connection)
        self.transaction.__aexit__ = AsyncMock(return_value=False)
        self.engine.begin.return_value = self.transaction
        self.coordination = Mock()
        self.coordination.digest.return_value = 'c'*64
        self.store = PostgreSQLRemovalStore(self.engine,self.coordination)

    async def test_restricted_engine(self):
        for engine in (object(),Mock(spec=AsyncEngine)):
            with self.assertRaises(ValueError): PostgreSQLRemovalStore(engine,self.coordination)
        self.engine.url.username = 'careertrellis_migration'
        with self.assertRaises(ValueError): PostgreSQLRemovalStore(self.engine,self.coordination)

    async def test_commit_before_positive_result_and_exact_contract(self):
        self.assertTrue(await self.store.reserve(self.row))
        self.transaction.__aexit__.assert_awaited_once_with(None,None,None)
        self.coordination.digest.assert_called_once_with('session',1,'user_synthetic','session_synthetic')
        self.assertEqual([x.args[0] for x in self.connection.exec_driver_sql.await_args_list],
            ["SET LOCAL lock_timeout='2s'","SET LOCAL statement_timeout='3s'"])
        sql, params = self.connection.scalar.await_args.args
        self.assertIn('careertrellis.factor_removal_request',str(sql))
        self.assertEqual(params['action'],'reserve')
        self.assertEqual(params['op'],self.row.operation)
        self.assertEqual(json.loads(params['new']),asdict(self.row))
        self.assertEqual(json.loads(params['actor']),asdict(self.actor))
        self.assertIsNone(params['old'])

    async def test_commit_ack_loss_is_uncertain_without_retry(self):
        self.transaction.__aexit__.side_effect = OSError('private connection detail')
        with self.assertRaises(RemovalUncertain) as caught: await self.store.reserve(self.row)
        self.assertNotIn('private',str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.engine.begin.assert_called_once()
        self.connection.scalar.assert_awaited_once()

    async def test_query_failures_sanitized_no_retry(self):
        for failure in (TimeoutError('private'),OSError('private'),RuntimeError('private')):
            with self.subTest(failure=type(failure).__name__):
                self.connection.scalar.reset_mock()
                self.connection.scalar.side_effect = failure
                with self.assertRaises(RemovalUncertain) as caught: await self.store.eligible(self.actor)
                self.assertNotIn('private',str(caught.exception))
                self.connection.scalar.assert_awaited_once()

    async def test_cancellation_propagates_no_retry(self):
        self.connection.scalar.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError): await self.store.reserve(self.row)
        self.connection.scalar.assert_awaited_once()
        self.transaction.__aexit__.assert_awaited_once()

    async def test_strict_boolean_results(self):
        for value in (1,0,'true',{},[]):
            self.connection.scalar.return_value = value
            with self.assertRaises(RemovalUncertain): await self.store.eligible(self.actor)
        self.connection.scalar.return_value = False
        self.assertFalse(await self.store.eligible(self.actor))
        self.connection.scalar.return_value = None
        self.assertFalse(await self.store.eligible(self.actor))
        self.assertFalse(await self.store.reserve(self.row))
        self.assertFalse(await self.store.cas(self.row,replace(self.row,
            state='challenge_ready',challenge='auth_challenge_synthetic'),actor=self.actor))

    async def test_get_roundtrip_all_states_and_fresh_inspection(self):
        rows = [self.row, replace(self.row,state='challenge_ready',challenge='auth_challenge_synthetic'),
            replace(self.row,state='verification_claimed',challenge='auth_challenge_synthetic',attempts=1),
            replace(self.row,state='delete_claimed',challenge='auth_challenge_synthetic',attempts=1,verified_at=1100),
            replace(self.row,state='delete_recorded',challenge='auth_challenge_synthetic',attempts=1,verified_at=1100,delete_acknowledged=True),
            replace(self.row,state='complete',challenge='auth_challenge_synthetic',attempts=1,verified_at=1100,delete_acknowledged=True)]
        fresh = replace(self.actor,session_id='session_fresher',browser='d'*64,authenticated_at=1200)
        for row in rows:
            self.connection.scalar.return_value = asdict(row)
            self.assertEqual(await self.store.get(fresh,row.operation),row)
        self.connection.scalar.return_value = None
        self.assertIsNone(await self.store.get(self.actor,self.row.operation))

    async def test_decode_rejects_wrong_shapes_types_fields_and_bindings(self):
        bad = [True,[], '{}', {'operation': self.row.operation}]
        for field,value in [('deadline',True),('attempts',True),('factor','wrong'),
                            ('delete_acknowledged',1),('actor',{}),('state','unknown')]:
            data = asdict(self.row); data[field] = value; bad.append(data)
        extra = asdict(self.row); extra['code'] = '123456'; bad.append(extra)
        nested = asdict(self.row); nested['actor']['authorization']['epoch'] = True; bad.append(nested)
        nested = asdict(self.row); nested['actor']['session_id'] = 'wrong'; bad.append(nested)
        for data in bad:
            with self.subTest(data_type=type(data).__name__):
                with self.assertRaises(RemovalUncertain): record(data)
        for row in (replace(self.row,operation='33333333-3333-4333-8333-333333333333'),
                    replace(self.row,actor=replace(self.actor,authorization=replace(self.actor.authorization,epoch=2)))):
            self.connection.scalar.return_value=asdict(row)
            with self.assertRaises(RemovalUncertain): await self.store.get(self.actor,self.row.operation)

    async def test_cas_exact_bindings_and_original_record(self):
        new = replace(self.row,state='challenge_ready',challenge='auth_challenge_synthetic')
        self.assertTrue(await self.store.cas(self.row,new,actor=self.actor))
        params = self.connection.scalar.await_args.args[1]
        self.assertEqual(json.loads(params['old']),asdict(self.row))
        self.assertEqual(json.loads(params['new']),asdict(new))
        self.assertEqual(params['action'],'cas')
        self.connection.scalar.reset_mock()
        for changed in (replace(new,factor='auth_factor_different'),
                        replace(new,operation='33333333-3333-4333-8333-333333333333'),
                        replace(new,actor=replace(self.actor,browser='d'*64))):
            with self.assertRaises(RemovalUncertain): await self.store.cas(self.row,changed,actor=self.actor)
        self.connection.scalar.assert_not_awaited()

    async def test_invalid_input_and_digest_failure_before_transaction(self):
        for operation in (False,'not-a-uuid',self.row.operation.upper()+'x'):
            with self.assertRaises(RemovalUncertain): await self.store.get(self.actor,operation)
        with self.assertRaises(RemovalUncertain): await self.store.eligible(object())
        with self.assertRaises(RemovalUncertain): await self.store.reserve(object())
        self.coordination.digest.side_effect=RuntimeError('private key detail')
        with self.assertRaises(RemovalUncertain): await self.store.eligible(self.actor)
        self.engine.begin.assert_not_called()


if __name__ == '__main__':
    unittest.main()
