"""Full fixed new98 dependency validator; no legacy291d PASS or expiry refresh.
The frozen98 contract namespace must be compiled from authenticated source bytes;
caller-supplied assertion wrappers are not accepted as a dependency.
"""
import datetime as dt
import historical98_controls
from pathlib import Path
CONTRACT_SHA='6eacc5f98edd31dfecf209f8e0692ab5ec993396fde6990e42e547cde598ce8f'
DOMAIN=Path('/var/backups/careertrellis/preparation98-repair-20261027/repair98-fixture-g1')
def need(v):
 if not v:raise RuntimeError('NEW98_DEPENDENCY_REFUSED')
def authenticate(ns,expected_sha,contract_raw):
 need(ns['sha'](contract_raw)==CONTRACT_SHA and ns['DOMAIN']==DOMAIN)
 read=ns['read'];decode=ns['decode'];sha=ns['sha'];closed=ns['closed'];canonical=ns['canonical']
 raw=read(DOMAIN/'accepted.json');a=decode(raw)
 need(sha(raw)==expected_sha and set(a)==ns['ACCEPTANCE_KEYS'] and a['schema']=='careertrellis.preparation98-repair-acceptance/v1'
  and a['result']=='qualified_prepared_inactive' and a['activated'] is False and a['release']==ns['REV'] and a['build']=='20261026.98')
 planraw=read(DOMAIN/'repair-plan.json');plan=closed('repair-plan.json',decode(planraw))
 decisionraw=read(DOMAIN/'repair-decision.json');decision=closed('repair-decision.json',decode(decisionraw))
 manifest_raw=read(DOMAIN/'source-manifest.json');manifest=ns['validate_source_manifest'](manifest_raw)
 pre_raw=read(DOMAIN/'full-preflight.json');pre=closed('full-preflight.json',decode(pre_raw))
 need(sha(planraw)==a['repair_plan_sha256']==decision['repair_plan_sha256'] and sha(decisionraw)==a['repair_decision_sha256']
  and sha(manifest_raw)==a['source_manifest_sha256']==plan['source_manifest_sha256']==decision['source_manifest_sha256']
  and sha(pre_raw)==a['full_live_preflight_sha256']==plan['full_live_preflight_sha256']==decision['full_live_preflight_sha256']
  and plan['repair_ops_commit']==a['repair_ops_commit']==manifest['repair_ops_commit']
  and plan['decision_at']==decision['decision_at'] and plan['expires_at']==decision['expires_at'])
 candidate_raw=read(DOMAIN/'candidate.json');candidate=closed('candidate.json',decode(candidate_raw))
 owner_raw=read(DOMAIN/'owner-terminal.json');owner=closed('owner-terminal.json',decode(owner_raw))
 post_raw=read(DOMAIN/'postterminal-owner-proof.json');post=closed('postterminal-owner-proof.json',decode(post_raw))
 checks_raw=read(DOMAIN/'repair-checks.json');checks=ns['validate_repair_checks'](checks_raw,plan,manifest)
 unit_raw=read(DOMAIN/'unit-terminal.json');unit=closed('unit-terminal.json',decode(unit_raw))
 need(sha(candidate_raw)==a['candidate_sha256']==owner['candidate_sha256']==post['candidate_sha256']
  and sha(owner_raw)==a['owner_terminal_sha256']==post['owner_terminal_sha256']
  and sha(post_raw)==a['postterminal_proof_sha256'] and sha(checks_raw)==a['checks_sha256']==candidate['checks_sha256']
  and sha(unit_raw)==a['unit_terminal_sha256']==candidate['unit_terminal_sha256'])
 completed=dt.datetime.fromisoformat(a['completed_utc']);decision_at=dt.datetime.fromisoformat(plan['decision_at'])
 need(decision_at<=dt.datetime.fromisoformat(candidate['completed_utc'])<=dt.datetime.fromisoformat(post['observed_at'])<=completed<dt.datetime.fromisoformat(plan['expires_at'])
  and post['original_deadline']==plan['expires_at'] and post['source_manifest_sha256']==a['source_manifest_sha256'])
 need(a['baseline']==ns['BASE'] and a['previous']==ns['PREVIOUS'] and a['repair_attempt_id']==ns['ATTEMPT_ID']
  and a['source_archive_sha256']==plan['source_archive_sha256'] and a['source_diff_sha256']==plan['source_diff_sha256']
  and a['runner_sha256']==manifest['files']['run_checks_repair98.py']['sha256']
  and a['qualifier_sha256']==manifest['files']['fixture_relocation.py']['sha256']
  and a['old_failure_chain_sha256']==plan['old_failure_chain_sha256']
  and candidate['repair_plan_sha256']==a['repair_plan_sha256'] and candidate['repair_decision_sha256']==a['repair_decision_sha256']
  and candidate['repair_ops_commit']==plan['repair_ops_commit'] and candidate['source_manifest_sha256']==plan['source_manifest_sha256']
  and candidate['full_live_preflight_sha256']==plan['full_live_preflight_sha256'] and candidate['old_failure_chain_sha256']==plan['old_failure_chain_sha256']
  and candidate['release']==ns['REV'] and candidate['build']=='20261026.98' and candidate['result']=='candidate_only_not_accepted')
 intent_raw=read(DOMAIN/'unit-intent.json');intent=closed('unit-intent.json',decode(intent_raw));need(sha(intent_raw)==unit['unit_intent_sha256'])
 historical98_controls.validate(ns,a,plan,decision,manifest,pre,candidate,owner,post,unit,checks,intent,completed)
 need(completed<=ns['utc']())
 currentboot=ns['boot']();need(currentboot==pre['boot_id']==owner['boot_id']==post['boot_id'])
 ns['validate_owner_terminal'](owner,candidate,candidate_raw,plan,a['repair_plan_sha256'],a['repair_decision_sha256'],currentboot)
 helpers=ns['original_helpers']();ns['validate_unit_terminal'](unit,currentboot,plan,manifest,a['repair_plan_sha256'],a['repair_decision_sha256'],helpers)
 # Deadline is historical evidence, never rewritten. Current observation calls
 # the raw full observer rather than the active-operation expiry-gated wrapper.
 live=ns['observe_retained'](plan['source_manifest_sha256'],plan['old_failure_chain_sha256'],helpers,True)
 ns['validate_browser_binding'](candidate,pre,live)
 before=decode(read(DOMAIN/'runtime-before.json'));runtime=decode(read(DOMAIN/'runtime-manifest.json'))
 need(before==runtime==live['runtime'] and sha(canonical(runtime))==a['runtime_manifest_sha256']==a['retained_runtime_before_sha256']==a['retained_runtime_after_sha256']
  and a['runtime_manifest']==str(DOMAIN/'runtime-manifest.json') and a['checks']==checks['checks']
  and a['fixture_qualification']==checks['details']['backend']['fixture_qualification'])
 ns['require_unit_absent']()
 return {'accepted_sha256':expected_sha,'runtime':runtime,'browser':live['browser'],'full_observation':live,'historical_completed_at':a['completed_utc'],'boot_id':currentboot}
