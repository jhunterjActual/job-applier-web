"""Synthetic protocol tests grant no native activation authority."""
import copy
import datetime as dt
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock

from deploy.onedrive_retention import activation_transition as t
from deploy.tests.test_onedrive_activation_readiness import evidence,NOW,BINDINGS


def fixture():
    context=SimpleNamespace(role='web',revision='a'*40,bindings=BINDINGS.copy(),document={
        'standalone_revision':'a'*40,'application_release':'b'*40,
        'valid_until':(NOW+dt.timedelta(days=1)).isoformat(),'acceptance_receipts':{'ops':'e'*64}})
    installation={'web':'e'*64,'db':'f'*64};proof=evidence()
    review={'schema':t.REVIEW_SCHEMA,'transaction':'d'*32,'baseline_sha256':t.digest(context.document),
        'baseline_bindings':context.bindings.copy(),'installation_receipts':installation.copy(),
        'replacement_evidence_sha256':t.digest(proof),'reviews':{key:'1'*64 for key in t.a.REVIEWS},
        'reviewed_at':NOW.isoformat(),'valid_until':(NOW+dt.timedelta(minutes=10)).isoformat(),
        'active_valid_until':context.document['valid_until']}
    return context,proof,installation,review


def ack(request):
    return {key:request[key] for key in ('transaction','role','phase','intent_sha256')} | {
        'request_sha256':t.digest(request),'result':{'prepare':'prepared','commit':'committed'}[request['phase']]}


