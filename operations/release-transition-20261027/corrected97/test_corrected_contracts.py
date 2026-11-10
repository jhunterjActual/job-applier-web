import copy,unittest
import checks97 as c
import qualified97_records as records
from qualified_specs import exact_spec
class CorrectedContracts(unittest.TestCase):
 def proof(self):return {'schema':'careertrellis.corrected-fixture-native-proof/v1','source_kind':'corrected-fixture97-new/v1','candidate':'bc68ea0dc253425a44e268452130becdf35d764e','module_sha256':c.MODULE_SHA,'method_ast_sha256':dict(c.NATIVE_METHOD_AST_SHA),'native_ids_succeeded':list(c.NATIVE_IDS),'assertion_evidence':'source_authenticated_positive_zero_and_missing_helper_nonzero_assertions_succeeded','runtime_substitution':False}
 def test_closed_corrected_native_proof(self):c.qualification(self.proof())
 def test_native_proof_does_not_accept_old_kind_or_substitution(self):
  for key,value in [('schema','careertrellis.exact-fixture-relocation/v1'),('source_kind','qualified97-new/v1'),('candidate','56c8b137fd6c6e778a9f09e3b04b925edea6270e'),('runtime_substitution',True),('native_ids_succeeded',[]),('module_sha256','0'*64),('method_ast_sha256',{}),('assertion_evidence','observed [0,1]')]:
   p=self.proof();p[key]=value
   with self.subTest(key=key),self.assertRaises(RuntimeError):c.qualification(p)
  p=self.proof();p['analyzer_returncodes']=[0,1]
  with self.assertRaises(RuntimeError):c.qualification(p)
 def test_old_kind_unconfigurable(self):
  with self.assertRaises(RuntimeError):exact_spec('qualified97-new/v1')
  self.assertEqual(exact_spec('corrected-fixture97-new/v1')['revision'],'bc68ea0dc253425a44e268452130becdf35d764e')
 def test_final_receipt_new_closed_semantics(self):
  schema,keys=records.SCHEMAS['accepted.json'];self.assertEqual(schema,'careertrellis.preparation97-corrected-acceptance/v1');self.assertIn('native_source_proof',keys);self.assertNotIn('fixture_qualification',keys)
  p={k:None for k in keys};p['schema']='careertrellis.preparation97-qualified-acceptance/v1'
  with self.assertRaises(RuntimeError):records.closed('accepted.json',p)
if __name__=='__main__':unittest.main()
