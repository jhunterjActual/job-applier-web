"""Offline enforcement at both worker entrypoints, including disabled mode."""
import contextlib
import io
import os
import unittest
from unittest.mock import patch

import config
import worker
import account_erasure_worker


class WorkerAdmissionBoundaryTests(unittest.TestCase):
    def test_all_web_names_fail_before_any_worker_side_effect(self):
        names = ['CAREERTRELLIS_ADMISSION_DATABASE_URL'] + [
            'CAREERTRELLIS_AUTH_V2_' + suffix for suffix in (
                'ENABLED', 'ISSUER', 'GENERATION', 'AUDIENCE', 'KEY_ID',
                'ENVELOPE_KEY', 'FLOW_KEY', 'SESSION_SECONDS', 'FUTURE_SECRET', '')]
        for module, enable in ((worker, 'CAREERTRELLIS_SEARCH_WORKER_ENABLED'),
                (account_erasure_worker, 'CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED')):
            for name in names:
                for value in ('synthetic-secret-do-not-disclose', '', ' ', '0'):
                    for enabled in ('true', 'false'):
                        with self.subTest(worker=module.__name__, name=name, value=bool(value), enabled=enabled):
                            output = io.StringIO()
                            with patch.dict(os.environ, {'CAREERTRELLIS_ENVIRONMENT': 'staging', enable: enabled, name: value}, clear=True), \
                                    patch.object(module, 'runtime_postgres_engine') as engine, \
                                    patch.object(module, '_write_heartbeat') as heartbeat, \
                                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                                with self.assertRaises(config.RuntimeConfigurationError) as failure:
                                    module.run(once=True)
                                engine.assert_not_called()
                                heartbeat.assert_not_called()
                                self.assertEqual('', output.getvalue())
                                self.assertNotIn('synthetic-secret', str(failure.exception))

    def test_legitimate_minimal_and_provider_settings_remain_valid(self):
        with patch.dict(os.environ, {'CAREERTRELLIS_ENVIRONMENT': 'staging',
                'CAREERTRELLIS_SEARCH_WORKER_ENABLED': 'false',
                'CAREERTRELLIS_PROVIDER_SECRET_KEYRING_B64': 'synthetic-search-owned'}, clear=True):
            config.validate_worker_runtime_configuration()
        with patch.dict(os.environ, {'CAREERTRELLIS_ENVIRONMENT': 'staging'}, clear=True):
            config.validate_account_erasure_worker_configuration()


if __name__ == '__main__':
    unittest.main()
