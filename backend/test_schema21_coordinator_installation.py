"""Synthetic additive installer guards; no host writes or subprocess actions."""
import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


class Schema21CoordinatorInstallationTests(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parent.parent/'deploy/scripts/install-schema21-coordinator.py'
        spec=importlib.util.spec_from_file_location('schema21_installer_test',path)
        self.module=importlib.util.module_from_spec(spec)
        # Import-only portability; no POSIX operation runs in these tests.
        with patch.dict(sys.modules,{name:sys.modules.get(name,ModuleType(name)) for name in ('fcntl','pwd')}):
            spec.loader.exec_module(self.module)

    def test_only_exact_deployed_baseline_and_build(self):
        m=self.module
        m.baseline(m.RELEASES/m.CURRENT,b'APP_BUILD="20260928.78"')
        for current,source in ((m.RELEASES/'another',b'APP_BUILD="20260928.78"'),
                (m.RELEASES/m.CURRENT,b'APP_BUILD="20260930.80"'),
                (m.RELEASES/m.CURRENT,b'APP_BUILD="20260928.78"\nAPP_BUILD="20260928.78"')):
            with self.assertRaises(RuntimeError): m.baseline(current,source)

    def test_distinct_immutable_application_operations_and_retained_runtime(self):
        m=self.module; m.bindings('a'*40,'c'*64,'b'*40)
        for commit,digest,app in (('a'*40,'c'*64,'a'*40),(m.CURRENT,'c'*64,'b'*40),
                ('a'*40,'c'*64,m.RUNTIME),('main','c'*64,'b'*40),('a'*40,'bad','b'*40)):
            with self.assertRaises(RuntimeError): m.bindings(commit,digest,app)
        self.assertEqual('4a7bf3c46d3b401616c7bfaefacb22c7c7ff30ab',m.RUNTIME)
        self.assertEqual('schema21-coordinator-installation',m.EVIDENCE)

    def test_required_barrier_schema21_tests_and_no_application_migrations(self):
        m=self.module
        manifest={**{'backend/'+name+'.py':'x' for name in (*m.REQUIRED,*m.WEB_TESTS)},
                  **dict.fromkeys(m.REQUIRED_FILES,'x')}
        m.source_inventory(manifest)
        for name in ('backend/identity_selection_barrier.py','backend/test_schema21_coordination.py',
                     'backend/session_v2_transition.py','deploy/scripts/install-schema21-coordinator.py'):
            with self.assertRaises(RuntimeError):
                m.source_inventory({key:value for key,value in manifest.items() if key!=name})
        with self.assertRaises(RuntimeError):
            m.source_inventory({**manifest,'backend/migrations/versions/20260929_0021_fake.py':'x'})
