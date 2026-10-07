"""Offline manifest and approval-slot checks; no installed state mutations."""
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import py_compile
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import owner_recovery as recovery
import owner_recovery_reserve_cli as cli


class CliTests(unittest.TestCase):
    def test_duplicate_manifest_keys_refused(self):
        with self.assertRaises(ValueError):cli.strict_json(b'{"version":1,"version":1}')
    def test_manifest_nonfinite_refused(self):
        with self.assertRaises(ValueError):cli.strict_json(b'{"version":NaN}')
    def test_fixed_slot_allowlist(self):
        with self.assertRaises(ValueError):cli.slot('../credential.json',metadata={})
    def test_exact_private_selection_slot_readback(self):
        metadata={'synthetic':'enrollment'};raw=b'private-selection'
        record=cli.record_selection(metadata,raw)
        with patch.object(recovery,'protected',return_value=recovery.encoded(record)) as read:
            self.assertEqual(cli.slot('primary-plan.json',metadata=metadata),raw)
        read.assert_called_once_with(cli.STATE/'primary-plan.json')
    def test_wrong_enrollment_or_selection_bytes_refused(self):
        record=cli.record_selection({'synthetic':'enrollment'},b'original')
        with patch.object(recovery,'protected',return_value=recovery.encoded(record)),self.assertRaises(ValueError):
            cli.slot('approval.json',metadata={'different':'enrollment'})
        record['selection_hex']=b'changed'.hex()
        with patch.object(recovery,'protected',return_value=recovery.encoded(record)),self.assertRaises(ValueError):
            cli.slot('approval.json',metadata={'synthetic':'enrollment'})
    def test_boolean_slot_version_refused(self):
        record=cli.record_selection({},b'original');record['version']=True
        with patch.object(recovery,'protected',return_value=recovery.encoded(record)),self.assertRaises(ValueError):
            cli.slot('approval.json',metadata={})
    def test_noninstalled_source_refused_before_project_loading(self):
        with patch.object(cli.os,'geteuid',create=True,return_value=0),\
             patch.object(cli.os,'uname',create=True,return_value=type('Node',(),{'nodename':'careertrellis-staging-web-1'})()),\
             patch.object(cli,'__file__','/tmp/backend/owner_recovery_reserve_cli.py'),\
             patch.object(cli,'protected',side_effect=AssertionError('must not read')),self.assertRaises(ValueError):
            cli.load_verified()


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.root=cli.BASE/('c'*40)
        self.files={}
        for name in cli.WRAPPER_MODULES:
            self.files[self.root/'backend'/(name+'.py')]=b'MARKER="verified source"\n'
        for name in cli.ESSENTIAL-cli.WRAPPER_MODULES:
            self.files[cli.LIBRARY/'backend'/(name+'.py')]=b'MARKER="pinned library"\n'
        self.manifest={'version':1,'operations':cli.OPS,'wrapper':{},'library':{}}
        for path,raw in self.files.items():
            field='wrapper' if path.is_relative_to(self.root) else 'library'
            self.manifest[field]['backend/'+path.name]=hashlib.sha256(raw).hexdigest()
        self.files[self.root/'source-manifest.json']=json.dumps(self.manifest).encode()
    def loader(self):
        from contextlib import ExitStack
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.dict(sys.modules))
        for name in cli.WRAPPER_MODULES|cli.ESSENTIAL:sys.modules.pop(name,None)
        original=list(sys.meta_path);self.addCleanup(lambda:sys.meta_path.__setitem__(slice(None),original))
        stack.enter_context(patch.object(cli,'__file__',str(self.root/'backend/owner_recovery_reserve_cli.py')))
        stack.enter_context(patch.object(cli.os,'geteuid',create=True,return_value=0))
        stack.enter_context(patch.object(cli.os,'uname',create=True,return_value=type('Node',(),{'nodename':'careertrellis-staging-web-1'})()))
        stack.enter_context(patch.object(cli,'protected',side_effect=lambda path:self.files[path]))
        return cli.load_verified()
    def test_poisoned_importable_bytecode_is_not_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'owner_recovery_reserve_host.py'
            path.write_text('raise RuntimeError("unreviewed bytecode executed")\n')
            py_compile.compile(str(path),cfile=str(path.with_suffix('.pyc')),doraise=True)
            path.unlink()
            with patch.object(sys,'path',[temporary,*sys.path]):
                self.loader()
                module=importlib.import_module('owner_recovery_reserve_host')
                self.assertEqual(module.MARKER,'verified source')
    def test_changed_source_after_manifest_verification_refused(self):
        verify=self.loader()
        self.files[self.root/'backend/owner_recovery_reserve_host.py']=b'MARKER="changed"\n'
        with self.assertRaises(ValueError):verify()
        with self.assertRaises(ValueError):importlib.import_module('owner_recovery_reserve_host')
    def test_closure_digest_is_exact_manifest_bytes(self):
        verify=self.loader()
        raw=self.files[self.root/'source-manifest.json']
        self.assertEqual(verify.closure_sha256,hashlib.sha256(raw).hexdigest())
        self.assertEqual(verify.wrapper_revision,'c'*40)
        self.files[self.root/'source-manifest.json']=raw+b' '
        with self.assertRaises(ValueError):verify()
    def test_omitted_project_dependency_cannot_fall_back(self):
        path=self.root/'backend/owner_recovery_reserve_host.py'
        self.files[path]=b'import reserve_unlisted_dependency\n'
        self.manifest['wrapper']['backend/owner_recovery_reserve_host.py']=hashlib.sha256(self.files[path]).hexdigest()
        self.files[self.root/'source-manifest.json']=json.dumps(self.manifest).encode()
        self.loader()
        actual=Path.exists
        with patch.object(Path,'exists',lambda path:True if path.name=='reserve_unlisted_dependency.py' else actual(path)):
            with self.assertRaises(ValueError):importlib.import_module('owner_recovery_reserve_host')


