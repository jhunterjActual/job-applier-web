"""Synthetic schedule and quota/restore health contracts, no native acceptance."""
import copy
import datetime as dt
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock,patch
from deploy.onedrive_retention import health

NOW=dt.datetime(2026,10,27,14,tzinfo=dt.timezone.utc)
BINDINGS={key:format(number,'064x') for number,key in enumerate(sorted(health.BINDINGS),1)}
REV='a'*40
RELEASE='b'*40


class ScheduleReceiverTests(unittest.TestCase):
    def test_delayed_delivery_cannot_write_even_if_within_monitoring_age(self):
        value=schedule()
        value['observed_at']=(NOW-dt.timedelta(minutes=3)).isoformat()
        context=SimpleNamespace(bindings=BINDINGS,revision=REV,
                                document={'application_release':RELEASE})
        class Clock(dt.datetime):
            @classmethod
            def now(cls,tz=None):return NOW
        with patch.object(health,'_context',return_value=context), \
             patch.object(health,'_installation'), \
             patch.object(health.dt,'datetime',Clock), \
             patch.object(health.coordinator,'control') as locked, \
             patch.object(health.coordinator,'replace_private') as write:
            # The older monitoring-age contract alone does not authorize a
            # delayed delivery to rewrite the current schedule witness.
            with patch.object(health,'validate_schedule'):
                with self.assertRaises(health.HealthRefused):health.store_schedule(value)
            locked.assert_not_called();write.assert_not_called()


def schedule():
    units={}
    for number,(name,(target,maximum)) in enumerate(health.TIMERS.items()):
        service={'source_sha256':str(number+3)*64,'dropins':{},'source_loaded':True,'load_state':'loaded',
            'result':'success','profile':{'Type':'oneshot','Restart':'no','RemainAfterExit':'no','User':'root','Group':'root'},
            'executable':'/usr/bin/python3','argv':list(health.COMMANDS[target]),'command_supported':True}
        units[name]={'source_sha256':str(number+1)*64,'dropins':{},'load_state':'loaded',
            'active_state':'active','sub_state':'waiting','enabled_state':'enabled',
            'target_unit':target,'source_loaded':True,'service':service,
            'next_trigger_epoch_us':int((NOW+dt.timedelta(seconds=maximum-60)).timestamp()*1000000)}
    return {'schema':health.SCHEMA,'result':'pass','observed_at':NOW.isoformat(),'artifact_revision':REV,
        'application_release':RELEASE,'baseline_bindings':dict(BINDINGS),'web_install_receipt_sha256':'c'*64,'units':units}


def evidence():
    entries=[]
    for number in (1,2):
        archive=str(number)*64
        entries.append({'id':archive,'accepted_at':(NOW-dt.timedelta(hours=number)).isoformat(),
            'archive_sha256':archive,'download_sha256':archive,'restore_archive_sha256':archive,
            'native_restore_receipt_sha256':str(number+2)*64,'complete':True,'current':False,
            'dependencies_resolved':True,'dependencies':[],
            'objects':[{'path':archive+'/repository.tar','object_id':'synthetic-'+str(number),'size':100,'sha256':archive}]})
    return {'observed_at':NOW.isoformat(),'quota':{'total':1000,'free':201,'observed_at':NOW.isoformat()},
        'namespace_bytes':200,'catalog':{'schema':'careertrellis.onedrive-bundle-catalog/v1',
            'observed_at':NOW.isoformat(),'entries':entries,'holds':[]},
        'latest_restore':{'archive_sha256':'1'*64,'backup_label':'20261027-000000F',
            'completed_at':(NOW-dt.timedelta(hours=1)).isoformat(),'native_restore_accepted':True,
            'checksums_verified':True,'receipt_sha256':'3'*64,'terminal_proof_sha256':'f'*64},'schedule_ready':True}


