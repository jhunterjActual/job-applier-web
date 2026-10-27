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
            'next_trigger_epoch_us':int((NOW+dt.timedelta(seconds=min(maximum-60,24*3600))).timestamp()*1000000)}
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
            'backup_completed_at':(NOW-dt.timedelta(hours=2)).isoformat(),
            'completed_at':(NOW-dt.timedelta(hours=1)).isoformat(),'native_restore_accepted':True,
            'checksums_verified':True,'receipt_sha256':'3'*64,'terminal_proof_sha256':'f'*64},'schedule_ready':True}


class Tests(unittest.TestCase):
    def test_monday_saturday_calendar_and_aggregate_sunday_opportunity(self):
        from deploy.tests.test_onedrive_coordinator import policy
        old=policy();original=old.digest
        self.assertNotIn('schedule_days',old.data)
        self.assertEqual(health.coordinator.Policy(old.data).digest,original)
        active=health.coordinator.Policy(dict(old.data,schedule_days=['Mon','Tue','Wed','Thu','Fri','Sat'],schedule_hour_utc=6,schedule_minute_utc=0))
        self.assertEqual(health.validate_daily_calendar(b'[Timer]\nOnCalendar=Mon..Sat *-*-* 06:00:00 UTC\n',active),'Mon..Sat *-*-* 06:00:00 UTC')
        value=schedule()
        value['units']['careertrellis-onedrive-retention.timer']['next_trigger_epoch_us']=int((NOW+dt.timedelta(hours=48)).timestamp()*1000000)
        value['units']['careertrellis-paired-maintenance.timer']['next_trigger_epoch_us']=int((NOW+dt.timedelta(hours=24)).timestamp()*1000000)
        health.validate_schedule(value,BINDINGS,REV,RELEASE,NOW)
        value['units']['careertrellis-paired-maintenance.timer']['next_trigger_epoch_us']=int((NOW+dt.timedelta(hours=40)).timestamp()*1000000)
        with self.assertRaises(health.HealthRefused):health.validate_schedule(value,BINDINGS,REV,RELEASE,NOW)

    def test_explicit_rpo_projection_catches_18utc_duplicate_without_claiming_native_timing(self):
        # Explicit conservative winter bound: Sunday03NY+60m effective delay,
        # hypothetical6h
        # acceptance bound. Neither bound nor successful execution is assumed.
        project=health.coordinator.recovery_schedule_projection
        previous='2030-01-05T03:15:00Z' # Saturday source point.
        sunday={'latest_start':'2030-01-06T09:00:00Z','source_point_not_before':'2030-01-06T08:00:00Z'}
        late=[sunday,{'latest_start':'2030-01-06T18:15:00Z','source_point_not_before':'2030-01-06T08:00:00Z'},
            {'latest_start':'2030-01-07T18:15:00Z','source_point_not_before':'2030-01-07T03:15:00Z'}]
        self.assertFalse(project(previous,late,6*3600)['fits_36h_assumptions'])
        proposed=[sunday,{'latest_start':'2030-01-07T06:15:00Z','source_point_not_before':'2030-01-07T03:15:00Z'},
            {'latest_start':'2030-01-08T06:15:00Z','source_point_not_before':'2030-01-08T03:15:00Z'}]
        result=project(previous,proposed,6*3600)
        self.assertTrue(result['fits_36h_assumptions']);self.assertFalse(result['native_timing_accepted'])
        self.assertEqual(result['maximum_projected_recovery_point_age_seconds'],35*3600+45*60)
        self.assertFalse(project(previous,proposed,7*3600)['fits_36h_assumptions'])

    def test_v2_actual_recovery_point_can_be_stale_with_recent_restore(self):
        value=evidence();value['latest_restore']['backup_completed_at']=(NOW-dt.timedelta(hours=40)).isoformat()
        result=health.evaluate(value,NOW)
        self.assertEqual(result['schema'],'careertrellis.onedrive-retention-health/v2')
        self.assertEqual(result['recovery_point_age_seconds'],40*3600)
        self.assertIn('recovery_point_stale',result['reasons'])
        cached=dict(result,authenticated=True,baseline_bindings=BINDINGS,artifact_revision=REV,application_release=RELEASE,activation_sha256='d'*64)
        health.validate_cached_health(cached)
        for field,item in (('recovery_point_age_seconds',True),('backup_completed_at',NOW.isoformat()),('result','ok')):
            changed=dict(cached);changed[field]=item
            with self.subTest(field=field),self.assertRaises(ValueError):health.validate_cached_health(changed)

    def test_cached_health_recomputes_counts_quota_namespace_reasons_and_severity(self):
        def cache(value,when=NOW):
            return dict(health.evaluate(value,when),authenticated=True,baseline_bindings=BINDINGS,
                artifact_revision=REV,application_release=RELEASE,activation_sha256='d'*64)
        for case in ('namespace','urgent','warning','schedule','one','none'):
            value=evidence()
            if case=='namespace':value['namespace_bytes']=health.NAMESPACE_BUDGET+1
            elif case=='urgent':value['quota']['free']=150
            elif case=='warning':value['quota']['free']=200
            elif case=='schedule':value['schedule_ready']=False
            elif case=='one':value['catalog']['entries']=value['catalog']['entries'][:1]
            else:value['catalog']['entries']=[];value['latest_restore']=None
            document=cache(value);health.validate_cached_health(document)
            with self.subTest(case=case):
                changed=dict(document,result='ok')
                with self.assertRaises(ValueError):health.validate_cached_health(changed)
                changed=dict(document,reasons=[])
                with self.assertRaises(ValueError):health.validate_cached_health(changed)
        valid=cache(evidence())
        for changes in ({'accepted_bundle_count':0},{'accepted_bundle_count':1},
                {'reasons':['no_verified_bundle'],'result':'alert'},
                {'reasons':['account_usage_urgent'],'result':'urgent'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):health.validate_cached_health(dict(valid,**changes))

    def test_fractional_36h_boundary_uses_actual_evaluation_time_not_display_integer(self):
        value=evidence();value['latest_restore']['backup_completed_at']=(NOW-dt.timedelta(seconds=health.MAX_BACKUP_AGE-.1)).isoformat()
        before=health.evaluate(value,NOW)
        self.assertNotIn('recovery_point_stale',before['reasons'])
        later=NOW+dt.timedelta(seconds=.2)
        after=health.evaluate(value,later)
        self.assertEqual(after['observed_at'],later.isoformat())
        self.assertEqual(after['recovery_point_age_seconds'],health.MAX_BACKUP_AGE)
        self.assertIn('recovery_point_stale',after['reasons'])
        cached=dict(after,authenticated=True,baseline_bindings=BINDINGS,artifact_revision=REV,
            application_release=RELEASE,activation_sha256='d'*64)
        health.validate_cached_health(cached)
        with self.assertRaises(ValueError):health.validate_cached_health(dict(cached,reasons=[],result='ok'))
    def test_daily_calendar_must_match_policy_not_just_an_enabled_timer(self):
        from deploy.tests.test_onedrive_coordinator import policy
        active=policy();active.data['schedule_hour_utc']=18;active.data['schedule_minute_utc']=0
        # Reconstruct so the digest binds the same reviewed schedule.
        active=health.coordinator.Policy(active.data)
        raw=b'[Timer]\nOnCalendar=*-*-* 18:00:00 UTC\n'
        self.assertEqual(health.validate_daily_calendar(raw,active),'*-*-* 18:00:00 UTC')
        for changed in (raw.replace(b'18:00',b'00:00'),
                        raw+b'OnCalendar=*-*-* 19:00:00 UTC\n',
                        b'[DEFAULT]\nOnCalendar=*-*-* 18:00:00 UTC\n[Timer]\n'):
            with self.assertRaises(ValueError):health.validate_daily_calendar(changed,active)

    def test_monitor_evidence_requires_real_selector_and_fresh_selector_bound_census(self):
        from deploy.onedrive_retention import activation
        context=SimpleNamespace(bindings=BINDINGS,revision=REV,document={'application_release':RELEASE})
        selected=SimpleNamespace(digest='d'*64)
        now=dt.datetime.now(dt.timezone.utc)
        document=health.evaluate(evidence(),NOW)
        document.update(authenticated=True,observed_at=now.isoformat(),baseline_bindings=BINDINGS,
            artifact_revision=REV,application_release=RELEASE,activation_sha256=selected.digest,
            latest_restore_completed_at=(now-dt.timedelta(hours=1)).isoformat(),backup_completed_at=(now-dt.timedelta(hours=2)).isoformat())
        with patch.object(health,'_context',return_value=context),patch.object(health,'_installation'), \
             patch.object(activation,'routing_state',return_value=selected), \
             patch.object(activation,'require_activated') as require, \
             patch.object(health,'_document',return_value=(health.bundle.canonical(document),document)), \
             patch.object(health.baseline,'native_context',return_value=context):
            result=health.native_monitor_evidence('20261027-000000F')
            require.assert_called_once_with(context)
            self.assertEqual(result['result'],'ok');self.assertEqual(result['backup_label'],document['backup_label'])
        with patch.object(health,'_context',return_value=context),patch.object(health,'_installation'), \
             patch.object(activation,'routing_state',return_value=None),patch.object(health,'_document') as read:
            with self.assertRaises(health.HealthRefused):health.native_monitor_evidence('20261027-000000F')
            read.assert_not_called()
        with patch.object(health,'_context',return_value=context),patch.object(health,'_installation'), \
             patch.object(activation,'routing_state',return_value=selected),patch.object(activation,'require_activated'), \
             patch.object(health,'_document') as read:
            result=health.native_monitor_evidence('20261027-000000F',unavailable=True)
            self.assertEqual(result['result'],'unavailable');self.assertIsNone(result['health_sha256'])
            read.assert_not_called()
        for changes in ({'activation_sha256':'f'*64},
                        {'observed_at':(now-dt.timedelta(seconds=301)).isoformat()}):
            changed=dict(document,**changes)
            with patch.object(health,'_context',return_value=context),patch.object(health,'_installation'), \
                 patch.object(activation,'routing_state',return_value=selected),patch.object(activation,'require_activated'), \
                 patch.object(health,'_document',return_value=(health.bundle.canonical(changed),changed)), \
                 self.assertRaises(health.HealthRefused):health.native_monitor_evidence('20261027-000000F')

    def test_database_proof_reads_preserve_real_required_size_bound_signature(self):
        from deploy.onedrive_retention import guard,execution
        value=evidence();snapshot=value['catalog']
        now=dt.datetime.now(dt.timezone.utc).isoformat()
        snapshot['observed_at']=now
        native=dict(value['latest_restore'],attempt=1,transaction='a'*32)
        native.pop('receipt_sha256');native.pop('terminal_proof_sha256');native.pop('backup_completed_at')
        raw=health.bundle.canonical(native)
        snapshot['entries'][0]['native_restore_receipt_sha256']=health.sha(raw)
        proof={'unit':'careertrellis-onedrive-bundle-restore-'+'a'*32+'.service'}
        proof_raw=health.bundle.canonical(proof)
        record={'evidence':{'native':native,'download':{'transaction':'a'*32},
                            'terminal_proof_sha256':health.sha(proof_raw)}}
        store=SimpleNamespace(io=SimpleNamespace(locked=lambda:nullcontext()),snapshot=lambda inventory:snapshot,
                              records=lambda:{'1'*64:record,'2'*64:record})
        roots={'complete.json':raw,'unit-terminal-proof.json':proof_raw,
               'ownership.json':health.bundle.canonical({'owner':{'synthetic':True}}),
               'unit-stop-intent.json':health.bundle.canonical({'synthetic_stop':True})}
        reads=[]
        def read(path,*,maximum):
            self.assertEqual(maximum,131072);reads.append(path.name);return roots[path.name]
        context=SimpleNamespace(bindings=BINDINGS)
        observed={'authenticated':True,'catalog':snapshot,'inventory':{'synthetic_inventory':True},
                  'quota':dict(value['quota'],observed_at=now),
                  'namespace_bytes':value['namespace_bytes']}
        with patch.object(health,'_context',return_value=context),patch.object(health,'_installation'), \
             patch.object(health.catalog,'NativeFiles',return_value=SimpleNamespace(require_repository_lock=Mock())), \
             patch.object(health.catalog,'NativeReadStore',return_value=store), \
             patch.object(guard,'native_catalog_inventory',return_value=observed), \
             patch.object(health.native_restore,'protected',autospec=True,side_effect=read), \
             patch.object(health.native_restore,'accepted_backup_completed_at',return_value=value['latest_restore']['backup_completed_at'],create=True), \
             patch.object(health.native_restore,'validate_terminal_proof') as validate, \
             patch.object(execution,'terminal') as terminal, \
             patch.object(health,'schedule_status',return_value={'result':'ready'}), \
             patch.object(health,'publish_database_health',side_effect=lambda result,context:result), \
             patch.object(health.baseline,'native_context',return_value=context):
            health.database_health(7)
        self.assertEqual(reads,['complete.json','unit-terminal-proof.json','ownership.json','unit-stop-intent.json'])
        validate.assert_called_once();terminal.assert_called_once_with(proof['unit'])

    def test_missing_accepted_provider_object_cannot_publish_native_health(self):
        from deploy.onedrive_retention import guard
        context=SimpleNamespace(bindings=BINDINGS)
        observed={'authenticated':True,'catalog':evidence()['catalog'],
                  'inventory':{'objects':[]},'quota':evidence()['quota'],'namespace_bytes':0}
        store=SimpleNamespace(snapshot=Mock(side_effect=health.catalog.CatalogRefused('missing object')))
        with patch.object(health,'_context',return_value=context),patch.object(health,'_installation'), \
             patch.object(health.catalog,'NativeFiles'),patch.object(health.catalog,'NativeReadStore',return_value=store), \
             patch.object(guard,'native_catalog_inventory',return_value=observed), \
             patch.object(health,'evaluate') as evaluate,patch.object(health,'publish_database_health') as publish:
            with self.assertRaises(health.catalog.CatalogRefused):health.database_health(7)
            store.snapshot.assert_called_once_with(observed['inventory'])
            evaluate.assert_not_called();publish.assert_not_called()

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
        value=evidence();value['latest_restore']['backup_completed_at']=(NOW-dt.timedelta(hours=36,seconds=1)).isoformat()
        result=health.evaluate(value,NOW);self.assertIn('recovery_point_stale',result['reasons'])
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

    def test_only_fixed_reviewed_paired_dropin_is_merged_with_preserved_baseline(self):
        from deploy import retention_install as install
        old='/etc/systemd/system/careertrellis-paired-maintenance.service.d/50-old.conf'
        fixed=install.PAIRED_RW_DROPIN
        plan={'files':{fixed:{'sha256':health.sha(install.PAIRED_RW_BYTES)},
                       '/etc/systemd/system/careertrellis-paired-maintenance.service.d/99-unreviewed.conf':{'sha256':'a'*64}},
              'baseline':{'unit_dropins':{old:'b'*64}}}
        self.assertEqual(health.expected_service_dropins('careertrellis-paired-maintenance.service',plan),
                         {old:'b'*64,fixed:health.sha(install.PAIRED_RW_BYTES)})
        self.assertEqual(health.expected_service_dropins('careertrellis-onedrive-retention.service',plan),{})
        plan['files'][fixed]['sha256']='0'*64
        with self.assertRaises(health.HealthRefused):
            health.expected_service_dropins('careertrellis-paired-maintenance.service',plan)

    def test_future_scheduled_command_is_not_supported_by_allowlist_alone(self):
        from deploy.onedrive_retention import execution
        context=SimpleNamespace(bindings=BINDINGS,revision=REV,document={'application_release':RELEASE})
        with patch.object(health.entry,'parse_command',side_effect=health.entry.EntryRefused()), \
             patch.object(health,'_context',return_value=context),patch.object(health,'_installation'), \
             patch.object(health,'_document',return_value=(b'{}',schedule())):
            with self.assertRaises(health.HealthRefused):health.require_service_commands()
            self.assertEqual(health.schedule_status()['result'],'not_ready')
        with patch.object(health.entry,'parse_command',return_value={'operation':'scheduled-run','role':'web'}), \
             patch.object(execution,'supports_operation',return_value=False):
            with self.assertRaises(health.HealthRefused):health.require_service_commands()


if __name__=='__main__':unittest.main()
