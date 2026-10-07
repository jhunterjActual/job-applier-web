"""Real middleware/threadpool context propagation, synthetic signed identities."""
import asyncio
import time
import unittest
from uuid import uuid4

import app
import signup_http
from session_authorization import current
from session_authorization import SessionAuthorizationRejected
from test_signup_http import SignupHTTPTests
from test_provider_session_candidate import SETTINGS, GENERATION, SUBJECT, SID
from workos_transport_candidate import VerifiedTokens


class SessionEpochHTTPTests(unittest.IsolatedAsyncioTestCase):
    setUpClass=classmethod(SignupHTTPTests.setUpClass.__func__)

    async def asyncSetUp(self):
        await SignupHTTPTests.asyncSetUp(self)
        self.codec.require_epoch=True
        async def mapping(session):
            row=self.store.accounts.get(session.subject)
            if row and (row['id'],row['epoch'],row['incarnation'])==(session.local_id,session.epoch,session.incarnation):
                return row
        self.store.session_mapping=mapping
        self.cookies=[]
        for uid,subject in ((10,SUBJECT),(11,'user_synthetic_second')):
            incarnation=str(uuid4())
            self.store.accounts[subject]={'id':uid,'role':'user','epoch':1,'incarnation':incarnation}
            identity=VerifiedTokens(subject,SID,GENERATION,self.http.token(sub=subject),'synthetic-refresh',int(time.time())+300,SETTINGS.binding)
            self.cookies.append(self.codec.issue(identity,uid,epoch=1,incarnation=incarnation))
        routes=list(app.app.router.routes)
        self.addCleanup(setattr,app.app.router,'routes',routes)
        def probe():
            authorization=current.get()
            time.sleep(.02)
            return {'id':authorization.local_id,'epoch':authorization.epoch}
        def failure():
            self.assertIsNotNone(current.get())
            raise ValueError('synthetic endpoint failure')
        def stale():
            raise SessionAuthorizationRejected()
        app.app.add_api_route('/api/_synthetic_epoch_probe',probe,methods=['GET'])
        app.app.add_api_route('/api/_synthetic_epoch_failure',failure,methods=['GET'])
        app.app.add_api_route('/api/_synthetic_epoch_stale',stale,methods=['GET'])

    def headers(self,index):
        return {'Cookie':signup_http.COOKIE_NAME+'='+self.cookies[index]}

    async def test_concurrent_threadpool_requests_keep_distinct_authorizations(self):
        results=await asyncio.gather(*(self.client.get('/api/_synthetic_epoch_probe',headers=self.headers(i)) for i in (0,1)))
        self.assertEqual([{'id':10,'epoch':1},{'id':11,'epoch':1}],[r.json() for r in results])
        self.assertIsNone(current.get())
        self.assertEqual(200,(await self.client.get('/api/version',headers=self.headers(0))).status_code)
        self.assertEqual(200,(await self.client.get('/',headers=self.headers(0))).status_code)

    async def test_endpoint_exception_does_not_leak_request_context(self):
        with self.assertRaises(ValueError):
            await self.client.get('/api/_synthetic_epoch_failure',headers=self.headers(0))
        self.assertIsNone(current.get())

    async def test_stale_protected_request_clears_cookie_and_returns_unauthorized(self):
        response=await self.client.get('/api/_synthetic_epoch_stale',headers=self.headers(0))
        self.assertEqual(401,response.status_code)
        self.assertTrue(any(signup_http.COOKIE_NAME+'=' in value and 'Max-Age=0' in value for value in response.headers.get_list('set-cookie')))
        self.assertIsNone(current.get())
        response=await self.client.get('/api/_synthetic_epoch_probe',headers=self.headers(1))
        self.assertEqual({'id':11,'epoch':1},response.json())
        self.assertIsNone(current.get())
