"""Provider-free native entry contracts, using explicit patched IO only."""
import contextlib
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy.onedrive_retention import execution as e, endpoint, common_io, bundle

TX = 'd'*32
SHA = 'a'*64


def inactive():
    return dict(LoadState='not-found',ActiveState='inactive',SubState='dead',
        MainPID='0',ControlPID='0',Job='',ControlGroup='')


def successful(stage):
    unit=endpoint.unit(stage,TX)
    result=dict(LoadState='loaded',ActiveState='active',SubState='exited',
        MainPID='0',ControlPID='0',Job='',ControlGroup='/system.slice/'+unit,
        Result='success',ExecMainStatus='0',ExecMainPID='123',InvocationID='f'*32,
        Type='exec',Restart='no',RemainAfterExit='yes',KillMode='control-group',
        SendSIGKILL='yes',PrivateNetwork='no',RuntimeMaxUSec={'hydrate':'9h','publish':'6h','prepare':'9h','retire':'1h'}[stage],
        TimeoutStartUSec='30s',TimeoutStopUSec='1min 30s')
    result.update({key:'' for key in e.native.OWNER_HOOK_FIELDS})
    return result


class Controls(unittest.TestCase):
    def test_scheduler_requires_owning_unit_before_any_dispatch(self):
        from deploy.onedrive_retention import entry
        command = entry.parse_command(['scheduled-run'])
        self.assertEqual(command['operation'],'scheduled-run')
        self.assertTrue(e.supports_operation('scheduled-run'))
        with patch.object(e,'require_scheduled_owner',side_effect=e.c.Refused('unit')), \
             patch.object(e.c,'run') as run,self.assertRaises(e.c.Refused):
            e.run_command(command)
        run.assert_not_called()

    def test_proposed_scheduler_profile_requires_finite_controls_and_exact_root_owner(self):
        value = successful('hydrate')
        value.update(LoadState='loaded',ActiveState='active',SubState='running',MainPID='123',ControlPID='0',Job='',
            User='root',Group='root',UMask='0077',RemainAfterExit='no',RuntimeMaxUSec='1d 12h')
        self.assertEqual(e.validate_scheduled_owner(value,123),value)
        for field,bad in (('User','nobody'),('Group','nobody'),('UMask','0022'),('MainPID','124'),
                          ('RuntimeMaxUSec','infinity'),('TimeoutStopUSec','1s'),('KillMode','process'),
                          ('SendSIGKILL','no'),('Restart','always'),('ExecStop','unexpected')):
            with self.subTest(field=field),self.assertRaises((e.c.Refused,e.native.NativeRefused)):
                e.validate_scheduled_owner(dict(value,**{field:bad}),123)
    def test_nonrestore_profiles_are_bounded_retained_and_separate(self):
        for stage in e.STAGE_SECONDS:
            owner=endpoint.command_plan(stage,TX,Path('/fixed'))
            watcher=e.stage_collector_command(TX,stage,Path('/fixed'))
            self.assertNotEqual(owner[2],watcher[2])
            for value in ('Type=exec','Restart=no','RemainAfterExit=yes',
                          'TimeoutStartSec=30s','TimeoutStopSec=90s',
                          'KillMode=control-group','SendSIGKILL=yes'):
                self.assertIn('--property='+value,owner)
            self.assertFalse(any('infinity' in item for item in owner+watcher))
            self.assertIn('--property=PrivateNetwork=yes',watcher)
            self.assertIn('--property=RuntimeMaxSec='+str(e.STAGE_SECONDS[stage]+300)+'s',watcher)
            self.assertEqual(common_io.unit_object_path(e.stage_collector(TX,stage)).split('/')[-1][:12],
                             'careertrelli')

    def test_loaded_success_and_exact_profile_required(self):
        for stage in e.STAGE_SECONDS:
            expected=successful(stage)
            self.assertEqual(e.validate_stage_success(expected,endpoint.unit(stage,TX),stage),expected)
            for key,bad in (('LoadState','not-found'),('Result','timeout'),('ExecMainStatus','1'),
                            ('MainPID','123'),('ControlPID','5'),('Job','1'),('PrivateNetwork','yes'),
                            ('RemainAfterExit','no'),('KillMode','process'),('SendSIGKILL','no'),
                            ('RuntimeMaxUSec','infinity'),('ExecStop','/unexpected'),('InvocationID','')):
                with self.subTest(stage=stage,key=key),self.assertRaises((e.c.Refused,e.native.NativeRefused)):
                    e.validate_stage_success(dict(expected,**{key:bad}),endpoint.unit(stage,TX),stage)

    def test_terminal_requires_pids_jobs_and_cgroup_empty(self):
        with patch.object(e,'show',return_value=inactive()),patch.object(e,'group_empty',return_value=True):
            self.assertTrue(e.terminal(endpoint.unit('hydrate',TX)))
        for key,bad in (('MainPID','10'),('Job','1'),('ActiveState','active'),('ControlGroup','/other')):
            with patch.object(e,'show',return_value=dict(inactive(),**{key:bad})), \
                    patch.object(e,'group_empty',return_value=True),self.assertRaises(e.c.Refused):
                e.terminal(endpoint.unit('hydrate',TX))
        with patch.object(e,'show',return_value=inactive()),patch.object(e,'group_empty',return_value=False), \
                self.assertRaises(e.c.Refused):e.terminal(endpoint.unit('hydrate',TX))

    def test_census_rejects_other_units_and_duplicate_names(self):
        for raw in (b'other.service loaded active running\n',
                    ((endpoint.unit('hydrate',TX)+' loaded active running\n')*2).encode()):
            with patch.object(e.hydrate,'command',return_value=raw),self.assertRaises(e.c.Refused):e.census()


