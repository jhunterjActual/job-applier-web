"""UNAPPLIED pure historical DAG inspection, never live admission or seal IO."""
from datetime import datetime, timedelta
import re
import adapter as a
import historical98_controls

REGISTRY_SHA = '18c97f07801ca83c05d1c434be829b5d894ffcd8dd7d905715e0bebc805a4901'
ATTEMPT = 'repair98-fixture-g1-20261027'
UNIT = 'careertrellis-preparation98-repair-fixture-g1-20261027'
MODULES = '''test_p0_regressions test_account_choice test_backup_health test_pilot_acceptance
test_pilot_acceptance_receipts test_search_runs test_serper_budget test_discovery_sources
test_additional_sources test_coverage_diagnostics test_feed_metadata test_catalog_reporting
test_public_job_feeds test_public_feed_integration test_shared_public_cache test_search_outcomes
test_search_completion_browser test_scheduled_search_dispatch test_account_erasure test_object_store
test_object_import test_import_reconciliation test_stored_objects test_cutover_rehearsal
test_final_cutover_coordinator test_generated_object_recovery test_generated_object_capture
test_generated_object_phases test_analytics test_observability test_ai_contracts test_claude_adapter
test_claude_integration test_claude_storage test_claude_workflows test_claude_search_failures
test_claude_browser test_claude_recovery test_role_queries test_role_plan_browser
test_title_compatibility_audit test_managed_search_reporting'''.split()
NATIVE_IDS = {'test_p0_regressions.DependencyLockTests.test_hosted_authority_marker_is_one_way_and_runtime_checks_fail_closed',
             'test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables'}
STATE_KEYS = {'LoadState','ActiveState','SubState','MainPID','ControlPID','Job','ControlGroup','Result','ExecMainStatus','InvocationID','Description'}
RELEASE='/opt/careertrellis/staging/releases/'+a.CORRECTION
WORK='/opt/careertrellis/staging/preparation98-repair-work-20261027/repair98-fixture-g1'
CODE='/opt/careertrellis/staging/preparation98-repair-code-20261027/repair98-fixture-g1'

def approved_properties():
    # Exact pure 291d sandbox_properties output for fixed repair paths. No host
    # lookup, mutable policy, caller option or current-clock input is used.
    return ['--property=Type=exec','--property=RemainAfterExit=yes','--property=Restart=no',
        '--property=User=nobody','--property=Group=nogroup','--property=SupplementaryGroups=careertrellis',
        '--property=PrivateNetwork=yes','--property=PrivateTmp=yes','--property=ProtectSystem=strict',
        '--property=ProtectHome=yes','--property=NoNewPrivileges=yes','--property=RestrictSUIDSGID=yes',
        '--property=MemoryMax=671088640','--property=MemorySwapMax=0','--property=TasksMax=192',
        '--property=RuntimeMaxSec=1200','--property=UMask=0077','--property=LimitFSIZE=67108864',
        '--property=KillMode=control-group','--property=ReadOnlyPaths='+RELEASE+' '+WORK+'/browsers',
        '--property=BindReadOnlyPaths=/var/lib/careertrellis/shared/ms-playwright:'+WORK+'/browsers',
        '--property=ReadWritePaths='+WORK,
        '--property=InaccessiblePaths=/etc/careertrellis /etc/caddy /var/lib/careertrellis -/var/lib/postgresql /var/backups /var/log /srv -/run/postgresql']

def approved_argv(receipt):
    return ['systemd-run','--unit='+UNIT,'--wait','--pipe','--collect',*approved_properties(),
        RELEASE+'/backend/venv/bin/python','-I','-B',CODE+'/run_checks_repair98.py',
        '--release',RELEASE,'--revision',a.CORRECTION,'--work',WORK,
        '--repair-ops-commit',receipt['repair_ops_commit'],'--approved-runner-sha256',receipt['runner_sha256'],
        '--approved-failure-chain-sha256',receipt['old_failure_chain_sha256'],
        '--retained-runtime-manifest-sha256',receipt['retained_runtime_before_sha256']]

def empty_group(value):
    a.need(type(value) is dict and set(value) == {'path','exists','processes','populated'}
           and value['path'] == '/system.slice/' + UNIT + '.service'
           and type(value['exists']) is bool
           and type(value['processes']) is int and value['processes'] == 0
           and type(value['populated']) is int and value['populated'] == 0)

