"""Single finite DB activation actor and immutable forced-command ingress.

Fixed installed CLI and endpoint consumers authenticate every native ingress.
A lost acknowledgement never authorizes redispatch or a new actor.
"""
import contextlib
import datetime as dt
import os
from pathlib import Path
import time
import uuid
import shlex
import stat

from . import activation as a,activation_transition as t,activation_controller as controller,activation_readiness as readiness
from . import baseline,bundle,entry,guard,health,preservation,hydrate

INGRESS='careertrellis.retention-activation-ingress/v1'
CLAIM='careertrellis.retention-activation-actor-dispatch/v1'
OWNER='careertrellis.retention-activation-actor-owner/v1'
_TERMINAL_SEAL=object()
WRITE_PATHS=('/run','/run/lock','/etc/careertrellis/onedrive-retention',
    '/var/lib/careertrellis/onedrive-retention','/var/backups/careertrellis')
PROFILE={'Slice':'system.slice','Type':'exec','User':'root','Group':'root','UMask':'0077',
    'Restart':'no','RemainAfterExit':'yes','RuntimeMaxSec':'30min','TimeoutStartSec':'30',
    'TimeoutStopSec':'30','KillMode':'control-group','SendSIGKILL':'yes','PrivateNetwork':'no',
    'NoNewPrivileges':'yes','ProtectSystem':'strict','ProtectHome':'yes','PrivateTmp':'yes',
    'DevicePolicy':'closed','DeviceAllow':'','RestrictAddressFamilies':'AF_INET AF_UNIX',
    'CapabilityBoundingSet':'CAP_CHOWN CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH CAP_FOWNER CAP_SETGID CAP_SETUID',
    'AmbientCapabilities':'CAP_SETUID','ReadWritePaths':' '.join(WRITE_PATHS),
    'MemoryMax':'256M','TasksMax':'64','LimitFSIZE':'1048576','StandardOutput':'null','StandardError':'null'}
HOOKS=('ExecCondition','ExecStartPre','ExecStartPost','ExecReload','ExecStop','ExecStopPost')


def _now():return dt.datetime.now(dt.timezone.utc)
def _sha(value):return t.digest(value)
def _unit(tx):return 'careertrellis-onedrive-activation@'+tx+'.service'
def _command(tx):return ['/usr/bin/python3','-I','-B',str(entry.ROOT/'entry.py'),'activation-actor','--transaction',tx]


def _context():
    t.need(Path(__file__).absolute()==entry.ROOT/'activation_actor.py' and 'activation_actor.py' in entry.FILES)
    entry.verify_install();current=baseline.native_context()
    t.need(type(current) is baseline.AuthenticatedContext and current.authenticated and current.role=='db')
    return current


def validate_request(request,context):
    """Claims validation only; native ingress separately proves its caller."""
    a.keys(request,('schema','transaction','policy_sha256','operation','payload'))
    tx=request['transaction'];a.hex_value(tx,32);t.need(tx!='0'*32)
    phase={'retention_activation_prepare':'prepare','retention_activation_commit':'commit'}.get(request['operation'])
    t.need(phase is not None and request['schema']=='careertrellis.paired-maintenance-request/v1'
        and request['policy_sha256']==context.bindings['paired_policy_sha256'])
    fields={'baseline_bindings','installation_receipts','intent_sha256','web_baseline_sha256'}
    if phase=='commit':fields.add('commit_authority_sha256')
    payload=request['payload'];a.keys(payload,fields)
    t.need(payload['baseline_bindings']==context.bindings)
    a.keys(payload['installation_receipts'],('web','db'))
    for value in payload['installation_receipts'].values():a.hex_value(value)
    for key in fields-{'baseline_bindings','installation_receipts'}:a.hex_value(payload[key])
    t.need(len(bundle.canonical(request))<=16384)
    return tx,phase


