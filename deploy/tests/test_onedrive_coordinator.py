import datetime as dt
import json
import copy
import importlib
from pathlib import Path
import sys
import unittest
import stat
import contextlib
from types import SimpleNamespace
import types
from unittest.mock import patch,Mock
from contextlib import contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
c = importlib.import_module("deploy.onedrive_retention.coordinator")
e = importlib.import_module("deploy.onedrive_retention.endpoint")


def policy():
    return c.Policy({"schema": "careertrellis.onedrive-retention-policy/v1", "enabled": True,
        "application_release": c.RELEASE, "valid_until": "2027-01-24T00:00:00Z", "reserve_percent": 20,
        "peak_multiplier": 4, "namespace_budget_bytes": 512*1024**2, "keep_verified": 2,
        "grace_seconds": 172800, "schedule_hour_utc": 12, "schedule_minute_utc": 30,
        "artifact_revision": "a"*40, "paired_policy_sha256": "b"*64})


def response(request):
    stage = request["payload"]["stage"]
    facts = {"archive_sha256": "a"*64, "archive_bytes": 12345,
             "generation_sha256": "b"*64, "backup_label": "20261026-155056F"}
    if stage in ("publish", "restore"): facts["native_restore_accepted"] = stage == "restore"
    if stage in ("prepare","retire"):
        facts = {"catalog_sha256": "f"*64, "prepared_count" if stage == "prepare" else "retired_count": 0}
    return {"schema": e.RESPONSE, "capture_transaction": request["capture_transaction"],
        "retention_policy_sha256": request["retention_policy_sha256"], "stage": stage,
        "state": "complete", "receipt_sha256": "c"*64,
        "facts": facts}


class Fake:
    def __init__(self, lost=None): self.events=[]; self.files={}; self.lost=lost
    @contextmanager
    def locked(self): yield
    def now(self): return dt.datetime.now(dt.timezone.utc)
    def preflight(self): self.events.append("preflight")
    def begin(self, *args): self.events.append("begin")
    def once(self, name, value):
        if name in self.files: raise RuntimeError("existing")
        self.files[name]=value
    def call(self, request):
        stage=request["payload"]["stage"]; self.events.append(stage)
        if stage==self.lost: raise RuntimeError("lost acknowledgement")
        return response(request)
    def web_preflight(self):
        self.events.append("fresh_web")
        return {"schema":"careertrellis.onedrive-restore-web-preflight/v1", "host":"careertrellis-staging-web-1",
            "authority":"postgres-r2-authoritative", "pending_phase":"activated", "activation_lease_absent":True,
            "services_healthy":True,"release_binding_verified":True,"release":c.RELEASE,"observed_at":self.now().isoformat()}
    def finish(self, state, value): self.events.append(state)


