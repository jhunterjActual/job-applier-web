"""Synthetic bridge boundaries; no systemd, provider or protected state access."""
import copy
import datetime as dt
from types import SimpleNamespace
import unittest
import tempfile
import sys
import types
from pathlib import Path
from unittest.mock import patch,Mock

from deploy.onedrive_retention import activation_actor as actor

TX='a'*32
CONTEXT=SimpleNamespace(bindings={'paired_policy_sha256':'b'*64,'standalone_policy_sha256':'c'*64,
    'standalone_manifest_sha256':'d'*64,'paired_manifest_sha256':'e'*64},
    document={'application_release':'f'*40})
OWNER={'pid':123,'start_ticks':456,'boot_id':'01234567-89ab-cdef-0123-456789abcdef'}


def request(phase='prepare'):
    payload={'baseline_bindings':copy.deepcopy(CONTEXT.bindings),
        'installation_receipts':{'web':'1'*64,'db':'2'*64},
        'intent_sha256':'3'*64,'web_baseline_sha256':'4'*64}
    if phase=='commit':payload['commit_authority_sha256']='5'*64
    return {'schema':'careertrellis.paired-maintenance-request/v1','transaction':TX,
        'policy_sha256':'b'*64,'operation':'retention_activation_'+phase,'payload':payload}


