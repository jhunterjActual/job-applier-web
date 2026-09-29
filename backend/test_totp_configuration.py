"""Offline opt-in/key separation and bounded cleanup receipts."""
import os
import unittest
from unittest.mock import Mock, patch, AsyncMock

from cryptography.fernet import Fernet
import signup_http
import totp_cleanup
import admission_operations as ops


class ConfigurationTests(unittest.TestCase):
    def test_disabled_ignores_unconfigured_key_and_invalid_flag_rejects(self):
        with patch.dict(os.environ,{'CAREERTRELLIS_AUTH_V2_TOTP_ENABLED':'0',
                                   'CAREERTRELLIS_AUTH_V2_TOTP_KEY':'not-a-key'}):
            self.assertIsNone(signup_http.enrollment_key(()))
        with patch.dict(os.environ,{'CAREERTRELLIS_AUTH_V2_TOTP_ENABLED':'yes'}):
            with self.assertRaises(ValueError): signup_http.enrollment_key(())

    def test_enabled_requires_fourth_distinct_key(self):
        existing = [Fernet.generate_key().decode() for _ in range(3)]
        new = Fernet.generate_key().decode()
        with patch.dict(os.environ,{'CAREERTRELLIS_AUTH_V2_TOTP_ENABLED':'1'}):
            for value in ('','bad',*existing):
                with patch.dict(os.environ,{'CAREERTRELLIS_AUTH_V2_TOTP_KEY':value}):
                    with self.assertRaises(ValueError): signup_http.enrollment_key(existing)
            with patch.dict(os.environ,{'CAREERTRELLIS_AUTH_V2_TOTP_KEY':new}):
                self.assertEqual(new,signup_http.enrollment_key(existing))

    def test_cleanup_locked_remainder_never_reports_success(self):
        db=Mock(); db.query.return_value={'scrubbed':0,'remaining':True}
        self.assertEqual({'result':'backlog','scrubbed':0},totp_cleanup.scrub(db))
        self.assertEqual(1,db.query.call_count)

    def test_cleanup_rounds_and_invalid_aggregate(self):
        db=Mock(); db.query.return_value={'scrubbed':500,'remaining':True}
        self.assertEqual({'result':'backlog','scrubbed':2000},totp_cleanup.scrub(db))
        self.assertEqual(4,db.query.call_count)
        for result in ({'scrubbed':True,'remaining':False},{'scrubbed':0,'remaining':0}):
            db.query.return_value=result
            with self.assertRaises(ops.OperationError): totp_cleanup.scrub(db)
        db.query.return_value={'scrubbed':1,'remaining':False}
        self.assertEqual({'result':'ok','scrubbed':1},totp_cleanup.scrub(db))


class ClosureTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_closes_enrollment_provider_with_existing_resources(self):
        runtime=object.__new__(signup_http.SignupHTTP)
        runtime.adapter=Mock(); runtime.adapter.provider.aclose=AsyncMock()
        runtime.enrollment_provider=Mock(); runtime.enrollment_provider.aclose=AsyncMock()
        runtime.removal_provider=Mock(); runtime.removal_provider.aclose=AsyncMock()
        engine=Mock(); engine.dispose=AsyncMock(); runtime.engines=[engine]
        await runtime.aclose()
        runtime.enrollment_provider.aclose.assert_awaited_once()
        runtime.removal_provider.aclose.assert_awaited_once()
        runtime.adapter.provider.aclose.assert_awaited_once()
        engine.dispose.assert_awaited_once()
