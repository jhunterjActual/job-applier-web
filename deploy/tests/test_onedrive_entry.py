"""Synthetic protected-package and routing contracts; no native side effects."""
import datetime as dt
import contextlib
import hashlib
import io
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy.onedrive_retention import entry


def metadata(mode,size=0,ino=1):
    return SimpleNamespace(st_dev=1,st_ino=ino,st_mode=mode,st_uid=0,st_gid=0,
        st_nlink=1,st_size=size,st_mtime_ns=1,st_ctime_ns=1)


class Reader:
    def __init__(self):
        self.sources={name:('# fixture '+name+'\n').encode() for name in entry.FILES}
        manifest={'schema':entry.SCHEMA,'revision':'a'*40,
            'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in self.sources.items()}}
        self.sources['manifest.json']=entry.canonical(manifest)
        self.metas={name:metadata(stat.S_IFREG|0o644,len(raw),index+10)
                    for index,(name,raw) in enumerate(self.sources.items())}
        self.directories=[metadata(stat.S_IFDIR|0o755)]
        self.opened={}
    def names(self):return sorted(self.sources)
    def parents(self):return self.directories
    def metadata(self,name):return self.metas[name]
    def read(self,name,limit):return self.sources[name],self.opened.get(name,self.metas[name])


class Offline:
    mode='offline'
    def __init__(self):self.calls=[];self.values={
        'schema':'careertrellis.retention-entry-bindings/v1','install_revision':'a'*40,'policy_revision':'a'*40,
        'application_release':'b'*40,'policy_application_release':'b'*40,'paired_application_release':'b'*40,
        'paired_policy_sha256':'c'*64,'policy_paired_policy_sha256':'c'*64,
        'baseline_authenticated':True,'policy_enabled':True,'valid_until':'2027-01-24T00:00:00Z'}
    def bindings(self):return self.values
    def now(self):return dt.datetime(2026,10,27,tzinfo=dt.timezone.utc)
    def dispatch(self,value):self.calls.append(value)


class ManifestTests(unittest.TestCase):
    def test_complete_exact_package_hashes_verified_only_as_unauthenticated_fixture(self):
        result=entry.verify_fixture(Reader())
        self.assertEqual(result['revision'],'a'*40)
        self.assertFalse(result['authenticated']);self.assertFalse(result['native_actions'])
        self.assertEqual(len(entry.FILES),24)
    def test_missing_extra_or_tampered_module_refuses(self):
        for action in ('missing','extra','tampered'):
            reader=Reader()
            if action=='missing':reader.sources.pop('collector.py')
            elif action=='extra':reader.sources['__init__.py']=b''
            else:reader.sources['collector.py']=b'X'+reader.sources['collector.py'][1:]
            with self.subTest(action=action),self.assertRaises(entry.EntryRefused):entry.verify_fixture(reader)
    def test_symlink_hardlink_wrong_owner_or_writable_source_refuses(self):
        for field,value in (('st_mode',stat.S_IFLNK|0o644),('st_nlink',2),('st_uid',1000),
                            ('st_gid',1000),('st_mode',stat.S_IFREG|0o666)):
            reader=Reader();setattr(reader.metas['collector.py'],field,value)
            with self.subTest(field=field),self.assertRaises(entry.EntryRefused):entry.verify_fixture(reader)
    def test_package_directory_mode_and_opened_file_identity_must_match(self):
        reader=Reader();reader.directories[0].st_mode=stat.S_IFDIR|0o777
        with self.assertRaises(entry.EntryRefused):entry.verify_fixture(reader)
        reader=Reader();reader.opened['collector.py']=metadata(stat.S_IFREG|0o644,len(reader.sources['collector.py']),999)
        with self.assertRaises(entry.EntryRefused):entry.verify_fixture(reader)
    def test_duplicate_noncanonical_and_wrong_manifest_membership_refuse(self):
        for raw in (b'{"schema":"x","schema":"x"}\n',b'{}',
                    entry.canonical({'schema':entry.SCHEMA,'revision':'a'*40,'files':{}})):
            reader=Reader();reader.sources['manifest.json']=raw
            reader.metas['manifest.json'].st_size=len(raw)
            with self.assertRaises(entry.EntryRefused):entry.verify_fixture(reader)
    def test_installed_verifier_has_no_editable_root_or_worktree_fallback(self):
        with patch.object(entry,'__file__','/untrusted/entry.py'),patch.object(entry,'_InstalledPackage') as reader:
            with self.assertRaises(entry.EntryRefused):entry.verify_install()
            reader.assert_not_called()
    def test_source_read_atime_is_ignored_but_directory_identity_drift_refuses(self):
        reader=Reader();original=reader.read
        def touched(name,limit):
            reader.metas[name].st_atime_ns=99
            return original(name,limit)
        reader.read=touched
        self.assertFalse(entry.verify_fixture(reader)['authenticated'])
        reader=Reader();original=reader.read
        def changed(name,limit):
            reader.directories[0].st_ino=999
            return original(name,limit)
        reader.read=changed
        with self.assertRaises(entry.EntryRefused):entry.verify_fixture(reader)


class RoutingTests(unittest.TestCase):
    def test_paired_namespace_requires_verified_package_without_sys_path_change(self):
        before=list(sys.path)
        prior=sys.modules.pop('paired_maintenance',None)
        try:
            with patch.object(entry,'_PairedPackage',return_value=contextlib.nullcontext(object())), \
                    patch.object(entry,'verify_package',return_value={}) as verify:
                package=entry._paired_namespace()
            self.assertEqual(list(package.__path__),[str(entry.PAIRED_ROOT)])
            self.assertEqual(sys.path,before);verify.assert_called_once()
        finally:
            sys.modules.pop('paired_maintenance',None)
            if prior is not None:sys.modules['paired_maintenance']=prior

    def test_all_fixed_stages_dispatch_offline_without_native_acceptance(self):
        for stage in entry.STAGES:
            backend=Offline();result=entry.route(['db-stage',stage,'--transaction','d'*32],backend)
            self.assertEqual(backend.calls[0]['stage'],stage)
            self.assertEqual(result['execution_mode'],'offline');self.assertFalse(result['native_restore_accepted'])
    def test_collect_attempt_separation_and_unknown_commands_refuse(self):
        backend=Offline();entry.route(['collect-native','--transaction','d'*32,'--attempt','2'],backend)
        self.assertEqual(backend.calls[0]['attempt'],2)
        for args in (['db-stage','restore','--transaction','../'+'d'*32],
                     ['db-stage','restore','--transaction','d'*32,'--command','anything'],
                     ['collect-native','--transaction','d'*32,'--attempt','10']):
            backend=Offline()
            with self.assertRaises(entry.EntryRefused):entry.route(args,backend)
            self.assertEqual(backend.calls,[])
    def test_binding_drift_disabled_expired_or_unauthenticated_baseline_never_dispatches(self):
        for field,value in (('policy_revision','f'*40),('policy_application_release','f'*40),
                           ('policy_paired_policy_sha256','f'*64),('baseline_authenticated',False),
                           ('policy_enabled',False),('valid_until','2026-01-01T00:00:00Z')):
            backend=Offline();backend.values[field]=value
            with self.subTest(field=field),self.assertRaises(entry.EntryRefused):
                entry.route(['db-stage','hydrate','--transaction','d'*32],backend)
            self.assertEqual(backend.calls,[])
    def test_native_hooks_require_fixed_installed_source_not_successful_stubs(self):
        for function,args in ((entry.retention_idle,('d'*32,'hydrate')),
                (entry.observe_retention_stage,('d'*32,'restore','f'*64)),
                (entry.dispatch_native_with_collector,('d'*32,)),
                (entry.identity_reverification_idle,('d'*32,1))):
            with self.assertRaises(entry.EntryRefused):
                function(*args)
    def test_cli_refuses_without_native_dependencies_and_never_dispatches(self):
        with patch.object(entry,'verify_install',return_value='a'*40), \
                patch.object(entry,'_native_dependencies',side_effect=entry.EntryRefused('private details')) as deps, \
                patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(entry.main(['db-stage','hydrate','--transaction','d'*32]),1)
            self.assertNotIn('private details',output.getvalue())
            self.assertIn('"native_actions":false',output.getvalue())
            deps.assert_called_once_with('a'*40,'db')
    def test_matching_baseline_context_still_requires_verified_execution_module(self):
        with patch.object(entry,'verify_install',return_value='a'*40), \
                patch.object(entry,'_native_dependencies',return_value=SimpleNamespace(authenticated=True)), \
                patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(entry.main(['db-stage','hydrate','--transaction','d'*32]),1)
            self.assertIn('"native_actions":false',output.getvalue())

    def test_preservation_has_no_caller_paths_and_requires_verified_install_before_loading(self):
        with patch.object(entry,'verify_install',side_effect=entry.EntryRefused('unverified')), \
             patch.object(entry,'_load') as load:
            with self.assertRaises(entry.EntryRefused):entry.route(['preserve-info-only'])
            load.assert_not_called()
        for args in (['preserve-info-only','--transaction','d'*32],
                     ['preserve-info-only','--path','/anything']):
            with self.assertRaises(entry.EntryRefused):entry.parse_command(args)
