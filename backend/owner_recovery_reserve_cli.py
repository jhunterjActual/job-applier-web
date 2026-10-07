"""Source-pinned, attended WEB reserve entry; never enrolls on import/install.

No credential arguments, environment selectors or arbitrary storage roots.
Installation and every live action require separate operator authorization.
"""
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import stat
import sys

OPS = 'bbbc4298b2ca80df107e64d2673d6c1f243c5e41'
BASE = Path('/opt/careertrellis/owner-recovery-reserve')
LIBRARY = Path('/opt/careertrellis/staging/releases') / OPS
STATE = Path('/var/lib/careertrellis-owner-recovery-reserve')
WRAPPER_MODULES = {'owner_recovery_reserve', 'owner_recovery_reserve_host', 'owner_recovery_reserve_cli',
    'owner_recovery_reserve_relay', 'owner_recovery_reserve_operator'}
ESSENTIAL = {'admission_operations', 'owner_recovery', 'owner_recovery_bridge',
    'identity_plan_store', 'identity_plan_packet', 'identity_plan_supersession',
    'identity_entry', 'identity_operator', 'identity_evidence', 'identity_web_handoff',
    'session_v2_transition', 'workos_transport_candidate'}
ERROR = 'Reserve operation refused or acknowledgement uncertain; retain all evidence.'


def require(value):
    if not value:
        raise ValueError(ERROR)


def protected(path, limit=1048576):
    for parent in path.parents:
        item = parent.lstat()
        require(stat.S_ISDIR(item.st_mode) and item.st_uid == 0 and not item.st_mode & 0o022)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        item = os.fstat(fd)
        require(stat.S_ISREG(item.st_mode) and item.st_uid == 0 and item.st_nlink == 1
            and not item.st_mode & 0o022 and item.st_size <= limit)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit+1)
        require(len(raw) <= limit)
        return raw
    finally:
        os.close(fd)


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value); value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(ERROR)))


def load_verified():
    """Verify source bytes before project imports; no project bytecode loader."""
    root = Path(__file__).absolute().parent.parent
    require(os.geteuid() == 0 and os.uname().nodename == 'careertrellis-staging-web-1'
        and root.parent == BASE and re.fullmatch('[0-9a-f]{40}', root.name))
    manifest_raw = protected(root/'source-manifest.json')
    manifest = strict_json(manifest_raw)
    require(type(manifest) is dict and set(manifest) == {'version', 'operations', 'wrapper', 'library'}
        and type(manifest['version']) is int and manifest['version'] == 1 and manifest['operations'] == OPS)
    mapping = {}
    for field, directory in (('wrapper', root), ('library', LIBRARY)):
        entries = manifest[field]
        require(type(entries) is dict and entries)
        for name, digest in entries.items():
            require(type(name) is str and re.fullmatch(r'(backend/[a-z0-9_]+\.py|deploy/systemd/[A-Za-z0-9_.@/-]+)', name)
                and '..' not in Path(name).parts and type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest))
            if name.startswith('backend/'):
                module = Path(name).stem
                require(module not in mapping)
                mapping[module] = (directory/name, digest)
    require(set(manifest['wrapper']) == {'backend/'+name+'.py' for name in WRAPPER_MODULES})
    require(ESSENTIAL <= set(mapping))

    def verify():
        require(protected(root/'source-manifest.json') == manifest_raw)
        for field, directory in (('wrapper', root), ('library', LIBRARY)):
            for name, digest in manifest[field].items():
                require(hashlib.sha256(protected(directory/name)).hexdigest() == digest)
        return True

    verify()
    require(all(name not in sys.modules for name in mapping))

    class Loader(importlib.abc.Loader):
        def create_module(self, spec): return None
        def exec_module(self, module):
            path, digest = mapping[module.__name__]
            raw = protected(path)
            require(hashlib.sha256(raw).hexdigest() == digest)
            exec(compile(raw, str(path), 'exec'), module.__dict__)

    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in mapping:
                return importlib.util.spec_from_file_location(fullname, mapping[fullname][0], loader=Loader())
            for directory in (root, LIBRARY):
                candidate = directory/'backend'/fullname.split('.')[0]
                require(not candidate.exists() and not candidate.with_suffix('.py').exists())
            return None

    sys.meta_path.insert(0, Finder())
    verify.library_pins = dict(manifest['library'])
    verify.closure_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    verify.wrapper_revision = root.name
    return verify


