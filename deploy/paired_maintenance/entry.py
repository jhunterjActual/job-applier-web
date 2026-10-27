"""Fixed host entry points, protected bootstrap and private/public policy binding."""
import datetime as dt
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time

INSTALL_ROOT = Path('/usr/local/libexec/careertrellis/paired_maintenance')
ROOT = Path('/var/lib/careertrellis/paired-maintenance')
PRIVATE_POLICY = Path('/etc/careertrellis/paired-maintenance/policy.json')
PUBLIC_POLICY = ROOT / 'public-policy.json'
PUBLIC_MARKER = ROOT / 'writer-interlock.json'
FILES = {'__init__.py', 'common.py', 'runtime.py', 'provider.py', 'client.py',
         'ssh_gate.py', 'db_control.py', 'web_control.py', 'db_host.py', 'host.py', 'entry.py'}
WEB_HOST = 'careertrellis-staging-web-1'
DB_HOST = 'job-applier-db-staging-01'
RESPONSE_SCHEMA = 'careertrellis.paired-maintenance-response/v1'


class EntryRefused(RuntimeError):
    pass


def _need(value):
    if not value:
        raise EntryRefused('entry_refused')


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result)
            result[key] = value
        return result
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    _need(type(value) is dict)
    return value


def _protected_read(path, modes, maximum=131072):
    path = Path(path)
    for parent in path.parents:
        info = os.lstat(parent)
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    before = os.lstat(path)
    _need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
          and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) in modes
          and 0 < before.st_size <= maximum)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                         | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        opened = os.fstat(descriptor)
        _need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino))
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        after = os.lstat(path)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                                 info.st_gid, info.st_nlink, info.st_size,
                                 info.st_mtime_ns, info.st_ctime_ns)
        _need(identity(before) == identity(opened) == identity(after) and len(raw) == before.st_size)
        return raw
    finally:
        os.close(descriptor)


def verify_install():
    """Only stdlib code executes before installed package byte verification."""
    info = os.lstat(INSTALL_ROOT)
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0
          and stat.S_IMODE(info.st_mode) == 0o755)
    manifest = _decode(_protected_read(INSTALL_ROOT / 'manifest.json', {0o644}))
    _need(set(manifest) == {'schema', 'revision', 'files'}
          and manifest['schema'] == 'careertrellis.paired-maintenance-install/v1'
          and isinstance(manifest['revision'], str)
          and re.fullmatch('[0-9a-f]{40}', manifest['revision'])
          and type(manifest['files']) is dict and set(manifest['files']) == FILES)
    for name in sorted(FILES):
        expected = manifest['files'][name]
        _need(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected))
        raw = _protected_read(INSTALL_ROOT / name, {0o644}, 2 * 1024**2)
        _need(hashlib.sha256(raw).hexdigest() == expected)
    return manifest['revision']


def _module(name):
    package = __package__ or 'paired_maintenance'
    return importlib.import_module(package + '.' + name)


def load_policy():
    common = _module('common')
    private = common.Policy(_decode(_protected_read(PRIVATE_POLICY, {0o600, 0o644})))
    public = common.Policy(_decode(_protected_read(PUBLIC_POLICY, {0o644})))
    _need(private.digest == public.digest)
    revision = private.data.get('manifest_revision')
    if revision is not None:
        _need(revision == verify_install())
    return private


def _boot():
    path = Path('/proc/sys/kernel/random/boot_id')
    for parent in path.parents:
        info = os.lstat(parent)
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    info = os.lstat(path)
    _need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    with path.open('rb') as stream:
        raw = stream.read(128)
    value = raw.decode('ascii').strip()
    _need(re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value))
    return value


def _release():
    # The mutable pointer is read, never moved by this operational bundle.
    pointer = Path('/opt/careertrellis/staging/current')
    for parent in pointer.parents:
        info = os.lstat(parent)
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    info = os.lstat(pointer)
    _need(stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0)
    current = pointer.resolve(strict=True)
    _need(current.parent == Path('/opt/careertrellis/staging/releases')
          and re.fullmatch('[0-9a-f]{40}', current.name))
    return current.name


