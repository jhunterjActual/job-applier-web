"""SYNTHETIC98 fixture function copied from parent test source; no real receipts."""
import copy,json
from pathlib import Path
import adapter as a
import historical as h
REGISTRY=Path(__file__).with_name("registry.snapshot.json")
def encoded(value):return json.dumps(value,sort_keys=True,separators=(",",":")).encode()

def fixture():
    registry_raw=REGISTRY.read_bytes();registry=a.decode(registry_raw)
    records={name:{key:None for key in spec['closed_keys']} for name,spec in registry['records'].items()}
    for name,value in records.items():
        value['schema']=registry['records'][name]['schema']
        if 'attempt_id' in value:value['attempt_id']=h.ATTEMPT
    raws={}
    def put(name):raws[name]=encoded(records[name]);return a.sha(raws[name])
    boot='12345678-1234-1234-1234-123456789abc';instant='2025-01-01T00:00:00+00:00'
    deadline='2025-01-01T00:55:00+00:00';other='1'*64;ops='a'*40
    archive='67b392f0bd1c66b3391aa6e4c7f18b90b0a17fe7da72a4b5c32e0da594afb327'
    diff='ad8f04dbf0afeeabb9ec1d3557cc5e709b4ebc0488490424950df83843beda12'
    runtime={'backend/venv/lib/synthetic.py':'2'*64}
    raws['runtime-before.json']=raws['runtime-manifest.json']=encoded(runtime);rt=a.sha(raws['runtime-manifest.json'])
    source=records['source-manifest.json'];source.update(repair_ops_commit=ops,
        files={name:{'bytes':10,'sha256':other} for name in registry['source_files']})
    source['files']['repair_contract.py']['sha256']='6eacc5f98edd31dfecf209f8e0692ab5ec993396fde6990e42e547cde598ce8f'
    source_sha=put('source-manifest.json')
    failure=records['old-failure-chain.json'];failure.update(release=a.CORRECTION,
        old_plan_sha256='82d42d67294c24feb13cd31ba17f0369ca220137ae3fd6471d401340be009a0e',
        files={name:other for name in registry['records']['old-failure-chain.json']['fixed_file_keys']})
    failure['files']['log']='b494486137d1a4383c9181b10cb043dcbbc704c1847a176ceb0db7e9e8b2425c';failure_sha=put('old-failure-chain.json')
    pre=records['full-preflight.json'];pre.update(observed_at=instant,boot_id=boot,baseline=a.BASE,
        previous=a.PREVIOUS,live_build='20261017.96',authority='postgres-r2-authoritative',
        source_archive_sha256=archive,source_diff_sha256=diff,runtime_manifest_sha256=rt,
        browser_manifest_sha256=other,source_manifest_sha256=source_sha,old_failure_chain_sha256=failure_sha,
        capacity={'available_bytes':6442450944,'available_inodes':10000,'memory_available':805306368},
        units={name:{'active_running':True,'main_pid':200+i} for i,name in enumerate(
            ('careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service'))},
        locks_clear=True,result='pass');pre_sha=put('full-preflight.json')
    plan=records['repair-plan.json'];plan.update(release=a.CORRECTION,build='20261026.98',baseline=a.BASE,
        previous=a.PREVIOUS,repair_ops_commit=ops,source_manifest_sha256=source_sha,
        old_failure_chain_sha256=failure_sha,full_live_preflight_sha256=pre_sha,
        retained_runtime_manifest_sha256=rt,source_archive_sha256=archive,source_diff_sha256=diff,
        decision_at=instant,expires_at=deadline);plan_sha=put('repair-plan.json')
    decision=records['repair-decision.json'];decision.update(decision_at=instant,expires_at=deadline,
        repair_plan_sha256=plan_sha,source_manifest_sha256=source_sha,full_live_preflight_sha256=pre_sha);decision_sha=put('repair-decision.json')
    owner=records['owner.json'];owner.update(pid=100,start_ticks='10',boot_id=boot,
        repair_plan_sha256=plan_sha,repair_decision_sha256=decision_sha,started_at=instant);put('owner.json')
    coord=records['coordinator.json'];coord.update(pid=101,start_ticks='11',boot_id=boot,owner_pid=100,started_at=instant);put('coordinator.json')
    spawn=records['child-spawn.json'];spawn.update(pid=101,start_ticks='11',boot_id=boot,owner_pid=100,
        repair_plan_sha256=plan_sha,repair_decision_sha256=decision_sha,source_manifest_sha256=source_sha);spawn_sha=put('child-spawn.json')
    intent=records['unit-intent.json'];intent.update(unit=h.UNIT,
        argv=h.approved_argv({'repair_ops_commit':ops,'runner_sha256':other,'old_failure_chain_sha256':failure_sha,
            'retained_runtime_before_sha256':rt}),sandbox_properties=h.approved_properties(),runner_sha256=other,
        qualifier_sha256=other,repair_plan_sha256=plan_sha,repair_decision_sha256=decision_sha,
        source_manifest_sha256=source_sha,boot_id=boot,coordinator_pid=101,coordinator_start_ticks='11',
        created_at=instant,description_binding='sha256-of-this-record');intent_sha=put('unit-intent.json')
    client=records['test-client.json'];client.update(pid=102,start_ticks='12',boot_id=boot,unit=h.UNIT,
        unit_intent_sha256=intent_sha);client_sha=put('test-client.json')
    group={'path':'/system.slice/'+h.UNIT+'.service','exists':False,'processes':0,'populated':0}
    execution={'unit':h.UNIT,'invocation_id':'a'*32,'state':{'LoadState':'loaded','ActiveState':'active','SubState':'exited',
        'MainPID':'0','ControlPID':'0','Job':'','Result':'success','ExecMainStatus':'0','InvocationID':'a'*32,
        'Description':'careertrellis-repair98:'+intent_sha},'cgroup':copy.deepcopy(group)}
    unit=records['unit-terminal.json'];unit.update(unit=h.UNIT,execution=execution,
        after={'LoadState':'not-found','Job':'','MainPID':'0','ControlPID':'0'},cgroup_after=group,client_pid=102,
        client_start_ticks='12',client_exit=0,boot_id=boot,observed_at=instant,
        unit_intent_sha256=intent_sha,test_client_sha256=client_sha);unit_sha=put('unit-terminal.json')
    q={key:True for key in a.QUALIFICATION_KEYS};q.update(schema='careertrellis.exact-fixture-relocation/v1',
        test_id='test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables',
        calls=1,analyzer_returncodes=[0,1],module_sha256='341e728a03f4f76bfa04c64aec612fae9fd87dc831266e547d9f42c7f0a42ab4',
        method_source_sha256='584aec03af3f6347fbce3f10f56b449deeea0925f3365e6f28a5d3cfcbd85cac',
        method_ast_sha256='ad2abbb01e5d212e3045b604105c2dc788281d56d64735887a827caad841af73',
        qualification='one TemporaryDirectory dir relocated; unchanged body and real positive/negative analyzer calls')
    checks=records['repair-checks.json'];checks.update(repair_ops_commit=ops,runner_sha256=other,
        qualifier_sha256=other,old_failure_chain_sha256=failure_sha,retained_runtime_manifest_sha256=rt,
        qualification='exact-one fixture-root relocation; original app/test bytes unchanged',
        revision=a.CORRECTION,build='20261026.98',checks={key:True for key in a.CHECKS},
        details={'backend':{'fixture_qualification':q,'tests':899,'modules':h.MODULES[:],'skipped':[],
            'success_ids':sorted(h.NATIVE_IDS),'failures':0,'errors':0}},started_utc=instant,ended_utc=instant,
        network_namespace_loopback_only=True,provider_requests=0);checks_sha=put('repair-checks.json')
    candidate=records['candidate.json'];candidate.update(release=a.CORRECTION,build='20261026.98',repair_ops_commit=ops,
        repair_plan_sha256=plan_sha,repair_decision_sha256=decision_sha,source_manifest_sha256=source_sha,
        old_failure_chain_sha256=failure_sha,full_live_preflight_sha256=pre_sha,checks_sha256=checks_sha,
        unit_terminal_sha256=unit_sha,runtime_before_sha256=rt,runtime_after_sha256=rt,
        browser_before_sha256=other,browser_after_sha256=other,completed_utc=instant,result='candidate_only_not_accepted');candidate_sha=put('candidate.json')
    terminal=records['owner-terminal.json'];terminal.update(owner=copy.deepcopy(owner),coordinator=copy.deepcopy(coord),
        coordinator_exit=0,boot_id=boot,repair_plan_sha256=plan_sha,repair_decision_sha256=decision_sha,
        candidate_sha256=candidate_sha,checks_sha256=checks_sha,unit_terminal_sha256=unit_sha,runtime_after_sha256=rt,
        source_manifest_sha256=source_sha,child_spawn_sha256=spawn_sha,
        captures={key:{'retained_bytes':0,'discarded_bytes':0,'eof':True,'error':False}
            for key in ('stdout','stderr')},ended_at=instant,owner_self_exit_claim=False);terminal_sha=put('owner-terminal.json')
    post=records['postterminal-owner-proof.json'];post.update(observed_at=instant,boot_id=boot,owner_terminal_sha256=terminal_sha,
        candidate_sha256=candidate_sha,owner_state='absent',coordinator_state='absent',unit=unit['after'],
        runtime_manifest_sha256=rt,browser_manifest_sha256=other,original_deadline=deadline,
        source_manifest_sha256=source_sha,cgroup_after=copy.deepcopy(group));post_sha=put('postterminal-owner-proof.json')
    receipt=records['accepted.json'];receipt.update(result='qualified_prepared_inactive',release=a.CORRECTION,
        build='20261026.98',baseline=a.BASE,previous=a.PREVIOUS,repair_attempt_id=h.ATTEMPT,repair_ops_commit=ops,
        source_manifest_sha256=source_sha,repair_plan_sha256=plan_sha,repair_decision_sha256=decision_sha,
        candidate_sha256=candidate_sha,source_archive_sha256=archive,source_diff_sha256=diff,runner_sha256=other,
        qualifier_sha256=other,old_failure_chain_sha256=failure_sha,full_live_preflight_sha256=pre_sha,
        retained_runtime_before_sha256=rt,retained_runtime_after_sha256=rt,runtime_manifest=a.REPAIR_ROOT+'runtime-manifest.json',
        runtime_manifest_sha256=rt,fixture_qualification=q,checks=checks['checks'],checks_sha256=checks_sha,
        unit_terminal_sha256=unit_sha,owner_terminal_sha256=terminal_sha,postterminal_proof_sha256=post_sha,
        completed_utc=instant,activated=False);receipt_sha=put('accepted.json')
    application_plan={'prepared_receipt_kinds':dict(a.KINDS),'prepared_receipts':{a.TARGET:other,a.CORRECTION:receipt_sha},
        'source_diffs':{a.TARGET:other,a.CORRECTION:diff}}
    return raws,application_plan,registry_raw
