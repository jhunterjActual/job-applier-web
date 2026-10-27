import copy,hashlib,json,unittest
import checks97 as c
class Checks97Tests(unittest.TestCase):
 def block(self):
  ids=['test_synthetic.C.test_a'];return {'tests':1,'modules':['test_synthetic'],'skipped':[],'success_ids':[],'catalog_ids':ids,'catalog_sha256':hashlib.sha256(json.dumps(ids,separators=(',',':')).encode()).hexdigest(),'successful_ids':ids,'failures':0,'errors':0}
 def test_actual_catalog_not_fixed899(self):self.assertEqual(c.catalog(self.block(),False),['test_synthetic.C.test_a'])
 def test_count_hash_partition_or_foreign_module_refused(self):
  for k,v in [('tests',899),('tests',True),('catalog_sha256','wrong'),('successful_ids',[]),('modules',['test_other'])]:
   b=self.block();b[k]=v
   with self.subTest(field=k),self.assertRaises(RuntimeError):c.catalog(b,False)
 def test_98_qualification_module_not_97(self):
  q={'schema':'careertrellis.exact-fixture-relocation/v1','test_id':'test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables','calls':1,'constructor_returned':True,'method_returned':True,'binding_restored':True,'analyzer_returncodes':[0,1],'serial_suite_verified':True,'runtime_code_verified':True,'fixture_ancestors_verified':True,'module_sha256':c.MODULE_SHA,'method_source_sha256':'584aec03af3f6347fbce3f10f56b449deeea0925f3365e6f28a5d3cfcbd85cac','method_ast_sha256':'ad2abbb01e5d212e3045b604105c2dc788281d56d64735887a827caad841af73','qualification':'one TemporaryDirectory dir relocated; unchanged body and real positive/negative analyzer calls'}
  c.qualification(q);q['module_sha256']='341e728a03f4f76bfa04c64aec612fae9fd87dc831266e547d9f42c7f0a42ab4'
  with self.assertRaises(RuntimeError):c.qualification(q)
if __name__=='__main__':unittest.main()