class Observation(unittest.TestCase):
    def context(self):
        return SimpleNamespace(bindings={'standalone_policy_sha256':SHA})

    def intent(self,stage):
        facts={} if stage=='hydrate' else {'predecessor_receipt_sha256':'b'*64}
        value=endpoint.request(SHA,TX,stage,{'stage':stage,'receipt_facts':facts})
        value['payload']['web_history']={'observed_at':'2020-01-01T00:00:00Z'}
        return value

    def test_missing_intent_is_unknown_without_mutation_or_lock(self):
        with patch.object(e,'authenticated',return_value=self.context()), \
                patch.object(e,'document',side_effect=FileNotFoundError), \
                patch.object(e.hydrate,'exclusive',side_effect=AssertionError('mutation')), \
                patch.object(e.c,'control',side_effect=AssertionError('lock')), \
                patch.object(e.hydrate,'command',side_effect=AssertionError('provider')):
            self.assertEqual(e.observe(TX,'hydrate',SHA)['state'],'unknown')

    def test_completed_hydration_requires_collected_actual_success(self):
        intent=self.intent('hydrate'); snapshot=successful('hydrate')
        complete={'schema':'careertrellis.repository-hydration/v1','transaction':TX,'phase':'complete',
            'result':{'archive_bytes':10240,'generation_sha256':'b'*64,
                      'backup_label':'20261026-155056F','sha256':'c'*64}}
        terminal={'schema':'careertrellis.onedrive-stage-terminal/v1','transaction':TX,'stage':'hydrate',
            'unit':endpoint.unit('hydrate',TX),'policy_sha256':SHA,'retained_success':snapshot,
            'owner':{'pid':123,'start_ticks':234,'boot_id':'synthetic','process_group':123}}
        birth={'transaction':TX,'stage':'hydrate','unit':endpoint.unit('hydrate',TX),
               'invocation_id':'f'*32,'owner':terminal['owner'],
               'request_sha256':hashlib.sha256(bundle.canonical(intent)).hexdigest()}
        def document(path,*args):
            selected={'intent.json':intent,'terminal.json':terminal,'status-05.json':complete,
                      'owner-start.json':birth,'unit-stop-intent.json':terminal}[Path(path).name]
            return bundle.canonical(selected),selected
        with patch.object(e,'authenticated',return_value=self.context()),patch.object(e,'document',side_effect=document), \
                patch.object(e,'show',return_value=inactive()),patch.object(e,'terminal',return_value=True), \
                patch.object(e.os.path,'lexists',return_value=False):
            result=e.observe(TX,'hydrate',SHA)
        self.assertEqual(result['state'],'complete')
        self.assertEqual(result['receipt_sha256'],hashlib.sha256(bundle.canonical(complete)).hexdigest())
        self.assertEqual(result['facts']['archive_sha256'],'c'*64)

    def test_policy_mismatch_refuses_before_reading_journal(self):
        with patch.object(e,'authenticated',return_value=self.context()),patch.object(e,'document') as read, \
                self.assertRaises(e.c.Refused):e.observe(TX,'hydrate','f'*64)
        read.assert_not_called()

    def test_persisted_provenance_does_not_freshen_expired_action(self):
        intent=self.intent('hydrate');raw=bundle.canonical(intent)
        self.assertEqual(e.persisted_intent(raw,intent,TX,'hydrate',SHA),hashlib.sha256(raw).hexdigest())
        from deploy.onedrive_retention import guard
        with patch.object(guard,'validate_peer_history',side_effect=guard.GuardRefused('stale')), \
                self.assertRaises(guard.GuardRefused):endpoint.validate_request(intent)
        # No dispatch path consumes persisted_intent's return value, and a
        # changed request cannot reuse the owner's bound provenance digest.
        changed=dict(intent,capture_transaction='f'*32)
        with self.assertRaises(e.c.Refused):e.persisted_intent(bundle.canonical(changed),changed,TX,'hydrate',SHA)

    def test_caller_archive_claim_never_authenticates_completed_history(self):
        fake=SimpleNamespace(authenticated=True,current_bindings=self.context().bindings,
                             archive_policy_sha='f'*64)
        with patch.object(e,'authenticated',return_value=self.context()), \
                patch.object(e,'_observe_records') as read,self.assertRaises(e.c.Refused):
            e.observe_completed_history(TX,'hydrate',fake)
        read.assert_not_called()


