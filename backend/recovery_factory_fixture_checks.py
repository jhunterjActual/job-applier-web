"""Actual schema0023 web factory with synthetic settings and no provider I/O."""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import text

import signup_http
from test_provider_session_candidate import SETTINGS


async def check_factory(fixture):
    with fixture.admin.begin() as connection:
        policy = connection.execute(text('SELECT maintenance,adapter_ready,minimum_session_version,realm '
                                         'FROM careertrellis_admission.policy')).one()
        binding = connection.scalar(text('SELECT binding FROM careertrellis_admission.identity_provider'))
        connection.execute(text('UPDATE careertrellis_admission.policy '
            'SET maintenance=false,adapter_ready=true,minimum_session_version=2,realm=:r'), {'r': SETTINGS.generation})
        connection.execute(text('UPDATE careertrellis_admission.identity_provider SET binding=:b'), {'b': SETTINGS.binding})
    settings = SimpleNamespace(client_id=SETTINGS.client_id, api_key=SETTINGS.client_secret,
        cookie_password=Fernet.generate_key().decode(), owner_workos_user_id='user_synthetic_owner',
        redirect_uri='https://app.example.test/auth/callback', logout_return_to='https://app.example.test/')
    environment = {'CAREERTRELLIS_AUTH_V2_' + name: value for name, value in {
        'ENABLED': '1', 'ISSUER': SETTINGS.issuer, 'GENERATION': SETTINGS.generation,
        'KEY_ID': 'synthetic-factory', 'SESSION_SECONDS': '3600',
        'ENVELOPE_KEY': Fernet.generate_key().decode(), 'FLOW_KEY': Fernet.generate_key().decode(),
        'TOTP_ENABLED': '1', 'TOTP_KEY': Fernet.generate_key().decode(),
        'REMOVAL_ENABLED': '1', 'RECOVERY_ENABLED': '1'}.items()}

    def url(name=None):
        role = 'careertrellis_admission_broker' if name else 'careertrellis_runtime'
        return fixture.url.set(username=role, password='synthetic-integration-only')

    runtime = None
    try:
        with patch.dict(os.environ, environment), \
                patch.object(signup_http, 'database_url_from_environment', side_effect=url), \
                patch('httpx.AsyncClient.send', side_effect=AssertionError('Synthetic factory must not call provider')) as send:
            runtime = await signup_http.create_runtime(settings, Path(__file__).parent / 'static')
            assert runtime.enrollment_http is not None and runtime.removal_http is not None
            assert runtime.recovery_http is not None
            assert runtime.recovery_http.credentials.provider is runtime.removal_provider
            send.assert_not_called()
        await runtime.aclose()
        assert runtime.enrollment_provider.client.is_closed
        assert runtime.removal_provider.client.is_closed
        assert runtime.adapter.provider._client.is_closed
        return 2
    finally:
        if runtime is not None:
            # aclose is idempotent on the owned HTTP clients and SQLAlchemy engines.
            await runtime.aclose()
        with fixture.admin.begin() as connection:
            connection.execute(text('UPDATE careertrellis_admission.policy SET maintenance=:m,'
                'adapter_ready=:a,minimum_session_version=:v,realm=:r'),
                {'m': policy[0], 'a': policy[1], 'v': policy[2], 'r': policy[3]})
            connection.execute(text('UPDATE careertrellis_admission.identity_provider SET binding=:b'), {'b': binding})