def writer_gate():
    try:
        common = _module('common')
        policy = common.Policy(_decode(_protected_read(PUBLIC_POLICY, {0o644})))
        marker = _decode(_protected_read(PUBLIC_MARKER, {0o644}))
        return 0 if common.validate_gate(policy, marker, _boot(), _release()) else 1
    except Exception:
        return 1


class NoStore:
    def exists(self, name):
        return False

    def read(self, name):
        raise EntryRefused('entry_refused')

    def write_once(self, name, value):
        raise EntryRefused('entry_refused')


def _control_lock():
    import fcntl
    path = ROOT / 'db' / 'control.lock'
    for parent in path.parents:
        info = os.lstat(parent)
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        _need(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
              and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _transaction_store(policy, request):
    common = _module('common')
    path = ROOT / 'db' / 'runs' / request['transaction']
    try:
        os.lstat(path)
    except FileNotFoundError:
        if request['operation'] in _module('db_control').READ_ONLY:
            return NoStore()
        _need(request['operation'] == 'begin')
        policy.require_new_start(dt.datetime.now(dt.timezone.utc))
        # Backend claims the weekly slot and rejects other active transactions
        # before dispatch; entry creates only the empty, bounded journal root.
        parent = path.parent
        info = os.lstat(parent)
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0
              and stat.S_IMODE(info.st_mode) == 0o700)
        os.mkdir(path, 0o700)
    return common.Journal(path)


def _retention_activation_evidence(retention_entry,request,retention_policy,context):
    """Fixed read-only native collector, never caller-provided recovery facts."""
    activation=retention_entry._load('activation')
    selector=activation.require_activated(context)
    _need(selector.role=='db' and selector.bindings==context.bindings
          and request['payload']['retention_policy_sha256']==retention_policy.digest
          and request['payload']['activation_sha256']==selector.digest)
    readiness=retention_entry._load('activation_readiness')
    catalog=retention_entry._load('catalog')
    with _module('provider').repository_lock() as (fd,_identity):
        files=catalog.NativeFiles(fd)
        # The fixed readiness factory uses an existing-only NativeReadStore;
        # missing catalog state is refusal, never initialization by this RPC.
        files.require_repository_lock()
        evidence=readiness.native_replacements(fd)
        readiness.validate_evidence(evidence,context.bindings,context.revision,
            context.document['application_release'],dt.datetime.now(dt.timezone.utc))
        current_backup=readiness.native_current_backup(fd)
        files.require_repository_lock()
    current=activation.require_activated(retention_entry._native_dependencies(retention_entry.verify_install(),'db'))
    _need(current.digest==selector.digest and current.bindings==context.bindings)
    return {'schema':'careertrellis.onedrive-retention-scheduled-eligibility/v1',
        'transaction':request['transaction'],'retention_policy_sha256':retention_policy.digest,
        'activation_sha256':selector.digest,'replacement_evidence':evidence,'current_backup':current_backup}


def _retention_bootstrap_evidence(retention_entry,request,retention_policy,context):
    """First-activation evidence only while BOTH fixed markers remain absent."""
    activation=retention_entry._load('activation')
    _need(activation.routing_state() is None
          and request['payload']['retention_policy_sha256']==retention_policy.digest
          and retention_policy.digest==context.bindings['standalone_policy_sha256'])
    health=retention_entry._load('health')
    _plan,installed=health._installation(context)
    _need(type(installed) is str and re.fullmatch('[0-9a-f]{64}',installed)
          and installed!='0'*64 and installed==context.document['acceptance_receipts']['ops'])
    readiness=retention_entry._load('activation_readiness')
    catalog=retention_entry._load('catalog')
    with _module('provider').repository_lock() as (fd,_identity):
        files=catalog.NativeFiles(fd);files.require_repository_lock()
        evidence=readiness.native_replacements(fd)
        readiness.validate_evidence(evidence,context.bindings,context.revision,
            context.document['application_release'],dt.datetime.now(dt.timezone.utc))
        files.require_repository_lock()
    current=retention_entry._native_dependencies(retention_entry.verify_install(),'db')
    _need(current.document==context.document and current.bindings==context.bindings
          and activation.routing_state() is None)
    return {'schema':'careertrellis.onedrive-retention-bootstrap-evidence/v1',
        'transaction':request['transaction'],'retention_policy_sha256':retention_policy.digest,
        'db_install_receipt_sha256':installed,'db_baseline_sha256':hashlib.sha256(
            retention_entry.canonical(context.document)).hexdigest(),'replacement_evidence':evidence}


