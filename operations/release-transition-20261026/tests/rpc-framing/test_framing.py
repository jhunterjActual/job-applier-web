"""Actual transport methods with memory-only IO; no SSH/thread/process signalling."""
import ast,hashlib,io,json,pathlib,types,unittest,sys,time
P=pathlib.Path(__file__).resolve().parents[2];MAX=3*1024**2
def need(v):
 if not v:raise RuntimeError('synthetic-refused')
def dump(v):return json.dumps(v,separators=(',',':'),ensure_ascii=True)+'\n'
def padded(v,limit):
 raw=dump(v);return raw[:-1]+(' '*(limit-len(raw)))+'\n'
def function(path,name):return next(n for n in ast.walk(ast.parse(path.read_bytes()))if isinstance(n,ast.FunctionDef)and n.name==name)
class Held(BaseException):pass
class Tests(unittest.TestCase):
 def proxy(self,response,request_args=None,output=None):
  node=next(n for n in ast.parse((P/'native_backend.py').read_bytes()).body if isinstance(n,ast.ClassDef)and n.name=='DBProxy');out=output if output is not None else io.BytesIO();ns={'need':need,'digest':lambda _:'a'*64,'time':types.SimpleNamespace(monotonic=lambda:0),'json':json,'sys':types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=out),stdin=types.SimpleNamespace(fileno=lambda:7)),'select':types.SimpleNamespace(select=lambda *a:([7],[],[])),'os':types.SimpleNamespace(read=lambda fd,size:response),'decode':json.loads}
  exec(compile(ast.Module(body=[node],type_ignores=[]),'actual-DBProxy-transport-only','exec'),ns);peer=ns['DBProxy']({})
  try:value=peer.call('synthetic',**(request_args or {}));return peer,value,out.getvalue()
  except BaseException:return peer,None,out.getvalue()
 def test_proxy_response_exact_encoded_byte_boundary_overflow_eof_nonascii(self):
  response=padded({'seq':0,'ok':True,'value':'synthetic'},MAX).encode('ascii');peer,value,out=self.proxy(response);self.assertFalse(peer.unknown);self.assertEqual(value,'synthetic')
  for response in (response+b' ',b'',dump({'seq':0,'ok':True,'value':'synthetic'}).encode()+b'\n',json.dumps({'seq':0,'ok':True,'value':'snowman-\u2603'},ensure_ascii=False).encode()+b'\n'):
   with self.subTest(size=len(response)):
    peer,value,out=self.proxy(response);self.assertTrue(peer.unknown);self.assertIsNone(value)
 def test_proxy_outer_exact_limit_and_nested_unicode_escaping(self):
  reply=dump({'seq':0,'ok':True,'value':None}).encode();empty={'kind':'db_rpc','request':{'seq':0,'plan_sha256':'a'*64,'action':'synthetic','args':{'payload':''}}};count=MAX-len(dump(empty));peer,value,out=self.proxy(reply,{'payload':'x'*count});self.assertFalse(peer.unknown);self.assertEqual(len(out),MAX)
  peer,value,out=self.proxy(reply,{'payload':'x'*(count+1)});self.assertTrue(peer.unknown);self.assertEqual(out,b'')
  peer,value,out=self.proxy(reply,{'payload':'\u2603'*100});self.assertFalse(peer.unknown);self.assertTrue(out.isascii());self.assertIn(b'\\u2603',out)
 def server(self,raw,claimed=True):
  fn=function(P/'peer_server.py','_serve');responses=[];limits=[];data=iter([raw,'']);peer=types.SimpleNamespace(claimed=claimed,check_locks=lambda:None,role='db',boot='syntheticboot');ns={'hashlib':hashlib,'json':json,'decode':json.loads,'need':need,'os':types.SimpleNamespace(getpid=lambda:123)};exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-serve-framing-only','exec'),ns)
  def read(size):limits.append(size);return next(data)
  def hold(_):raise Held()
  try:ns['_serve'](peer,{},read,responses.append,hold)
  except Held:pass
  return responses,limits
 def test_server_exact_boundary_overflow_eof_and_no_newline_refuse_hold(self):
  token=hashlib.sha256(b'{}').hexdigest();msg={'seq':0,'plan_sha256':token,'action':'ping','args':{}};raw=padded(msg,MAX);responses,limits=self.server(raw.encode('ascii'),False);self.assertTrue(responses[0]['ok']);self.assertEqual(limits[0],MAX+1)
  for raw in ((raw+' ').encode('ascii'),raw[:-1].encode('ascii'),b'',(dump(msg)+'\n').encode('ascii'),dump(msg).replace('ping','p\u2603ng').encode('utf-8')):
   with self.subTest(size=len(raw)):
    responses,limits=self.server(raw)
    if raw:self.assertFalse(responses[0]['ok'])
 def test_peer_writer_exact_boundary_overflow_and_unicode(self):
  fn=function(P/'peer_server.py','write_response');out=io.BytesIO();ns={'json':json,'need':need,'sys':types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=out))};exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-peer-writer-only','exec'),ns)
  empty={'value':''};count=MAX-len(dump(empty));ns['write_response']({'value':'x'*count});self.assertEqual(len(out.getvalue()),MAX)
  out.seek(0);out.truncate()
  with self.assertRaises(RuntimeError):ns['write_response']({'value':'x'*(count+1)})
  self.assertEqual(out.getvalue(),b'');ns['write_response']({'value':'\u2603'});self.assertTrue(out.getvalue().isascii())
 def test_operator_reader_max_plus_one_eof_and_no_real_thread(self):
  tree=ast.parse((P/'operator/drive.py').read_bytes());node=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='Reader');sizes=[]
  class Stream(io.BytesIO):
   def readline(self,size=-1):sizes.append(size);return super().readline(size)
  class Thread:
   def __init__(self,target,daemon):self.target=target
   def start(self):self.target()
  import queue
  ns={'queue':queue,'threading':types.SimpleNamespace(Thread=Thread)};exec(compile(ast.Module(body=[node],type_ignores=[]),'actual-reader-memory-only','exec'),ns);reader=ns['Reader'](Stream(b'x\n'));self.assertEqual(reader.read(),b'x\n');self.assertEqual(reader.read(),b'');self.assertEqual(sizes,[MAX+1,MAX+1])
 def test_component_caps_use_actual_ascii_lf_not_char_count(self):
  import importlib.util
  def module(name,file):
   sp=importlib.util.spec_from_file_location(name,P/file);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m
  c=module('framing_collector','one_shot_readiness/collector.py');r=module('framing_bridge','readiness_bridge.py');value={'unicode':'\u2603'*45000};self.assertGreater(len(c.canonical(value)),262144);self.assertLess(len(json.dumps(value,ensure_ascii=False)),262144)
  with self.assertRaises(RuntimeError):r.verify_raw_context(value,{},'db',c,'a'*64,now=None)
  value={'payload':'x'*(2*1024**2)}
  with self.assertRaises(RuntimeError):r.verify_qualified_context(value,{},c,'a'*64,now=None)
 def drive_flow(self,web_frame=None,db_response=None,write_mode=None):
  fn=function(P/'operator/drive.py','drive');token='a'*64;request={'seq':0,'plan_sha256':token,'action':'qualify_backup_proofs','args':{'binding':{},'web_health':{'synthetic':'\u2603'*100}}};frame=dump({'kind':'db_rpc','request':request}).encode('ascii');response=padded({'seq':0,'ok':True,'value':{'health':'synthetic'}},MAX).encode('ascii');terminal=dump({'kind':'terminal','receipt':{'result':'target_healthy','release':'bc68ea0dc253425a44e268452130becdf35d764e','build':'20261026.97'}}).encode('ascii');streams={'web':iter([frame if web_frame is None else web_frame,terminal]),'db':iter([response if db_response is None else db_response])};sent={'web':[],'db':[]};starts=[];log=[]
  class Input:
   def __init__(self,role):self.role=role;self.calls=0
   def write(self,value):
    self.calls+=1
    if write_mode=='zero':return 0
    if write_mode=='error' and self.calls==2:raise OSError('synthetic-private-body')
    count=min(len(value),65536) if write_mode else len(value)
    sent[self.role].append(value[:count]);return count
   def flush(self):pass
   def close(self):pass
  class Process:
   def __init__(self,role):self.role=role;self.stdout=role;self.stdin=Input(role);self.pid=1
   def wait(self,timeout):return 0
   def kill(self):raise AssertionError('NO_KILL')
   def terminate(self):raise AssertionError('NO_SIGNAL')
  class Log:
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def write(self,value):log.append(value)
   def flush(self):pass
  receipt=types.SimpleNamespace(exists=lambda:False,open=lambda *a,**k:Log())
  def start(role,*a):starts.append(role);return Process(role)
  from datetime import datetime,timezone
  import subprocess
  ns={'need':need,'datetime':datetime,'timezone':timezone,'json':json,'decode':json.loads,'start':start,'Reader':lambda stream:types.SimpleNamespace(read=lambda:next(streams[stream])),'TARGET':'bc68ea0dc253425a44e268452130becdf35d764e','subprocess':subprocess};exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-drive-memory-only','exec'),ns);code=ns['drive']('c'*40,token,receipt);return code,sent,log,starts
 def test_actual_driver_forwarding_exact_response_byte_cap_and_privacy(self):
  code,sent,log,starts=self.drive_flow();self.assertEqual(code,0);self.assertEqual(starts,['web','db']);self.assertEqual(len(sent['web'][0]),MAX);self.assertTrue(sent['db'][0].isascii());self.assertNotIn('synthetic',''.join(log))
 def test_actual_driver_overflow_eof_unicode_refuse_without_kill(self):
  response=padded({'seq':0,'ok':True,'value':None},MAX).encode('ascii')
  for raw in (response+b' ',response[:-1],b'',(json.dumps({'seq':0,'ok':True,'value':'\u2603'},ensure_ascii=False)+'\n').encode('utf-8'),b'{}\r\n'):
   code,sent,log,starts=self.drive_flow(db_response=raw);self.assertEqual(code,1);self.assertEqual(sent['web'],[])
  code,sent,log,starts=self.drive_flow(web_frame=b'x'*(MAX+1));self.assertEqual(code,1);self.assertEqual(starts,['web']);self.assertEqual(sent['db'],[])
 def test_actual_binary_os_pipe_preserves_exact_bytes_no_normalization(self):
  import subprocess
  payloads=(padded({'synthetic':'boundary'},MAX).encode('ascii'),padded({'synthetic':'overflow'},MAX+1).encode('ascii'),b'{}\r\n',b'{}\n\n',b'non-ascii-\xc3\xa9\n')
  code='import sys; value=sys.stdin.buffer.read(); sys.stdout.buffer.write(value); sys.stdout.buffer.flush()'
  for payload in payloads:
   process=subprocess.Popen([sys.executable,'-I','-S','-B','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
   output,error=process.communicate(payload)
   self.assertEqual(process.returncode,0);self.assertEqual(len(error),0);self.assertEqual(output,payload)
 def test_actual_peer_writer_short_zero_and_midframe_exception(self):
  fn=function(P/'peer_server.py','write_response')
  class Output:
   def __init__(self,mode):self.mode=mode;self.raw=b'';self.calls=0;self.flushed=False
   def write(self,raw):
    self.calls+=1
    if self.mode=='zero':return 0
    if self.mode=='error' and self.calls==2:raise OSError('synthetic-private-body')
    count=min(7,len(raw));self.raw+=raw[:count];return count
   def flush(self):self.flushed=True
  for mode in ('short','zero','error'):
   out=Output(mode);ns={'json':json,'need':need,'sys':types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=out))};exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-peer-writer-short-only','exec'),ns)
   if mode=='short':ns['write_response']({'synthetic':'x'*99});self.assertEqual(out.raw,dump({'synthetic':'x'*99}).encode('ascii'));self.assertTrue(out.flushed)
   else:
    with self.assertRaises((RuntimeError,OSError)):ns['write_response']({'synthetic':'x'*99})
    self.assertFalse(out.flushed)
 def test_proxy_and_driver_short_zero_midframe_no_replay(self):
  class Output(io.BytesIO):
   def __init__(self,mode):super().__init__();self.mode=mode;self.calls=0
   def write(self,raw):
    self.calls+=1
    if self.mode=='zero':return 0
    if self.mode=='error' and self.calls==2:raise OSError('private-synthetic')
    return super().write(raw[:7])
  for mode in ('short','zero','error'):
   out=Output(mode);peer,value,raw=self.proxy(dump({'seq':0,'ok':True,'value':1}).encode(),output=out)
   self.assertEqual(peer.unknown,mode!='short')
   code,sent,log,starts=self.drive_flow(write_mode=mode)
   self.assertEqual(code,0 if mode=='short' else 1);self.assertEqual(starts,['web','db']);self.assertNotIn('private',''.join(log))
   if mode=='short':self.assertEqual(len(b''.join(sent['web'])),MAX)
if __name__=='__main__':unittest.main()