def _birth(pid):
    raw=Path('/proc/%d/stat'%pid).read_text('ascii')
    return {'pid':pid,'start_ticks':int(raw[raw.rindex(')')+2:].split()[19]),
        'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text('ascii').strip()}


def _endpoint_owner():
    wanted=['/usr/bin/python3','-I','-B',str(entry.PAIRED_ROOT/'entry.py'),'db-endpoint']
    t.need(os.geteuid()==0 and Path('/proc/self/cmdline').read_bytes().split(b'\0')[:-1]
        ==[item.encode() for item in wanted])
    entry._paired_namespace()
    from paired_maintenance import entry as pe
    t.need(Path(pe.__file__).absolute()==entry.PAIRED_ROOT/'entry.py')
    pe.verify_install()
    return _birth(os.getpid())


def _same_endpoint(owner):
    t.need(_birth(owner['pid'])==owner)
    t.need(Path('/proc/%d/cmdline'%owner['pid']).read_bytes().split(b'\0')[:-1]==[
        value.encode() for value in ['/usr/bin/python3','-I','-B',str(entry.PAIRED_ROOT/'entry.py'),'db-endpoint']])
    status=Path('/proc/%d/status'%owner['pid']).read_text('ascii')
    t.need(next(line for line in status.splitlines() if line.startswith('Uid:')).split()[1:]==['0']*4)


def _source():
    return {'paired_entry_sha256':a.sha((entry.PAIRED_ROOT/'entry.py').read_bytes()),
        'actor_sha256':a.sha((entry.ROOT/'activation_actor.py').read_bytes())}


def _absent(path):
    try:path.lstat()
    except FileNotFoundError as error:t.need(error.errno==2)
    else:t.need(False)


def _packet(request,context,owner):
    tx,phase=validate_request(request,context)
    return {'schema':INGRESS,'transaction':tx,'phase':phase,'request':request,'request_sha256':_sha(request),
        'endpoint_owner':owner,'source':_source(),'baseline_sha256':_sha(context.document),
        'baseline_bindings':context.bindings,'issued_at':_now().isoformat(),
        'unit':_unit(tx),'command':_command(tx)}


def _validate_packet(packet,context,tx,phase):
    a.keys(packet,('schema','transaction','phase','request','request_sha256','endpoint_owner','source',
        'baseline_sha256','baseline_bindings','issued_at','unit','command'))
    t.need(packet['schema']==INGRESS and packet['transaction']==tx and packet['phase']==phase
        and validate_request(packet['request'],context)==(tx,phase)
        and packet['request_sha256']==_sha(packet['request']) and packet['source']==_source()
        and packet['baseline_sha256']==_sha(context.document) and packet['baseline_bindings']==context.bindings
        and packet['unit']==_unit(tx) and packet['command']==_command(tx)
        and 0<=(_now()-a.timestamp(packet['issued_at'])).total_seconds()<=120)
    _same_endpoint(packet['endpoint_owner'])


def _material(value,context,tx,intent,review,owner):
    a.keys(value,('transaction','retention_policy_sha256','intent_sha256','documents'))
    t.need(value['transaction']==tx and value['retention_policy_sha256']==context.bindings['standalone_policy_sha256']
        and value['intent_sha256']==_sha(intent))
    docs=value['documents'];a.keys(docs,('commit-authority.json','prepared-web.json','prepare-request-web.json'))
    request=docs['prepare-request-web.json'];receipt=docs['prepared-web.json']
    t.need(request=={'transaction':tx,'role':'web','phase':'prepare','intent_sha256':_sha(intent),
        'prepare_deadline':review['prepare_deadline'],'baseline_bindings':context.bindings})
    t.need(receipt=={'transaction':tx,'role':'web','phase':'prepare','intent_sha256':_sha(intent),
        'request_sha256':_sha(request),'result':'prepared'})
    authority=docs['commit-authority.json']
    t.planned_selector(authority,intent,context,review['prepare_deadline'],_now())
    t.need(authority['dispatcher_owner']==owner and authority['prepared']['web']==_sha(receipt))
    _raw,db=t._private(t.REVIEW_ROOT/tx/'prepared-db.json')
    t.need(authority['prepared']['db']==_sha(db))
    return docs


def submit_material(raw):
    """Authenticated forced-command transfer to the original live actor.

    This is not a replacement for the strict prepare/commit request. No local
    preparation receipt is synthesized; the peer's exact bytes are preserved.
    """
    import json
    context=_context();endpoint=_endpoint_owner();t.need(type(raw) is bytes and 0<len(raw)<=16384)
    value=json.loads(raw);t.need(raw==bundle.canonical(value));tx=value['transaction'];a.hex_value(tx,32)
    root=t.REVIEW_ROOT/tx;_absent(a.FINALIZED)
    _raw,started=t._private(root/'actor-owner.json');_require_actor(started,context,tx)
    _raw,intent=t._private(root/'intent.json');_raw,review=t._private(root/'review.json')
    _material(value,context,tx,intent,review,started['owner'])
    packet={'schema':'careertrellis.retention-activation-material-ingress/v1','transaction':tx,
        'value':value,'value_sha256':_sha(value),'endpoint_owner':endpoint,'actor_owner':started['owner'],
        'actor_invocation':started['invocation'],'source':_source(),'baseline_sha256':_sha(context.document),
        'issued_at':_now().isoformat()}
    t._exclusive_marker(root/'material-ingress.json',packet)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        try:
            _raw,ack=t._private(root/'material-accepted.json');_require_actor(started,context,tx)
            t.need(ack=={'schema':'careertrellis.retention-activation-material-accepted/v1',
                'transaction':tx,'material_sha256':_sha(packet),'value_sha256':_sha(value),'actor_owner':started['owner'],
                'actor_invocation':started['invocation']})
            return ack
        except FileNotFoundError:time.sleep(.2)
    raise t.TransitionRefused('activation_actor_requires_reconciliation')


def submit(raw):
    """Fixed forced-command ingress; no supplied context, path or proof grant.

    Prepare dispatches once. Commit only feeds the original still-live actor.
    Callers must reconcile any exception; this function has no retry branch.
    """
    import json
    context=_context();owner=_endpoint_owner()
    t.need(type(raw) is bytes and 0<len(raw)<=16384)
    request=json.loads(raw);t.need(raw==bundle.canonical(request))
    tx,phase=validate_request(request,context);root=t.REVIEW_ROOT/tx
    _absent(a.FINALIZED)
    packet=_packet(request,context,owner)
    _raw,intent=t._private(root/'intent.json');_raw,review=t._private(root/'review.json')
    t.need(_sha(intent)==request['payload']['intent_sha256'] and intent['transaction']==tx
        and review['baseline_sha256']==_sha(context.document)
        and review['installation_receipts']==request['payload']['installation_receipts']
        and review['peer_baseline_sha256']==request['payload']['web_baseline_sha256']
        and _now()<a.timestamp(review['prepare_deadline']))
    if phase=='prepare':
        t.need(a.routing_state() is None)
        raw=hydrate.command(['/usr/bin/systemctl','show',_unit(tx),
            '--property=LoadState,ActiveState,MainPID,ControlPID'],output=True,timeout=10)
        state=dict(line.split('=',1) for line in raw.decode('ascii').splitlines())
        t.need(state=={'LoadState':'not-found','ActiveState':'inactive','MainPID':'0','ControlPID':'0'})
    else:
        _raw,actual=t._private(a.INTENT);t.need(actual==intent)
        _raw,authority=t._private(root/'commit-authority.json')
        t.need(_sha(authority)==request['payload']['commit_authority_sha256'])
        _owner_raw,started=t._private(root/'actor-owner.json');_require_actor(started,context,tx)
    t._exclusive_marker(root/('ingress-'+phase+'.json'),packet)
    if phase=='prepare':
        claim={'schema':CLAIM,'transaction':tx,'ingress_sha256':_sha(packet),'endpoint_owner':owner,
            'unit':_unit(tx),'command':_command(tx),'issued_at':_now().isoformat()}
        t._exclusive_marker(root/'actor-dispatch.json',claim)
        # A timeout or ambiguous systemd outcome leaves both records intact.
        hydrate.command(['/usr/bin/systemd-run','--quiet','--no-block','--unit='+_unit(tx),
            *['--property='+key+'='+value for key,value in PROFILE.items()],*_command(tx)],timeout=30)
    deadline=time.monotonic()+110
    receipt_path=root/('prepared-db.json' if phase=='prepare' else 'committed-db.json')
    while time.monotonic()<deadline:
        try:
            _raw,receipt=t._private(receipt_path)
            _raw,started=t._private(root/'actor-owner.json');_require_actor(started,context,tx,allow_terminal=phase=='commit')
            t.need(receipt['transaction']==tx and receipt['role']=='db' and receipt['phase']==phase
                and receipt['intent_sha256']==_sha(intent)
                and receipt['result']==('prepared' if phase=='prepare' else 'committed'))
            # The peer observation is published before acknowledgement.
            _raw,peer=t._private(root/('peer-state-'+phase+'.json'))
            t.need(peer['prepared_receipt_sha256']==_sha(t._private(root/'prepared-db.json')[1]))
            accepted=readiness.native_activation_state(tx,_sha(intent))
            t.need(accepted['baseline_bindings']==context.bindings
                and accepted['db_baseline_sha256']==_sha(context.document))
            if phase=='commit':
                t._exclusive_marker(root/'commit-response-observed.json',{
                    'schema':'careertrellis.retention-activation-response-observed/v1','transaction':tx,
                    'request_sha256':_sha(request),'receipt_sha256':_sha(receipt),
                    'endpoint_owner':owner,'actor_owner':started['owner'],'actor_invocation':started['invocation']})
            return receipt
        except FileNotFoundError:time.sleep(.2)
    raise t.TransitionRefused('activation_actor_requires_reconciliation')


def _unit_state(tx):
    fields=['ActiveState','SubState','Type','User','Group','MainPID','ControlPID','InvocationID','ExecMainPID',
        'Restart','RemainAfterExit','RuntimeMaxUSec','TimeoutStartUSec','TimeoutStopUSec','KillMode','SendSIGKILL',
        'PrivateNetwork','UMask','Result','ExecMainCode','ExecMainStatus','ControlGroup','Slice','LoadState','Job']
    raw=hydrate.command(['/usr/bin/systemctl','show',_unit(tx),'--property='+','.join(fields)],output=True,timeout=10)
    values=dict(line.split('=',1) for line in raw.decode('ascii').splitlines());t.need(set(values)==set(fields))
    t.need(values['Type']=='exec' and values['User']==values['Group']=='root'
        and values['ControlPID']=='0' and values['Restart']=='no' and values['RemainAfterExit']=='yes'
        and values['RuntimeMaxUSec']=='30min' and values['TimeoutStartUSec']==values['TimeoutStopUSec']=='30s'
        and values['KillMode']=='control-group' and values['SendSIGKILL']=='yes'
        and values['PrivateNetwork']=='no' and values['UMask']=='0077' and values['Slice']=='system.slice'
        and values['LoadState']=='loaded' and values['Job']=='')
    _typed_profile(tx)
    uuid.UUID(values['InvocationID']);return values


def _typed_profile(tx):
    """Inspect actual typed D-Bus properties, never infer omitted arrays."""
    a.hex_value(tx,32)
    path='/org/freedesktop/systemd1/unit/'+''.join(chr(byte) if chr(byte).isalnum() else '_%02x'%byte
        for byte in _unit(tx).encode('ascii'))
    def tokens(name):
        raw=hydrate.command(['/usr/bin/busctl','get-property','org.freedesktop.systemd1',path,
            'org.freedesktop.systemd1.Service',name],output=True,timeout=10)
        t.need(type(raw) is bytes and 0<len(raw)<=16384 and raw.endswith(b'\n') and b'\r' not in raw)
        return shlex.split(raw.decode('ascii'))
    expected={'NoNewPrivileges':['b','true'],'ProtectSystem':['s','strict'],
        'ProtectHome':['s','yes'],'PrivateTmp':['b','true'],'DevicePolicy':['s','closed'],
        'DeviceAllow':['a(ss)','0'],'CapabilityBoundingSet':['t',str(sum(1<<bit for bit in (0,1,2,3,6,7)))],
        'AmbientCapabilities':['t','128'],'MemoryMax':['t',str(256*1024**2)],
        'TasksMax':['t','64'],'LimitFSIZE':['t','1048576'],
        'StandardOutput':['s','null'],'StandardError':['s','null']}
    for name,wanted in expected.items():t.need(tokens(name)==wanted)
    families=tokens('RestrictAddressFamilies')
    t.need(len(families)==5 and families[:3]==['(bas)','true','2'] and set(families[3:])=={'AF_UNIX','AF_INET'})
    paths=tokens('ReadWritePaths')
    t.need(paths[:2]==['as',str(len(WRITE_PATHS))] and len(paths)==2+len(WRITE_PATHS)
        and set(paths[2:])==set(WRITE_PATHS))
    for name in HOOKS:t.need(tokens(name)==['a(sasbttttuii)','0'])


def _require_actor(value,context,tx,allow_terminal=False):
    a.keys(value,('schema','transaction','owner','invocation','claim_sha256','baseline_sha256','source'))
    t.need(value['schema']==OWNER and value['transaction']==tx and value['baseline_sha256']==_sha(context.document)
        and value['source']==_source())
    _raw,claim=t._private(t.REVIEW_ROOT/tx/'actor-dispatch.json');t.need(value['claim_sha256']==_sha(claim))
    state=_unit_state(tx);t.need(state['InvocationID']==value['invocation'])
    if allow_terminal and state['ActiveState']=='active' and state['SubState']=='exited':
        _terminal_state(value,context,tx,state);return
    t.need(state['ActiveState']=='active' and state['SubState']=='running' and state['MainPID']==str(value['owner']['pid'])
        and _birth(value['owner']['pid'])==value['owner']
        and Path('/proc/%d/cgroup'%value['owner']['pid']).read_text('ascii').strip()=='0::/system.slice/'+_unit(tx))


@contextlib.contextmanager
def _fences():
    import fcntl
    descriptors=[]
    try:
        for name in preservation.HISTORICAL_LOCKS:
            fd=os.open(name,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC);descriptors.append(fd)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);preservation.require_owned_lock(name,fd)
        yield dict(zip(preservation.HISTORICAL_LOCKS,descriptors))
    finally:
        for fd in reversed(descriptors):os.close(fd)


