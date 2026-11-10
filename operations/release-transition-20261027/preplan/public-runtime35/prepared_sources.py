"""Unapplied original-evidence checks; callbacks must be the reviewed host readers."""
import prepared_types as a
import prepared98_history as h

OPS='ae4e5171b9f0c874721cce8db5170a5815d59e8e'
PREFIX='operations/release-transition-20261027/repair98/'
CLASSIFIER_SHA='d49e8c5ab6dc7e40f782d9524e8748635fb41b8e8e83b6a8a87215e844edd134'
LOG_SHA='b494486137d1a4383c9181b10cb043dcbbc704c1847a176ceb0db7e9e8b2425c'
OLD='/var/backups/careertrellis/app-release-preparation-20261026/'+a.CORRECTION+'-attempt/'
OLD_STAGE='/opt/careertrellis/staging/pr98-117-release-preparation-20261026/'
FAILURE_PATHS={'intent':OLD+'intent.json','failed':OLD+'failed-inspection-required.json',
    'dispatch':OLD_STAGE+'dispatch-terminal.json','log':OLD+'synthetic-tests.log',
    'classifier02':'/opt/careertrellis/staging/preparation98-repair-source-20261027/repair98-fixture-g1/classifier02.json'}

def inspect_git_sources(manifest_raw,registry_raw,git_blob,*,return_bytes=False):
    """git_blob(commit, fixed_relative_path) performs no checkout/fetch/write."""
    a.need(type(return_bytes) is bool and a.sha(registry_raw)==h.REGISTRY_SHA)
    registry=a.decode(registry_raw);value=a.decode(manifest_raw)
    a.need(type(value) is dict and set(value)=={'schema','repair_ops_commit','files'}
           and value['schema']=='careertrellis.repair98-source-manifest/v1'
           and value['repair_ops_commit']==OPS and type(value['files']) is dict
           and set(value['files'])==set(registry['source_files']))
    observed={}
    for name,row in value['files'].items():
        a.need('/' not in name and '\\' not in name and name.endswith('.py')
               and type(row) is dict and set(row)=={'bytes','sha256'}
               and type(row['bytes']) is int and 0<row['bytes']<=1024*1024)
        a.digest(row['sha256']);raw=git_blob(OPS,PREFIX+name)
        a.need(type(raw) is bytes and len(raw)==row['bytes'] and a.sha(raw)==row['sha256'])
        observed[name]=raw
    for name,raw in observed.items():a.need(git_blob(OPS,PREFIX+name)==raw)
    if return_bytes:
        # Public executable sources only, never original failure/config bytes.
        # Consumer semantics use the SAME verified first snapshot.
        return observed
    return {'original_ops_git_bytes_verified':True,'files':len(observed)}

def inspect_original_failure(chain_raw,protected_read):
    """No raw log/record value is returned. No actor or clock is queried."""
    chain=a.decode(chain_raw)
    a.need(type(chain) is dict and set(chain)=={'schema','release','old_plan_sha256','files'}
           and chain['schema']=='careertrellis.repair98-old-failure-chain/v1'
           and chain['release']==a.CORRECTION
           and chain['old_plan_sha256']=='82d42d67294c24feb13cd31ba17f0369ca220137ae3fd6471d401340be009a0e'
           and type(chain['files']) is dict and set(chain['files'])==set(FAILURE_PATHS)
           and chain['files']['log']==LOG_SHA and chain['files']['classifier02']==CLASSIFIER_SHA)
    rows={name:protected_read(path) for name,path in FAILURE_PATHS.items()}
    for name,raw in rows.items():
        a.need(type(raw) is bytes and len(raw)<=1024*1024 and a.sha(raw)==chain['files'][name])
    for name,path in FAILURE_PATHS.items():a.need(protected_read(path)==rows[name])
    intent=a.decode(rows['intent']);dispatch=a.decode(rows['dispatch']);diagnostic=a.decode(rows['classifier02'])
    a.need(intent['revision']==dispatch['revision']==a.CORRECTION
           and intent['plan_sha256']==dispatch['plan_sha256']==chain['old_plan_sha256']
           and type(dispatch['exit_code']) is int and dispatch['exit_code']==1
           and diagnostic['result']=='TERMINAL_FAILURE_CLASSIFIED' and diagnostic['accepted'] is False
           and diagnostic['classification']['numeric_errno']==[30]
           and diagnostic['classification']['public_failed_test_ids']==[
             'test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables']
           and diagnostic['dispatch_sha256']==chain['files']['dispatch']
           and diagnostic['intent_sha256']==chain['files']['intent']
           and diagnostic['failed_marker_sha256']==chain['files']['failed'])
    return {'original_failure_bytes_verified':True,'authorizes_preparation':False}
