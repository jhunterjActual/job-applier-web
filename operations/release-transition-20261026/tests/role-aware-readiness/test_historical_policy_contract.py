import unittest,types,copy,hashlib
from unittest.mock import patch
import portable_bootstrap
import collector as c
from historical_policy05_fixture import fixture,common,terminal,NOW,BOOT,TX
from pathlib import PurePosixPath,Path
class Tests(unittest.TestCase):
 def setUp(self):
  import sys
  self.saved_collector=dict(vars(c));self.saved_modules=dict(sys.modules)
 def tearDown(self):
  import sys
  for key in tuple(vars(c)):
   if key not in self.saved_collector:delattr(c,key)
  vars(c).update(self.saved_collector)
  for key in tuple(sys.modules):
   if key not in self.saved_modules:del sys.modules[key]
  sys.modules.update(self.saved_modules)

 def prepared(self):
  c.INSTALL=PurePosixPath('/usr/local/libexec/careertrellis/paired_maintenance');reader,values,dirs,F,path,old,current=fixture();run=F('/var/lib/careertrellis/paired-maintenance/web/runs/'+TX);prior=values[str(run/'status.json')];prior['result']='ok';complete=values[str(run/'complete.json')];complete.update(state='complete',backup_label='20261028-000000F',manifest_sha256='f'*64)
  original=reader.read
  reader.read=lambda path,mode=0o600: (__import__('pathlib').Path(__file__).parent/'source-catalog493'/PurePosixPath(str(path)).name).read_bytes() if str(path).startswith('/usr/local/libexec/') else original(path,mode)
  F.lstat=lambda self:types.SimpleNamespace(st_mtime_ns=1 if self.name=='complete.json' else 2)
  return reader,values,run,old,prior
 def test_complete_and_tamper(self):
  reader,values,run,old,prior=self.prepared();proof=c.derived_complete_input(reader,terminal.terminal,old,prior,run,NOW,c.REVISION);self.assertEqual(proof['interpretation'],'DERIVED_INPUT')
  for key,value in [('result','failed'),('services_restored',1),('worker_invocation','x'),('boot_id','bad'),('transaction','f'*32)]:
   bad=dict(prior,**{key:value})
   with self.assertRaises(c.Refused):c.derived_complete_input(reader,terminal.terminal,old,bad,run,NOW,c.REVISION)
 def test_complete_closed_and_source_tamper(self):
  reader,values,run,old,prior=self.prepared();values[str(run/'complete.json')]['extra']=True
  with self.assertRaises(c.Refused):c.derived_complete_input(reader,terminal.terminal,old,prior,run,NOW,c.REVISION)
  reader,values,run,old,prior=self.prepared();reader.read=lambda *args,**kwargs:b'changed'
  with self.assertRaises(c.Refused):c.derived_complete_input(reader,terminal.terminal,old,prior,run,NOW,c.REVISION)
 def test_order_and_stability(self):
  reader,values,run,old,prior=self.prepared();type(run).lstat=lambda self:types.SimpleNamespace(st_mtime_ns=4 if self.name=='complete.json' else 2)
  with self.assertRaises(c.Refused):c.derived_complete_input(reader,terminal.terminal,old,prior,run,NOW,c.REVISION)
  reader,values,run,old,prior=self.prepared();c.derived_complete_input(reader,terminal.terminal,old,prior,run,NOW,c.REVISION);type(run).lstat=lambda self:types.SimpleNamespace(st_mtime_ns=9)
  with self.assertRaises(c.Refused):reader.stable()
 def test_common_truthful_health_unchanged(self):
  raw=b'FILE=__file__';self.assertEqual(c.module('_one_shot_common493',raw).FILE,str(c.INSTALL/'common.py'));self.assertEqual(c.module('_one_shot_health_web',raw).FILE,str(c.CORE))
 def test_actual_full_history_original_preferred(self):
  import sys
  reader,values,dirs,F,path,old,current=fixture();reader.role='web';reader.boot=BOOT;c.ROOT=PurePosixPath('/var/lib/careertrellis/paired-maintenance');c.INSTALL=PurePosixPath('/usr/local/libexec/careertrellis/paired_maintenance')
  values[str(c.ROOT/'web/runs'/TX/'status.json')]['result']='ok';values[str(c.ROOT/'web/runs'/TX/'complete.json')].update(state='complete',backup_label='20261028-000000F',manifest_sha256='f'*64);F.lstat=lambda self:types.SimpleNamespace(st_mtime_ns=1 if self.name=='complete.json' else 2)
  original=reader.read;reader.read=lambda path,mode=0o600: (__import__('pathlib').Path(__file__).parent/'source-catalog493'/PurePosixPath(str(path)).name).read_bytes() if str(path).startswith(str(c.INSTALL)+'/') else original(path,mode)
  pins={'files':{'common.py':c.SOURCE_CATALOG493['common.py']}}
  with patch.dict(sys.modules,{'terminal493':terminal}),patch.object(c,'public_document',lambda name:pins),patch.object(c.os.path,'lexists',lambda path:str(path)in values or str(path)in dirs),patch.object(c,'derived_complete_input',side_effect=AssertionError('fallback called')):
   c.historical_terminal(reader,current,values[str(c.ROOT/'web/status.json')],NOW)
 def test_db_no_fallback(self):
  import sys
  reader,values,dirs,F,path,old,current=fixture('db');reader.role='db';reader.boot=BOOT;c.ROOT=PurePosixPath('/var/lib/careertrellis/paired-maintenance');c.INSTALL=PurePosixPath('/usr/local/libexec/careertrellis/paired_maintenance')
  original=reader.read;reader.read=lambda path,mode=0o600: (__import__('pathlib').Path(__file__).parent/'source-catalog493'/PurePosixPath(str(path)).name).read_bytes() if str(path).startswith(str(c.INSTALL)+'/') else original(path,mode)
  # Remove archived global transaction association while preserving valid idle original snapshot.
  values[str(path/'old-status.json')].update(transaction=None,phase='idle')
  with patch.dict(sys.modules,{'terminal493':terminal}),patch.object(c,'public_document',lambda name:{'files':{'common.py':c.SOURCE_CATALOG493['common.py']}}),patch.object(c.os.path,'lexists',lambda path:str(path)in values or str(path)in dirs):
   with self.assertRaises(c.Refused):c.historical_terminal(reader,current,values[str(c.ROOT/'db/status.json')],NOW)
 def full_history(self,change=None,original_global=False,revision=c.REVISION):
  import sys
  reader,values,dirs,F,path,old,current=fixture(revision=revision);reader.role='web';reader.boot=BOOT;c.ROOT=PurePosixPath('/var/lib/careertrellis/paired-maintenance');c.INSTALL=PurePosixPath('/usr/local/libexec/careertrellis/paired_maintenance');run=F(str(c.ROOT/'web/runs'/TX))
  values[str(run/'status.json')]['result']='ok';values[str(run/'complete.json')].update(state='complete',backup_label='20261028-000000F',manifest_sha256='f'*64)
  if not original_global:values[str(path/'old-status.json')].update(transaction=None,phase='idle')
  original=reader.read
  reader.read=lambda path,mode=0o600:(__import__('pathlib').Path(__file__).parent/'source-catalog493'/PurePosixPath(str(path)).name).read_bytes() if str(path).startswith(str(c.INSTALL)+'/') else original(path,mode)
  F.lstat=lambda self:types.SimpleNamespace(st_mtime_ns=1 if self.name=='complete.json' else 2)
  if change:change(reader,values,run,path,F)
  with patch.dict(sys.modules,{'terminal493':terminal}),patch.object(c,'public_document',lambda name:{'files':{'common.py':c.SOURCE_CATALOG493['common.py']}}),patch.object(c.os.path,'lexists',lambda path:str(path)in values or str(path)in dirs):
   c.historical_terminal(reader,current,values[str(c.ROOT/'web/status.json')],NOW);reader.stable()
 def test_full_fallback_positive(self):self.full_history()
 def test_full_preferred_private_bypass_matrix(self):
  for key,value in [('state','failed'),('result','failed'),('services_restored',False),('observed_at','2099-01-01T00:00:00+00:00')]:
   with self.subTest(key=key),self.assertRaises(c.Refused):self.full_history(lambda r,v,run,*_:v[str(run/'status.json')].update({key:value}),True)
 def test_full_fallback_private_complete_source_time_negatives(self):
  for kind in ('private_extra','private_failed','complete_extra','complete_digest','source','order','canonical','body_digest','stable'):
   def change(reader,values,run,path,F):
    if kind=='private_extra':values[str(run/'status.json')]['extra']=True
    elif kind=='private_failed':values[str(run/'status.json')]['result']='failed'
    elif kind=='complete_extra':values[str(run/'complete.json')]['extra']=True
    elif kind=='complete_digest':values[str(run/'complete.json')]['policy_sha256']='e'*64
    elif kind=='source':
     oldread=reader.read;reader.read=lambda p,mode=0o600:b'changed' if PurePosixPath(str(p)).name=='runtime.py' else oldread(p,mode)
    elif kind=='order':F.lstat=lambda self:types.SimpleNamespace(st_mtime_ns=3 if self.name=='complete.json' else 2)
    elif kind=='canonical':
     oldread=reader.read;reader.read=lambda p,mode=0o600:oldread(p,mode)+(b' ' if str(p)==str(path/'old-policy.json') else b'')
    elif kind=='body_digest':values[str(path/'old-policy.json')]['policy_label']='synthetic-changed'
    elif kind=='stable':reader.checks.append(lambda:c.need(False))
   with self.subTest(kind=kind),self.assertRaises(c.Refused):self.full_history(change)
 def database_source_fixture(self,change=None):
  import ast,sys
  reader,values,dirs,F,path,old,current=fixture('db');reader.role='db';reader.boot=BOOT;c.ROOT=PurePosixPath('/var/lib/careertrellis/paired-maintenance');c.INSTALL=PurePosixPath('/usr/local/libexec/careertrellis/paired_maintenance');run=F(str(c.ROOT/'db/runs'/TX))
  raw=(__import__('pathlib').Path(__file__).parent/'source-catalog493/db_host.py').read_bytes();tree=ast.parse(raw);cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='DBBackend');methods=[x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name in ('complete','_write_status','_global')]
  class Store:
   def exists(self,name):return str(run/name)in values
   def read(self,name):return copy.deepcopy(values[str(run/name)])
   def replace(self,name,value):values[str(run/name)]=copy.deepcopy(value)
   def write_once(self,name,value):self.replace(name,value)
  class Global:
   def __init__(self,root):pass
   def replace(self,name,value):values[str(path/'old-status.json')]=copy.deepcopy(value)
  ns={'need':c.need,'Journal':Global,'ROLE_ROOT':'synthetic'};exec(compile(ast.Module(body=methods,type_ignores=[]),'authenticated-db493','exec'),ns)
  backend=types.SimpleNamespace(store=Store(),transaction=TX,policy=old,r=types.SimpleNamespace(boot=BOOT),now=lambda:NOW,mirror_status=lambda tx:{'state':'mirror_complete'},health_status=lambda tx:{'state':'health_complete'})
  for name in ('complete','_write_status','_global'):setattr(backend,name,types.MethodType(ns[name],backend))
  values[str(run/'status.json')].update(state='released');payload={'backup_label':'20261028-000000F','publication_manifest_sha256':'f'*64};values[str(run/'mirror-intent.json')]={'payload':payload};backend.complete(TX,payload)
  self.assertEqual(set(values[str(run/'status.json')]),{'state','transaction','policy_sha256','release','boot_id','worker_invocation','services_restored'})
  original=reader.read;reader.read=lambda p,mode=0o600:(__import__('pathlib').Path(__file__).parent/'source-catalog493/common.py').read_bytes() if str(p)==str(c.INSTALL/'common.py') else original(p,mode)
  if change:change(values,run,path)
  with patch.dict(sys.modules,{'terminal493':terminal}),patch.object(c,'public_document',lambda name:{'files':{'common.py':c.SOURCE_CATALOG493['common.py']}}),patch.object(c.os.path,'lexists',lambda p:str(p)in values or str(p)in dirs):
   c.historical_terminal(reader,current,values[str(c.ROOT/'db/status.json')],NOW);reader.stable()
 def test_actual_source_generated_db_full_positive(self):self.database_source_fixture()
 def test_db_full_schema_outcome_identity_time_negatives(self):
  for kind in ('extra','state','restoration','invocation','boot','complete_extra','complete_digest','future_global'):
   def change(values,run,path):
    prior=values[str(run/'status.json')]
    if kind=='extra':prior['result']='ok'
    elif kind=='state':prior['state']='failed'
    elif kind=='restoration':prior['services_restored']=False
    elif kind=='invocation':prior['worker_invocation']='d'*32
    elif kind=='boot':prior['boot_id']='bad'
    elif kind=='complete_extra':values[str(run/'complete-result.json')]['extra']=True
    elif kind=='complete_digest':values[str(run/'complete-result.json')]['policy_sha256']='e'*64
    else:values[str(path/'old-status.json')]['observed_at']='2099-01-01T00:00:00+00:00'
   with self.subTest(kind=kind),self.assertRaises(c.Refused):self.database_source_fixture(change)
 def test_other_domain_reverse_times_and_mtimes_valid(self):
  def change(reader,values,run,path,F):
   values[str(run/'status.json')]['observed_at']='2026-10-27T22:00:00+00:00'
   F.lstat=lambda self:types.SimpleNamespace(st_mtime_ns=9 if self.name=='complete.json' else 2)
  self.full_history(change,True,'f'*40)
 def test_other_domain_future_global_or_private_refused(self):
  for which in ('global','private'):
   def change(reader,values,run,path,F):values[str((path/'old-status.json') if which=='global' else (run/'status.json'))]['observed_at']='2099-01-01T00:00:00+00:00'
   with self.subTest(which=which),self.assertRaises(c.Refused):self.full_history(change,True,'f'*40)
 def test_known493_reverse_order_and_source_refused(self):
  for kind in ('time','source'):
   def change(reader,values,run,path,F):
    if kind=='time':values[str(run/'status.json')]['observed_at']='2026-10-27T22:00:00+00:00'
    else:
     original=reader.read;reader.read=lambda p,mode=0o600:b'wrong' if PurePosixPath(str(p)).name=='runtime.py' else original(p,mode)
   with self.subTest(kind=kind),self.assertRaises(c.Refused):self.full_history(change,True)
 def prestart_history(self,change=None):
  import sys
  reader,values,dirs,F,path,old,current=fixture();reader.role='web';reader.boot=BOOT;c.ROOT=PurePosixPath('/var/lib/careertrellis/paired-maintenance');c.INSTALL=PurePosixPath('/usr/local/libexec/careertrellis/paired_maintenance');run=F(str(c.ROOT/'web/runs'/TX))
  prior=values[str(run/'status.json')];prior.update(state='blocked',result='failed',services_restored=True)
  values[str(path/'old-status.json')].update(phase='failed',result='failed',observed_at=NOW.isoformat())
  for name in ('complete.json','worker-started.json'):values.pop(str(run/name))
  values[str(run/'prestart-original-status.json')]=copy.deepcopy(prior)
  record={'schema':'careertrellis.paired-maintenance-prestart-abort/v1','transaction':TX,'policy_sha256':old.digest,'release':old.data['application_release'],'boot_id':BOOT,'slot_claim_name':'20261028.json','original_status_sha256':hashlib.sha256(common.canonical(prior)).hexdigest(),'no_dispatch':True,'services_unchanged':True,'services_restored':True,'restoration_performed':False,'verified_at':NOW.isoformat(),'verifier_invocation':'d'*32}
  values[str(run/'prestart-aborted.json')]=record;values[str(c.ROOT/'web/slots/20261028.json')]={'transaction':TX,'policy_sha256':old.digest}
  original=reader.read;reader.read=lambda p,mode=0o600:(Path(__file__).parent/'source-catalog493'/PurePosixPath(str(p)).name).read_bytes() if str(p).startswith(str(c.INSTALL)+'/') else original(p,mode)
  if change:change(reader,values,run,path)
  with patch.dict(sys.modules,{'terminal493':terminal}),patch.object(c,'public_document',lambda name:{'files':{'common.py':c.SOURCE_CATALOG493['common.py']}}),patch.object(c.os.path,'lexists',lambda p:str(p)in values or str(p)in dirs):
   c.historical_terminal(reader,current,values[str(c.ROOT/'web/status.json')],NOW);reader.stable()
 def test_full_prestart_source_shape_positive(self):self.prestart_history()
 def test_full_prestart_negatives(self):
  for kind in ('private_raw','snapshot_raw','hash','slot','dispatch','restoration','future','unknown','complete_contradiction'):
   def change(reader,values,run,path):
    prior=values[str(run/'status.json')];record=values[str(run/'prestart-aborted.json')]
    if kind=='private_raw':prior['state']='failed'
    elif kind=='snapshot_raw':values[str(run/'prestart-original-status.json')]['result']='blocked'
    elif kind=='hash':record['original_status_sha256']='f'*64
    elif kind=='slot':values[str(c.ROOT/'web/slots/20261028.json')]['transaction']='f'*32
    elif kind=='dispatch':record['no_dispatch']=False
    elif kind=='restoration':record['restoration_performed']=True
    elif kind=='future':prior['observed_at']='2099-01-01T00:00:00+00:00'
    elif kind=='unknown':prior['state']='other'
    else:values[str(path/'old-status.json')].update(phase='complete',result='ok')
   with self.subTest(kind=kind),self.assertRaises(c.Refused):self.prestart_history(change)
if __name__=='__main__':unittest.main()