class Tests(unittest.TestCase):
    def history_fixture(self,retired=True,active_policy=None):
        from deploy.onedrive_retention import pre_growth_native as pg
        p=policy() if active_policy is None else active_policy;tx='f'*32;now=c.dt.datetime.now(c.dt.timezone.utc).isoformat()
        allocation={'source_bytes':100,'candidate_bytes':200,'namespace_new_bytes':200}
        snapshot={'allocation':allocation,'quota':{'total':100000,'free':10000 if retired else 90000},
            'namespace_bytes':0,'observed_at':now,'baseline_bindings':{'standalone_policy_sha256':p.digest},'activation_sha256':'c'*64}
        files={'owner.json':{'schema':'careertrellis.pre-growth-web-owner/v1','transaction':tx,'capture_transaction':'d'*32,
            'retention_policy_sha256':p.digest,'activation_sha256':'c'*64,'owner':{'fixture':1},'observed_at':now},
            'capacity-before.json':snapshot,
            'complete.json':{'schema':'careertrellis.pre-growth-web-complete/v1','transaction':tx,'capture_transaction':'d'*32,
                'capacity_allowed':True,'growth_authorized':False,'retirement_dispatched':retired,'observed_at':now}}
        if retired:
            history={'fixture':'bound'}
            request=pg.request({'schema':pg.SCHEMA,'operation':pg.PURPOSE,'transaction':tx,'retention_policy_sha256':p.digest,
                'activation_sha256':'c'*64,'web_history_sha256':c.hashlib.sha256(c.bundle.canonical(history)).hexdigest(),'allocation':allocation})
            submitted={'request':request,'web_history':history};intent=c.hashlib.sha256(c.bundle.canonical(
                {'schema':'careertrellis.pre-growth-ingress/v1',**submitted})).hexdigest()
            files.update({'submit-intent.json':submitted,'submit-ack.json':{'schema':'careertrellis.pre-growth-dispatched/v1',
                'transaction':tx,'intent_sha256':intent,'state':'dispatched','growth_authorized':False},
                'terminal-observation.json':{'transaction':tx,'state':'verification_required','growth_authorized':False},
                'verify-intent.json':{'transaction':tx,'retention_policy_sha256':p.digest},
                'verify-ack.json':{'transaction':tx,'state':'complete','terminal_proof_sha256':'e'*64,'growth_authorized':False},
                'capacity-after.json':copy.deepcopy(snapshot)})
            files['capacity-after.json']['quota']['free']=90000
        def audit():
            root=c.STATE/'pre-growth-web'/tx
            with patch.object(c,'directory'),patch.object(c,'_bound_history_policy',return_value=p) as bound,\
                 patch.object(c.Path,'iterdir',side_effect=lambda:(root/name for name in files)),\
                 patch.object(c,'read_private',side_effect=lambda path:files[path.name]):
                result=c.pre_growth_web_history(tx)
                bound.assert_called_once_with(p.digest);return result
        return files,audit

    def test_closed_pre_growth_history_checks_all_phase_payloads_and_exact_branch_sets(self):
        files,audit=self.history_fixture();self.assertEqual(audit()['state'],'complete')
        for name in ('submit-intent.json','submit-ack.json','terminal-observation.json','verify-intent.json','verify-ack.json','capacity-after.json'):
            original=files.pop(name)
            with self.subTest(missing=name),self.assertRaises(Exception):audit()
            files[name]=original
        for name,key,value in (('submit-ack.json','transaction','a'*32),('submit-ack.json','intent_sha256','0'*64),
            ('submit-ack.json','state','unknown'),('terminal-observation.json','state','unknown'),
            ('verify-intent.json','transaction','a'*32),('verify-intent.json','retention_policy_sha256','0'*64),
            ('verify-ack.json','state','unknown')):
            original=files[name][key];files[name][key]=value
            with self.subTest(name=name,key=key),self.assertRaises(c.Refused):audit()
            files[name][key]=original
        files,audit=self.history_fixture(False);self.assertEqual(audit()['state'],'complete')
        files['verify-intent.json']={'transaction':'f'*32,'retention_policy_sha256':policy().digest}
        with self.assertRaises(c.Refused):audit()

    def test_stricter_policy_limits_change_actual_and_historical_capacity_decisions(self):
        for changes,free,source,candidate in (({'reserve_percent':60},500000,100,200),
                ({'peak_multiplier':100},300000,2000,2000),({'namespace_budget_bytes':1},900000,100,200)):
            p=c.Policy(dict(policy().data,**changes))
            value={'quota':{'total':1000000,'free':free},'allocation':{'source_bytes':source,'candidate_bytes':candidate,
                'namespace_new_bytes':candidate},'namespace_bytes':0}
            self.assertTrue(c._capacity_with_policy(value,policy())['allowed'])
            self.assertFalse(c._capacity_with_policy(value,p)['allowed'])
        # A historical receipt claiming success under stricter limits fails;
        # supplied default-looking snapshots cannot silently weaken the policy.
        p=c.Policy(dict(policy().data,reserve_percent=95))
        files,audit=self.history_fixture(False,p)
        with self.assertRaises(c.Refused):audit()

    def pre_growth_fixture(self,*,lost=False,after_allowed=True,selected=True):
        from deploy.onedrive_retention import activation,pre_growth_native as pg,native_restore
        backend=c.NativeWEB(policy());backend.retention_selector=SimpleNamespace(digest='c'*64) if selected else None
        backend.retention_context=SimpleNamespace(bindings={'standalone_policy_sha256':backend.policy.digest})
        backend.web_history=Mock(return_value={'fixture':'fresh-authenticated-WEB-PURPOSE'})
        blobs={};dirs=set();calls=[]
        before={'allocation':{'source_bytes':100,'candidate_bytes':200,'namespace_new_bytes':200}}
        backend._pre_growth_capacity=Mock(side_effect=[(before,{'allowed':False}),(before,{'allowed':after_allowed})])
        def call(tx,op,payload):
            calls.append(op)
            if op==pg.PURPOSE:
                if lost:raise OSError('lostack')
                sha=c.hashlib.sha256(c.bundle.canonical({'schema':'careertrellis.pre-growth-ingress/v1',**payload})).hexdigest()
                return {'schema':'careertrellis.pre-growth-dispatched/v1','transaction':tx,'state':'dispatched','intent_sha256':sha,'growth_authorized':False}
            if op.endswith('_status'):return {'transaction':tx,'state':'verification_required','growth_authorized':False}
            return {'transaction':tx,'state':'complete','terminal_proof_sha256':'e'*64,'growth_authorized':False}
        backend._pre_growth_call=Mock(side_effect=call)
        def exclusive(path,raw):
            if path in blobs:raise ValueError('exclusive')
            blobs[path]=json.loads(raw)
        def mkdir(path,*args,**kwargs):
            if path in dirs:raise FileExistsError('no replay')
            dirs.add(path)
        @contextmanager
        def context():
            with patch.object(c,'directory'),patch.object(c,'fsync_directory'),\
                 patch.object(c.Path,'iterdir',return_value=iter(())),\
                 patch.object(c.Path,'mkdir',autospec=True,side_effect=mkdir),\
                 patch.object(c.hydrate,'exclusive',side_effect=exclusive),\
                 patch.object(c.os.path,'lexists',side_effect=lambda path:path in blobs),\
                 patch.object(c.secrets,'token_hex',return_value='f'*32),patch.object(c.time,'sleep'),\
                 patch.object(native_restore,'process_identity',return_value={'fixture_owner':1}),\
                 patch.object(activation,'require_activated',return_value=backend.retention_selector):yield
        return backend,blobs,calls,context

    def test_pre_growth_once_status_readonly_verify_once_then_fresh_guard(self):
        from deploy.onedrive_retention import pre_growth_native as pg
        backend,blobs,calls,context=self.pre_growth_fixture()
        with context():backend.pre_growth('d'*32)
        self.assertEqual(calls,[pg.PURPOSE,pg.PURPOSE+'_status',pg.PURPOSE+'_verify'])
        self.assertEqual(backend._pre_growth_capacity.call_count,2)
        backend.web_history.assert_called_once_with('f'*32,pg.PURPOSE)
        self.assertTrue(any(path.name=='complete.json' for path in blobs))
        self.assertTrue(any(path.name=='verify-intent.json' for path in blobs))

    def test_first_selected_permanent_cycle_renews_even_with_initial_capacity_allowed(self):
        backend,blobs,calls,context=self.pre_growth_fixture()
        backend.policy=c.Policy(dict(backend.policy.data,expiry_mode='conditional_permanent'))
        value={'allocation':{'source_bytes':100,'candidate_bytes':200,'namespace_new_bytes':200}}
        backend._pre_growth_capacity=Mock(side_effect=[(value,{'allowed':True}),(value,{'allowed':True})])
        with context():backend.pre_growth('d'*32)
        self.assertEqual(len(calls),3);self.assertEqual(backend._pre_growth_capacity.call_count,2)
        self.assertTrue(any(path.name=='verify-ack.json' for path in blobs))
        self.assertTrue(any(path.name=='capacity-after.json' for path in blobs))
        p=c.Policy(dict(policy().data,expiry_mode='conditional_permanent'))
        records,audit=self.history_fixture(True,p)
        records['capacity-before.json']['quota']['free']=90000
        self.assertEqual(audit()['state'],'complete') # Allowed capacity does not negate required renewal history.

    def test_pre_growth_lostack_has_no_retry_and_no_begin(self):
        backend,blobs,calls,context=self.pre_growth_fixture(lost=True)
        with context():
            with self.assertRaises(c.Refused):backend.pre_growth('d'*32)
            with self.assertRaises(Exception):backend.pre_growth('d'*32)
        self.assertEqual(len(calls),1)
        self.assertTrue(any(path.name=='failed.json' for path in blobs))
        self.assertFalse(any(path.name=='verify-intent.json' for path in blobs))

    def test_retirement_success_never_credits_future_space_and_bootstrap_never_submits(self):
        backend,blobs,calls,context=self.pre_growth_fixture(after_allowed=False)
        with context(),self.assertRaises(c.Refused):backend.pre_growth('d'*32)
        self.assertFalse(any(path.name=='complete.json' for path in blobs))
        backend,blobs,calls,context=self.pre_growth_fixture(selected=False)
        with context():backend.pre_growth('d'*32)
        self.assertEqual(calls,[]);self.assertEqual(blobs,{})

    def test_capacity_requires_authenticated_rpc_allocation_and_fresh_selector_binding(self):
        from deploy.onedrive_retention import guard
        backend=c.NativeWEB(policy());backend.retention_context=SimpleNamespace(bindings={'fixture':'bound'})
        backend.retention_selector=SimpleNamespace(digest='c'*64)
        value={'schema':'careertrellis.retention-capacity-preflight/v1','history':{'baseline_bindings':{'fixture':'bound'}},
            'quota':{'total':1000000,'free':900000},'namespace_bytes':0,'account_scope':'fixedproviderbinding',
            'observed_at':backend.now().isoformat(),'baseline_bindings':{'fixture':'bound'},
            'allocation':{'source_bytes':100,'candidate_bytes':200,'namespace_new_bytes':200},'activation_sha256':'c'*64}
        backend._pre_growth_call=Mock(return_value=value)
        with patch.object(guard,'validate_peer_history'):
            _,assessment=backend._pre_growth_capacity('f'*32);self.assertTrue(assessment['allowed'])
            del value['allocation']
            with self.assertRaises(c.Refused):backend._pre_growth_capacity('f'*32)
        self.assertEqual(backend._pre_growth_call.call_args.args[1],'retention_capacity_snapshot')

    def test_begin_updates_fixed_own_latest_path(self):
        from deploy.onedrive_retention import baseline,guard
        backend=c.NativeWEB(policy());backend._prior_terminal=Mock();backend.once=Mock()
        with patch.object(baseline,'native_context'),patch.object(guard,'native_paired_handoff',return_value=None),\
             patch.object(c.Path,'mkdir'),patch.object(c,'fsync_directory'),patch.object(c,'public_status'),\
             patch.object(c.Path,'read_text',return_value='fixture) '+' '.join(['1']*25)),\
             patch.object(c,'replace_private') as replace:
            backend.begin('d'*32,backend.policy)
        replace.assert_called_once_with(backend.root/'latest.json',{'transaction':'d'*32,'state':'running'})
    def test_pipeline_proof_is_fresh_after_download_and_before_restore(self):
        fake=Fake(); c.run("d"*32,policy(),fake)
        self.assertEqual(fake.events,["preflight","begin","hydrate","publish","fresh_web","restore","prepare","retire","complete"])
    def test_lost_ack_preserves_intent_and_never_redispatches(self):
        fake=Fake("publish")
        with self.assertRaises(c.Refused): c.run("d"*32,policy(),fake)
        self.assertEqual(fake.events.count("publish"),1)
        self.assertIn("publish-intent.json",fake.files); self.assertNotIn("restore",fake.events)
    def test_policy_weaker_or_bool_counter_refuses(self):
        for key,value in (("reserve_percent",19),("peak_multiplier",3),("keep_verified",1),
                          ("grace_seconds",True),("namespace_budget_bytes",512*1024**2+1)):
            with self.subTest(key=key),self.assertRaises(c.Refused): c.Policy(dict(policy().data,**{key:value}))
    def test_stale_web_proof_refuses(self):
        fake=Fake(); value=fake.web_preflight(); value["observed_at"]="2026-01-01T00:00:00Z"
        with self.assertRaises(c.Refused): c.web_proof(value,fake.now())
    def test_changed_archive_or_simulated_restore_never_reaches_retirement(self):
        for mode in ("archive_drift", "native_false"):
            fake=Fake(); original=fake.call
            def altered(request):
                value=original(request)
                if mode=="archive_drift" and value["stage"]=="publish":value["facts"]["archive_sha256"]="f"*64
                if mode=="native_false" and value["stage"]=="restore":value["facts"]["native_restore_accepted"]=False
                return value
            fake.call=altered
            with self.subTest(mode=mode),self.assertRaises(c.Refused):c.run("d"*32,policy(),fake)
            self.assertNotIn("retire",fake.events)
    def test_disabled_expired_policy_stops_before_begin(self):
        for change in ({"enabled":False},{"valid_until":"2026-01-01T00:00:00Z"}):
            fake=Fake()
            with self.assertRaises(c.Refused):c.run("d"*32,c.Policy(dict(policy().data,**change)),fake)
            self.assertEqual(fake.events,[])

    def test_weekly_capture_label_mismatch_stops_before_provider_publication(self):
        fake=Fake(); fake.expected_backup_label='20261027-155056F'
        with self.assertRaises(c.Refused):c.run('d'*32,policy(),fake)
        self.assertIn('hydrate',fake.events)
        self.assertNotIn('publish',fake.events)
        self.assertNotIn('restore',fake.events)

    def test_weekly_capture_label_match_uses_normal_verified_pipeline(self):
        fake=Fake(); fake.expected_backup_label='20261026-155056F'
        c.run('d'*32,policy(),fake)
        self.assertIn('complete',fake.events)

    def test_native_expected_label_is_strict(self):
        for value in ('',True,'20261026-155056F\n','../20261026-155056F'):
            with self.subTest(value=value),self.assertRaises(c.Refused):c.NativeWEB(policy(),value)


