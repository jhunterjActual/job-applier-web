"""Offline structural contracts; no native/provider authority is simulated."""
import copy
import datetime as dt
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from unittest import mock

from deploy.onedrive_retention import pre_growth_native as p, identity_collect, retire, entry, guard, bundle, execution, common_io
from deploy.tests.test_onedrive_pre_growth import entries, Fake, LATE, key


def request():
    return {'schema': p.SCHEMA, 'operation': p.PURPOSE, 'transaction': 'a'*32,
        'retention_policy_sha256': 'b'*64, 'activation_sha256': 'c'*64,
        'web_history_sha256': 'd'*64,
        'allocation': {'source_bytes': 100, 'candidate_bytes': 100, 'namespace_new_bytes': 100}}


def checkpoints():
    records = {}; replacements = []
    for n, accepted in enumerate(entries()[:3]):
        native = {'schema': 'careertrellis.onedrive-native-local-restore/v1', 'result': 'verified',
            'native_restore_accepted': True, 'postmaster_terminal': True, 'checksums_verified': True,
            'archive_sha256': accepted['id'], 'transaction': format(n+1, '032x'), 'backup_label': 'fixture-'+str(n)}
        accepted['native_restore_receipt_sha256'] = p.sha(native)
        records[accepted['id']] = {'entry': accepted, 'evidence': {'native': native,
            'download': {'transaction': native['transaction']}, 'terminal_proof_sha256': key(200+n)}}
        replacements.append({'archive_sha256': accepted['id'], 'native_receipt_sha256': p.sha(native),
            'terminal_proof_sha256': key(200+n), 'backup_label': native['backup_label']})
    return records, replacements[:2]


