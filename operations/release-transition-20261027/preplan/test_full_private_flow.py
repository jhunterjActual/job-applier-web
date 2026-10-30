"""Actual web/DBProxy/database/relay flow; native IO and core validation are explicit synthetic seams."""
import io,json,queue,threading,types,unittest,copy,tempfile,pathlib
from unittest.mock import patch
import private_channel as channel
import session_server as server
import preplan_program as program
import native_idle as idle
import native_io as native

class Pipe:
 def __init__(self):self.q=queue.Queue(maxsize=4);self.closed=False;self.closes=0
 def write(self,raw):
  if self.closed:raise OSError('synthetic closed pipe')
  self.q.put(bytes(raw),timeout=2);return len(raw)
 def flush(self):pass
 def readline(self,maximum):
  raw=self.q.get(timeout=2)
  return raw[:maximum]
 def read(self,maximum):return self.readline(maximum)
 def close(self):
  self.closes+=1
  if not self.closed:self.closed=True;self.q.put(b'',timeout=2)

class Child:
 def __init__(self,role,run):
  self.stdin=Pipe();self.stdout=Pipe();self.code=None;self.role=role
  def execute():
   try:self.code=run(role,self.stdout,self.stdin)
   except BaseException:self.code=1
  self.thread=threading.Thread(target=execute,daemon=True);self.thread.start()
 def wait(self):self.thread.join(3);native.need(not self.thread.is_alive());return self.code
 def poll(self):return self.code

class Facts:
 def __init__(self,session,hello,proxy):self.session=session;self.proxy=proxy
 def bind(self,contract,capsule):return {'SYNTHETIC_PRIVATE':'not public'}
 def authenticate(self,*a):self.proxy.call('stable')

