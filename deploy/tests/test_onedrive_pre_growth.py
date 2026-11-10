"""Offline liveness and preservation checks. No runtime module registration."""
import copy
import datetime as dt
import unittest

from deploy.onedrive_retention import pre_growth as pre, policy, coordinator, retire

B=85411840
EARLY=dt.datetime(2026,11,5,11,59,tzinfo=dt.timezone.utc)
LATE=dt.datetime(2026,11,6,11,59,tzinfo=dt.timezone.utc)


def key(n):return format(n+1,'064x')
def active():
    return {'schema':'careertrellis.onedrive-retention-policy/v1','enabled':True,
        'application_release':coordinator.RELEASE,'valid_until':'2027-01-23T00:00:00Z',
        'reserve_percent':20,'peak_multiplier':4,'namespace_budget_bytes':policy.NAMESPACE_BUDGET,
        'keep_verified':2,'grace_seconds':172800,'schedule_hour_utc':12,'schedule_minute_utc':30,
        'artifact_revision':'a'*40,'paired_policy_sha256':'b'*64}
def entries():
    result=[]
    for n in range(6):
        accepted='2026-10-01T12:00:00Z' if n==0 else '2026-11-%02dT12:00:00Z'%n
        if n==5:accepted='2026-11-05T11:58:00Z'
        result.append({'id':key(n),'accepted_at':accepted,'archive_sha256':key(n),'download_sha256':key(n),
            'restore_archive_sha256':key(n),'native_restore_receipt_sha256':key(100+n),'complete':True,
            'current':n==5,'dependencies_resolved':True,'dependencies':[],
            'objects':[{'path':key(n)+'/repository.tar','object_id':'fixture-'+str(n),'size':B,'sha256':key(n)}]})
    return result
def request():return {'schema':pre.SCHEMA,'operation':pre.OPERATION,'transaction':'a'*32,
    'retention_policy_sha256':coordinator.Policy(active()).digest,
    'allocation':{'source_bytes':B,'candidate_bytes':B,'namespace_new_bytes':B}}


class Fake:
    mode='offline-test'
    def __init__(self,now=LATE):
        self.instant=now;self.rows=entries();self.records={};self.calls=[];self.dispatches=0
        self.free=2*1024**3;self.fail=False;self.skip_removal=False;self.fence=False
        self.holds=[{'name':'named-old-recovery','owner':'fixture operator','reason':'recovery hold',
                     'review_after':'2026-10-02T00:00:00Z','bundles':[key(0)]}]
        self.changed_identity=False
        self.mutate_retained=False;self.stale_after=False
    def now(self):return self.instant
    def once(self,tx,name,value):
        if name in self.records:raise ValueError('exclusive intent exists')
        self.records[name]=copy.deepcopy(value);return pre.sha(value)
    def fenced_snapshot(self,tx):
        assert not self.fence;self.fence=True
        try:
            self.calls.append('snapshot')
            observed=LATE if self.stale_after and len(self.calls)>=3 else self.instant
            objects=[copy.deepcopy(obj) for item in self.rows for obj in item['objects']]
            witnesses=[dict(obj,etag='"fixture-'+obj['object_id']+'"',observed_at=observed.isoformat(),
                identity_verified_before_after_download=True,etag_verified_before_after_download=True) for obj in objects]
            if self.changed_identity and len(self.calls)>1:witnesses[0]['etag']='"changed"'
            return {'policy':active(),'catalog':{'schema':'careertrellis.onedrive-bundle-catalog/v1',
                'observed_at':observed.isoformat(),'entries':copy.deepcopy(self.rows),'holds':copy.deepcopy(self.holds)},
                'inventory':{'schema':'careertrellis.onedrive-bundle-inventory/v1','namespace':retire.NAMESPACE,
                    'observed_at':observed.isoformat(),'objects':objects},'identity_witnesses':witnesses,
                'quota':{'total':5*1024**3,'free':self.free,'observed_at':observed.isoformat()},
                'repository_idle':True,'unresolved_retirement':False,'unresolved_growth':False}
        finally:self.fence=False
    def dispatch_retirement(self,tx,plan):
        assert not self.fence
        self.dispatches+=1
        if self.fail:raise RuntimeError('lost acknowledgement')
        retired=sorted(item['bundle_id'] for item in plan['retirement']['candidates'])
        if not self.skip_removal:self.rows=[item for item in self.rows if item['id'] not in retired]
        if self.mutate_retained:self.rows[-1]['native_restore_receipt_sha256']=key(999)
        if self.stale_after:self.instant+=dt.timedelta(minutes=2)
        return {'schema':'careertrellis.pre-growth-retirement-simulation/v1','result':'simulated_complete',
            'transaction':tx,'plan_sha256':pre.sha(plan),'simulated_retired_bundle_ids':retired,
            'simulated_retired_object_count':sum(len(item['objects']) for item in plan['retirement']['candidates']),
            'native_retirement_accepted':False}


