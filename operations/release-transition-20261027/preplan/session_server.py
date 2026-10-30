"""Ephemeral private peer session, no daemon/install/provider action."""
import os,sys,stat,json,secrets,time
from datetime import datetime,timezone
from native_io import need,sha,canonical,decode,bootstrap,set_public_phase,public_failure
from private_channel import send,MAXIMUM
from preplan_program import NativeSession,PreparedFacts,OPS,PACKET,READINESS,validate_peer
from allocation_entry import allocate_bound

class DBProxy:
 def __init__(self,wire,stream,program_sha):self.wire=wire;self.stream=stream;self.sha=program_sha;self.seq=0
 def call(self,action,**args):
  need(action==('hello' if self.seq==0 else 'challenge' if self.seq==1 else 'qualify' if self.seq==2 else 'stable'));request={'seq':self.seq,'program_sha256':self.sha,'action':action,'args':args};send(self.wire,{'kind':'db_rpc','request':request})
  raw=self.stream.readline(MAXIMUM+1);need(type(raw)is bytes and 0<len(raw)<=MAXIMUM and raw.endswith(b'\n'));response=decode(raw);need(type(response)is dict and set(response)=={'seq','ok','value'} and response['seq']==self.seq and response['ok'] is True);self.seq+=1;return response['value']

def database(session,wire,stream,program_sha):
 seq=0
 while True:
  raw=stream.readline(MAXIMUM+1)
  if raw==b'':return 0
  need(type(raw)is bytes and len(raw)<=MAXIMUM and raw.endswith(b'\n'));r=decode(raw);need(type(r)is dict and set(r)=={'seq','program_sha256','action','args'} and type(r['seq'])is int and r['seq']==seq and r['program_sha256']==program_sha and type(r['args'])is dict and r['action']==('hello' if seq==0 else 'challenge' if seq==1 else 'qualify' if seq==2 else 'stable'))
  set_public_phase({'hello':'PEER_HELLO','challenge':'CHALLENGE','qualify':'QUALIFICATION','stable':'AUTHENTICATION'}[r['action']])
  if r['action']=='hello':need(r['args']=={});value=session.hello()
  elif r['action']=='challenge':need(set(r['args'])=={'request'});value=session.challenge(r['args']['request'])
  elif r['action']=='stable':need(r['args']=={});value=session.stable()
  elif r['action']=='qualify':
   need(set(r['args'])=={'web_raw','db_raw','binding'});value=session.idle.qualify(r['args']['web_raw'],r['args']['db_raw'],r['args']['binding'])
  else:raise RuntimeError('PRIVATE_ACTION_REFUSED')
  send(wire,{'seq':seq,'ok':True,'value':value});seq+=1

def web(session,mode,wire,stream,program_sha):
 send(wire,{'kind':'owner','role':'web','actor':session.actor,'boot':session.boot,'program_sha256':program_sha});db=DBProxy(wire,stream,program_sha);hello=validate_peer(db.call('hello'),'db',program_sha,session.profile)
 contract={'schema':'careertrellis.app-plan-source-contract/v1','mode':'actual','source_commit':session.profile['source_commit'],'packet_sha256':session.profile['packet_sha256'],'readiness_manifest_sha256':session.profile['readiness_manifest_sha256'],'collector_source_sha256':program_sha}
 # Expensive scans completed before this sole immutable challenge clock.
 request={'nonce':secrets.token_hex(32),'issued_at':datetime.now(timezone.utc).isoformat(),'boots':{'web':session.boot,'db':hello['boot_id']},'context_sha256':sha(canonical(contract))}
 set_public_phase('CHALLENGE');db_capsule=db.call('challenge',request=request);session.challenge(request);set_public_phase('QUALIFICATION');generation=db.call('qualify',web_raw=session.idle.raw_health,db_raw=db_capsule['raw_health'],binding=session.idle.last_binding);session.idle.accept(generation,db_capsule['raw_health']);facts=PreparedFacts(session,hello,db);value=facts.bind(contract,db_capsule)
 if mode=='allocate':set_public_phase('MATERIALIZATION');result=allocate_bound(facts,contract)
 else:
  facts.authenticate(value,contract);result={'result':'PREPLAN_INSPECTED_NOT_ALLOCATED','source_commit':session.profile['source_commit'],'packet_sha256':session.profile['packet_sha256'],'program_sha256':program_sha,'nonce':request['nonce'],'facts_sha256':sha(canonical(value)),'runtime_file_count':sum(v['runtime_count'] for v in session.current['releases'].values()),'historical_receipts':4,'scan_started_at':session.started,'scan_finished_at':session.finished}
 send(wire,{'kind':'terminal','value':result});return 0

def main(role,mode,program_sha,caps,runtime_profile=None):
 """Only exact staged bootstrap calls this; normal stdout is replaced by null.
 Private frames use a dedicated duplicated PIPE handle consumed only by memory relay.
 """
 bootstrap(role);need(mode in ('inspect','allocate'));out=os.fstat(sys.stdout.fileno());need(stat.S_ISFIFO(out.st_mode) or stat.S_ISSOCK(out.st_mode));fd=os.dup(sys.stdout.fileno());wire=os.fdopen(fd,'wb',buffering=0)
 null=os.open(os.devnull,os.O_WRONLY);os.dup2(null,1);os.dup2(null,2);os.close(null)
 try:
  public={n[len('public-runtime35/'):]:v for n,v in caps.items() if n.startswith('public-runtime35/')};session=NativeSession(role,caps['packet_manifest.json'],program_sha,public_capsule=public,runtime_profile=runtime_profile,historical_packet_raw=caps['historical_packet_manifest.json'],original_scanners={name:caps['scanner-original-'+name] for name in ('native_backend.py','canonical_primitives.py')})
  if role=='db':return database(session,wire,sys.stdin.buffer,program_sha)
  return web(session,mode,wire,sys.stdin.buffer,program_sha)
 except BaseException as error:
  # No bodies/errors/tracebacks escape. Claimed allocation exceptions hold inside
  # allocate_bound, so this path cannot silently abandon its mutation custody.
  try:send(wire,{'kind':'unknown','no_replay':True,'failure':public_failure(error)})
  except BaseException:pass
  return 1
 finally:wire.close()
