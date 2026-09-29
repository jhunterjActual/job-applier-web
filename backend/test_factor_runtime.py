"""Offline explicit factor factory flags, readiness and owned-resource lifecycle."""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from cryptography.fernet import Fernet
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine

import signup_http
from enrollment_disposition import AttributableEnrollmentCoordinator
from enrollment_disposition_postgres import PostgreSQLEnrollmentDispositionStore
from factor_removal import SessionEndingRemovalCoordinator
from factor_removal_http import RemovalHTTP
from factor_removal_postgres import PostgreSQLRemovalStore
from web_auth import AuthenticationConfigurationError


class FactorRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings=SimpleNamespace(client_id='client_synthetic_factory',api_key='synthetic-provider-key',
            cookie_password=Fernet.generate_key().decode(),owner_workos_user_id='user_synthetic_owner',
            redirect_uri='https://synthetic.invalid/auth/callback',logout_return_to='https://synthetic.invalid/')
        self.environment={'CAREERTRELLIS_AUTH_V2_'+key:value for key,value in {
            'ENABLED':'1','ISSUER':'https://api.workos.com/user_management/'+self.settings.client_id,
            'GENERATION':'synthetic-generation','ENVELOPE_KEY':Fernet.generate_key().decode(),
            'FLOW_KEY':Fernet.generate_key().decode(),'KEY_ID':'synthetic-key','SESSION_SECONDS':'3600',
            'TOTP_KEY':Fernet.generate_key().decode()}.items()}
        self.engines=[]; self.providers=[]; self.factor_providers=[]; self.removal_providers=[]; self.queries=[]
        self.ready={'schema':'20260929_0020','functions_ready':True,'grants_ready':True}

        def url(name=None):
            principal='careertrellis_admission_broker' if name else 'careertrellis_runtime'
            return URL.create('postgresql+psycopg',username=principal,password='synthetic',
                              host='127.0.0.1',database='synthetic_factor_factory')
        def engine(url,**kwargs):
            value=Mock(spec=AsyncEngine); value.url=url; value.dispose=AsyncMock()
            self.engines.append(value); return value
        def provider(settings):
            value=Mock(settings=settings,aclose=AsyncMock()); self.providers.append(value); return value
        def factor(key):
            value=Mock(aclose=AsyncMock()); self.factor_providers.append(value); return value
        def removal(settings):
            value=Mock(binding=settings.binding,aclose=AsyncMock()); self.removal_providers.append(value); return value
        async def query(engine,sql,*args,**kwargs):
            self.queries.append((engine,sql,kwargs))
            if sql.strip()=='SELECT session_user': return engine.url.username
            if kwargs.get('mapping') is True: return self.ready
            raise AssertionError('Unexpected synthetic factory query')
        patches=[patch.dict(os.environ,self.environment,clear=True),
            patch.object(signup_http,'database_url_from_environment',side_effect=url),
            patch.object(signup_http,'create_async_engine',side_effect=engine),
            patch.object(signup_http.AsyncAdmissionStore,'_query',new=AsyncMock(side_effect=query)),
            patch.object(signup_http.AdmissionAdapterCandidate,'context',new=AsyncMock()),
            patch.object(signup_http,'WorkOSTransportCandidate',side_effect=provider),
            patch('workos_totp.WorkOSTOTP',side_effect=factor),
            patch('workos_removal.WorkOSRemoval',side_effect=removal),
            patch('httpx.AsyncClient.send',side_effect=AssertionError('No network in factory tests'))]
        for item in patches:
            item.start(); self.addCleanup(item.stop)

    def flags(self,enrollment,removal):
        os.environ['CAREERTRELLIS_AUTH_V2_TOTP_ENABLED']=enrollment
        os.environ['CAREERTRELLIS_AUTH_V2_REMOVAL_ENABLED']=removal

    async def build(self):
        return await signup_http.create_runtime(self.settings,Path(__file__).parent/'static')

    def assert_closed(self):
        for resource in self.providers+self.factor_providers+self.removal_providers:
            resource.aclose.assert_awaited_once()
        for engine in self.engines: engine.dispose.assert_awaited_once()

    async def test_default_disabled_factors_do_not_probe_factor_schema(self):
        runtime=await self.build()
        self.assertIsNone(runtime.enrollment_http); self.assertIsNone(runtime.removal_http)
        self.assertEqual([],self.factor_providers); self.assertEqual([],self.removal_providers)
        self.assertEqual(2,len(self.queries))
        await runtime.aclose(); self.assert_closed()

    async def test_disabled_boundary_creates_no_resources(self):
        os.environ['CAREERTRELLIS_AUTH_V2_ENABLED']='0'
        self.flags('1','1')
        self.assertIsNone(await signup_http.create_runtime(None,Path('.')))
        self.assertEqual([],self.engines); self.assertEqual([],self.providers)

    async def test_enrollment_only_uses_attributable_coordinator_and_store(self):
        self.flags('1','0'); runtime=await self.build()
        self.assertIsInstance(runtime.enrollment_http.coordinator,AttributableEnrollmentCoordinator)
        self.assertIsInstance(runtime.enrollment_http.coordinator.store,PostgreSQLEnrollmentDispositionStore)
        self.assertIsNone(runtime.removal_http); self.assertEqual([],self.removal_providers)
        self.assertEqual(1,len([q for q in self.queries if q[2].get('mapping')]))
        await runtime.aclose(); self.assert_closed()

    async def test_both_flags_install_exact_session_ending_components(self):
        self.flags('1','1'); runtime=await self.build()
        self.assertIsInstance(runtime.removal_http,RemovalHTTP)
        self.assertIsInstance(runtime.removal_http.coordinator,SessionEndingRemovalCoordinator)
        self.assertIsInstance(runtime.removal_http.coordinator.store,PostgreSQLRemovalStore)
        self.assertIs(runtime.removal_http.enrollment_store,runtime.enrollment_http.coordinator.store)
        self.assertEqual(runtime.adapter.codec.provider_binding,runtime.removal_provider.binding)
        self.assertEqual(1,len(self.factor_providers)); self.assertEqual(1,len(self.removal_providers))
        await runtime.aclose(); self.assert_closed()

    async def test_removal_requires_enrollment_and_strict_flags(self):
        for enrollment,removal in (('0','1'),('1','yes'),('1',''),('1','true'),('yes','0')):
            self.flags(enrollment,removal)
            with self.subTest(enrollment=enrollment,removal=removal):
                with self.assertRaises(AuthenticationConfigurationError): await self.build()
        self.assertEqual([],self.factor_providers); self.assertEqual([],self.removal_providers)

    async def test_stale_missing_or_malformed_readiness_fails_before_factor_construction(self):
        self.flags('1','1')
        for ready in (None,True,{}, {'schema':'20260928_0018','functions_ready':True,'grants_ready':True},
                      {'schema':'20260929_0019','functions_ready':True,'grants_ready':True},
                      {'schema':'20260929_0020','functions_ready':False,'grants_ready':True},
                      {'schema':'20260929_0020','functions_ready':True,'grants_ready':False},
                      {'schema':'20260929_0020','functions_ready':1,'grants_ready':True},
                      {'schema':'20260929_0020','functions_ready':True}):
            self.ready=ready
            with self.subTest(ready=ready):
                with self.assertRaises(AuthenticationConfigurationError): await self.build()
        self.assertEqual([],self.factor_providers); self.assertEqual([],self.removal_providers)
        self.assert_closed()

    async def test_readiness_query_covers_exact_contract_and_runtime_grants(self):
        self.flags('1','1'); runtime=await self.build()
        queries=[q for q in self.queries if q[2].get('mapping')]
        self.assertEqual(1,len(queries)); engine,sql,_=queries[0]
        self.assertEqual('careertrellis_runtime',engine.url.username)
        for fragment in ('alembic_version','totp_request','totp_finish_verified','factor_removal_request','has_function_privilege'):
            self.assertIn(fragment,sql)
        await runtime.aclose(); self.assert_closed()

    async def test_failure_after_all_providers_constructed_closes_every_resource(self):
        self.flags('1','1')
        with patch('factor_removal_http.RemovalHTTP',side_effect=ValueError('private setup detail')):
            with self.assertRaises(AuthenticationConfigurationError) as caught: await self.build()
        self.assertNotIn('private',str(caught.exception)); self.assertEqual(1,len(self.removal_providers))
        self.assert_closed()

    async def test_cancelled_construction_preserves_cancellation_and_closes_resources(self):
        self.flags('1','1')
        with patch('factor_removal_http.RemovalHTTP',side_effect=asyncio.CancelledError()):
            with self.assertRaises(asyncio.CancelledError): await self.build()
        self.assert_closed()

    async def test_provider_constructor_failure_closes_preceding_resources(self):
        self.flags('1','1')
        with patch('workos_removal.WorkOSRemoval',side_effect=ValueError('synthetic constructor failure')):
            with self.assertRaises(AuthenticationConfigurationError): await self.build()
        self.assertEqual(1,len(self.factor_providers)); self.assertEqual([],self.removal_providers)
        self.assert_closed()


if __name__=='__main__': unittest.main()
