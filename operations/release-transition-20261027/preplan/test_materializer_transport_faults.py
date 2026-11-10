"""Actual entry/relay methods; synthetic native IO seams only, no host or private records."""
import unittest,io,stat,types,contextlib,json
from unittest.mock import patch
from contextlib import ExitStack
import native_io as n
import allocation_entry as e
import preplan_program as p
import private_channel as c
import assemble_plan as a
class Held(Exception):pass
class KeepBytes(io.BytesIO):
 def close(self):self.closed_by_owner=True
class Child:
 def __init__(self,raw):self.stdout=KeepBytes(raw);self.stdin=KeepBytes();self.pid=999;self.waits=0
 def wait(self):self.waits+=1;return 0
 def poll(self):return 0
class Tests(unittest.TestCase):
 def materialize(self,fail,existing=False):
  session=object.__new__(p.PreparedFacts);session.facts={'nonce':'a'*64,'SYNTHETIC_PRIVATE':'not-loggable'};contract={'collector_source_sha256':'b'*64};calls=[];st=types.SimpleNamespace(st_dev=1,st_ino=2,st_mode=stat.S_IFDIR|0o700,st_uid=0,st_gid=0,st_nlink=2,st_size=0,st_mtime_ns=0,st_ctime_ns=0)
  def create(path,raw):
   calls.append(str(path))
   if fail=='intent_fsync' and str(path).endswith('-intent.json'):raise OSError('synthetic-fsync')
  with ExitStack() as stack:
   for obj,name,value in [(p.PreparedFacts,'authenticate',lambda *_:None),(p.PreparedFacts,'private_proof',lambda _:b'{"synthetic_private_proof":true}'),(e.os,'statvfs',lambda _:types.SimpleNamespace(f_bavail=6*1024**3,f_frsize=1,f_favail=50000)),(e.os,'geteuid',lambda:0),(e.os,'O_DIRECTORY',0x10000),(e.os,'O_NOFOLLOW',0x20000),(e.os,'O_NONBLOCK',0x800),(e,'parents',lambda *_:None),(e.os.path,'lexists',lambda x:existing),(e,'boot',lambda:'synthetic-boot'),(e,'process_identity',lambda _:dict(pid=123,parent_pid=2,birth='456',state='S')),(e,'deferred_child_signals',contextlib.nullcontext),(e,'create',create),(e,'hold',lambda:(_ for _ in ()).throw(Held())),(e.os,'mkdir',lambda *x,**kw:calls.append('mkdir')),(e.os,'open',lambda *x,**kw:55),(e.os,'close',lambda *_:None),(e.os,'fstat',lambda _:st),(e.assembler,'bind_native_session',lambda *_:None),(e.assembler,'_NATIVE_PARENT',None)]:stack.enter_context(patch.object(obj,name,value,create=True))
   def fsync(_):
    calls.append('fsync')
    if fail=='directory_fsync':raise OSError('synthetic-fsync')
   stack.enter_context(patch.object(e.os,'fsync',fsync))
   def allocate(*_):calls.append('allocation_claim_reached');raise OSError('synthetic-claim-fsync')
   stack.enter_context(patch.object(e.assembler,'allocate_actual',allocate))
   try:e.allocate_bound(session,contract)
   except (Held,n.Refused):pass
   else:raise AssertionError('expected fault not reached')
  return calls
 def test_materializer_intent_fsync_unknown_retains_before_mkdir(self):
  calls=self.materialize('intent_fsync');self.assertEqual(len(calls),1);self.assertTrue(calls[0].endswith('-intent.json'))
 def test_materializer_directory_fsync_unknown_retains(self):
  calls=self.materialize('directory_fsync');self.assertIn('mkdir',calls);self.assertIn('fsync',calls);self.assertNotIn('allocation_claim_reached',calls)
 def test_materializer_claim_failure_reached_after_private_files(self):
  calls=self.materialize('claim_fsync');self.assertIn('allocation_claim_reached',calls);self.assertTrue(any(x.endswith('private-facts.json') for x in calls));self.assertFalse(any(x.endswith('-terminal.json') for x in calls))
 def test_materializer_existing_root_refuses_no_mutation(self):self.assertEqual(self.materialize('none',True),[])
 def public(self):return {'result':'PREPLAN_INSPECTED_NOT_ALLOCATED','source_commit':p.OPS,'packet_sha256':p.PACKET,'program_sha256':'a'*64,'nonce':'b'*64,'facts_sha256':'c'*64,'runtime_file_count':1,'historical_receipts':4,'scan_started_at':'2000-01-01T00:00:00+00:00','scan_finished_at':'2000-01-01T00:00:01+00:00'}
 def run_relay(self,webraw,dbraw=b''):
  web,db=Child(webraw),Child(dbraw);children=iter([web,db])
  with patch.object(c,'deferred_child_signals',contextlib.nullcontext),patch.object(c.subprocess,'Popen',side_effect=lambda *a,**k:next(children)):
   result=c.relay('a'*64,'inspect',p.BASE_PROFILE)
  return result,web,db
 def frame(self,v):return n.canonical(v)+b'\n'
 def test_actual_relay_missing_owner_hello_refuses_no_private_public(self):
  frames=self.frame({'kind':'db_rpc','request':{'seq':0,'program_sha256':'a'*64,'action':'hello','args':{}}})
  replies=self.frame({'seq':0,'ok':True,'value':{'SYNTHETIC_PRIVATE':'not-loggable'}});out=io.StringIO()
  with contextlib.redirect_stdout(out):self.assertRaises(RuntimeError,self.run_relay,frames,replies)
  self.assertEqual(out.getvalue(),'')
 def test_actual_relay_eof_partial_oversize_outofsequence(self):
  for raw in [b'',b'{"PRIVATE":',b'x'*65537,self.frame({'kind':'db_rpc','request':{'seq':7,'program_sha256':'a'*64,'action':'hello','args':{}}})]:
   with self.subTest(raw_length=len(raw)):self.assertRaises(RuntimeError,self.run_relay,raw)
 def test_public_result_nested_private_value_refuses(self):
  value=self.public();value['runtime_file_count']={'PRIVATE':'not-loggable'};self.assertRaises(n.Refused,c.public_result,value,'inspect','a'*64,p.BASE_PROFILE)
 def test_public_result_wrong_source_refuses(self):
  value=self.public();value['source_commit']='f'*40;self.assertRaises(n.Refused,c.public_result,value,'inspect','a'*64,p.BASE_PROFILE)
