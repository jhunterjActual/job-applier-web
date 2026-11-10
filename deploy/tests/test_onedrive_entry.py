"""Synthetic protected-package and routing contracts; no native side effects."""
import datetime as dt
import ast
import contextlib
import hashlib
import io
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock

from deploy.onedrive_retention import entry


class RenewalWrapperTests(unittest.TestCase):
    def test_actual_fd_and_native_false_are_preserved(self):
        opened,other=os.pipe()
        preservation=Mock();execution=Mock();execution.expiry_capability_idle.return_value=False
        try:
            with patch.object(entry,'_load',side_effect=lambda name:{'preservation':preservation,'execution':execution}[name]):
                self.assertIs(entry.expiry_capability_idle(opened),False)
            preservation.require_owned_lock.assert_called_once_with('/run/lock/careertrellis-pgbackrest-repository.lock',opened)
            execution.expiry_capability_idle.assert_called_once_with(opened)
        finally:os.close(opened);os.close(other)

    def test_invalid_or_closed_fd_never_reaches_native_backend(self):
        with patch.object(entry,'_load') as load:
            for value in (True,-1,None,1.5,'4'):
                with self.assertRaises(entry.EntryRefused):entry.expiry_capability_idle(value)
            opened,other=os.pipe();os.close(opened);os.close(other)
            with self.assertRaises(OSError):entry.expiry_capability_idle(opened)
            load.assert_not_called()

    def test_canonical_lock_failure_and_missing_allowlist_never_dispatch(self):
        opened,other=os.pipe()
        try:
            preservation=Mock();preservation.require_owned_lock.side_effect=ValueError('fixture refusal')
            with patch.object(entry,'_load',return_value=preservation) as load:
                with self.assertRaises(ValueError):entry.expiry_capability_idle(opened)
                load.assert_called_once_with('preservation')
        finally:os.close(opened);os.close(other)
        with self.assertRaises(entry.EntryRefused):entry.parse_command(['pre-growth-identity','--transaction','a'*32])


