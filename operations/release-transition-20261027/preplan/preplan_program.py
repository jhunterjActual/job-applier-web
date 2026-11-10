"""One native PREPLAN session: historical+current scans before original30s challenge."""
import hashlib,json,re,time,secrets
from pathlib import Path
from datetime import datetime,timezone
import preplan_history as history
from native_io import need,sha,canonical,decode,bootstrap,boot,process_identity,ProtectedReader,GitObjects
from native_runtime import collect_current,TARGET,CORRECTION,EXPECTED_SCANNER_LINEAGE,scanner_lineage
from native_idle import IdleChallenge
import native_pair
from native_io import set_public_phase
PACKET=history.PACKET_SHA;READINESS=history.READINESS_SHA;OPS=history.OPS
HISTORY={'corrected_acceptance':history.ACCEPTED97,'legacy_acceptance':history.ACCEPTED98,'web_pair':'19743124faf4864a8e5efdde5b9f416cc77e0070967dc6f2c487807a07262f09','db_pair':'42fec6f6ff56c4c2c4021d717458dc620542cd68e88b06ac32cd4b7762d84f98'}
DIFFS={TARGET:'db02c4ca5e044b3862fcef562a9eca75c0e4423fadca83db4eba5fdc9e6d4357',CORRECTION:'ad8f04dbf0afeeabb9ec1d3557cc5e709b4ebc0488490424950df83843beda12'}

BASE_PROFILE={'source_commit':OPS,'packet_sha256':PACKET,'readiness_manifest_sha256':READINESS,'scanner_methods':EXPECTED_SCANNER_LINEAGE}
def profile(value):
 need(type(value)is dict and set(value)=={'source_commit','packet_sha256','readiness_manifest_sha256','scanner_methods'} and type(value['source_commit'])is str and re.fullmatch('[0-9a-f]{40}',value['source_commit']) and value['source_commit']!='0'*40 and all(type(value[k])is str and re.fullmatch('[0-9a-f]{64}',value[k]) and value[k]!='0'*64 for k in ('packet_sha256','readiness_manifest_sha256')));scanner_lineage(value['scanner_methods']);return dict(value)

def public_sources(packet_raw,git=None,capsule=None,runtime_profile=None):
 binding=profile(BASE_PROFILE if runtime_profile is None else runtime_profile)
 need(type(packet_raw) is bytes and sha(packet_raw)==binding['packet_sha256']);m=decode(packet_raw);need(m['schema']=='careertrellis.app-release-packet/v1' and m['source_commit']==binding['source_commit'] and m['readiness']['SOURCE_MANIFEST.json']==binding['readiness_manifest_sha256'])
 paths={n+'.py':pin for n,pin in m['modules'].items()};paths.update(m['scripts']);paths.update({'one_shot_readiness/'+n:pin for n,pin in m['readiness'].items()});need(len(paths)==35 and len(m['modules'])==24 and len(m['scripts'])==4 and len(m['readiness'])==7)
 from package_bootstrap import public_paths
 need(set(paths)==set(public_paths()))
 if git is not None:
  raw={p:git.blob(binding['source_commit'],history.PREFIX+p) for p in paths};need(all(sha(v)==paths[p] for p,v in raw.items()));need(all(git.blob(binding['source_commit'],history.PREFIX+p)==v for p,v in raw.items()))
 else:
  need(type(capsule) is dict and all(type(k) is str and type(v) is bytes for k,v in dict.items(capsule)) and set(capsule)==set(paths));raw=dict(capsule);need(all(sha(v)==paths[p] for p,v in raw.items()))
 nested=decode(raw['one_shot_readiness/SOURCE_MANIFEST.json']);need(type(nested)is dict and set(nested)=={'schema','files'} and nested['schema']=='careertrellis.one-shot-readiness-source/v1' and nested['files']=={n:pin for n,pin in m['readiness'].items() if n!='SOURCE_MANIFEST.json'})
 transaction=decode(raw['one_shot_readiness/transaction04-pins.json']);expected={n+'.py':pin for n,pin in m['modules'].items()};expected.update(m['scripts']);need(type(transaction)is dict and set(transaction)=={'files'} and transaction['files']==expected)
 return raw

