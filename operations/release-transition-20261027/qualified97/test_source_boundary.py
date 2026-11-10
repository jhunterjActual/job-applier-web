"""No host operations: closed source loading and fixed-kind admission negatives."""
import unittest
from unittest.mock import patch,Mock
import source_boundary as s
import accepted98_dependency as a
class SourceBoundaryTests(unittest.TestCase):
 def test_contract_wrongbytes_refuses_before_git(self):
  with patch.object(s.subprocess,'run') as run,self.assertRaises(RuntimeError):s.contract_namespace(b'unapproved','a'*40)
  run.assert_not_called()
 def test_invalid_ops_revision_refuses_before_git(self):
  with patch.object(s.subprocess,'run') as run,self.assertRaises(RuntimeError):s.contract_namespace(b'', 'main')
  run.assert_not_called()
 def test_unrecognized_kind_refuses_before_read(self):
  with patch.object(s,'read_root') as read,self.assertRaises(RuntimeError):s.load_fixed('arbitrary-kind','a'*40)
  read.assert_not_called()
 def test_legacy_or_wrong_contract_dependency_never_reads_receipt(self):
  read=Mock();ns={'sha':lambda raw:'wrong','DOMAIN':a.DOMAIN,'read':read}
  with self.assertRaises(RuntimeError):a.authenticate(ns,'a'*64,b'wrong-source')
  read.assert_not_called()
if __name__=='__main__':unittest.main()