class Tests(unittest.TestCase):
 def flow(self,kind,wrong=None,mode='inspect',receipt=None):
  calls=[];objects=[];request_holder={};profile=copy.deepcopy(program.BASE_PROFILE);sha='a'*64
  def session(role):
   obj=types.SimpleNamespace(actor={'pid':123,'parent_pid':2,'birth':'456','state':'S'},boot=('11111111-1111-1111-1111-111111111111' if role=='web' else '22222222-2222-2222-2222-222222222222'),profile=profile,started='2000-01-01T00:00:00+00:00',finished='2000-01-01T00:00:01+00:00',current={'releases':{'synthetic':{'runtime_count':3}}})
   health={'result':'ok' if role=='web' or kind=='healthy' else 'alert','evaluation_generation_sha256':('1' if role=='web' else '2')*64,'PRIVATE_SENTINEL':'must stay memory'}
   obj.idle=object.__new__(idle.IdleChallenge);x=obj.idle;x.role=role;x.raw_health=health;x.runtime_profile=profile;x.collector=object();x.stable=lambda:None
   def qualify(*args,**kwargs):calls.append('reevaluate');native.need(kind=='legacy');return {'schema':'careertrellis.qualified-database-generation/v1','evaluation_generation_sha256':'3'*64,'envelope':{'SYNTHETIC':'native core seam'}}
   x.bridge=types.SimpleNamespace(verify_raw_context=lambda *a,**k:None,qualify_local_database=qualify)
   def pair(*args,**kwargs):calls.append('pair');native.need(kind!='other')
   x.pair=types.SimpleNamespace(check_pair=pair);x.last_reader=object()
   x.accept=lambda generation,raw:calls.append('accept:'+generation['evaluation_generation_sha256'][0])
   obj.hello=lambda:{'schema':'careertrellis.preplan-private-peer/v1','role':role,'boot_id':obj.boot,'actor':obj.actor,'program_sha256':sha,'source_commit':profile['source_commit'],'packet_sha256':profile['packet_sha256'],'readiness_manifest_sha256':profile['readiness_manifest_sha256'],'scan_started_at':obj.started,'scan_finished_at':obj.finished}
   def challenge(request):
    calls.append(role+':challenge');request_holder.setdefault('original',copy.deepcopy(request));self.assertEqual(request,request_holder['original']);x.last_binding={'same_original_nonce':request['nonce']};return {'raw_health':health}
   obj.challenge=challenge
   obj.stable=lambda:calls.append('db:stable') or {'synthetic_stable':True}
   return obj
  def run(role,wire,stream):
   obj=session(role)
   if role=='web':
    if wrong=='extraowner':channel.send(wire,{'kind':'owner','role':'web','actor':{},'boot':'synthetic','program_sha256':sha})
    code=server.web(obj,mode,wire,stream,sha);return 1 if wrong=='nonzero' else code
   if wrong in ('partial','overflow','eof'):
    original=wire
    class Corrupt:
     def write(self,raw):
      original.write(b'' if wrong=='eof' else b'x'*(channel.MAXIMUM+1) if wrong=='overflow' else b'{PRIVATE');raise OSError('SYNTHETIC_MIDFRAME')
     def flush(self):pass
    wire=Corrupt()
   return server.database(obj,wire,stream,sha)
  def spawn(argv,**kwargs):
   child=Child(argv,run);objects.append(child);return child
  def allocation(*a):
   a[0].authenticate(None,None);calls.append('materializer');self.assertTrue(pathlib.Path(receipt).exists());value=json.loads(pathlib.Path(receipt).read_bytes());self.assertEqual(value['challenge']['nonce'],request_holder['original']['nonce']);return {'result':'PRIVATE_PLAN_ALLOCATED_NOT_DISPATCHED','nonce':value['challenge']['nonce'],'plan_sha256':'4'*64,'decision_sha256':'5'*64,'authorizes_dispatch':False}
  with patch.object(server,'allocate_bound',side_effect=allocation),patch.object(channel,'command',side_effect=lambda role,*a:role),patch.object(channel.subprocess,'Popen',side_effect=spawn),patch.object(server,'validate_peer',side_effect=lambda value,*a:value),patch.object(server,'PreparedFacts',Facts):
   if kind=='other' or wrong or receipt=='SYNTHETIC_EXPECTED_FAILURE':self.assertRaises(RuntimeError,channel.relay,sha,mode,profile,receipt);result=None
   else:result=channel.relay(sha,mode,profile,receipt)
  self.assertTrue(all(child.wait()==0 for child in objects) if kind!='other' and not wrong and receipt!='SYNTHETIC_EXPECTED_FAILURE' else all(child.poll() is not None for child in objects));self.assertTrue(all(child.stdin.closes==1 for child in objects));self.assertEqual(len(objects),1 if wrong=='extraowner' else 2)
  if result:self.assertNotIn('PRIVATE_SENTINEL',json.dumps(result));self.assertEqual(result['historical_receipts'],4) if mode=='inspect' else self.assertFalse(result['authorizes_dispatch'])
  return calls
 def test_actual_whole_path_healthy_no_database_reevaluation(self):
  calls=self.flow('healthy');self.assertNotIn('reevaluate',calls);self.assertIn('accept:2',calls);self.assertIn('db:stable',calls)
 def test_actual_whole_path_sole_legacy_qualification(self):
  calls=self.flow('legacy');self.assertEqual(calls.count('reevaluate'),1);self.assertIn('accept:3',calls);self.assertIn('db:stable',calls)
 def test_actual_whole_path_other_failure_refuses(self):self.flow('other')
 def test_ascii_wire_inclusive_newline_boundaries_and_escaping(self):
  overhead=len(json.dumps({'x':''},separators=(',',':')).encode())+1
  for delta in (-1,0,1):
   value={'x':'a'*(channel.MAXIMUM-overhead+delta)};sink=io.BytesIO()
   if delta<=0:channel.send(sink,value);self.assertEqual(len(sink.getvalue()),channel.MAXIMUM+delta)
   else:self.assertRaises(native.Refused,channel.send,sink,value);self.assertEqual(sink.getvalue(),b'')
  sink=io.BytesIO();channel.send(sink,{'x':'\u00e9'*200000});self.assertTrue(sink.getvalue().isascii());self.assertGreater(len(sink.getvalue()),1000000)
 def test_component_and_large_valid_envelope_budget(self):
  raw={'schema':'SYNTHETIC_SOURCE_BOUND_RAW','payload':'\u00e9'*20000};native.component_bound(raw,256*1024)
  envelope={'raw_web':raw,'raw_db':raw,'coverage':{'web':raw,'db':raw},'derived_snapshot':raw,'derived_result':raw,'binding':{'nonce':'a'*64}}
  wrapper={'schema':'SYNTHETIC_COMPLETE_ENVELOPE','envelope':envelope};native.component_bound(wrapper,2*1024**2);sink=io.BytesIO();channel.send(sink,{'seq':2,'ok':True,'value':wrapper});self.assertLess(len(sink.getvalue()),channel.MAXIMUM)
  self.assertRaises(native.Refused,native.component_bound,{'x':'a'*(256*1024)},256*1024)

 def test_actual_whole_path_postterminal_nonzero_and_extraowner_refuse(self):
  self.flow('healthy','nonzero');self.flow('healthy','extraowner')
 def test_actual_whole_path_response_eof_partial_and_overflow_refuse(self):
  for fault in ('eof','partial','overflow'):self.flow('healthy',fault)

 def test_actual_allocate_receipt_flushed_before_materializer(self):
  with tempfile.TemporaryDirectory(prefix='custody-synthetic-') as root:
   receipt=str(pathlib.Path(root).resolve()/'preplan-observable-custody.json');calls=self.flow('healthy',mode='allocate',receipt=receipt);self.assertEqual(calls.count('materializer'),1)
 def test_receipt_failure_never_reaches_materializer_or_forwards_challenge(self):
  with patch.object(channel,'create_custody_receipt',side_effect=OSError('SYNTHETIC_FSYNC')):
   # Receipt failure is the injected fault; no policy/private body is exposed.
   calls=self.flow('healthy',mode='allocate',receipt='SYNTHETIC_EXPECTED_FAILURE');self.assertNotIn('materializer',calls);self.assertNotIn('db:challenge',calls)
