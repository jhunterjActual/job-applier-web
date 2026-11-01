from portable_bootstrap import install
install()
"""Occurrence-specific synthetic97 attacks, with no real records or live IO."""
import re,unittest,copy,json
import qualified97_history as q
from synthetic97 import Synthetic97,encoded,sha
OPAQUE_DIGESTS={('preflight.json',('software_inventory_sha256',)),('checks.json',('details','startup','log_sha256'))}
def mutation(node,path,value):
 key=str(path[-1])
 if (node,path) in OPAQUE_DIGESTS:return 'SYNTHETIC_BAD_DIGEST'
 if node=='materialization-child.json':
  if key=='pid':return 0
  if key=='start_ticks':return 'invalid'
 if isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value):return '9'*64 if value!='9'*64 else '8'*64
 if key in {'schema','kind','source_kind','revision','build','baseline','previous','ops_commit','repair_ops_commit','attempt_id','unit','runtime_manifest','qualification','test_id','owner_state','coordinator_state'} and isinstance(value,str):return 'SYNTHETIC_OTHER'
 if key=='boot_id':return '99999999-1111-4222-8333-444444444444'
 if key in {'started_at','created_at','completed_at','observed_at','completed_utc','ended_at','started_utc','ended_utc','decision_at','expires_at','original_deadline'}:return 'INVALID_TIME'
 if key in {'birth','owner_birth','coordinator_birth','client_birth'}:return value+'7'
 if key in {'pid','owner_pid','coordinator_pid','client_pid'} and type(value) is int:return value+77
 if key=='bytes' and type(value) is int:return value+1
 if key in {'processes','populated','client_exit','coordinator_exit','calls','provider_requests','tests','failures','errors'} and type(value) is int:return value+1
 if type(value) is bool and key in {'constructor_returned','method_returned','binding_restored','serial_suite_verified','runtime_code_verified','fixture_ancestors_verified','network_namespace_loopback_only','owner_self_exit_claim','activated','release_absent','namespace_absent','unit_absent','no_replay','eof','error','dashboard_match','owned_process_stopped'}:return not value
 if type(value) is bool and len(path)>1 and path[-2]=='checks':return not value
 if key in {'MainPID','ControlPID','ExecMainStatus','Job'}:return '17'
 if key in {'InvocationID','invocation_id'}:return 'f'*32
 if key=='Description':return 'SYNTHETIC_OTHER'
 if key=='LoadState':return 'loaded' if value=='not-found' else 'not-found'
 if key=='ActiveState':return 'failed'
 if key=='SubState':return 'running'
 if key=='Result':return 'failed'
 if key=='path':return '/system.slice/SYNTHETIC_OTHER.service'
 if node=='postterminal-proof.json' and key=='exists':return True
 if key=='api_build':return 'SYNTHETIC_OTHER'
 if key=='exit_signal':return 'SIGKILL'
 # Every argv/property/module/catalog literal is source-derived, not self-approved.
 if isinstance(value,str) and any(x in path for x in ('argv','properties','modules','catalog_ids','successful_ids','success_ids')):return 'SYNTHETIC_OTHER'
 return None
def walk(node,v,path=()):
 if isinstance(v,dict):
  for k,item in v.items():yield from walk(node,item,path+(k,))
 elif isinstance(v,list):
  for k,item in enumerate(v):yield from walk(node,item,path+(k,))
 else:
  replacement=mutation(node,path,v)
  if replacement is not None:yield node,path,replacement
class OccurrenceTests(unittest.TestCase):
 def test_all_critical_occurrences_retain_mutant_then_refuse(self):
  base=Synthetic97();cases=[case for name,v in base.v.items() for case in walk(name,v)]
  self.assertGreater(len(cases),300)
  for name,path,value in cases:
   with self.subTest(node=name,path=path):
    f=Synthetic97();f.mutate_occurrence(name,path,value);leaf=q.decode(f.b[name])
    for key in path:leaf=leaf[key]
    self.assertEqual(leaf,value)
    with self.assertRaises(q.Refused):f.inspect()
 def test_source_registry_runtime_and_actor_coherent_attacks(self):
  f=Synthetic97();f.sources['qualified97/checks97.py']+=b'\n# tampered\n'
  with self.assertRaises(q.Refused):f.inspect()
  f=Synthetic97();f.binding['ops_commit']='3'*40
  with self.assertRaises(q.Refused):f.inspect()
  f=Synthetic97();reg=q.decode(f.registry_raw);reg['ops_commit']='3'*40;f.registry_raw=encoded(reg);f.binding['registry_sha256']=sha(f.registry_raw)
  with self.assertRaises(q.Refused):f.inspect()
  f=Synthetic97();o=f.v['owner.json'];co=f.v['coordinator.json'];co['pid']=o['pid'];co['birth']=o['birth'];f.v['child-spawn.json']['pid']=o['pid'];f.v['child-spawn.json']['birth']=o['birth'];f.v['unit-intent.json']['coordinator_pid']=o['pid'];f.v['unit-intent.json']['coordinator_birth']=o['birth'];f.refresh()
  with self.assertRaises(q.Refused):f.inspect()
 def test_missing_extra_duplicate_and_unknown_source_refuse(self):
  for key in q.BUNDLE22:
   f=Synthetic97();f.b.pop(key)
   with self.subTest(missing=key),self.assertRaises(q.Refused):f.inspect()
  f=Synthetic97();f.b['arbitrary.json']=b'{}'
  with self.assertRaises(q.Refused):f.inspect()
  f=Synthetic97();f.sources['arbitrary']=b'ignored'
  with self.assertRaises(q.Refused):f.inspect()
  f=Synthetic97();f.b['accepted.json']=b'{"schema":1,"schema":2}'
  with self.assertRaises(q.Refused):f.inspect()
if __name__=='__main__':unittest.main()