def _collect_db_readiness(phase,participant,context,started,descriptors):
    """Fresh immutable role-native proof after the actual phase origin exists."""
    t.need(phase in ('prepare','commit'));tx=participant.transaction;root=participant.root
    t._unchanged(context,_context());_require_actor(started,context,tx)
    review_raw,_review=t._private(root/'review.json');intent_raw,intent=t._private(root/'intent.json')
    observed=guard.native_history_snapshot(tx,'prepare',descriptors[preservation.HISTORICAL_LOCKS[2]])
    t.need(observed['complete'] is True and observed['unresolved']==[])
    entry._paired_namespace()
    from paired_maintenance import entry as pe,runtime,provider,db_host
    pe.verify_install();policy=pe.load_policy();live=runtime.preflight_runtime(policy,'db')
    live.verify_sources();live.authority()
    database=live.state('postgresql@18-main.service')
    t.need(database['ActiveState']=='active' and database['SubState']=='running' and not database['Job'])
    db_host.facts_ok(policy,provider.database_facts())
    pending=hydrate.protected('/etc/careertrellis/authority/pending-cutover')[0].decode('ascii').splitlines()
    t.need([line.partition('=')[2] for line in pending if line.startswith('phase=')]==['activated'])
    ready={'schema':t.READINESS,'transaction':tx,'role':'db','baseline_sha256':_sha(context.document),
        'intent_sha256':_sha(intent),'observed_at':_now().isoformat(),'dispatcher_owner':t._birth(),
        'authority':'postgres-r2-authoritative','application_release':context.document['application_release'],
        'idle':True,'policy_sha256':context.bindings['standalone_policy_sha256'],
        'source_manifest_sha256':context.bindings['standalone_manifest_sha256'],**participant._readiness_binding(phase)}
    t._unchanged(context,_context());_require_actor(started,context,tx)
    t.need(t._private(root/'review.json')[0]==review_raw and t._private(root/'intent.json')[0]==intent_raw)
    return t._exclusive_marker(root/('readiness-'+phase+'.json'),ready)


