"""Synthetic preparation boundaries; no host operations or provider calls."""
from contextlib import ExitStack
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


def load(name):
    path=Path(__file__).resolve().parents[1]/'deploy/scripts'/name
    spec=importlib.util.spec_from_file_location(name.replace('-','_'),path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def archive(data):
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w') as output:
        for name,content in data.items():
            item=tarfile.TarInfo(name); item.mode=0o644; item.size=len(content)
            output.addfile(item,io.BytesIO(content))
    raw=buffer.getvalue()
    return raw,hashlib.sha256(raw).hexdigest()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.module=load('prepare-session-application.py')
        self.data=dict.fromkeys(self.module.REQUIRED,b'# synthetic\n')
        self.data['backend/version.py']=b'APP_BUILD = "20260916.74"\n'

    def test_exact_payload_and_digest(self):
        raw,digest=archive(self.data)
        self.assertEqual(set(self.data),set(self.module.archive_contents(raw,digest)))
        with self.assertRaises(RuntimeError): self.module.archive_contents(raw,'0'*64)

    def test_predecessor_build_is_rejected(self):
        self.data['backend/version.py']=b'APP_BUILD = "20260910.72"\n'
        raw,digest=archive(self.data)
        with self.assertRaises(RuntimeError): self.module.archive_contents(raw,digest)

    def test_missing_enrollment_schema_or_runtime_is_rejected(self):
        for name in ('backend/migrations/versions/20260915_0016_totp_enrollment.sql',
                'backend/totp_http.py','backend/static/totp-enrollment.js',
                'backend/test_identity_session_postgres.py',
                'backend/test_provider_session_postgres.py',
                'backend/test_stored_objects.py','backend/test_session_epoch_http.py',
                'backend/signup_http.py',
                'backend/signup_prototype/integration_fixture.py'):
            data = {key:value for key,value in self.data.items() if key != name}
            raw,digest = archive(data)
            with self.assertRaises(RuntimeError): self.module.archive_contents(raw,digest)

    def test_protected_and_ambiguous_names_rejected(self):
        for name in ('../outside','/absolute','backend/../outside','backend/venv/a',
                '.codex/config.toml','data/private','backend/.env','application-preparation/complete.json',
                'backend\\outside','backend/./outside'):
            with self.subTest(name=name):
                raw,digest=archive({**self.data,name:b'forbidden'})
                with self.assertRaises(RuntimeError): self.module.archive_contents(raw,digest)

    def test_symlinks_and_writable_archive_members_rejected(self):
        for mode,kind in ((0o666,tarfile.REGTYPE),(0o644,tarfile.SYMTYPE)):
            buffer=io.BytesIO()
            with tarfile.open(fileobj=buffer,mode='w') as output:
                item=tarfile.TarInfo('unsafe'); item.mode=mode; item.type=kind; item.linkname='/outside'
                output.addfile(item)
            raw=buffer.getvalue()
            with self.assertRaises(RuntimeError): self.module.archive_contents(raw,hashlib.sha256(raw).hexdigest())


@unittest.skipUnless(os.name=='posix','root-owned Linux fixture')
class PreparationTests(unittest.TestCase):
    def setUp(self):
        if os.geteuid()!=0: self.skipTest('root-owned fixture')
        self.module=load('prepare-session-application.py')
        self.util=load('install-handoff-ops.py')
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/')))
        self.releases=self.root/'releases'; self.releases.mkdir()
        self.transfer=self.root/'transfer'; self.transfer.mkdir()
        self.browsers=self.root/'browsers'; self.browsers.mkdir(); (self.browsers/'binary').write_bytes(b'synthetic browser')
        self.base=self.releases/self.module.OPS/'backend'; self.base.mkdir(parents=True)
        self.runtime=self.base/'venv'; (self.runtime/'bin').mkdir(parents=True)
        (self.runtime/'bin/python').write_bytes(b'\x7fELFsynthetic'); (self.runtime/'pyvenv.cfg').write_text(str(self.runtime))
        self.data=dict.fromkeys(self.module.REQUIRED,b'# synthetic\n')
        self.data['backend/version.py']=b'APP_BUILD = "20260916.74"\n'
        for name in ('requirements.in','requirements.txt','dependency_lock.py'): (self.base/name).write_bytes(self.data['backend/'+name])
        self.commit='c'*40; self.target=self.releases/self.commit
        for name,value in (('RELEASES',self.releases),('TRANSFER',self.transfer),('BROWSERS',self.browsers)):
            self.stack.enter_context(patch.object(self.module,name,value))
        self.stack.enter_context(patch.object(self.module.os,'uname',return_value=SimpleNamespace(nodename='careertrellis-staging-web-1')))
        import pwd
        self.stack.enter_context(patch.object(pwd,'getpwnam',return_value=SimpleNamespace(pw_gid=0)))
        self.stack.enter_context(patch.object(self.util,'snapshot',return_value={'fixed':'unchanged'}))
        self.commands=self.stack.enter_context(patch.object(self.util,'command',return_value=b''))

    def payload(self):
        raw,digest=archive(self.data)
        path=self.transfer/('careertrellis-application-'+self.commit+'.tar'); path.write_bytes(raw); path.chmod(0o600)
        return digest

    def test_private_copy_and_no_activation_or_dependency_install(self):
        digest=self.payload(); original=self.util.runtime_manifest(self.runtime)
        self.assertEqual('private_runtime_prepared_not_activated',self.module.prepare(self.commit,digest,self.util))
        receipt=json.loads((self.target/'application-preparation/complete.json').read_bytes())
        self.assertEqual('pending',receipt['native_application_tests'])
        self.assertEqual(original,self.util.runtime_manifest(self.runtime))
        calls=[call.args[0] for call in self.commands.call_args_list]
        self.assertEqual(2,len(calls)); self.assertEqual(['-m','pip','check'],calls[0][-3:])
        self.assertEqual('/usr/sbin/runuser',calls[1][0])
        with self.assertRaises(RuntimeError): self.module.prepare(self.commit,digest,self.util)

    def test_dependency_drift_and_bad_digest_refuse_before_creation(self):
        digest=self.payload()
        with self.assertRaises(RuntimeError): self.module.prepare(self.commit,'0'*64,self.util)
        (self.base/'requirements.txt').write_bytes(b'drift')
        with self.assertRaises(RuntimeError): self.module.prepare(self.commit,digest,self.util)
        self.assertFalse(self.target.exists()); self.commands.assert_not_called()

    def test_storage_guard_reserves_runtime_fixture_and_free_floor_before_creation(self):
        digest=self.payload()
        budget=self.module.storage_budget({name:(raw,False) for name,raw in self.data.items()},
            self.runtime,10**12)
        self.assertGreater(budget['runtime_copy_bytes'],0)
        self.assertEqual(2*sum(map(len,self.data.values()))+budget['runtime_copy_bytes']+
            self.module.REHEARSAL_ALLOWANCE,budget['planned_additional_bytes'])
        threshold=budget['planned_additional_bytes']+self.module.SPACE_FLOOR
        with patch.object(self.module.shutil,'disk_usage',return_value=SimpleNamespace(free=threshold-1)):
            with self.assertRaises(RuntimeError): self.module.prepare(self.commit,digest,self.util)
        self.assertFalse(self.target.exists()); self.commands.assert_not_called()
        with patch.object(self.module.shutil,'disk_usage',return_value=SimpleNamespace(free=threshold)):
            self.module.prepare(self.commit,digest,self.util)
        receipt=json.loads((self.target/'application-preparation/started.json').read_bytes())
        self.assertEqual(threshold,receipt['storage_budget']['free_before'])

    def test_separate_fixture_filesystem_shortage_refuses_before_creation(self):
        digest=self.payload()
        def space(path):
            return SimpleNamespace(free=3000000000 if path==self.transfer else 10**12)
        with patch.object(self.module.shutil,'disk_usage',side_effect=space):
            with self.assertRaises(RuntimeError): self.module.prepare(self.commit,digest,self.util)
        self.assertFalse(self.target.exists()); self.commands.assert_not_called()

    def test_snapshot_drift_retains_incomplete_attempt(self):
        digest=self.payload()
        with patch.object(self.util,'snapshot',side_effect=[{'fixed':1},{'fixed':2}]):
            with self.assertRaises(RuntimeError): self.module.prepare(self.commit,digest,self.util)
        self.assertTrue((self.target/'application-preparation/started.json').exists())
        self.assertFalse((self.target/'application-preparation/complete.json').exists())

    def test_service_user_failure_retains_attempt_without_completion(self):
        digest=self.payload(); self.commands.side_effect=[b'',RuntimeError('synthetic refusal')]
        with self.assertRaises(RuntimeError): self.module.prepare(self.commit,digest,self.util)
        self.assertTrue((self.target/'application-preparation/started.json').exists())
        self.assertFalse((self.target/'application-preparation/complete.json').exists())

    def test_restrictive_umask_keeps_target_traversable(self):
        digest=self.payload(); previous=os.umask(0o077)
        try: self.module.prepare(self.commit,digest,self.util)
        finally: os.umask(previous)
        self.assertEqual(0o750,self.target.stat().st_mode&0o777)
        self.assertEqual(0o750,(self.target/'backend').stat().st_mode&0o777)

    def test_browser_internal_link_is_recorded_and_external_link_refused(self):
        link=self.browsers/'link'; link.symlink_to('binary')
        self.assertEqual('link',self.module.browser_manifest(self.browsers)['link'][0])
        link.unlink(); link.symlink_to(self.base/'requirements.txt')
        with self.assertRaises(RuntimeError): self.module.browser_manifest(self.browsers)


@unittest.skipUnless(os.name=='posix','root-owned Linux fixture')
class NativeVerificationTests(unittest.TestCase):
    def setUp(self):
        if os.geteuid()!=0: self.skipTest('root-owned fixture')
        self.module=load('verify-prepared-session-application.py')
        self.util=load('install-handoff-ops.py')
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/')))
        self.commit='c'*40; self.release=self.root/self.commit
        self.evidence=self.release/'application-preparation'; self.evidence.mkdir(parents=True,mode=0o700)
        (self.release/'backend').mkdir(); (self.release/'backend/version.py').write_bytes(b'# synthetic')
        (self.release/'backend/version.py').chmod(0o640)
        self.util.record(self.evidence/'source-manifest.json',{'backend/version.py':hashlib.sha256(b'# synthetic').hexdigest()})
        self.util.record(self.evidence/'complete.json',{'application':self.commit,'ops':self.module.OPS,
            'build':'20260916.74','schema':'20260915_0016','phase':'private_runtime_prepared_not_activated'})
        self.stack.enter_context(patch.object(self.module,'RELEASES',self.root))
        self.stack.enter_context(patch.object(self.module,'FIXTURES',self.root))
        self.proc=self.root/'proc'; self.proc.mkdir()
        self.stack.enter_context(patch.object(self.module,'PROC',self.proc))
        self.stack.enter_context(patch.object(self.module.os,'uname',return_value=SimpleNamespace(nodename='careertrellis-staging-web-1')))
        self.stack.enter_context(patch.object(self.module.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=0,pw_gid=0)))
        fixture=self.root/'fixture'
        def make_fixture(**kwargs): fixture.mkdir(); return str(fixture)
        self.stack.enter_context(patch.object(self.module.tempfile,'mkdtemp',side_effect=make_fixture))
        self.stack.enter_context(patch.object(self.util,'snapshot',return_value={'fixed':'same'}))
        self.stack.enter_context(patch.object(self.util,'runtime_manifest',return_value={'runtime':'same'}))
        self.process=Mock(returncode=0,pid=987654)
        self.result={'tests':450,'failures':0,'errors':0,'skipped':[
            'test_p0_regressions.DependencyLockTests.test_batch_launcher_discovers_bootstrap_python_with_quoted_paths']}
        self.process.communicate.return_value=(json.dumps(self.result).encode(),b'private diagnostics')
        self.launch=self.stack.enter_context(patch.object(self.module.subprocess,'Popen',return_value=self.process))

    def test_native_source_copy_private_network_clean_environment_and_receipt(self):
        self.assertEqual('native_synthetic_tests_passed',self.module.verify(self.commit,self.util))
        call=self.launch.call_args
        self.assertEqual(['/usr/bin/unshare','--net','--','/usr/bin/python3','-I','-S','-B','-c'],call.args[0][:8])
        self.assertEqual(self.module.NAMESPACE_BOOTSTRAP,call.args[0][8])
        self.assertIn("'link','set','dev','lo','up'",call.args[0][8])
        self.assertIn("'runuser','-u','careertrellis','--'",call.args[0][8])
        self.assertIn("'/usr/bin/env','-i',*environment",call.args[0][8])
        self.assertIn("assert Path.home()==root.parent/'data'",call.args[0][-1])
        self.assertTrue(call.kwargs['start_new_session'])
        self.assertFalse(any('WORKOS' in name or 'DATABASE_URL' in name for name in call.kwargs['env']))
        self.assertTrue((self.root/'fixture/data/uploads').is_dir())
        self.assertTrue((self.root/'fixture/data/output').is_dir())
        self.assertEqual('/var/lib/careertrellis/shared/ms-playwright',call.kwargs['env']['PLAYWRIGHT_BROWSERS_PATH'])
        self.assertTrue((self.evidence/'native-tests-result.json').exists())
        raw=(self.evidence/'native-application-tests.json').read_bytes()
        self.assertNotIn(b'private diagnostics',raw)
        with self.assertRaises(RuntimeError): self.module.verify(self.commit,self.util)

    def test_space_is_rechecked_before_native_fixture_or_receipt_creation(self):
        with patch.object(self.module.shutil,'disk_usage',return_value=SimpleNamespace(free=3000000000)):
            with self.assertRaises(RuntimeError): self.module.verify(self.commit,self.util)
        self.launch.assert_not_called()
        self.assertFalse((self.root/'fixture').exists())
        self.assertFalse((self.evidence/'native-tests-started.json').exists())

    def test_unexpected_skip_does_not_clear_gate(self):
        self.result['skipped'].append('unexpected')
        self.process.communicate.return_value=(json.dumps(self.result).encode(),b'')
        with self.assertRaises(RuntimeError): self.module.verify(self.commit,self.util)
        self.assertFalse((self.evidence/'native-application-tests.json').exists())
        self.assertTrue((self.evidence/'native-tests-started.json').exists())

    def test_timeout_terminates_private_group_and_retains_attempt(self):
        import subprocess
        self.process.communicate.side_effect=subprocess.TimeoutExpired('synthetic',180)
        with patch.object(self.module.os,'killpg') as kill:
            with self.assertRaises(subprocess.TimeoutExpired): self.module.verify(self.commit,self.util)
        kill.assert_called_once_with(self.process.pid,self.module.signal.SIGKILL)
        self.process.wait.assert_called_once_with(timeout=10)
        self.assertFalse((self.evidence/'native-application-tests.json').exists())

    def test_any_existing_native_attempt_refuses_without_launch(self):
        for name in ('native-tests-started.json','native-tests-result.json',
                'native-tests-corrected-started.json','native-application-tests.json'):
            with self.subTest(name=name):
                path=self.evidence/name
                path.write_bytes(b'prior immutable receipt')
                try:
                    with self.assertRaises(RuntimeError): self.module.verify(self.commit,self.util)
                    self.launch.assert_not_called()
                    self.assertEqual(b'prior immutable receipt',path.read_bytes())
                finally: path.unlink()

    def test_predecessor_completion_receipt_refuses_without_launch(self):
        path=self.evidence/'complete.json'; value=json.loads(path.read_bytes())
        value['build']='20260910.72'; path.write_text(json.dumps(value))
        with self.assertRaises(RuntimeError): self.module.verify(self.commit,self.util)
        self.launch.assert_not_called()

    def test_native_suite_includes_entry_and_signed_token_modules(self):
        self.assertIn('test_signup_http',self.module.TESTS)
        self.assertIn('test_provider_session_candidate',self.module.TESTS)

if __name__=='__main__': unittest.main()
