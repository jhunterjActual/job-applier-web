"""Pinned fallback preparation guards; no live files or host operations."""
from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from test_application_preparation import load


class Fallback79PreparationTests(unittest.TestCase):
    def setUp(self):
        self.rules=load('fallback79-application-contract.py')
        self.prepare=load('prepare-fallback79-application.py')

    def test_exact_source_binding_rejects_main_or_changed_archive(self):
        r=self.rules
        r.bindings(r.COMMIT,r.ARCHIVE_SHA256)
        for commit,digest in (('a'*40,r.ARCHIVE_SHA256),(r.COMMIT,'0'*64),('main',r.ARCHIVE_SHA256),
                (r.COMMIT,'d5f28d8813ee792ebdc9ec63fb8d21801a0e43a5ac8edf993d505ab796420ee6')):
            with self.assertRaises(RuntimeError): r.bindings(commit,digest)
        self.assertEqual('20260930.79',r.BUILD)
        self.assertEqual('20260929_0021',r.SCHEMA)

    def test_archive_size_and_digest_are_checked_before_unpacking(self):
        with patch.object(self.prepare,'contract',return_value=self.rules), \
                patch.object(self.prepare.tarfile,'open') as unpack:
            with self.assertRaises(RuntimeError):
                self.prepare.archive_contents(b'synthetic',self.rules.ARCHIVE_SHA256)
            with self.assertRaises(RuntimeError):
                self.prepare.archive_contents(b'synthetic','0'*64)
        unpack.assert_not_called()

    def test_only_existing_78_baseline_is_accepted(self):
        r=self.rules
        r.baseline(r.CURRENT,b'APP_BUILD="20260928.78"')
        for current,version in ((r.COMMIT,b'APP_BUILD="20260928.78"'),
                (r.CURRENT,b'APP_BUILD="20260930.79"'),
                (r.CURRENT,b'APP_BUILD="20260930.80"')):
            with self.assertRaises(RuntimeError): r.baseline(current,version)

    def test_native_policy_is_fallback_specific_and_exact(self):
        r=self.rules
        policy=dict(application=r.COMMIT,operations='a'*40,archive_sha256=r.ARCHIVE_SHA256,
            build=r.BUILD,schema=r.SCHEMA,tests=list(r.TESTS),allowed_skips=[])
        r.policy(policy,r.COMMIT,'a'*40,r.ARCHIVE_SHA256)
        for key,value in (('build','20260930.80'),('application','b'*40),('tests',[])):
            bad=deepcopy(policy); bad[key]=value
            with self.assertRaises(RuntimeError): r.policy(bad,r.COMMIT,'a'*40,r.ARCHIVE_SHA256)
        good=dict(tests=1,failures=0,errors=0,skipped=[],failed_tests=[],error_tests=[])
        r.result(good,policy)
        with self.assertRaises(RuntimeError): r.result({**good,'errors':False},policy)

    def test_fallback_inventory_excludes_later_rollout_features(self):
        r=self.rules
        self.assertIn('session_v2_transition',r.MODULES)
        self.assertIn('test_disabled_search_cleanup',r.TESTS)
        self.assertNotIn('identity_selection_barrier',r.MODULES)
        self.assertNotIn('schema21_rollout_contract',r.MODULES)

    def test_pinned_contract_and_main_helper_builds_remain_distinct(self):
        scripts=Path(__file__).resolve().parents[1]/'deploy/scripts'
        digest=hashlib.sha256((scripts/'fallback79-application-contract.py').read_bytes()).hexdigest()
        for name in ('prepare-fallback79-application.py','verify-prepared-fallback79-application.py'):
            source=(scripts/name).read_text()
            self.assertIn(digest,source)
            self.assertIn('fallback79-application-preparation',source)
        self.assertEqual('20260930.80',load('schema21-application-contract.py').BUILD)