class Tests(unittest.TestCase):
    def test_db_phase_producer_rechecks_native_runtime_and_writes_distinct_exclusive_proofs(self):
        participant=SimpleNamespace(transaction=TX,root=Path('/synthetic'),
            _readiness_binding=Mock(side_effect=lambda phase:{'phase':phase,'origin_sha256':'1'*64,
                'prepared_receipt_sha256':None if phase=='prepare' else '2'*64,
                'material_sha256':None if phase=='prepare' else '3'*64}))
        live=Mock();live.state.return_value={'ActiveState':'active','SubState':'running','Job':''}
        pe=SimpleNamespace(verify_install=Mock(),load_policy=Mock(return_value=SimpleNamespace(digest='b'*64)))
        runtime=SimpleNamespace(preflight_runtime=Mock(return_value=live))
        provider=SimpleNamespace(database_facts=Mock(return_value={'fixture':True}))
        dbhost=SimpleNamespace(facts_ok=Mock())
        package=types.ModuleType('paired_maintenance');package.entry=pe;package.runtime=runtime
        package.provider=provider;package.db_host=dbhost
        records={}
        def exclusive(path,value):
            if path.name in records:raise FileExistsError('immutable')
            records[path.name]=copy.deepcopy(value)
        def private(path):
            value={'transaction':TX} if path.name=='intent.json' else {'fixture_review':True}
            return actor.bundle.canonical(value),value
        descriptors={actor.preservation.HISTORICAL_LOCKS[2]:17}
        with patch.dict(sys.modules,{'paired_maintenance':package}),patch.object(actor,'_context',return_value=CONTEXT),\
             patch.object(actor.t,'_unchanged'),\
             patch.object(actor,'_require_actor'),patch.object(actor.entry,'_paired_namespace'),\
             patch.object(actor.t,'_private',side_effect=private),patch.object(actor.t,'_birth',return_value=OWNER),\
             patch.object(actor.t,'_exclusive_marker',side_effect=exclusive),\
             patch.object(actor.guard,'native_history_snapshot',return_value={'complete':True,'unresolved':[]}),\
             patch.object(actor.hydrate,'protected',return_value=(b'phase=activated\n',None)):
            actor._collect_db_readiness('prepare',participant,CONTEXT,{},descriptors)
            first=copy.deepcopy(records['readiness-prepare.json'])
            actor._collect_db_readiness('commit',participant,CONTEXT,{},descriptors)
            self.assertEqual(records['readiness-prepare.json'],first)
            self.assertEqual(records['readiness-commit.json']['phase'],'commit')
            self.assertEqual(live.verify_sources.call_count,2);self.assertEqual(live.authority.call_count,2)
            self.assertEqual(provider.database_facts.call_count,2)
            with self.assertRaises(FileExistsError):actor._collect_db_readiness('commit',participant,CONTEXT,{},descriptors)
        self.assertNotIn('readiness.json',records)
    def test_actual_unit_profile_refuses_extra_hooks_or_weaker_owner_controls(self):
        values={'ActiveState':'active','SubState':'running','Type':'exec','User':'root','Group':'root',
            'MainPID':'123','ControlPID':'0','InvocationID':'a'*32,'ExecMainPID':'123','Restart':'no','RemainAfterExit':'yes',
            'RuntimeMaxUSec':'30min','TimeoutStartUSec':'30s','TimeoutStopUSec':'30s',
            'KillMode':'control-group','SendSIGKILL':'yes','PrivateNetwork':'no','UMask':'0077',
            'Result':'success','ExecMainCode':'0','ExecMainStatus':'0','ControlGroup':'/system.slice/'+actor._unit(TX),
            'Slice':'system.slice','LoadState':'loaded','Job':''}
        def raw(value):return ''.join(key+'='+item+'\n' for key,item in value.items()).encode()
        with patch.object(actor,'_typed_profile') as typed,patch.object(actor.hydrate,'command',return_value=raw(values)) as command:
            self.assertEqual(actor._unit_state(TX),values)
            typed.assert_called_once_with(TX)
            for key,bad in (('Slice','system-other.slice'),('User','hunter'),('Restart','always'),
                            ('KillMode','process'),('RuntimeMaxUSec','infinity'),('InvocationID','')):
                changed=dict(values);changed[key]=bad;command.return_value=raw(changed)
                with self.subTest(key=key),self.assertRaises(ValueError):actor._unit_state(TX)

    def test_typed_sandbox_arrays_masks_and_bounds_are_actual_and_exact(self):
        replies={'NoNewPrivileges':'b true','ProtectSystem':'s "strict"','ProtectHome':'s "yes"',
            'PrivateTmp':'b true','DevicePolicy':'s "closed"','DeviceAllow':'a(ss) 0',
            'CapabilityBoundingSet':'t 207','AmbientCapabilities':'t 128','MemoryMax':'t 268435456',
            'TasksMax':'t 64','LimitFSIZE':'t 1048576','StandardOutput':'s "null"','StandardError':'s "null"',
            'RestrictAddressFamilies':'(bas) true 2 "AF_UNIX" "AF_INET"',
            'ReadWritePaths':'as 5 '+' '.join('"'+path+'"' for path in actor.WRITE_PATHS),
            **{name:'a(sasbttttuii) 0' for name in actor.HOOKS}}
        def command(args,**kw):return (replies[args[-1]]+'\n').encode()
        with patch.object(actor.hydrate,'command',side_effect=command) as invoke:
            actor._typed_profile(TX)
            self.assertTrue(all(call.args[0][0]=='/usr/bin/busctl' for call in invoke.call_args_list))
            for name,bad in (('DeviceAllow','a(ss) 1 "/dev/null" "rw"'),
                             ('RestrictAddressFamilies','(bas) false 2 "AF_UNIX" "AF_INET"'),
                             ('CapabilityBoundingSet','t 18446744073709551615'),
                             ('ReadWritePaths','as 1 "/"'),('NoNewPrivileges','b false'),
                             ('MemoryMax','t 18446744073709551615'),('ExecCondition',''),
                             ('ExecStop','a(sasbttttuii) 1')):
                original=replies[name];replies[name]=bad
                with self.subTest(name=name),self.assertRaises(ValueError):actor._typed_profile(TX)
                replies[name]=original

    def test_dispatch_profile_explicit_slice_and_reviewed_bounds(self):
        self.assertEqual(actor.PROFILE['Slice'],'system.slice')
        self.assertEqual(actor.PROFILE['DeviceAllow'],'')
        self.assertEqual(actor.PROFILE['MemoryMax'],'256M')
        self.assertEqual(actor.PROFILE['TasksMax'],'64')
        self.assertEqual(actor.PROFILE['LimitFSIZE'],'1048576')
        self.assertEqual(actor.PROFILE['ReadWritePaths'].split(),list(actor.WRITE_PATHS))
        self.assertEqual(actor.PROFILE['StandardOutput'],actor.PROFILE['StandardError'])
        self.assertEqual(actor.PROFILE['StandardOutput'],'null')
        self.assertEqual(actor.PROFILE['RemainAfterExit'],'yes')

    def test_terminal_requires_original_successful_exited_invocation_and_dead_birth(self):
        from deploy.onedrive_retention import native_restore
        started={'owner':OWNER,'invocation':'a'*32}
        state={'ActiveState':'active','SubState':'exited','MainPID':'0','ControlPID':'0','Job':'',
            'InvocationID':'a'*32,'ExecMainPID':'123','Result':'success','ExecMainCode':'1',
            'ExecMainStatus':'0','ControlGroup':'/system.slice/'+actor._unit(TX)}
        with patch.object(actor.Path,'read_text',return_value=OWNER['boot_id']),\
             patch.object(native_restore,'process_identity',side_effect=FileNotFoundError(2,'gone')),\
             patch.object(actor,'_empty_cgroup',return_value={'empty':True}) as group:
            self.assertEqual(actor._terminal_state(started,CONTEXT,TX,state)['cgroup'],{'empty':True})
            for name,bad in (('MainPID','123'),('ControlPID','456'),('Job','123 stop'),
                             ('InvocationID','b'*32),('ExecMainPID','456'),('Result','timeout'),
                             ('ExecMainCode','2'),('ExecMainStatus','1'),('SubState','running')):
                changed=dict(state);changed[name]=bad
                with self.subTest(name=name),self.assertRaises(ValueError):actor._terminal_state(started,CONTEXT,TX,changed)
        with patch.object(actor.Path,'read_text',return_value=OWNER['boot_id']),\
             patch.object(native_restore,'process_identity',return_value=dict(OWNER)),patch.object(actor,'_empty_cgroup') as group:
            with self.assertRaises(ValueError):actor._terminal_state(started,CONTEXT,TX,state)
            group.assert_not_called()
        with patch.object(actor.Path,'read_text',return_value=OWNER['boot_id']),\
             patch.object(native_restore,'process_identity',return_value=dict(OWNER,start_ticks=457)),\
             patch.object(actor,'_empty_cgroup',return_value={'empty':True}):
            self.assertEqual(actor._terminal_state(started,CONTEXT,TX,state)['cgroup'],{'empty':True})

    def test_terminal_evidence_cannot_be_fabricated_from_claims(self):
        with self.assertRaises(ValueError):actor.AuthenticatedActorTerminal({},CONTEXT.bindings)

    def test_terminal_status_poll_never_writes_locks_or_dispatches(self):
        intent={'transaction':TX};sha=actor._sha(intent)
        started={'source':{'actor_sha256':'1'*64,'paired_entry_sha256':'2'*64},
            'owner':OWNER,'invocation':'a'*32}
        def private(path):
            value=intent if path.name=='intent.json' else started
            return actor.bundle.canonical(value),value
        proof=actor.AuthenticatedActorTerminal({'fixture':'actual-inspection-boundary-mocked'},CONTEXT.bindings,actor._TERMINAL_SEAL)
        with patch.object(actor,'_context',return_value=CONTEXT),patch.object(actor,'_endpoint_owner',return_value=OWNER),\
             patch.object(actor.t,'_private',side_effect=private),patch.object(actor,'_require_actor'),\
             patch.object(actor.t,'_unchanged'),patch.object(actor,'_unit_state',return_value={'ActiveState':'active','SubState':'running'}) as state,\
             patch.object(actor,'inspect_terminal',return_value=proof) as inspect,\
             patch.object(actor.t,'_exclusive_marker') as write,patch.object(actor,'_fences') as locks,\
             patch.object(actor.hydrate,'command') as dispatch:
            self.assertEqual(actor.terminal_status(TX,sha)['state'],'running');inspect.assert_not_called()
            state.return_value={'ActiveState':'active','SubState':'exited'}
            self.assertEqual(actor.terminal_status(TX,sha)['state'],'terminal')
            write.assert_not_called();locks.assert_not_called();dispatch.assert_not_called()

    def test_entire_cgroup_tree_must_be_empty(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            base=Path(temporary);root=base/'system.slice'/actor._unit(TX)
            child=root/'child';child.mkdir(parents=True)
            (root/'cgroup.procs').write_text('');(child/'cgroup.procs').write_text('')
            with patch.object(actor,'Path',return_value=base):
                result=actor._empty_cgroup(TX,'/system.slice/'+actor._unit(TX))
                self.assertEqual(result['directories'],2)
                (child/'cgroup.procs').write_text('567\n')
                with self.assertRaises(ValueError):actor._empty_cgroup(TX,'/system.slice/'+actor._unit(TX))
                with self.assertRaises(ValueError):actor._empty_cgroup(TX,'/system.slice/unrelated.service')

    def test_cleanup_requires_actual_finalization_before_stop_or_intent(self):
        proof=SimpleNamespace(value={'commit_receipt_sha256':'1'*64},digest='2'*64)
        with patch.object(actor,'_context',return_value=CONTEXT),patch.object(actor,'_endpoint_owner',return_value=OWNER),\
             patch.object(actor,'observe_terminal',return_value=proof),\
             patch.object(actor.a,'require_activated',side_effect=ValueError()),\
             patch.object(actor.t,'_exclusive_marker') as write,patch.object(actor.hydrate,'command') as command:
            with self.assertRaises(ValueError):actor.cleanup_terminal(TX,'3'*64)
            write.assert_not_called();command.assert_not_called()

    def test_cleanup_unknown_stop_retains_intent_and_never_retries(self):
        proof=SimpleNamespace(value={'actor_owner':OWNER,'actor_invocation':'a'*32,
            'commit_receipt_sha256':'1'*64},digest='2'*64)
        finalized={'intent_sha256':'3'*64,'committed_receipts':{'db':'1'*64}}
        writes={}
        def exclusive(path,value):
            if path in writes:raise FileExistsError()
            writes[path]=value
        with patch.object(actor,'_context',return_value=CONTEXT),patch.object(actor,'_endpoint_owner',return_value=OWNER),\
             patch.object(actor,'observe_terminal',return_value=proof),\
             patch.object(actor.a,'require_activated',return_value=SimpleNamespace(value={'transaction':TX})),\
             patch.object(actor.t,'_private',return_value=(b'',finalized)),patch.object(actor.t,'_unchanged'),\
             patch.object(actor.t,'_exclusive_marker',side_effect=exclusive),\
             patch.object(actor.hydrate,'command',side_effect=TimeoutError()) as command:
            with self.assertRaises(TimeoutError):actor.cleanup_terminal(TX,'3'*64)
            with self.assertRaises(FileExistsError):actor.cleanup_terminal(TX,'3'*64)
            self.assertEqual(command.call_count,1)
            self.assertEqual({p.name for p in writes},{'actor-cleanup-intent.json'})

    def test_uninstalled_native_factory_refuses(self):
        with self.assertRaises(ValueError):actor._context()

    def test_exact_phase_policy_and_request_contract(self):
        for phase in ('prepare','commit'):
            self.assertEqual(actor.validate_request(request(phase),CONTEXT),(TX,phase))
        for mutate in (lambda x:x.update(policy_sha256='0'*64),
                       lambda x:x.update(operation='retention_activation_state'),
                       lambda x:x['payload'].update(command=['anything']),
                       lambda x:x['payload']['baseline_bindings'].update(standalone_policy_sha256='0'*64)):
            value=request();mutate(value)
            with self.assertRaises(ValueError):actor.validate_request(value,CONTEXT)

    def test_ingress_authenticates_original_endpoint_and_fresh_source_bindings(self):
        with patch.object(actor,'_source',return_value={'paired_entry_sha256':'6'*64,'actor_sha256':'7'*64}),\
             patch.object(actor,'_same_endpoint') as original:
            packet=actor._packet(request(),CONTEXT,OWNER)
            actor._validate_packet(packet,CONTEXT,TX,'prepare');original.assert_called_once_with(OWNER)
            for key,bad in (('request_sha256','0'*64),('baseline_sha256','0'*64),
                            ('unit','other.service'),('command',['other']),('phase','commit'),
                            ('issued_at',(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=121)).isoformat())):
                changed=copy.deepcopy(packet);changed[key]=bad
                with self.subTest(key=key),self.assertRaises(ValueError):actor._validate_packet(changed,CONTEXT,TX,'prepare')
            original.side_effect=ProcessLookupError()
            with self.assertRaises(ProcessLookupError):actor._validate_packet(packet,CONTEXT,TX,'prepare')

    def test_unknown_dispatch_is_never_reissued(self):
        value=request();intent={'transaction':TX};value['payload']['intent_sha256']=actor._sha(intent)
        review={'baseline_sha256':actor._sha(CONTEXT.document),'installation_receipts':value['payload']['installation_receipts'],
            'peer_baseline_sha256':'4'*64,'prepare_deadline':(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=10)).isoformat()}
        records={}
        def exclusive(path,value,**kw):
            if path in records:raise FileExistsError()
            records[path]=copy.deepcopy(value)
        def private(path):
            value=review if path.name=='review.json' else intent
            return actor.bundle.canonical(value),value
        def command(args,**kw):
            if args[0]=='/usr/bin/systemctl':
                return b'LoadState=not-found\nActiveState=inactive\nMainPID=0\nControlPID=0\n'
            raise TimeoutError('unknown dispatch outcome')
        with patch.object(actor,'_context',return_value=CONTEXT),patch.object(actor,'_endpoint_owner',return_value=OWNER),\
             patch.object(actor,'_source',return_value={}),patch.object(actor,'_absent'),\
             patch.object(actor.a,'routing_state',return_value=None),patch.object(actor.t,'_private',side_effect=private),\
             patch.object(actor.t,'_exclusive_marker',side_effect=exclusive),\
             patch.object(actor.hydrate,'command',side_effect=command) as invoke:
            raw=actor.bundle.canonical(value)
            with self.assertRaises(TimeoutError):actor.submit(raw)
            self.assertEqual(sum(call.args[0][0]=='/usr/bin/systemd-run' for call in invoke.call_args_list),1)
            with self.assertRaises(FileExistsError):actor.submit(raw)
            self.assertEqual(sum(call.args[0][0]=='/usr/bin/systemd-run' for call in invoke.call_args_list),1)
            self.assertEqual({p.name for p in records},{'ingress-prepare.json','actor-dispatch.json'})

    def test_missing_actor_observation_cannot_dispatch_collect_or_take_fences(self):
        with patch.object(actor,'_context',return_value=CONTEXT),\
             patch.object(actor.t,'_private',side_effect=FileNotFoundError()),\
             patch.object(actor,'_fences') as fences,patch.object(actor.hydrate,'command') as command:
            with self.assertRaises(FileNotFoundError):actor.observe(TX,'1'*64)
            fences.assert_not_called();command.assert_not_called()

    def test_partial_or_orphan_finalized_refuses_before_ingress(self):
        with patch.object(actor,'_context',return_value=CONTEXT),patch.object(actor,'_endpoint_owner',return_value=OWNER),\
             patch.object(actor,'_absent',side_effect=ValueError()),\
             patch.object(actor.t,'_exclusive_marker') as write,patch.object(actor.hydrate,'command') as command:
            with self.assertRaises(ValueError):actor.submit(actor.bundle.canonical(request()))
            write.assert_not_called();command.assert_not_called()

    def test_material_requires_exact_peer_receipts_and_original_actor_authority(self):
        intent={'transaction':TX};review={'prepare_deadline':'2026-10-28T00:00:00Z'}
        request={'transaction':TX,'role':'web','phase':'prepare','intent_sha256':actor._sha(intent),
            'prepare_deadline':review['prepare_deadline'],'baseline_bindings':CONTEXT.bindings}
        receipt={'transaction':TX,'role':'web','phase':'prepare','intent_sha256':actor._sha(intent),
            'request_sha256':actor._sha(request),'result':'prepared'}
        db={'real':'local protected receipt'}
        authority={'dispatcher_owner':OWNER,'prepared':{'web':actor._sha(receipt),'db':actor._sha(db)}}
        value={'transaction':TX,'retention_policy_sha256':'c'*64,'intent_sha256':actor._sha(intent),
            'documents':{'commit-authority.json':authority,'prepared-web.json':receipt,'prepare-request-web.json':request}}
        with patch.object(actor.t,'planned_selector'),patch.object(actor.t,'_private',return_value=(b'',db)):
            actor._material(value,CONTEXT,TX,intent,review,OWNER)
            for mutate in (lambda x:x['documents']['commit-authority.json'].update(dispatcher_owner={}),
                           lambda x:x['documents']['commit-authority.json']['prepared'].update(db='0'*64),
                           lambda x:x['documents']['prepared-web.json'].update(result='claimed'),
                           lambda x:x['documents'].update(unreviewed={}),
                           lambda x:x.update(retention_policy_sha256='0'*64)):
                changed=copy.deepcopy(value);mutate(changed)
                with self.assertRaises(ValueError):actor._material(changed,CONTEXT,TX,intent,review,OWNER)


if __name__=='__main__':unittest.main()