def _retention_activation_bridge(retention_entry,request,retention_policy,context):
    """Fixed actor ingress without paired-control or repository lock acquisition.

    The actor must remain the canonical fence owner across requests. The fixed
    installed loader refuses this module while it remains outside FILES. These
    synchronous methods are not a declaration that the 35s transport budget can
    contain native preparation; unknown outcomes must never be retried.
    """
    protocol=_module('db_control');operation=request['operation'];value=request['payload']
    protocol.validate_request(request)
    _need(operation in protocol.RETENTION_BRIDGE and context.role=='db'
        and context.authenticated is True
        and request['policy_sha256']==context.bindings['paired_policy_sha256']
        and retention_policy.digest==context.bindings['standalone_policy_sha256']
        and retention_policy.data['application_release']==context.document['application_release'])
    if operation in (protocol.RETENTION_PREPARE,protocol.RETENTION_COMMIT):
        _need(value['baseline_bindings']==context.bindings
            and value['installation_receipts']['db']==context.document['acceptance_receipts']['ops'])
    else:_need(value['retention_policy_sha256']==retention_policy.digest)
    if operation==protocol.RETENTION_MATERIAL:
        _need(value['documents']['prepare-request-web.json']['baseline_bindings']==context.bindings)
    actor=retention_entry._load('activation_actor')
    if operation==protocol.RETENTION_ACTOR_TERMINAL_STATUS:
        result=actor.terminal_status(request['transaction'],value['intent_sha256'])
        _need(type(result) is dict and set(result)=={'schema','transaction','intent_sha256','baseline_bindings',
            'baseline_sha256','source','actor_owner','actor_invocation','observed_at','state','terminal'}
            and result['schema']=='careertrellis.retention-activation-actor-terminal-status/v1'
            and result['transaction']==request['transaction'] and result['intent_sha256']==value['intent_sha256']
            and result['baseline_bindings']==context.bindings
            and result['baseline_sha256']==hashlib.sha256(retention_entry.canonical(context.document)).hexdigest()
            and result['state'] in ('running','terminal')
            and ((result['state']=='running' and result['terminal'] is None)
                 or (result['state']=='terminal' and type(result['terminal']) is dict)))
        observed=protocol.utc(result['observed_at'])
        _need(0<=(dt.datetime.now(dt.timezone.utc)-observed).total_seconds()<=120)
    elif operation==protocol.RETENTION_ACTOR_STATE:
        result=actor.observe(request['transaction'],value['intent_sha256'])
        _need(type(result) is dict and set(result)=={'schema','transaction','intent_sha256','actor_owner','actor_invocation',
            'prepare_request_sha256','baseline_bindings','baseline_sha256','source'}
            and result['schema']=='careertrellis.retention-activation-actor-state/v1'
            and result['transaction']==request['transaction'] and result['intent_sha256']==value['intent_sha256']
            and result['baseline_bindings']==context.bindings
            and result['baseline_sha256']==hashlib.sha256(retention_entry.canonical(context.document)).hexdigest())
    elif operation==protocol.RETENTION_MATERIAL:
        result=actor.submit_material(retention_entry.canonical(value))
        _need(type(result) is dict and set(result)=={'schema','transaction','material_sha256','value_sha256',
            'actor_owner','actor_invocation'} and result['schema']=='careertrellis.retention-activation-material-accepted/v1'
            and result['transaction']==request['transaction']
            and result['value_sha256']==hashlib.sha256(retention_entry.canonical(value)).hexdigest()
            and protocol.nonzero(result['material_sha256']) and protocol.nonzero(result['actor_invocation'],32)
            and result['actor_owner']==value['documents']['commit-authority.json']['dispatcher_owner'])
    else:
        result=actor.submit(retention_entry.canonical(request))
        phase='prepare' if operation==protocol.RETENTION_PREPARE else 'commit'
        fields={'transaction','role','phase','intent_sha256','result'}
        fields|=({'request_sha256'} if phase=='prepare' else {'selector_sha256','committed_at'})
        _need(type(result) is dict and set(result)==fields and result['transaction']==request['transaction']
            and result['role']=='db' and result['phase']==phase and result['intent_sha256']==value['intent_sha256']
            and result['result']==('prepared' if phase=='prepare' else 'committed'))
        if phase=='prepare':_need(protocol.nonzero(result['request_sha256']))
        else:
            _need(protocol.nonzero(result['selector_sha256']));protocol.utc(result['committed_at'])
    current=retention_entry._native_dependencies(retention_entry.verify_install(),'db')
    _need(current.role=='db' and current.authenticated is True
        and current.document==context.document and current.bindings==context.bindings)
    return result


