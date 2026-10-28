"""Synthetic policies/facts/clock/temporary output only; no real plan allocation."""
import copy,io,tempfile,unittest,contextlib
from pathlib import Path
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import assemble_plan as a
class AssemblerTests(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,10,27,tzinfo=timezone.utc);self.context={'schema':'careertrellis.app-plan-source-contract/v1','mode':'SYNTHETIC_TEST_ONLY','source_commit':'c'*40,'packet_sha256':'d'*64,'readiness_manifest_sha256':'e'*64,'collector_source_sha256':'f'*64}
  policy={'schema':'careertrellis.paired-maintenance-policy/v1','application_release':a.t.BASE,'application_build':'20261017.96','valid_from':'2026-10-26T00:00:00+00:00','valid_until':'2026-11-01T00:00:00+00:00','synthetic_limit':900,'unicode':'syntheticÃƒÂ©'}
  hosts={r:{'schema':'careertrellis.app-plan-host-facts/v1','role':r,'boot_id':('1' if r=='web' else '2')*8+'-1111-1111-1111-111111111111','nonce':'3'*64,'source_commit':'c'*40,'observed_at':self.now.isoformat(),'context_sha256':a.sha(a.canonical(self.context)),'private_policy':copy.deepcopy(policy),'public_policy':copy.deepcopy(policy),'evidence_sha256':'4'*64} for r in a.t.ROLES}
  self.facts={'schema':'careertrellis.app-plan-allocation-facts/v1','qualification':'SYNTHETIC_TEST_ONLY','context_sha256':a.sha(a.canonical(self.context)),'collector_source_sha256':'f'*64,'nonce':'3'*64,'observed_at':self.now.isoformat(),'hosts':hosts,'historical_bindings':dict(a.HISTORY),'source_diffs':dict(a.DIFFS)}
 def assemble(self):return a.assemble(self.facts,self.context,self.now,test_only=True)
 def test_closed15_field_plan_and_source_validator(self):
  p=self.assemble();self.assertEqual(len(p),15);self.assertNotIn('issued_at',p);a.t.validate_plan(p,self.now);self.assertEqual((a.t.timestamp(p['expires_at'])-self.now).total_seconds(),3600)
 def test_493_hashes_and_only_release_build_policy_changes(self):
  p=self.assemble()
  for r in a.t.ROLES:
   old=self.facts['hosts'][r]['private_policy'];self.assertEqual(p['old_policy_digests'][r],a.t.policy_digest(old));new=a.t.new_policy(old,a.t.TARGET,'20261026.97',self.now)
   self.assertEqual({k:v for k,v in old.items() if k not in a.t.POLICY_KEYS},{k:v for k,v in new.items() if k not in a.t.POLICY_KEYS})
 def test_actual_missing_source_contract_hardrefuses(self):
  with self.assertRaises(a.Refused):a.assemble(self.facts,self.context,self.now)
 def test_main_holds_before_clock_reads_or_claim(self):
  with patch.object(a,'_allocation_sequence') as sequence,contextlib.redirect_stdout(io.StringIO()) as output:
   self.assertEqual(a.main([]),1);sequence.assert_not_called();self.assertNotIn('private_policy',output.getvalue())
 def test_stale_and_future_facts_refuse(self):
  for seconds in (-1,31):
   with self.assertRaises(a.Refused):a.assemble(self.facts,self.context,self.now+timedelta(seconds=seconds),test_only=True)
 def test_missing_extra_and_bad_context_refuse(self):
  for key,value in [('packet_sha256','UNALLOCATED'),('source_commit','f81'),('readiness_manifest_sha256',None)]:
   context=copy.deepcopy(self.context);context[key]=value
   with self.assertRaises(a.Refused):a.assemble(self.facts,context,self.now,test_only=True)
  f=copy.deepcopy(self.facts);f['extra']=True
  with self.assertRaises(a.Refused):a.assemble(f,self.context,self.now,test_only=True)
 def test_public_private_policy_mismatch_refuses(self):
  self.facts['hosts']['web']['public_policy']['synthetic_limit']=1000
  with self.assertRaises(a.Refused):self.assemble()
 def test_crosshost_nonce_boot_source_context_and_history_refuse(self):
  for field,value in [('nonce','5'*64),('boot_id','badboot'),('source_commit','d'*40),('context_sha256','1'*64)]:
   f=copy.deepcopy(self.facts);f['hosts']['db'][field]=value
   with self.assertRaises(a.Refused):a.assemble(f,self.context,self.now,test_only=True)
  self.facts['historical_bindings']['corrected_acceptance']='0'*64
  with self.assertRaises(a.Refused):self.assemble()
 def test_lifetime_type_max_and_policy_expiry(self):
  for value in (True,0,3601):
   with self.assertRaises(a.Refused):a.assemble(self.facts,self.context,self.now,value,test_only=True)
  for host in self.facts['hosts'].values():host['private_policy']['valid_until']=host['public_policy']['valid_until']=(self.now+timedelta(minutes=30)).isoformat()
  with self.assertRaises(a.Refused):self.assemble()
 def test_claim_precedes_operative_decision_clock_and_replay_refuses(self):
  events=[];write=a._write_exclusive
  def clock():events.append('clock');return self.now
  def record(path,raw):events.append(Path(path).name);return write(path,raw)
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent,prefix="preplan-synthetic-") as tmp,patch.object(a,'_write_exclusive',side_effect=record):
   a._allocation_sequence(self.facts,self.context,Path(tmp).absolute(),clock,3600,test_only=True)
   self.assertEqual(events[:3],['clock','allocation-claim.json','clock'])
   with self.assertRaises(a.Refused):a._allocation_sequence(self.facts,self.context,Path(tmp).absolute(),clock,3600,test_only=True)
 def test_concurrent_claim_single_winner(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent,prefix="preplan-synthetic-") as tmp:
   def attempt(_):
    try:return a._allocation_sequence(self.facts,self.context,Path(tmp).absolute(),lambda:self.now,3600,test_only=True)['result']
    except (a.Refused,FileExistsError):return 'refused'
   with ThreadPoolExecutor(max_workers=2) as pool:r=list(pool.map(attempt,range(2)))
   self.assertEqual(r.count('private_plan_files_allocated_only'),1);self.assertEqual(r.count('refused'),1)
 def test_stale_after_claim_retains_claim_without_plan(self):
  times=iter([self.now,self.now+timedelta(seconds=31)])
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent,prefix="preplan-synthetic-") as tmp:
   with self.assertRaises(a.Refused):a._allocation_sequence(self.facts,self.context,Path(tmp).absolute(),lambda:next(times),3600,test_only=True)
   self.assertTrue((Path(tmp)/'allocation-claim.json').exists());self.assertFalse((Path(tmp)/'plan.json').exists())
 def test_write_failure_keeps_partial_claim_no_retry(self):
  original=a._write_exclusive
  def fail(path,raw):
   if Path(path).name=='plan.json':raise OSError('synthetic')
   return original(path,raw)
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent,prefix="preplan-synthetic-") as tmp,patch.object(a,'_write_exclusive',side_effect=fail):
   with self.assertRaises(OSError):a._allocation_sequence(self.facts,self.context,Path(tmp).absolute(),lambda:self.now,3600,test_only=True)
   self.assertTrue((Path(tmp)/'allocation-claim.json').exists())
   with self.assertRaises(a.Refused):a._allocation_sequence(self.facts,self.context,Path(tmp).absolute(),lambda:self.now,3600,test_only=True)
if __name__=='__main__':unittest.main()