def run(transaction):
    """One process owns both phases; never restart this actor to resume."""
    context=_context();a.hex_value(transaction,32);tx=transaction;root=t.REVIEW_ROOT/tx
    _absent(a.FINALIZED)
    t.need(Path('/proc/self/cmdline').read_bytes().split(b'\0')[:-1]==[item.encode() for item in _command(tx)])
    _raw,packet=t._private(root/'ingress-prepare.json');_validate_packet(packet,context,tx,'prepare')
    _raw,claim=t._private(root/'actor-dispatch.json')
    t.need(claim=={'schema':CLAIM,'transaction':tx,'ingress_sha256':_sha(packet),
        'endpoint_owner':packet['endpoint_owner'],'unit':_unit(tx),'command':_command(tx),'issued_at':claim['issued_at']})
    state=_unit_state(tx)
    started={'schema':OWNER,'transaction':tx,'owner':_birth(os.getpid()),'invocation':state['InvocationID'],
        'claim_sha256':_sha(claim),'baseline_sha256':_sha(context.document),'source':_source()}
    _require_actor(started,context,tx);t._exclusive_marker(root/'actor-owner.json',started)
    with _fences() as descriptors:
        participant=t.native(tx);engine=controller.NativeController(participant,controller._SEAL)
        def collect(phase,packet):
            _validate_packet(packet,context,tx,phase);t._unchanged(context,_context());_require_actor(started,context,tx)
            origin={'schema':'careertrellis.retention-activation-paired-origin/v1','request':packet['request'],
                'dispatcher_owner':t._birth(),'paired_entry_sha256':_source()['paired_entry_sha256']}
            t._exclusive_marker(root/('paired-'+phase+'-request.json'),origin)
        collect('prepare',packet)
        _raw,intent=t._private(root/'intent.json')
        _collect_db_readiness('prepare',participant,context,started,descriptors)
        participant.prepare();engine.publish_db_peer_state('prepare')
        deadline=time.monotonic()+110
        while time.monotonic()<deadline:
            try:_raw,material=t._private(root/'material-ingress.json');break
            except FileNotFoundError:time.sleep(.2)
        else:raise t.TransitionRefused('activation_actor_requires_reconciliation')
        a.keys(material,('schema','transaction','value','value_sha256','endpoint_owner','actor_owner',
            'actor_invocation','source','baseline_sha256','issued_at'))
        t.need(material['schema']=='careertrellis.retention-activation-material-ingress/v1'
            and material['transaction']==tx and material['value_sha256']==_sha(material['value'])
            and material['actor_owner']==started['owner'] and material['actor_invocation']==started['invocation']
            and material['source']==_source() and material['baseline_sha256']==_sha(context.document)
            and 0<=(_now()-a.timestamp(material['issued_at'])).total_seconds()<=120)
        _same_endpoint(material['endpoint_owner']);_require_actor(started,context,tx)
        _raw,review=t._private(root/'review.json')
        docs=_material(material['value'],context,tx,intent,review,started['owner'])
        for name,value in docs.items():t._exclusive_marker(root/name,value)
        t._exclusive_marker(root/'material-accepted.json',{
            'schema':'careertrellis.retention-activation-material-accepted/v1','transaction':tx,
            'material_sha256':_sha(material),'value_sha256':_sha(material['value']),
            'actor_owner':started['owner'],'actor_invocation':started['invocation']})
        while time.monotonic()<deadline:
            try:_raw,packet=t._private(root/'ingress-commit.json');break
            except FileNotFoundError:time.sleep(.2)
        else:raise t.TransitionRefused('activation_actor_requires_reconciliation')
        collect('commit',packet)
        _collect_db_readiness('commit',participant,context,started,descriptors)
        participant.commit();engine.publish_db_peer_state('commit')
        t._unchanged(context,_context());_require_actor(started,context,tx)
        receipt=t._private(root/'committed-db.json')[1]
        # Keep the original invocation observable through the endpoint ACK;
        # no service-success inference bridges an exit/garbage-collection gap.
        acknowledgement=time.monotonic()+30
        while time.monotonic()<acknowledgement:
            try:
                _raw,response=t._private(root/'commit-response-observed.json')
                t.need(response=={'schema':'careertrellis.retention-activation-response-observed/v1',
                    'transaction':tx,'request_sha256':packet['request_sha256'],'receipt_sha256':_sha(receipt),
                    'endpoint_owner':packet['endpoint_owner'],'actor_owner':started['owner'],
                    'actor_invocation':started['invocation']})
                return receipt
            except FileNotFoundError:time.sleep(.2)
        raise t.TransitionRefused('activation_actor_requires_reconciliation')