def _retention_activation_finalize(retention_entry,request,retention_policy,context):
    """One fixed receiver call; it owns its fences and independent exit proof."""
    protocol=_module('db_control');protocol.validate_request(request)
    value=request['payload']
    _need(request['operation']==protocol.RETENTION_FINALIZE and context.role=='db' and context.authenticated is True
        and request['policy_sha256']==context.bindings['paired_policy_sha256']
        and value['retention_policy_sha256']==retention_policy.digest==context.bindings['standalone_policy_sha256']
        and value['intent']['baseline_bindings']==context.bindings
        and value['intent']['artifact_revision']==context.revision
        and value['intent']['application_release']==retention_policy.data['application_release']==context.document['application_release']
        and value['finalized']['baseline_sha256']['db']==hashlib.sha256(retention_entry.canonical(context.document)).hexdigest()
        and value['intent']['installation_receipts']['db']==context.document['acceptance_receipts']['ops'])
    # The installed loader refuses while this receiver is outside FILES.
    finalizer=retention_entry._load('activation_finalize')
    result=finalizer.receive(retention_entry.canonical(value))
    finalizer.validate_ack(result,value,dt.datetime.now(dt.timezone.utc))
    current=retention_entry._native_dependencies(retention_entry.verify_install(),'db')
    _need(current.role=='db' and current.authenticated is True
        and current.document==context.document and current.bindings==context.bindings)
    return result


def _retention_pre_growth(retention_entry,request,retention_policy,context):
    protocol=_module('db_control');protocol.validate_request(request)
    _need(context.role=='db' and context.authenticated is True
        and request['policy_sha256']==context.bindings['paired_policy_sha256']
        and retention_policy.digest==context.bindings['standalone_policy_sha256'])
    native=retention_entry._load('pre_growth_native')
    if request['operation'] in (protocol.RETENTION_PRE_GROWTH_STATUS,protocol.RETENTION_PRE_GROWTH_VERIFY):
        _need(request['payload']['retention_policy_sha256']==retention_policy.digest)
        if request['operation']==protocol.RETENTION_PRE_GROWTH_VERIFY:return native.verify_complete(request['transaction'])
        return native.observe(request['transaction'])
    value=request['payload']['request']
    _need(request['operation']==protocol.RETENTION_PRE_GROWTH
        and value['retention_policy_sha256']==retention_policy.digest
        and request['payload']['web_history']['baseline_bindings']==context.bindings
        and native.native_routes_available() is True)
    return native.submit(retention_entry.canonical(request))


