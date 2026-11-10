"""Execute shared97 role functions with synthetic boundaries; no native claims."""
import contextlib,datetime as dt
from unittest.mock import Mock,patch
import unittest
import qualified_route as r
from qualified_specs import exact_spec
class Boundary:
 def __init__(self,fault=None):
  self.fault=fault;self.events=[];self.spec=exact_spec('corrected-fixture97-new/v1');self.runtime={'file':'hash'};self.child=Mock(pid=123)
  self.end=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=5)).isoformat()
  self.p={'kind':'corrected-fixture97-new/v1','revision':self.spec['revision'],'build':self.spec['build'],'source_manifest_sha256':'m','expires_at':self.end}
  self.d={'plan_sha256':'p','expires_at':self.end,'boot_id':'boot'};self.m={}
 def load_closed_controls(self,k):return self.p,self.d,self.m
 def digest(self,v):return 'p' if v is self.p else 'd' if v is self.d else 'm'
 def source_manifest_sha256(self,m):return 'm'
 def boot(self):return 'boot'
 def now(self):return dt.datetime.now(dt.timezone.utc)
 def record_current_coordinator(self):self.events.append('coord-record')
 @contextlib.contextmanager
 def canonical_fences(self):
  self.events.append('fence-enter')
  try:yield
  finally:self.events.append('fence-exit')
 def authenticate_accepted98(self,p):self.events.append('98-auth');return {}
 def full_preflight(self,*a,**k):return {'release_absent':True,'runtime':self.runtime}
 def materialize_new97(self,*a):self.events.extend(['materialize','runtime-before']);return {},self.runtime
 def require_materialization_proof(self,*a):self.events.append('materialization-proof')
 def create_record(self,n,v):
  self.events.append(n)
  if self.fault==n:raise OSError('synthetic boundary')
  return 'hash'
 def prepare_root_readonly_code_and_private_work(self,*a):self.events.append('code-work')
 def exact_sandbox_command(self,*a):return ['fixed'],['strict']
 def require_new_unit_absent(self,*a):self.events.append('unit-absent')
 def birth(self,p):
  if p==123 and self.fault=='birth':raise OSError('synthetic birth')
  return 'birth'
 def spawn_exact_unit(self,*a):self.events.append('unit-spawn');return self.child
 def wait_and_collect_exact_owned_unit(self,*a):return {}
 def read_and_authenticate_checks(self,*a):return {'checks':{k:True for k in self.spec['checks']}}
 def authenticate_actual_catalogs(self,*a):self.events.append('catalog')
 def candidate(self,*a):return {}
 def retain_owned_unit_until_terminal(self,child,*a):self.events.append('unit-hold' if child else 'no-child')
 def exclusive_owner_intent(self,*a):self.events.append('owner-intent')
 def spawn_fixed_coordinator(self,*a):self.events.append('coordinator-spawn');return self.child
 def record_coordinator_birth(self,*a):
  self.events.append('child-birth')
  if self.fault=='child-birth':raise OSError('synthetic')
 def drain_and_retain_coordinator(self,*a):return {'exit_code':1 if self.fault=='nonzero' else 0,'stdout_eof':True,'stderr_eof':True,'capture_errors':0}
 def validate_candidate_and_unit_chain(self,*a):self.events.append('chain-auth')
 def owner_terminal(self,*a,**k):return {}
 def retain_coordinator_and_unit_until_terminal(self,*a):self.events.append('owner-hold')
 def authenticate_complete_nested_chain(self,*a):return {},{},{},{}
 def full_runtime_before(self):return self.runtime
 def full_runtime_after(self):return self.runtime
 def authenticate_browser_before_after(self,*a):self.events.append('browser-auth')
 def final_original_birth_unit_cgroup_deadline_guard(self,*a):
  self.events.append('final-guard')
  if self.fault=='second-guard' and self.events.count('final-guard')==2:raise RuntimeError('expired')
 def postterminal_proof(self,*a):return {}
 def acceptance(self,*a):return {}
class RoleFlowTests(unittest.TestCase):
 def run_role(self,role,b):
  with patch.object(r,'READY',True),patch.object(r.signal,'SIGHUP',1,create=True),patch.object(r.signal,'signal',side_effect=lambda *a:b.events.append('signal')),patch.object(r.signal,'getsignal',return_value=None):
   return getattr(r,role)(b,'corrected-fixture97-new/v1')
 def test_new97_materialization_precedes_code_tests_and_no_duplicate_before(self):
  b=Boundary();self.run_role('coordinate',b)
  self.assertEqual(b.events.count('runtime-before'),1)
  self.assertLess(b.events.index('materialize'),b.events.index('code-work'));self.assertLess(b.events.index('code-work'),b.events.index('unit-spawn'))
  self.assertLess(b.events.index('unit-hold'),b.events.index('fence-exit'));self.assertEqual(b.events[0],'signal')
 def test_intent_failure_never_spawns(self):
  b=Boundary('unit-intent.json')
  with self.assertRaises(OSError):self.run_role('coordinate',b)
  self.assertNotIn('unit-spawn',b.events)
 def test_postspawn_birth_and_receipt_fault_hold_inside_fences(self):
  for f in ('birth','unit-client.json'):
   b=Boundary(f)
   with self.assertRaises(OSError):self.run_role('coordinate',b)
   self.assertLess(b.events.index('unit-hold'),b.events.index('fence-exit'));self.assertNotIn('candidate.json',b.events)
 def test_owner_nonzero_and_birth_fault_never_terminal_success(self):
  for f in ('nonzero','child-birth'):
   b=Boundary(f)
   with self.assertRaises((RuntimeError,OSError)):self.run_role('retained_owner',b)
   self.assertIn('owner-hold',b.events);self.assertNotIn('owner-terminal.json',b.events)
 def test_seal_persistence_fault_never_attempts_acceptance(self):
  b=Boundary('postterminal-proof.json')
  with self.assertRaises(OSError):self.run_role('seal',b)
  self.assertNotIn('accepted.json',b.events)
 def test_seal_rechecks_before_acceptance_after_proof(self):
  b=Boundary('second-guard')
  with self.assertRaises(RuntimeError):self.run_role('seal',b)
  self.assertIn('postterminal-proof.json',b.events);self.assertNotIn('accepted.json',b.events)
if __name__=='__main__':unittest.main()
