"""Exact .79 fallback source/native-test contract; no activation authority."""
import ast
import re

BUILD='20260930.79'
SCHEMA='20260929_0021'
COMMIT='8540990edd9e661aac67aabf81c1cd22dd7e5e6d'
ARCHIVE_SHA256='6546d3cb0901fba1bf07ed1c98aa16464cbcc4a3a652999b0c89cae0b17bf5c2'
ARCHIVE_BYTES=6666240
CURRENT='cafad5b48a579adc80695519f0f8b7d39f621cd5'
MODULES=('factor_runtime','search_run_repository','enrollment_disposition',
    'enrollment_disposition_postgres','factor_removal','factor_removal_http',
    'factor_removal_postgres','workos_removal',
    'identity_plan_store','identity_plan_transfer','identity_plan_supersession',
    'identity_operator','identity_entry','identity_plan_operator','owner_recovery','factor_release_packet',
    'admission_purge','session_v2_transition')
TESTS=('test_p0_regressions','test_object_store','test_object_import','test_stored_objects',
    'test_account_erasure','test_search_runs','test_session_epoch','test_session_epoch_http',
    'test_provider_session_candidate','test_signup_http','test_totp_configuration',
    'test_totp_enrollment','test_totp_http','test_disabled_search_cleanup','test_postgres_rls',
    'test_factor_runtime','test_factor_runtime_postgres','test_factor_removal',
    'test_factor_removal_store','test_factor_removal_http','test_workos_removal',
    'test_enrollment_disposition','test_enrollment_transfer_postgres',
    'test_removal_session_postgres','test_security_slots_postgres','test_session_v2_transition',
    'test_identity_plan_store','test_identity_plan_transfer',
    'test_identity_plan_supersession','test_factor_release_packet',
    'test_admission_initial','test_identity_operator','test_identity_plan_operator',
    'test_owner_recovery','test_owner_recovery_bridge')
REQUIRED=tuple('backend/'+name+'.py' for name in MODULES+TESTS)+(
    'backend/static/factor-removal.js','deploy/postgres/grant-runtime.sql')
CHAIN=('20260909_0015','20260915_0016','20260928_0017','20260928_0018',
    '20260929_0019','20260929_0020',SCHEMA)


def require(value):
    if not value: raise RuntimeError('Schema21 application contract rejected.')


def bindings(commit,digest):
    require(commit==COMMIT and digest==ARCHIVE_SHA256)


def baseline(current,version):
    require(current==CURRENT and literal(version,'APP_BUILD')=='20260928.78')


def literal(raw,name):
    tree=ast.parse(raw)
    values=[node.value for node in tree.body if isinstance(node,(ast.Assign,ast.AnnAssign))
        and any(isinstance(target,ast.Name) and target.id==name
            for target in (node.targets if isinstance(node,ast.Assign) else [node.target]))]
    require(len(values)==1)
    return ast.literal_eval(values[0])


def source(files):
    require(all(name in files and files[name][0] for name in REQUIRED))
    require(literal(files['backend/version.py'][0],'APP_BUILD')==BUILD)
    migrations={}
    for name,(raw,executable) in files.items():
        if name.startswith('backend/migrations/versions/') and name.endswith('.py'):
            revision=literal(raw,'revision'); parent=literal(raw,'down_revision')
            require(type(revision) is str and revision not in migrations
                and (parent is None or type(parent) is str))
            require(len(name.split('/'))==4 and name.split('/')[-1].startswith(revision+'_')
                and literal(raw,'branch_labels') is None and literal(raw,'depends_on') is None)
            migrations[revision]=(parent,name)
    require(set(migrations)-{parent for parent,name in migrations.values()}=={SCHEMA})
    seen=set(); current=SCHEMA
    while current is not None:
        require(current in migrations and current not in seen)
        seen.add(current); current=migrations[current][0]
    require(seen==set(migrations))
    previous='20260907_0014'
    for revision in CHAIN:
        parent,name=migrations[revision]
        require(parent==previous and name[:-3]+'.sql' in files)
        previous=revision


def policy(value,commit,operations,digest):
    bindings(commit,digest)
    require(type(value) is dict and set(value)=={
        'application','operations','archive_sha256','build','schema','tests','allowed_skips'})
    require(value['application']==commit and value['operations']==operations
        and value['archive_sha256']==digest and value['build']==BUILD and value['schema']==SCHEMA
        and value['tests']==list(TESTS) and type(value['allowed_skips']) is list)
    skipped=value['allowed_skips']
    for item in skipped:
        require(type(item) is str)
        ordinary=re.fullmatch(r'(test_[a-zA-Z0-9_]+)\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+',item)
        setup=re.fullmatch(r'setUpClass \((test_[a-zA-Z0-9_]+)\.[a-zA-Z0-9_]+\)',item)
        matched=ordinary or setup
        require(matched is not None and matched.group(1) in TESTS)
    require(len(skipped)==len(set(skipped)))
    return value


def result(value,reviewed):
    require(type(value) is dict and set(value)=={'tests','failures','errors','skipped','failed_tests','error_tests'}
        and type(value.get('tests')) is int and value['tests']>0
        and type(value.get('failures')) is int and value['failures']==0
        and type(value.get('errors')) is int and value['errors']==0
        and value['failed_tests']==[] and value['error_tests']==[]
        and type(value.get('skipped')) is list
        and sorted(value['skipped'])==sorted(reviewed['allowed_skips']))

def snapshot(util):
    """Exact .78 protected baseline; never delegates the historical .71 gate."""
    from pathlib import Path
    releases=Path('/opt/careertrellis/staging/releases')
    current=Path('/opt/careertrellis/staging/current')
    expected=releases/'cafad5b48a579adc80695519f0f8b7d39f621cd5'
    require(current.resolve()==expected)
    lease=Path('/run/careertrellis-hosted-activation.lease')
    require(not lease.exists() and not lease.is_symlink())
    require(util.command(['/usr/local/sbin/careertrellis-authority','status']).strip()
        ==b'postgres-r2-authoritative')
    names=('staging.env','auth-v2.env','admission-web.json','postgres-runtime.env',
        'postgres-migration.env','object-storage.env','search-worker.env','postgres-worker.env',
        'provider-secrets.env','account-erasure-worker.env','postgres-erasure-worker.env',
        'account-erasure-object-storage.env')
    services=('careertrellis-web.service','careertrellis-worker.service',
        'careertrellis-erasure-worker.service')
    files={str(Path('/etc/careertrellis')/name):
        util.protected(Path('/etc/careertrellis')/name,modes=(0o600,)) for name in names}
    require(re.search(rb'(?m)^CAREERTRELLIS_AUTH_V2_ENABLED=0\r?$',files['/etc/careertrellis/auth-v2.env']))
    for name in services:
        for suffix in ('','.d/hosted-authority.conf'):
            path=Path('/etc/systemd/system')/(name+suffix)
            files[str(path)]=util.protected(path)
    files['authority_helper']=util.protected(Path('/usr/local/sbin/careertrellis-authority'))
    files['baseline_version']=util.protected(expected/'backend/version.py')
    require(literal(files['baseline_version'],'APP_BUILD')=='20260928.78')
    state={name:util.command(['/usr/bin/systemctl','show',name,'--property=ActiveState',
        '--property=MainPID','--property=NRestarts']) for name in services}
    require(all(b'ActiveState=active\n' in value for value in state.values()))
    require(current.resolve()==expected)
    return {'files':files,'services':state,'current':str(expected),
        'previous':str(Path('/opt/careertrellis/staging/previous').resolve())}