def observe(transaction,intent_sha256):
    """Provider-free original actor observation, never dispatch or lock."""
    context=_context();a.hex_value(transaction,32);a.hex_value(intent_sha256)
    root=t.REVIEW_ROOT/transaction
    _raw,intent=t._private(root/'intent.json');t.need(_sha(intent)==intent_sha256)
    _raw,started=t._private(root/'actor-owner.json');_require_actor(started,context,transaction)
    _raw,packet=t._private(root/'ingress-prepare.json')
    # Original endpoint may have exited after its real ACK; no claim to its
    # continued life is made by this observation of the still-live actor.
    t.need(packet['transaction']==transaction and packet['phase']=='prepare'
        and packet['request_sha256']==_sha(packet['request']) and packet['source']==_source()
        and packet['baseline_sha256']==_sha(context.document)
        and validate_request(packet['request'],context)==(transaction,'prepare'))
    _raw,claim=t._private(root/'actor-dispatch.json');t.need(claim['ingress_sha256']==_sha(packet))
    t._unchanged(context,_context());_require_actor(started,context,transaction)
    return {'schema':'careertrellis.retention-activation-actor-state/v1','transaction':transaction,
        'intent_sha256':intent_sha256,'actor_owner':started['owner'],'actor_invocation':started['invocation'],
        'prepare_request_sha256':packet['request_sha256'],'baseline_bindings':context.bindings,
        'baseline_sha256':_sha(context.document),'source':started['source']}