class RelayEntryTests(unittest.TestCase):
    def setUp(self):
        self.identifier='11111111-1111-4111-8111-111111111111'
        self.relay=SimpleNamespace(peek=Mock(return_value={'status':'idle'}),
            inspect=Mock(return_value={'status':'expired'}),publish=Mock(return_value={'status':'published'}))
        self.verify=Mock(return_value=True)
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules,{'owner_recovery_reserve_relay':self.relay}).start()
        patch.object(cli.os,'isatty',return_value=False).start()
    def test_peek_and_status_return_protocol_without_credential_actions(self):
        self.assertEqual(cli.relay_action(['relay-peek'],self.verify),{'status':'idle'})
        self.assertEqual(cli.relay_action(['relay-status',self.identifier],self.verify),{'status':'expired'})
        self.relay.peek.assert_called_once_with();self.relay.inspect.assert_called_once_with(self.identifier)
        self.assertEqual(self.verify.call_count,4)
    def test_publish_exact_bounded_input_one_call(self):
        observation={'synthetic':'observation'}
        raw=json.dumps({'request_id':self.identifier,'observation':observation}).encode()
        self.assertEqual(cli.relay_action(['relay-publish'],self.verify,stdin=io.BytesIO(raw)),{'status':'published'})
        self.relay.publish.assert_called_once_with(self.identifier,observation)
    def test_invalid_args_and_input_refused_before_publish(self):
        for args,raw in ((['relay-peek','extra'],b''),(['relay-status','bad'],b''),
            (['relay-publish','path'],b''),(['relay-issue'],b''),(['relay-publish'],b'x'*65537),
            (['relay-publish'],b'{"request_id":"x","observation":{},"extra":1}'),
            (['relay-publish'],b'{"request_id":"x","request_id":"y","observation":{}}')):
            with self.subTest(args=args),self.assertRaises(ValueError):
                cli.relay_action(args,self.verify,stdin=io.BytesIO(raw))
        self.relay.publish.assert_not_called()
    def test_tty_or_source_drift_refuses(self):
        with patch.object(cli.os,'isatty',return_value=True),self.assertRaises(ValueError):
            cli.relay_action(['relay-peek'],self.verify)
        self.relay.peek.assert_not_called()
        self.verify.side_effect=[True,False]
        with self.assertRaises(ValueError):cli.relay_action(['relay-peek'],self.verify)
    def test_publish_exception_is_not_retried(self):
        self.relay.publish.side_effect=ValueError('unknown acknowledgement')
        raw=json.dumps({'request_id':self.identifier,'observation':{}}).encode()
        with self.assertRaises(ValueError):cli.relay_action(['relay-publish'],self.verify,stdin=io.BytesIO(raw))
        self.assertEqual(self.relay.publish.call_count,1)


class PackageTests(unittest.TestCase):
    def test_manifest_library_bytes_come_from_exact_ops_git_revision(self):
        path=Path(__file__).resolve().parents[1]/'deploy/scripts/package-owner-recovery-reserve.py'
        spec=importlib.util.spec_from_file_location('reserve_package_test',path)
        package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
        commit='d'*40;calls=[]
        def read(*args):
            calls.append(args)
            if args[0]=='rev-parse':return args[1].encode()+b'\n'
            if args[0]=='ls-tree':return b'backend/owner_recovery.py\nbackend/identity_plan_store.py\nbackend/test_private.py\n'
            if args[0]=='show':return ('public source '+args[1]).encode()
            raise AssertionError()
        files=package.sources(commit,read=read)
        manifest=json.loads(files['source-manifest.json'])
        self.assertEqual(manifest['operations'],package.OPS)
        self.assertEqual(len(manifest['wrapper']),5)
        for name in ('owner_recovery_reserve_relay','owner_recovery_reserve_operator'):
            self.assertIn('backend/'+name+'.py',manifest['wrapper'])
        self.assertNotIn('backend/test_private.py',manifest['library'])
        for name,digest in manifest['library'].items():
            self.assertEqual(digest,hashlib.sha256(('public source '+package.OPS+':'+name).encode()).hexdigest())
            self.assertIn(('show',package.OPS+':'+name),calls)
        self.assertIn((package.BASE+commit).encode(),files['careertrellis-owner-recovery-reserve'])


if __name__=='__main__':unittest.main()