def _pre_growth_capacity(retention_entry,request,retention_policy,context):
    """Fixed bounded admission observation, never a progress/status operation."""
    protocol=_module('db_control');protocol.validate_request(request)
    _need(request['operation']==protocol.RETENTION_CAPACITY
        and request['payload']['stage']==protocol.RETENTION_PRE_GROWTH
        and request['payload']['retention_policy_sha256']==retention_policy.digest
        and request['policy_sha256']==context.bindings['paired_policy_sha256']
        and retention_policy.digest==context.bindings['standalone_policy_sha256']
        and context.authenticated is True and context.role=='db')
    _need(_module('client').pre_growth_capacity_transport_ready(request) is True)
    activation=retention_entry._load('activation');selector=activation.require_activated(context)
    _need(selector.role=='db' and selector.bindings==context.bindings)
    guard=retention_entry._load('guard');deadline=time.monotonic()+180
    with _module('provider').repository_lock() as (fd,_identity):
        guard.native_history_snapshot(request['transaction'],protocol.RETENTION_PRE_GROWTH,fd)
        _need(time.monotonic()<deadline)
        provider=guard.NativeProvider(fd,operation_deadline=deadline)
        try:
            source,candidate=provider.source_allocation()
            collected=provider.collect();_need(collected.authenticated is True)
            facts=collected.value
            _need(time.monotonic()<deadline)
        finally:provider.close()
        _need(type(source) is int and type(candidate) is int and 0<=source<=candidate<=512*1024**2 and candidate>0)
        guard._namespace_owners(facts,fd)
        history=guard.native_history_snapshot(request['transaction'],protocol.RETENTION_PRE_GROWTH,fd)
        guard.validate_peer_history(history,request['transaction'],protocol.RETENTION_PRE_GROWTH,
            retention_policy.digest,expected_role='db')
        _need(facts['baseline_bindings']==history['baseline_bindings']==context.bindings
            and time.monotonic()<deadline)
        observed=dt.datetime.now(dt.timezone.utc)
        _need(all(0<=(observed-protocol.utc(stamp)).total_seconds()<=120 for stamp in
            (facts['observed_at'],facts['quota']['observed_at'],history['observed_at'])))
        current=activation.require_activated(context)
        _need(current.digest==selector.digest and current.bindings==context.bindings)
        return {'schema':'careertrellis.retention-capacity-preflight/v1','history':history,'quota':facts['quota'],
            'namespace_bytes':facts['namespace_bytes'],'account_scope':facts['account_scope'],
            'observed_at':facts['observed_at'],'baseline_bindings':facts['baseline_bindings'],
            'allocation':{'source_bytes':source,'candidate_bytes':candidate,'namespace_new_bytes':candidate},
            'activation_sha256':selector.digest}


