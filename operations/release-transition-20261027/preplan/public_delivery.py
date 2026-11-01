"""Separate PUBLIC packet/closed-plan staging. No private facts, app/policy/unit writes."""
import os,re,json
from pathlib import Path
from datetime import datetime,timezone
from native_io import need,sha,canonical,decode,bootstrap,boot,process_identity,ProtectedReader,deferred_child_signals,GitObjects,set_public_phase,identity
from package_bootstrap import verify_sources,create,hold
from native_idle import readonly_modules,clear_kernel_locks
from public_version import pointer_state
from preplan_program import public_sources,HISTORY,DIFFS
from pure_plan_contract import validate_plan,TARGET,CORRECTION
REMOTE=Path('/opt/careertrellis/staging/pr98-117-release-ops-20261026')
RECEIPTS=Path('/var/backups/careertrellis')

def bundle(role,manifest_raw,approved_sha,caps):
 need(type(role)is str and role in ('web','db'))
 m=verify_sources(manifest_raw,approved_sha,caps,GitObjects().blob if role=='web' else None)
 raw=caps['packet_manifest.json'];public={name[len('public-runtime35/'):]:value for name,value in caps.items() if name.startswith('public-runtime35/')}
 public_sources(raw,capsule=public,runtime_profile=m['runtime']);return m,raw,public

