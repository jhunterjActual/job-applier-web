"""Synthetic native PostgreSQL release checks; disposable fixture only."""
from datetime import datetime, timedelta, timezone
import asyncio
import json
import time
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from recovery_release_postgres import PostgreSQLRecoveryReleaseStore
from recovery_factor_proof import RecoveryProofUncertain
from factor_removal import FactorObservation, RemovalDeletion
from recovery_provider import SessionObservation, SessionRevocation, FactorInventory
from recovery_workflow import RecoveryWorkflow
from workos_transport_candidate import VerifiedTokens


async def check_releases(fixture):
    engine = create_async_engine(fixture.engine('careertrellis_migration').url, hide_parameters=True)
    store = PostgreSQLRecoveryReleaseStore(engine)
    checks = 0

    def seed():
        op, issuance, generation = (str(uuid4()) for _ in range(3))
        subject = 'user_release_' + uuid4().hex
        with fixture.admin.begin() as c:
            c.execute(text("UPDATE careertrellis_admission.identity_provider SET binding=:b"), {'b': 'a' * 64})
            uid = c.scalar(text("INSERT INTO careertrellis.app_users(workos_user_id,role) VALUES (:s,'user') RETURNING id"), {'s': subject})
            incarnation = str(c.scalar(text('SELECT incarnation FROM careertrellis_admission.identity_accounts WHERE id=:id'), {'id': uid}))
            actor = {'authorization': dict(local_id=uid, subject=subject, provider_binding='a' * 64,
                                          incarnation=incarnation, epoch=1)}
            c.execute(text("UPDATE careertrellis_admission.identity_accounts SET held=true,epoch=2 WHERE id=:id"), {'id': uid})
            c.execute(text("INSERT INTO careertrellis_admission.identity_transitions "
                "(operation,account_id,incarnation,epoch,old_binding,old_subject,target_binding,target_subject,"
                "expected_role,owner_authorized,started,deadline,state) VALUES "
                "(CAST(:op AS uuid),:id,CAST(:inc AS uuid),2,:b,:s,:b,:s,'user',false,clock_timestamp(),"
                "clock_timestamp()+interval '10 minutes','held')"),
                {'op': op, 'id': uid, 'inc': incarnation, 'b': 'a' * 64, 's': subject})
            c.execute(text("INSERT INTO careertrellis_admission.recovery_credential_sets "
                "(account_id,operation,actor,proof,purpose,generation,proposed,state,deadline,consumed_operation,consumed_epoch,consumed_at) "
                "VALUES (:id,CAST(:issuance AS uuid),CAST(:actor AS jsonb),'{}','issue_recovery_credentials',"
                "CAST(:gen AS uuid),'{}','consumed',1000,CAST(:op AS uuid),2,clock_timestamp())"),
                {'id': uid, 'issuance': issuance, 'actor': json.dumps(actor), 'gen': generation, 'op': op})
        return op, uid, incarnation, subject

    async def rejected(call):
        try:
            await call
        except RecoveryProofUncertain:
            return
        raise AssertionError('Unsafe release accepted')

    def evidence(subject):
        now = datetime.now(timezone.utc)
        return dict(binding='a' * 64, subject=subject, authenticated_at=now.isoformat(),
                    observed_at=now.isoformat(), expires_at=(now + timedelta(minutes=2)).isoformat(),
                    session_id='session_fresh_fixture', remaining_sessions=['session_fresh_fixture'],
                    remaining_factors=[], proof_digest='b' * 64)

    try:
        op, uid, inc, subject = seed()
        for sql in (
            "SELECT careertrellis_admission.identity_activate(CAST(:op AS uuid),2)",
            "SELECT careertrellis_admission.identity_attest(CAST(:op AS uuid),2,repeat('a',64),clock_timestamp(),clock_timestamp()+interval '1 minute',false)",
            "SELECT careertrellis_admission.identity_resume(CAST(:op AS uuid),gen_random_uuid(),2,clock_timestamp()+interval '1 minute',false)"):
            try:
                async with engine.begin() as connection:
                    await connection.execute(text(sql), {'op': op})
            except Exception:
                pass
            else:
                raise AssertionError('Generic identity entry bypassed recovery')
            checks += 1
        effects = [dict(effect_id=str(uuid4()), kind='revoke_session', target='session_old_fixture')]
        await store.reserve(op, 2, inc, effects, 'c' * 64)
        ctx = await store.context(op)
        assert ctx['account_id'] == uid and ctx['effects'][0]['state'] == 'ready'
        checks += 1
        await rejected(store.reserve(op, 2, inc, [], 'd' * 64))
        checks += 1
        await rejected(store.complete(op, 2, inc, evidence(subject)))
        checks += 1
        assert await store.claim(op, effects[0]['effect_id']) is True
        assert await store.claim(op, effects[0]['effect_id']) is False
        checks += 1
        await rejected(store.complete(op, 2, inc, evidence(subject)))
        checks += 1
        await store.record(op, effects[0]['effect_id'], 'succeeded', 'd' * 64)
        await rejected(store.record(op, effects[0]['effect_id'], 'succeeded', 'd' * 64))
        checks += 1
        proof = evidence(subject)
        await rejected(store.complete(op, 2, str(uuid4()), proof))
        await rejected(store.complete(op, 2, inc, {**proof, 'subject': 'user_wrong_fixture'}))
        await rejected(store.complete(op, 2, inc, {**proof, 'remaining_sessions': ['session_old_fixture']}))
        checks += 3
        await rejected(store.complete(op, 2, inc, {**proof, 'authenticated_at': ctx['started']}))
        checks += 1
        assert await store.complete(op, 2, inc, proof) == {'state': 'committed', 'epoch': 2}
        with fixture.admin.begin() as c:
            assert tuple(c.execute(text('SELECT held,epoch FROM careertrellis_admission.identity_accounts WHERE id=:id'), {'id': uid}).one()) == (False, 2)
            assert not c.scalar(text("SELECT has_function_privilege('careertrellis_runtime',"
                "'careertrellis_admission.recovery_release_request(uuid,text,jsonb)','EXECUTE')"))
            assert not c.scalar(text("SELECT has_function_privilege('careertrellis_migration',"
                "'careertrellis_admission.identity_activate_nonrecovery_base(uuid,bigint)','EXECUTE')"))
        checks += 2
        op, uid, inc, subject = seed()
        effect = dict(effect_id=str(uuid4()), kind='revoke_session', target='session_unknown_fixture')
        await store.reserve(op, 2, inc, [effect], 'e' * 64)
        await store.claim(op, effect['effect_id'])
        await store.record(op, effect['effect_id'], 'uncertain', 'f' * 64)
        await rejected(store.complete(op, 2, inc, evidence(subject)))
        assert not await store.claim(op, effect['effect_id'])
        checks += 1
        # Same coordinator used by the protected operator, backed by real SQL.
        # Only remote provider/authentication responses are synthetic here.
        op, uid, inc, subject = seed()

        class Provider:
            binding = 'a' * 64

            def __init__(self):
                self.sessions = ['session_e2e_old']
                self.factors = ['auth_factor_e2e_lost']
                self.calls = []

            async def observe(self, owner, factor=None):
                assert owner == subject
                if factor is not None:
                    assert factor in self.factors
                    return FactorObservation(self.binding, owner, factor, 'totp', 'present', True)
                return SessionObservation(self.binding, owner, tuple(self.sessions), int(time.time()))

            async def observe_factors(self, owner):
                assert owner == subject
                return FactorInventory(self.binding, owner, tuple(self.factors), int(time.time()))

            async def claimed(self, kind, target):
                ctx = await store.context(op)
                assert any(e['kind'] == kind and e['target'] == target and e['state'] == 'claimed'
                           for e in ctx['effects'])
                self.calls.append((kind, target))

            async def delete(self, owner, factor, operation):
                assert owner == subject and operation == op
                await self.claimed('delete_factor', factor)
                self.factors.remove(factor)
                return RemovalDeletion(self.binding, owner, factor, operation, 'deleted')

            async def revoke(self, owner, sid, operation):
                assert owner == subject and operation == op
                await self.claimed('revoke_session', sid)
                self.sessions.remove(sid)
                return SessionRevocation(self.binding, owner, sid, operation, int(time.time()))

        class Authentication:
            settings = SimpleNamespace(binding='a' * 64)

            async def verify_recent(self, access, owner, sid, maximum_age):
                assert access == 'synthetic-access' and owner == subject
                assert sid == 'session_e2e_fresh' and maximum_age == 300
                return {'auth_time': authenticated_at, 'exp': authenticated_at + 120}

        provider = Provider()
        workflow = RecoveryWorkflow(store, provider, provider, Authentication())
        assert await workflow.contain_provider(op, 'auth_factor_e2e_lost') == {
            'outcome': 'provider_contained_account_held'}
        assert len(provider.calls) == 2
        assert await workflow.contain_provider(op, 'auth_factor_e2e_lost') == {
            'outcome': 'provider_contained_account_held'}
        assert len(provider.calls) == 2
        checks += 1
        await asyncio.sleep(1.01 - time.time() % 1)
        authenticated_at = int(time.time())
        provider.sessions = ['session_e2e_fresh']
        tokens = VerifiedTokens(subject, 'session_e2e_fresh', 'synthetic', 'synthetic-access',
                                'synthetic-refresh', authenticated_at + 120, 'a' * 64)
        assert await workflow.release(op, tokens) == {'state': 'committed', 'epoch': 2}
        assert (await store.context(op))['state'] == 'committed'
        checks += 1
        return checks
    finally:
        await engine.dispose()
