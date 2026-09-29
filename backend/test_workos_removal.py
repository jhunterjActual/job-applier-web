"""MockTransport only: strict removal response semantics and bounded dispatch."""
import asyncio
import json
import unittest

import httpx

from factor_removal import RemovalUncertain
from workos_removal import WorkOSRemoval
from workos_transport_candidate import ProviderSettings


SUBJECT='user_synthetic_owner'
FACTOR='auth_factor_synthetic_owner'
CHALLENGE='auth_challenge_synthetic_owner'
OP='11111111-1111-4111-8111-111111111111'


def response(value=None,*,status=200,raw=None,headers=None):
    return httpx.Response(status,headers=headers or {'Content-Type':'application/json'},
        stream=httpx.ByteStream(raw if raw is not None else json.dumps(value).encode()))


def challenge():
    return {'object':'authentication_challenge','id':CHALLENGE,'authentication_factor_id':FACTOR,
            'expires_at':'2030-01-01T00:00:00Z'}


def page(*,items=None,after=None):
    return {'object':'list','data':items if items is not None else
        [{'object':'authentication_factor','id':FACTOR,'type':'totp','user_id':SUBJECT}],
        'list_metadata':{'after':after}}


class WorkOSRemovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests=[]; self.responses=[]
        self.settings=ProviderSettings('client_synthetic','synthetic_key',
            'https://api.workos.com/user_management/client_synthetic','synthetic_generation')
        async def handle(request):
            self.requests.append(request)
            value=self.responses.pop(0)
            if isinstance(value,BaseException): raise value
            return value
        self.adapter=WorkOSRemoval(self.settings,transport=httpx.MockTransport(handle))

    async def asyncTearDown(self): await self.adapter.aclose()

    async def test_membership_is_presence_not_verified_and_absence_is_complete(self):
        self.responses=[response(page()),response(page(items=[]))]
        found=await self.adapter.observe(SUBJECT,FACTOR)
        self.assertEqual(found.disposition,'present'); self.assertEqual(found.kind,'totp')
        self.assertTrue(found.complete); self.assertEqual(found.provider_binding,self.settings.binding)
        absent=await self.adapter.observe(SUBJECT,FACTOR)
        self.assertEqual(absent.disposition,'absent'); self.assertIsNone(absent.kind)
        request=self.requests[0]
        self.assertEqual(str(request.url),'https://api.workos.com/user_management/users/'+SUBJECT+'/auth_factors?limit=100')
        self.assertEqual(request.headers['Authorization'],'Bearer synthetic_key')

    async def test_complete_pagination_and_unknown_factor_type(self):
        other={'object':'authentication_factor','id':'auth_factor_other_owner','type':'totp'}
        self.responses=[response(page(items=[other],after=other['id'])),response(page())]
        self.assertEqual((await self.adapter.observe(SUBJECT,FACTOR)).disposition,'present')
        self.assertEqual(self.requests[1].url.params['after'],other['id'])
        unknown=page(); unknown['data'][0]['type']='future_kind'
        self.responses=[response(unknown)]
        self.assertEqual((await self.adapter.observe(SUBJECT,FACTOR)).disposition,'unknown')

    async def test_challenge_and_verify_bind_exact_ids_and_boolean(self):
        self.responses=[response(challenge()),response({'challenge':challenge(),'valid':True}),
                        response({'challenge':challenge(),'valid':False})]
        issued=await self.adapter.challenge(SUBJECT,FACTOR)
        self.assertEqual(issued.challenge,CHALLENGE); self.assertEqual(issued.subject,SUBJECT)
        self.assertEqual(issued.provider_binding,self.settings.binding)
        self.assertTrue((await self.adapter.verify(SUBJECT,FACTOR,CHALLENGE,'123456')).valid)
        self.assertFalse((await self.adapter.verify(SUBJECT,FACTOR,CHALLENGE,'654321')).valid)
        self.assertEqual(self.requests[0].url.path,'/auth/factors/'+FACTOR+'/challenge')
        self.assertEqual(self.requests[1].url.path,'/auth/challenges/'+CHALLENGE+'/verify')
        self.assertEqual(json.loads(self.requests[1].content),{'code':'123456'})

    async def test_delete_empty_response_locally_binds_operation(self):
        self.responses=[response(raw=b'\n')]
        receipt=await self.adapter.delete(SUBJECT,FACTOR,OP)
        self.assertEqual(receipt.operation,OP); self.assertEqual(receipt.disposition,'deleted')
        self.assertEqual(self.requests[0].method,'DELETE')
        self.assertEqual(self.requests[0].url.path,'/auth/factors/'+FACTOR)
        self.assertNotIn('idempotency-key',self.requests[0].headers)

    async def test_invalid_inputs_never_dispatch(self):
        for subject,factor in (('user_bad/../x',FACTOR),(SUBJECT,'auth_factor_bad?x'),(None,FACTOR)):
            with self.assertRaises(RemovalUncertain): await self.adapter.observe(subject,factor)
        for code in ('12345','1234567','１２３４５６',123456):
            with self.assertRaises(RemovalUncertain): await self.adapter.verify(SUBJECT,FACTOR,CHALLENGE,code)
        with self.assertRaises(RemovalUncertain): await self.adapter.verify(SUBJECT,FACTOR,'wrong','123456')
        with self.assertRaises(RemovalUncertain): await self.adapter.delete(SUBJECT,FACTOR,'wrong')
        with self.assertRaises(RemovalUncertain): WorkOSRemoval('arbitrary binding')
        self.assertEqual(self.requests,[])

    async def test_wrong_response_bindings_and_nonboolean_fail(self):
        for field,value in (('id','auth_challenge_other_owner'),('authentication_factor_id','auth_factor_other_owner'),
                            ('user_id','user_other_owner'),('object','other')):
            altered=challenge(); altered[field]=value
            self.responses=[response({'challenge':altered,'valid':True})]
            with self.assertRaises(RemovalUncertain): await self.adapter.verify(SUBJECT,FACTOR,CHALLENGE,'123456')
        for valid in (1,'true',None):
            self.responses=[response({'challenge':challenge(),'valid':valid})]
            with self.assertRaises(RemovalUncertain): await self.adapter.verify(SUBJECT,FACTOR,CHALLENGE,'123456')

    async def test_missing_or_mismatched_ownership_and_partial_pages_fail(self):
        wrong=page(); wrong['data'][0]['user_id']='user_other_owner'
        missing=page(); del missing['list_metadata']['after']
        repeated=page(); repeated['data']*=2
        for value in (wrong,missing,repeated,{'data':[]}):
            self.responses=[response(value)]
            with self.assertRaises(RemovalUncertain): await self.adapter.observe(SUBJECT,FACTOR)
        self.responses=[response(page(after=FACTOR)),response(page(items=[],after=FACTOR))]
        with self.assertRaises(RemovalUncertain): await self.adapter.observe(SUBJECT,FACTOR)
        self.responses=[response(page(after=FACTOR)),response({},status=500)]
        with self.assertRaises(RemovalUncertain): await self.adapter.observe(SUBJECT,FACTOR)

    async def test_pagination_cap_never_claims_absence(self):
        self.responses=[response(page(items=[],after='auth_factor_cursor_'+str(n))) for n in range(10)]
        with self.assertRaises(RemovalUncertain): await self.adapter.observe(SUBJECT,FACTOR)
        self.assertEqual(len(self.requests),10)

    async def test_status_redirects_rate_limit_and_network_errors_never_retry(self):
        for status in (201,204,302,400,404,429,500):
            count=len(self.requests)
            self.responses=[response(challenge(),status=status,headers={'Location':'https://elsewhere.invalid'})]
            with self.assertRaises(RemovalUncertain): await self.adapter.challenge(SUBJECT,FACTOR)
            self.assertEqual(len(self.requests),count+1)
        self.responses=[httpx.ReadTimeout('private transport detail')]
        with self.assertRaises(RemovalUncertain) as caught: await self.adapter.challenge(SUBJECT,FACTOR)
        self.assertNotIn('private',str(caught.exception)); self.assertEqual(self.adapter._inflight,0)

    async def test_json_encoding_size_and_duplicate_members_rejected(self):
        for value in (response(raw=b'{}',headers={'Content-Type':'text/html'}),
                      response(raw=b'{}',headers={'Content-Type':'application/json','Content-Encoding':'gzip'}),
                      response(raw=b'X'*65537),response(raw=b'{"valid":true,"valid":false}'),
                      response(raw=b'{"valid":NaN}'),response(raw=b'[]')):
            self.responses=[value]
            with self.assertRaises(RemovalUncertain): await self.adapter.challenge(SUBJECT,FACTOR)

    async def test_delete_rejects_generic200_nonempty_body_and_other_status(self):
        for value in (response({'deleted':True}),response(raw=b'<html>ok</html>'),
                      response(raw=b' '*129),response(raw=b'',status=204),response(raw=b'',status=404)):
            self.responses=[value]
            with self.assertRaises(RemovalUncertain): await self.adapter.delete(SUBJECT,FACTOR,OP)

    async def test_challenge_requires_timezone_and_valid_expiry(self):
        for expiry in ('2030-01-01T00:00:00','unknown',None,123):
            value=challenge(); value['expires_at']=expiry; self.responses=[response(value)]
            with self.assertRaises(RemovalUncertain): await self.adapter.challenge(SUBJECT,FACTOR)

    async def test_cancellation_propagates_and_capacity_fails_closed(self):
        self.responses=[asyncio.CancelledError()]
        with self.assertRaises(asyncio.CancelledError): await self.adapter.challenge(SUBJECT,FACTOR)
        self.assertEqual(self.adapter._inflight,0)
        self.adapter._inflight=8; count=len(self.requests)
        with self.assertRaises(RemovalUncertain): await self.adapter.challenge(SUBJECT,FACTOR)
        self.assertEqual(len(self.requests),count)
        self.adapter._inflight=0


if __name__=='__main__': unittest.main()