def binding_current(role,profile,binding,expected_boot=None,expected_sha=None):
 """Original challenge only; neither observation nor staging refreshes its TTL."""
 need(type(role)is str and role in ('web','db') and type(binding)is dict and set(binding)=={'schema','nonce','source_commit','boots','issued_at','phase','mode','release','build'} and binding['schema']=='careertrellis.preplan-scheduler-binding/v1' and binding['source_commit']==profile['source_commit'] and type(binding['nonce'])is str and re.fullmatch('[0-9a-f]{64}',binding['nonce']) and type(binding['boots'])is dict and set(binding['boots'])=={'web','db'} and all(type(v)is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',v) for v in binding['boots'].values()) and binding['phase']=='preflight' and binding['mode']=='idle' and binding['release']=='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4' and binding['build']=='20261017.96' and type(binding['issued_at'])is str)
 current=boot();need(current==binding['boots'][role] and (expected_boot is None or current==expected_boot));issued=datetime.fromisoformat(binding['issued_at']);need(issued.tzinfo is not None and 0<=(datetime.now(timezone.utc)-issued).total_seconds()<=30)
 pin=sha(canonical(binding));need(expected_sha is None or pin==expected_sha);return current,pin

def preflight(role,public,profile,binding):
 """Fresh root/source-bound LOCAL custody; never promotes DB templates to health."""
 current=bootstrap(role);_,original_sha=binding_current(role,profile,binding,expected_boot=current)
 clear_kernel_locks(role)
 with readonly_modules(public) as c:
  c.VERIFIED_SOURCE_MANIFEST=profile['readiness_manifest_sha256'];r=c.Reader();sources=c.installed493(r);custody=c.native_custody(r,role,binding,sources);c.authority(r,role);r.stable()
 pointers=pointer_state() if role=='web' else None
 binding_current(role,profile,binding,expected_boot=current,expected_sha=original_sha)
 return {'boot':current,'pointers':pointers,'binding_sha256':original_sha}

def plan_bytes(raw,approved_sha,profile,now):
 need(type(raw)is bytes and 0<len(raw)<=65536 and type(approved_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_sha) and sha(raw)==approved_sha)
 value=decode(raw);validate_plan(value,now);need(canonical(value)==raw and value['packet_sha256']==profile['packet_sha256'] and value['readiness_manifest_sha256']==profile['readiness_manifest_sha256'] and value['source_diffs']==DIFFS and value['prepared_receipts']=={TARGET:HISTORY['corrected_acceptance'],CORRECTION:HISTORY['legacy_acceptance']})
 return value

def stored_packet(role,profile,packet_raw,public):
 reader=ProtectedReader();s=REMOTE.lstat();need(__import__('stat').S_ISDIR(s.st_mode) and s.st_uid==0 and __import__('stat').S_IMODE(s.st_mode)==0o700)
 need(reader.read(REMOTE/'packet_manifest.json')==packet_raw)
 for name,raw in public.items():need(reader.read(REMOTE/name)==raw)
 intent=RECEIPTS/('app-public-packet-'+profile['packet_sha256']+'-'+role+'-intent.json');ir=reader.read(intent);sr=reader.read(intent.with_name(intent.name.replace('-intent.json','-success.json')));i=decode(ir);success=decode(sr)
 need(type(i)is dict and set(i)=={'schema','role','boot','actor','source_commit','packet_sha256','program_sha256'} and i['schema']=='careertrellis.app-public-packet-intent/v1' and i['role']==role and i['source_commit']==profile['source_commit'] and i['packet_sha256']==profile['packet_sha256'])
 need(success==dict(i,schema='careertrellis.app-public-packet-success/v1',intent_sha256=sha(ir),files=len(public)+1));actor=i['actor'];need(type(actor)is dict and type(actor['pid'])is int and actor['pid']>1 and type(actor['parent_pid'])is int and actor['parent_pid']>0 and type(actor['birth'])is str and re.fullmatch('[1-9][0-9]*',actor['birth']) and type(actor['state'])is str and actor['state'] in ('R','S','D','T','t','I'))
 if boot()==i['boot']:
  try:need(process_identity(actor['pid'])['birth']!=actor['birth'])
  except FileNotFoundError:pass
 reader.stable();return reader

def stage_packet(role,manifest_raw,approved_sha,caps,binding):
 m,packet,public=bundle(role,manifest_raw,approved_sha,caps);profile=m['runtime'];state=preflight(role,public,profile,binding);intent=RECEIPTS/('app-public-packet-'+profile['packet_sha256']+'-'+role+'-intent.json');success=intent.with_name(intent.name.replace('-intent.json','-success.json'))
 need(not any(os.path.lexists(p) for p in (REMOTE,intent,success)));fs=os.statvfs(REMOTE.parent);need(fs.f_bavail*fs.f_frsize-sum(len(v) for v in public.values())-len(packet)-65536>=5*1024**3 and fs.f_favail>=len(public)+1024)
 actor=process_identity(os.getpid());record={'schema':'careertrellis.app-public-packet-intent/v1','role':role,'boot':state['boot'],'actor':actor,'source_commit':profile['source_commit'],'packet_sha256':profile['packet_sha256'],'program_sha256':approved_sha};claimed=False
 with deferred_child_signals():
  try:
   binding_current(role,profile,binding,expected_boot=state['boot'],expected_sha=state['binding_sha256']);claimed=True;create(intent,canonical(record));os.mkdir(REMOTE,0o700)
   fd=os.open(REMOTE.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
   try:os.fsync(fd)
   finally:os.close(fd)
   os.mkdir(REMOTE/'one_shot_readiness',0o700)
   for name,raw in public.items():create(REMOTE/name,raw)
   create(REMOTE/'packet_manifest.json',packet);need(boot()==state['boot'] and (role!='web' or pointer_state()==state['pointers']))
   create(success,canonical(dict(record,schema='careertrellis.app-public-packet-success/v1',intent_sha256=sha(canonical(record)),files=len(public)+1)))
   return {'result':'PUBLIC_PACKET_STAGED_ONLY','packet_sha256':profile['packet_sha256'],'source_commit':profile['source_commit'],'files':len(public)+1,'intent_sha256':created_intent_sha(intent,record)}
  except BaseException:
   if claimed:hold()
   raise

def stage_plan(role,manifest_raw,approved_sha,caps,binding,raw,approved_plan_sha):
 m,packet,public=bundle(role,manifest_raw,approved_sha,caps);profile=m['runtime'];state=preflight(role,public,profile,binding);plan=plan_bytes(raw,approved_plan_sha,profile,datetime.now(timezone.utc));need(plan['boots']==binding['boots']);reader=stored_packet(role,profile,packet,public)
 intent=RECEIPTS/('app-public-plan-'+approved_plan_sha+'-'+role+'-intent.json');success=intent.with_name(intent.name.replace('-intent.json','-success.json'));path=REMOTE/'plan.json';need(not any(os.path.lexists(p) for p in (path,intent,success)));fs=os.statvfs(REMOTE);need(fs.f_bavail*fs.f_frsize-len(raw)-65536>=5*1024**3 and fs.f_favail>=1024);actor=process_identity(os.getpid());record={'schema':'careertrellis.app-public-plan-intent/v1','role':role,'boot':state['boot'],'actor':actor,'plan_sha256':approved_plan_sha,'packet_sha256':profile['packet_sha256'],'source_commit':profile['source_commit'],'program_sha256':approved_sha};claimed=False
 with deferred_child_signals():
  try:
   reader.stable();plan_bytes(raw,approved_plan_sha,profile,datetime.now(timezone.utc));binding_current(role,profile,binding,expected_boot=state['boot'],expected_sha=state['binding_sha256']);claimed=True;create(intent,canonical(record));plan_bytes(raw,approved_plan_sha,profile,datetime.now(timezone.utc));create(path,raw)
   need(ProtectedReader().read(path,65536)==raw and boot()==state['boot'] and (role!='web' or pointer_state()==state['pointers']));create(success,canonical(dict(record,schema='careertrellis.app-public-plan-success/v1',intent_sha256=sha(canonical(record)))))
   return {'result':'PUBLIC_PLAN_STAGED_NOT_DISPATCHED','plan_sha256':approved_plan_sha,'packet_sha256':profile['packet_sha256'],'source_commit':profile['source_commit'],'intent_sha256':created_intent_sha(intent,record)}
  except BaseException:
   if claimed:hold()
   raise

def export_chain(raw,decision_raw,claim_raw,intent_raw,terminal_raw,nonce,plan_sha,decision_sha,program_sha,profile,current_boot,now):
 plan=plan_bytes(raw,plan_sha,profile,now);need(type(nonce)is str and re.fullmatch('[0-9a-f]{64}',nonce) and type(decision_sha)is str and re.fullmatch('[0-9a-f]{64}',decision_sha) and sha(decision_raw)==decision_sha and plan['boots']['web']==current_boot)
 i=decode(intent_raw);t=decode(terminal_raw);d=decode(decision_raw);c=decode(claim_raw)
 expected_context={'schema':'careertrellis.app-plan-source-contract/v1','mode':'actual','source_commit':profile['source_commit'],'packet_sha256':profile['packet_sha256'],'readiness_manifest_sha256':profile['readiness_manifest_sha256'],'collector_source_sha256':program_sha}
 need(type(i)is dict and set(i)=={'schema','nonce','boot_id','actor','program_sha256','facts_sha256','context_sha256'} and i['schema']=='careertrellis.preplan-private-materialization/v1' and i['nonce']==nonce and i['boot_id']==current_boot and i['program_sha256']==program_sha and i['context_sha256']==sha(canonical(expected_context)))
 need(type(t)is dict and set(t)=={'schema','result','nonce','facts_sha256','context_sha256','plan_sha256','decision_sha256','intent_sha256','boot_id','authorizes_dispatch'} and t=={'schema':'careertrellis.preplan-private-materialization-terminal/v1','result':'PRIVATE_PLAN_ALLOCATED_NOT_DISPATCHED','nonce':nonce,'facts_sha256':i['facts_sha256'],'context_sha256':i['context_sha256'],'plan_sha256':plan_sha,'decision_sha256':decision_sha,'intent_sha256':sha(intent_raw),'boot_id':current_boot,'authorizes_dispatch':False})
 need(type(c)is dict and set(c)=={'schema','context_sha256','private_facts_sha256'} and c=={'schema':'careertrellis.app-plan-allocation-claim/v1','context_sha256':i['context_sha256'],'private_facts_sha256':i['facts_sha256']})
 need(type(d)is dict and set(d)=={'schema','decision_at','expires_at','plan_sha256','claim_sha256','context_sha256'} and d['schema']=='careertrellis.app-plan-allocation-decision/v1' and d['plan_sha256']==plan_sha and d['claim_sha256']==sha(claim_raw) and d['context_sha256']==i['context_sha256'] and d['expires_at']==plan['expires_at'])
 for pin in (i['facts_sha256'],i['context_sha256']):need(type(pin)is str and re.fullmatch('[0-9a-f]{64}',pin))
 decision=datetime.fromisoformat(d['decision_at']);expiry=datetime.fromisoformat(d['expires_at']);need(decision.tzinfo is not None and decision<=now<expiry and 0<(expiry-decision).total_seconds()<=3600)
 return plan,i['actor']

def export_plan(role,manifest_raw,approved_sha,caps,nonce,approved_plan_sha,approved_decision_sha):
 need(type(role)is str and role=='web' and type(nonce)is str and re.fullmatch('[0-9a-f]{64}',nonce));current_boot=bootstrap(role);m,_,_=bundle(role,manifest_raw,approved_sha,caps);reader=ProtectedReader();root=RECEIPTS/('preplan-app-'+nonce);controls=root/'controls'
 raw=reader.read(controls/'plan.json',65536);decision=reader.read(controls/'allocation-decision.json',65536);claim=reader.read(controls/'allocation-claim.json',65536);intent=reader.read(RECEIPTS/('preplan-app-'+nonce+'-materialization-intent.json'),65536);terminal=reader.read(RECEIPTS/('preplan-app-'+nonce+'-materialization-terminal.json'),65536)
 plan,actor=export_chain(raw,decision,claim,intent,terminal,nonce,approved_plan_sha,approved_decision_sha,approved_sha,m['runtime'],current_boot,datetime.now(timezone.utc))
 need(type(actor)is dict and set(actor)=={'pid','parent_pid','birth','state'} and type(actor['pid'])is int and actor['pid']>1 and type(actor['parent_pid'])is int and actor['parent_pid']>0 and type(actor['birth'])is str and re.fullmatch('[1-9][0-9]*',actor['birth']) and type(actor['state'])is str and actor['state'] in ('R','S','D','T','t','I'))
 try:need(process_identity(actor['pid'])['birth']!=actor['birth'])
 except FileNotFoundError:pass
 reader.stable();need(boot()==current_boot);plan_bytes(raw,approved_plan_sha,m['runtime'],datetime.now(timezone.utc))
 return {'result':'SANITIZED_OPERATIONAL_PLAN','source_commit':m['runtime']['source_commit'],'plan_sha256':approved_plan_sha,'plan':plan,'authorizes_dispatch':False}

def main(role,mode,manifest_raw,approved_sha,caps,binding,*,payload=None,approved_plan_sha=None,nonce=None,approved_decision_sha=None):
 # Root's reviewed source loader calls this normal API; no __file__/clock spoof.
 need(type(role)is str and role in ('web','db') and type(mode)is str and mode in ('stage-packet','stage-plan','export-plan'));set_public_phase({'stage-packet':'DELIVERY_PACKET','stage-plan':'DELIVERY_PLAN','export-plan':'PLAN_EXPORT'}[mode])
 if mode=='export-plan':need(payload is None);return export_plan(role,manifest_raw,approved_sha,caps,nonce,approved_plan_sha,approved_decision_sha)
 if mode=='stage-packet':need(payload is None and approved_plan_sha is None);return stage_packet(role,manifest_raw,approved_sha,caps,binding)
 return stage_plan(role,manifest_raw,approved_sha,caps,binding,payload,approved_plan_sha)


def created_intent_sha(path,record):
 reader=ProtectedReader();raw=reader.read(path,65536);need(raw==canonical(record));reader.stable();return sha(raw)

def packet_inventory(public,include_plan):
 # Fixed two-directory source namespace; unknown leaves/links never qualify.
 expected=set(public)|{'packet_manifest.json'}|({'plan.json'} if include_plan else set());need(all(type(name)is str for name in expected))
 stat=__import__('stat');result={}
 for path in (REMOTE,REMOTE/'one_shot_readiness'):
  info=path.lstat();need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o700);result[str(path.relative_to(REMOTE))]=identity(info)
  names=list(path.iterdir());need(len(names)<=64)
  actual=set()
  for leaf in names:
   rel=leaf.relative_to(REMOTE).as_posix();value=leaf.lstat()
   if rel=='one_shot_readiness':need(stat.S_ISDIR(value.st_mode) and value.st_uid==0 and stat.S_IMODE(value.st_mode)==0o700);continue
   need(rel in expected and stat.S_ISREG(value.st_mode) and value.st_uid==0 and value.st_nlink==1 and stat.S_IMODE(value.st_mode)==0o600);actual.add(rel);result[rel]=identity(value)
  required={name for name in expected if ('/' not in name if path==REMOTE else name.startswith('one_shot_readiness/'))}
  need(actual==required and (path!=REMOTE or any(leaf.name=='one_shot_readiness' for leaf in names)))
 need(set(result)==expected|{'.','one_shot_readiness'});return result

def terminal_actor(record,current_boot):
 need(record['boot']==current_boot);actor=record['actor']
 need(type(actor)is dict and set(actor)=={'pid','parent_pid','birth','state'} and type(actor['pid'])is int and actor['pid']>1 and type(actor['parent_pid'])is int and actor['parent_pid']>0 and type(actor['birth'])is str and re.fullmatch('[1-9][0-9]*',actor['birth']) and type(actor['state'])is str and actor['state'] in ('R','S','D','T','t','I'))
 try:need(process_identity(actor['pid'])['birth']!=actor['birth'])
 except FileNotFoundError:pass

def packet_records(role,profile,program_sha,current_boot,reader,approved_intent_sha=None):
 intent=RECEIPTS/('app-public-packet-'+profile['packet_sha256']+'-'+role+'-intent.json');ir=reader.read(intent,65536);sr=reader.read(intent.with_name(intent.name.replace('-intent.json','-success.json')),65536);i=decode(ir);success=decode(sr)
 need(type(i)is dict and set(i)=={'schema','role','boot','actor','source_commit','packet_sha256','program_sha256'} and i['schema']=='careertrellis.app-public-packet-intent/v1' and i['role']==role and i['source_commit']==profile['source_commit'] and i['packet_sha256']==profile['packet_sha256'] and (approved_intent_sha is None and bool(re.fullmatch('[0-9a-f]{64}',i['program_sha256'])) or i['program_sha256']==program_sha))
 need(approved_intent_sha is None or sha(ir)==approved_intent_sha);need(success==dict(i,schema='careertrellis.app-public-packet-success/v1',intent_sha256=sha(ir),files=36));terminal_actor(i,current_boot);return i,sha(ir),sha(sr)

def readback_packet(role,manifest_raw,approved_sha,caps,approved_intent_sha):
 need(type(approved_intent_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_intent_sha));current=bootstrap(role);m,packet,public=bundle(role,manifest_raw,approved_sha,caps);profile=m['runtime'];before=packet_inventory(public,False);reader=ProtectedReader();record,ir,sr=packet_records(role,profile,approved_sha,current,reader,approved_intent_sha);original=stored_packet(role,profile,packet,public)
 reader.stable();original.stable();need(packet_inventory(public,False)==before and boot()==current);terminal_actor(record,current)
 return {'result':'PUBLIC_PACKET_READBACK_ONLY','packet_sha256':profile['packet_sha256'],'source_commit':profile['source_commit'],'files':36,'intent_sha256':ir,'success_sha256':sr,'original_actor_absent':True,'authorizes_dispatch':False}

def readback_plan(role,manifest_raw,approved_sha,caps,approved_plan_sha,approved_intent_sha):
 need(type(approved_plan_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_plan_sha) and type(approved_intent_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_intent_sha));current=bootstrap(role);m,packet,public=bundle(role,manifest_raw,approved_sha,caps);profile=m['runtime'];before=packet_inventory(public,True);packet_reader=stored_packet(role,profile,packet,public);reader=ProtectedReader();packet_record,_,_=packet_records(role,profile,approved_sha,current,reader)
 raw=reader.read(REMOTE/'plan.json',65536);plan=plan_bytes(raw,approved_plan_sha,profile,datetime.now(timezone.utc));need(plan['boots'][role]==current)
 intent=RECEIPTS/('app-public-plan-'+approved_plan_sha+'-'+role+'-intent.json');ir=reader.read(intent,65536);sr=reader.read(intent.with_name(intent.name.replace('-intent.json','-success.json')),65536);i=decode(ir);success=decode(sr)
 need(sha(ir)==approved_intent_sha and type(i)is dict and set(i)=={'schema','role','boot','actor','plan_sha256','packet_sha256','source_commit','program_sha256'} and i['schema']=='careertrellis.app-public-plan-intent/v1' and i['role']==role and i['plan_sha256']==approved_plan_sha and i['packet_sha256']==profile['packet_sha256'] and i['source_commit']==profile['source_commit'] and i['program_sha256']==approved_sha)
 need(success==dict(i,schema='careertrellis.app-public-plan-success/v1',intent_sha256=sha(ir)));terminal_actor(i,current);reader.stable();packet_reader.stable();need(packet_inventory(public,True)==before and boot()==current);terminal_actor(packet_record,current);terminal_actor(i,current);plan_bytes(raw,approved_plan_sha,profile,datetime.now(timezone.utc))
 return {'result':'PUBLIC_PLAN_READBACK_ONLY','plan_sha256':approved_plan_sha,'packet_sha256':profile['packet_sha256'],'source_commit':profile['source_commit'],'intent_sha256':sha(ir),'success_sha256':sha(sr),'original_actor_absent':True,'authorizes_dispatch':False}
