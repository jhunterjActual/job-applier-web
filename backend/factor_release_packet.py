"""Pure source-packet preflight. No host operations or activation authority."""
import ast
import hashlib
import re
from pathlib import PurePosixPath


SCHEMA='20260929_0020'
CHAIN=('20260909_0015','20260915_0016','20260928_0017',
       '20260928_0018','20260929_0019',SCHEMA)
MODULES=('signup_http','account_session_candidate','factor_runtime',
    'enrollment_disposition','enrollment_disposition_postgres','factor_removal',
    'factor_removal_http','factor_removal_postgres','workos_removal','totp_enrollment',
    'totp_postgres','totp_http','workos_totp','session_v2_transition','totp_configuration',
    'admission_operations','identity_operator','identity_plan_operator','owner_recovery')
TESTS=('test_factor_runtime','test_factor_runtime_postgres','test_factor_removal',
    'test_factor_removal_store','test_factor_removal_http','test_workos_removal',
    'test_enrollment_disposition','test_enrollment_transfer_postgres',
    'test_removal_session_postgres','test_security_slots_postgres','test_totp_configuration',
    'test_session_v2_transition','test_p0_regressions','test_object_store',
    'test_object_import','test_stored_objects')
REQUIRED=tuple('backend/'+name+'.py' for name in MODULES+TESTS)+(
    'backend/version.py','backend/static/totp-enrollment.js','backend/static/factor-removal.js',
    'deploy/postgres/grant-runtime.sql','backend/requirements.in','backend/requirements.txt',
    'backend/dependency_lock.py')
PENDING=('immutable_release_commit_and_distinct_build',
    'successor_preparation_and_native_verification_tools',
    'identity_plan_and_session_transition_schema0020_contract',
    'protected_configuration_and_removal_flag_contract',
    'quiescence_and_no_existing_removal_attempts',
    'target_source_function_and_restricted_grant_verification',
    'matched_restore_and_forward_correction_plan',
    'host_storage_and_retention_review',
    'attended_provider_budget_and_identity_approval',
    'deployment_and_live_acceptance')


def require(value):
    if not value: raise ValueError('Factor release source packet rejected.')


def literal(raw,name):
    tree=ast.parse(raw)
    values=[node.value for node in tree.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id==name for target in node.targets)]
    values.extend(node.value for node in tree.body if isinstance(node,ast.AnnAssign)
        and isinstance(node.target,ast.Name) and node.target.id==name)
    require(len(values)==1)
    return ast.literal_eval(values[0])


def source_packet(files,*,base):
    """Validate an in-memory source snapshot; output cannot authorize execution.

    Caller must supply the complete intended source snapshot, not merely a diff.
    Content hashes bind that supplied snapshot; they do not attest Git provenance.
    No receipt booleans can promote this result into an operational approval.
    """
    require(isinstance(files,dict) and isinstance(base,str) and re.fullmatch('[0-9a-f]{40}',base))
    require(0<len(files)<=10000)
    total=0
    for name,raw in files.items():
        require(isinstance(name,str) and isinstance(raw,bytes))
        path=PurePosixPath(name)
        require(not path.is_absolute() and str(path)==name and '\\' not in name
            and ':' not in name and '..' not in path.parts)
        require(not any(part in ('.git','.codex','data','venv','__pycache__')
            or part.startswith('.env') and name!='backend/.env.example' for part in path.parts))
        require(not any(str(parent) in files for parent in path.parents))
        total+=len(raw)
        require(len(raw)<=10000000 and total<=100000000)
    require(all(name in files and files[name] for name in REQUIRED))
    build=literal(files['backend/version.py'],'APP_BUILD')
    require(isinstance(build,str) and re.fullmatch(r'\d{8}\.\d+',build))
    migrations={}
    for name,raw in files.items():
        if name.startswith('backend/migrations/versions/') and name.endswith('.py'):
            revision=literal(raw,'revision')
            require(isinstance(revision,str) and revision not in migrations)
            migrations[revision]=(name,literal(raw,'down_revision'),raw)
    previous='20260907_0014'
    for revision in CHAIN:
        require(revision in migrations)
        name,parent,raw=migrations[revision]
        require(parent==previous and name[:-3]+'.sql' in files)
        previous=revision
    # This application has one linear history. Missing ancestors, merges,
    # disconnected roots and sibling heads need a separately reviewed contract.
    parents=[]
    for _,parent,_ in migrations.values():
        require(parent is None or isinstance(parent,str) and parent in migrations)
        if parent is not None: parents.append(parent)
    require(set(migrations)-set(parents)=={SCHEMA})
    visited=set(); current=SCHEMA
    while current is not None:
        require(current not in visited)
        visited.add(current)
        current=migrations[current][1]
    require(visited==set(migrations))
    manifest={name:hashlib.sha256(raw).hexdigest() for name,raw in sorted(files.items())}
    import json
    digest=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(kind='factor_source_preflight_v1',phase='local_source_checked_not_release_ready',
        base=base,build=build,schema=SCHEMA,source_manifest=manifest,
        source_manifest_sha256=digest,source_bytes=total,required_tests=list(TESTS),
        pending=list(PENDING),execution_authorized=False,
        rollback='compatible_forward_correction_only_no_schema_downgrade')
