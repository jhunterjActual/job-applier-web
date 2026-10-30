import unittest
from unittest.mock import patch
from types import SimpleNamespace
import native97_unit as n
class UnitTests(unittest.TestCase):
 def test_absent_exact_unit_is_closed(self):
  body=b'LoadState=not-found\nMainPID=0\nControlPID=0\nJob=\nInvocationID=\n'
  with patch.object(n.subprocess,'run',return_value=SimpleNamespace(returncode=4,stdout=body)),patch.object(n.Path,'exists',return_value=False):
   state,cg=n.require_absent();self.assertEqual(cg['processes'],0)
 def test_live_or_queued_never_absent(self):
  for extra in (b'MainPID=12\n',b'Job=9\n',b'InvocationID=abc\n'):
   body=b'LoadState=not-found\n'+extra
   with patch.object(n.subprocess,'run',return_value=SimpleNamespace(returncode=4,stdout=body)),patch.object(n.Path,'exists',return_value=False),self.assertRaises(RuntimeError):n.require_absent()
 def test_arbitrary_controlgroup_or_extra_key_refused(self):
  for body in (b'LoadState=loaded\nControlGroup=/other\n',b'LoadState=not-found\nprivate=secret\n'):
   with patch.object(n.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=body)),self.assertRaises(RuntimeError):n.snapshot()
if __name__=='__main__':unittest.main()