class NativeSession:
 def __init__(self,role,packet_raw,program_sha256,public_capsule=None,runtime_profile=None,historical_packet_raw=None,original_scanners=None):
  set_public_phase('SOURCE_AUTH');need(runtime_profile is not None);need(type(program_sha256) is str and re.fullmatch('[0-9a-f]{64}',program_sha256));self.role=role;self.boot=bootstrap(role);self.actor=process_identity(__import__('os').getpid());self.program_sha=program_sha256;self.reader=ProtectedReader();self.git=GitObjects();self.profile=profile(BASE_PROFILE if runtime_profile is None else runtime_profile);self.public=public_sources(packet_raw,self.git,runtime_profile=self.profile) if role=='web' else public_sources(packet_raw,capsule=public_capsule,runtime_profile=self.profile)
  self.started=datetime.now(timezone.utc).isoformat();self.started_monotonic=time.monotonic();self.current=None;self.historical=None;self.challenge_result=None;self.request=None
  if role=='web':
   context={'schema':'careertrellis.preplan-historical-context/v1','qualification':'authenticated_private_historical_inputs','runtime_source_commit':OPS,'packet_sha256':PACKET,'readiness_manifest_sha256':READINESS,'prepared_receipts':{TARGET:history.ACCEPTED97,CORRECTION:history.ACCEPTED98}}
   set_public_phase('HISTORY');self.historical=history.inspect_preplan_history(context,historical_packet_raw if historical_packet_raw is not None else packet_raw,self.reader,self.git.blob)
   set_public_phase('RUNTIME_SCAN');self.current=collect_current(self.public,self.reader,self.git,self.historical,self.profile['scanner_methods'],original_scanners)
  set_public_phase('PAIR_HISTORY');self.accepted_pair_raw=native_pair.local(role,self.reader)
  self.finished=datetime.now(timezone.utc).isoformat();self.scan_done_monotonic=time.monotonic();self.idle=IdleChallenge(role,self.public,self.reader,self.git,self.profile)
 def hello(self):
  self.identity_current();return {'schema':'careertrellis.preplan-private-peer/v1','role':self.role,'boot_id':self.boot,'actor':self.actor,'program_sha256':self.program_sha,'source_commit':self.profile['source_commit'],'packet_sha256':self.profile['packet_sha256'],'readiness_manifest_sha256':self.profile['readiness_manifest_sha256'],'scan_started_at':self.started,'scan_finished_at':self.finished}
 def identity_current(self):
  need(boot()==self.boot and process_identity(self.actor['pid'])['birth']==self.actor['birth'])
 def original_scans_current(self):
  self.identity_current();self.reader.stable()
  if self.current is not None:self.current['scanner'].stable()
 def challenge(self,request):
  need(self.request is None);self.original_scans_current();self.request=dict(request);result=self.idle.collect(request);self.original_scans_current()
  self.challenge_result={'schema':'careertrellis.preplan-private-challenge/v1','peer':self.hello(),'request':dict(request),'host':result,'scan_proof':None,'raw_health':self.idle.raw_health,'accepted_pair_hex':self.accepted_pair_raw.hex()}
  if self.role=='web':
   c=self.current;self.challenge_result['scan_proof']={'original_started_at':self.started,'original_finished_at':self.finished,'historical_bindings':HISTORY,'source_diffs':DIFFS,'releases':{rev:sha(canonical(value['snapshot'])) for rev,value in c['releases'].items()},'browser_sha256':sha(canonical(c['browser'])),'interpreter_sha256':c['interpreter']['sha256'],'provenance_sha256':c['provenance_sha256'],'scanner_source_lineage':c['scanner_source_lineage']}
  from native_io import component_bound
  component_bound(self.challenge_result,512*1024);raw=canonical(self.challenge_result);self.challenge_raw=raw;return self.challenge_result
 def stable(self):
  need(self.challenge_result is not None);self.identity_current();self.original_scans_current();self.idle.stable()
  if self.role=='db':native_pair.cleanup_absent()
  now=datetime.now(timezone.utc);issued=datetime.fromisoformat(self.request['issued_at']);need(0<=(now-issued).total_seconds()<=30)
  return {'schema':'careertrellis.preplan-private-stable/v1','role':self.role,'nonce':self.request['nonce'],'program_sha256':self.program_sha,'boot_id':self.boot,'actor':self.actor,'challenge_sha256':sha(self.challenge_raw),'checked_at':now.isoformat(),'database_generation_sha256':getattr(self.idle,'adopted_generation_sha256',None)}

