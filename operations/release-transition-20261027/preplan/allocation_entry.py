"""Separate root-invoked private materialization/allocator; never called in inspect mode."""
import os,stat,re,time
from pathlib import Path
from datetime import datetime,timezone
import assemble_plan as assembler
from native_io import need,sha,canonical,parents,identity,boot,process_identity,ProtectedReader,deferred_child_signals
from package_bootstrap import create,hold
BASE=Path('/var/backups/careertrellis')

def allocate_bound(session,contract,lifetime_seconds=3600):
 from preplan_program import PreparedFacts
 need(type(session) is PreparedFacts)
 need(os.geteuid()==0 and type(lifetime_seconds) is int and 0<lifetime_seconds<=3600)
 session.authenticate(session.facts,contract);nonce=session.facts['nonce'];need(re.fullmatch('[0-9a-f]{64}',nonce));parent=BASE/('preplan-app-'+nonce);intent=BASE/('preplan-app-'+nonce+'-materialization-intent.json');terminal=BASE/('preplan-app-'+nonce+'-materialization-terminal.json')
 parents(parent);parents(intent);need(not any(os.path.lexists(p) for p in (parent,intent,terminal)))
 raw=canonical(session.facts);context_raw=canonical(contract);proof_raw=session.private_proof();need(len(raw)<=65536 and len(proof_raw)<=32*1024**2)
 fs=os.statvfs(BASE);need(fs.f_bavail*fs.f_frsize-len(raw)-len(proof_raw)-len(context_raw)-131072>=5*1024**3 and fs.f_favail>=4096);current_boot=boot();actor=process_identity(os.getpid());claimed=False;directory_fd=None
 with deferred_child_signals():
  try:
   session.authenticate(session.facts,contract);claimed=True
   materialization={'schema':'careertrellis.preplan-private-materialization/v1','nonce':nonce,'boot_id':current_boot,'actor':actor,'program_sha256':contract['collector_source_sha256'],'facts_sha256':sha(raw),'context_sha256':sha(context_raw)}
   create(intent,canonical(materialization));os.mkdir(parent,0o700)
   basefd=os.open(BASE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
   try:os.fsync(basefd)
   finally:os.close(basefd)
   create(parent/'private-facts.json',raw);create(parent/'source-contract.json',context_raw);create(parent/'validation-evidence.json',proof_raw)
   controls=parent/'controls';os.mkdir(controls,0o700);parentfd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
   try:os.fsync(parentfd)
   finally:os.close(parentfd)
   directory_fd=os.open(controls,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NONBLOCK);expected=identity(os.fstat(directory_fd));need(expected[3]==0 and stat.S_ISDIR(expected[2]) and stat.S_IMODE(expected[2])==0o700)
   # NativeIO identity order: dev,ino,mode,uid,gid,nlink,size,mtime,ctime.
   # Original797 claim precedes the operative decision clock and all plan writes.
   assembler.bind_native_session(session,contract);assembler._NATIVE_PARENT=(controls,directory_fd,expected)
   result=assembler.allocate_actual(session.facts,contract,controls,lifetime_seconds)
   reader=ProtectedReader();plan=reader.read(controls/'plan.json',65536);decision=reader.read(controls/'allocation-decision.json',65536);session.authenticate(session.facts,contract);need(boot()==current_boot)
   success={'schema':'careertrellis.preplan-private-materialization-terminal/v1','result':'PRIVATE_PLAN_ALLOCATED_NOT_DISPATCHED','nonce':nonce,'facts_sha256':sha(raw),'context_sha256':sha(context_raw),'plan_sha256':sha(plan),'decision_sha256':sha(decision),'intent_sha256':sha(canonical(materialization)),'boot_id':current_boot,'authorizes_dispatch':False}
   create(terminal,canonical(success));os.close(directory_fd);directory_fd=None;return {'result':success['result'],'nonce':nonce,'plan_sha256':sha(plan),'decision_sha256':sha(decision),'authorizes_dispatch':False}
  except BaseException:
   if claimed:hold()
   raise
  finally:
   if directory_fd is not None and not claimed:os.close(directory_fd)
