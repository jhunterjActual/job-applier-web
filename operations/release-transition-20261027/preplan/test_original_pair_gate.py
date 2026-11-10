"""Original pair IO negatives; no real private receipts are fixtures."""
import unittest,types,ast,pathlib,json
from datetime import datetime,timezone
from unittest.mock import patch
import native_pair as pair
import native_io as n
import public_version
D=pathlib.Path(__file__).parent
class Tests(unittest.TestCase):
 def test_pair_original_wrong_bytes_never_claim_verified(self):
  public={'accepted_pair_validator.py':(D/'public-runtime35/accepted_pair_validator.py').read_bytes()}
  names=[]
  try:pair.validate(public,b'SYNTHETIC_BAD_WEB',b'SYNTHETIC_BAD_DB',datetime.now(timezone.utc).isoformat())
  except Exception as error:
   tb=error.__traceback__
   while tb is not None:names.append(tb.tb_frame.f_code.co_name);tb=tb.tb_next
  else:self.fail('synthetic wrong original bytes accepted')
  self.assertIn('decode',names)
 def test_db_cleanup_unknown_owner_refuses_before_receipt(self):
  reader=types.SimpleNamespace(read=lambda *a:(_ for _ in ()).throw(AssertionError('receipt should not be reached')))
  with patch.object(pair.os.path,'lexists',return_value=True):self.assertRaises(n.Refused,pair.local,'db',reader)
 def test_fixed_receipt_digest_refuses(self):
  reader=types.SimpleNamespace(read=lambda *a:b'SYNTHETIC')
  with patch.object(pair,'cleanup_absent'):self.assertRaises(n.Refused,pair.local,'db',reader)
 def test_public_version_functions_exact_preflight05_ast(self):
  originals=json.loads((D/'PUBLIC_VERSION_PROVENANCE.json').read_bytes())['functions_ast'];current=ast.parse((D/'public_version.py').read_bytes())
  for node in current.body:
   if isinstance(node,ast.FunctionDef):self.assertEqual(ast.dump(node,include_attributes=False),originals[node.name])

 def test_active_cleanup_unit_refuses_before_receipt(self):
  reader=types.SimpleNamespace(read=lambda *a:(_ for _ in ()).throw(AssertionError('receipt not reached')))
  raw=b'ActiveState=active\nMainPID=123\nControlPID=0\nJob=\nControlGroup=\n'
  with patch.object(pair.os.path,'lexists',return_value=False),patch.object(pair,'retained_run',return_value=raw):self.assertRaises(n.Refused,pair.local,'db',reader)
 def test_same_repository_any_and_full_and_original_full_receipt_required(self):
  tree=ast.parse((D/'test-public-role8853/readiness_bridge.py').read_bytes());node=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='verify_repository_binding');module=ast.Module(body=[node],type_ignores=[]);ast.fix_missing_locations(module);ns={'need':n.need};exec(compile(module,'exact-public-repository-binding','exec'),ns)
  repo={'latest_backup_label':pair.LABEL,'latest_full_backup_label':pair.LABEL,'checked_at':'SYNTHETIC'};health={'latest_accepted_full_label':pair.LABEL,'original_snapshot':{'latest_backup_label':pair.LABEL},'latest_receipts':{'full_backup':{'backup_label':pair.LABEL,'receipt_sha256':pair.FULL}}};fn=ns['verify_repository_binding'];fn(repo,health,pair.LABEL,pair.FULL)
  for key in ('latest_backup_label','latest_full_backup_label'):
   bad=dict(repo);bad[key]='SYNTHETIC_WRONG';self.assertRaises(n.Refused,fn,bad,health,pair.LABEL,pair.FULL)
  health['latest_receipts']['full_backup']['receipt_sha256']='f'*64;self.assertRaises(n.Refused,fn,repo,health,pair.LABEL,pair.FULL)
