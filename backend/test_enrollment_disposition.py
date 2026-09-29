"""Synthetic coordinator and transaction tests; no provider or database access."""
import asyncio
from dataclasses import asdict, replace
import json
import unittest
from unittest.mock import AsyncMock, Mock

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncEngine

from enrollment_disposition import AttributableEnrollmentCoordinator
from enrollment_disposition_postgres import PostgreSQLEnrollmentDispositionStore
from session_authorization import SessionAuthorization
from totp_enrollment import EnrollmentActor, EnrollmentRecord, EnrollmentRejected, EnrollmentUncertain


def actor_fixture():
    return EnrollmentActor(SessionAuthorization(1,'user_synthetic','a'*64,1,
        '11111111-1111-4111-8111-111111111111'),'session_synthetic','b'*64,1000)


class State:
    """Shared synthetic committed state survives replacement of coordinator objects."""
    def __init__(self,row):
        self.row=row; self.receipts=[]; self.allowed=True; self.lost_claim=False; self.lost_finish=False

    async def eligible(self,actor): return self.allowed
    async def get(self,actor,operation): return self.row

    async def cas(self,old,new,*,actor):
        if self.row != old or old.actor != actor: return False
        self.row=new
        if self.lost_claim: raise OSError('private commit error')
        return True

    async def finish_verified(self,old,new,*,actor):
        if self.row != old or old.actor != actor or old.state != 'verifying': return False
        self.row=new; self.receipts.append((old.operation,old.factor,old.challenge,old.attempts))
        if self.lost_finish: raise OSError('private commit error')
        return True


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor=actor_fixture(); self.clock=1001; self.key=Fernet.generate_key()
        self.row=EnrollmentRecord('22222222-2222-4222-8222-222222222222',self.actor,
            'pending',1250,'auth_factor_synthetic','auth_challenge_synthetic','sealed_fixture',0)
        self.store=State(self.row)
        self.provider=Mock(verify=AsyncMock(return_value=True),is_verified=AsyncMock(return_value=True))
        self.coordinator=self.make()

    def make(self):
        return AttributableEnrollmentCoordinator(self.store,self.provider,self.key,now=lambda:self.clock)

    async def verify(self): return await self.coordinator.verify(self.actor,self.row.operation,'123456')

    async def test_positive_exact_claim_receipt_and_scrub(self):
        async def proof(*args):
            self.assertEqual(self.store.row.state,'verifying')
            self.assertEqual(self.store.row.attempts,1)
            self.assertEqual(args,(self.row.challenge,self.row.factor,'123456'))
            return True
        self.provider.verify.side_effect=proof
        self.assertEqual(await self.verify(),'complete')
        self.assertIsNone(self.store.row.sealed_secret)
        self.assertEqual(self.store.receipts,[(self.row.operation,self.row.factor,self.row.challenge,1)])
        self.provider.is_verified.assert_awaited_once_with(self.actor.authorization.subject,self.row.factor)

    async def test_invalid_code_only_explicit_false_returns_pending(self):
        self.provider.verify.return_value=False
        self.assertEqual(await self.verify(),'code_not_accepted')
        self.assertEqual(self.store.row.state,'pending'); self.assertEqual(self.store.row.attempts,1)
        self.assertEqual(self.store.receipts,[])
        self.provider.is_verified.assert_not_awaited()

    async def test_unknown_positive_and_response_loss_never_receipt_or_replay(self):
        for outcome in (None,1,'true',{},OSError('private')):
            self.store.row=self.row
            self.provider.verify.reset_mock(); self.provider.verify.side_effect=None
            self.provider.verify.return_value=outcome
            if isinstance(outcome,Exception): self.provider.verify.side_effect=outcome
            with self.assertRaises(EnrollmentUncertain): await self.verify()
            restarted=self.make()
            self.assertEqual(await restarted.reconcile(self.actor,self.row.operation),'unresolved')
            with self.assertRaises(EnrollmentRejected): await restarted.verify(self.actor,self.row.operation,'123456')
            self.provider.verify.assert_awaited_once()
            self.assertEqual(self.store.receipts,[])

    async def test_membership_failure_keeps_verification_claim(self):
        for outcome in (False,None,1,OSError('private')):
            self.store.row=self.row
            self.provider.is_verified.side_effect=None; self.provider.is_verified.return_value=outcome
            if isinstance(outcome,Exception): self.provider.is_verified.side_effect=outcome
            with self.assertRaises(EnrollmentUncertain): await self.verify()
            self.assertEqual(self.store.row.state,'verifying'); self.assertEqual(self.store.receipts,[])

    async def test_lost_claim_commit_stops_provider(self):
        self.store.lost_claim=True
        with self.assertRaises(EnrollmentUncertain): await self.verify()
        self.provider.verify.assert_not_awaited()
        with self.assertRaises(EnrollmentRejected): await self.make().verify(self.actor,self.row.operation,'123456')

    async def test_lost_finish_ack_keeps_single_receipt_on_reconnect(self):
        self.store.lost_finish=True
        with self.assertRaises(EnrollmentUncertain): await self.verify()
        restarted=self.make()
        self.assertEqual(await restarted.reconcile(self.actor,self.row.operation),'complete')
        with self.assertRaises(EnrollmentRejected): await restarted.verify(self.actor,self.row.operation,'123456')
        self.provider.verify.assert_awaited_once(); self.assertEqual(len(self.store.receipts),1)

    async def test_legacy_complete_and_pending_reconcile_cannot_mint(self):
        self.assertEqual(await self.coordinator.reconcile(self.actor,self.row.operation),'unresolved')
        self.store.row=replace(self.row,state='complete',sealed_secret=None)
        self.assertEqual(await self.coordinator.reconcile(self.actor,self.row.operation),'complete')
        self.assertEqual(self.store.receipts,[])
        self.provider.verify.assert_not_awaited(); self.provider.is_verified.assert_not_awaited()

    async def test_actor_operation_and_factor_bindings_fail_before_call(self):
        for actor in (replace(self.actor,browser='c'*64),replace(self.actor,session_id='session_changed'),
                      replace(self.actor,authorization=replace(self.actor.authorization,epoch=2))):
            with self.assertRaises(EnrollmentRejected): await self.coordinator.verify(actor,self.row.operation,'123456')
        with self.assertRaises(EnrollmentRejected): await self.coordinator.verify(self.actor,'different','123456')
        self.store.row=replace(self.row,factor='wrong')
        with self.assertRaises(EnrollmentRejected): await self.verify()
        self.provider.verify.assert_not_awaited()

    async def test_expiry_or_revocation_after_await_blocks_completion(self):
        for phase in ('verify','membership'):
            for revoke in (False,True):
                self.store.row=self.row; self.store.allowed=True; self.clock=1001
                self.provider.verify.side_effect=None; self.provider.is_verified.side_effect=None
                async def change(*args):
                    if revoke: self.store.allowed=False
                    else: self.clock=1250
                    return True
                getattr(self.provider,'verify' if phase=='verify' else 'is_verified').side_effect=change
                with self.assertRaises(EnrollmentRejected): await self.verify()
                self.assertEqual(self.store.receipts,[])

    async def test_invalid_budget_and_cancellation(self):
        self.store.row=replace(self.row,attempts=5)
        with self.assertRaises(EnrollmentRejected): await self.verify()
        self.store.row=self.row; self.provider.verify.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError): await self.verify()
        self.assertEqual(self.store.row.state,'verifying'); self.assertEqual(self.store.receipts,[])


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor=actor_fixture()
        self.old=EnrollmentRecord('22222222-2222-4222-8222-222222222222',self.actor,
            'verifying',1250,'auth_factor_synthetic','auth_challenge_synthetic','sealed_fixture',1)
        self.new=replace(self.old,state='complete',sealed_secret=None)
        self.engine=Mock(spec=AsyncEngine); self.engine.url=Mock(username='careertrellis_runtime')
        self.connection=Mock(exec_driver_sql=AsyncMock(),scalar=AsyncMock(return_value=True))
        self.tx=Mock(); self.tx.__aenter__=AsyncMock(return_value=self.connection)
        self.tx.__aexit__=AsyncMock(return_value=False); self.engine.begin.return_value=self.tx
        self.coordination=Mock(); self.coordination.digest.return_value='c'*64
        self.store=PostgreSQLEnrollmentDispositionStore(self.engine,self.coordination)

    async def finish(self): return await self.store.finish_verified(self.old,self.new,actor=self.actor)

    async def test_exact_contract_and_commit(self):
        self.assertTrue(await self.finish()); self.tx.__aexit__.assert_awaited_once_with(None,None,None)
        self.coordination.digest.assert_called_once_with('session',1,'user_synthetic','session_synthetic')
        sql,p=self.connection.scalar.await_args.args
        self.assertIn('careertrellis.totp_finish_verified',str(sql))
        self.assertEqual(json.loads(p['old']),asdict(self.old)); self.assertEqual(json.loads(p['new']),asdict(self.new))
        self.assertEqual([x.args[0] for x in self.connection.exec_driver_sql.await_args_list],
            ["SET LOCAL lock_timeout='2s'","SET LOCAL statement_timeout='3s'"])

    async def test_denials_and_malformed_outcomes(self):
        for value in (None,False):
            self.connection.scalar.return_value=value; self.assertFalse(await self.finish())
        for value in (1,'true',{}):
            self.connection.scalar.return_value=value
            with self.assertRaises(EnrollmentUncertain): await self.finish()

    async def test_commit_uncertainty_timeout_and_no_retry(self):
        for error in (OSError('private'),TimeoutError('private')):
            self.connection.scalar.reset_mock(); self.tx.__aexit__.side_effect=error
            with self.assertRaises(EnrollmentUncertain) as caught: await self.finish()
            self.assertNotIn('private',str(caught.exception)); self.connection.scalar.assert_awaited_once()

    async def test_exact_completion_and_actor_required(self):
        for new in (replace(self.new,factor='auth_factor_otherxx'),replace(self.new,attempts=2),
                    replace(self.new,actor=replace(self.actor,browser='d'*64)),self.old):
            with self.assertRaises(EnrollmentUncertain): await self.store.finish_verified(self.old,new,actor=self.actor)
        with self.assertRaises(EnrollmentUncertain):
            await self.store.finish_verified(self.old,self.new,actor=replace(self.actor,session_id='session_fresher'))
        self.engine.begin.assert_not_called()

    async def test_cancellation_and_restricted_engine(self):
        self.connection.scalar.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError): await self.finish()
        self.connection.scalar.assert_awaited_once()
        self.engine.url.username='careertrellis_migration'
        with self.assertRaises(ValueError): PostgreSQLEnrollmentDispositionStore(self.engine,self.coordination)


if __name__ == '__main__': unittest.main()
