"""Combined application/browser/PostgreSQL enrollment; synthetic provider only."""
import os
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from cryptography.fernet import Fernet
import httpx

import app
import config
import signup_http
import test_p0_regressions as regressions
import test_totp_enrollment_postgres as pg_tests
import test_totp_http as http_tests
from totp_http import EnrollmentHTTP, BASE
from totp_postgres import scrub_expired
import totp_cleanup
from workos_transport_candidate import VerifiedTokens


class CombinedEnrollmentTests(unittest.IsolatedAsyncioTestCase):
    schema_revision = pg_tests.TOTPPostgresTests.schema_revision
    setUpClass = classmethod(pg_tests.TOTPPostgresTests.setUpClass.__func__)
    query = pg_tests.TOTPPostgresTests.query
    flow = pg_tests.TOTPPostgresTests.flow
    activate = pg_tests.TOTPPostgresTests.activate
    operator = pg_tests.TOTPPostgresTests.operator
    headers = http_tests.EnrollmentHTTPTests.headers
    fields = http_tests.EnrollmentHTTPTests.fields
    pending = http_tests.EnrollmentHTTPTests.pending

    async def asyncSetUp(self):
        await pg_tests.TOTPPostgresTests.asyncSetUp(self)
        environment = regressions.WebAuthenticationTests().staging_environment()
        environment.update({signup_http.ENABLED_ENV:'1','CAREERTRELLIS_DATA_STORE':'postgres'})
        patcher = patch.dict(os.environ,environment)
        patcher.start(); self.addCleanup(patcher.stop)
        self.adapter.redirect_uri = http_tests.signup_tests.ORIGIN+'/auth/callback'
        runtime = signup_http.SignupHTTP(self.adapter,key=Fernet.generate_key(),
            logout_return_to=http_tests.signup_tests.ORIGIN+'/',static_dir=config.BASE_DIR/'static')
        self.enroll_store.coordination = runtime.coordination
        runtime.enrollment_http = EnrollmentHTTP(runtime,self.enrollment)
        self.runtime = runtime
        previous = getattr(app.app.state,'signup_runtime',None)
        app.app.state.signup_runtime = runtime
        self.addCleanup(setattr,app.app.state,'signup_runtime',previous)
        a = self.actor.authorization
        identity = VerifiedTokens(a.subject,self.actor.session_id,self.codec.generation,
            self.http.token(sub=a.subject,auth_time=int(time.time())-10),'synthetic-refresh',
            int(time.time())+300,a.provider_binding)
        self.fresh_cookies = [self.codec.issue(identity,a.local_id,epoch=a.epoch,incarnation=a.incarnation)]
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app.app),
            base_url=http_tests.signup_tests.ORIGIN,headers={
                app.TRUSTED_PROXY_TOKEN_HEADER:environment['CAREERTRELLIS_TRUSTED_PROXY_TOKEN'],
                'Origin':http_tests.signup_tests.ORIGIN})
        self.addAsyncCleanup(self.client.aclose)

    async def test_browser_to_real_database_completes_and_preserves_profile(self):
        self.query("UPDATE careertrellis.profiles SET name='Synthetic preserved profile' WHERE owner_user_id=:id",
                   id=self.actor.authorization.local_id)
        before = self.query('SELECT jsonb_agg(to_jsonb(p)) FROM careertrellis.profiles p')
        self.assertIsNotNone(before)
        await http_tests.EnrollmentHTTPTests.test_browser_submits_origin_without_referrer_and_completes(self)
        self.assertEqual('complete',self.query("SELECT record->>'state' FROM careertrellis_admission.totp_attempts"))
        self.assertIsNone(self.query("SELECT record->>'sealed_secret' FROM careertrellis_admission.totp_attempts"))
        self.assertEqual(before,self.query('SELECT jsonb_agg(to_jsonb(p)) FROM careertrellis.profiles p'))
        for path in ('/api/version','/'):
            self.assertEqual(200,(await self.client.get(path,headers=self.headers())).status_code)

    async def test_database_disable_between_form_and_submit_prevents_create(self):
        ready = await self.client.get(BASE,headers=self.headers())
        self.assertEqual(200,ready.status_code)
        self.query('UPDATE careertrellis.app_users SET disabled_at=clock_timestamp() WHERE id=:id',
                   id=self.actor.authorization.local_id)
        response = await self.client.post(BASE+'/start',headers=self.headers(),data=self.fields(ready))
        self.assertEqual(403,response.status_code)
        self.assertEqual(0,self.factor_provider.creates)
        self.assertEqual(0,self.query('SELECT count(*) FROM careertrellis_admission.totp_attempts'))

    async def test_cleanup_then_browser_revisit_cannot_retry_or_reveal_secret(self):
        pending = await self.pending()
        self.query("UPDATE careertrellis_admission.totp_attempts SET record=jsonb_set(record,'{deadline}',to_jsonb(extract(epoch FROM clock_timestamp())::bigint-1))")
        self.assertEqual({'expired_secrets_scrubbed':1},await scrub_expired(self.migration))
        revisit = await self.client.get(BASE,headers=self.headers())
        self.assertEqual(200,revisit.status_code)
        self.assertNotIn(pg_tests.SECRET,revisit.text)
        self.assertNotIn('Add authenticator</button>',revisit.text)
        stale = await self.client.post(BASE+'/verify',headers=self.headers(),data={**self.fields(pending),'code':'123456'})
        self.assertEqual(403,stale.status_code)
        self.assertEqual(0,self.factor_provider.verifications)
        self.assertEqual(1,self.factor_provider.creates)

    async def test_opt_in_factory_with_distinct_key_closes_without_provider_calls(self):
        settings=SimpleNamespace(client_id=self.provider.settings.client_id,
            api_key=self.provider.settings.client_secret,cookie_password=Fernet.generate_key().decode(),
            owner_workos_user_id='user_synthetic_owner',redirect_uri=self.adapter.redirect_uri,
            logout_return_to=http_tests.signup_tests.ORIGIN+'/')
        values={'CAREERTRELLIS_AUTH_V2_'+key:value for key,value in {
            'ISSUER':self.provider.settings.issuer,'GENERATION':self.codec.generation,
            'KEY_ID':'synthetic-key','SESSION_SECONDS':'3600',
            'ENVELOPE_KEY':Fernet.generate_key().decode(),'FLOW_KEY':Fernet.generate_key().decode(),
            'TOTP_ENABLED':'1','TOTP_KEY':Fernet.generate_key().decode()}.items()}
        def url(name=None):
            role='careertrellis_admission_broker' if name else 'careertrellis_runtime'
            return self.fixture.url.set(username=role,password='synthetic-integration-only')
        with patch.dict(os.environ,values),patch.object(signup_http,'database_url_from_environment',side_effect=url), \
                patch('httpx.AsyncClient.send',side_effect=AssertionError('No provider calls permitted')):
            runtime=await signup_http.create_runtime(settings,config.BASE_DIR/'static')
        try:
            self.assertIsNotNone(runtime.enrollment_http)
            self.assertFalse(runtime.enrollment_provider.client.is_closed)
        finally:
            await runtime.aclose()
        self.assertTrue(runtime.enrollment_provider.client.is_closed)

    async def test_db_host_cleanup_detects_locked_remainder_and_preserves_slot(self):
        from sqlalchemy import text
        await self.pending()
        self.query("UPDATE careertrellis_admission.totp_attempts SET record=jsonb_set(record,'{deadline}',to_jsonb(extract(epoch FROM clock_timestamp())::bigint-1))")
        def query(sql):
            raw=self.fixture.admin.raw_connection()
            try:
                cursor=raw.cursor(); cursor.execute(sql); result=None
                while True:
                    if cursor.description:
                        rows=cursor.fetchall()
                        if rows and isinstance(rows[-1][0],dict): result=rows[-1][0]
                    if not cursor.nextset(): break
                cursor.close()
                return result
            finally:
                raw.close()
        with self.fixture.admin.begin() as locked:
            locked.execute(text('SELECT account_id FROM careertrellis_admission.totp_attempts FOR UPDATE'))
            self.assertEqual({'result':'backlog','scrubbed':0},totp_cleanup.scrub(SimpleNamespace(query=query)))
        self.assertEqual({'result':'ok','scrubbed':1},totp_cleanup.scrub(SimpleNamespace(query=query)))
        self.assertEqual(1,self.query('SELECT count(*) FROM careertrellis_admission.totp_attempts'))
        self.assertIsNone(self.query("SELECT record->>'sealed_secret' FROM careertrellis_admission.totp_attempts"))


if __name__=='__main__':
    unittest.main()