def _empty_cgroup(tx,group):
    expected='/system.slice/'+_unit(tx);t.need(group in ('',expected))
    root=Path('/sys/fs/cgroup')/expected.lstrip('/')
    try:meta=root.lstat()
    except FileNotFoundError as error:
        t.need(error.errno==2)
        return {'path':expected,'present':False,'directories':0,'empty':True}
    t.need(stat.S_ISDIR(meta.st_mode) and not stat.S_ISLNK(meta.st_mode))
    directories=[root]
    for path in root.rglob('*'):
        actual=path.lstat();t.need(not stat.S_ISLNK(actual.st_mode))
        if stat.S_ISDIR(actual.st_mode):directories.append(path)
    for directory in directories:
        raw=(directory/'cgroup.procs').read_bytes()
        t.need(len(raw)<=65536 and not raw.strip())
    return {'path':expected,'present':True,'directories':len(directories),'empty':True}


def _terminal_state(started,context,tx,state=None):
    """Actual original invocation success; no process absence inference alone."""
    if state is None:state=_unit_state(tx)
    t.need(state['ActiveState']=='active' and state['SubState']=='exited'
        and state['MainPID']==state['ControlPID']=='0' and state['Job']==''
        and state['InvocationID']==started['invocation']
        and state['ExecMainPID']==str(started['owner']['pid'])
        and state['Result']=='success' and state['ExecMainCode']=='1' and state['ExecMainStatus']=='0'
        and Path('/proc/sys/kernel/random/boot_id').read_text('ascii').strip()==started['owner']['boot_id'])
    preservation._terminal_birth(started['owner'])
    return {'unit_state':state,'cgroup':_empty_cgroup(tx,state['ControlGroup'])}