class Routing(unittest.TestCase):
    def test_scheduled_branch_uses_fresh_tx_and_the_real_eligibility_gate(self):
        from deploy.tests.test_onedrive_coordinator import policy
        with patch.object(e,'require_scheduled_owner'),patch.object(e.c,'read_private',return_value=policy().data), \
             patch.object(e.c,'run',return_value={'state':'fixture'}) as run:
            e.run_command({'operation':'scheduled-run','transaction':None})
            e.run_command({'operation':'scheduled-run','transaction':None})
        calls=run.call_args_list
        self.assertNotEqual(calls[0].args[0],calls[1].args[0])
        for called in calls:
            self.assertRegex(called.args[0],'^[0-9a-f]{32}$')
            self.assertIs(type(called.args[2]),e.c.NativeWEB)
            self.assertIs(called.args[2].scheduled,True)

    @contextlib.contextmanager
    def scheduled_owner_fixture(self,*,argv=None,changes=None):
        from deploy.onedrive_retention import entry,health
        unit='careertrellis-onedrive-retention.service'
        state=successful('hydrate')
        state.update(LoadState='loaded',ActiveState='active',SubState='running',MainPID='123',ControlPID='0',Job='',
            User='root',Group='root',UMask='0077',RemainAfterExit='no',RuntimeMaxUSec='1d 12h',
            ControlGroup='/system.slice/'+unit,InvocationID='f'*32)
        state.update(changes or {})
        command=(b'/usr/bin/python3\0-I\0-B\0/usr/local/libexec/careertrellis/onedrive_retention/entry.py\0scheduled-run\0'
                 if argv is None else argv)
        def read(path):return {'/proc/self/cgroup':('0::/system.slice/'+unit+'\n').encode(),
                              '/proc/self/cmdline':command}[path.as_posix()]
        context=SimpleNamespace(role='web');executable=Path('synthetic-python.exe').absolute()
        with patch.object(entry,'verify_install',return_value='a'*40), \
             patch.object(entry,'_native_dependencies',return_value=context), \
             patch.object(e.os,'geteuid',return_value=0,create=True),patch.object(e.os,'getegid',return_value=0,create=True), \
             patch.object(e.os,'getpid',return_value=123),patch.object(Path,'read_bytes',autospec=True,side_effect=read), \
             patch.object(Path,'resolve',return_value=executable),patch.object(e.os,'readlink',return_value=str(executable)), \
             patch.dict(e.os.environ,{'INVOCATION_ID':'f'*32}), \
             patch.object(e.common_io,'read_unit_properties',return_value=state), \
             patch.object(health,'_installation',return_value=({},{})),patch.object(health,'_service') as installed:
            yield context,installed

    def test_scheduled_owner_requires_current_installed_unit_in_addition_to_profile(self):
        with self.scheduled_owner_fixture() as (context,installed):
            self.assertIs(e.require_scheduled_owner(),context)
        installed.assert_called_once()

    def test_manual_or_changed_scheduled_command_never_reaches_installation_gate(self):
        with self.scheduled_owner_fixture(argv=b'/usr/bin/python3\0arbitrary.py\0') as (_,installed):
            with self.assertRaises(e.c.Refused):e.require_scheduled_owner()
        installed.assert_not_called()

    def test_scheduled_owner_rejects_wrong_unit_invocation(self):
        with self.scheduled_owner_fixture(changes={'InvocationID':'e'*32}) as (_,installed):
            with self.assertRaises(e.c.Refused):e.require_scheduled_owner()
        installed.assert_not_called()

    def test_cli_requires_own_unit_before_provider_stage(self):
        from deploy.onedrive_retention import activation
        command={'operation':'db-stage','transaction':TX,'stage':'hydrate'}
        with patch.object(activation,'routing_state',return_value=None), \
                patch.object(e,'require_stage_owner',side_effect=e.c.Refused('unit')), \
                patch.object(e.hydrate,'hydrate') as capture,self.assertRaises(e.c.Refused):e.run_command(command)
        capture.assert_not_called()

    def test_partial_activation_fences_new_stage_before_owner_or_provider(self):
        from deploy.onedrive_retention import activation
        with patch.object(activation,'routing_state',side_effect=activation.ActivationRefused()), \
             patch.object(e,'require_stage_owner') as owner,patch.object(e.hydrate,'hydrate') as capture, \
             self.assertRaises(activation.ActivationRefused):
            e.run_command({'operation':'db-stage','transaction':TX,'stage':'hydrate'})
        owner.assert_not_called();capture.assert_not_called()

    def test_dispatch_preflight_refuses_before_any_write(self):
        with patch.object(e,'authenticated',side_effect=e.c.Refused('baseline')), \
                patch.object(e.hydrate,'exclusive') as write,patch.object(e.hydrate,'command') as command, \
                self.assertRaises(e.c.Refused):e.dispatch_restore(TX)
        write.assert_not_called();command.assert_not_called()

    def test_collector_cli_is_distinct_from_status(self):
        from deploy.onedrive_retention import entry
        for stage in e.STAGE_SECONDS:
            parsed=entry.parse_command(['collect-stage',stage,'--transaction',TX])
            self.assertEqual(parsed['operation'],'collect-stage')
        with self.assertRaises(entry.EntryRefused):entry.parse_command(['collect-stage','restore','--transaction',TX])