def validate_peer(value,role,program_sha,runtime_profile=None):
 binding=profile(BASE_PROFILE if runtime_profile is None else runtime_profile)
 keys={'schema','role','boot_id','actor','program_sha256','source_commit','packet_sha256','readiness_manifest_sha256','scan_started_at','scan_finished_at'}
 need(type(value) is dict and set(value)==keys and value['schema']=='careertrellis.preplan-private-peer/v1' and value['role']==role and value['program_sha256']==program_sha and value['source_commit']==binding['source_commit'] and value['packet_sha256']==binding['packet_sha256'] and value['readiness_manifest_sha256']==binding['readiness_manifest_sha256'])
 need(type(value['boot_id']) is str and re.fullmatch('[0-9a-f-]{36}',value['boot_id']));a=value['actor'];need(type(a) is dict and set(a)=={'pid','parent_pid','birth','state'} and type(a['pid']) is int and a['pid']>1 and type(a['birth']) is str and re.fullmatch('[0-9]+',a['birth']))
 need(datetime.fromisoformat(value['scan_started_at'])<=datetime.fromisoformat(value['scan_finished_at']));return value

def host_fields(capsule,request,role,program_sha,peer,runtime_profile=None):
 binding=profile(BASE_PROFILE if runtime_profile is None else runtime_profile)
 need(type(capsule) is dict and set(capsule)=={'schema','peer','request','host','scan_proof','raw_health','accepted_pair_hex'} and capsule['schema']=='careertrellis.preplan-private-challenge/v1' and capsule['request']==request and capsule['peer']==peer);validate_peer(peer,role,program_sha,binding)
 h=capsule['host'];keys={'schema','role','boot_id','nonce','source_commit','observed_at','context_sha256','private_policy','public_policy','evidence_sha256','_private_evidence'}
 need(type(h) is dict and set(h)==keys and h['schema']=='careertrellis.app-plan-host-facts/v1' and h['role']==role and h['boot_id']==request['boots'][role] and h['nonce']==request['nonce'] and h['context_sha256']==request['context_sha256'] and h['source_commit']==binding['source_commit'])
 issued=datetime.fromisoformat(request['issued_at']);observed=datetime.fromisoformat(h['observed_at']);now=datetime.now(timezone.utc);need(0<=(observed-issued).total_seconds()<=30 and 0<=(now-observed).total_seconds()<=30)
 need(sha(canonical(h['_private_evidence']))==h['evidence_sha256']);e=h['_private_evidence'];need(e['role']==role and e['nonce']==request['nonce'] and e['boot_id']==request['boots'][role] and e['source_commit']==binding['source_commit'])
 if role=='web':need(type(capsule['scan_proof']) is dict and capsule['scan_proof']['historical_bindings']==HISTORY and capsule['scan_proof']['source_diffs']==DIFFS)
 else:need(capsule['scan_proof'] is None)
 return {k:v for k,v in h.items() if k!='_private_evidence'}