class PreGrowthNativeTests(unittest.TestCase):
    def test_old_partial_parent_never_uses_another_current_transaction_exception(self):
        names={'intent.json','dispatch.json','ingress-provenance.json'}
        with mock.patch.object(p,'_names',return_value=names),mock.patch.object(p,'_current') as current:
            with self.assertRaises(p.PreGrowthNativeRefused):p.history_complete('a'*32,'b'*32,9)
            current.assert_not_called()
        group='0::/system.slice/'+p.coordinates('a'*32)['owner_unit']
        with mock.patch.object(p,'_names',return_value=names),mock.patch.object(Path,'read_text',return_value=group),\
                mock.patch.object(p,'_current',side_effect=p.PreGrowthNativeRefused('wrong_actual_owner')):
            with self.assertRaises(p.PreGrowthNativeRefused):p.history_complete('a'*32,'a'*32,9)

    def test_one_past_child_closes_while_second_child_is_inflight(self):
        tx='a'*32;source='b'*32;other='c'*32;root=p.JOURNAL_ROOT/tx
        owner={'pid':222,'start_ticks':3,'process_group':222,'boot_id':'11111111-1111-1111-1111-111111111111'}
        child_owner=dict(owner,pid=333,process_group=333)
        unit=p.identity_unit(tx,source,1);prefix='child-'+source+'-01'
        intent={'transaction':tx,'source_transaction':source,'verification':1,'unit':unit,'parent_owner':owner}
        birth={'unit':unit,'owner':child_owner,'invocation_id':'d'*32,'dispatch_sha256':p.sha(intent)}
        identity={'synthetic':True}
        ack={'schema':'careertrellis.pre-growth-child-exit/v1','transaction':tx,'dispatch_sha256':p.sha(intent),
            'birth_sha256':p.sha(birth),'exit_code':0,'parent_owner':owner}
        terminal={'schema':'careertrellis.pre-growth-identity-terminal/v1','transaction':tx,'source_transaction':source,
            'verification':1,'dispatch_sha256':p.sha(intent),'birth_sha256':p.sha(birth),'parent_owner':owner,'identity_sha256':p.sha(identity)}
        docs={prefix+'-intent.json':intent,prefix+'-start.json':birth,prefix+'-launch-ack.json':ack,prefix+'-terminal.json':terminal}
        names=set(docs)|{'child-'+other+'-01-intent.json','child-'+other+'-01-start.json'}
        def read(path,*args):
            value=identity if path.name=='bundle-object-identity-01.json' else docs[path.name]
            return bundle.canonical(value),value
        state={'LoadState':'loaded','MainPID':'0','ControlPID':'0','Job':'','Result':'success',
            'ExecMainStatus':'0','ExecMainPID':'333','InvocationID':'d'*32}
        with mock.patch.object(p,'_names',return_value=names),mock.patch.object(execution,'document',side_effect=read),\
                mock.patch.object(p,'_properties',return_value=state),mock.patch.object(execution,'group_empty',return_value=True),\
                mock.patch.object(guard.native_restore,'process_identity',side_effect=ProcessLookupError):
            self.assertEqual(len(p._children_closed(root,owner,selected=(source,1))),1)
            with self.assertRaises(p.PreGrowthNativeRefused):p._children_closed(root,owner)

    def test_retirement_history_requires_strict_permanent_ack_boolean_and_all_phases(self):
        snapshot=Fake().fenced_snapshot('a'*32);catalog=snapshot['catalog'];plan=retire.prepare(catalog,LATE.isoformat())
        operation={'schema':'careertrellis.bundle-retirement-native-intent/v1','transaction':'a'*32,'namespace':retire.NAMESPACE,
            'purpose':'pre_growth_retirement','simulation':False,'expiry_mode':'conditional_permanent',
            'catalog':catalog,'plan':plan,'catalog_sha256':plan['catalog_sha256']}
        docs={'operation-intent.json':operation};terminals=[]
        for index,(candidate,obj) in enumerate((row,obj) for row in plan['candidates'] for obj in row['objects']):
            prefix='object-%05d-'%index
            intent={'schema':'careertrellis.bundle-object-retirement-intent/v1','transaction':'a'*32,'sequence':index,
                'namespace':retire.NAMESPACE,'bundle_id':candidate['bundle_id'],'object':obj,'operation_intent_sha256':p.sha(operation),
                'simulation':False,'expiry_mode':'conditional_permanent'}
            ack={'result':'deleted','object_id':obj['object_id'],'request_id':p.sha(intent),'recoverable':False,
                'permanent':True,'expiry_mode':'conditional_permanent'}
            terminal={'schema':'careertrellis.bundle-object-retirement-terminal/v1','transaction':'a'*32,'sequence':index,
                'namespace':retire.NAMESPACE,'bundle_id':candidate['bundle_id'],'object':obj,'intent_sha256':p.sha(intent),
                'dispatch_ack_sha256':p.sha(ack),'absent':True,'simulation':False,'expiry_mode':'conditional_permanent'}
            docs.update({prefix+'intent.json':intent,prefix+'dispatch.json':{'intent_sha256':p.sha(intent),'simulation':False},
                prefix+'ack.json':ack,prefix+'terminal.json':terminal});terminals.append(p.sha(terminal))
        docs['operation-complete.json']={'schema':'careertrellis.bundle-retirement-native/v1','result':'complete','transaction':'a'*32,
            'native_retirement_accepted':True,'expiry_mode':'conditional_permanent','deletions_performed':len(terminals),
            'operation_intent_sha256':p.sha(operation),'terminal_receipt_sha256':terminals}
        store=SimpleNamespace(io=SimpleNamespace(locked=lambda:nullcontext()),records=lambda:{},
            retired=lambda records:{row['bundle_id'] for row in plan['candidates']})
        def read(path,*args):
            value=docs['operation-complete.json'] if path.name=='retirement-result.json' else docs[path.name]
            return bundle.canonical(value),value
        names=set(docs)
        with mock.patch.object(Path,'iterdir',side_effect=lambda:iter(SimpleNamespace(name=name) for name in names)):
            with mock.patch.object(retire.Native,'ROOT',Path('/synthetic-retirements')),mock.patch.object(execution,'document',side_effect=read),\
                    mock.patch.object(p,'history_complete',return_value=True),mock.patch.object(p.catalog,'NativeReadStore',return_value=store):
                self.assertTrue(p.retirement_history_complete('a'*32,repository_lock_fd=9))
                for field,value in (('permanent',1),('recoverable',0)):
                    saved=docs['object-00000-ack.json'][field];docs['object-00000-ack.json'][field]=value
                    with self.subTest(field=field),self.assertRaises(ValueError):p.retirement_history_complete('a'*32,repository_lock_fd=9)
                    docs['object-00000-ack.json'][field]=saved
                names.remove('object-00000-dispatch.json')
                with self.assertRaises(ValueError):p.retirement_history_complete('a'*32,repository_lock_fd=9)

    def test_existing_checkpoints_are_claims_never_authority(self):
        records, selected = checkpoints()
        result = p.existing_claims(records, set(), selected, LATE)
        self.assertFalse(result['authenticated']); self.assertFalse(result['native_actions'])
        self.assertEqual(len(result['existing_sources']), 3)
        with self.assertRaises(p.PreGrowthNativeRefused): p.ParentCapability(request(), result['existing_sources'])

    def test_two_actual_active_distinct_checkpoints_required(self):
        records, selected = checkpoints()
        for mutate in ('one_remaining', 'checksum', 'terminal_duplicate', 'selector', 'source_duplicate'):
            with self.subTest(mutate=mutate):
                rows = copy.deepcopy(records); selection = copy.deepcopy(selected); retired = set()
                ids = list(rows)
                if mutate == 'one_remaining': retired = set(ids[:2])
                if mutate == 'checksum': rows[ids[0]]['evidence']['native']['checksums_verified'] = False
                if mutate == 'terminal_duplicate': rows[ids[1]]['evidence']['terminal_proof_sha256'] = rows[ids[0]]['evidence']['terminal_proof_sha256']
                if mutate == 'selector': selection[0]['terminal_proof_sha256'] = 'f'*64
                if mutate == 'source_duplicate':
                    rows[ids[1]]['evidence']['download']['transaction'] = rows[ids[0]]['evidence']['download']['transaction']
                with self.assertRaises(p.PreGrowthNativeRefused): p.existing_claims(rows, retired, selection, LATE)

    def test_proposal_preserves_holds_latest_two_and_grace(self):
        snapshot = Fake().fenced_snapshot('a'*32)
        plan = p.proposal(request(), snapshot['catalog'], LATE)
        self.assertEqual([r['bundle_id'] for r in plan['retirement']['candidates']], [key(1)])
        self.assertTrue({key(0), key(4), key(5)} <= set(plan['retirement']['retained_bundle_ids']))
        self.assertFalse(plan['dispatch_authorized']); self.assertFalse(plan['native_growth_authorized'])
        self.assertIn('pre-growth-retire-', plan['coordinates']['owner_unit'])
        self.assertNotIn('prepare', plan['coordinates']['intent_root'])

    def test_closed_request_rejects_boolean_size_and_unknown_operation(self):
        for field, value in [('transaction', '../escape'), ('operation', 'prepare')]:
            row=request(); row[field]=value
            with self.assertRaises(ValueError): p.request(row)
        row=request(); row['allocation']['source_bytes']=True
        with self.assertRaises(ValueError): p.request(row)

    def test_source_capability_still_refuses_checkout_before_provider_and_normal_pipeline(self):
        self.assertTrue(p.native_routes_available())
        with mock.patch.object(p, '_authenticated_ingress') as ingress, mock.patch.object(retire, 'Native') as normal:
            with self.assertRaises(p.PreGrowthNativeRefused): p.run('a'*32)
            ingress.assert_not_called(); normal.assert_not_called()
        self.assertIn('pre_growth_native.py', entry.FILES)

    def test_new_wrappers_never_fall_back_to_normal_authority(self):
        with mock.patch.object(identity_collect, 'Native') as identity, mock.patch.object(retire, 'Native') as normal:
            with self.assertRaises(identity_collect.IdentityRefused): identity_collect.collect_existing_for_parent('a'*32, 1, {})
            with self.assertRaises(retire.RetirementRefused): retire.run_existing_for_parent('a'*32, {})
            identity.assert_not_called(); normal.assert_not_called()

    def test_native_ingress_cannot_bypass_closed_typed_unit_reader(self):
        with mock.patch.object(execution, 'document', return_value=(bundle.canonical({}), {})) as reader:
            with self.assertRaises(p.PreGrowthNativeRefused):
                p._authenticated_ingress('a'*32, object(), object())
            self.assertEqual(reader.call_count, 0)
            for call in reader.call_args_list:
                self.assertEqual(call.args[0].parent, p.JOURNAL_ROOT/('a'*32))

    def test_closed_ingress_claims_bind_peer_owner_watcher_and_dispatch(self):
        tx='a'*32; coords=p.coordinates(tx)
        owner={'pid':101,'start_ticks':10,'process_group':101,'boot_id':'0'*8+'-'+'0'*4+'-'+'0'*4+'-'+'0'*4+'-'+'0'*12}
        watcher=dict(owner,pid=102,process_group=102,start_ticks=11)
        bindings={key:'b'*64 for key in ('standalone_manifest_sha256','paired_manifest_sha256','paired_policy_sha256','standalone_policy_sha256')}
        witness={'schema':'careertrellis.onedrive-host-history/v1','role':'web','transaction':tx,
            'stage':p.PURPOSE,'observed_at':LATE.isoformat(),'baseline_bindings':bindings,
            'roots':[{'category':category,'root':root,'entry_count':0,'inventory_sha256':'e'*64}
                     for category,root in guard.HISTORY_ROOTS['web']],
            'unresolved':[],'complete':True,'growth_authorized':False,'deletion_authorized':False,
            'authentication':'requires_authenticated_transport','owner':owner,
            'reservation':{'path':'/run/lock/careertrellis-maintenance.lock','held':True,
                'identity':{'device':1,'inode':2,'owner_pid':101}}}
        value=request();value['web_history_sha256']=p.sha(witness)
        intent={'schema':'careertrellis.pre-growth-ingress/v1','request':value,'web_history':witness}
        docs={'intent':intent}; states=[]
        for role, identity in (('owner',owner),('watcher',watcher)):
            docs[role+'-start']={'schema':'careertrellis.pre-growth-'+role+'-birth/v1',
                'transaction':tx,'purpose':p.PURPOSE,'unit':coords[role+'_unit'],'owner':identity,
                'invocation_id':('1' if role=='owner' else '2')*32,'intent_sha256':p.sha(intent),'policy_sha256':'b'*64}
            states.append({'LoadState':'loaded','ActiveState':'active','SubState':'running','MainPID':str(identity['pid']),
                'ControlPID':'0','Job':'','InvocationID':docs[role+'-start']['invocation_id'],
                'ControlGroup':'/system.slice/'+coords[role+'_unit'],'Type':'exec','Restart':'no',
                'RemainAfterExit':'yes','PrivateNetwork':'no' if role=='owner' else 'yes',
                'KillMode':'control-group','SendSIGKILL':'yes','RuntimeMaxUSec':'9h' if role=='owner' else '545min',
                'TimeoutStartUSec':'30s','TimeoutStopUSec':'90s',
                **{field:'' for field in ('ExecCondition','ExecStartPre','ExecStartPost','ExecStop','ExecStopPost')}})
        docs['dispatch']={'schema':'careertrellis.pre-growth-dispatch/v1','transaction':tx,'purpose':p.PURPOSE,
            'intent_sha256':p.sha(intent),'owner_unit':coords['owner_unit'],'watcher_unit':coords['watcher_unit']}
        result=p.ingress_claims(docs,*states,owner,bindings,'c'*64,LATE.isoformat())
        self.assertFalse(result['authenticated']);self.assertFalse(result['dispatch_authorized'])
        for key,value in (('InvocationID','f'*32),('MainPID','999'),('ExecStop','nonempty'),('RuntimeMaxUSec','infinity')):
            changed=copy.deepcopy(states);changed[1][key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):
                p.ingress_claims(docs,*changed,owner,bindings,'c'*64,LATE.isoformat())
        changed=copy.deepcopy(docs);changed['dispatch']['purpose']='prepare'
        with self.assertRaises(ValueError):p.ingress_claims(changed,*states,owner,bindings,'c'*64,LATE.isoformat())


if __name__ == '__main__': unittest.main()