class Preparation(unittest.TestCase):
    def test_identity_command_waits_for_exact_bounded_child(self):
        command=e.identity_command(TX,2,Path('/fixed/log'))
        self.assertIn('--wait',command);self.assertNotIn('--no-block',command)
        self.assertIn('--property=RuntimeMaxSec=15min',command)
        self.assertIn('--property=TimeoutStopSec=30s',command)
        self.assertIn('--property=RemainAfterExit=no',command)
        self.assertIn('--unit=careertrellis-onedrive-bundle-identity-'+TX+'-02.service',command)
        self.assertEqual(command[-5:],['collect-identity','--transaction',TX,'--verification','2'])
        for invalid in (0,100,True):
            with self.assertRaises(e.c.Refused):e.identity_command(TX,invalid,Path('/fixed/log'))

    def test_parent_releases_repository_lock_for_each_child(self):
        events=[]; held=[False]
        record={'evidence':{'download':{'transaction':'e'*32}}}
        class Store:
            def __init__(self):self.io=self;self.repository_lock_fd=123
            @contextlib.contextmanager
            def locked(self):yield
            def records(self):return {'b'*64:record}
            def retired(self,records):return set()
            def accept(self,tx):
                self_check=held[0]
                if not self_check:raise AssertionError('catalog admission without repository lock')
                events.append('admit');return {'native_authenticated':True}
        @contextlib.contextmanager
        def store():
            self.assertFalse(held[0]);held[0]=True
            try:yield Store()
            finally:held[0]=False
        def child(parent,source):
            self.assertFalse(held[0]);events.append(source)
        with patch.object(e,'require_stage_owner'),patch.object(e,'authenticated'), \
                patch.object(e,'preparation_owner_continuation'), \
                patch.object(e,'document',return_value=(b'',{'archive_sha256':'c'*64})), \
                patch.object(e,'catalog_store',side_effect=store),patch.object(e,'fresh_identity',side_effect=child), \
                patch.object(e.hydrate,'exclusive') as write, \
                patch('deploy.onedrive_retention.guard.native_catalog_inventory',return_value={'authenticated':True,'catalog':{}}):
            result=e.prepare_retirement(TX)
        self.assertEqual(events,['e'*32,TX,'admit'])
        self.assertTrue(result['native_authenticated']);write.assert_called_once()

    def test_oversized_catalog_refuses_before_any_child_or_write(self):
        class Store:
            def __init__(self):self.io=self
            @contextlib.contextmanager
            def locked(self):yield
            def records(self):return {str(number):{} for number in range(32)}
            def retired(self,records):return set()
        @contextlib.contextmanager
        def store():yield Store()
        with patch.object(e,'require_stage_owner'),patch.object(e,'authenticated'), \
                patch.object(e,'preparation_owner_continuation'), \
                patch.object(e,'document',return_value=(b'',{'archive_sha256':'c'*64})), \
                patch.object(e,'catalog_store',side_effect=store),patch.object(e,'fresh_identity') as child, \
                patch.object(e.hydrate,'exclusive') as write,self.assertRaises(e.c.Refused):e.prepare_retirement(TX)
        child.assert_not_called();write.assert_not_called()

    def test_missing_parent_mapping_does_not_allow_active_parent(self):
        units=[endpoint.unit('prepare',TX),e.stage_collector(TX,'prepare')]
        context=SimpleNamespace(bindings={'standalone_policy_sha256':SHA})
        with patch.object(e,'document',side_effect=FileNotFoundError):
            self.assertEqual(e.identity_parent_units('e'*32,1,units,context),set())

    def test_wrong_mapping_source_policy_or_verification_refuses(self):
        context=SimpleNamespace(bindings={'standalone_policy_sha256':SHA})
        mapping={'schema':'careertrellis.onedrive-identity-preparation/v1','parent_transaction':TX,
            'source_transaction':'e'*32,'verification':1,'parent_owner':{},'parent_snapshot':{},
            'watcher_owner':{},'watcher_snapshot':{},'policy_sha256':SHA}
        for key,bad in (('source_transaction','f'*32),('verification',2),('policy_sha256','f'*64)):
            with patch.object(e,'document',return_value=(b'',dict(mapping,**{key:bad}))), \
                    self.subTest(key=key),self.assertRaises(e.c.Refused):
                e.identity_parent_units('e'*32,1,[endpoint.unit('prepare',TX)],context)

    def test_active_watcher_yields_running_without_terminal_action(self):
        with patch.object(e,'show',return_value=dict(inactive(),LoadState='loaded',ActiveState='active')), \
                patch.object(e,'terminal') as terminal:
            self.assertFalse(e.terminal_or_running(e.stage_collector(TX,'hydrate')))
        terminal.assert_not_called()

    def test_readonly_continuation_binds_original_owner_not_a_fresh_action(self):
        context=SimpleNamespace(bindings={'standalone_policy_sha256':SHA})
        owner={'pid':111,'start_ticks':222,'process_group':111,'boot_id':'synthetic'}
        watcher={'pid':234,'start_ticks':345,'process_group':234,'boot_id':'synthetic'}
        request={'schema':endpoint.SCHEMA,'operation':'bundle_prepare','retention_policy_sha256':SHA,
            'capture_transaction':TX,'payload':{'stage':'prepare','receipt_facts':{'predecessor_receipt_sha256':'b'*64},
            'web_history':{'observed_at':'2020-01-01T00:00:00Z'}}}
        birth={'schema':'careertrellis.onedrive-stage-owner/v1','transaction':TX,'stage':'prepare',
            'request_sha256':hashlib.sha256(bundle.canonical(request)).hexdigest(),'policy_sha256':SHA,
            'owner':owner,'invocation_id':'f'*32}
        dispatch={'transaction':TX,'stage':'prepare','owner_unit':endpoint.unit('prepare',TX),
                  'collector_unit':e.stage_collector(TX,'prepare')}
        watch_intent={'transaction':TX,'stage':'prepare','owner':watcher,'policy_sha256':SHA}
        state=dict(successful('prepare'),ActiveState='active',SubState='running',MainPID='234',
                   RemainAfterExit='no',PrivateNetwork='yes',RuntimeMaxUSec='9h 5min')
        records={'intent.json':request,'owner-start.json':birth,'stage-dispatch-intent.json':dispatch,
                 'collector-intent.json':watch_intent}
        def document(path,*args):
            value=records[Path(path).name];return bundle.canonical(value),value
        def identity(pid):return owner if pid==111 else watcher
        with patch.object(e,'authenticated',return_value=context), \
                patch.object(e,'stage_owner_profile',return_value={'InvocationID':'f'*32}), \
                patch.object(e.c,'directory'),patch.object(e,'document',side_effect=document), \
                patch.object(e,'stage_properties',return_value=state),patch.object(e.os,'getpid',return_value=111), \
                patch.object(e.native,'process_identity',side_effect=identity), \
                patch.object(e.Path,'iterdir',return_value=iter([])), \
                patch.object(endpoint,'validate_request',side_effect=AssertionError('continuation is not new authorization')):
            self.assertIs(e.preparation_owner_continuation(TX),context)
            birth['request_sha256']='e'*64
            with self.assertRaises(e.c.Refused):e.preparation_owner_continuation(TX)
