import portable_bootstrap as pb
import copy,hashlib,importlib.util,json,os,pathlib,sys,tempfile,unittest
from datetime import timedelta
import public_schema_fixtures as f
P=pathlib.Path(__file__).parent
class Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  raw=(pb.FIXTURES/'db_core.reference.py').read_bytes();assert hashlib.sha256(raw).hexdigest()=='8d2facf6d71bf985c1c0ffe8b6a661fc9a9699548b6bb81163cd787f53ba97fa'
  sp=importlib.util.spec_from_file_location('pinned_actual_database_health_core',pb.FIXTURES/'db_core.reference.py');h=importlib.util.module_from_spec(sp);sys.modules[sp.name]=h;sp.loader.exec_module(h);cls.h=h;f.backup_health=h
  (pathlib.Path(tempfile.gettempdir())/'careertrellis-role-tests').mkdir(exist_ok=True)
 def root(self):return tempfile.TemporaryDirectory(prefix='dbcore-synthetic-',dir=pathlib.Path(tempfile.gettempdir())/'careertrellis-role-tests')
 def put(self,path,raw):
  with path.open('xb')as stream:stream.write(raw)
  if os.name=='posix':os.chmod(path,0o600)
 def healthy(self,root):
  root=pathlib.Path(root);times={'full_backup':f.OBSERVED-timedelta(days=1),'differential_backup':f.OBSERVED-timedelta(hours=2),'pgbackrest_mirror':f.OBSERVED-timedelta(hours=1),'pgbackrest_inventory':f.OBSERVED-timedelta(days=1),'pgbackrest_restore':f.OBSERVED-timedelta(days=10)}
  for number,(operation,when)in enumerate(times.items()):
   doc=f.operation_receipt(operation,completed=when)
   if operation in ('pgbackrest_mirror','pgbackrest_inventory'):
    payload=json.dumps({'synthetic':operation,'at':f.timestamp(when)}).encode();pin=hashlib.sha256(payload).hexdigest();self.put(root/('pgbackrest-generation-'+pin+'.json'),payload);doc.update(schema=self.h.GENERATION_RECEIPT_SCHEMA,generation_sha256=pin)
   payload=self.h.serialize_operation_receipt(doc);name='20260825T120000Z-'+operation+'-'+format(number,'016x')+'.json';self.put(root/name,payload);self.put(root/name.replace('.json','.sha256'),(hashlib.sha256(payload).hexdigest()+'  '+name+'\n').encode())
  return root
 def test_real_unmodified_core_solelegacy_then_exact_one_field_derived_ok(self):
  with self.root()as name:
   root=self.healthy(name);snapshot=f.snapshot('database');snapshot['units']['full_backup']['missed']=True;before=copy.deepcopy(snapshot);raw=self.h.evaluate_backup_health(snapshot,receipt_root=root);self.assertEqual(raw['failure_codes'],['full_backup_unit_missed']);derived=copy.deepcopy(snapshot);derived['units']['full_backup']['missed']=False;result=self.h.evaluate_backup_health(derived,receipt_root=root);self.assertEqual(result['result'],'ok');self.assertEqual(snapshot,before);self.assertTrue(self.h.scan_receipts(root=root).receipts['full_backup'])
 def test_real_core_all_other_unit_failures_and_wal_preserved(self):
  with self.root()as name:
   root=self.healthy(name)
   for operation in self.h.SURFACE_OPERATIONS['database']:
    snapshot=f.snapshot('database');snapshot['units'][operation]['last_result']='failed';self.assertIn(operation+'_unit_failed',self.h.evaluate_backup_health(snapshot,receipt_root=root)['failure_codes'])
   snapshot=f.snapshot('database');snapshot['wal']['failed_count']=2;snapshot['wal']['previous_failed_count']=0;self.assertNotEqual(self.h.evaluate_backup_health(snapshot,receipt_root=root)['result'],'ok')
 def test_real_core_corrupt_committed_inventory_not_hidden(self):
  with self.root()as name:
   root=self.healthy(name);path=next(root.glob('*-pgbackrest_inventory-*.sha256'));path.write_bytes(b'invalid-synthetic-manifest');result=self.h.evaluate_backup_health(f.snapshot('database'),receipt_root=root);self.assertIn('pgbackrest_inventory_receipt_invalid',result['failure_codes'])
if __name__=='__main__':unittest.main()
