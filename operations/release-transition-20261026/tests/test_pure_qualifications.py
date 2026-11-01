from portable_bootstrap import install
install()
"""Pure historical proofs: known minimums, original causality, cached source only."""
import copy,json,socket,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
import qualified97_history as q
from synthetic97 import Synthetic97,encoded,sha
class PureQualificationTests(unittest.TestCase):
 def test_full_inspection_performs_no_filesystem_process_or_socket_io(self):
  f=Synthetic97()
  with patch('builtins.open',side_effect=AssertionError('filesystem forbidden')),patch.object(Path,'open',side_effect=AssertionError('Path IO forbidden')),patch.object(subprocess,'run',side_effect=AssertionError('process forbidden')),patch.object(subprocess,'Popen',side_effect=AssertionError('process forbidden')),patch.object(socket,'socket',side_effect=AssertionError('socket forbidden')):
   result=f.inspect()
  self.assertFalse(result['historical']['authorizes_preparation']);self.assertTrue(result['historical']['requires_current_source_runtime_equality'])
 def test_known_preflight_minimums_and_original_freshness_refuse(self):
  cases=[(('capacity','memory_available'),768*1024**2-1),(('capacity','available_bytes'),5*1024**3-1),(('capacity','available_inodes'),4095),(('observed_at',),'2025-01-02T00:01:01+00:00'),(('observed_at',),'2025-01-01T23:55:59+00:00')]
  for path,value in cases:
   f=Synthetic97();f.mutate_occurrence('preflight.json',path,value)
   with self.subTest(path=path,value=value),self.assertRaises(q.Refused):f.inspect()
 def test_coherent_self_reported_catalog_cannot_replace_source_catalog(self):
  f=Synthetic97();block=f.v['checks.json']['details']['summary'];ids=['test_fake.C.test_fake']
  block.update(tests=1,modules=['test_fake'],catalog_ids=ids,catalog_sha256=sha(json.dumps(ids,separators=(',',':')).encode()),successful_ids=ids,success_ids=[],skipped=[]);f.refresh()
  with self.assertRaises(q.Refused):f.inspect()
 def test_cached_deploy_facade_refuses_any_other_path(self):
  with self.assertRaises(q.Refused):q._ReleaseSource(b'public')/'private.env'
 def test_changed_module_catalog_ast_is_not_executed(self):
  f=Synthetic97();sources=dict(f.sources);raw=sources['qualified97/run_checks_qualified97.py'];needle=b'def modules_for(release,revision):\n';self.assertEqual(raw.count(needle),1)
  sources['qualified97/run_checks_qualified97.py']=raw.replace(needle,needle+b"    raise AssertionError('injected body must not execute')\n")
  with self.assertRaises(q.Refused):q._catalogs(sources)
 def test_coherent_97_reboot_cannot_claim_original98_same_boot_admission(self):
  f=Synthetic97();old=f.boot;new='99999999-1111-4222-8333-444444444444'
  def change(v):
   if isinstance(v,dict):return {k:change(x) for k,x in v.items()}
   if isinstance(v,list):return [change(x) for x in v]
   return new if v==old else v
  f.v=change(f.v);f.refresh()
  with self.assertRaises(q.Refused):f.inspect()
 def test_97_preflight_cannot_predate_accepted98_completion(self):
  f=Synthetic97()
  def shift(v):
   if isinstance(v,dict):return {k:shift(x) for k,x in v.items()}
   if isinstance(v,list):return [shift(x) for x in v]
   return v.replace('2025-01-02T','2024-12-31T') if isinstance(v,str) else v
  f.v=shift(f.v);f.refresh()
  with self.assertRaises(q.Refused):f.inspect()
 def rewrite_owner(self,f,pid,birth):
  f.v['owner.json']['pid']=pid;f.v['owner.json']['birth']=birth
  for name in ('coordinator.json','child-spawn.json','intent.json','coordinator-spawn-intent.json'):
   f.v[name]['owner_pid']=pid
   if 'owner_birth' in f.v[name]:f.v[name]['owner_birth']=birth
  f.refresh()
 def test_exact_original98_actor_identity_cannot_be_reused(self):
  f=Synthetic97();old=q.decode(f.old['owner.json']);self.rewrite_owner(f,old['pid'],old['start_ticks'])
  with self.assertRaises(q.Refused):f.inspect()
 def test_different_birth_pid_reuse_remains_valid(self):
  f=Synthetic97();old=q.decode(f.old['owner.json']);self.rewrite_owner(f,old['pid'],'999999')
  self.assertFalse(f.inspect()['historical']['authorizes_preparation'])
 def test_each97_role_cannot_match_each_original98_identity(self):
  for old_name in ('owner.json','coordinator.json'):
   for new_role in ('owner','coordinator'):
    f=Synthetic97();old=q.decode(f.old[old_name]);pid=old['pid'];birth=old['start_ticks']
    if new_role=='owner':self.rewrite_owner(f,pid,birth)
    else:
     f.v['coordinator.json']['pid']=pid;f.v['coordinator.json']['birth']=birth
     f.v['child-spawn.json']['pid']=pid;f.v['child-spawn.json']['birth']=birth
     f.v['unit-intent.json']['coordinator_pid']=pid;f.v['unit-intent.json']['coordinator_birth']=birth;f.refresh()
    with self.subTest(old=old_name,new=new_role),self.assertRaises(q.Refused):f.inspect()
 def test_copied_pure_dependencies_are_exact_and_published_semantics_match(self):
  import ast,hashlib
  root=Path(__file__).resolve().parent;register=json.loads((root/'DEPENDENCIES.json').read_text())
  for name,meta in register.items():self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),meta['sha256'])
  for name in ('qualified97_records.py','qualified_specs.py','checks97.py','historical98_controls.py'):
   local=(root/name).read_bytes();published=(root/'public-fixtures/qualified97-source'/name).read_bytes()
   self.assertEqual(ast.dump(ast.parse(local),include_attributes=False),ast.dump(ast.parse(published),include_attributes=False))
if __name__=='__main__':unittest.main()
