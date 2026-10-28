"""Source-only actual-plan assembler DESIGN implementation; real allocation HARD HOLD."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import hashlib,json,re,os,stat
import pure_plan_contract as t
ALLOCATOR_READY=False
REAL_CONTRACT={'source_commit':None,'packet_sha256':None,'readiness_manifest_sha256':None,'collector_source_sha256':None}
_NATIVE_SESSION=None
_NATIVE_CONTEXT=None
_NATIVE_PARENT=None
HISTORY={'corrected_acceptance':'199a0c5db19644054d50ea09160146c5c325a6c7fe95733ffc41ffea619885c7','legacy_acceptance':'d91cb6623a18a98ce082e02f55b09e5e2a971ba985dd062f1ba6094ba3bacff3','web_pair':'19743124faf4864a8e5efdde5b9f416cc77e0070967dc6f2c487807a07262f09','db_pair':'42fec6f6ff56c4c2c4021d717458dc620542cd68e88b06ac32cd4b7762d84f98'}
DIFFS={t.TARGET:'db02c4ca5e044b3862fcef562a9eca75c0e4423fadca83db4eba5fdc9e6d4357',t.CORRECTION:'ad8f04dbf0afeeabb9ec1d3557cc5e709b4ebc0488490424950df83843beda12'}
CONTEXT_KEYS={'schema','mode','source_commit','packet_sha256','readiness_manifest_sha256','collector_source_sha256'}
FACT_KEYS={'schema','qualification','context_sha256','collector_source_sha256','nonce','observed_at','hosts','historical_bindings','source_diffs'}
HOST_KEYS={'schema','role','boot_id','nonce','source_commit','observed_at','context_sha256','private_policy','public_policy','evidence_sha256'}
class Refused(RuntimeError):pass
def need(v):
 if not v:raise Refused('DEPLOYMENT_PLAN_ASSEMBLER_REFUSED')
def sha(b):return hashlib.sha256(b).hexdigest()
def canonical(v):return t.canonical(v)
def decode(raw):
 def pairs(rows):
  d={}
  for k,v in rows:need(k not in d);d[k]=v
  return d
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
def hexvalue(v,n=64):return type(v) is str and re.fullmatch('[0-9a-f]{%d}'%n,v) is not None and v!='0'*n

def context(value,*,test_only=False):
 need(type(value) is dict and set(value)==CONTEXT_KEYS and value['schema']=='careertrellis.app-plan-source-contract/v1')
 need(hexvalue(value['source_commit'],40) and all(hexvalue(value[k]) for k in ('packet_sha256','readiness_manifest_sha256','collector_source_sha256')))
 if test_only:need(value['mode']=='SYNTHETIC_TEST_ONLY' and value['source_commit']=='c'*40)
 else:
  need(value['mode']=='actual' and _NATIVE_CONTEXT is not None and value==_NATIVE_CONTEXT)
 return value

def bind_native_session(session,contract):
 from preplan_program import PreparedFacts,OPS,PACKET,READINESS
 global _NATIVE_SESSION,_NATIVE_CONTEXT
 need(type(session) is PreparedFacts and _NATIVE_SESSION is None and contract['mode']=='actual' and contract['source_commit']==session.web.profile['source_commit'] and contract['packet_sha256']==session.web.profile['packet_sha256'] and contract['readiness_manifest_sha256']==session.web.profile['readiness_manifest_sha256'] and contract['collector_source_sha256']==session.web.program_sha)
 session.authenticate(session.facts,contract);_NATIVE_SESSION=session;_NATIVE_CONTEXT=dict(contract)

def authenticate_external_facts(facts,contract):
 need(_NATIVE_SESSION is not None and _NATIVE_CONTEXT==contract)
 _NATIVE_SESSION.authenticate(facts,contract)

def validate_inputs(facts,contract,now,*,test_only=False):
 context(contract,test_only=test_only);need(isinstance(now,datetime) and now.tzinfo is not None)
 need(type(facts) is dict and set(facts)==FACT_KEYS and facts['schema']=='careertrellis.app-plan-allocation-facts/v1')
 need(facts['qualification']==('SYNTHETIC_TEST_ONLY' if test_only else 'source_authenticated_private_facts'))
 need(facts['context_sha256']==sha(canonical(contract)) and facts['collector_source_sha256']==contract['collector_source_sha256'] and hexvalue(facts['nonce']))
 need(0<=(now-t.timestamp(facts['observed_at'])).total_seconds()<=30)
 need(facts['historical_bindings']==HISTORY and facts['source_diffs']==DIFFS and type(facts['hosts']) is dict and set(facts['hosts'])==set(t.ROLES))
 for role,value in facts['hosts'].items():
  need(type(value) is dict and set(value)==HOST_KEYS and value['schema']=='careertrellis.app-plan-host-facts/v1' and value['role']==role)
  need(value['nonce']==facts['nonce'] and value['source_commit']==contract['source_commit'] and value['context_sha256']==facts['context_sha256'])
  need(type(value['boot_id']) is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value['boot_id']) and hexvalue(value['evidence_sha256']))
  need(0<=(now-t.timestamp(value['observed_at'])).total_seconds()<=30)
  need(type(value['private_policy']) is dict and type(value['public_policy']) is dict and t.policy_canonical(value['private_policy'])==t.policy_canonical(value['public_policy']))
  t.new_policy(value['private_policy'],t.TARGET,'20261026.97',now)
 if not test_only:authenticate_external_facts(facts,contract)
 return facts

def assemble(facts,contract,decision_at,lifetime_seconds=3600,*,test_only=False):
 """PURE; injected decision time is a test/input seam, never an actual CLI clock."""
 validate_inputs(facts,contract,decision_at,test_only=test_only)
 need(type(lifetime_seconds) is int and 0<lifetime_seconds<=3600)
 expiry=decision_at+timedelta(seconds=lifetime_seconds)
 need(all(expiry<=t.timestamp(facts['hosts'][role]['private_policy']['valid_until']) for role in t.ROLES))
 policies={role:facts['hosts'][role]['private_policy'] for role in t.ROLES}
 plan={'schema':'careertrellis.app-policy-release/v2','base':t.BASE,'previous':t.PREVIOUS,'target':t.TARGET,'correction':t.CORRECTION,
  'builds':{**t.BUILDS,t.CORRECTION:'20261026.98'},'boots':{r:facts['hosts'][r]['boot_id'] for r in t.ROLES},
  'old_policy_digests':{r:t.policy_digest(p) for r,p in policies.items()},
  'new_policy_digests':{revision:{r:t.policy_digest(t.new_policy(p,revision,build,decision_at)) for r,p in policies.items()} for revision,build in ((t.TARGET,'20261026.97'),(t.CORRECTION,'20261026.98'))},
  'packet_sha256':contract['packet_sha256'],'readiness_manifest_sha256':contract['readiness_manifest_sha256'],'source_diffs':dict(DIFFS),
  'prepared_receipts':{t.TARGET:HISTORY['corrected_acceptance'],t.CORRECTION:HISTORY['legacy_acceptance']},
  'prepared_receipt_kinds':{t.TARGET:'corrected-fixture97-new/v1',t.CORRECTION:'repair98-retained/v1'},'expires_at':expiry.isoformat()}
 t.validate_plan(plan,decision_at);return plan

def _write_exclusive(path,raw):
 if _NATIVE_PARENT is not None:
  from native_io import identity,parents
  directory,fd,expected=_NATIVE_PARENT;path=Path(path);need(path.parent==directory);parents(path);need(identity(os.fstat(fd))[:6]==expected[:6]==identity(directory.lstat())[:6])
  out=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=fd)
  try:
   os.fchmod(out,0o600);offset=0
   while offset<len(raw):n=os.write(out,raw[offset:]);need(n>0);offset+=n
   os.fsync(out)
  finally:os.close(out)
  os.fsync(fd);check=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
  try:
   st=os.fstat(check);need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and stat.S_IMODE(st.st_mode)==0o600 and st.st_nlink==1 and st.st_size==len(raw));observed=b''
   while len(observed)<=len(raw):
    block=os.read(check,min(65536,len(raw)+1-len(observed)))
    if not block:break
    observed+=block
   need(observed==raw and identity(os.fstat(check))==identity(st))
  finally:os.close(check)
  need(identity(os.fstat(fd))[:6]==expected[:6] and identity(directory.lstat())[:6]==expected[:6]);return
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
 try:
  with os.fdopen(os.dup(fd),'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 finally:os.close(fd)
 need(Path(path).read_bytes()==raw)
 if os.name=='posix':
  fd=os.open(Path(path).parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  try:os.fsync(fd)
  finally:os.close(fd)

def _allocation_sequence(facts,contract,parent,clock,lifetime_seconds,*,test_only=False):
 """Internal transaction; tests use synthetic facts/temp only. No CLI caller clock."""
 parent=Path(parent);need(parent.is_absolute() and parent.is_dir() and not any(parent.iterdir()))
 for p in (parent,*parent.parents):
  st=p.lstat();need(stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode) and not getattr(st,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0))
 if not test_only:
  st=parent.lstat();need(st.st_uid==0 and stat.S_IMODE(st.st_mode)==0o700)
 # Preclaim freshness observation is NOT the operative decision timestamp.
 validate_inputs(facts,contract,clock(),test_only=test_only)
 claim={'schema':'careertrellis.app-plan-allocation-claim/v1','context_sha256':sha(canonical(contract)),'private_facts_sha256':sha(canonical(facts))}
 _write_exclusive(parent/'allocation-claim.json',canonical(claim))
 # Only winning fixed O_EXCL claimant obtains the operative decision clock.
 decision=clock();plan=assemble(facts,contract,decision,lifetime_seconds,test_only=test_only);plan_raw=canonical(plan)
 for name,raw in [('plan.json',plan_raw),('allocation-decision.json',canonical({'schema':'careertrellis.app-plan-allocation-decision/v1','decision_at':decision.isoformat(),'expires_at':plan['expires_at'],'plan_sha256':sha(plan_raw),'claim_sha256':sha(canonical(claim)),'context_sha256':sha(canonical(contract))}))]:
  actual=clock();validate_inputs(facts,contract,actual,test_only=test_only);need(decision<=actual<t.timestamp(plan['expires_at']));t.validate_plan(plan,actual);_write_exclusive(parent/name,raw)
 # Original expiry/decision are never recalculated or refreshed.
 return {'result':'private_plan_files_allocated_only','authorizes_dispatch':False}

def allocate_actual(facts,contract,parent,lifetime_seconds=3600):
 need(_NATIVE_SESSION is not None and _NATIVE_CONTEXT==contract)
 context(contract);need(os.name=='posix' and os.geteuid()==0)
 return _allocation_sequence(facts,contract,parent,lambda:datetime.now(timezone.utc),lifetime_seconds)

def main(argv=None):
 # Source-review hold BEFORE private reads, argument output, any clock or claim.
 if not ALLOCATOR_READY or any(v is None for v in REAL_CONTRACT.values()):
  print('{"result":"deployment_plan_allocator_source_hold","allocated":false}');return 1
 raise Refused('PROTECTED_FACT_FILE_BOOTSTRAP_NOT_YET_BOUND')
if __name__=='__main__':raise SystemExit(main())
