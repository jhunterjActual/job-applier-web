"""Attended, disabled-v2 provider handoff; no provider calls or service starts.

Preparation reads current files before prompting. The sealed bundle is private
recovery material, never a portable receipt or independent provider attestation.
"""
import base64
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat

import admission_operations as ops
import identity_configuration as configuration
import identity_operator as operator
import session_v2_transition as transition
import totp_configuration

PREFIX='CAREERTRELLIS_AUTH_V2_'
MUTABLE=('staging.env','auth-v2.env','admission-web.json')
FILES=tuple(dict.fromkeys((*MUTABLE,'postgres-runtime.env','postgres-migration.env',
    'object-storage.env',*ops.WORKER_FILES)))
CURRENT=Path('/opt/careertrellis/staging/current')
RELEASES=CURRENT.parent/'releases'
ROTATION=Path('/run/lock/careertrellis-provider-secret-rotation.lock')


@contextmanager
def locks():
    import fcntl
    with ops.host_locks():
        fd=os.open(ROTATION,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            item=os.fstat(fd)
            ops.require(stat.S_ISREG(item.st_mode) and item.st_uid==0 and item.st_nlink==1)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
        finally: os.close(fd)


def inventory():
    # Require installed units and the deployment's authority start guard to
    # match this reviewed bundle. Do not compare dynamic PID/timestamp fields.
    result={}
    for name in ops.SERVICES:
        def prop(key):
            return ops.command(['/usr/bin/systemctl','show',name,'--property='+key,'--value']).decode().strip()
        dropin=Path('/etc/systemd/system')/(name+'.d/hosted-authority.conf')
        ops.require(prop('NeedDaemonReload')=='no' and prop('DropInPaths')==dropin.as_posix()
            and prop('PassEnvironment')=='')
        fragment=Path(prop('FragmentPath'))
        ops.require(fragment in (Path('/etc/systemd/system')/name,Path('/usr/lib/systemd/system')/name))
        expected=ops.read_protected(Path(__file__).absolute().parent.parent/'deploy/systemd'/name,modes=(0o600,0o640,0o644))
        ops.require(ops.read_protected(fragment,modes=(0o600,0o640,0o644))==expected)
        guard=ops.read_protected(Path(__file__).absolute().parent.parent/'deploy/systemd'/(name+'.d/hosted-authority.conf'),modes=(0o600,0o640,0o644))
        ops.require(ops.read_protected(dropin,modes=(0o600,0o640,0o644))==guard)
        files=[line.split('=',1)[1] for line in expected.decode().splitlines() if line.startswith('EnvironmentFile=')]
        wanted=' '.join(path.lstrip('-')+' (ignore_errors='+('yes' if path.startswith('-') else 'no')+')' for path in files)
        ops.require(' '.join(prop('EnvironmentFiles').splitlines())==wanted and prop('Environment')=='PYTHONDONTWRITEBYTECODE=1')
        result[name]={'fragment':expected.decode(),'authority_guard':guard.decode()}
    return result


def read_files():
    return {name:ops.read_protected(ops.ROOT/name).decode('utf-8') for name in FILES}


def validate_files(files):
    from cryptography.fernet import Fernet
    from workos_transport_candidate import ProviderSettings
    ops.require(set(files)==set(FILES))
    values={name:ops.assignments(raw.encode()) for name,raw in files.items() if name.endswith('.env')}
    staging,auth=values['staging.env'],values['auth-v2.env']
    required={PREFIX+k for k in ('ENABLED','ISSUER','GENERATION','KEY_ID','SESSION_SECONDS','ENVELOPE_KEY','FLOW_KEY')}|{'CAREERTRELLIS_ADMISSION_DATABASE_URL'}
    ops.require(required <= set(auth) <= required|{PREFIX+'AUDIENCE'}|totp_configuration.FIELDS
        and auth[PREFIX+'ENABLED']=='0' and auth[PREFIX+'SESSION_SECONDS']=='3600')
    for name,v in values.items():
        if name!='auth-v2.env':
            ops.require(not any(k.startswith(PREFIX) or k=='CAREERTRELLIS_ADMISSION_DATABASE_URL' for k in v))
        if name not in ('staging.env','auth-v2.env'):
            ops.require(not any(k.startswith('WORKOS_') or k in ('CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID',
                'CAREERTRELLIS_AUTH_MODE','CAREERTRELLIS_IDENTITY_STORE') for k in v))
    keys=[auth[PREFIX+'ENVELOPE_KEY'],auth[PREFIX+'FLOW_KEY'],staging['WORKOS_COOKIE_PASSWORD']]
    for key in keys: Fernet(key.encode())
    ops.require(len({base64.urlsafe_b64decode(key) for key in keys})==3)
    totp_configuration.validate(auth,keys)
    metadata={'issuer':auth[PREFIX+'ISSUER'],'generation':auth[PREFIX+'GENERATION'],
        'key_id':auth[PREFIX+'KEY_ID'],'session_seconds':3600}
    if PREFIX+'AUDIENCE' in auth: metadata['audience']=auth[PREFIX+'AUDIENCE']
    ops.require(ops.strict_json(files['admission-web.json'].encode())==metadata)
    provider=ProviderSettings(staging['WORKOS_CLIENT_ID'],staging['WORKOS_API_KEY'],
        metadata['issuer'],metadata['generation'],metadata.get('audience'))
    ops.require(re.fullmatch('user_[A-Za-z0-9_]{1,120}',staging['CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID']))
    return provider,staging,auth


def replace_fields(raw,changes):
    # Preserve comments, ordering and every unrelated byte. Optional audience is
    # the only removable/addable assignment; values use the strict env grammar.
    old=ops.assignments(raw.encode())
    for key,value in changes.items():
        if value is not None: ops.require(ops.assignments((key+'='+value+'\n').encode())=={key:value})
        if key in old:
            raw,count=re.subn(r'(?m)^'+key+r'=[^\r\n]*(?:\r?\n|$)',
                lambda m: '' if value is None else key+'='+value+('\r\n' if m[0].endswith('\r\n') else '\n'),raw)
            ops.require(count==1)
        else:
            ops.require(key==PREFIX+'AUDIENCE')
            if value is not None: raw+=('' if raw.endswith('\n') else '\n')+key+'='+value+'\n'
    return raw


def candidate(p,activation,files,ask):
    source,staging,auth=validate_files(files)
    totp_configuration.validate(auth,[auth[PREFIX+'ENVELOPE_KEY'],auth[PREFIX+'FLOW_KEY'],
        staging['WORKOS_COOKIE_PASSWORD']],activation_schema=activation['schema'])
    ops.require(source.binding==p['baseline_binding'])
    ops.require(ask('Confirm you started this in your visible secure terminal: type VISIBLE')=='VISIBLE')
    ops.require(ask('Exact independently approved configuration operation UUID')==p['operation'])
    ops.require(ask('Confirm exact provider migration, account mapping and configuration authority: type AUTHORIZED')=='AUTHORIZED')
    target=dict(files)
    if p['mode']=='switch_environment':
        client=ask('Approved target WorkOS client ID')
        secret=ask('Approved target WorkOS API key')
        owner=ask('Independently verified target owner WorkOS user ID')
        hostname=ask('Verified stable production managed AuthKit hostname')
        ops.require(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.authkit\.(?:com|app)',hostname))
        ops.require(ask('Final domain, callbacks, logout, OAuth, MFA and account mappings verified for this target: type VERIFIED')=='VERIFIED')
        target['staging.env']=replace_fields(files['staging.env'],{
            'WORKOS_CLIENT_ID':client,'WORKOS_API_KEY':secret,'CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID':owner})
        issuer='https://api.workos.com/user_management/'+client
        audience=ask('Approved target audience, or NONE')
        audience=None if audience=='NONE' else audience
        target['auth-v2.env']=replace_fields(files['auth-v2.env'],{
            PREFIX+'ISSUER':issuer,PREFIX+'GENERATION':activation['realm'],PREFIX+'AUDIENCE':audience})
        metadata={'issuer':issuer,'generation':activation['realm'],'key_id':auth[PREFIX+'KEY_ID'],'session_seconds':3600}
        if audience is not None: metadata['audience']=audience
        target['admission-web.json']=ops.encoded(metadata).decode()
    provider,staging,_=validate_files(target)
    ops.require(provider.binding==p['target_binding'] and provider.generation==activation['realm']
        and transition.owner_proof(p['operation'],provider.binding,staging['CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID'])==p['owner_proof'])
    ops.require(ask('Save encrypted candidate only, without changing live files: type SAVE')=='SAVE')
    return target


def paths(p):
    ops.protected_parents(ops.STATE/'sentinel')
    item=ops.STATE.lstat()
    ops.require(stat.S_ISDIR(item.st_mode) and item.st_uid==0 and stat.S_IMODE(item.st_mode)==0o700)
    stem='provider-config-'+p['operation']+'-'+hashlib.sha256(ops.encoded(p)).hexdigest()
    return ops.STATE/(stem+'.sealed'),ops.STATE/(stem+'.json')


def plans(source):
    return plan_snapshot(source)[:2]


def plan_snapshot(source):
    import identity_plan_store
    snapshot,pair=identity_plan_store.load()
    p,activation=pair['provider'],pair['activation']
    ops.require(source.parent.parent.name==p['operations'])
    return p,activation,snapshot


def check_selection(selection):
    if selection is not None:
        import identity_plan_store
        identity_plan_store.require_current(selection)


def prepare(p,activation,*,selection=None):
    from cryptography.fernet import Fernet
    from identity_entry import SecureTerminal
    sealed,receipt=paths(p)
    ops.require(not any(x.exists() or x.is_symlink() for x in (sealed,receipt)))
    ops.require(CURRENT.resolve()==RELEASES/p['source_application'])
    before=read_files(); units=inventory(); boot=transition.boot()
    _,_,auth=validate_files(before)
    # No host locks or DB connections are held during human input.
    with SecureTerminal() as terminal:
        after=candidate(p,activation,before,lambda prompt:terminal.ask(prompt,limit=512))
    packet={'plan':p,'activation':activation,'boot':boot,'before':before,'after':after,'units':units}
    payload=ops.encoded(packet)
    ops.require(len(payload)<=380000)
    with locks():
        check_selection(selection)
        ops.require(CURRENT.resolve()==RELEASES/p['source_application'] and transition.boot()==boot
            and read_files()==before and inventory()==units and not receipt.exists() and not receipt.is_symlink())
        ops.write_protected(sealed,Fernet(auth[PREFIX+'ENVELOPE_KEY'].encode()).encrypt(payload))
    return {'outcome':'encrypted_candidate_saved_no_configuration_changed'}


@contextmanager
def committed_guard(p,activation,engine):
    from sqlalchemy import text
    with operator.secure_connection(engine) as c:
        operator.setup(c)
        c.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-admission-maintenance-v1',0))")
        c.exec_driver_sql('SELECT careertrellis_admission.lock_lifecycle()')
        digest=hashlib.sha256(ops.encoded(p)).hexdigest()
        ops.require(c.scalar(text('SELECT EXISTS(SELECT 1 FROM careertrellis_admission.identity_configuration_receipts r '
            'JOIN careertrellis_admission.policy p ON p.generation=r.generation WHERE r.operation=CAST(:op AS uuid) '
            'AND r.plan_digest=:digest AND p.maintenance AND NOT p.signup_open)'),{'op':p['operation'],'digest':digest}) is True)
        ops.require(c.scalar(text("SELECT count(*)=4 AND bool_and(NOT rolcanlogin) FROM pg_roles WHERE rolname IN "
            "('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker')")) is True)
        c.exec_driver_sql(ops.assert_quiescent_sql())
        c.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-alembic-migrations-v1',0))")
        # Schema-owner access is needed for Alembic, admission-owner for the
        # private identity ledger. Do not broaden either role's grants.
        c.exec_driver_sql('RESET ROLE')
        operator.setup(c,expected_schema=transition.schema(activation))
        ops.require(c.scalar(text("SELECT (SELECT binding FROM careertrellis_admission.identity_provider)=:binding "
            "AND (SELECT count(*) FROM careertrellis.app_users)<=:cap AND EXISTS("
            "SELECT 1 FROM careertrellis_admission.identity_accounts a "
            "JOIN careertrellis_admission.identity_history h ON h.account_id=a.id AND h.binding=a.binding AND h.subject=a.subject AND h.state='active' "
            "JOIN careertrellis.app_users u ON u.id=a.id AND u.role=a.role AND u.workos_user_id=a.subject "
            "WHERE a.role='owner' AND NOT a.held AND NOT a.disabled AND u.disabled_at IS NULL "
            "AND NOT careertrellis_admission.identity_erasing(a.id) AND a.binding=:binding "
            "AND encode(sha256(convert_to(:operation||chr(10)||a.binding||chr(10)||a.subject,'UTF8')),'hex')=:proof)"),
            {'binding':activation['provider_binding'],'cap':activation['cap'],'operation':p['operation'],'proof':activation['owner_proof']}) is True)
        yield


def publish(p,activation,engine,*,reconcile=False,selection=None):
    from cryptography.fernet import Fernet
    sealed,receipt=paths(p)
    with locks():
        check_selection(selection)
        ops.stopped_services()
        ops.require(CURRENT.resolve()==RELEASES/p['application'])
        current=read_files()
        auth=ops.assignments(current['auth-v2.env'].encode())
        cipher=Fernet(auth[PREFIX+'ENVELOPE_KEY'].encode())
        packet=ops.strict_json(cipher.decrypt(ops.read_protected(sealed,limit=524288)))
        ops.require(set(packet)=={'plan','activation','boot','before','after','units'} and packet['plan']==p
            and packet['activation']==activation and (reconcile or packet['boot']==transition.boot()) and packet['units']==inventory())
        before,after=packet['before'],packet['after']
        source,_,_=validate_files(before); target,_,_=validate_files(after)
        ops.require(source.binding==p['baseline_binding'] and target.binding==p['target_binding'])
        ops.require(all(current[k] in (before[k],after[k]) for k in FILES))
        expected={'phase':'configured_services_stopped','operation':p['operation'],
            'application':p['application'],'operations':p['operations']}
        if receipt.exists(): ops.require(ops.strict_json(ops.read_protected(receipt))==expected and current==after)
        # A lost DB acknowledgement leaves the exact sealed candidate. A retry
        # consults its durable database receipt; it never remaps a second time.
        # Explicit reconciliation never submits a database mutation. It can
        # finish after reboot only when the exact durable DB commit still passes
        # the fresh policy/owner/fence checks below.
        if not reconcile:
            configuration.apply(ops.encoded(p),engine,verified_current_binding=source.binding,
                expected_schema=transition.schema(activation))
        with committed_guard(p,activation,engine):
            ops.stopped_services()
            ops.require(inventory()==packet['units'] and read_files()==current)
            for name in MUTABLE:
                if current[name]!=after[name]:
                    ops.write_protected(ops.ROOT/name,after[name].encode(),previous=current[name].encode())
            ops.require(read_files()==after)
            transition.web_enable(activation,preflight=True)
            ops.stopped_services()
            if not receipt.exists(): ops.write_protected(receipt,ops.encoded(expected))
    return {'outcome':'configuration_complete_services_stopped'}


def run(action,source):
    try:
        p,activation,selection=plan_snapshot(source)
        if action=='enter': result=prepare(p,activation,selection=selection)
        elif action=='preflight': result=prepared_preflight(p,activation,selection)
        else:
            from sqlalchemy import create_engine
            from sqlalchemy.pool import NullPool
            ops.require(action in ('apply','reconcile'))
            engine=create_engine(ops.migration_connection_url(operator.DATABASE),poolclass=NullPool,
                hide_parameters=True,connect_args={'connect_timeout':5})
            try: result=publish(p,activation,engine,reconcile=action=='reconcile',selection=selection)
            finally: engine.dispose()
        print(ops.encoded(result).decode().strip())
        return 0
    except Exception:
        raise ops.OperationError(ops.ERROR) from None


def prepared_preflight(p,activation,selection):
    """Advance check of the sealed target while the source remains current."""
    from cryptography.fernet import Fernet
    with locks():
        check_selection(selection)
        ops.require(CURRENT.resolve()==RELEASES/p['source_application'])
        current=read_files(); _,_,auth=validate_files(current)
        sealed,_=paths(p)
        candidate=ops.strict_json(Fernet(auth[PREFIX+'ENVELOPE_KEY'].encode()).decrypt(ops.read_protected(sealed,limit=524288)))
        ops.require(set(candidate)=={'plan','activation','boot','before','after','units'}
            and candidate['plan']==p and candidate['activation']==activation
            and candidate['boot']==transition.boot() and candidate['before']==current
            and candidate['units']==inventory())
        provider,_,values=validate_files(candidate['after'])
        ops.require(provider.binding==p['target_binding'] and values[PREFIX+'ENABLED']=='0')
        transition.validate_web_inputs(activation,{name:candidate['after'][name].encode() for name in transition.WEB_INPUTS})
    return {'outcome':'sealed_candidate_configuration_valid_source_unchanged'}
