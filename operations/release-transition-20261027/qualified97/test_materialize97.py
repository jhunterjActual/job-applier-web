"""Pure97 proposal controls. No materialization/host subprocess is invoked."""
import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import materialize97 as m
from qualified_specs import exact_spec
class MaterializationTests(unittest.TestCase):
 def test_fixed_modes_and_checks_cannot_cross(self):
  self.assertEqual(exact_spec('repair98-retained/v1')['materialization'],'retained-only')
  self.assertEqual(exact_spec('qualified97-new/v1')['materialization'],'new-only')
  self.assertEqual(len(exact_spec('qualified97-new/v1')['checks']),11)
  with self.assertRaises(RuntimeError):exact_spec('caller-revision')
  with self.assertRaises(TypeError):exact_spec('qualified97-new/v1')['revision']='wrong'
 def test_real_owned_child_control_success_records_birth_without_timeout(self):
  child=Mock(pid=42);child.poll.side_effect=[None,0,0];factory=Mock(return_value=child);record=Mock()
  m.own_child(['fixed'],record,lambda pid:'birth',factory,lambda _:None)
  self.assertNotIn('timeout',factory.call_args.kwargs);record.assert_called_once();child.kill.assert_not_called();child.terminate.assert_not_called()
 def test_birth_and_ledger_faults_retain_child_until_terminal(self):
  for where in ('birth','record'):
   child=Mock(pid=42);child.poll.side_effect=[None,0];factory=Mock(return_value=child)
   birth=Mock(side_effect=OSError('synthetic') if where=='birth' else None,return_value='birth')
   record=Mock(side_effect=OSError('synthetic') if where=='record' else None)
   with self.assertRaises(OSError):m.own_child(['fixed'],record,birth,factory,lambda _:None)
   self.assertEqual(child.poll.call_count,2);child.kill.assert_not_called();child.terminate.assert_not_called()
 def test_nonzero_child_never_success(self):
  child=Mock(pid=42);child.poll.return_value=1
  with self.assertRaises(RuntimeError):m.own_child(['fixed'],Mock(),lambda pid:'birth',Mock(return_value=child),lambda _:None)
 def test_expired_and_reboot_refuse(self):
  now=dt.datetime.now(dt.timezone.utc)
  for boot,expiry in [('new',now+dt.timedelta(minutes=1)),('old',now-dt.timedelta(seconds=1))]:
   with self.assertRaises(RuntimeError):m.guard_deadline({'expires_at':expiry.isoformat()},'old',lambda:boot,lambda:now)
 def test_enabled_entry_rejects_malformed_controls_before_child(self):
  from unittest.mock import patch
  self.assertTrue(m.READY)
  with patch.object(m,'own_child') as child,self.assertRaises(RuntimeError):
   m.materialize({},None,None,None,None,None,None,None,None,None,None)
  child.assert_not_called()
if __name__=='__main__':unittest.main()