class AuthenticatedActorTerminal:
    __slots__=('_raw','_bindings','_seal')
    def __init__(self,value,bindings,seal=None):
        t.need(seal is _TERMINAL_SEAL)
        self._raw=bundle.canonical(value);self._bindings=bundle.canonical(bindings);self._seal=seal
    @property
    def authenticated(self):return self._seal is _TERMINAL_SEAL
    @property
    def value(self):
        import json
        return json.loads(self._raw)
    @property
    def digest(self):return a.sha(self._raw)
    @property
    def bindings(self):
        import json
        return json.loads(self._bindings)
    @property
    def role(self):return 'db'


def inspect_terminal(transaction,intent_sha256):
    """Read-only independent actual terminal inspection; no receipt is written."""
    context=_context();observer=_endpoint_owner();a.hex_value(transaction,32);a.hex_value(intent_sha256)
    tx=transaction;root=t.REVIEW_ROOT/tx
    _raw,intent=t._private(root/'intent.json');t.need(_sha(intent)==intent_sha256)
    _raw,started=t._private(root/'actor-owner.json')
    t.need(started['owner']['pid']!=observer['pid'])
    # _require_actor binds the protected claim/source and actual original unit.
    _require_actor(started,context,tx,allow_terminal=True)
    actual=_terminal_state(started,context,tx)
    _raw,commit=t._private(root/'committed-db.json')
    _raw,response=t._private(root/'commit-response-observed.json')
    _raw,packet=t._private(root/'ingress-commit.json')
    t.need(commit['transaction']==tx and commit['role']=='db' and commit['phase']=='commit'
        and commit['intent_sha256']==intent_sha256 and commit['result']=='committed'
        and response=={'schema':'careertrellis.retention-activation-response-observed/v1','transaction':tx,
            'request_sha256':packet['request_sha256'],'receipt_sha256':_sha(commit),
            'endpoint_owner':packet['endpoint_owner'],'actor_owner':started['owner'],
            'actor_invocation':started['invocation']})
    value={'schema':'careertrellis.retention-activation-actor-terminal/v1','transaction':tx,
        'intent_sha256':intent_sha256,'baseline_bindings':context.bindings,'baseline_sha256':_sha(context.document),
        'source':_source(),'actor_owner':started['owner'],'actor_invocation':started['invocation'],
        'unit':_unit(tx),'claim_sha256':started['claim_sha256'],'commit_receipt_sha256':_sha(commit),
        'response_sha256':_sha(response),'terminal':actual,'observed_at':_now().isoformat(),'observer_owner':observer}
    t._unchanged(context,_context());t.need(_terminal_state(started,context,tx)==actual)
    return AuthenticatedActorTerminal(value,context.bindings,_TERMINAL_SEAL)


def terminal_status(transaction,intent_sha256):
    """Provider-free read-only polling of the same actual original invocation."""
    context=_context();_endpoint_owner();a.hex_value(transaction,32);a.hex_value(intent_sha256)
    root=t.REVIEW_ROOT/transaction
    _raw,intent=t._private(root/'intent.json');t.need(_sha(intent)==intent_sha256)
    _raw,started=t._private(root/'actor-owner.json')
    _require_actor(started,context,transaction,allow_terminal=True)
    state=_unit_state(transaction)
    value={'schema':'careertrellis.retention-activation-actor-terminal-status/v1','transaction':transaction,
        'intent_sha256':intent_sha256,'baseline_bindings':context.bindings,'baseline_sha256':_sha(context.document),
        'source':started['source'],'actor_owner':started['owner'],'actor_invocation':started['invocation'],
        'observed_at':_now().isoformat(),'state':'running','terminal':None}
    if state['ActiveState']=='active' and state['SubState']=='running':
        _require_actor(started,context,transaction)
    else:
        proof=inspect_terminal(transaction,intent_sha256)
        t.need(type(proof) is AuthenticatedActorTerminal and proof.authenticated and proof.bindings==context.bindings)
        value['state']='terminal';value['terminal']=proof.value
    t._unchanged(context,_context());return value


