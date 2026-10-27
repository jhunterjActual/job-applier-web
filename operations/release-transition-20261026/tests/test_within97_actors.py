from portable_bootstrap import install
install()
"""Within97 historical identities: six exact-pair refusals, concurrency preserved."""
import unittest
import qualified97_history as q
from synthetic97 import Synthetic97
from test_cross_generation_actors import rewrite
PAIRS=[('owner','coordinator'),('owner','unitclient'),('coordinator','unitclient'),('owner','materialization-child'),('coordinator','materialization-child'),('materialization-child','unitclient')]
def identity(f,role):
 name={'owner':'owner.json','coordinator':'coordinator.json','unitclient':'unit-client.json','materialization-child':'materialization-child.json'}[role]
 v=f.v[name];return v['pid'],v['start_ticks'] if role=='materialization-child' else v['birth']
class Within97Actors(unittest.TestCase):
 def test_all6_exact_pairs_refuse_with_serialized_mutant_and_relinked_ancestors(self):
  for source,target in PAIRS:
   f=Synthetic97();pid,birth=identity(f,source);record,field=rewrite(f,target,pid,birth)
   self.assertEqual(q.decode(f.b[record])[field],birth)
   with self.subTest(source=source,target=target),self.assertRaises(q.Refused):f.inspect()
 def test_concurrent5_same_pid_still_refuse_even_different_birth(self):
  for source,target in PAIRS[:-1]:
   f=Synthetic97();pid,birth=identity(f,source);record,field=rewrite(f,target,pid,'999999999')
   self.assertEqual(q.decode(f.b[record])[field],'999999999')
   with self.subTest(source=source,target=target),self.assertRaises(q.Refused):f.inspect()
 def test_sequential_materialization_to_unit_same_pid_different_birth_is_valid(self):
  f=Synthetic97();pid,birth=identity(f,'materialization-child');record,field=rewrite(f,'unitclient',pid,'999999999')
  self.assertEqual(q.decode(f.b[record])['pid'],pid);self.assertEqual(q.decode(f.b[record])[field],'999999999')
  value=f.inspect();self.assertFalse(value['historical']['authorizes_preparation'])
  self.assertIn('no invented parent/time/terminal',value['historical']['materialization_child_qualification'])
if __name__=='__main__':unittest.main()
