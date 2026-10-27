"""Synthetic finalization-chain checks; never native activation or host access."""
import copy
import datetime as dt
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock,patch

from deploy.onedrive_retention import activation_finalize as f
from deploy.tests.test_onedrive_activation_controller import chain,fixture,NOW


def material():
    terminal,arguments=chain();web,*unused=fixture();db=copy.deepcopy(web)
    db.role='db';db.document['acceptance_receipts']['ops']='f'*64
    terminal['baseline_sha256']['db']=f.t.digest(db.document)
    intent=arguments['intent'];selector=arguments['selector'];documents={'controller-terminal.json':terminal}
    for role in ('web','db'):
        for stem,key in (('committed-','committed_receipts'),('commit-ack-','commit_acknowledgements'),
                ('reconciliation-','reconciliations')):documents[stem+role+'.json']=arguments[key][role]
    finalized={'schema':f.c.FINALIZED,'transaction':intent['transaction'],'intent_sha256':f.t.digest(intent),
        'selector_sha256':f.t.digest(selector),'baseline_bindings':web.bindings,
        'baseline_sha256':terminal['baseline_sha256'],'installation_receipts':terminal['installation_receipts'],
        'committed_receipts':terminal['committed_receipts'],'controller_terminal_sha256':f.t.digest(terminal),
        'finalized_at':(NOW+dt.timedelta(minutes=6)).isoformat(),'valid_until':intent['valid_until']}
    value={'schema':f.SCHEMA,'transaction':intent['transaction'],'retention_policy_sha256':web.bindings['standalone_policy_sha256'],
        'intent':intent,'selector':selector,'finalized':finalized,'documents':documents}
    return value,web,db


def acknowledgement(value):
    return {'schema':f.ACK,'transaction':value['transaction'],'intent_sha256':f.t.digest(value['intent']),
        'selector_sha256':f.t.digest(value['selector']),'finalized_sha256':f.t.digest(value['finalized']),
        'controller_terminal_sha256':f.t.digest(value['documents']['controller-terminal.json']),
        'db_baseline_sha256':value['finalized']['baseline_sha256']['db'],'actor_terminal_sha256':'1'*64,
        'source_manifest_sha256':value['intent']['baseline_bindings']['standalone_manifest_sha256'],
        'accepted_at':(NOW+dt.timedelta(minutes=7)).isoformat()}


def actor_status(value,state='terminal'):
    original={'transaction':value['transaction'],'intent_sha256':f.t.digest(value['intent']),
        'baseline_bindings':value['intent']['baseline_bindings'],'baseline_sha256':value['finalized']['baseline_sha256']['db'],
        'source':{'actor_sha256':'2'*64,'paired_entry_sha256':'3'*64},
        'actor_owner':{'pid':222,'start_ticks':333,'boot_id':'01234567-89ab-cdef-0123-456789abcdef'},'actor_invocation':'4'*32}
    status={'schema':'careertrellis.retention-activation-actor-terminal-status/v1',**original,
        'observed_at':(NOW+dt.timedelta(minutes=7)).isoformat(),'state':state,'terminal':None}
    if state=='terminal':
        unit=f.actor._unit(value['transaction'])
        status['terminal']={'schema':'careertrellis.retention-activation-actor-terminal/v1',**original,
            'unit':unit,'claim_sha256':'5'*64,'response_sha256':'6'*64,
            'commit_receipt_sha256':f.t.digest(value['documents']['committed-db.json']),
            'observed_at':status['observed_at'],'observer_owner':{'pid':444,'start_ticks':555,'boot_id':original['actor_owner']['boot_id']},
            'terminal':{'unit_state':{'ActiveState':'active','SubState':'exited','MainPID':'0','ControlPID':'0','Job':'',
                'InvocationID':'4'*32,'ExecMainPID':'222','Result':'success','ExecMainCode':'1','ExecMainStatus':'0','RemainAfterExit':'yes'},
                'cgroup':{'path':'/system.slice/'+unit,'present':True,'directories':1,'empty':True}}}
    return status,original