class WebActivationRoutingTests(unittest.TestCase):
    def test_pre_growth_closed_arguments_cannot_promote_unallowlisted_native_code(self):
        commands=[['pre-growth-owner','--transaction','a'*32],['pre-growth-watch','--transaction','a'*32],
            ['pre-growth-identity','--transaction','a'*32,'--source-transaction','b'*32,'--verification','99']]
        for args in commands:
            command=entry.parse_command(args);self.assertEqual(command['role'],'db')
            with patch.object(entry,'FILES',entry.FILES-{'pre_growth_native.py'}),\
                 patch.object(entry,'verify_install',return_value='b'*40),\
                 patch.object(entry,'_native_dependencies'),patch.object(entry,'_load') as load:
                with self.assertRaises(entry.EntryRefused):entry.route(args)
                load.assert_not_called()
        for value in ('0','00','01','100','-1','1;other'):
            with self.assertRaises(entry.EntryRefused):entry.parse_command(commands[-1][:-1]+[value])
        for index in (2,4):
            args=list(commands[-1]);args[index]='0'*32
            with self.assertRaises(entry.EntryRefused):entry.parse_command(args)

    def test_closed_commands_require_nonzero_transaction_and_explicit_source_admission(self):
        for operation in entry.WEB_ACTIVATION_COMMANDS:
            command=entry.parse_command([operation,'--transaction','a'*32])
            self.assertEqual((command['role'],command['transaction']),('web','a'*32))
            for args in ([operation,'--transaction','0'*32],[operation,'--transaction','../other'],
                         [operation,'--transaction','a'*32,'--retry']):
                with self.assertRaises(entry.EntryRefused):entry.parse_command(args)
            with patch.object(entry,'FILES',entry.FILES-{'activation_web.py'}),\
                 patch.object(entry,'verify_install',return_value='b'*40),\
                 patch.object(entry,'_native_dependencies'),patch.object(entry,'_load') as load:
                with self.assertRaises(entry.EntryRefused):entry.route([operation,'--transaction','a'*32])
                load.assert_not_called()


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
    def test_admitted_actor_parser_has_only_exact_nonzero_transaction(self):
        command=entry.parse_command(['activation-actor','--transaction','d'*32])
        self.assertEqual(command['operation'],'activation-actor');self.assertEqual(command['role'],'db')
        self.assertIn('activation_actor.py',entry.FILES)
        for args in (['activation-actor','--transaction','0'*32],
                     ['activation-actor','--transaction','D'*32],
                     ['activation-actor','--transaction','d'*32,'--path','/tmp/anything']):
            with self.assertRaises(entry.EntryRefused):entry.parse_command(args)

    def test_complete_exact_package_hashes_verified_only_as_unauthenticated_fixture(self):
        result=entry.verify_fixture(Reader())
        self.assertEqual(result['revision'],'a'*40)
        self.assertFalse(result['authenticated']);self.assertFalse(result['native_actions'])
        self.assertEqual(len(entry.FILES),33)

    def test_runtime_dependency_closure_and_offline_modules_stay_separate(self):
        root=Path(entry.__file__).parent
        offline={'pre_growth.py','capacity_window.py','package.py'} # Simulations and packet builder, never runtime.
        self.assertTrue(offline.isdisjoint(entry.FILES))
        self.assertEqual({p.name for p in root.glob('*.py') if p.name!='__init__.py'},set(entry.FILES)|offline)
        for name in sorted(entry.FILES):
            tree=ast.parse((root/name).read_bytes(),filename=name)
            dependencies=set()
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom) and node.level:
                    self.assertEqual(node.level,1,(name,node.lineno))
                    dependencies.update([node.module.split('.')[0]+'.py'] if node.module else
                        [alias.name.split('.')[0]+'.py' for alias in node.names])
                if (isinstance(node,ast.Call) and isinstance(node.func,(ast.Name,ast.Attribute))
                        and (node.func.id if isinstance(node.func,ast.Name) else node.func.attr)=='_load'
                        and node.args and isinstance(node.args[0],ast.Constant) and type(node.args[0].value) is str):
                    dependencies.add(node.args[0].value+'.py')
            self.assertLessEqual(dependencies,set(entry.FILES),(name,sorted(dependencies-set(entry.FILES))))
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
    def test_prepared_verification_needs_no_accepted_baseline_and_has_no_mutation_route(self):
        result={'result':'dependencies_verified','native_actions':False}
        with patch.object(entry,'verify_install',return_value='a'*40), \
             patch.object(entry,'verify_prepared_dependencies',return_value=result) as verify, \
             patch.object(entry,'_native_dependencies') as active:
            self.assertIs(entry.route(['verify-prepared-install']),result)
        verify.assert_called_once_with('a'*40);active.assert_not_called()
        with self.assertRaises(entry.EntryRefused):entry.parse_command(['verify-prepared-install','--skip-helper-check'])

    def test_prepared_dependency_failure_cannot_return_installation_proof(self):
        with patch.object(entry,'verify_install',return_value='a'*40), \
             patch.object(entry,'verify_prepared_dependencies',side_effect=entry.EntryRefused('source_drift')), \
             patch.object(entry,'_native_dependencies') as active,self.assertRaises(entry.EntryRefused):
            entry.route(['verify-prepared-install'])
        active.assert_not_called()

    def test_activation_evidence_is_fixed_db_read_only_command_without_caller_paths(self):
        command = entry.parse_command(['activation-evidence-db'])
        self.assertEqual(command,{'operation':'activation-evidence-db','role':'db','stage':None,
                                 'transaction':None,'attempt':None})
        for args in (['activation-evidence-db','--path','/anything'],['activation-evidence-db','latest']):
            with self.assertRaises(entry.EntryRefused):entry.parse_command(args)

    def test_monitor_bridge_has_only_bounded_fixed_modes(self):
        self.assertEqual(entry.parse_command(['monitor-mode'])['role'],'db')
        self.assertFalse(entry.parse_command(['evaluate-core'])['bundle_unavailable'])
        self.assertTrue(entry.parse_command(['evaluate-core','--bundle-unavailable'])['bundle_unavailable'])
        for args in (['evaluate-core','--path','/anything'],['evaluate-core','--healthy'],
                     ['monitor-mode','--bundle-unavailable']):
            with self.assertRaises(entry.EntryRefused):entry.parse_command(args)

    def test_selected_legacy_pg_route_refuses_without_starting_another_operation(self):
        activation = SimpleNamespace(routing_state=lambda:object())
        with patch.object(entry,'verify_install',return_value='a'*40), \
             patch.object(entry.os,'geteuid',return_value=0,create=True), \
             patch.object(entry.os,'getegid',return_value=0,create=True), \
             patch.object(entry.socket,'gethostname',return_value='job-applier-db-staging-01'), \
             patch.object(entry,'_load',return_value=activation) as load, \
             patch.object(entry,'_native_dependencies') as dependencies,patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(entry.main(['legacy-pg-routing']),1)
        load.assert_called_once_with('activation');dependencies.assert_not_called()
    def test_legacy_object_guard_has_only_fixed_release_label_and_inherited_fds(self):
        args = ['legacy-object-guard','--release','a'*40,'--backup-label','20261027-000000F',
                '--maintenance-fd','8','--recovery-fd','9']
        parsed = entry.parse_command(args)
        self.assertEqual(parsed['maintenance_fd'],8);self.assertEqual(parsed['recovery_fd'],9)
        self.assertEqual(parsed['role'],'web')
        for index,bad in ((2,'../source'),(4,'latest'),(6,'3'),(8,'10')):
            changed = list(args);changed[index]=bad
            with self.assertRaises(entry.EntryRefused):entry.parse_command(changed)

    def test_unselected_router_does_not_load_executor_or_require_active_baseline(self):
        args = ['legacy-object-guard','--release','a'*40,'--backup-label','20261027-000000F',
                '--maintenance-fd','8','--recovery-fd','9']
        activation = SimpleNamespace(routing_state=lambda:None)
        with patch.object(entry,'verify_install',return_value='a'*40), \
             patch.object(entry.os,'geteuid',return_value=0,create=True), \
             patch.object(entry.os,'getegid',return_value=0,create=True), \
             patch.object(entry.socket,'gethostname',return_value='careertrellis-staging-web-1'), \
             patch.object(entry,'_load',return_value=activation) as load, \
             patch.object(entry,'_native_dependencies') as dependencies:
            result = entry.route(args)
        self.assertEqual(result['result'],'legacy_strategy_unselected')
        load.assert_called_once_with('activation');dependencies.assert_not_called()
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
