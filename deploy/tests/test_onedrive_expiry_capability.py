"""Synthetic offline capability bindings and fail-closed native boundaries."""
import copy
import datetime as dt
import importlib.util
import json
from contextlib import ExitStack,nullcontext
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from deploy.onedrive_retention import expiry_capability as e,entry

NOW=dt.datetime(2030,1,1,tzinfo=dt.timezone.utc)
BINDINGS={'standalone_manifest_sha256':'a'*64,'paired_manifest_sha256':'b'*64,
    'paired_policy_sha256':'c'*64,'standalone_policy_sha256':'d'*64}
CONTEXT=SimpleNamespace(bindings=BINDINGS,revision='e'*40)


def complete():
    return {'schema':'careertrellis.expiry-capability-complete/v1','transaction':'a'*32,
        'drive_id':'synthetic-private-drive','observed_at':NOW.isoformat(),'bindings':dict(BINDINGS),
        'revision':CONTEXT.revision,'stale_status':412,'current_status':204,'exact_absence':True,
        'children_terminal':True,'bootstrap_source_sha256':e.BOOTSTRAP_SHA}


class Tests(unittest.TestCase):
    def test_bootstrap_checks_process_birth_and_allows_unrelated_pid_reuse(self):
        from deploy.onedrive_retention import preservation
        boot='01234567-89ab-cdef-0123-456789abcdef'
        original={'pid':123,'start_ticks':100,'boot_id':boot}
        child={'pid':124,'start_ticks':200,'boot_id':boot}
        terminal={'source_sha256':e.BOOTSTRAP_SHA,'transaction':e.BOOTSTRAP_TX,
            'child_terminal':True,'classification':'synthetic_conditional_behavior_observed','child_owner':original}
        def document(path):
            if path.name=='terminal-projection.json':value=terminal
            elif path.name=='owned-folder.json':value={'id':'synthetic-parent'}
            elif path.name=='wrong-etag-ack.json':value={'status':412}
            elif path.name=='right-etag-ack.json':value={'status':204}
            elif path.name=='readback-child.json':value=child
            else:value={'classification':'synthetic_conditional_behavior_observed'}
            return e.bundle.canonical(value),value
        for live in (False,True):
            with self.subTest(original_still_live=live),\
                 patch.object(e.hydrate,'protected',return_value=(b'pass\n',None)),\
                 patch.object(e,'digest',side_effect=[e.BOOTSTRAP_SHA,e.TERMINAL_SHA]),\
                 patch.object(e,'document',side_effect=document),\
                 patch.object(e.os,'listdir',return_value=['readback-child.json','complete.json']),\
                 patch.object(Path,'read_text',return_value=boot),\
                 patch.object(Path,'exists',side_effect=AssertionError('PID existence is not birth identity')),\
                 patch.object(e.native_restore,'process_identity',side_effect=lambda pid:{'start_ticks':100 if live and pid==123 else 999}):
                if live:
                    with self.assertRaises(preservation.PreservationRefused):e._bootstrap()
                else:
                    module,owned=e._bootstrap()
                    self.assertEqual(owned,{'id':'synthetic-parent'})
                    self.assertEqual(module.__name__,'_verified_synthetic_expiry')

    def orchestration(self,*,prior_state='complete',drift=False,fail_latest=False):
        # In-memory protected filesystem and Graph. The actual native_check and
        # production fixed Probe engine run; no provider/subprocess/filesystem IO.
        path=Path(__file__).resolve().parents[2]/'artifacts/onedrive-activation-resume-20261026/permanent-expiry-capability/native_probe.py'
        spec=importlib.util.spec_from_file_location('_offline_expiry_protocol',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        blobs={};dirs={e.ROOT};state={'drift':False};tx='f'*32
        old=complete();old['observed_at']=(NOW-dt.timedelta(days=2)).isoformat()
        blobs[e.ROOT/('a'*32)/'complete.json']=e.bundle.canonical(old);dirs.add(e.ROOT/('a'*32))
        latest={'schema':'careertrellis.expiry-capability-latest/v1','transaction':'a'*32,
            'state':prior_state,'complete_sha256':e.digest(e.bundle.canonical(old))}
        blobs[e.ROOT/'latest.json']=e.bundle.canonical(latest)
        token=json.dumps({'access_token':'synthetic','token_type':'Bearer','expiry':(NOW+dt.timedelta(days=1)).isoformat()})
        config=('[r2]\ntype=s3\n[onedrive]\ntype=onedrive\ndrive_id=synthetic-private-drive\ntoken='+token+'\n').encode()
        class Journal:
            def once(self,name,value):
                target=self.root/(name+'.json')
                if target in blobs:raise ValueError('exclusive intent')
                blobs[target]=e.bundle.canonical(value)
        class Graph:
            def __init__(self):self.data=None;self.version=0;self.deletes=[];self.calls=[]
            def row(self):return {'id':'fixture','name':module.NAME,'file':{},'size':len(self.data),
                'parentReference':{'driveId':'synthetic-private-drive','id':'owned-parent'},'eTag':'"v%d"'%self.version}
            def call(self,method,path,body=None,headers=None):
                self.calls.append((method,path))
                if path.endswith('/owned-parent/children'):
                    value={'value':[] if self.data is None else [self.row()],'@odata.context':'private-not-recorded'}
                elif path.endswith('/owned-parent'):
                    value={'id':'owned-parent','name':'careertrellis-permanent-expiry-probe-'+e.BOOTSTRAP_TX,
                        'folder':{},'parentReference':{'driveId':'synthetic-private-drive','id':'root'}}
                elif method=='PUT' and 'conflictBehavior=fail' in path:
                    assert headers['If-None-Match']=='*' and self.data is None
                    self.data=body;self.version=1
                    if drift:state['drift']=True
                    return 201,e.bundle.canonical(self.row())
                elif method=='PUT':
                    assert headers['If-Match']==self.row()['eTag'];self.data=body;self.version=2
                    return 200,e.bundle.canonical(self.row())
                elif path.endswith('/permanentDelete'):
                    self.deletes.append(headers['If-Match'])
                    if headers['If-Match']!=self.row()['eTag']:return 412,b''
                    self.data=None;return 204,b''
                elif path.endswith('/fixture'):
                    if self.data is None:return 404,b''
                    value=self.row()
                elif ':/' in path:return 404,b''
                else:raise AssertionError('unexpected fixture route')
                return 200,e.bundle.canonical(value)
        graph=Graph()
        class Readback:
            def __init__(self,source_tx,raw,drive,journal,unchanged):
                assert source_tx==e.BOOTSTRAP_TX and b'[r2]' not in raw
                self.journal,self.unchanged,self.count=journal,unchanged,0
            def __call__(self):
                self.unchanged();self.count+=1
                phase=('one','two','three')[self.count-1]
                self.journal.once('readback-'+phase+'-terminal',{'pid':10+self.count,'terminal':True,'exit_code':0})
                return graph.data
        module.Journal=Journal;module.Readback=Readback;module.HTTP=lambda token:graph
        def document(path):
            raw=blobs[path];return raw,json.loads(raw)
        def listdir(root):
            return sorted({p.relative_to(root).parts[0] for p in blobs if p!=root and p.is_relative_to(root)})
        def exclusive(path,raw):
            if path in blobs:raise ValueError('exclusive')
            blobs[path]=raw
        def replace(source,target):
            if fail_latest and json.loads(blobs[source])['state']=='complete':raise OSError('atomic commit failed')
            blobs[target]=blobs.pop(source)
        store=SimpleNamespace(io=SimpleNamespace(locked=lambda:nullcontext()),
            records=lambda:{str(n):{'entry':{'accepted_at':(NOW-dt.timedelta(hours=n)).isoformat()},
                'evidence':{'download':{'transaction':str(n)*32}}} for n in (1,2)},retired=lambda records:set())
        with ExitStack() as stack:
            patches=[patch.object(e,'_context',return_value=CONTEXT),patch.object(e,'_root'),
                patch.object(e,'clock',return_value=NOW),patch.object(e,'document',side_effect=document),
                patch.object(e.os,'listdir',side_effect=listdir),patch.object(e.os,'replace',side_effect=replace),
                patch.object(e.hydrate,'protected',side_effect=lambda *a,**k:(config+(b' ' if state['drift'] else b''),'identity')),
                patch.object(e.hydrate,'exclusive',side_effect=exclusive),patch.object(e.catalog,'NativeFiles',return_value=Mock()),
                patch.object(e.catalog,'NativeReadStore',return_value=store),patch.object(e,'_bootstrap',return_value=(module,
                    {'drive_id':'synthetic-private-drive','parent_id':'owned-parent','root_id':'root'})),
                patch.object(e.secrets,'token_hex',return_value=tx),patch.object(e.Path,'mkdir',side_effect=lambda *a,**k:None),
                patch.object(e.Path,'exists',return_value=False),patch.object(e.native_restore,'fsync_directory'),
                patch.object(e.native_restore,'process_identity',return_value={'pid':100,'start_ticks':1,'boot_id':'fixture'}),
                patch.object(entry,'expiry_capability_idle',return_value=True,create=True)]
            for item in patches:stack.enter_context(item)
            # Old complete is historical evidence, never a usable fresh witness.
            self.assertRaises(e.CapabilityRefused,e.native_current)
            if prior_state!='complete' or drift or fail_latest:
                self.assertRaises(Exception,e.native_check,27)
                self.assertRaises(Exception,e.native_current)
                result=None
            else:
                result=e.native_check(27);fresh=e.native_current()
                self.assertEqual(fresh.observed_at,NOW.isoformat());self.assertTrue(fresh.authenticated)
        return result,graph,blobs

    def test_stale_success_renews_via_complete_native_fixed_synthetic_engine(self):
        result,graph,blobs=self.orchestration()
        self.assertEqual(result['result'],'synthetic_capability_renewed')
        self.assertEqual(graph.deletes,['"v1"','"v2"'])
        self.assertTrue(all('postgresql' not in path for method,path in graph.calls))
        self.assertEqual(json.loads(blobs[e.ROOT/('a'*32)/'complete.json'])['observed_at'],(NOW-dt.timedelta(days=2)).isoformat())
    def test_unknown_prior_config_drift_and_latest_commit_failure_fence(self):
        _,graph,_=self.orchestration(prior_state='checking');self.assertEqual(graph.calls,[])
        _,graph,_=self.orchestration(drift=True);self.assertEqual(graph.deletes,[])
        _,graph,blobs=self.orchestration(fail_latest=True)
        self.assertEqual(graph.deletes,['"v1"','"v2"'])
        self.assertEqual(json.loads(blobs[e.ROOT/'latest.json'])['state'],'checking')

    def test_complete_requires_actual_negative_positive_and_terminal_claims(self):
        self.assertEqual(e.validate_complete(complete(),CONTEXT,NOW),complete())
        for key,value in (('stale_status',204),('stale_status',True),('current_status',412),
            ('exact_absence',False),('children_terminal',False),('revision','f'*40),('bootstrap_source_sha256','0'*64)):
            row=complete();row[key]=value
            with self.subTest(key=key),self.assertRaises(e.CapabilityRefused):e.validate_complete(row,CONTEXT,NOW)
        with self.assertRaises(e.CapabilityRefused):e.validate_complete(complete(),CONTEXT,NOW+dt.timedelta(seconds=86401))

    def test_plain_claims_cannot_mint_witness_and_repr_has_no_drive(self):
        with self.assertRaises(e.CapabilityRefused):e.FreshWitness('drive','a'*64,NOW.isoformat(),BINDINGS,'e'*40)
        witness=e.FreshWitness('private-drive','a'*64,NOW.isoformat(),BINDINGS,'e'*40,e._SEAL)
        e.require_fresh(witness,'private-drive',BINDINGS,NOW.isoformat())
        self.assertNotIn('private-drive',repr(witness))
        for drive,bindings,now in (('other',BINDINGS,NOW),('private-drive',{},NOW),
                ('private-drive',BINDINGS,NOW+dt.timedelta(seconds=86401))):
            with self.assertRaises(e.CapabilityRefused):e.require_fresh(witness,drive,bindings,now.isoformat())

    def test_native_current_authenticated_cached_only_and_checking_blocks(self):
        row=complete();raw=e.bundle.canonical(row)
        latest={'schema':'careertrellis.expiry-capability-latest/v1','transaction':row['transaction'],
            'state':'complete','complete_sha256':e.digest(raw)}
        config=b'[r2]\ntype=s3\n[onedrive]\ntype=onedrive\ndrive_id=synthetic-private-drive\n'
        def document(path):
            value=latest if path.name=='latest.json' else row
            return e.bundle.canonical(value),value
        with patch.object(e,'_context',return_value=CONTEXT),patch.object(e,'_root'),\
             patch.object(e,'clock',return_value=NOW),patch.object(e,'document',side_effect=document),\
             patch.object(e.hydrate,'protected',return_value=(config,None)),\
             patch.object(e.os,'listdir',side_effect=lambda path:['latest.json','a'*32] if path==e.ROOT else ['complete.json']),\
             patch.object(e.graph_delete,'refresh_existing_remote') as refresh:
            witness=e.native_current()
            self.assertTrue(witness.authenticated);self.assertEqual(witness.digest,e.digest(raw));refresh.assert_not_called()
            latest['state']='checking'
            with self.assertRaises(e.CapabilityRefused):e.native_current()

    def test_orphan_failed_attempt_blocks_even_with_valid_latest(self):
        row=complete();latest={'schema':'careertrellis.expiry-capability-latest/v1','transaction':'a'*32,
            'state':'complete','complete_sha256':e.digest(e.bundle.canonical(row))}
        def document(path):
            if 'b'*32 in str(path):raise FileNotFoundError('missing protected complete')
            value=latest if path.name=='latest.json' else row
            return e.bundle.canonical(value),value
        with patch.object(e,'_context',return_value=CONTEXT),patch.object(e,'_root'),\
             patch.object(e,'clock',return_value=NOW),patch.object(e,'document',side_effect=document),\
             patch.object(e.hydrate,'protected',return_value=(b'[r2]\n[onedrive]\ntype=onedrive\ndrive_id=synthetic-private-drive\n',None)),\
             patch.object(e.os,'listdir',side_effect=lambda path:['latest.json','a'*32,'b'*32] if path==e.ROOT else ['complete.json']):
            with self.assertRaises(FileNotFoundError):e.native_current()

    def test_missing_entry_history_adapter_refuses_before_bootstrap_provider_or_refresh(self):
        files=Mock()
        with patch.object(entry,'expiry_capability_idle',None),\
             patch.object(e,'_context',return_value=CONTEXT),patch.object(e,'_root'),\
             patch.object(e.catalog,'NativeFiles',return_value=files),\
             patch.object(e,'_bootstrap') as bootstrap,patch.object(e.graph_delete,'refresh_existing_remote') as refresh:
            with self.assertRaises(e.CapabilityRefused):e.native_check(17)
            files.require_repository_lock.assert_called_once_with();bootstrap.assert_not_called();refresh.assert_not_called()
        with self.assertRaises(e.CapabilityRefused):e.native_current()
        self.assertIn('expiry_capability.py',entry.FILES)

    def test_mapping_rejects_new_root_and_secrets_never_in_claims(self):
        raw=b'[r2]\ntype=s3\n[onedrive]\ntype=onedrive\ndrive_id=private\nroot_folder_id=other\n'
        with self.assertRaises(e.CapabilityRefused):e._mapping(raw)
        self.assertNotIn('token',complete());self.assertNotIn('item_id',complete())


if __name__=='__main__':unittest.main()