def unit_history(value, post, intent_sha):
    a.need(value['unit'] == UNIT and type(value['client_pid']) is int and value['client_pid'] > 1
           and type(value['client_start_ticks']) is str and re.fullmatch('[0-9]+', value['client_start_ticks'])
           and type(value['client_exit']) is int and value['client_exit'] == 0)
    execution=value['execution']
    a.need(type(execution) is dict and set(execution) == {'unit','invocation_id','state','cgroup'}
           and execution['unit'] == UNIT and type(execution['invocation_id']) is str
           and re.fullmatch('[0-9a-f]{32}', execution['invocation_id']))
    state=execution['state']
    a.need(type(state) is dict and set(state) <= STATE_KEYS and all(type(v) is str for v in state.values())
           and state['LoadState'] == 'loaded' and state['ActiveState'] == 'active' and state['SubState'] == 'exited'
           and state['MainPID'] == state['ControlPID'] == '0' and state['Job'] == ''
           and state['Result'] == 'success' and state['ExecMainStatus'] == '0'
           and state['InvocationID'] == execution['invocation_id']
           and state['Description'] == 'careertrellis-repair98:'+intent_sha)
    empty_group(execution['cgroup']);empty_group(value['cgroup_after']);empty_group(post['cgroup_after'])
    for after in (value['after'], post['unit']):
        a.need(type(after) is dict and set(after) <= STATE_KEYS and all(type(v) is str for v in after.values())
               and after['LoadState'] in ('not-found','loaded')
               and after.get('MainPID','0') in ('','0') and after.get('ControlPID','0') in ('','0')
               and not after.get('Job') and after.get('ControlGroup','') in ('','/system.slice/'+UNIT+'.service'))
        if after['LoadState'] == 'loaded':
            a.need(after['ActiveState'] == 'inactive' and after['SubState'] == 'dead'
                   and after['Result'] == 'success' and after['ExecMainStatus'] == '0'
                   and after['InvocationID'] == execution['invocation_id'])
    a.need(post['unit']['LoadState'] == 'not-found')

def stamp(value):
    a.need(type(value) is str)
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    a.need(result.tzinfo is not None)
    return result

def inspect_dag(bundle, application_plan, registry_raw):
    try:
        return _inspect_dag(bundle, application_plan, registry_raw)
    except (KeyError, TypeError, ValueError, IndexError):
        raise a.Refused('HISTORICAL_RECORD_SHAPE_REFUSED') from None

