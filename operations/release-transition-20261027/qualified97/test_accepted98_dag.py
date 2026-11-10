"""Actual accepted98 validator on SYNTHETIC closed bytes; no real receipt/actor IDs."""
import copy,unittest
from synthetic98_dag import SyntheticDag
class Accepted98DagTests(unittest.TestCase):
 def test_actual_validator_positive_after_historical_expiry(self):
  f=SyntheticDag();value=f.authenticate();self.assertEqual(value['accepted_sha256'],f.expected)
  self.assertEqual(value['runtime'],f.live_runtime)
  self.assertEqual(f.ns['utc'].__code__.co_filename,'authenticated6eacc fixture')
 def test_rehashed_adversarial_closed_dag(self):
  def runtime(f):
   for leaf in ('runtime-before.json','runtime-manifest.json'):f.nodes[leaf]['backend/venv/bin/python']='d'*64
   h=f.sha(f.canonical(f.nodes['runtime-manifest.json']))
   f.nodes['full-preflight.json']['runtime_manifest_sha256']=h;f.nodes['repair-plan.json']['retained_runtime_manifest_sha256']=h
   f.nodes['repair-checks.json']['retained_runtime_manifest_sha256']=h;f.nodes['postterminal-owner-proof.json']['runtime_manifest_sha256']=h
   f.nodes['unit-intent.json']['argv']=f.ns['expected_unit_command'](f.nodes['repair-plan.json'],f.nodes['source-manifest.json'],f.ns['original_helpers']())
  def browser(f):
   h='e'*64;f.nodes['full-preflight.json']['browser_manifest_sha256']=h
   f.nodes['candidate.json']['browser_before_sha256']=h;f.nodes['candidate.json']['browser_after_sha256']=h
   f.nodes['postterminal-owner-proof.json']['browser_manifest_sha256']=h
  def deadline(f):
   f.nodes['repair-plan.json']['expires_at']='2020-01-01T00:09:00+00:00'
   f.nodes['repair-decision.json']['expires_at']='2020-01-01T00:09:00+00:00'
   f.nodes['postterminal-owner-proof.json']['original_deadline']='2020-01-01T00:09:00+00:00'
  cases={
   'source-contract-byte':lambda f:f.sources.__setitem__('repair_contract.py',f.sources['repair_contract.py']+b'\n# SYNTHETIC unapproved change\n'),
   'source-legacy-kind':lambda f:f.nodes['source-manifest.json'].__setitem__('repair_ops_commit',f.ns['OLD_OPS']),
   'plan-build':lambda f:f.nodes['repair-plan.json'].__setitem__('build','SYNTHETIC-wrong'),
   'decision-start':lambda f:f.nodes['repair-decision.json'].__setitem__('decision_at','2020-01-01T00:02:00+00:00'),
   'preflight-result':lambda f:f.nodes['full-preflight.json'].__setitem__('result','unknown'),
   'preflight-locks':lambda f:f.nodes['full-preflight.json'].__setitem__('locks_clear',False),
   'preflight-capacity':lambda f:f.nodes['full-preflight.json']['capacity'].__setitem__('available_bytes',0),
   'preflight-units':lambda f:f.nodes['full-preflight.json'].__setitem__('units',{}),
   'candidate-result':lambda f:f.nodes['candidate.json'].__setitem__('result','accepted'),
   'checks-provider':lambda f:f.nodes['repair-checks.json'].__setitem__('provider_requests',1),
   'checks-qualification':lambda f:f.nodes['repair-checks.json']['details']['backend']['fixture_qualification'].__setitem__('calls',True),
   'unit-intent-argv':lambda f:f.nodes['unit-intent.json']['argv'].append('--property=ProtectSystem=off'),
   'unit-intent-properties':lambda f:f.nodes['unit-intent.json']['sandbox_properties'].append('--property=PrivateNetwork=no'),
   'unit-invocation':lambda f:f.nodes['unit-terminal.json']['execution'].__setitem__('invocation_id','f'*32),
   'unit-nonzero':lambda f:f.nodes['unit-terminal.json'].__setitem__('client_exit',1),
   'client-birth':lambda f:f.nodes['test-client.json'].__setitem__('start_ticks','99999'),
   'child-spawn-birth':lambda f:f.nodes['child-spawn.json'].__setitem__('start_ticks','99999'),
   'owner-capture':lambda f:f.nodes['owner-terminal.json']['captures']['stdout'].__setitem__('error',True),
   'owner-self-exit':lambda f:f.nodes['owner-terminal.json'].__setitem__('owner_self_exit_claim',True),
   'postterminal-owner-live':lambda f:f.nodes['postterminal-owner-proof.json'].__setitem__('owner_state','live'),
   'postterminal-unit-pid':lambda f:f.nodes['postterminal-owner-proof.json']['unit'].__setitem__('MainPID','42'),
   'postterminal-cgroup':lambda f:f.nodes['postterminal-owner-proof.json']['cgroup_after'].__setitem__('populated',1),
   'runtime-coherent-rewrite':runtime,'browser-coherent-rewrite':browser,'deadline-expired-before-complete':deadline,
   'cross-boot':lambda f:setattr(f,'boot','99999999-1111-4222-8333-444444444444'),
   'cross-kind':lambda f:f.nodes['accepted.json'].__setitem__('schema','careertrellis.preparation97-qualified-acceptance/v1')}
  for name,mutate in cases.items():
   with self.subTest(mutation=name):
    f=SyntheticDag();original=f.expected;mutate(f);f.refresh()
    if name=='cross-boot':self.assertEqual(original,f.expected) # Host reboot changes observation, not immutable bytes.
    else:self.assertNotEqual(original,f.expected)
    with self.assertRaises((RuntimeError,ValueError,KeyError)):f.authenticate()
if __name__=='__main__':unittest.main()
