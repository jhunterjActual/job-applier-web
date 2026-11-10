"""All critical repeated schema occurrences: one target, hash ancestors only."""
import copy,re,unittest
from synthetic98_dag import SyntheticDag

def critical(node,path,value):
 key=path[-1];name=str(key)
 if isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value):return '9'*64 if value!='9'*64 else '8'*64
 if name in {'schema','release','build','baseline','previous','repair_ops_commit','attempt_id','unit','runtime_manifest','test_id','qualification','owner_state','coordinator_state'} and isinstance(value,str):return 'SYNTHETIC_OTHER'
 if name=='boot_id' and isinstance(value,str):return '99999999-1111-4222-8333-444444444444'
 if name in {'started_at','created_at','observed_at','completed_utc','ended_at','started_utc','ended_utc','decision_at','expires_at','original_deadline'} and isinstance(value,str):return 'SYNTHETIC_INVALID_TIME'
 if name in {'start_ticks','coordinator_start_ticks','client_start_ticks'} and isinstance(value,str):return value+'7'
 if name in {'pid','owner_pid','coordinator_pid','client_pid'} and type(value) is int:return value+77
 if name in {'processes','populated','client_exit','coordinator_exit','calls','provider_requests','tests','failures','errors','bytes'} and type(value) is int:return value+1
 if isinstance(value,bool) and name in {'constructor_returned','method_returned','binding_restored','serial_suite_verified','runtime_code_verified','fixture_ancestors_verified','network_namespace_loopback_only','owner_self_exit_claim','activated','locks_clear','active_running','eof','error'}:return not value
 if isinstance(value,bool) and (len(path)>1 and path[-2]=='checks'):return not value
 if node=='postterminal-owner-proof.json' and name=='exists':return True
 if name in {'MainPID','ControlPID','ExecMainStatus','Job'} and isinstance(value,str):return '17'
 if name in {'InvocationID','invocation_id'} and isinstance(value,str):return 'f'*32 if value!='f'*32 else 'e'*32
 if name=='Description' and isinstance(value,str):return 'SYNTHETIC_OTHER'
 if name=='LoadState':return 'loaded' if value=='not-found' else 'not-found'
 if name=='ActiveState':return 'failed'
 if name=='SubState':return 'running'
 if name=='Result':return 'failed'
 if name=='path' and isinstance(value,str):return '/system.slice/SYNTHETIC_OTHER.service'
 return None

def occurrences(node,value,path=()):
 if isinstance(value,dict):
  for key,item in value.items():yield from occurrences(node,item,path+(key,))
 elif isinstance(value,list):
  for index,item in enumerate(value):yield from occurrences(node,item,path+(index,))
 else:
  mutation=critical(node,path,value)
  if mutation is not None:yield node,path,mutation
class OccurrenceMatrixTests(unittest.TestCase):
 def test_complete_critical_occurrence_matrix_retains_exact_target_bytes(self):
  base=SyntheticDag();cases=[case for node,value in base.nodes.items() if node!='old-failure-chain.json' for case in occurrences(node,value)]
  self.assertGreater(len(cases),200)
  for node,path,value in cases:
   with self.subTest(node=node,path=path):
    f=SyntheticDag();old=f.expected;f.mutate_occurrence(node,path,value)
    if node in ('owner.json','coordinator.json'):self.assertEqual(old,f.expected) # These originals are linked by duplicate payload equality, not a direct digest.
    else:self.assertNotEqual(old,f.expected)
    leaf=f.ns['decode'](f.raw[node])
    for key in path:leaf=leaf[key]
    self.assertEqual(leaf,value,'emitter repaired the requested mutated occurrence')
    with self.assertRaises((RuntimeError,ValueError,KeyError)):f.authenticate()
 def test_five_specific_coherent_invalid_histories(self):
  for key in ('source_manifest_sha256','old_failure_chain_sha256'):
   f=SyntheticDag();f.mutate_occurrence('full-preflight.json',(key,),'9'*64)
   with self.assertRaises(RuntimeError):f.authenticate()
  f=SyntheticDag();f.mutate_occurrence('repair-checks.json',('started_utc',),'2020-01-01T00:00:10+00:00')
  with self.assertRaises(RuntimeError):f.authenticate()
  f=SyntheticDag();f.mutate_occurrence('unit-intent.json',('created_at',),'2020-01-01T00:00:30+00:00')
  with self.assertRaises(RuntimeError):f.authenticate()
  f=SyntheticDag();owner=f.nodes['owner.json'];coord=f.nodes['coordinator.json'];coord['pid']=owner['pid'];coord['start_ticks']=owner['start_ticks']
  f.nodes['child-spawn.json']['pid']=owner['pid'];f.nodes['child-spawn.json']['start_ticks']=owner['start_ticks']
  f.nodes['unit-intent.json']['coordinator_pid']=owner['pid'];f.nodes['unit-intent.json']['coordinator_start_ticks']=owner['start_ticks']
  f.refresh() # Deliberately coherent actor rewrite across semantic duplicates.
  with self.assertRaises(RuntimeError):f.authenticate()
if __name__=='__main__':unittest.main()
