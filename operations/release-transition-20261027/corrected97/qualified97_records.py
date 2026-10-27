"""Closed97 record domain. Incompatible with retained98 and legacy291d receipts."""
DOMAIN='/var/backups/careertrellis/preparation97-corrected-20261027/corrected97-fixture-g1'
ATTEMPT='corrected97-fixture-g1-20261027'
SCHEMAS={
 'materialization-intent.json':('careertrellis.corrected97-materialization-intent/v1',{'schema','revision','plan_sha256','source_manifest_sha256','accepted98_sha256','boot_id','peak_bytes','created_at'}),
 'materialization-proof.json':('careertrellis.corrected97-materialization-proof/v1',{'schema','revision','plan_sha256','accepted98_sha256','source_manifest_sha256','runtime_manifest_sha256','boot_id','completed_at'}),
 'preflight.json':('careertrellis.corrected97-preflight/v1',{'schema','kind','observed_at','boot_id','source_manifest_sha256','accepted98_receipt_sha256','archive_sha256','provenance_sha256','baseline','previous','live_build','release_absent','namespace_absent','unit_absent','software_inventory_sha256','browser_manifest_sha256','capacity','result'}),
 'owner.json':('careertrellis.corrected97-owner/v1',{'schema','pid','birth','boot_id','plan_sha256','started_at'}),
 'coordinator.json':('careertrellis.corrected97-coordinator/v1',{'schema','pid','birth','boot_id','owner_pid','plan_sha256','started_at'}),
 'child-spawn.json':('careertrellis.corrected97-child-spawn/v1',{'schema','pid','birth','boot_id','owner_pid','plan_sha256','decision_sha256','source_manifest_sha256'}),
 'unit-intent.json':('careertrellis.corrected97-unit-intent/v1',{'schema','kind','plan_sha256','decision_sha256','argv','properties','source_manifest_sha256','coordinator_pid','coordinator_birth','boot_id','created_at'}),
 'unit-client.json':('careertrellis.corrected97-unit-client/v1',{'schema','pid','birth','boot_id','intent_sha256'}),
 'unit-terminal.json':('careertrellis.corrected97-unit-terminal/v1',{'schema','kind','unit','intent_sha256','client_sha256','boot_id','observed_at','execution','after','cgroup_after','client_pid','client_birth','client_exit'}),
 'candidate.json':('careertrellis.corrected97-candidate/v1',{'schema','kind','revision','build','ops_commit','source_manifest_sha256','plan_sha256','decision_sha256','accepted98_receipt_sha256','materialization_proof_sha256','runtime_before_sha256','runtime_after_sha256','browser_before_sha256','browser_after_sha256','checks_sha256','unit_terminal_sha256','completed_at','result'}),
 'owner-terminal.json':('careertrellis.corrected97-owner-terminal/v1',{'schema','kind','boot_id','owner','coordinator','child_spawn_sha256','coordinator_exit','plan_sha256','decision_sha256','source_manifest_sha256','candidate_sha256','checks_sha256','unit_terminal_sha256','runtime_after_sha256','captures','owner_self_exit_claim','ended_at'}),
 'postterminal-proof.json':('careertrellis.corrected97-postterminal-proof/v1',{'schema','kind','boot_id','observed_at','plan_sha256','decision_sha256','source_manifest_sha256','accepted98_receipt_sha256','candidate_sha256','owner_terminal_sha256','owner_state','coordinator_state','unit','cgroup_after','runtime_manifest_sha256','browser_manifest_sha256','original_deadline'}),
 'accepted.json':('careertrellis.preparation97-corrected-acceptance/v1',{'schema','result','source_kind','revision','build','baseline','previous','attempt_id','ops_commit','source_manifest_sha256','plan_sha256','decision_sha256','candidate_sha256','archive_sha256','materialization_proof_sha256','accepted98_receipt_sha256','runtime_manifest','runtime_manifest_sha256','runtime_before_sha256','native_source_proof','checks','checks_sha256','unit_intent_sha256','unit_terminal_sha256','child_spawn_sha256','owner_terminal_sha256','postterminal_proof_sha256','completed_utc','activated'})}
def closed(name,value):
 schema,keys=SCHEMAS[name]
 if type(value) is not dict or set(value)!=keys or value['schema']!=schema:raise RuntimeError('CLOSED97_RECORD_REFUSED')
 return value

def validate_chronology(plan,owner,coordinator,material_intent,material_proof,unit_intent,checks,unit,candidate,current_time,owner_terminal=None,postterminal=None,acceptance=None):
 """Pure ordered97 causal chain within the ORIGINAL plan interval."""
 import datetime as dt
 def need(v):
  if not v:raise RuntimeError('QUALIFIED97_CHRONOLOGY_REFUSED')
 def at(text):
  value=dt.datetime.fromisoformat(text);need(value.tzinfo is not None and value.utcoffset() is not None);return value
 need(isinstance(current_time,dt.datetime) and current_time.tzinfo is not None)
 start=at(plan['decision_at']);deadline=at(plan['expires_at']);need(start<deadline<=start+dt.timedelta(minutes=55))
 actors=[owner['pid'],coordinator['pid'],unit['client_pid']];need(all(type(v) is int and v>1 for v in actors) and len(set(actors))==3)
 values=[start,at(owner['started_at']),at(coordinator['started_at']),at(material_intent['created_at']),at(material_proof['completed_at']),
  at(unit_intent['created_at']),at(checks['started_utc']),at(checks['ended_utc']),at(unit['observed_at']),at(candidate['completed_at'])]
 if owner_terminal is not None:values.append(at(owner_terminal['ended_at']))
 if postterminal is not None:values.append(at(postterminal['observed_at']))
 if acceptance is not None:values.append(at(acceptance['completed_utc']))
 need(all(a<=b for a,b in zip(values,values[1:])) and values[-1]<=current_time<deadline)
 return True