class PreGrowthTests(unittest.TestCase):
    def test_jitter_trap_does_not_weaken_grace_or_credit_planned_deletion(self):
        fake=Fake(EARLY);plan=pre.prepare(request(),fake.fenced_snapshot('a'*32),EARLY)
        self.assertEqual(plan['retirement']['namespace_bytes'],6*B)
        self.assertEqual(plan['capacity_before']['namespace_peak_bytes'],7*B)
        self.assertEqual(plan['retirement']['candidates'],[])
        self.assertFalse(plan['capacity_before']['allowed'])
        fake=Fake();plan=pre.prepare(request(),fake.fenced_snapshot('a'*32),LATE)
        self.assertEqual([item['bundle_id'] for item in plan['retirement']['candidates']],[key(1)])
        self.assertTrue({key(0),key(4),key(5)} <= set(plan['retirement']['retained_bundle_ids']))
        self.assertFalse(plan['capacity_before']['allowed'])
    def test_plan_is_deterministic_and_unknown_staging_inventory_is_not_ignored(self):
        snapshot=Fake().fenced_snapshot('a'*32)
        self.assertEqual(pre.prepare(request(),snapshot,LATE),pre.prepare(request(),copy.deepcopy(snapshot),LATE))
        snapshot['inventory']['objects'].append({'path':key(999)+'/repository.tar','object_id':'unknown-staging',
            'size':B,'sha256':key(999)})
        with self.assertRaises(pre.PreGrowthRefused):pre.prepare(request(),snapshot,LATE)
    def test_retirement_only_progress_then_new_actual_facts_breaks_trap(self):
        fake=Fake();result=pre.run(request(),fake)
        self.assertTrue(result['simulated_growth_allowed']);self.assertEqual(fake.dispatches,1)
        self.assertEqual(result['capacity_after']['namespace_peak_bytes'],6*B)
        self.assertFalse(result['native_growth_authorized']);self.assertEqual(result['deletions_performed'],0)
        self.assertIsNone(result['physical_bytes_reclaimed'])
        self.assertIn('retirement-dispatch-intent.json',fake.records)
    def test_hold_and_dependency_closure_preserve_recovery_even_when_capacity_blocked(self):
        fake=Fake();fake.rows[-1]['dependencies']=[key(1)]
        result=pre.run(request(),fake)
        self.assertFalse(result['simulated_growth_allowed']);self.assertEqual(fake.dispatches,0)
        self.assertEqual(len(fake.rows),6)
    def test_unknown_dependencies_stale_identity_or_missing_etag_refuse(self):
        for change in ('dependency','stale','etag'):
            fake=Fake();snapshot=fake.fenced_snapshot('a'*32)
            if change=='dependency':snapshot['catalog']['entries'][0]['dependencies_resolved']=False
            elif change=='stale':snapshot['identity_witnesses'][0]['observed_at']='2026-11-01T00:00:00Z'
            else:snapshot['identity_witnesses'][0]['etag']='*'
            with self.subTest(change=change),self.assertRaises(pre.PreGrowthRefused):pre.prepare(request(),snapshot,LATE)
    def test_changed_locked_identity_refuses_before_child_dispatch(self):
        fake=Fake();fake.changed_identity=True
        with self.assertRaises(pre.PreGrowthRefused):pre.run(request(),fake)
        self.assertEqual(fake.dispatches,0)
    def test_lost_ack_exclusive_intent_prevents_replay(self):
        fake=Fake();fake.fail=True
        with self.assertRaises(pre.PreGrowthRefused):pre.run(request(),fake)
        self.assertIn('retirement-dispatch-intent.json',fake.records)
        with self.assertRaises(pre.PreGrowthRefused):pre.run(request(),fake)
        self.assertEqual(fake.dispatches,1)
    def test_ack_without_observed_removal_never_credits_deletion(self):
        fake=Fake();fake.skip_removal=True
        with self.assertRaises(pre.PreGrowthRefused):pre.run(request(),fake)
        self.assertNotIn('workflow-complete.json',fake.records)
    def test_soft_deletion_does_not_invent_account_quota_reclamation(self):
        fake=Fake();fake.free=1100*1024**2
        result=pre.run(request(),fake)
        self.assertFalse(result['simulated_growth_allowed'])
        self.assertIn('account_reserve_shortfall',result['capacity_after']['reasons'])
    def test_remaining_catalog_mutation_or_predispatch_snapshot_cannot_clear_growth(self):
        for option in ('mutate_retained','stale_after'):
            fake=Fake();setattr(fake,option,True)
            with self.subTest(option=option),self.assertRaises(pre.PreGrowthRefused):pre.run(request(),fake)
            self.assertNotIn('workflow-complete.json',fake.records)
    def test_no_two_accepted_native_records_or_unknown_work_refuses(self):
        fake=Fake();snapshot=fake.fenced_snapshot('a'*32);snapshot['catalog']['entries']=snapshot['catalog']['entries'][:1]
        with self.assertRaises(pre.PreGrowthRefused):pre.prepare(request(),snapshot,LATE)
        snapshot=fake.fenced_snapshot('a'*32);snapshot['unresolved_retirement']=True
        with self.assertRaises(pre.PreGrowthRefused):pre.prepare(request(),snapshot,LATE)
    def test_default_native_and_caller_claimed_native_backend_never_dispatch(self):
        with self.assertRaisesRegex(pre.PreGrowthRefused,'^pre_growth_native_route_unavailable$'):pre.run_native(request())
        with self.assertRaises(pre.PreGrowthRefused):pre.run(request())
        fake=Fake();fake.mode='native-installed'
        with self.assertRaises(pre.PreGrowthRefused):pre.run(request(),fake)
        self.assertEqual(fake.calls,[])
        from deploy.onedrive_retention import entry
        self.assertNotIn('pre_growth.py',entry.FILES)
    def test_closed_request_rejects_paths_commands_and_boolean_allocation(self):
        for change in ({'command':'anything'},{'path':'/other'}):
            value=request();value.update(change)
            with self.assertRaises(pre.PreGrowthRefused):pre.validate_request(value)
        value=request();value['allocation']['candidate_bytes']=True
        with self.assertRaises(pre.PreGrowthRefused):pre.validate_request(value)