class InheritedWebFenceContracts(unittest.TestCase):
    def test_partial_activation_blocks_manual_bootstrap_before_further_preflight(self):
        from deploy.onedrive_retention import activation,entry
        with patch.object(activation,'routing_state',side_effect=activation.ActivationRefused()), \
             patch.object(entry,'verify_install') as verify,self.assertRaises(activation.ActivationRefused):
            c.NativeWEB(policy()).preflight()
        verify.assert_not_called()

    def test_absent_activation_allows_bootstrap_to_reach_source_verification(self):
        from deploy.onedrive_retention import activation,entry
        with patch.object(activation,'routing_state',return_value=None) as selected, \
             patch.object(entry,'verify_install',side_effect=RuntimeError('source fixture stop')), \
             self.assertRaisesRegex(RuntimeError,'source fixture stop'):
            c.NativeWEB(policy()).preflight()
        selected.assert_called_once_with()

    def test_verified_paired_child_reuses_same_descriptor_without_reopening_or_unlocking_parent(self):
        from deploy.onedrive_retention import baseline,guard
        lock = SimpleNamespace(st_dev=1,st_ino=2,st_mode=stat.S_IFREG|0o644,st_uid=0,st_gid=0,
            st_nlink=1,st_size=0,st_mtime_ns=3,st_ctime_ns=4)
        def metadata(path):
            if path == Path('/run/lock/careertrellis-maintenance.lock'):return lock
            return SimpleNamespace(st_dev=1,st_ino=3,st_mode=stat.S_IFDIR|(
                0o1777 if path == Path('/run/lock') else 0o755),st_uid=0,st_gid=0)
        context = SimpleNamespace()
        handoff = SimpleNamespace(maintenance_fd=10)
        native = c.NativeWEB(policy());native.lock_transaction='d'*32
        fcntl = SimpleNamespace(flock=Mock(),LOCK_EX=2,LOCK_NB=4)
        with patch.object(c.os,'geteuid',return_value=0,create=True), \
             patch.object(c.os,'getegid',return_value=0,create=True), \
             patch.object(c.socket,'gethostname',return_value='careertrellis-staging-web-1'), \
             patch.object(Path,'lstat',autospec=True,side_effect=metadata), \
             patch.object(c.os,'fstat',return_value=lock), \
             patch.object(c.os,'open') as opened,patch.object(c.os,'close') as closed, \
             patch.object(c,'control',return_value=contextlib.nullcontext()), \
             patch.object(baseline,'native_context',return_value=context), \
             patch.object(guard,'native_paired_handoff',return_value=handoff) as reader, \
             patch.object(guard,'_paired_inherited_maintenance') as inherited, \
             patch.dict(sys.modules,{'fcntl':fcntl}):
            with native.locked():pass
        opened.assert_not_called();fcntl.flock.assert_not_called()
        reader.assert_called_once_with('d'*32,context,initial=True)
        self.assertEqual(inherited.call_count,2);closed.assert_called_once_with(10)


