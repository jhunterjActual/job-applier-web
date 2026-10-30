"""Public custody and failure observer contracts; synthetic selectors only."""
import unittest,types,tempfile,pathlib,json,copy,io
from datetime import datetime,timezone
from unittest.mock import patch
import private_channel as c
import preplan_program as p
import native_io as n
class Tests(unittest.TestCase):
 def fixture(self):
  sha='a'*64;profile=copy.deepcopy(p.BASE_PROFILE);actor={'pid':123,'parent_pid':2,'birth':'456','state':'S'};boots={'web':'11111111-1111-1111-1111-111111111111','db':'22222222-2222-2222-2222-222222222222'};now=datetime.now(timezone.utc).isoformat()
  owner={'kind':'owner','role':'web','actor':actor,'boot':boots['web'],'program_sha256':sha}
  hello={'schema':'careertrellis.preplan-private-peer/v1','role':'db','boot_id':boots['db'],'actor':dict(actor,pid=124),'program_sha256':sha,'source_commit':profile['source_commit'],'packet_sha256':profile['packet_sha256'],'readiness_manifest_sha256':profile['readiness_manifest_sha256'],'scan_started_at':now,'scan_finished_at':now}
  contract={'schema':'careertrellis.app-plan-source-contract/v1','mode':'actual','source_commit':profile['source_commit'],'packet_sha256':profile['packet_sha256'],'readiness_manifest_sha256':profile['readiness_manifest_sha256'],'collector_source_sha256':sha}
  request={'nonce':'b'*64,'issued_at':now,'boots':boots,'context_sha256':n.sha(n.canonical(contract))};return owner,hello,request,sha,profile
 def test_closed_metadata_only_and_replay_refusal(self):
  value=c.custody_value(*self.fixture());self.assertNotIn('policy',json.dumps(value));self.assertNotIn('raw_health',json.dumps(value));self.assertNotIn('url',json.dumps(value))
  with tempfile.TemporaryDirectory(prefix='custody-synthetic-') as root:
   path=str(pathlib.Path(root).resolve()/'preplan-observable-custody.json');pin=c.create_custody_receipt(path,value);self.assertEqual(pin,n.sha(pathlib.Path(path).read_bytes()));self.assertRaises(FileExistsError,c.create_custody_receipt,path,value)
 def test_bad_owner_boot_birth_pid_and_missing_dbhello_refuse(self):
  for role,field,value in [('owner','boot',True),('owner','actor',{'pid':True,'parent_pid':2,'birth':'456','state':'S'}),('owner','actor',{'pid':123,'parent_pid':2,'birth':'wrong','state':'S'}),('hello','boot_id','wrong'),('hello','actor',{'pid':124,'parent_pid':2,'birth':'456','state':'Z'})]:
   owner,hello,request,sha,profile=self.fixture();(owner if role=='owner' else hello)[field]=value;self.assertRaises(n.Refused,c.custody_value,owner,hello,request,sha,profile)
  owner,_,request,sha,profile=self.fixture();self.assertRaises(n.Refused,c.custody_value,owner,{},request,sha,profile)
 def test_nonce_context_source_and_future_time_refuse(self):
  for field,value in [('nonce',True),('context_sha256','f'*64),('issued_at','2999-01-01T00:00:00+00:00'),('issued_at',True)]:
   owner,hello,request,sha,profile=self.fixture();request[field]=value;self.assertRaises(n.Refused,c.custody_value,owner,hello,request,sha,profile)
 def test_file_fsync_failure_retains_partial_no_adoption(self):
  value=c.custody_value(*self.fixture())
  with tempfile.TemporaryDirectory(prefix='custody-synthetic-') as root:
   path=str(pathlib.Path(root).resolve()/'preplan-observable-custody.json')
   with patch.object(c.os,'fsync',side_effect=OSError('SYNTHETIC_PRIVATE')):self.assertRaises(OSError,c.create_custody_receipt,path,value)
   self.assertTrue(pathlib.Path(path).exists());self.assertRaises(FileExistsError,c.create_custody_receipt,path,value)
 def test_failure_metadata_never_exports_message_locals_or_path(self):
  n.set_public_phase('HISTORY')
  try:raise OSError('SYNTHETIC_PRIVATE /secret/config provider endpoint')
  except OSError as error:value=n.public_failure(error)
  self.assertEqual(value['phase'],'HISTORY');self.assertEqual(value['category'],'IO');self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(value));self.assertNotIn('/secret',json.dumps(value));self.assertEqual(n.validate_public_failure(value),value)
 def test_unknown_phase_origin_extra_keys_and_bool_line_refuse(self):
  good={'schema':'careertrellis.preplan-public-failure/v1','phase':'HISTORY','origin':'PUBLIC_PROGRAM','line':0,'category':'REFUSED'}
  for field,value in [('phase','PRIVATE'),('origin','/secret/config'),('line',True),('category','SYNTHETIC_PRIVATE'),('extra','SYNTHETIC_PRIVATE')]:
   bad=dict(good);bad[field]=value;self.assertRaises(n.Refused,n.validate_public_failure,bad)
