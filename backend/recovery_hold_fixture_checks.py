"""Synthetic native checks called only by the disposable migration harness."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from recovery_credentials import credential_hash
from recovery_credentials_hold_postgres import PostgreSQLRecoveryHoldStore
from recovery_factor_proof import RecoveryProofUncertain
from session_authorization import SessionAuthorization
from totp_enrollment import EnrollmentActor


async def check_holds(fixture):
    engine = create_async_engine(fixture.engine('careertrellis_migration').url,
                                 hide_parameters=True)
    store = PostgreSQLRecoveryHoldStore(engine)
    checks = 0

    def seed():
        generation, issuance = str(uuid4()), str(uuid4())
        subject = 'user_recovery_' + uuid4().hex
        with fixture.admin.begin() as c:
            c.execute(text("UPDATE careertrellis_admission.identity_provider SET binding=:b"), {'b': 'a' * 64})
            uid = c.scalar(text("INSERT INTO careertrellis.app_users(workos_user_id,role) VALUES (:s,'user') RETURNING id"), {'s': subject})
            incarnation = str(c.scalar(text('SELECT incarnation FROM careertrellis_admission.identity_accounts WHERE id=:id'), {'id': uid}))
            actor = EnrollmentActor(SessionAuthorization(uid, subject, 'a' * 64, 1, incarnation),
                                    'session_recovery_fixture', 'b' * 64, 1000)
            digest = credential_hash(actor, issuance, 'issue_recovery_credentials', generation, 'A' * 43)
            c.execute(text("INSERT INTO careertrellis_admission.recovery_credential_sets "
                "(account_id,operation,actor,proof,purpose,generation,proposed,state,attempts,deadline) "
                "VALUES (:id,CAST(:op AS uuid),CAST(:actor AS jsonb),'{}','issue_recovery_credentials',"
                "CAST(:gen AS uuid),CAST(:proposed AS jsonb),'active',5,1001)"),
                {'id': uid, 'op': issuance, 'actor': json.dumps(asdict(actor)), 'gen': generation,
                 'proposed': json.dumps({'hashes': [digest]})})
        return dict(account_id=uid, generation=generation, recovery_operation=str(uuid4()),
                    target_binding='a' * 64, target_subject=subject,
                    until_time=datetime.now(timezone.utc) + timedelta(minutes=10),
                    owner_authorized=False, code='A' * 43)

    def state(request):
        with fixture.admin.begin() as c:
            return tuple(c.execute(text('SELECT a.held,a.epoch,c.state,c.hold_attempts '
                'FROM careertrellis_admission.identity_accounts a '
                'JOIN careertrellis_admission.recovery_credential_sets c ON c.account_id=a.id '
                'WHERE a.id=:id'), {'id': request['account_id']}).one())

    try:
        request = seed()
        for code in ('B' * 43, 'malformed'):
            result = await store.consume_and_hold(**{**request, 'code': code})
            assert result == {'outcome': 'code_not_accepted'}
            checks += 1
        assert state(request) == (False, 1, 'active', 2)
        checks += 1
        result = await store.consume_and_hold(**request)
        assert result == {'outcome': 'account_held', 'operation': request['recovery_operation'], 'epoch': 2}
        assert state(request) == (True, 2, 'consumed', 3)
        checks += 1
        receipt_request = {k: v for k, v in request.items() if k != 'code'}
        receipt = await store.reconcile_hold(**receipt_request)
        assert receipt == {'outcome': 'hold_recorded', 'operation': request['recovery_operation'],
                           'epoch': 2, 'transition_state': 'held'}
        assert state(request) == (True, 2, 'consumed', 3)
        checks += 1
        try:
            await store.reconcile_hold(**{**receipt_request, 'target_subject': 'user_wrong_receipt'})
        except RecoveryProofUncertain:
            pass
        else:
            raise AssertionError('Mismatched receipt accepted')
        checks += 1
        try:
            await store.consume_and_hold(**request)
        except RecoveryProofUncertain:
            pass
        else:
            raise AssertionError('Consumed credential reused')
        assert state(request) == (True, 2, 'consumed', 3)
        checks += 1
        request = seed()
        try:
            await store.consume_and_hold(**{**request, 'target_subject': 'user_different_identity'})
        except RecoveryProofUncertain:
            pass
        else:
            raise AssertionError('Recovery changed identity')
        assert state(request) == (False, 1, 'active', 0)
        checks += 1
        for _ in range(5):
            assert await store.consume_and_hold(**{**request, 'code': 'bad'}) == {'outcome': 'code_not_accepted'}
        try:
            await store.consume_and_hold(**request)
        except RecoveryProofUncertain:
            pass
        else:
            raise AssertionError('Exhausted budget accepted')
        assert state(request) == (False, 1, 'active', 5)
        checks += 1
        return checks
    finally:
        await engine.dispose()
