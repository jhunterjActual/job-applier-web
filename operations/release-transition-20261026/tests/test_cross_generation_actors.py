from portable_bootstrap import install
install()
"""All3 historical98 actors x all4 recorded97 actors; synthetic only."""
import unittest
import qualified97_history as q
from synthetic97 import Synthetic97

def rewrite(f,role,pid,birth):
 if role=='owner':
  f.v['owner.json']['pid']=pid;f.v['owner.json']['birth']=birth
  for name in ('coordinator.json','child-spawn.json','intent.json','coordinator-spawn-intent.json'):
   f.v[name]['owner_pid']=pid
   if 'owner_birth' in f.v[name]:f.v[name]['owner_birth']=birth
  record,field='owner.json','birth'
 elif role=='coordinator':
  f.v['coordinator.json']['pid']=pid;f.v['coordinator.json']['birth']=birth
  f.v['child-spawn.json']['pid']=pid;f.v['child-spawn.json']['birth']=birth
  f.v['unit-intent.json']['coordinator_pid']=pid;f.v['unit-intent.json']['coordinator_birth']=birth
  record,field='coordinator.json','birth'
 elif role=='unitclient':
  f.v['unit-client.json']['pid']=pid;f.v['unit-client.json']['birth']=birth
  f.v['unit-terminal.json']['client_pid']=pid;f.v['unit-terminal.json']['client_birth']=birth
  record,field='unit-client.json','birth'
 else:
  assert role=='materialization-child';f.v['materialization-child.json']['pid']=pid;f.v['materialization-child.json']['start_ticks']=birth
  record,field='materialization-child.json','start_ticks'
 f.refresh()
 actual=q.decode(f.b[record]);assert actual['pid']==pid and actual[field]==birth
 return record,field
class CrossGenerationActors(unittest.TestCase):
 def test_all12_exact_old_identities_refuse_after_coherent_rehash(self):
  for oldname in ('owner.json','coordinator.json','test-client.json'):
   for role in ('owner','coordinator','unitclient','materialization-child'):
    f=Synthetic97();old=q.decode(f.old[oldname]);record,field=rewrite(f,role,old['pid'],old['start_ticks'])
    with self.subTest(old=oldname,new=role),self.assertRaises(q.Refused):f.inspect()
    self.assertEqual(q.decode(f.b[record])[field],old['start_ticks'])
 def test_all12_different_birth_pid_reuse_remains_valid(self):
  for oldname in ('owner.json','coordinator.json','test-client.json'):
   for role in ('owner','coordinator','unitclient','materialization-child'):
    f=Synthetic97();old=q.decode(f.old[oldname]);record,field=rewrite(f,role,old['pid'],'999999999')
    result=f.inspect()
    with self.subTest(old=oldname,new=role):
     self.assertEqual(q.decode(f.b[record])[field],'999999999');self.assertFalse(result['historical']['authorizes_preparation'])
if __name__=='__main__':unittest.main()