def db_endpoint(raw):
    request = {'transaction': '0' * 32, 'policy_sha256': '0' * 64}
    common = _module('common')
    try:
        _need(os.geteuid() == 0 and socket.gethostname() == DB_HOST)
        request = _module('db_control').validate_request(_module('client').decode(raw, 16384))
        policy = load_policy()
        _need(request['policy_sha256'] == policy.digest)
        if 'release' in request['payload']:
            _need(request['payload']['release'] == policy.data['application_release'])
        if (request['operation'] in _module('db_control').RETENTION
                or request['operation'] in (_module('db_control').RETENTION_PREFLIGHT,
                                            _module('db_control').RETENTION_CAPACITY,
                                            _module('db_control').RETENTION_SCHEDULE,
                                            _module('db_control').RETENTION_ACTIVATION,
                                            _module('db_control').RETENTION_BOOTSTRAP,
                                            _module('db_control').RETENTION_STATE,
                                            _module('db_control').RETENTION_FINALIZE)
                or request['operation'] in _module('db_control').RETENTION_BRIDGE
                or request['operation'] in (_module('db_control').RETENTION_PRE_GROWTH,
                                            _module('db_control').RETENTION_PRE_GROWTH_STATUS,
                                            _module('db_control').RETENTION_PRE_GROWTH_VERIFY)):
            # Authenticate both installed namespaces and both policy bindings
            # before loading operational retention code. Status takes no paired
            # control lock and never creates a transaction store.
            from onedrive_retention import entry as retention_entry
            revision = retention_entry.verify_install()
            context=retention_entry._native_dependencies(revision, 'db')
            endpoint = retention_entry._load('endpoint')
            coordinator = retention_entry._load('coordinator')
            retention_policy = coordinator.Policy(retention_entry._protected_document(
                coordinator.PRIVATE_POLICY, 0o600)[1])
            _need(retention_policy.data['paired_policy_sha256'] == policy.digest)
            if request['operation']==_module('db_control').RETENTION_FINALIZE:
                payload=_retention_activation_finalize(retention_entry,request,retention_policy,context)
            elif request['operation'] in (_module('db_control').RETENTION_PRE_GROWTH,
                                        _module('db_control').RETENTION_PRE_GROWTH_STATUS,
                                        _module('db_control').RETENTION_PRE_GROWTH_VERIFY):
                payload=_retention_pre_growth(retention_entry,request,retention_policy,context)
            elif request['operation'] in _module('db_control').RETENTION_BRIDGE:
                payload=_retention_activation_bridge(retention_entry,request,retention_policy,context)
            elif request['operation'] == _module('db_control').RETENTION_STATE:
                _need(request['payload']['retention_policy_sha256']==retention_policy.digest
                      and retention_policy.digest==context.bindings['standalone_policy_sha256'])
                payload=retention_entry._load('activation_readiness').native_activation_state(
                    request['transaction'],request['payload']['intent_sha256'])
                _need(payload['baseline_bindings']==context.bindings
                      and payload['db_baseline_sha256']==hashlib.sha256(
                          retention_entry.canonical(context.document)).hexdigest())
            elif request['operation'] == _module('db_control').RETENTION_BOOTSTRAP:
                payload=_retention_bootstrap_evidence(retention_entry,request,retention_policy,context)
            elif request['operation'] == _module('db_control').RETENTION_ACTIVATION:
                payload=_retention_activation_evidence(retention_entry,request,retention_policy,context)
            elif request['operation'] == _module('db_control').RETENTION_SCHEDULE:
                payload=retention_entry._load('health').store_schedule(request['payload']['witness'])
            elif request['operation'] in (_module('db_control').RETENTION_PREFLIGHT,
                                        _module('db_control').RETENTION_CAPACITY):
                _need(request['payload']['retention_policy_sha256'] == retention_policy.digest)
                # One-shot admission collection, never a progress request. It
                # owns the exact existing repository fence while inspecting
                # histories; a running repository operation makes it refuse.
                guard = retention_entry._load('guard')
                if (request['operation']==_module('db_control').RETENTION_CAPACITY
                        and request['payload']['stage']==_module('db_control').RETENTION_PRE_GROWTH):
                    payload=_pre_growth_capacity(retention_entry,request,retention_policy,context)
                else:
                    with _module('provider').repository_lock() as (fd, _identity):
                        payload = guard.native_history_snapshot(request['transaction'],
                            request['payload']['stage'], fd)
                        if request['operation'] == _module('db_control').RETENTION_CAPACITY:
                            collected = guard.collect_provider_facts(fd)
                            _need(collected.authenticated is True)
                            facts = collected.value
                            guard._namespace_owners(facts, fd)
                            payload = {'schema':'careertrellis.retention-capacity-preflight/v1',
                                'history':payload,'quota':facts['quota'],
                                'namespace_bytes':facts['namespace_bytes'],
                                'account_scope':facts['account_scope'],
                                'observed_at':facts['observed_at'],
                                'baseline_bindings':facts['baseline_bindings']}
            else:
                value = endpoint.from_paired_request(request)
                payload = endpoint.Endpoint(retention_policy, endpoint.NativeDB(retention_policy)).handle(value)
            _need(type(payload) is dict and len(common.canonical(payload)) <= 60000)
            return common.canonical({'schema': RESPONSE_SCHEMA, 'transaction': request['transaction'],
                                     'policy_sha256': request['policy_sha256'], 'payload': payload})
        descriptor = _control_lock()
        try:
            store = _transaction_store(policy, request)
            backend = _module('db_host').DBBackend(policy, request['transaction'], store)
            payload = _module('db_control').DBControl(policy, store, backend).handle(request)
            _need(type(payload) is dict and len(common.canonical(payload)) <= 60000)
        finally:
            os.close(descriptor)
    except Exception:
        payload = {'state': 'unknown', 'operator_required': True}
    return common.canonical({'schema': RESPONSE_SCHEMA, 'transaction': request['transaction'],
                             'policy_sha256': request['policy_sha256'], 'payload': payload})


