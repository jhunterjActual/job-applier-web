"""Synthetic protected construction: no credentials, database or provider calls."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy.engine import make_url
import recovery_runtime as runtime


def files():
    prefix = runtime.PREFIX
    auth = {prefix + 'ENABLED': '1', prefix + 'SESSION_SECONDS': '3600',
            prefix + 'ISSUER': 'https://api.workos.com/user_management/client_example123',
            prefix + 'GENERATION': 'test-generation', prefix + 'KEY_ID': 'test-key',
            'CAREERTRELLIS_ADMISSION_DATABASE_URL':
                'postgresql+psycopg://careertrellis_admission_broker:synthetic@10.70.0.2/careertrellis'}
    staging = {'WORKOS_CLIENT_ID': 'client_example123', 'WORKOS_API_KEY': 'synthetic-only',
               'CAREERTRELLIS_AUTH_MODE': 'workos', 'CAREERTRELLIS_IDENTITY_STORE': 'postgres'}
    encode = lambda value: ''.join(k + '=' + v + '\n' for k, v in value.items()).encode()
    result = {name: b'' for name in runtime.FILES}
    result.update({'staging.env': encode(staging), 'auth-v2.env': encode(auth),
        'admission-web.json': json.dumps(dict(issuer=auth[prefix + 'ISSUER'],
            generation='test-generation', key_id='test-key', session_seconds=3600)).encode()})
    return result


URL = make_url('postgresql+psycopg://careertrellis_migration:synthetic@10.70.0.2/careertrellis')


class ConfigurationTests(unittest.TestCase):
    def load(self, values):
        with patch.object(runtime.os, 'name', 'posix'), \
                patch.object(runtime.os, 'geteuid', return_value=0, create=True), \
                patch.object(runtime.ops, 'read_protected', side_effect=lambda p: values[p.name]), \
                patch.object(runtime.ops, 'migration_connection_url', return_value=URL):
            return runtime.configuration()

    def test_existing_protected_configuration(self):
        settings, url, raw = self.load(files())
        self.assertEqual('client_example123', settings.client_id)
        self.assertEqual(URL, url)
        self.assertEqual(set(runtime.FILES), set(raw))

    def test_refuses_disabled_or_divergent_configuration(self):
        for field, replacement in (('auth-v2.env', b'CAREERTRELLIS_AUTH_V2_ENABLED=0\n'),
                                   ('admission-web.json', b'{}'),
                                   ('object-storage.env', b'WORKOS_API_KEY=override\n')):
            with self.subTest(field=field):
                values = files()
                values[field] = replacement
                with self.assertRaises(runtime.RecoveryProofUncertain):
                    self.load(values)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_requires_schema_binding_and_active_v2_policy(self):
        for observations in ((True, '20261005_0023', 'b', True),
                             (True, '20260907_0014', 'b', True),
                             (True, '20261005_0023', 'other', True),
                             (True, '20261005_0023', 'b', False)):
            with self.subTest(observations=observations):
                connection = SimpleNamespace(exec_driver_sql=AsyncMock(),
                    scalar=AsyncMock(side_effect=observations))
                context = AsyncMock()
                context.__aenter__.return_value = connection
                engine = SimpleNamespace(echo=False, pool=SimpleNamespace(echo=False),
                    sync_engine=SimpleNamespace(hide_parameters=True,
                        logger=SimpleNamespace(isEnabledFor=lambda level: False)),
                    begin=Mock(return_value=context))
                if observations == (True, '20261005_0023', 'b', True):
                    await runtime.preflight(engine, 'b')
                else:
                    with self.assertRaises(runtime.RecoveryProofUncertain):
                        await runtime.preflight(engine, 'b')

    async def test_closes_every_resource_on_success_and_caller_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                engine = SimpleNamespace(dispose=AsyncMock())
                settings = SimpleNamespace(binding='b' * 64)
                clients = [SimpleNamespace(aclose=AsyncMock()) for _ in range(3)]
                with patch.object(runtime, 'configuration', return_value=(settings, URL, {})), \
                        patch.object(runtime, 'create_async_engine', return_value=engine) as factory, \
                        patch.object(runtime, 'preflight', new=AsyncMock()) as check, \
                        patch.object(runtime, 'WorkOSRecoverySessions', return_value=clients[0]), \
                        patch.object(runtime, 'WorkOSRemoval', return_value=clients[1]), \
                        patch.object(runtime, 'WorkOSTransportCandidate', return_value=clients[2]), \
                        patch.object(runtime, 'PostgreSQLRecoveryHoldStore'), \
                        patch.object(runtime, 'PostgreSQLRecoveryReleaseStore'):
                    try:
                        async with runtime.open_recovery_runtime() as value:
                            self.assertIs(value.workflow.sessions, clients[0])
                            if fail:
                                raise ValueError('private diagnostic must not escape')
                    except runtime.RecoveryProofUncertain as exc:
                        self.assertTrue(fail)
                        self.assertNotIn('private diagnostic', str(exc))
                    check.assert_awaited_once_with(engine, 'b' * 64)
                    self.assertTrue(factory.call_args.kwargs['hide_parameters'])
                    self.assertIs(factory.call_args.kwargs['poolclass'], runtime.NullPool)
                engine.dispose.assert_awaited_once()
                for client in clients:
                    client.aclose.assert_awaited_once()

    async def test_failed_preflight_never_constructs_provider(self):
        engine = SimpleNamespace(dispose=AsyncMock())
        with patch.object(runtime, 'configuration', return_value=(SimpleNamespace(binding='b'), URL, {})), \
                patch.object(runtime, 'create_async_engine', return_value=engine), \
                patch.object(runtime, 'preflight', new=AsyncMock(side_effect=ValueError('private'))), \
                patch.object(runtime, 'WorkOSRecoverySessions') as provider:
            with self.assertRaises(runtime.RecoveryProofUncertain):
                async with runtime.open_recovery_runtime():
                    self.fail('preflight failure yielded runtime')
            provider.assert_not_called()
        engine.dispose.assert_awaited_once()

    async def test_partial_client_construction_is_cleaned(self):
        engine = SimpleNamespace(dispose=AsyncMock())
        client = SimpleNamespace(aclose=AsyncMock())
        with patch.object(runtime, 'configuration', return_value=(SimpleNamespace(binding='b'), URL, {})), \
                patch.object(runtime, 'create_async_engine', return_value=engine), \
                patch.object(runtime, 'preflight', new=AsyncMock()), \
                patch.object(runtime, 'WorkOSRecoverySessions', return_value=client), \
                patch.object(runtime, 'WorkOSRemoval', side_effect=ValueError('private')):
            with self.assertRaises(runtime.RecoveryProofUncertain):
                async with runtime.open_recovery_runtime():
                    self.fail('construction failure yielded runtime')
        engine.dispose.assert_awaited_once()
        client.aclose.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
