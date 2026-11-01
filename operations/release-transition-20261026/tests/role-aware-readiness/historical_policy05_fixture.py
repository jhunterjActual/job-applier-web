"""Synthetic in-memory history; real Policy/terminal493 functions, no host."""
import copy,importlib.util,json,sys,types,unittest
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import collector as i
P=Path(__file__).parent;NOW=datetime(2026,10,28,tzinfo=timezone.utc);BOOT='11111111-1111-1111-1111-111111111111';TX='a'*32

def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
common=module('actual_public_common493',P/'public-fixtures/common.py');collector=types.ModuleType('collector');collector.Refused=i.Refused
with patch.dict(sys.modules,{'collector':collector}):terminal=module('actual_terminal493',P/'public-fixtures/terminal493.py')
def fixture(role='web',revision='493bf14a3bae2cb2b36f3a57191602b72ea6e3d3'):
 data=json.loads((P/'public-fixtures/policy-example.json').read_bytes());data.update(application_release='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4',application_build='20261017.96',policy_label='synthetic-current',manifest_revision=revision);current=common.Policy(data);olddata=dict(data,policy_label='synthetic-old');old=common.Policy(olddata);root=PurePosixPath('/var/lib/careertrellis/paired-maintenance');transition=root/role/'policy-transitions'/current.digest;run=root/role/'runs'/TX;values={};directories={str(root/role/'policy-transitions'),str(transition),str(root/role/'runs'),str(run)}
 status={'schema':'careertrellis.paired-maintenance-status/v1','transaction':TX,'policy_sha256':old.digest,'boot_id':BOOT,'release':old.data['application_release'],'phase':'complete','result':'ok','observed_at':(NOW-timedelta(hours=1)).isoformat(),'services_restored':True}
 intent={'schema':'careertrellis.paired-policy-transition/v1','role':role,'old_policy_sha256':old.digest,'new_policy_sha256':current.digest,'boot_id':BOOT,'release':old.data['application_release'],'installed_revision':revision,'prepared_at':NOW.isoformat(),'result':'prepared'}
 def put(path,value):values[str(path)]=copy.deepcopy(value)
 put('/etc/careertrellis/paired-maintenance/policy.json',data);put(root/'public-policy.json',data);put(root/role/'status.json',dict(status,transaction=None,policy_sha256=current.digest,phase='idle',result='ok'))
 put(transition/'intent.json',intent);put(transition/'success.json',dict(intent,result='applied'));put(transition/'old-policy.json',olddata);put(transition/'old-status.json',status)
 worker={'state':'complete','transaction':TX,'policy_sha256':old.digest,'release':old.data['application_release'],'boot_id':BOOT,'observed_at':status['observed_at'],'worker_invocation':'c'*32,'services_restored':True};put(run/'status.json',worker)
 put(run/('complete.json' if role=='web' else 'complete-result.json'),{'transaction':TX,'policy_sha256':old.digest,'services_restored':True});put(run/'worker-started.json',{'boot_id':BOOT,'worker_invocation':'c'*32})
 class F(PurePosixPath):
  def iterdir(self):
   prefix=str(self)+'/'
   return iter(F(name) for name in sorted(set(values)|directories) if name.startswith(prefix) and '/' not in name[len(prefix):])
 class Reader:
  def __init__(self):self.role=role;self.checks=[]
  def path(self,path):return F(str(path))
  def directory(self,path,mode):i.need(str(path) in directories and mode==0o700)
  def json(self,path,mode=0o600):
   if str(path) not in values:raise FileNotFoundError()
   return copy.deepcopy(values[str(path)])
  def read(self,path,mode=0o600):return common.canonical(self.json(path,mode))
  def stable(self):[fn() for fn in self.checks]
 return Reader(),values,directories,F,transition,old,current
