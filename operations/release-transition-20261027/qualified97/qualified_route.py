"""Shared fixed-spec role primitives. No generic revision, paths or native admission.
All root IO/authentication functions come from a source-bound Admission97 adapter, never
from a serialized caller callback. This module is still source-only integration.
"""
import datetime as dt,os,signal,subprocess,time
from qualified_specs import exact_spec
from materialize97 import own_child
READY=True

def need(v):
 if not v:raise RuntimeError('QUALIFIED_ROUTE_REFUSED')
def controls(boundary,kind):
 spec=exact_spec(kind);plan,decision,manifest=boundary.load_closed_controls(kind)
 need(plan['kind']==kind and plan['revision']==spec['revision'] and plan['build']==spec['build'])
 need(plan['source_manifest_sha256']==boundary.source_manifest_sha256(manifest)
  and decision['plan_sha256']==boundary.digest(plan) and decision['expires_at']==plan['expires_at'])
 need(boundary.boot()==decision['boot_id'] and boundary.now()<dt.datetime.fromisoformat(plan['expires_at']))
 return spec,plan,decision,manifest

def deferred_signals(fn):
 def wrapped(*args,**kwargs):
  prior={}
  for sig in (signal.SIGHUP,signal.SIGTERM,signal.SIGINT):prior[sig]=signal.getsignal(sig);signal.signal(sig,lambda *_:None)
  try:return fn(*args,**kwargs)
  finally:
   for sig,value in prior.items():signal.signal(sig,value)
 return wrapped

@deferred_signals
def coordinate(boundary,kind):
 need(READY);spec,plan,decision,manifest=controls(boundary,kind)
 boundary.record_current_coordinator()
 with boundary.canonical_fences():
  dependency=boundary.authenticate_accepted98(plan) if kind=='qualified97-new/v1' else None
  current=boundary.full_preflight(spec,plan,dependency)
  if spec['materialization']=='new-only':
   need(current['release_absent'] is True)
   materialization,runtime=boundary.materialize_new97(plan,manifest,current,dependency)
   # runtime-before is DERIVED here, never supplied by the precreation plan.
   boundary.require_materialization_proof(materialization,runtime,plan)
  else:
   need(spec['revision']=='f70417d4bd70c3eb086ea63c90938dd2f43e0b99')
   runtime=boundary.authenticate_retained98(plan,current)
  if spec['materialization']=='retained-only':boundary.create_record('runtime-before.json',runtime)
  boundary.prepare_root_readonly_code_and_private_work(spec,manifest)
  argv,props=boundary.exact_sandbox_command(spec,plan,manifest)
  boundary.require_new_unit_absent(spec)
  intent={'schema':'careertrellis.qualified97-unit-intent/v1','kind':kind,'plan_sha256':boundary.digest(plan),
   'decision_sha256':boundary.digest(decision),'argv':argv,'properties':props,'source_manifest_sha256':boundary.digest(manifest),
   'coordinator_pid':os.getpid(),'coordinator_birth':boundary.birth(os.getpid()),'boot_id':boundary.boot(),'created_at':boundary.now().isoformat()}
  intent_sha=boundary.create_record('unit-intent.json',intent)
  boundary.require_new_unit_absent(spec)
  child=None
  try:
   child=boundary.spawn_exact_unit(argv,intent_sha)
   boundary.create_record('unit-client.json',{'schema':'careertrellis.qualified97-unit-client/v1','pid':child.pid,'birth':boundary.birth(child.pid),'intent_sha256':intent_sha,'boot_id':boundary.boot()})
   terminal=boundary.wait_and_collect_exact_owned_unit(child,spec,intent_sha)
   boundary.create_record('unit-terminal.json',terminal)
   checks=boundary.read_and_authenticate_checks(spec,plan,manifest,terminal)
   need(set(checks['checks'])==spec['checks'] and all(type(v) is bool and v for v in checks['checks'].values()))
   # Test counts are actual per-suite results and full source-derived catalogs.
   boundary.authenticate_actual_catalogs(spec,checks)
   after=boundary.full_preflight(spec,plan,dependency,materialized=True)
   need(runtime==after['runtime']);boundary.create_record('runtime-manifest.json',after['runtime'])
   boundary.create_record('candidate.json',boundary.candidate(spec,plan,decision,manifest,checks,terminal,after))
  finally:boundary.retain_owned_unit_until_terminal(child,spec)

def retained_owner(boundary,kind):
 need(READY);spec,plan,decision,manifest=controls(boundary,kind)
 previous={};child=None;claimed=False
 for sig in (signal.SIGHUP,signal.SIGTERM,signal.SIGINT):previous[sig]=signal.getsignal(sig);signal.signal(sig,lambda *_:None)
 try:
  boundary.full_preflight(spec,plan,boundary.authenticate_accepted98(plan) if kind=='qualified97-new/v1' else None)
  boundary.require_new_unit_absent(spec)
  claimed=True;boundary.exclusive_owner_intent(spec,plan,decision)
  child=boundary.spawn_fixed_coordinator(kind)
  boundary.record_coordinator_birth(child.pid,boundary.birth(child.pid),plan,decision)
  terminal=boundary.drain_and_retain_coordinator(child)
  need(terminal['exit_code']==0 and terminal['stdout_eof'] is True and terminal['stderr_eof'] is True
   and terminal['capture_errors']==0)
  boundary.validate_candidate_and_unit_chain(spec,plan,decision)
  boundary.create_record('owner-terminal.json',boundary.owner_terminal(spec,plan,decision,terminal,owner_self_exit_claim=False))
  return 0
 finally:
  # Every evidence/write/read/poll failure retains the exact postmutation owner.
  # The adapter may return only after independently authenticated terminal.
  if claimed:boundary.retain_coordinator_and_unit_until_terminal(child,spec)
  for sig,old in previous.items():signal.signal(sig,old)

@deferred_signals
def seal(boundary,kind):
 need(READY);spec,plan,decision,manifest=controls(boundary,kind)
 with boundary.canonical_fences():
  owner,candidate,checks,unit=boundary.authenticate_complete_nested_chain(spec,plan,decision,manifest)
  dependency=boundary.authenticate_accepted98(plan) if kind=='qualified97-new/v1' else None
  live=boundary.full_preflight(spec,plan,dependency,materialized=True,postterminal=True)
  need(boundary.full_runtime_before()==boundary.full_runtime_after()==live['runtime'])
  boundary.authenticate_browser_before_after(candidate,live)
  boundary.final_original_birth_unit_cgroup_deadline_guard(spec,plan,decision,owner)
  proof=boundary.postterminal_proof(spec,plan,decision,owner,candidate,unit,live)
  proof_sha=boundary.create_record('postterminal-proof.json',proof)
  receipt=boundary.acceptance(spec,plan,decision,manifest,candidate,checks,unit,proof_sha,dependency)
  boundary.final_original_birth_unit_cgroup_deadline_guard(spec,plan,decision,owner)
  boundary.create_record('accepted.json',receipt)
  return 0