class PreparedFacts:
 """WEB live source object retains scanner/reader proof; no trusted status booleans."""
 def __init__(self,session,db_hello,db_proxy):
  from session_server import DBProxy
  need(type(session) is NativeSession and type(db_proxy) is DBProxy and session.role=='web');self.web=session;self.db_hello=validate_peer(db_hello,'db',session.program_sha,session.profile);self.db_proxy=db_proxy;self.raw=None
 def bind(self,contract,db_capsule):
  set_public_phase('AUTHENTICATION');request=self.web.request;need(request is not None and request['context_sha256']==sha(canonical(contract)))
  web_capsule=self.web.challenge_result;web_hello=self.web.hello();w=host_fields(web_capsule,request,'web',self.web.program_sha,web_hello,self.web.profile);d=host_fields(db_capsule,request,'db',self.web.program_sha,self.db_hello,self.web.profile)
  need(type(db_capsule['accepted_pair_hex'])is str and len(db_capsule['accepted_pair_hex'])<=131072);db_pair=bytes.fromhex(db_capsule['accepted_pair_hex']);native_pair.validate(self.web.public,self.web.accepted_pair_raw,db_pair,request['issued_at']);self.web.idle.bridge.verify_repository_binding(db_capsule['raw_health']['repository'],db_capsule['raw_health'],native_pair.LABEL,native_pair.FULL);
  need(hasattr(self.web.idle,'health_evidence'));health=self.web.idle.health_evidence;need(health['raw_web_generation_sha256']==web_capsule['raw_health']['evaluation_generation_sha256'] and health['raw_db_generation_sha256']==db_capsule['raw_health']['evaluation_generation_sha256']);self.db_generation_sha=health['database_generation_sha256'];self.db_capsule=decode(canonical(db_capsule))
  for h in (w,d):h['evidence_sha256']=sha(canonical({'original_private_evidence_sha256':h['evidence_sha256'],'joined_health_proof_sha256':sha(canonical(health))}))
  self.db_capsule_sha=sha(canonical(db_capsule));facts={'schema':'careertrellis.app-plan-allocation-facts/v1','qualification':'source_authenticated_private_facts','context_sha256':request['context_sha256'],'collector_source_sha256':self.web.program_sha,'nonce':request['nonce'],'observed_at':max(w['observed_at'],d['observed_at']),'hosts':{'web':w,'db':d},'historical_bindings':HISTORY,'source_diffs':DIFFS}
  self.contract=contract;self.raw=canonical(facts);self.facts=facts;self.authenticate(facts,contract);return facts
 def authenticate(self,facts,contract):
  need(self.raw is not None and canonical(facts)==self.raw and contract==self.contract and contract['source_commit']==self.web.profile['source_commit'] and contract['packet_sha256']==self.web.profile['packet_sha256'] and contract['readiness_manifest_sha256']==self.web.profile['readiness_manifest_sha256'] and contract['collector_source_sha256']==self.web.program_sha)
  self.web.stable();ack=self.db_proxy.call('stable');need(type(ack) is dict and set(ack)=={'schema','role','nonce','program_sha256','boot_id','actor','challenge_sha256','checked_at','database_generation_sha256'} and ack['schema']=='careertrellis.preplan-private-stable/v1' and ack['role']=='db' and ack['nonce']==facts['nonce'] and ack['program_sha256']==self.web.program_sha and ack['boot_id']==self.db_hello['boot_id'] and ack['actor']==self.db_hello['actor'] and ack['challenge_sha256']==self.db_capsule_sha and ack['database_generation_sha256']==self.db_generation_sha)
  now=datetime.now(timezone.utc);need(0<=(now-datetime.fromisoformat(ack['checked_at'])).total_seconds()<=30 and 0<=(now-datetime.fromisoformat(self.web.request['issued_at'])).total_seconds()<=30)
  return facts

 def private_proof(self):
  need(self.raw is not None)
  proof={'schema':'careertrellis.preplan-protected-source-proof/v1','program_sha256':self.web.program_sha,'runtime_profile':self.web.profile,'original_scan_started_at':self.web.started,'original_scan_finished_at':self.web.finished,'complete_current_runtime':{k:v['snapshot'] for k,v in self.web.current['releases'].items()},'complete_browser':self.web.current['browser'],'interpreter':self.web.current['interpreter'],'scanner_source_lineage':self.web.current['scanner_source_lineage'],'historical_bindings':HISTORY,'web_original_challenge':self.web.challenge_result,'db_original_challenge':self.db_capsule,'joined_health':self.web.idle.health_evidence,'database_generation':self.web.idle.accepted_generation}
  raw=canonical(proof);need(len(raw)<=32*1024**2);return raw
