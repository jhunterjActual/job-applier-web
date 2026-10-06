"""Actual runtime issuance persistence in the disposable native fixture only."""
from datetime import datetime, timedelta, timezone
from dataclasses import asdict, replace
import json
import time
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from factor_removal import RemovalChallenge, RemovalRecord
from factor_removal_postgres import PostgreSQLRemovalStore
from enrollment_disposition_postgres import PostgreSQLEnrollmentDispositionStore
from recovery_release_postgres import PostgreSQLRecoveryReleaseStore
from factor_runtime import recovery_readiness
from admission_adapter_candidate import AsyncAdmissionStore
from recovery_credentials import RecoveryCredentialCoordinator, CredentialDisplay
from recovery_credentials_postgres import PostgreSQLRecoveryCredentialStore
from recovery_credentials_hold_postgres import PostgreSQLRecoveryHoldStore
from recovery_factor_proof import RecoveryProofRejected
from session_authorization import SessionAuthorization
from signup_http import Coordination
from test_recovery_factor_proof import Provider, FACTOR, CHALLENGE
from totp_enrollment import EnrollmentActor, EnrollmentRecord, EnrollmentUncertain


def completed_enrollment(fixture, actor):
    """Seed exact disposable prerequisite rows; no provider truth claim."""
    operation = str(uuid4())
    record = EnrollmentRecord(operation, actor, 'complete', int(time.time()) + 240,
                              FACTOR, CHALLENGE, None, 1)
    params = {'id': actor.authorization.local_id, 'op': operation,
              'record': json.dumps(asdict(record)), 'actor': json.dumps(asdict(actor)),
              'auth': json.dumps(asdict(actor.authorization))}
    with fixture.admin.begin() as connection:
        connection.execute(text('INSERT INTO careertrellis_admission.totp_attempts(account_id,operation,record) '
            'VALUES(:id,CAST(:op AS uuid),CAST(:record AS jsonb))'), params)
        connection.execute(text('INSERT INTO careertrellis_admission.enrollment_dispositions(account_id,operation,record) '
            'VALUES(:id,CAST(:op AS uuid),CAST(:record AS jsonb))'), params)
        connection.execute(text('INSERT INTO careertrellis_admission.account_security_slots '
            '(account_id,operation_kind,operation,authorization_binding,actor,stage) '
            "VALUES(:id,'enrollment',CAST(:op AS uuid),CAST(:auth AS jsonb),CAST(:actor AS jsonb),'complete')"), params)