def _inspect_dag(bundle, application_plan, registry_raw):
    """Inspect protected-byte snapshots supplied by a future fixed-path reader.

    Deliberately has no current time, current pointer, process lookup or live
    seal call. Historical deadline validity cannot expire at deployment time.
    Does not replace source/Git, complete runtime or original-failure checks.
    """
    a.need(a.sha(registry_raw) == REGISTRY_SHA)
    registry = a.decode(registry_raw)
    a.need(registry['schema'] == 'careertrellis.repair98-nested-registry/v3'
           and registry['domain'] == a.REPAIR_ROOT.rstrip('/'))
    names = set(registry['records']) | set(registry['plain_full_maps'])
    a.need(type(bundle) is dict and set(bundle) == names)
    values = {}
    for name in names:
        raw = bundle[name]
        value = a.decode(raw)
        a.need(type(value) is dict)
        if name in registry['records']:
            contract = registry['records'][name]
            a.need(contract['path'] == a.REPAIR_ROOT + name
                   and set(value) == set(contract['closed_keys'])
                   and value['schema'] == contract['schema'])
            if 'attempt_id' in value:
                a.need(value['attempt_id'] == ATTEMPT)
        else:
            a.need(registry['plain_full_maps'][name] == a.REPAIR_ROOT + name and value)
            for path, digest in value.items():
                a.need(type(path) is str and not path.startswith('/')
                       and '\\' not in path and '..' not in path.split('/'))
                a.digest(digest)
        values[name] = value
    receipt = a.inspect_repair98(bundle['accepted.json'], application_plan)
    plan = values['repair-plan.json']; decision = values['repair-decision.json']
    candidate = values['candidate.json']; owner = values['owner.json']
    coordinator = values['coordinator.json']; terminal = values['owner-terminal.json']
    post = values['postterminal-owner-proof.json']; pre = values['full-preflight.json']
    unit = values['unit-terminal.json']; checks = values['repair-checks.json']
    source = values['source-manifest.json']
    intent=values['unit-intent.json'];client=values['test-client.json'];spawn=values['child-spawn.json']
    old_failure=values['old-failure-chain.json']
    a.need(old_failure['release'] == a.CORRECTION
           and old_failure['old_plan_sha256'] == '82d42d67294c24feb13cd31ba17f0369ca220137ae3fd6471d401340be009a0e'
           and type(old_failure['files']) is dict
           and set(old_failure['files']) == {'intent','failed','dispatch','log','classifier02'})
    for value in old_failure['files'].values():a.digest(value)
    a.need(old_failure['files']['log'] == 'b494486137d1a4383c9181b10cb043dcbbc704c1847a176ceb0db7e9e8b2425c')
    links = {'repair_plan_sha256':'repair-plan.json', 'repair_decision_sha256':'repair-decision.json',
        'candidate_sha256':'candidate.json', 'source_manifest_sha256':'source-manifest.json',
        'old_failure_chain_sha256':'old-failure-chain.json', 'full_live_preflight_sha256':'full-preflight.json',
        'checks_sha256':'repair-checks.json', 'unit_terminal_sha256':'unit-terminal.json',
        'owner_terminal_sha256':'owner-terminal.json', 'postterminal_proof_sha256':'postterminal-owner-proof.json',
        'runtime_manifest_sha256':'runtime-manifest.json','unit_intent_sha256':'unit-intent.json',
        'test_client_sha256':'test-client.json','child_spawn_sha256':'child-spawn.json'}
    for field, name in links.items():
        expected=a.sha(bundle[name])
        if field in receipt:a.need(receipt[field] == expected)
        for record in (plan, decision, candidate, terminal, post, owner, pre, checks, unit, intent, client, spawn):
            if field in record:
                a.need(record[field] == expected)
    a.need(source['repair_ops_commit'] == plan['repair_ops_commit'] == candidate['repair_ops_commit']
           == checks['repair_ops_commit'] == receipt['repair_ops_commit'])
    a.need(plan['release'] == candidate['release'] == a.CORRECTION
           and plan['build'] == candidate['build'] == '20261026.98'
           and plan['baseline'] == a.BASE and plan['previous'] == a.PREVIOUS
           and candidate['result'] == 'candidate_only_not_accepted')
    a.need(pre['result'] == 'pass' and pre['baseline'] == a.BASE and pre['previous'] == a.PREVIOUS
           and pre['live_build'] == '20261017.96' and pre['authority'] == 'postgres-r2-authoritative'
           and pre['locks_clear'] is True)
    capacity=pre['capacity']
    a.need(type(capacity) is dict and set(capacity) == {'available_bytes','available_inodes','memory_available'}
           and all(type(v) is int for v in capacity.values()) and capacity['available_bytes'] >= 6442450944
           and capacity['available_inodes'] >= 10000 and capacity['memory_available'] >= 805306368)
    a.need(type(pre['units']) is dict and set(pre['units']) == {'careertrellis-web.service',
        'careertrellis-worker.service','careertrellis-erasure-worker.service'})
    for state in pre['units'].values():
        a.need(type(state) is dict and set(state) == {'active_running','main_pid'} and state['active_running'] is True
               and type(state['main_pid']) is int and state['main_pid'] > 1)
    for field in ('source_archive_sha256', 'source_diff_sha256'):
        a.need(plan[field] == receipt[field] == pre[field])
    a.need(set(source['files']) == set(registry['source_files']))
    for value in source['files'].values():
        a.need(type(value) is dict and set(value) == {'bytes', 'sha256'}
               and type(value['bytes']) is int and value['bytes'] > 0)
        a.digest(value['sha256'])
    a.need(receipt['runner_sha256'] == source['files']['run_checks_repair98.py']['sha256']
           and receipt['qualifier_sha256'] == source['files']['fixture_relocation.py']['sha256'])
    runtime_hash = a.sha(bundle['runtime-manifest.json'])
    a.need(bundle['runtime-before.json'] == bundle['runtime-manifest.json']
           and receipt['retained_runtime_before_sha256'] == receipt['retained_runtime_after_sha256'] == runtime_hash
           and candidate['runtime_before_sha256'] == candidate['runtime_after_sha256'] == runtime_hash
           and terminal['runtime_after_sha256'] == plan['retained_runtime_manifest_sha256'] == runtime_hash
           and pre['runtime_manifest_sha256'] == post['runtime_manifest_sha256'] == runtime_hash
           and candidate['browser_before_sha256'] == candidate['browser_after_sha256']
           == pre['browser_manifest_sha256'] == post['browser_manifest_sha256'])
    start = stamp(plan['decision_at']); deadline = stamp(plan['expires_at'])
    a.need(start < deadline <= start + timedelta(minutes=55)
           and decision['decision_at'] == plan['decision_at'] and decision['expires_at'] == plan['expires_at']
           and post['original_deadline'] == plan['expires_at'])
    observed = stamp(pre['observed_at'])
    a.need(observed <= start <= observed + timedelta(minutes=5))
    ordered = [start, stamp(owner['started_at']), stamp(coordinator['started_at']),stamp(intent['created_at']),
        stamp(checks['started_utc']), stamp(checks['ended_utc']), stamp(unit['observed_at']),
        stamp(candidate['completed_utc']), stamp(terminal['ended_at']), stamp(post['observed_at']),
        stamp(receipt['completed_utc'])]
    a.need(ordered == sorted(ordered) and ordered[-1] < deadline)
    boot = pre['boot_id']
    a.need(type(boot) is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',boot)
           and all(record['boot_id'] == boot
           for record in (owner, coordinator, unit, terminal, post, intent, client, spawn)))
    for identity in (owner, coordinator):
        a.need(type(identity['pid']) is int and identity['pid'] > 1
               and type(identity['start_ticks']) is str and identity['start_ticks'].isdigit())
    a.need(owner['pid'] != coordinator['pid'] and coordinator['owner_pid'] == owner['pid']
           and terminal['owner'] == owner and terminal['coordinator'] == coordinator
           and type(terminal['coordinator_exit']) is int and terminal['coordinator_exit'] == 0
           and terminal['owner_self_exit_claim'] is False
           and post['owner_state'] in ('absent', 'pid_reused_original_absent')
           and post['coordinator_state'] in ('absent', 'pid_reused_original_absent'))
    a.need(type(terminal['captures']) is dict and set(terminal['captures']) == {'stdout','stderr'})
    for capture in terminal['captures'].values():
        a.need(type(capture) is dict and set(capture) == {'retained_bytes','discarded_bytes','eof','error'}
               and capture['eof'] is True and capture['error'] is False
               and type(capture['retained_bytes']) is int and 0 <= capture['retained_bytes'] <= 1048576
               and type(capture['discarded_bytes']) is int and capture['discarded_bytes'] >= 0)
    a.need(spawn['pid'] == coordinator['pid'] and spawn['start_ticks'] == coordinator['start_ticks']
           and spawn['owner_pid'] == owner['pid']
           and intent['coordinator_pid'] == coordinator['pid']
           and intent['coordinator_start_ticks'] == coordinator['start_ticks']
           and intent['unit'] == client['unit'] == UNIT
           and intent['runner_sha256'] == receipt['runner_sha256']
           and intent['qualifier_sha256'] == receipt['qualifier_sha256']
           and intent['description_binding'] == 'sha256-of-this-record'
           and intent['sandbox_properties'] == approved_properties() and intent['argv'] == approved_argv(receipt)
           and client['pid'] == unit['client_pid'] and client['start_ticks'] == unit['client_start_ticks'])
    for identity in (client,spawn):
        a.need(type(identity['pid']) is int and identity['pid'] > 1
               and type(identity['start_ticks']) is str and identity['start_ticks'].isdigit())
    unit_history(unit,post,a.sha(bundle['unit-intent.json']))
    a.need(checks['revision'] == a.CORRECTION and checks['build'] == '20261026.98'
           and checks['checks'] == receipt['checks'] and checks['network_namespace_loopback_only'] is True
           and type(checks['provider_requests']) is int and checks['provider_requests'] == 0
           and checks['details']['backend']['fixture_qualification'] == receipt['fixture_qualification'])
    a.need(checks['runner_sha256'] == receipt['runner_sha256']
           and checks['qualifier_sha256'] == receipt['qualifier_sha256']
           and checks['old_failure_chain_sha256'] == receipt['old_failure_chain_sha256']
           and checks['retained_runtime_manifest_sha256'] == plan['retained_runtime_manifest_sha256']
           and checks['qualification'] == 'exact-one fixture-root relocation; original app/test bytes unchanged')
    backend=checks['details']['backend']
    a.need(type(backend) is dict and set(backend) == {'tests','modules','skipped','success_ids','failures','errors','fixture_qualification'}
           and type(backend['tests']) is int and backend['tests'] == 899
           and type(backend['failures']) is int and backend['failures'] == 0
           and type(backend['errors']) is int and backend['errors'] == 0
           and backend['modules'] == MODULES and type(backend['skipped']) is list
           and all(type(v) is str for v in backend['skipped'])
           and type(backend['success_ids']) is list and len(backend['success_ids']) == len(NATIVE_IDS)
           and set(backend['success_ids']) == NATIVE_IDS
           and not (set(backend['skipped']) & NATIVE_IDS))
    # The same exact pure semantic validator is used by qualified97's dependency
    # admission. This supplements byte-DAG checks without any live clock or IO.
    try:
        historical98_controls.validate(
            {'REV': a.CORRECTION, 'BASE': a.BASE, 'PREVIOUS': a.PREVIOUS,
             'OLD_OPS': '291d1b4b58bb17448395012408556090bea5ede1',
             'ATTEMPT_ID': ATTEMPT, 'UNIT': UNIT},
            receipt, plan, decision, source, pre, candidate, terminal, post,
            unit, checks, intent, datetime.fromisoformat(receipt['completed_utc']))
    except RuntimeError:
        raise a.Refused('SHARED_HISTORICAL98_SEMANTICS_REFUSED') from None
    return {'historical_dag_inspected': True, 'authorizes_preparation': False,
            'requires_original_failure_source_and_current_runtime_guards': True,
            'postterminal_cgroup_and_childspawn_links_inspected': True}