class Tests(unittest.TestCase):
    def boundary_fixture(self,when,*,unknown_history=False):
        from deploy.onedrive_retention import guard,native_restore
        context,candidate=self.candidate();intent=candidate['intent'];tx=intent['transaction']
        participant=object.__new__(t.NativeLocal);participant.context=context;participant.transaction=tx;participant.root=Path('/synthetic')
        owner={'pid':123,'start_ticks':456,'process_group':123,'boot_id':'11111111-1111-1111-1111-111111111111'}
        ready={'observed_at':NOW.isoformat()};review_raw=b'fixed-private-review'
        snapshot={'readiness-prepare.json':t.digest(ready),'review.json':t.a.sha(review_raw)}
        participant._finite_owner=Mock();participant._fences=Mock(return_value={})
        participant._review_snapshot=Mock(return_value=snapshot);participant._validate_readiness=Mock()
        # Simulate a slow actual collector: proof was fresh on entry, but source
        # collection finishes at the explicit later actual UTC supplied here.
        participant._collect=Mock(return_value=(intent,{'prepare_deadline':candidate['prepare_deadline']},
            t.a.sha(review_raw),NOW+dt.timedelta(minutes=10),snapshot))
        history={'schema':'careertrellis.onedrive-host-history/v1','role':'web','transaction':tx,'stage':'prepare',
            'observed_at':when.isoformat(),'baseline_bindings':context.bindings,
            'roots':[{'category':category,'root':root,'entry_count':0,'inventory_sha256':'1'*64} for category,root in guard.HISTORY_ROOTS['web']],
            'unresolved':['orphan'] if unknown_history else [],'complete':not unknown_history,'growth_authorized':False,
            'deletion_authorized':False,'authentication':'requires_authenticated_transport','owner':owner,
            'reservation':{'path':'/run/lock/careertrellis-maintenance.lock','identity':{'device':1,'inode':2,'owner_pid':123},'held':True}}
        class Clock(dt.datetime):
            @classmethod
            def now(cls,tz=None):return when
        def reader(path):
            return (t.bundle.canonical(ready),ready) if path.name=='readiness-prepare.json' else (review_raw,{})
        return participant,context,history,owner,Clock,reader

    def test_slow_native_collection_cannot_use_initially_fresh_proof_to_write_intent(self):
        from deploy.onedrive_retention import guard,native_restore
        participant,context,history,owner,Clock,reader=self.boundary_fixture(NOW+dt.timedelta(seconds=121))
        with patch.object(t.dt,'datetime',Clock),patch.object(t,'native_context',return_value=context),\
             patch.object(t,'_unchanged'),patch.object(t.os.path,'lexists',return_value=False),\
             patch.object(t,'_private',side_effect=reader),patch.object(guard,'native_history_snapshot',return_value=history),\
             patch.object(native_restore,'process_identity',return_value=owner),patch.object(t,'_exclusive_marker') as write:
            with self.assertRaises(ValueError):participant.prepare()
            write.assert_not_called()

    def test_late_incomplete_census_cannot_write_any_target_marker(self):
        from deploy.onedrive_retention import guard,native_restore
        participant,context,history,owner,Clock,reader=self.boundary_fixture(NOW,unknown_history=True)
        with patch.object(t.dt,'datetime',Clock),patch.object(t,'native_context',return_value=context),\
             patch.object(t,'_unchanged'),patch.object(t.os.path,'lexists',return_value=False),\
             patch.object(t,'_private',side_effect=reader),patch.object(guard,'native_history_snapshot',return_value=history),\
             patch.object(native_restore,'process_identity',return_value=owner),patch.object(t,'_exclusive_marker') as write:
            with self.assertRaises(ValueError):participant.prepare()
            write.assert_not_called()

    def test_write_boundary_uses_readiness_expiry_not_only_review_deadline(self):
        from deploy.onedrive_retention import guard,native_restore
        participant,context,history,owner,Clock,reader=self.boundary_fixture(NOW+dt.timedelta(seconds=119))
        intent,local,review_sha,deadline,snapshot=participant._collect('prepare')
        with patch.object(t.dt,'datetime',Clock),patch.object(t,'native_context',return_value=context),\
             patch.object(t,'_unchanged'),patch.object(t,'_private',side_effect=reader),\
             patch.object(guard,'native_history_snapshot',return_value=history),patch.object(native_restore,'process_identity',return_value=owner):
            effective=participant._write_boundary('prepare',intent,review_sha,deadline,snapshot,check_history=True)
        self.assertEqual(effective,NOW+dt.timedelta(seconds=120))
        participant._validate_readiness.assert_called_once()

    def test_phase_readiness_binds_immutable_prepare_and_fresh_commit_origins(self):
        context,candidate=self.candidate();intent=candidate['intent'];tx=intent['transaction']
        participant=object.__new__(t.NativeLocal);participant.context=context;participant.transaction=tx;participant.root=Path('/synthetic')
        owner={'pid':123,'start_ticks':456,'boot_id':'11111111-1111-1111-1111-111111111111'}
        dispatch_owner=dict(owner,process_group=123)
        request={'transaction':tx,'role':'web','phase':'prepare','intent_sha256':t.digest(intent),
            'prepare_deadline':candidate['prepare_deadline'],'baseline_bindings':context.bindings}
        records={'intent.json':intent,'prepare-request-web.json':request,
            'prepare-web.dispatch.json':{'transaction':tx,'owner':dispatch_owner,'request':request,'request_sha256':t.digest(request)}}
        def reader(path):
            value=records[path.name];return t.bundle.canonical(value),value
        from deploy.onedrive_retention import native_restore
        with patch.object(t,'_private',side_effect=reader),patch.object(t,'_birth',return_value=owner),\
             patch.object(native_restore,'process_identity',return_value=dispatch_owner):
            def ready(phase,when):return {'schema':t.READINESS,'transaction':tx,'role':'web',
                'baseline_sha256':t.digest(context.document),'intent_sha256':t.digest(intent),'observed_at':when.isoformat(),
                'dispatcher_owner':owner,'authority':'postgres-r2-authoritative','application_release':context.document['application_release'],
                'idle':True,'policy_sha256':context.bindings['standalone_policy_sha256'],
                'source_manifest_sha256':context.bindings['standalone_manifest_sha256'],**participant._readiness_binding(phase)}
            prepare=ready('prepare',NOW);records['readiness-prepare.json']=prepare
            participant._validate_readiness(prepare,'prepare',context,intent,NOW)
            later=NOW+dt.timedelta(minutes=5)
            with self.assertRaises(ValueError):participant._validate_readiness(prepare,'prepare',context,intent,later)
            commit_request={'transaction':tx,'role':'web','phase':'commit','intent_sha256':t.digest(intent),'selector_sha256':'9'*64}
            records.update({'commit-web.dispatch.json':{'transaction':tx,'owner':dispatch_owner,'request':commit_request,'request_sha256':t.digest(commit_request)},
                'prepared-web.json':{'transaction':tx,'role':'web','phase':'prepare','intent_sha256':t.digest(intent),
                    'request_sha256':t.digest(request),'result':'prepared'},
                'commit-material.ack.json':{'schema':'careertrellis.retention-activation-material-accepted/v1','transaction':tx}})
            refreshed=ready('commit',later);records['readiness-commit.json']=refreshed
            participant._validate_readiness(refreshed,'commit',context,intent,later)
            records['commit-web.dispatch.json']['owner']=owner # Missing process_group is not silently projected.
            with self.assertRaises(ValueError):participant._readiness_binding('commit')
            records['commit-web.dispatch.json']['owner']=dict(dispatch_owner,unexpected=True)
            with self.assertRaises(ValueError):participant._readiness_binding('commit')
            records['commit-web.dispatch.json']['owner']=dispatch_owner
            with self.assertRaises(ValueError):participant._validate_readiness(prepare,'commit',context,intent,later)
            wrong=copy.deepcopy(refreshed);wrong['phase']='prepare'
            with self.assertRaises(ValueError):participant._validate_readiness(wrong,'commit',context,intent,later)
            records['commit-web.dispatch.json']['request']['phase']='prepare'
            with self.assertRaises(ValueError):participant._validate_readiness(refreshed,'commit',context,intent,later)
            self.assertEqual(records['readiness-prepare.json'],prepare) # Never overwrite old proof.

    def candidate(self):
        context,proof,installation,review=fixture()
        return context,t.proposal(context,proof,installation,review,NOW)

    def test_two_phase_order_and_no_authentication_claim(self):
        context,candidate=self.candidate();state=t.initial(candidate)
        self.assertFalse(candidate['authenticated']);self.assertFalse(candidate['activation_performed'])
        for expected in t.SEQUENCE:
            action=t.next_request(candidate,state,context,NOW)
            request=action['request'];self.assertEqual(request['role']+'_'+request['phase'],expected)
            state=t.acknowledge(action['persist_before_dispatch'],request,ack(request))
        self.assertEqual(state['completed'],list(t.SEQUENCE))
        with self.assertRaises(ValueError):t.next_request(candidate,state,context,NOW)

    def test_pending_or_lost_ack_never_replays_or_rolls_back(self):
        context,candidate=self.candidate();action=t.next_request(candidate,t.initial(candidate),context,NOW)
        pending=action['persist_before_dispatch']
        for state in (pending,t.lost_acknowledgement(pending)):
            with self.assertRaises(ValueError):t.next_request(candidate,state,context,NOW)
        with self.assertRaises(ValueError):t.acknowledge(t.lost_acknowledgement(pending),action['request'],ack(action['request']))

    def test_current_policy_baseline_and_receipt_drift_refuse(self):
        context,candidate=self.candidate()
        for change in ('policy','receipt','source'):
            altered=copy.deepcopy(context)
            if change=='policy':altered.bindings['standalone_policy_sha256']='9'*64
            elif change=='receipt':altered.document['acceptance_receipts']['ops']='9'*64
            else:altered.revision='9'*40
            with self.subTest(change=change),self.assertRaises(ValueError):
                t.next_request(candidate,t.initial(candidate),altered,NOW)

    def test_proof_review_and_installation_drift_refuse_before_prepare(self):
        for change in ('proof','review','installation','expired','too_long'):
            context,proof,installation,review=fixture()
            if change=='proof':proof['replacement_bundles'].pop()
            elif change=='review':review['baseline_sha256']='9'*64
            elif change=='installation':installation['db']='9'*64
            elif change=='expired':review['valid_until']=NOW.isoformat()
            else:review['valid_until']=(NOW+dt.timedelta(seconds=901)).isoformat()
            with self.subTest(change=change),self.assertRaises(ValueError):
                t.proposal(context,proof,installation,review,NOW)

    def test_receipt_mismatch_and_out_of_order_partial_state_refuse(self):
        context,candidate=self.candidate();action=t.next_request(candidate,t.initial(candidate),context,NOW)
        receipt=ack(action['request']);receipt['intent_sha256']='9'*64
        with self.assertRaises(ValueError):t.acknowledge(action['persist_before_dispatch'],action['request'],receipt)
        state=t.initial(candidate);state['completed']=['web_prepare']
        with self.assertRaises(ValueError):t.next_request(candidate,state,context,NOW)

    def test_expiry_after_prepare_refuses_commit(self):
        context,candidate=self.candidate();state=t.initial(candidate)
        for unused in range(2):
            action=t.next_request(candidate,state,context,NOW)
            state=t.acknowledge(action['persist_before_dispatch'],action['request'],ack(action['request']))
        with self.assertRaises(ValueError):t.next_request(candidate,state,context,NOW+dt.timedelta(minutes=10))

    def test_review_deadline_does_not_expire_committed_selector(self):
        context,candidate=self.candidate();intent=candidate['intent']
        self.assertEqual(intent['valid_until'],context.document['valid_until'])
        selector={'schema':t.a.SCHEMA,'transaction':intent['transaction'],
            'activation_intent_sha256':t.digest(intent),'enabled':True,
            'activated_at':(NOW+dt.timedelta(minutes=1)).isoformat(),'valid_until':intent['valid_until']}
        t.a.validate(intent,selector,context.bindings,context.document,'web',NOW+dt.timedelta(hours=1))
        with self.assertRaises(ValueError):
            t.next_request(candidate,t.initial(candidate),context,NOW+dt.timedelta(minutes=10))

    def test_active_validity_requires_explicit_approval_within_policy_and_baseline(self):
        for mode in ('missing','beyond_baseline','before_deadline'):
            context,proof,installation,review=fixture()
            if mode=='missing':review.pop('active_valid_until')
            elif mode=='beyond_baseline':review['active_valid_until']=(NOW+dt.timedelta(days=2)).isoformat()
            else:review['active_valid_until']=(NOW+dt.timedelta(minutes=5)).isoformat()
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                t.proposal(context,proof,installation,review,NOW)

    def test_producer_selector_identical_across_roles_and_different_commit_clocks(self):
        context,candidate=self.candidate();intent=candidate['intent']
        selector={'schema':t.a.SCHEMA,'transaction':intent['transaction'],'enabled':True,
            'activation_intent_sha256':t.digest(intent),'activated_at':(NOW+dt.timedelta(minutes=1)).isoformat(),
            'valid_until':intent['valid_until']}
        authority={'schema':'careertrellis.retention-activation-commit-authority/v1',
            'transaction':intent['transaction'],'intent_sha256':t.digest(intent),
            'prepare_deadline':candidate['prepare_deadline'],'dispatcher_owner':{},'prepared':{},
            'selector':selector,'selector_sha256':t.digest(selector)}
        db=copy.deepcopy(context);db.role='db';db.document['acceptance_receipts']['ops']='f'*64
        first=t.planned_selector(authority,intent,db,candidate['prepare_deadline'],NOW+dt.timedelta(minutes=2))
        second=t.planned_selector(authority,intent,context,candidate['prepare_deadline'],NOW+dt.timedelta(minutes=7))
        self.assertEqual(t.digest(first),t.digest(second));self.assertEqual(first,selector)
        t.matching_peer_selector(second,first)
        for mutation in ('digest','intent','validity','future','deadline'):
            broken=copy.deepcopy(authority)
            if mutation=='digest':broken['selector_sha256']='9'*64
            elif mutation=='intent':broken['selector']['activation_intent_sha256']='9'*64
            elif mutation=='validity':broken['selector']['valid_until']=(NOW+dt.timedelta(days=2)).isoformat()
            elif mutation=='future':broken['selector']['activated_at']=(NOW+dt.timedelta(minutes=9)).isoformat()
            else:broken['selector']['activated_at']=candidate['prepare_deadline']
            if mutation!='digest':broken['selector_sha256']=t.digest(broken['selector'])
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                t.planned_selector(broken,intent,context,candidate['prepare_deadline'],NOW+dt.timedelta(minutes=7))
        different=copy.deepcopy(first);different['activated_at']=(NOW+dt.timedelta(minutes=2)).isoformat()
        with self.assertRaises(ValueError):t.matching_peer_selector(second,different)

    def test_checkout_cannot_authenticate_native_and_no_timer_dispatch(self):
        with self.assertRaises(ValueError):t.native_context()
        requirements=t.integration_requirements()
        self.assertTrue(requirements['native_dispatch_available'])
        self.assertFalse(requirements['activation_authority_established'])
        self.assertEqual(requirements['sequence'],list(t.SEQUENCE))

    def test_public_constructor_cannot_authenticate_fixtures(self):
        context,*unused=fixture()
        with self.assertRaises(ValueError):t.NativeLocal('d'*32,context,object())

    def test_expired_native_write_deadline_never_opens_marker(self):
        with patch.object(t.os,'open') as opened:
            with self.assertRaises(ValueError):
                t._exclusive_marker(Path('/synthetic/intent.json'),{'synthetic':True},
                    deadline=dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=1))
            opened.assert_not_called()

    def test_failed_native_collection_creates_no_marker(self):
        participant=object.__new__(t.NativeLocal)
        participant._collect=Mock(side_effect=t.TransitionRefused('missing_native_proof'))
        with patch.object(t,'_exclusive_marker') as writer:
            for method in (participant.prepare,participant.commit):
                with self.assertRaises(ValueError):method()
            writer.assert_not_called()

    def test_orphan_finalized_refuses_before_collection_or_any_marker(self):
        participant=object.__new__(t.NativeLocal)
        participant._collect=Mock()
        with patch.object(t.os.path,'lexists',side_effect=lambda path:path==t.a.FINALIZED), \
                patch.object(t,'_exclusive_marker') as writer:
            for method in (participant.prepare,participant.commit):
                with self.assertRaises(ValueError):method()
        participant._collect.assert_not_called();writer.assert_not_called()

    def test_review_drift_before_prepare_creates_no_marker(self):
        context,candidate=self.candidate();participant=object.__new__(t.NativeLocal)
        participant.context=context;participant.transaction='d'*32;participant.root=Path('/synthetic')
        raw=b'protected-review';snapshot={'review.json':t.a.sha(raw)}
        participant._collect=Mock(return_value=(candidate['intent'],{'prepare_deadline':candidate['prepare_deadline']},
            t.a.sha(raw),NOW+dt.timedelta(minutes=10),snapshot))
        participant._review_snapshot=Mock(return_value={'review.json':'9'*64})
        with patch.object(t.os.path,'lexists',return_value=False),patch.object(t,'_private',return_value=(raw,{})),\
                patch.object(t,'_exclusive_marker') as writer:
            with self.assertRaises(ValueError):participant.prepare()
            writer.assert_not_called()

    def test_marker_writer_retains_created_marker_on_unknown_readback(self):
        # POSIX root fixture exercises actual O_EXCL/no-follow/fsync semantics;
        # never runs against canonical markers or a provider.
        if os.name!='posix' or os.geteuid()!=0 or not Path('/run').is_dir():
            self.skipTest('root POSIX protected scratch fixture')
        with tempfile.TemporaryDirectory(prefix='retention-activation-test-',dir='/run') as directory:
            marker=Path(directory)/'intent.json';value={'synthetic':True}
            with patch.object(t,'_private',side_effect=OSError('synthetic lost readback')):
                with self.assertRaises(OSError):t._exclusive_marker(marker,value)
            self.assertEqual(marker.read_bytes(),t.bundle.canonical(value))
            with self.assertRaises(FileExistsError):t._exclusive_marker(marker,value)
            self.assertEqual(marker.read_bytes(),t.bundle.canonical(value))

    def test_marker_writer_private_readback_and_no_symlink_overwrite(self):
        if os.name!='posix' or os.geteuid()!=0 or not Path('/run').is_dir():
            self.skipTest('root POSIX protected scratch fixture')
        with tempfile.TemporaryDirectory(prefix='retention-activation-test-',dir='/run') as directory:
            target=Path(directory)/'target.json';value={'synthetic':True}
            self.assertEqual(t._exclusive_marker(target,value),t.digest(value))
            self.assertEqual(target.stat().st_mode&0o777,0o600)
            link=Path(directory)/'link.json';link.symlink_to(target)
            with self.assertRaises(FileExistsError):t._exclusive_marker(link,{'changed':True})
            self.assertEqual(target.read_bytes(),t.bundle.canonical(value))


if __name__=='__main__':unittest.main()