def observe_terminal(transaction,intent_sha256):
    """Explicitly seal an actual independent terminal inspection once."""
    proof=inspect_terminal(transaction,intent_sha256);value=proof.value
    root=t.REVIEW_ROOT/transaction
    path=root/'actor-terminal.json'
    try:
        _raw,existing=t._private(path)
    except FileNotFoundError as error:
        t.need(error.errno==2);t._exclusive_marker(path,value)
    else:
        # Re-observe actual state; immutable first observation is never refreshed.
        for key in value.keys()-{'observed_at','observer_owner'}:t.need(existing[key]==value[key])
        a.keys(existing,value.keys());value=existing
    repeated=inspect_terminal(transaction,intent_sha256)
    for key in value.keys()-{'observed_at','observer_owner'}:t.need(repeated.value[key]==value[key])
    return AuthenticatedActorTerminal(value,proof.bindings,_TERMINAL_SEAL)


def cleanup_terminal(transaction,intent_sha256):
    """Explicit finalized cleanup only; one exact retained empty unit stop.

    Existing cleanup intent, including an uncertain previous stop, refuses.
    This method does not remove any receipt, lock, data, or unit definition.
    """
    context=_context();observer=_endpoint_owner()
    proof=observe_terminal(transaction,intent_sha256);root=t.REVIEW_ROOT/transaction
    selected=a.require_activated(context);t.need(selected.value['transaction']==transaction)
    _raw,finalized=t._private(a.FINALIZED)
    t.need(finalized['intent_sha256']==intent_sha256
        and finalized['committed_receipts']['db']==proof.value['commit_receipt_sha256'])
    request={'schema':'careertrellis.retention-activation-actor-cleanup/v1','transaction':transaction,
        'intent_sha256':intent_sha256,'unit':_unit(transaction),'actor_terminal_sha256':proof.digest,
        'actor_owner':proof.value['actor_owner'],'actor_invocation':proof.value['actor_invocation'],
        'finalized_sha256':_sha(finalized),'observer_owner':observer,'issued_at':_now().isoformat()}
    t._exclusive_marker(root/'actor-cleanup-intent.json',request)
    # Recheck all immutable references and actual typed-empty hooks immediately
    # before the one mutation. No supplied proof can reach this boundary.
    repeated=observe_terminal(transaction,intent_sha256);t.need(repeated.digest==proof.digest)
    t.need(_sha(t._private(a.FINALIZED)[1])==request['finalized_sha256'])
    a.require_activated(context);t._unchanged(context,_context())
    hydrate.command(['/usr/bin/systemctl','stop',_unit(transaction)],timeout=30)
    raw=hydrate.command(['/usr/bin/systemctl','show',_unit(transaction),
        '--property=LoadState,ActiveState,SubState,MainPID,ControlPID,Job,InvocationID,ExecMainPID,Result'],output=True,timeout=10)
    state=dict(line.split('=',1) for line in raw.decode('ascii').splitlines())
    t.need(set(state)=={'LoadState','ActiveState','SubState','MainPID','ControlPID','Job','InvocationID','ExecMainPID','Result'}
        and state['LoadState'] in ('loaded','not-found') and state['ActiveState']=='inactive'
        and state['SubState']=='dead' and state['MainPID']==state['ControlPID']=='0' and state['Job']=='')
    if state['LoadState']=='loaded':
        t.need(state['InvocationID']==proof.value['actor_invocation']
            and state['ExecMainPID']==str(proof.value['actor_owner']['pid']) and state['Result']=='success')
    empty=_empty_cgroup(transaction,'')
    t._unchanged(context,_context())
    receipt={'schema':'careertrellis.retention-activation-actor-cleaned/v1','transaction':transaction,
        'cleanup_intent_sha256':_sha(request),'actor_terminal_sha256':proof.digest,'unit':_unit(transaction),
        'post_stop':state,'cgroup':empty,'completed_at':_now().isoformat()}
    t._exclusive_marker(root/'actor-cleanup-complete.json',receipt)
    return receipt