def slot(name, *, metadata, value=None):
    """One immutable primary-plan or independently approved successor slot."""
    require(name in ('primary-plan.json', 'approval.json'))
    import owner_recovery as recovery
    path = STATE/name
    if value is not None:
        raw = recovery.encoded(value)
        recovery.parents(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
    record = recovery.strict_json(recovery.protected(path))
    require(type(record) is dict and set(record) == {'version', 'enrollment_sha256', 'selection_hex', 'selection_sha256'}
        and type(record['version']) is int and record['version'] == 1
        and record['enrollment_sha256'] == hashlib.sha256(recovery.encoded(metadata)).hexdigest())
    require(type(record['selection_hex']) is str and len(record['selection_hex']) <= 2048)
    selected = bytes.fromhex(record['selection_hex'])
    require(hashlib.sha256(selected).hexdigest() == record['selection_sha256'])
    return selected


def record_selection(metadata, selected):
    import owner_recovery as recovery
    return {'version': 1, 'enrollment_sha256': hashlib.sha256(recovery.encoded(metadata)).hexdigest(),
        'selection_hex': selected.hex(), 'selection_sha256': hashlib.sha256(selected).hexdigest()}


def approve_selection(action, coordinator, terminal):
    import owner_recovery as recovery
    import identity_plan_store as plans
    import identity_plan_packet as packet
    from owner_recovery_reserve import anchor
    with coordinator.locked():
        metadata = coordinator.reserve.read_metadata()
        primary, reserve = coordinator.primary.read(), coordinator.reserve.read()
        issued = coordinator.reserve.immutable('issued.json')
        require(primary['id'] == metadata['primary_id'] and anchor(primary) == metadata['primary_anchor']
            and reserve['id'] == issued['reserve_id'] and reserve['state'] == 'active'
            and reserve['confirmed'] > 0 and reserve['binding'] == primary['binding'] == metadata['binding'])
        if action == 'arm-primary':
            require(primary['version'] == 1 and primary['state'] == 'active')
            with coordinator.host.primary_view() as view:
                selected = view['selection']
                require(reserve['confirmed'] <= recovery.context_times(view['bridge']['context'])[0])
            name = 'primary-plan.json'
        else:
            require(action == 'approve-successor' and primary['version'] == 2 and primary['state'] == 'consumed')
            with coordinator.host.locks():
                selected, pair = plans.load(activation=True)
                original = packet.validate(bytes.fromhex(metadata['original_plan_hex']))
                value = recovery.strict_json(selected)
                raw = plans.ops.read_protected(plans.archive(value['digest']), limit=packet.LIMIT)
                successor = packet.validate(raw)
                packet.successor(original, successor)
                require(successor['predecessor'] == metadata['original_plan'] and pair == successor['switch'])
            name = 'approval.json'
        require(terminal.ask('Independently reviewed exact selected-plan SHA-256') == hashlib.sha256(selected).hexdigest())
        require(terminal.ask('Freeze this one immutable reserve plan binding: type FREEZE') == 'FREEZE')
        if action == 'arm-primary':
            with coordinator.host.primary_view() as view:
                require(view['selection'] == selected)
                slot(name, metadata=metadata, value=record_selection(metadata, selected))
        else:
            with coordinator.host.locks():
                plans.require_current(selected)
                slot(name, metadata=metadata, value=record_selection(metadata, selected))
    return {'outcome': 'reserve_plan_binding_frozen'}


def relay_action(argv, verify, *, stdin=None):
    """Remote-only fixed verbs for captured private pipes, never credential IO.

    Source verification does not turn caller JSON into independent evidence.
    Only the reviewed workstation relay may publish its actual DB observation.
    These verbs intentionally acquire no primary, reserve or host locks: the
    waiting operation owns them. The relay protocol owns its separate namespace.
    """
    require(verify() is True and not any(os.isatty(fd) for fd in (0, 1, 2)))
    from uuid import UUID
    import owner_recovery_reserve_relay as relay
    require(type(argv) is list and argv)
    action = argv[0]
    if action == 'relay-peek':
        require(len(argv) == 1)
        result = relay.peek()
    elif action == 'relay-status':
        require(len(argv) == 2 and str(UUID(argv[1])) == argv[1])
        result = relay.inspect(argv[1])
    else:
        require(action == 'relay-publish' and len(argv) == 1)
        stream = stdin if stdin is not None else sys.stdin.buffer
        raw = stream.read(65537)
        require(type(raw) is bytes and 0 < len(raw) <= 65536)
        value = strict_json(raw)
        require(type(value) is dict and set(value) == {'request_id', 'observation'}
            and type(value['request_id']) is str and str(UUID(value['request_id'])) == value['request_id'])
        result = relay.publish(value['request_id'], value['observation'])
    require(verify() is True)
    return result


def main():
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        verify = load_verified()
        if len(sys.argv) > 1 and sys.argv[1].startswith('relay-'):
            result = relay_action(sys.argv[1:], verify)
            print(json.dumps(result, sort_keys=True, separators=(',', ':')))
            return 0
        import owner_recovery as recovery
        import owner_recovery_reserve as reserve
        from owner_recovery_reserve_host import Host
        import owner_recovery_bridge as bridge
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        from uuid import UUID
        require(len(sys.argv) in (2, 3))
        action = sys.argv[1]
        simple = ('enroll', 'confirm', 'arm-primary', 'approve-successor')
        require((action in simple and len(sys.argv) == 2) or
            (action in ('consume', 'receipt', 'enter', 'attest', 'status', 'reconcile') and len(sys.argv) == 3))
        operation = None if len(sys.argv) == 2 else sys.argv[2]
        if operation is not None: require(str(UUID(operation)) == operation)
        store = reserve.Store()
        engine = create_engine(bridge.ops.migration_connection_url(bridge.operator.DATABASE),
            poolclass=NullPool, echo=False, hide_parameters=True, connect_args={'connect_timeout': 5})
        try:
            host = Host(engine, verify_sources=verify, metadata_reader=store.read_metadata,
                approval_reader=lambda: slot('approval.json', metadata=store.read_metadata()),
                primary_plan_reader=lambda: slot('primary-plan.json', metadata=store.read_metadata()),
                library_pins=verify.library_pins, action=action, closure_sha256=verify.closure_sha256)
            coordinator = reserve.Coordinator(host, reserve=store)
            if action == 'attest': result = coordinator.attest(operation, OPS, engine)
            elif action == 'receipt': result = coordinator.receipt(operation)
            elif action in ('status', 'reconcile'): result = coordinator.historical(action, operation, OPS, engine)
            else:
                with recovery.Terminal() as terminal:
                    require(terminal.ask('Confirm this is your visible secure terminal: type VISIBLE') == 'VISIBLE')
                    if action in ('enroll', 'confirm'): result = getattr(coordinator, action)(terminal)
                    elif action in ('arm-primary', 'approve-successor'): result = approve_selection(action, coordinator, terminal)
                    else: result = getattr(coordinator, action)(operation, terminal)
            verify()
            print(recovery.encoded(result).decode())
            return 1 if result.get('outcome') == 'recorded_cleanup_pending' else 0
        finally:
            engine.dispose()
    except BaseException:
        print(ERROR, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
