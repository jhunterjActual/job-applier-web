"""PURE historical controls validation. No IO, clock substitution or live gate calls."""
import datetime as dt,re

def need(v):
 if not v:raise RuntimeError('HISTORICAL98_CONTROLS_REFUSED')
def aware(value):
 t=dt.datetime.fromisoformat(value);need(t.tzinfo is not None and t.utcoffset() is not None);return t
def validate(ns,a,plan,decision,manifest,pre,candidate,owner,post,unit,checks,intent,historical_time):
 need(isinstance(historical_time,dt.datetime) and historical_time.tzinfo is not None)
 start=aware(plan['decision_at']);deadline=aware(plan['expires_at']);observed=aware(pre['observed_at'])
 completed=aware(a['completed_utc']);need(completed==historical_time)
 need(observed<=start and 0<=(start-observed).total_seconds()<=300 and start<deadline<=start+dt.timedelta(minutes=55))
 need(decision['decision_at']==plan['decision_at'] and decision['expires_at']==plan['expires_at'])
 need(plan['release']==a['release']==candidate['release']==ns['REV'] and plan['build']==a['build']==candidate['build']=='20261026.98'
  and plan['baseline']==a['baseline']==pre['baseline']==ns['BASE'] and plan['previous']==a['previous']==pre['previous']==ns['PREVIOUS']
  and plan['repair_ops_commit']==a['repair_ops_commit']==candidate['repair_ops_commit']==manifest['repair_ops_commit']!=ns['OLD_OPS']
  and re.fullmatch('[0-9a-f]{40}',plan['repair_ops_commit'])
  and manifest['files']['repair_contract.py']['sha256']=='6eacc5f98edd31dfecf209f8e0692ab5ec993396fde6990e42e547cde598ce8f'
  and a['repair_attempt_id']==ns['ATTEMPT_ID'] and a['activated'] is False)
 need(pre['source_manifest_sha256']==plan['source_manifest_sha256']==decision['source_manifest_sha256']==candidate['source_manifest_sha256']==owner['source_manifest_sha256']==post['source_manifest_sha256']==a['source_manifest_sha256'])
 need(pre['old_failure_chain_sha256']==plan['old_failure_chain_sha256']==checks['old_failure_chain_sha256']==candidate['old_failure_chain_sha256']==a['old_failure_chain_sha256'])
 actors=[owner['owner'],owner['coordinator'],{'pid':unit['client_pid'],'start_ticks':unit['client_start_ticks']}]
 need(all(type(x['pid']) is int and x['pid']>1 for x in actors) and len({x['pid'] for x in actors})==3)
 need(pre['live_build']=='20261017.96' and pre['authority']=='postgres-r2-authoritative' and pre['result']=='pass' and pre['locks_clear'] is True)
 need(pre['source_archive_sha256']==plan['source_archive_sha256']==a['source_archive_sha256']=='67b392f0bd1c66b3391aa6e4c7f18b90b0a17fe7da72a4b5c32e0da594afb327'
  and pre['source_diff_sha256']==plan['source_diff_sha256']==a['source_diff_sha256']=='ad8f04dbf0afeeabb9ec1d3557cc5e709b4ebc0488490424950df83843beda12')
 runtime=plan['retained_runtime_manifest_sha256']
 need(runtime==pre['runtime_manifest_sha256']==checks['retained_runtime_manifest_sha256']==candidate['runtime_before_sha256']
  ==candidate['runtime_after_sha256']==a['retained_runtime_before_sha256']==a['retained_runtime_after_sha256']==a['runtime_manifest_sha256']==post['runtime_manifest_sha256'])
 need(pre['browser_manifest_sha256']==candidate['browser_before_sha256']==candidate['browser_after_sha256']==post['browser_manifest_sha256'])
 need(type(pre['capacity']) is dict and set(pre['capacity'])=={'available_bytes','available_inodes','memory_available'}
  and all(type(x) is int for x in pre['capacity'].values()) and pre['capacity']['available_bytes']>=6442450944
  and pre['capacity']['available_inodes']>=10000 and pre['capacity']['memory_available']>=805306368)
 names={'careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service'}
 need(type(pre['units']) is dict and set(pre['units'])==names)
 for v in pre['units'].values():need(type(v) is dict and set(v)=={'active_running','main_pid'} and v['active_running'] is True and type(v['main_pid']) is int and v['main_pid']>1)
 need(start<=aware(owner['owner']['started_at'])<=aware(owner['coordinator']['started_at'])<=aware(intent['created_at'])
  <=aware(checks['started_utc'])<=aware(checks['ended_utc'])<=aware(unit['observed_at'])<=aware(candidate['completed_utc'])
  <=aware(owner['ended_at'])<=aware(post['observed_at'])<=completed<deadline)
 expected_path='/system.slice/'+ns['UNIT']+'.service'
 for group in (unit['execution']['cgroup'],unit['cgroup_after']):
  need(type(group) is dict and set(group)=={'path','exists','processes','populated'} and group['path']==expected_path
   and type(group['exists']) is bool and type(group['processes']) is int and group['processes']==0 and type(group['populated']) is int and group['populated']==0)
 if unit['after'].get('LoadState')=='not-found':need(not unit['after'].get('InvocationID'))
 need(post['owner_state'] in ('absent','pid_reused_original_absent') and post['coordinator_state'] in ('absent','pid_reused_original_absent')
  and post['original_deadline']==plan['expires_at'])
 state=post['unit'];cg=post['cgroup_after']
 need(type(state) is dict and state.get('LoadState')=='not-found' and state.get('MainPID','0') in ('','0') and state.get('ControlPID','0') in ('','0') and not state.get('Job') and not state.get('InvocationID'))
 need(type(cg) is dict and set(cg)=={'path','exists','processes','populated'} and cg['path']=='/system.slice/'+ns['UNIT']+'.service'
  and cg['exists'] is False and type(cg['processes']) is int and cg['processes']==0 and type(cg['populated']) is int and cg['populated']==0)
 return {'historical_time':completed.isoformat(),'original_deadline':deadline.isoformat(),'historical_validated':True}