class Tests(unittest.TestCase):
    def test_absence_check_does_not_treat_io_errors_as_absence(self):
        with patch.object(Path,'lstat',side_effect=FileNotFoundError(2,'absent')):f._absent(Path('/fixed'))
        for error in (PermissionError(13,'denied'),OSError(5,'io')):
            with patch.object(Path,'lstat',side_effect=error),self.assertRaises(OSError):f._absent(Path('/fixed'))

    def test_terminal_gate_binds_actual_original_actor_and_never_accepts_running_claim(self):
        value,*unused=material();status,expected=actor_status(value)
        self.assertTrue(f.validate_terminal_status(status,value,expected,NOW+dt.timedelta(minutes=7)))
        running,_=actor_status(value,'running')
        self.assertFalse(f.validate_terminal_status(running,value,expected,NOW+dt.timedelta(minutes=7)))
        for mutate in (lambda x:x.update(actor_invocation='9'*32),
                       lambda x:x.update(baseline_sha256='9'*64),
                       lambda x:x.update(observed_at=NOW.isoformat()),
                       lambda x:x['terminal'].update(commit_receipt_sha256='9'*64),
                       lambda x:x['terminal']['terminal']['unit_state'].update(ExecMainStatus='1'),
                       lambda x:x['terminal']['terminal']['cgroup'].update(empty=False)):
            changed=copy.deepcopy(status);mutate(changed)
            with self.assertRaises(ValueError):f.validate_terminal_status(changed,value,expected,NOW+dt.timedelta(minutes=7))
    def test_one_common_finalization_validates_both_role_specific_baselines(self):
        value,web,db=material()
        for context in (web,db):
            self.assertEqual(f.validate_material(value,context,NOW+dt.timedelta(minutes=7)),
                {'authenticated':False,'material_consistent':True})
        self.assertNotEqual(value['finalized']['baseline_sha256']['web'],value['finalized']['baseline_sha256']['db'])

    def test_missing_proof_unknown_ack_or_selector_baseline_drift_refuses(self):
        for mode in ('missing','unknown','selector','baseline','policy','extra'):
            value,web,db=material()
            if mode=='missing':value['documents'].pop('commit-ack-db.json')
            elif mode=='unknown':value['documents']['controller-terminal.json']['unknown_outcome']=True
            elif mode=='selector':value['selector']['activated_at']=(NOW+dt.timedelta(minutes=2)).isoformat()
            elif mode=='baseline':db.document['acceptance_receipts']['ops']='9'*64
            elif mode=='policy':value['retention_policy_sha256']='9'*64
            else:value['documents']['caller-proof.json']={}
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                f.validate_material(value,db,NOW+dt.timedelta(minutes=7))

    def test_actual_db_ack_must_bind_all_finalization_and_actor_digest_fields(self):
        value,*unused=material();ack=acknowledgement(value)
        f.validate_ack(ack,value,NOW+dt.timedelta(minutes=8))
        for field in ('finalized_sha256','selector_sha256','controller_terminal_sha256',
                'db_baseline_sha256','source_manifest_sha256','actor_terminal_sha256'):
            broken=copy.deepcopy(ack);broken[field]='0'*64
            with self.subTest(field=field),self.assertRaises(ValueError):f.validate_ack(broken,value,NOW+dt.timedelta(minutes=8))

    def test_native_sender_and_receiver_refuse_checkout_before_any_marker(self):
        value,*unused=material()
        with patch.object(f.t,'_exclusive_marker') as writer:
            with self.assertRaises(ValueError):f.receive(f.bundle.canonical(value))
            with self.assertRaises(ValueError):f.send(value['transaction'])
            writer.assert_not_called()

    def test_lost_db_ack_keeps_pending_and_never_writes_web_finalized_or_resends(self):
        # Isolated control-flow fixture: all native I/O/admission is mocked.
        value,context,_db=material();tx=value['transaction'];root=f.t.REVIEW_ROOT/tx
        intent=value['intent'];selector=value['selector'];owner=value['documents']['controller-terminal.json']['owner']
        pending={'schema':f.a.PENDING_SCHEMA,'transaction':tx,'intent_sha256':f.t.digest(intent),
            'baseline_bindings':context.bindings,'source_manifest_sha256':context.bindings['standalone_manifest_sha256'],
            'started_at':NOW.isoformat()}
        documents={root/name:document for name,document in value['documents'].items()}
        status,expected=actor_status(value)
        documents.update({f.a.INTENT:intent,f.a.SELECTOR:selector,f.a.PENDING:pending,
            root/'db-actor-state-prepare.json':expected});written={}
        def reader(path):return f.bundle.canonical(documents[path]),documents[path]
        def absent(path):
            if path in written:raise f.t.TransitionRefused('prior_dispatch')
        def exclusive(path,document,**kwargs):
            if path in written:raise FileExistsError()
            written[path]=copy.deepcopy(document);return f.t.digest(document)
        peer=Mock();peer.call.side_effect=[status,OSError('lost acknowledgement')]
        package=types.ModuleType('paired_maintenance')
        package.entry=types.SimpleNamespace(verify_install=Mock(),load_policy=Mock(return_value=types.SimpleNamespace(
            digest=context.bindings['paired_policy_sha256'])))
        package.client=types.SimpleNamespace(Client=Mock(return_value=peer))
        package.db_control=types.SimpleNamespace(SCHEMA='careertrellis.paired-maintenance-request/v1',validate_request=Mock())
        with patch.object(f,'_context',return_value=context),patch.object(f,'_same'),patch.object(f,'_absent',side_effect=absent),\
                patch.object(f,'now',return_value=NOW+dt.timedelta(minutes=7)),\
                patch.object(f.t.NativeLocal,'_finite_owner'),patch.object(f.t.NativeLocal,'_fences'),\
                patch.object(f.t,'_private',side_effect=reader),patch.object(f.t,'_exclusive_marker',side_effect=exclusive),\
                patch.object(f.entry,'_paired_namespace'),patch.object(f.native_restore,'process_identity',return_value=owner),\
                patch.dict(sys.modules,{'paired_maintenance':package}):
            running,_=actor_status(value,'running');peer.call.side_effect=[running]
            self.assertEqual(f.send(tx),{'result':'awaiting_actor_terminal','dispatch_performed':False})
            self.assertEqual(written,{})
            peer.call.side_effect=[status,OSError('lost acknowledgement')]
            with self.assertRaises(ValueError):f.send(tx)
            with self.assertRaises(ValueError):f.send(tx)
        self.assertEqual(peer.call.call_count,3)
        self.assertEqual(peer.call.call_args_list[0].args[0],f.TERMINAL_STATUS)
        self.assertEqual(peer.call.call_args_list[1].args[0],f.TERMINAL_STATUS)
        self.assertEqual(peer.call.call_args_list[2].args[0],f.OPERATION)
        self.assertEqual({path.name for path in written},{'finalize-db.dispatch.json','finalize-db.unknown.json'})
        self.assertNotIn(f.a.FINALIZED,written);self.assertNotIn(f.a.PENDING,written)


if __name__=='__main__':unittest.main()