class ScheduledEligibilityTests(unittest.TestCase):
    def fixture(self):
        p=policy();now=dt.datetime.now(dt.timezone.utc)
        bindings={'standalone_manifest_sha256':'1'*64,'paired_manifest_sha256':'2'*64,
            'paired_policy_sha256':p.data['paired_policy_sha256'],'standalone_policy_sha256':p.digest}
        context=SimpleNamespace(bindings=bindings,revision=p.data['artifact_revision'],
            document={'application_release':p.data['application_release']})
        selected=SimpleNamespace(digest='3'*64,role='web',bindings=bindings)
        evidence={'schema':'careertrellis.onedrive-retention-replacement-evidence/v1','observed_at':now.isoformat(),
            'artifact_revision':context.revision,'application_release':context.document['application_release'],
            'baseline_bindings':copy.deepcopy(bindings),'catalog_sha256':'4'*64,
            'replacement_bundles':[{'archive_sha256':str(index+5)*64,'native_receipt_sha256':str(index+7)*64,
                'terminal_proof_sha256':str(index+1)*64,'backup_label':'20261026-155056F',
                'accepted_at':(now-dt.timedelta(hours=16-index)).isoformat()} for index in range(2)]}
        answer={'schema':'careertrellis.onedrive-retention-scheduled-eligibility/v1','transaction':'d'*32,
            'retention_policy_sha256':p.digest,'activation_sha256':selected.digest,'replacement_evidence':evidence,
            'current_backup':{'schema':'careertrellis.onedrive-current-backup/v1',
                'backup_label':'20261026-155056F','backup_completed_at':(now-dt.timedelta(hours=15)).isoformat(),
                'observed_at':now.isoformat()}}
        return p,context,selected,answer

    @contextmanager
    def native_preflight(self,*,selection='valid',scheduled=True,answer_change=None):
        from deploy.onedrive_retention import activation,entry
        p,context,selected,answer=self.fixture()
        if answer_change is not None:answer_change(answer)
        runtime=SimpleNamespace(preflight_runtime=Mock(return_value=SimpleNamespace(verify_sources=Mock(),health=Mock())))
        paired_entry=SimpleNamespace(verify_install=Mock(),load_policy=Mock(return_value=SimpleNamespace(
            digest=p.data['paired_policy_sha256'],data={'minimum_capture_age_seconds':12*3600})))
        client=Mock();client.call.return_value=answer
        package=types.ModuleType('paired_maintenance');package.entry=paired_entry;package.runtime=runtime
        client_module=types.ModuleType('paired_maintenance.client');client_module.Client=Mock(return_value=client)
        backend=c.NativeWEB(p,scheduled=scheduled);backend.locked=lambda:contextlib.nullcontext()
        backend.pre_growth=Mock() # Scheduling-only fixture; separate lifecycle tests below.
        backend._prior_terminal=Mock()
        backend.begin=Mock(side_effect=RuntimeError('fixture_begin_reached'));backend.call=Mock()
        mode=Mock(return_value=None if selection=='absent' else selected)
        if selection=='partial':mode.side_effect=activation.ActivationRefused()
        activated=Mock(return_value=selected)
        if selection=='invalid':activated.side_effect=activation.ActivationRefused()
        with patch.object(activation,'routing_state',mode), \
             patch.object(activation,'require_activated',activated), \
             patch.object(entry,'verify_install',return_value=context.revision), \
             patch.object(entry,'_native_dependencies',return_value=context), \
             patch.object(c,'native_policy_binding'), \
             patch.dict(sys.modules,{'paired_maintenance':package,'paired_maintenance.client':client_module}):
            yield backend,p,client,client_module

    def test_scheduled_evidence_is_required_before_any_native_transaction_intent(self):
        with self.native_preflight() as (backend,p,client,client_module):
            with self.assertRaisesRegex(RuntimeError,'fixture_begin_reached'):c.run('d'*32,p,backend)
            backend.begin.assert_called_once();backend.call.assert_not_called()
            client.call.assert_called_once_with('retention_activation_evidence',{
                'retention_policy_sha256':p.digest,'activation_sha256':'3'*64})
            self.assertRegex(backend.scheduled_eligibility_sha256,'^[0-9a-f]{64}$')

    def test_unresolved_prior_operation_stops_before_remote_eligibility_traversal(self):
        with self.native_preflight() as (backend,p,client,_):
            backend._prior_terminal.side_effect=c.Refused('unfinished fixture')
            with self.assertRaises(c.Refused):c.run('d'*32,p,backend)
            client.call.assert_not_called();backend.begin.assert_not_called()

    def test_recent_actual_native_acceptance_skips_without_creating_or_refreshing_a_run(self):
        def recent(answer):
            answer['replacement_evidence']['replacement_bundles'][-1]['accepted_at']=(
                dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=1)).isoformat()
            answer['current_backup']['backup_completed_at']=(
                dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=2)).isoformat()
        with self.native_preflight(answer_change=recent) as (backend,p,client,_), \
                patch.object(c,'public_status') as status:
            result=c.run('d'*32,p,backend)
            self.assertEqual(result['state'],'skipped')
            self.assertEqual(result['reason'],'recent_verified_native_backup')
            self.assertFalse(result['new_backup_created'])
            self.assertEqual(result['eligibility_sha256'],backend.scheduled_eligibility_sha256)
            backend.begin.assert_not_called();backend.call.assert_not_called();status.assert_not_called()

    def test_exact_minimum_age_is_due_and_does_not_reuse_an_old_skip(self):
        with self.native_preflight() as (backend,p,client,_):
            fixed=dt.datetime.now(dt.timezone.utc)
            backend.now=lambda:fixed
            client.call.return_value['replacement_evidence']['replacement_bundles'][-1]['accepted_at']=(
                fixed-dt.timedelta(hours=12)).isoformat()
            backend.scheduled_not_due={'state':'skipped'}
            with self.assertRaisesRegex(RuntimeError,'fixture_begin_reached'):c.run('d'*32,p,backend)
            self.assertIsNone(backend.scheduled_not_due);backend.begin.assert_called_once()

    def test_recent_acceptance_of_old_label_or_old_capture_does_not_suppress_due_work(self):
        for changed in ('newer-backup','old-capture'):
            def mutation(answer):
                answer['replacement_evidence']['replacement_bundles'][-1]['accepted_at']=(
                    dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=1)).isoformat()
                if changed=='newer-backup':answer['current_backup']['backup_label']='20261027-160000F'
            with self.subTest(changed=changed),self.native_preflight(answer_change=mutation) as (backend,p,_,_):
                with self.assertRaisesRegex(RuntimeError,'fixture_begin_reached'):c.run('d'*32,p,backend)
                self.assertIsNone(backend.scheduled_not_due);backend.begin.assert_called_once()

    def test_missing_or_partial_selector_never_starts_or_collects_evidence(self):
        for selection in ('absent','partial','invalid'):
            with self.subTest(selection=selection),self.native_preflight(selection=selection) as (backend,p,client,_):
                with self.assertRaises(Exception):c.run('d'*32,p,backend)
                backend.begin.assert_not_called();backend.call.assert_not_called();client.call.assert_not_called()

    def test_wrong_selector_fake_stale_different_policy_or_one_acceptance_never_starts(self):
        mutations=(lambda a:a.update(activation_sha256='f'*64),
            lambda a:a.update(schema='caller-claims'),lambda a:a.update(authenticated=True),
            lambda a:a.update(transaction='f'*32),lambda a:a.update(retention_policy_sha256='f'*64),
            lambda a:a['replacement_evidence'].update(observed_at='2020-01-01T00:00:00Z'),
            lambda a:a['replacement_evidence']['baseline_bindings'].update(standalone_policy_sha256='f'*64),
            lambda a:a['replacement_evidence']['replacement_bundles'].pop(),
            lambda a:a['replacement_evidence']['replacement_bundles'][1].update(
                native_receipt_sha256=a['replacement_evidence']['replacement_bundles'][0]['native_receipt_sha256']))
        for mutate in mutations:
            with self.native_preflight(answer_change=mutate) as (backend,p,client,_):
                with self.assertRaises(Exception):c.run('d'*32,p,backend)
                backend.begin.assert_not_called();backend.call.assert_not_called()

    def test_manual_bootstrap_remains_possible_without_selector_but_never_with_partial_transition(self):
        for selection in ('absent','valid'):
            with self.native_preflight(selection=selection,scheduled=False) as (backend,p,client,_):
                with self.assertRaisesRegex(RuntimeError,'fixture_begin_reached'):c.run('d'*32,p,backend)
                backend.begin.assert_called_once();client.call.assert_not_called()
        with self.native_preflight(selection='partial',scheduled=False) as (backend,p,client,_):
            with self.assertRaises(Exception):c.run('d'*32,p,backend)
            backend.begin.assert_not_called();client.call.assert_not_called()

    def test_scheduled_flag_cannot_be_a_truthy_counter(self):
        for flag in (1,'true',None):
            with self.assertRaises(c.Refused):c.NativeWEB(policy(),scheduled=flag)

    def test_selector_change_after_authentic_response_still_prevents_begin(self):
        from deploy.onedrive_retention import activation
        _,_,selected,_=self.fixture();changed=copy.deepcopy(selected);changed.digest='4'*64
        with self.native_preflight() as (backend,p,client,_), \
             patch.object(activation,'routing_state',side_effect=[selected,changed]):
            with self.assertRaises(c.Refused):c.run('d'*32,p,backend)
            client.call.assert_called_once();backend.begin.assert_not_called()
