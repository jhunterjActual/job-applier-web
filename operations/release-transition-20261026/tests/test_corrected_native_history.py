from portable_bootstrap import install
install()
"""Synthetic historical proof regressions, never real receipt/native evidence."""
import unittest,copy
from synthetic97 import Synthetic97,encoded,sha
import qualified97_history as q
class CorrectedHistoryTests(unittest.TestCase):
 def test_expired_corrected_receipt_and_no_current_authority(self):
  f=Synthetic97();r=f.inspect()['historical'];self.assertEqual(r['revision'],q.REV);self.assertEqual(r['source_kind'],'corrected-fixture97-new/v1');self.assertFalse(r['authorizes_preparation']);self.assertTrue(r['requires_current_source_runtime_equality'])
 def test_native_numerical_claim_and_wrong_candidate_refused(self):
  for field,value in [('analyzer_returncodes',[0,1]),('candidate','0'*40),('runtime_substitution',True),('assertion_evidence','fabricated numeric outcomes')]:
   f=Synthetic97();proof=f.v['checks.json']['details']['backend']['native_source_proof'];proof[field]=value;f.v['accepted.json']['native_source_proof']=copy.deepcopy(proof);f.refresh()
   with self.subTest(field=field),self.assertRaises(q.Refused):f.inspect()
 def test_old_relocation_field_refused(self):
  f=Synthetic97();block=f.v['checks.json']['details']['backend'];block['fixture_qualification']=block.pop('native_source_proof');f.refresh()
  with self.assertRaises(q.Refused):f.inspect()
 def test_campaign_21_actual_source_ids_must_all_succeed(self):
  f=Synthetic97();block=f.v['checks.json']['details']['evaluation'];one=q.corrected_source_contract(f.sources)[0];block['successful_ids'].remove(one);block['skipped'].append(one);f.refresh()
  with self.assertRaises(q.Refused):f.inspect()
 def test_changed_public_native_or_campaign_source_refused(self):
  for path in ('target97/backend/test_p0_regressions.py','target97/scripts/value_evaluation/test_campaign_transport.py'):
   f=Synthetic97();f.sources[path]+=b'\n'
   with self.subTest(path=path),self.assertRaises(q.Refused):f.inspect()
if __name__=='__main__':unittest.main()
