"""Synthetic new-release contracts; no host writes or deployment operations."""
from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_application_preparation import load, archive


class Schema21PreparationTests(unittest.TestCase):
    def setUp(self):
        self.prepare=load('prepare-schema21-application.py')
        self.rules=load('schema21-application-contract.py')
        self.real_contract=self.prepare.contract
        patched=patch.object(self.prepare,'contract',return_value=self.rules)
        patched.start(); self.addCleanup(patched.stop)
        self.files=dict.fromkeys(self.prepare.REQUIRED+self.rules.REQUIRED,b'# synthetic\n')
        self.files['backend/version.py']=b'APP_BUILD = "20260930.80"\n'
        # Preserve the exact historical filenames required by preparation.
        for name in list(self.files):
            if name.startswith('backend/migrations/versions/'):
                del self.files[name]
        previous=None
        revisions=('20260907_0014',)+self.rules.CHAIN
        historical={'20260909_0015':'identity_sessions','20260915_0016':'totp_enrollment'}
        for revision in revisions:
            name='backend/migrations/versions/'+revision+'_'+historical.get(revision,'synthetic')
            self.files[name+'.py']=('revision='+repr(revision)+'\ndown_revision='+repr(previous)+
                '\nbranch_labels=None\ndepends_on=None\n').encode()
            self.files[name+'.sql']=b'-- synthetic migration\n'
            previous=revision

    def check(self,files=None):
        raw,digest=archive(self.files if files is None else files)
        return self.prepare.archive_contents(raw,digest)

    def test_complete_new_build_and_schema_chain(self):
        self.assertEqual(set(self.files),set(self.check()))
        self.assertEqual('20260929_0021',self.rules.SCHEMA)

    def test_78_snapshot_requires_exact_current_and_rejects_changed_protected_state(self):
        # snapshot() models Linux host paths, including literal POSIX dictionary
        # keys. Keep its synthetic paths POSIX even when this test runs on Windows.
        class HostPath(PurePosixPath):
            def resolve(self): return expected
            def exists(self): return False
            def is_symlink(self): return False
        for filename in ('schema21-application-contract.py','fallback79-application-contract.py'):
            rules=load(filename)
            utility=Mock()
            changed=[False]
            def protected(path,**kwargs):
                if path.name=='version.py': return b'APP_BUILD="20260928.78"'
                if path.name=='auth-v2.env': return b'CAREERTRELLIS_AUTH_V2_ENABLED=0\n'
                return b'changed' if changed[0] and path.name=='staging.env' else b'unchanged'
            utility.protected.side_effect=protected
            utility.command.side_effect=lambda argv: (b'postgres-r2-authoritative\n'
                if argv[0].endswith('careertrellis-authority') else
                b'ActiveState=active\nMainPID=7\nNRestarts=0\n')
            expected=HostPath('/opt/careertrellis/staging/releases/cafad5b48a579adc80695519f0f8b7d39f621cd5')
            with self.subTest(contract=filename),patch('pathlib.Path',HostPath):
                before=rules.snapshot(utility)
                self.assertEqual(20,len(before['files']))
                self.assertEqual(3,len(before['services']))
                rules.require(rules.snapshot(utility)==before)
                changed[0]=True
                with self.assertRaises(RuntimeError): rules.require(rules.snapshot(utility)==before)
                with patch.object(HostPath,'resolve',return_value=HostPath('/wrong/current')):
                    with self.assertRaises(RuntimeError): rules.snapshot(utility)
                with patch.object(HostPath,'is_symlink',return_value=True):
                    with self.assertRaises(RuntimeError): rules.snapshot(utility)
            utility.snapshot.assert_not_called()

    def test_inventory_includes_factor_source_packet_requirements(self):
        import factor_release_packet
        self.assertEqual(factor_release_packet.SCHEMA,self.rules.SCHEMA)
        self.assertTrue(set(factor_release_packet.REQUIRED).issubset(
            set(self.prepare.REQUIRED+self.rules.REQUIRED)))

    def test_wrong_digest_old_build_and_missing_feature_rejected(self):
        raw,digest=archive(self.files)
        with self.assertRaises(RuntimeError): self.prepare.archive_contents(raw,'0'*64)
        for name in ('backend/factor_removal.py','backend/identity_selection_barrier.py',
                     'backend/test_disabled_search_cleanup.py','backend/static/factor-removal.js'):
            with self.subTest(name=name):
                files={key:value for key,value in self.files.items() if key!=name}
                with self.assertRaises(RuntimeError): self.check(files)
        files={**self.files,'backend/version.py':b'APP_BUILD="20260928.78"\n'}
        with self.assertRaises(RuntimeError): self.check(files)

    def test_traversal_private_and_evidence_paths_rejected(self):
        for name in ('../escape','/escape','backend/venv/private','backend/.env',
                     'C:/escape','schema21-application-preparation/complete.json'):
            with self.subTest(name=name),self.assertRaises(RuntimeError):
                self.check({**self.files,name:b'forbidden'})

    def test_sibling_missing_sql_or_changed_ancestry_rejected(self):
        files={**self.files,'backend/migrations/versions/other.py':
            b'revision="other"\ndown_revision="20260929_0020"\n'}
        with self.assertRaises(RuntimeError): self.check(files)
        files=dict(self.files)
        del files['backend/migrations/versions/20260929_0021_synthetic.sql']
        with self.assertRaises(RuntimeError): self.check(files)
        files={**self.files,'backend/migrations/versions/20260929_0021_synthetic.py':
            b'revision="20260929_0021"\ndown_revision="20260928_0018"\n'}
        with self.assertRaises(RuntimeError): self.check(files)

    def reviewed(self):
        return dict(application='a'*40,operations='b'*40,archive_sha256='c'*64,
            build=self.rules.BUILD,schema=self.rules.SCHEMA,tests=list(self.rules.TESTS),
            allowed_skips=['test_postgres_rls.PostgreSQLTenantIsolationTests.test_example'])

    def test_review_policy_binds_exact_source_and_module_inventory(self):
        policy=self.reviewed()
        self.rules.policy(policy,'a'*40,'b'*40,'c'*64)
        for key in ('application','operations','archive_sha256','build','schema','tests'):
            altered=deepcopy(policy); altered[key]=[] if key=='tests' else 'changed'
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                self.rules.policy(altered,'a'*40,'b'*40,'c'*64)

    def test_verifiers_read_policy_from_protected_evidence_before_fixture_effects(self):
        class PolicyReadReached(Exception): pass
        for prefix,build in (('schema21','20260930.80'),('fallback79','20260930.79')):
            # No account lookup is reached before the protected policy read.
            # Supply the POSIX import on Windows without pretending to do one.
            pwd_stub=SimpleNamespace(getpwnam=Mock(side_effect=AssertionError('unexpected account lookup')))
            with patch.dict(sys.modules,{'pwd':pwd_stub}):
                verifier=load('verify-prepared-'+prefix+'-application.py')
            commit,operations,digest='a'*40,'b'*40,'c'*64
            evidence=verifier.RELEASES/commit/(prefix+'-application-preparation')
            expected=evidence/'reviewed-test-policy.json'
            rules=Mock(CURRENT='d'*40)
            utility=Mock()
            complete=dict(application=commit,phase='private_runtime_prepared_not_activated',
                ops=operations,runtime_ops=verifier.OPS,archive_sha256=digest,
                build=build,schema='20260929_0021')
            def protected(path,**kwargs):
                if path.name=='version.py': return b'APP_BUILD="20260928.78"'
                if path==evidence/'complete.json': return json.dumps(complete).encode()
                if path==evidence/'source-manifest.json': return b'{}'
                self.assertEqual(expected,path)
                self.assertEqual({'modes':(0o600,)},kwargs)
                self.assertNotIn(verifier.FIXTURES,path.parents)
                self.assertFalse(path.name.startswith('native-'))
                raise PolicyReadReached()
            utility.protected.side_effect=protected
            with self.subTest(prefix=prefix), \
                    patch.object(verifier.os,'geteuid',return_value=0,create=True), \
                    patch.object(verifier.os,'uname',return_value=SimpleNamespace(
                        nodename='careertrellis-staging-web-1'),create=True), \
                    patch.object(verifier,'contract',return_value=rules), \
                    patch.object(Path,'resolve',return_value=verifier.RELEASES/rules.CURRENT), \
                    patch.object(Path,'glob',return_value=iter(())), \
                    patch.object(verifier.tempfile,'mkdtemp') as make_fixture, \
                    patch.object(verifier.subprocess,'Popen') as start_process:
                with self.assertRaises(PolicyReadReached):
                    verifier.verify(commit,digest,operations,utility)
                self.assertEqual(Path('/var/tmp'),verifier.FIXTURES)
                make_fixture.assert_not_called(); start_process.assert_not_called()
                utility.record.assert_not_called(); utility.runtime_manifest.assert_not_called()

    def test_unexpected_skip_failure_or_empty_suite_cannot_pass(self):
        policy=self.reviewed()
        good=dict(tests=100,failures=0,errors=0,skipped=policy['allowed_skips'],failed_tests=[],error_tests=[])
        self.rules.result(good,policy)
        for change in ({'tests':0},{'failures':1},{'errors':1},{'failures':False},{'errors':False},
                       {'unexpected':True},{'skipped':[]},
                       {'skipped':policy['allowed_skips']*2}):
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                self.rules.result({**good,**change},policy)

    def test_class_setup_skip_ids_are_bound_to_fixed_modules(self):
        policy=self.reviewed()
        policy['allowed_skips']=['setUpClass (test_postgres_rls.PostgreSQLTenantIsolationTests)']
        self.rules.policy(policy,'a'*40,'b'*40,'c'*64)
        for skips in (policy['allowed_skips']*2,['setUpClass (test_unknown.SomeTests)'],
                      ['tearDownClass (test_postgres_rls.SomeTests)']):
            with self.assertRaises(RuntimeError):
                self.rules.policy({**policy,'allowed_skips':skips},'a'*40,'b'*40,'c'*64)

    def test_branch_dependency_and_misnamed_migration_rejected(self):
        name='backend/migrations/versions/20260929_0021_synthetic.py'
        for token in (b'branch_labels',b'depends_on'):
            files={**self.files,name:self.files[name].replace(token+b'=None',token+b'="other"')}
            with self.assertRaises(RuntimeError): self.check(files)
        files=dict(self.files); files['backend/migrations/versions/wrong.py']=files.pop(name)
        with self.assertRaises(RuntimeError): self.check(files)

    def test_protected_contract_loader_refuses_unsafe_bytes_and_metadata(self):
        if os.name!='posix' or os.geteuid()!=0:
            self.skipTest('native root protected contract fixture required')
        original=Path(self.prepare.__file__).with_name('schema21-application-contract.py').read_bytes()
        with tempfile.TemporaryDirectory(dir='/') as temporary:
            root=Path(temporary)
            target=root/'schema21-application-contract.py'
            target.write_bytes(original); target.chmod(0o600)
            with patch.object(self.prepare,'__file__',str(root/'prepare-schema21-application.py')):
                self.assertEqual(self.rules.SCHEMA,self.real_contract().SCHEMA)
                target.chmod(0o666)
                with self.assertRaises(RuntimeError): self.real_contract()
                target.chmod(0o600)
                target.write_bytes(original+b'\nraise AssertionError("must not execute")\n')
                with self.assertRaises(RuntimeError): self.real_contract()
                target.write_bytes(original)
                alias=root/'alias'; os.link(target,alias)
                with self.assertRaises(RuntimeError): self.real_contract()
                alias.unlink()
                os.chown(target,1000,0)
                with self.assertRaises(RuntimeError): self.real_contract()
                os.chown(target,0,0)
                target.rename(alias); target.symlink_to(alias)
                with self.assertRaises(OSError): self.real_contract()