class Tests(unittest.TestCase):
    def test_database_proof_reads_preserve_real_required_size_bound_signature(self):
        from deploy.onedrive_retention import guard,execution
        value=evidence();snapshot=value['catalog']
        now=dt.datetime.now(dt.timezone.utc).isoformat()
        snapshot['observed_at']=now
        native=dict(value['latest_restore'],attempt=1,transaction='a'*32)
        native.pop('receipt_sha256');native.pop('terminal_proof_sha256')
        raw=health.bundle.canonical(native)
        snapshot['entries'][0]['native_restore_receipt_sha256']=health.sha(raw)
        proof={'unit':'careertrellis-onedrive-bundle-restore-'+'a'*32+'.service'}
        proof_raw=health.bundle.canonical(proof)
        record={'evidence':{'native':native,'download':{'transaction':'a'*32},
                            'terminal_proof_sha256':health.sha(proof_raw)}}
        store=SimpleNamespace(io=SimpleNamespace(locked=lambda:nullcontext()),
                              records=lambda:{'1'*64:record})
        roots={'complete.json':raw,'unit-terminal-proof.json':proof_raw,
               'ownership.json':health.bundle.canonical({'owner':{'synthetic':True}}),
               'unit-stop-intent.json':health.bundle.canonical({'synthetic_stop':True})}
        reads=[]
        def read(path,*,maximum):
            self.assertEqual(maximum,131072);reads.append(path.name);return roots[path.name]
        context=SimpleNamespace(bindings=BINDINGS)
        observed={'authenticated':True,'catalog':snapshot,'quota':dict(value['quota'],observed_at=now),
                  'namespace_bytes':value['namespace_bytes']}
        with patch.object(health,'_context',return_value=context),patch.object(health,'_installation'), \
             patch.object(health.catalog,'NativeFiles',return_value=SimpleNamespace(require_repository_lock=Mock())), \
             patch.object(health.catalog,'NativeStore',return_value=store), \
             patch.object(guard,'native_catalog_inventory',return_value=observed), \
             patch.object(health.native_restore,'protected',autospec=True,side_effect=read), \
             patch.object(health.native_restore,'validate_terminal_proof') as validate, \
             patch.object(execution,'terminal') as terminal, \
             patch.object(health,'schedule_status',return_value={'result':'ready'}), \
             patch.object(health.baseline,'native_context',return_value=context):
            health.database_health(7)
        self.assertEqual(reads,['complete.json','unit-terminal-proof.json','ownership.json','unit-stop-intent.json'])
        validate.assert_called_once();terminal.assert_called_once_with(proof['unit'])

    def test_schedule_is_fresh_policy_bound_and_two_real_future_schedules_required(self):
        value=schedule()
        self.assertEqual(health.validate_schedule(value,BINDINGS,REV,RELEASE,NOW),value)
        for change in (lambda v:v.update(observed_at=(NOW-dt.timedelta(seconds=1801)).isoformat()),
            lambda v:v.update(observed_at=(NOW+dt.timedelta(seconds=1)).isoformat()),
            lambda v:v.update(artifact_revision='d'*40),lambda v:v.update(application_release='d'*40),
            lambda v:v['baseline_bindings'].update(paired_policy_sha256='d'*64),
            lambda v:v['units'].pop('careertrellis-onedrive-retention.timer'),
            lambda v:v['units']['careertrellis-paired-maintenance.timer'].update(active_state='inactive'),
            lambda v:v['units']['careertrellis-paired-maintenance.timer'].update(enabled_state='disabled'),
            lambda v:v['units']['careertrellis-paired-maintenance.timer'].update(target_unit='unrelated.service'),
            lambda v:v['units']['careertrellis-paired-maintenance.timer'].update(source_loaded=False),
            lambda v:v['units']['careertrellis-onedrive-retention.timer']['service'].update(source_loaded=False),
            lambda v:v['units']['careertrellis-onedrive-retention.timer']['service'].update(command_supported=False),
            lambda v:v['units']['careertrellis-onedrive-retention.timer']['service'].update(argv=['/bin/sh','-c','unreviewed']),
            lambda v:v['units']['careertrellis-onedrive-retention.timer'].update(next_trigger_epoch_us=int(NOW.timestamp()*1000000)),
            lambda v:v['units']['careertrellis-onedrive-retention.timer'].update(next_trigger_epoch_us=True)):
            value=schedule();change(value)
            with self.assertRaises(health.HealthRefused):health.validate_schedule(value,BINDINGS,REV,RELEASE,NOW)

    def test_schedule_dropins_cannot_escape_and_numeric_bus_values_are_typed(self):
        value=schedule();timer='careertrellis-onedrive-retention.timer'
        value['units'][timer]['dropins']['/etc/systemd/system/caddy.service.d/override.conf']='a'*64
        with self.assertRaises(health.HealthRefused):health.validate_schedule(value,BINDINGS,REV,RELEASE,NOW)
        with patch.object(health.hydrate,'command',return_value=b't 123456789\n'):
            self.assertEqual(health._property(timer,'NextElapseUSecRealtime'),123456789)
        for raw in (b'u 123\n',b't 0\n',b't 18446744073709551615\n',b't 123 extra\n',b't 123'):
            with patch.object(health.hydrate,'command',return_value=raw),self.assertRaises(health.HealthRefused):
                health._property(timer,'NextElapseUSecRealtime')

    def test_storage_thresholds_and_namespace_include_all_measured_bytes(self):
        result=health.evaluate(evidence(),NOW)
        self.assertEqual(result['result'],'ok');self.assertFalse(result['authenticated'])
        self.assertFalse(result['alerts_cleared']);self.assertFalse(result['current_remote_bytes_reverified'])
        for free,result_name,reason in ((200,'warning','account_usage_warning'),(150,'urgent','account_usage_urgent')):
            value=evidence();value['quota']['free']=free
            result=health.evaluate(value,NOW);self.assertEqual(result['result'],result_name)
            self.assertIn(reason,result['reasons'])
        value=evidence();value['namespace_bytes']=health.NAMESPACE_BUDGET+1
        result=health.evaluate(value,NOW)
        self.assertEqual(result['result'],'alert');self.assertIn('bundle_namespace_budget_exceeded',result['reasons'])

    def test_restore_staleness_schedule_failure_and_bootstrap_do_not_report_success(self):
        value=evidence();value['latest_restore']['completed_at']=(NOW-dt.timedelta(hours=36,seconds=1)).isoformat()
        result=health.evaluate(value,NOW);self.assertIn('latest_native_restore_stale',result['reasons'])
        value=evidence();value['schedule_ready']=False
        self.assertIn('replacement_schedule_not_ready',health.evaluate(value,NOW)['reasons'])
        value=evidence();value['catalog']['entries']=[];value['latest_restore']=None
        result=health.evaluate(value,NOW)
        self.assertEqual(result['result'],'alert');self.assertIn('no_verified_bundle',result['reasons'])

    def test_invalid_native_restore_claim_or_stale_quota_refuses(self):
        for mutate in (lambda v:v['latest_restore'].update(checksums_verified=False),
            lambda v:v['latest_restore'].update(native_restore_accepted=False),
            lambda v:v['latest_restore'].update(receipt_sha256='0'*64),
            lambda v:v['latest_restore'].update(completed_at=(NOW+dt.timedelta(seconds=1)).isoformat()),
            lambda v:v['quota'].update(free=True),
            lambda v:v['quota'].update(observed_at=(NOW-dt.timedelta(seconds=901)).isoformat())):
            value=evidence();mutate(value)
            with self.assertRaises(health.HealthRefused):health.evaluate(value,NOW)

    def test_missing_native_context_is_not_ready_without_lock_or_provider(self):
        with patch.object(health,'_context',side_effect=health.HealthRefused()), \
             patch.object(health.hydrate,'command') as command:
            result=health.schedule_status()
            self.assertEqual(result['result'],'not_ready');self.assertFalse(result['authenticated'])
            self.assertEqual(result['locks_acquired'],0);command.assert_not_called()

    def service_exec(self,target):
        import json
        argv=health.COMMANDS[target]
        return ('a(sasbttttuii) 1 "/usr/bin/python3" '+str(len(argv))+' '+
                ' '.join(json.dumps(arg) for arg in argv)+' false 0 0 0 0 0 0 0\n').encode()

    def test_loaded_service_execstart_must_be_exact_one_fixed_command(self):
        for target in health.COMMANDS:
            raw=self.service_exec(target)
            self.assertEqual(health.validate_exec_start(raw,target)['argv'],health.COMMANDS[target])
            for changed in (raw.replace(b'a(sasbttttuii) 1',b'a(sasbttttuii) 2'),
                raw.replace(b'false',b'true'),raw.replace(b'"-I"',b'"-E"'),
                raw.replace(b'"/usr/bin/python3"',b'"/bin/sh"'),raw+b' extra'):
                with self.assertRaises(health.HealthRefused):health.validate_exec_start(changed,target)

    def test_target_service_drift_or_loaded_override_never_proves_schedule(self):
        target='careertrellis-onedrive-retention.service'
        source=health.Path('/etc/systemd/system')/target
        raw=b'[Service]\nExecStart=/usr/bin/python3 -I -B fixed scheduled-run\n'
        plan={'files':{str(source):{'sha256':health.sha(raw)}},'baseline':{'unit_dropins':{}}}
        state={'LoadState':'loaded','ActiveState':'inactive','SubState':'dead','UnitFileState':'static',
            'Job':'','FragmentPath':str(source),'DropInPaths':'','NeedDaemonReload':'no','Result':'success',
            'Type':'oneshot','Restart':'no','RemainAfterExit':'no','User':'root','Group':'root'}
        with patch.object(health,'require_service_commands'),patch.object(health.hydrate,'protected',return_value=(raw,None)), \
             patch.object(health,'_show',return_value=state),patch.object(health.os.path,'lexists',return_value=False), \
             patch.object(health.hydrate,'command',return_value=self.service_exec(target)):
            self.assertEqual(health._service(target,plan)['argv'],health.COMMANDS[target])
        for changes,changed_raw,exec_raw in (({},raw+b'# drift',self.service_exec(target)),
            ({'NeedDaemonReload':'yes'},raw,self.service_exec(target)),
            ({'DropInPaths':'/run/systemd/system.control/'+target+'.d/override.conf'},raw,self.service_exec(target)),
            ({},raw,self.service_exec(target).replace(b'"scheduled-run"',b'"unrelated-command"'))):
            with patch.object(health,'require_service_commands'), \
                 patch.object(health.hydrate,'protected',return_value=(changed_raw,None)), \
                 patch.object(health,'_show',return_value=dict(state,**changes)), \
                 patch.object(health.os.path,'lexists',return_value=False), \
                 patch.object(health.hydrate,'command',return_value=exec_raw),self.assertRaises(health.HealthRefused):
                health._service(target,plan)

    def test_future_scheduled_command_is_not_supported_by_allowlist_alone(self):
        context=SimpleNamespace(bindings=BINDINGS,revision=REV,document={'application_release':RELEASE})
        with patch.object(health.entry,'parse_command',side_effect=health.entry.EntryRefused()), \
             patch.object(health,'_context',return_value=context),patch.object(health,'_installation'), \
             patch.object(health,'_document',return_value=(b'{}',schedule())):
            with self.assertRaises(health.HealthRefused):health.require_service_commands()
            self.assertEqual(health.schedule_status()['result'],'not_ready')


if __name__=='__main__':unittest.main()