async def check_issuance(fixture):
    """Return an aggregate count; never print generated synthetic codes."""
    runtime = create_async_engine(fixture.engine('careertrellis_runtime').url,
                                  poolclass=NullPool, hide_parameters=True)
    migration = create_async_engine(fixture.engine('careertrellis_migration').url,
                                    poolclass=NullPool, hide_parameters=True)
    broker = create_async_engine(fixture.engine('careertrellis_runtime').url.set(username='careertrellis_admission_broker'),
                                 poolclass=NullPool, hide_parameters=True)
    checks = 0
    with fixture.admin.begin() as connection:
        policy = connection.execute(text('SELECT maintenance,adapter_ready,minimum_session_version '
                                         'FROM careertrellis_admission.policy')).one()
        connection.execute(text('UPDATE careertrellis_admission.policy '
                                'SET maintenance=false,adapter_ready=true,minimum_session_version=2'))
        connection.execute(text("UPDATE careertrellis_admission.identity_provider SET binding=:b"), {'b': 'a' * 64})
        subject = 'user_issuance_' + uuid4().hex
        uid = connection.scalar(text('INSERT INTO careertrellis.app_users(workos_user_id,role) '
                                     "VALUES (:s,'user') RETURNING id"), {'s': subject})
        incarnation = str(connection.scalar(text('SELECT incarnation FROM careertrellis_admission.identity_accounts '
                                                  'WHERE id=:id'), {'id': uid}))
    actor = EnrollmentActor(SessionAuthorization(uid, subject, 'a' * 64, 1, incarnation),
                            'session_issuance_' + uuid4().hex, 'b' * 64, int(time.time()))
    completed_enrollment(fixture, actor)
    coordination = Coordination(None, b'synthetic-issuance-coordination', 'a' * 64)
    store = PostgreSQLRecoveryCredentialStore(runtime, coordination)
    enrollment_store = PostgreSQLEnrollmentDispositionStore(runtime, coordination)
    removal_store = PostgreSQLRemovalStore(runtime, coordination)
    provider = Provider()
    async def challenge(subject, factor):
        provider.calls.append('challenge')
        return RemovalChallenge('a' * 64, subject, factor, CHALLENGE, int(time.time()) + 240)
    provider.challenge = challenge
    coordinator = RecoveryCredentialCoordinator(store, provider)
    operation = str(uuid4())
    purpose = 'issue_recovery_credentials'
    try:
        await recovery_readiness(AsyncAdmissionStore(broker, runtime), runtime)
        checks += 1
        with fixture.admin.begin() as connection:
            for signature in (
                'careertrellis_admission.totp_request_prerecovery(jsonb,text,text,uuid,jsonb,jsonb)',
                'careertrellis_admission.factor_removal_request_prerecovery(jsonb,text,text,uuid,jsonb,jsonb)',
                'careertrellis_admission.recovery_factor_request(jsonb,text,text,uuid,jsonb,jsonb,text)'):
                assert connection.scalar(text("SELECT has_function_privilege('careertrellis_runtime',:signature,'EXECUTE')"),
                    {'signature': signature}) is False
            assert connection.scalar(text("SELECT has_table_privilege('careertrellis_runtime',"
                "'careertrellis_admission.recovery_enrollment_history','SELECT,INSERT,UPDATE,DELETE')")) is False
        checks += 1
        assert await store.eligible(actor) is True
        checks += 1
        assert await store.enrolled_factor(actor) == FACTOR
        checks += 1
        # Completed enrollment is a transfer condition, never expiry or a hint.
        for stage in ('creating', 'verifying', 'pending'):
            with fixture.admin.begin() as connection:
                connection.execute(text('UPDATE careertrellis_admission.account_security_slots '
                    'SET stage=:stage WHERE account_id=:id'), {'stage': stage, 'id': uid})
            assert await store.eligible(actor) is False
            try:
                await coordinator.start(actor, str(uuid4()), FACTOR, purpose)
            except RecoveryProofRejected:
                pass
            else:
                raise AssertionError('Unresolved enrollment slot transferred')
            checks += 1
        with fixture.admin.begin() as connection:
            connection.execute(text("UPDATE careertrellis_admission.account_security_slots SET stage='complete',"
                "authorization_binding=jsonb_set(authorization_binding,'{epoch}','999'::jsonb),"
                "actor=jsonb_set(actor,'{authorization,epoch}','999'::jsonb) WHERE account_id=:id"), {'id': uid})
        assert await store.eligible(actor) is False
        checks += 1
        with fixture.admin.begin() as connection:
            connection.execute(text('UPDATE careertrellis_admission.account_security_slots '
                "SET authorization_binding=CAST(:auth AS jsonb),actor=jsonb_set(actor,'{authorization}',CAST(:auth AS jsonb)) WHERE account_id=:id"),
                {'auth': json.dumps(asdict(actor.authorization)), 'id': uid})
        await coordinator.start(actor, operation, FACTOR, purpose)
        assert await enrollment_store.eligible(actor) is False
        assert await removal_store.eligible(actor) is False
        checks += 1
        competing = str(uuid4())
        try:
            await coordinator.start(actor, competing, FACTOR, purpose)
        except RecoveryProofRejected:
            pass
        else:
            raise AssertionError('Pending recovery slot replaced')
        with fixture.admin.begin() as connection:
            assert connection.scalar(text('SELECT operation FROM careertrellis_admission.account_security_slots '
                'WHERE account_id=:id'), {'id': uid}) == UUID(operation)
            assert connection.scalar(text('SELECT count(*) FROM careertrellis_admission.recovery_credential_sets '
                'WHERE operation=CAST(:op AS uuid)'), {'op': competing}) == 0
            sentinel_subject = 'user_issuance_' + uuid4().hex
            sentinel_id = connection.scalar(text('INSERT INTO careertrellis.app_users(workos_user_id,role) '
                "VALUES (:s,'user') RETURNING id"), {'s': sentinel_subject})
            sentinel_incarnation = str(connection.scalar(text('SELECT incarnation FROM careertrellis_admission.identity_accounts '
                'WHERE id=:id'), {'id': sentinel_id}))
        checks += 1
        sentinel_actor = replace(actor, authorization=SessionAuthorization(
            sentinel_id, sentinel_subject, 'a' * 64, 1, sentinel_incarnation))
        completed_enrollment(fixture, sentinel_actor)
        sentinel_operation = str(uuid4())
        await coordinator.start(sentinel_actor, sentinel_operation, FACTOR, purpose)
        with fixture.admin.begin() as connection:
            sentinel_before = connection.scalar(text('SELECT row_to_json(r)::text FROM careertrellis_admission.recovery_credential_sets r '
                'WHERE operation=CAST(:op AS uuid)'), {'op': sentinel_operation})
        display = await coordinator.verify(actor, operation, FACTOR, purpose, '123456')
        assert isinstance(display, CredentialDisplay) and len(set(display.codes)) == 8
        checks += 1
        with fixture.admin.begin() as connection:
            row = connection.execute(text('SELECT state,proposed::text FROM careertrellis_admission.recovery_credential_sets '
                                          'WHERE operation=CAST(:op AS uuid)'), {'op': operation}).one()
        assert row[0] == 'pending' and all(code not in row[1] for code in display.codes)
        checks += 1
        try:
            await coordinator.confirm_custody(actor, operation, purpose, 'malformed')
        except RecoveryProofRejected:
            pass
        else:
            raise AssertionError('Malformed custody code accepted')
        checks += 1
        assert await coordinator.confirm_custody(actor, operation, purpose, display.codes[0]) == 'credentials_activated'
        checks += 1
        with fixture.admin.begin() as connection:
            row = connection.execute(text('SELECT state,generation,attempts FROM careertrellis_admission.recovery_credential_sets '
                                          'WHERE operation=CAST(:op AS uuid)'), {'op': operation}).one()
        assert row[0] == 'active' and row[2] == 2
        checks += 1
        assert await enrollment_store.eligible(actor) is True
        assert (await enrollment_store.get(actor)).factor == FACTOR
        assert await removal_store.eligible(actor) is True
        assert await removal_store.get(actor) is None
        checks += 1
        # Replacement factor readback remains available after ownership transfers.
        assert await store.enrolled_factor(actor) == FACTOR
        replacement_op = str(uuid4())
        await coordinator.start(actor, replacement_op, FACTOR, 'replace_recovery_credentials')
        replacement_display = await coordinator.verify(actor, replacement_op, FACTOR,
                                                        'replace_recovery_credentials', '123456')
        with fixture.admin.begin() as connection:
            assert connection.scalar(text('SELECT state FROM careertrellis_admission.recovery_credential_sets '
                'WHERE operation=CAST(:op AS uuid)'), {'op': operation}) == 'active'
        assert await coordinator.confirm_custody(actor, replacement_op, 'replace_recovery_credentials',
                                                 replacement_display.codes[0]) == 'credentials_activated'
        with fixture.admin.begin() as connection:
            assert connection.scalar(text('SELECT state FROM careertrellis_admission.recovery_credential_sets '
                'WHERE operation=CAST(:op AS uuid)'), {'op': operation}) == 'revoked'
            row = connection.execute(text('SELECT state,generation,attempts FROM careertrellis_admission.recovery_credential_sets '
                'WHERE operation=CAST(:op AS uuid)'), {'op': replacement_op}).one()
        display = replacement_display
        checks += 1
        recovery_operation = str(uuid4())
        result = await PostgreSQLRecoveryHoldStore(migration).consume_and_hold(
            uid, str(row[1]), recovery_operation, 'a' * 64, subject,
            datetime.now(timezone.utc) + timedelta(minutes=10), False, display.codes[1])
        assert result == {'outcome': 'account_held', 'operation': recovery_operation, 'epoch': 2}
        checks += 1
        with fixture.admin.begin() as connection:
            assert connection.scalar(text('SELECT row_to_json(r)::text FROM careertrellis_admission.recovery_credential_sets r '
                'WHERE operation=CAST(:op AS uuid)'), {'op': sentinel_operation}) == sentinel_before
        checks += 1
        release_store = PostgreSQLRecoveryReleaseStore(migration)
        effect = {'effect_id': str(uuid4()), 'kind': 'delete_factor', 'target': FACTOR}
        await release_store.reserve(recovery_operation, 2, incarnation, [effect], 'c' * 64)
        assert await release_store.claim(recovery_operation, effect['effect_id']) is True
        await release_store.record(recovery_operation, effect['effect_id'], 'succeeded', 'd' * 64)
        now = datetime.now(timezone.utc)
        proof = dict(binding='a' * 64, subject=subject, authenticated_at=now.isoformat(),
            observed_at=now.isoformat(), expires_at=(now + timedelta(seconds=60)).isoformat(),
            session_id='session_reenrollment_fixture', remaining_sessions=['session_reenrollment_fixture'],
            remaining_factors=[], proof_digest='e' * 64)
        assert await release_store.complete(recovery_operation, 2, incarnation, proof) == {'state': 'committed', 'epoch': 2}
        recovered_actor = replace(actor, authorization=replace(actor.authorization, epoch=2),
                                  session_id='session_reenrollment_fixture', authenticated_at=int(time.time()))
        assert await enrollment_store.eligible(recovered_actor) is True
        assert await enrollment_store.get(recovered_actor) is None
        assert await removal_store.eligible(recovered_actor) is False
        checks += 1
        new_enrollment = EnrollmentRecord(str(uuid4()), recovered_actor, 'creating', int(time.time()) + 240)
        try:
            await enrollment_store.reserve(replace(new_enrollment, deadline=int(time.time()) - 1))
        except EnrollmentUncertain:
            pass
        else:
            raise AssertionError('Invalid re-enrollment reserve did not roll back')
        with fixture.admin.begin() as connection:
            assert connection.scalar(text('SELECT count(*) FROM careertrellis_admission.recovery_enrollment_history '
                'WHERE account_id=:id'), {'id': uid}) == 0
            assert connection.scalar(text('SELECT stage FROM careertrellis_admission.account_security_slots '
                'WHERE account_id=:id'), {'id': uid}) == 'released'
        checks += 1
        assert await enrollment_store.reserve(new_enrollment) is True
        assert await enrollment_store.reserve(new_enrollment) is False
        with fixture.admin.begin() as connection:
            assert connection.scalar(text('SELECT count(*) FROM careertrellis_admission.recovery_enrollment_history '
                'WHERE account_id=:id AND release_operation=CAST(:op AS uuid)'), {'id': uid, 'op': recovery_operation}) == 1
            assert connection.scalar(text('SELECT operation_kind FROM careertrellis_admission.account_security_slots '
                'WHERE account_id=:id'), {'id': uid}) == 'enrollment'
            assert connection.scalar(text('SELECT state FROM careertrellis_admission.recovery_credential_sets '
                'WHERE operation=CAST(:op AS uuid)'), {'op': replacement_op}) == 'consumed'
        checks += 1
        # A separate active recovery owner can atomically enter ordinary removal.
        sentinel_codes = await coordinator.verify(sentinel_actor, sentinel_operation, FACTOR, purpose, '123456')
        await coordinator.confirm_custody(sentinel_actor, sentinel_operation, purpose, sentinel_codes.codes[0])
        removal = RemovalRecord(str(uuid4()), sentinel_actor, FACTOR, 'challenge_claimed', int(time.time()) + 240)
        assert await removal_store.reserve(removal) is True
        assert await removal_store.reserve(removal) is False
        with fixture.admin.begin() as connection:
            assert connection.scalar(text('SELECT operation_kind FROM careertrellis_admission.account_security_slots '
                'WHERE account_id=:id'), {'id': sentinel_id}) == 'removal'
            assert connection.scalar(text('SELECT state FROM careertrellis_admission.recovery_credential_sets '
                'WHERE operation=CAST(:op AS uuid)'), {'op': sentinel_operation}) == 'active'
        checks += 1
        return checks
    finally:
        await runtime.dispose()
        await migration.dispose()
        await broker.dispose()
        with fixture.admin.begin() as connection:
            connection.execute(text('UPDATE careertrellis_admission.policy SET maintenance=:m,'
                'adapter_ready=:a,minimum_session_version=:v'), {'m': policy[0], 'a': policy[1], 'v': policy[2]})