def _inspect():
    role = 'web' if socket.gethostname() == WEB_HOST else 'db'
    value = _decode(_protected_read(ROOT / role / 'status.json', {0o600}))
    db_states = {'idle', 'starting', 'fenced', 'backup_running', 'pair_ready', 'sealed',
                 'released', 'abort_wait_backup', 'abort_ready', 'aborted', 'mirroring',
                 'health_verifying', 'complete', 'failed', 'operator_required', 'unknown'}
    if role == 'db' and 'state' in value:
        # Rich private DB state never leaves the host. Only these fixed fields
        # are inspectable; future internal fields are deliberately discarded.
        _need(isinstance(value['state'], str) and value['state'] in db_states)
        projected = {'schema': 'careertrellis.paired-maintenance-inspection/v1', 'state': value['state']}
        for key, pattern in (('transaction', '[0-9a-f]{32}'), ('boot_id',
                            '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')):
            if key in value:
                _need(isinstance(value[key], str) and re.fullmatch(pattern, value[key]))
                projected[key] = value[key]
        if 'operator_required' in value:
            _need(type(value['operator_required']) is bool)
            projected['operator_required'] = value['operator_required']
        return _module('common').canonical(projected)
    required = {'schema', 'policy_sha256', 'boot_id', 'release', 'phase', 'result',
                'observed_at', 'services_restored'}
    _need(set(value) in (required, required | {'transaction'})
          and value.get('schema') == 'careertrellis.paired-maintenance-status/v1')
    for key, length in (('policy_sha256', 64), ('release', 40)):
        _need(isinstance(value[key], str) and re.fullmatch('[0-9a-f]{%d}' % length, value[key]))
    _need(isinstance(value['boot_id'], str) and re.fullmatch(
        '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value['boot_id']))
    _need(type(value['services_restored']) is bool)
    _need(value['result'] in {'ok', 'running', 'failed', 'operator_required', 'attention', 'blocked'})
    phases = {'idle', 'preflight', 'db_fenced', 'quiescing', 'backup', 'capture',
              'restoring', 'publishing', 'mirroring', 'verifying', 'complete',
              'aborting', 'recovery', 'failed', 'operator_required', 'blocked'}
    if role == 'db':
        phases |= db_states
    _need(isinstance(value['phase'], str) and value['phase'] in phases)
    if 'transaction' in value:
        _need(isinstance(value['transaction'], str) and re.fullmatch('[0-9a-f]{32}', value['transaction']))
    timestamp = value['observed_at']
    _need(isinstance(timestamp, str) and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', timestamp))
    dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    return _module('common').canonical(value)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ['writer-gate']:
        return writer_gate()
    try:
        if args == ['ssh-gate']:
            raw = sys.stdin.buffer.read(16385)
            sys.stdout.buffer.write(_module('ssh_gate').run(os.environ, raw))
            return 0
        _need(os.geteuid() == 0)
        if args == ['db-endpoint']:
            sys.stdout.buffer.write(db_endpoint(sys.stdin.buffer.read(16385)))
            return 0
        _need(socket.gethostname() in (WEB_HOST, DB_HOST))
        if args == ['inspect']:
            sys.stdout.buffer.write(_inspect())
            return 0
        policy = load_policy()
        if args == ['web-bootstrap']:
            _need(socket.gethostname() == WEB_HOST)
            _module('host').bootstrap_web(policy)
        elif args in (['web-run'], ['web-recover']):
            _need(socket.gethostname() == WEB_HOST)
            _module('host').run_web(policy, recovery_only=args == ['web-recover'])
        elif len(args) == 2 and args[0] in ('db-worker', 'db-recover'):
            _need(socket.gethostname() == DB_HOST and re.fullmatch('[0-9a-f]{32}', args[1]))
            _module('db_host').db_worker(policy, args[1], recovery_only=args[0] == 'db-recover')
        else:
            raise EntryRefused('entry_refused')
        return 0
    except Exception:
        # No exception text, host data or diagnostic output crosses this boundary.
        return 1


if __name__ == '__main__':
    try:
        verify_install()
        sys.path.insert(0, str(INSTALL_ROOT.parent))
        raise SystemExit(main())
    except Exception:
        raise SystemExit(1) from None
