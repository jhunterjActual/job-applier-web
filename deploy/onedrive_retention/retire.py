"""Exact-object retirement transaction with fixed installed/native boundary.

Offline receipts and terminal records remain explicitly simulations. Native
execution requires authenticated snapshots, all canonical locks, finite owning
unit, fresh downloaded-byte/eTag witnesses and durable interruption fences.
Unfinished operations block new work; unknown outcomes require reconciliation,
never another delete call. Native explicit policy selects recoverable deletion
or capability-gated conditional permanent POST. No sync, account-trash or
legacy/source operation is available.
"""
import copy
import datetime as dt
import hashlib
import re
import os
import socket
import stat
from pathlib import Path
from contextlib import contextmanager

from . import bundle, coordinator, policy, catalog as catalog_module, hydrate, graph_delete


NAMESPACE = 'onedrive:job-applier-staging/postgresql-bundles'
LOCK = '/run/lock/careertrellis-pgbackrest-repository.lock'


class RetirementRefused(ValueError):
    """Fixed public category; backend exceptions may contain private details."""


def need(value, code='retirement_requires_reconciliation'):
    if not value:
        raise RetirementRefused(code)


def digest(value):
    return hashlib.sha256(bundle.canonical(value)).hexdigest()


def fresh(value, now):
    observed = policy.timestamp(value)
    need(0 <= (now - observed).total_seconds() <= policy.MAX_INVENTORY_AGE_SECONDS)


def objects(catalog):
    result = {}
    for entry in catalog['entries']:
        for obj in entry['objects']:
            policy._keys(obj, ('path', 'object_id', 'size', 'sha256'))
            policy._path(obj['path']); policy._text(obj['object_id']); policy._digest(obj['sha256'])
            policy.integer(obj['size'], 1)
            need(obj['path'] not in result)
            result[obj['path']] = copy.deepcopy(obj)
        need(any(obj['path'] == entry['id'] + '/repository.tar'
                 and obj['sha256'] == entry['archive_sha256'] for obj in entry['objects']))
    return result


def prepare(catalog, now):
    """Pure planner wrapper with validated zero/one-bundle bootstrap behavior."""
    current = policy.timestamp(now)
    policy._keys(catalog, ('schema', 'observed_at', 'entries', 'holds'))
    need(catalog['schema'] == 'careertrellis.onedrive-bundle-catalog/v1')
    fresh(catalog['observed_at'], current)
    need(type(catalog['entries']) is list and type(catalog['holds']) is list)
    if len(catalog['entries']) >= 2:
        result = policy.plan_retirement(catalog, now)
        objects(catalog)
        return result
    # Bootstrap never fabricates a second accepted generation to make the normal
    # planner run. Validate the single accepted entry and preserve every object.
    ids = set()
    for entry in catalog['entries']:
        policy._keys(entry, ('id', 'accepted_at', 'archive_sha256', 'download_sha256',
            'restore_archive_sha256', 'native_restore_receipt_sha256', 'complete',
            'current', 'dependencies_resolved', 'dependencies', 'objects'))
        for field in ('id', 'archive_sha256', 'download_sha256', 'restore_archive_sha256',
                      'native_restore_receipt_sha256'):
            policy._digest(entry[field])
        need(entry['complete'] is True and entry['dependencies_resolved'] is True
             and type(entry['current']) is bool and entry['dependencies'] == [])
        need(entry['archive_sha256'] == entry['download_sha256'] == entry['restore_archive_sha256'])
        need(policy.timestamp(entry['accepted_at']) <= policy.timestamp(catalog['observed_at']))
        need(type(entry['objects']) is list and 0 < len(entry['objects']) <= 2)
        for obj in entry['objects']:
            need(type(obj) is dict and type(obj.get('path')) is str
                 and obj['path'] in (entry['id'] + '/repository.tar', entry['id'] + '/manifest.json'))
        ids.add(entry['id'])
    names = set()
    for hold in catalog['holds']:
        policy._keys(hold, ('name', 'owner', 'reason', 'review_after', 'bundles'))
        need(type(hold['name']) is str and policy.NAME.fullmatch(hold['name'])
             and hold['name'] not in names)
        names.add(hold['name']); policy._text(hold['owner']); policy._text(hold['reason'])
        policy.timestamp(hold['review_after'])
        need(type(hold['bundles']) is list and 0 < len(hold['bundles']) <= 10000
             and all(type(key) is str and key in ids for key in hold['bundles']))
    rows = objects(catalog)
    need(len({row['object_id'] for row in rows.values()}) == len(rows))
    size = sum(row['size'] for row in rows.values()); need(size <= policy.NAMESPACE_BUDGET)
    return {'schema': 'careertrellis.onedrive-retirement-plan/v1',
            'catalog_sha256': hashlib.sha256(policy.canonical(catalog)).hexdigest(),
            'planned_at': now, 'namespace_bytes': size, 'retained_bundle_ids': sorted(ids),
            'candidates': [], 'requires_locked_revalidation': True, 'deletions_performed': 0,
            'bootstrap': True}


def clock(backend):
    now = backend.now()
    need(type(now) is dt.datetime and now.utcoffset() == dt.timedelta(0))
    return now


def quota(backend):
    value = backend.quota()
    policy._keys(value, ('total', 'free', 'observed_at'))
    need(type(value['total']) is int and type(value['free']) is int
         and 0 <= value['free'] <= value['total'] and value['total'] > 0)
    fresh(value['observed_at'], clock(backend))
    return copy.deepcopy(value)


def snapshot(backend, transaction, completed=()):
    """Validate a fresh authenticated snapshot while the canonical lock is held.

    completed contains only objects with known successful acknowledgements and
    observed absence in this invocation. It is not a reusable deletion allowlist.
    The backend must report an earlier/unknown invocation as unresolved even if
    the caller reuses its transaction ID; current owned progress is not unknown.
    """
    value = backend.trusted_snapshot(transaction)
    policy._keys(value, ('policy', 'catalog', 'inventory', 'repository_idle', 'unresolved_retirement'))
    need(value['repository_idle'] is True and value['unresolved_retirement'] is False)
    active = coordinator.Policy(value['policy']); now = clock(backend)
    active.require_active(now)
    # The hard-coded planner must not silently weaken stricter active policies.
    need(active.data['keep_verified'] == 2 and active.data['grace_seconds'] == policy.GRACE_SECONDS
         and active.data['namespace_budget_bytes'] == policy.NAMESPACE_BUDGET,
         'retirement_policy_profile_unsupported')
    plan = prepare(value['catalog'], now.isoformat())
    inventory = value['inventory']
    policy._keys(inventory, ('schema', 'namespace', 'observed_at', 'objects'))
    need(inventory['schema'] == 'careertrellis.onedrive-bundle-inventory/v1'
         and inventory['namespace'] == NAMESPACE and type(inventory['objects']) is list)
    fresh(inventory['observed_at'], now)
    expected = objects(value['catalog'])
    for removed in completed:
        need(expected.get(removed['path']) == removed)
        del expected[removed['path']]
    actual = {}
    for obj in inventory['objects']:
        policy._keys(obj, ('path', 'object_id', 'size', 'sha256'))
        policy._path(obj['path']); policy._text(obj['object_id']); policy._digest(obj['sha256'])
        policy.integer(obj['size'], 1)
        need(obj['path'] not in actual)
        actual[obj['path']] = obj
    need(actual == expected, 'retirement_inventory_diverged')
    return copy.deepcopy(value), plan, active.digest


def comparable(catalog):
    value = copy.deepcopy(catalog); value.pop('observed_at')
    return bundle.canonical(value)


def selected_expiry_mode(policy_data):
    mode=policy_data.get('expiry_mode','recoverable_manual')
    need(mode in ('recoverable_manual','conditional_permanent'))
    return mode


def authenticated_retirement_absences(repository_lock_fd,transaction,provider_rows,*,parent=None):
    """Exact known-ack absences for the CURRENT finite native operation only.

    This is not an allowlist argument. Missing objects must be candidates in
    the protected original grace/hold plan and have the exact contiguous durable
    intent, dispatch and successful soft-delete acknowledgement. A missing file,
    unknown acknowledgement or unmatched object refuses. No deletion is issued.
    """
    from . import execution,entry
    need(type(transaction) is str and re.fullmatch('[0-9a-f]{32}',transaction)
         and type(provider_rows) is dict)
    revision=entry.verify_install();entry._native_dependencies(revision,'db')
    files=catalog_module.NativeFiles(repository_lock_fd)
    root=catalog_module.ROOT.parent/'retirements'/transaction
    with files.directory(root,private=True) as fd:names=set(os.listdir(fd))
    need(0<len(names)<=10000 and all(name in ('operation-intent.json','operation-complete.json')
        or re.fullmatch(r'object-[0-9]{5}-(?:intent|dispatch|ack|terminal)\.json',name) for name in names))
    raw=files.read_retirement(transaction,'operation-intent.json');operation=catalog_module.decode(raw)
    if parent is None:
        need(operation.get('purpose','retention_stage')=='retention_stage')
        execution.require_stage_owner(transaction,'retire')
    else:
        need(operation.get('purpose')=='pre_growth_retirement')
        graph_delete._parent_authority(parent,None,None,transaction)
    need(operation.get('schema')=='careertrellis.bundle-retirement-native-intent/v1'
         and operation.get('transaction')==transaction and operation.get('namespace')==NAMESPACE
         and operation.get('simulation') is False)
    original=operation['catalog'];plan=prepare(original,operation['plan']['planned_at'])
    need(plan==operation['plan'] and operation['catalog_sha256']==hashlib.sha256(policy.canonical(original)).hexdigest())
    current=coordinator.Policy(catalog_module.decode(hydrate.protected(coordinator.PRIVATE_POLICY,0o600)[0]))
    coordinator.native_policy_binding(current);current.require_active(dt.datetime.now(dt.timezone.utc))
    need(current.digest==operation['retention_policy_sha256'])
    mode=selected_expiry_mode(current.data)
    need(operation.get('expiry_mode','recoverable_manual')==mode)
    expected=[(candidate['bundle_id'],obj) for candidate in plan['candidates'] for obj in candidate['objects']]
    indices={int(name[7:12]) for name in names if name.startswith('object-')}
    need(not indices or indices==set(range(max(indices)+1)))
    missing=set()
    for index in sorted(indices):
        need(index<len(expected));bundle_id,obj=expected[index];prefix='object-%05d-'%index
        intent_raw=files.read_retirement(transaction,prefix+'intent.json');intent=catalog_module.decode(intent_raw)
        dispatch=catalog_module.decode(files.read_retirement(transaction,prefix+'dispatch.json'))
        ack_raw=files.read_retirement(transaction,prefix+'ack.json');ack=catalog_module.decode(ack_raw)
        expected_ack={'result':'deleted','object_id':obj['object_id'],
            'request_id':catalog_module.sha(intent_raw),'recoverable':mode=='recoverable_manual',
            'permanent':mode=='conditional_permanent'}
        if 'expiry_mode' in operation:expected_ack['expiry_mode']=mode
        need(intent.get('schema')=='careertrellis.bundle-object-retirement-intent/v1'
             and intent.get('transaction')==transaction and intent.get('sequence')==index
             and intent.get('namespace')==NAMESPACE and intent.get('bundle_id')==bundle_id
             and intent.get('object')==obj and intent.get('simulation') is False
             and intent.get('operation_intent_sha256')==catalog_module.sha(raw)
             and dispatch=={'intent_sha256':catalog_module.sha(intent_raw),'simulation':False}
             and intent.get('expiry_mode','recoverable_manual')==mode
             and ('expiry_mode' in intent)==('expiry_mode' in operation) and ack==expected_ack)
        if prefix+'terminal.json' in names:
            saved=catalog_module.decode(files.read_retirement(transaction,prefix+'terminal.json'))
            need(saved.get('transaction')==transaction and saved.get('sequence')==index
                 and saved.get('object')==obj and saved.get('simulation') is False and saved.get('absent') is True
                 and saved.get('intent_sha256')==catalog_module.sha(intent_raw)
                 and saved.get('dispatch_ack_sha256')==catalog_module.sha(ack_raw)
                 and saved.get('expiry_mode','recoverable_manual')==mode
                 and ('expiry_mode' in saved)==('expiry_mode' in operation))
        if obj['path'] not in provider_rows:missing.add(obj['path'])
    files.require_repository_lock()
    return frozenset(missing)


def run_existing_for_parent(transaction, parent):
    """New purpose needs its own Graph/absence/idle authority, not retire proof."""
    try:
        need(type(transaction) is str and re.fullmatch('[0-9a-f]{32}', transaction))
        graph_delete._parent_authority(parent,None,None,transaction)
    except Exception:
        raise RetirementRefused('pre_growth_retirement_native_adapter_unavailable') from None
    return run(transaction,Native(parent=parent))


def run(transaction, backend=None):
    """Run an exact-object transaction; native requires the fixed Native adapter.

    Backend contract: locked(LOCK), now(), trusted_snapshot(tx), quota(),
    once(tx,name,value)->SHA256(bundle.canonical(value)), and
    delete_exact(namespace,object,request_id,recoverable=True,permanent=False).
    once is durable/exclusive; a duplicate or lost acknowledgement must refuse.
    The canonical-lock context must preserve interruption evidence/fences rather
    than make an unknown operation look disposable. No retry path is provided.
    """
    need(type(transaction) is str and re.fullmatch('[0-9a-f]{32}', transaction))
    if backend is None: backend = Native()
    need(backend is not None and (getattr(backend, 'mode', None) == 'offline-test'
         or type(backend) is Native),
         'native_retirement_adapter_unsupported')
    native = type(backend) is Native
    try:
        with backend.locked(LOCK):
            baseline, plan, policy_sha = snapshot(backend, transaction)
            expiry_mode=selected_expiry_mode(baseline['policy']) if native else 'recoverable_manual'
            need(not native or backend.parent is None or expiry_mode=='conditional_permanent')
            recoverable=expiry_mode=='recoverable_manual';permanent=not recoverable
            if native and permanent and plan['candidates']:
                backend.require_expiry_capability() # Read-only; missing proof creates no retirement ledger.
            before = quota(backend)
            operation = {'schema': 'careertrellis.bundle-retirement-native-intent/v1' if native else
                'careertrellis.bundle-retirement-simulation-intent/v1',
                'transaction': transaction, 'namespace': NAMESPACE, 'retention_policy_sha256': policy_sha,
                'catalog_sha256': plan['catalog_sha256'], 'plan': plan, 'simulation': not native}
            if native:
                operation['catalog'] = baseline['catalog'];operation['expiry_mode']=expiry_mode
                operation['purpose']='pre_growth_retirement' if backend.parent is not None else 'retention_stage'

            def once(name, value):
                expected = digest(value)
                need(backend.once(transaction, name, copy.deepcopy(value)) == expected)
                return expected

            operation_sha = once('operation-intent.json', operation)
            completed, terminals = [], []
            for candidate in plan['candidates']:
                for obj in candidate['objects']:
                    current, revised, current_policy = snapshot(backend, transaction, completed)
                    need(current_policy == policy_sha and comparable(current['catalog']) == comparable(baseline['catalog']))
                    need(revised['candidates'] == plan['candidates']
                         and revised['retained_bundle_ids'] == plan['retained_bundle_ids'])
                    index = len(completed)
                    if native and permanent:backend.require_expiry_capability()
                    intent = {'schema': 'careertrellis.bundle-object-retirement-intent/v1',
                        'transaction': transaction, 'sequence': index, 'namespace': NAMESPACE,
                        'bundle_id': candidate['bundle_id'], 'object': obj,
                        'successor_id': candidate['successor_id'],
                        'successor_accepted_at': candidate['successor_accepted_at'],
                        'operation_intent_sha256': operation_sha, 'simulation': not native}
                    if native:intent['expiry_mode']=expiry_mode
                    intent_sha = once('object-%05d-intent.json' % index, intent)
                    if native and permanent:backend.require_expiry_capability()
                    once('object-%05d-dispatch.json' % index, {'intent_sha256': intent_sha, 'simulation': not native})
                    # Sole dispatch. Any exception, malformed acknowledgement or
                    # failure to durably publish it ends this invocation.
                    ack = backend.delete_exact(NAMESPACE, copy.deepcopy(obj), intent_sha,
                                               recoverable=recoverable, permanent=permanent)
                    policy._keys(ack, ('result', 'object_id', 'request_id', 'recoverable', 'permanent')
                        + (('expiry_mode',) if native else ()))
                    need(ack['result'] == 'deleted' and ack['object_id'] == obj['object_id']
                         and ack['request_id'] == intent_sha and ack['recoverable'] is recoverable
                         and ack['permanent'] is permanent and (not native or ack['expiry_mode']==expiry_mode))
                    ack_sha = once('object-%05d-ack.json' % index, ack)
                    acknowledged_at = clock(backend)
                    absent, _revised, absent_policy = snapshot(backend, transaction, completed + [obj])
                    need(absent_policy == policy_sha and comparable(absent['catalog']) == comparable(baseline['catalog']))
                    need(policy.timestamp(absent['inventory']['observed_at']) >= acknowledged_at,
                         'retirement_absence_observation_predates_acknowledgement')
                    terminal = {'schema': 'careertrellis.bundle-object-retirement-terminal/v1',
                        'transaction': transaction, 'sequence': index, 'namespace': NAMESPACE,
                        'bundle_id': candidate['bundle_id'], 'object': obj, 'intent_sha256': intent_sha,
                        'dispatch_ack_sha256': ack_sha, 'absent': True,
                        'absence_observed_at': absent['inventory']['observed_at'], 'simulation': not native}
                    if native:terminal['expiry_mode']=expiry_mode
                    terminals.append(once('object-%05d-terminal.json' % index, terminal))
                    completed.append(copy.deepcopy(obj))
            after = quota(backend)
            result = {'schema': 'careertrellis.bundle-retirement-simulation/v1',
                'result': 'simulated_complete', 'transaction': transaction,
                'catalog_sha256': plan['catalog_sha256'], 'retention_policy_sha256': policy_sha,
                'native_retirement_accepted': False, 'deletions_performed': 0,
                'simulated_retired_count': len(completed),
                'simulated_logical_bytes': sum(obj['size'] for obj in completed),
                'terminal_receipt_sha256': terminals, 'quota_before': before, 'quota_after': after,
                'provider_reported_free_delta_bytes': after['free'] - before['free']
                    if after['total'] == before['total'] else None,
                'physical_bytes_reclaimed': None, 'bootstrap': plan.get('bootstrap', False)}
            if native:
                result.update(schema='careertrellis.bundle-retirement-native/v1', result='complete',
                    native_retirement_accepted=True, deletions_performed=len(completed),
                    operation_intent_sha256=operation_sha, completed_at=clock(backend).isoformat())
                result['expiry_mode']=expiry_mode
                result.pop('simulated_retired_count'); result.pop('simulated_logical_bytes')
                result['logical_bytes_retired'] = sum(obj['size'] for obj in completed)
            once('operation-complete.json', result)
            if native: backend.commit_catalog(transaction)
            return result
    except BaseException:
        # No repeated append/delete, cleanup, accepted tombstone or replay. The
        # unfinished exclusive intent is authoritative reconciliation evidence.
        raise RetirementRefused('retirement_requires_reconciliation') from None


class Native:
    """Installed fixed-root adapter. No caller paths, credentials or snapshots."""
    mode = 'native-installed'
    ROOT = catalog_module.ROOT.parent/'retirements'

    def __init__(self,parent=None):
        self.parent=parent
        self.fds = []; self.tx = None; self.root = None; self.store = None

    def now(self): return dt.datetime.now(dt.timezone.utc)

    def require_expiry_capability(self):
        """Read-only sealed current proof, no grant/provider/refresh/intent.

        The Graph client independently repeats its final freshness check at the
        actual POST. This earlier check prevents known missing proof from being
        recorded as an uncertain provider dispatch.
        """
        import configparser
        from . import entry,capture_plan
        self.files.require_repository_lock()
        revision=entry.verify_install();context=entry._native_dependencies(revision,'db')
        need(context.authenticated is True and context.role=='db' and context.revision==revision)
        original=hydrate.protected(capture_plan.CONFIG,0o600)
        parser=configparser.ConfigParser(interpolation=None,strict=True);parser.read_string(original[0].decode('utf-8'))
        need(not parser.defaults() and set(parser.sections())=={'r2','onedrive'}
            and parser['onedrive'].get('type')=='onedrive')
        remote=parser['onedrive']
        value=graph_delete._permanent_capability(remote['drive_id'],remote.get('root_folder_id',''),revision)
        need(type(value) is str and re.fullmatch('[0-9a-f]{64}',value)
            and hydrate.protected(capture_plan.CONFIG,0o600)==original)
        self.files.require_repository_lock();return value

    @contextmanager
    def locked(self, lock):
        from . import entry, identity_collect
        need(lock == LOCK and os.name == 'posix' and os.geteuid() == os.getegid() == 0
             and socket.gethostname() == 'job-applier-db-staging-01'
             and Path(__file__).absolute() == Path('/usr/local/libexec/careertrellis/onedrive_retention/retire.py'))
        revision = entry.verify_install(); entry._native_dependencies(revision, 'db')
        if self.parent is not None:graph_delete._parent_authority(self.parent,None,None,self.parent.transaction)
        import fcntl
        try:
            for name in identity_collect.LOCKS:
                path = Path(name)
                # Walk all ancestors without following a writable directory/link.
                for parent in path.parents:
                    meta = parent.lstat()
                    need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0
                         and (stat.S_IMODE(meta.st_mode) == 0o1777 if str(parent) == '/run/lock'
                              else not meta.st_mode & 0o022))
                before = path.lstat()
                need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
                     and before.st_nlink == 1 and before.st_size == 0 and stat.S_IMODE(before.st_mode) == 0o600)
                fd = os.open(path, os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK); self.fds.append(fd)
                need((os.fstat(fd).st_dev,os.fstat(fd).st_ino) == (before.st_dev,before.st_ino))
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.files = catalog_module.NativeFiles(self.fds[-1])
            self.store = catalog_module.NativeStore(self.fds[-1])
            yield
        finally:
            for fd in reversed(self.fds): os.close(fd)
            self.fds = []

    def trusted_snapshot(self, transaction):
        from . import guard, entry, execution
        if self.parent is None:execution.require_stage_owner(transaction,'retire')
        else:graph_delete._parent_authority(self.parent,None,None,transaction)
        need(self.fds and re.fullmatch('[0-9a-f]{32}',transaction)
             and (self.parent is not None or entry.retention_idle(transaction,'retire') is True)
             and not os.path.lexists('/run/careertrellis-hosted-activation.lease'))
        self.files.require_repository_lock()
        with self.store.io.locked():
            records = self.store.records(); retired = self.store.retired(records)
            holds = [catalog_module.decode(self.files.read_store('holds/'+name))
                     for name in sorted(self.files.names('holds'))]
        self.transactions = {}
        for identifier, record in records.items():
            if identifier in retired: continue
            tx = record['evidence']['download']['transaction']
            witness = catalog_module.read_identity(self.files,tx)
            need(witness.get('schema') == 'careertrellis.onedrive-bundle-identity/v2'
                 and witness.get('etag_verified_before_after_download') is True
                 and witness.get('sha256') == identifier and graph_delete.valid_etag(witness.get('etag')))
            fresh(witness['observed_at'],self.now())
            self.transactions[identifier] = tx
        observed = (guard.native_catalog_inventory(self.fds[-1],retirement_transaction=self.tx,
            retirement_parent=self.parent) if self.parent is not None
            else guard.native_catalog_inventory(self.fds[-1],retirement_transaction=self.tx))
        need(observed['authenticated'] is True)
        pending = self.unresolved(transaction)
        return {'policy':observed['policy'], 'catalog':observed['catalog'],
            'inventory':observed['inventory'], 'repository_idle':True, 'unresolved_retirement':pending}

    def unresolved(self, transaction):
        # Missing root is the first invocation, never permission to ignore an
        # inaccessible root or an unknown directory/partial prior operation.
        try:
            with self.files.directory(self.ROOT, private=True) as fd:
                names = os.listdir(fd)
        except FileNotFoundError: return False
        need(len(names) <= catalog_module.MAX_ENTRIES)
        for name in names:
            need(re.fullmatch('[0-9a-f]{32}',name))
            if name == self.tx == transaction: continue
            try:
                self.files._protected(self.ROOT/name/'operation-complete.json',catalog_module.MAX_RECORD)
                operation = catalog_module.decode(self.files._protected(self.ROOT/name/'operation-intent.json',catalog_module.MAX_RECORD))
                candidates = {row['bundle_id'] for row in operation['plan']['candidates']}
                with self.store.io.locked():
                    need(candidates <= self.store.retired(self.store.records()),
                         'completed_retirement_catalog_commit_missing')
            except FileNotFoundError: return True
        return False

    def quota(self):
        from . import guard
        return guard.collect_provider_facts(self.fds[-1]).value['quota']

    def once(self, transaction, name, value):
        need(re.fullmatch('[0-9a-f]{32}',transaction) and type(name) is str
             and re.fullmatch(r'operation-(?:intent|complete)\.json|object-[0-9]{5}-(?:intent|dispatch|ack|terminal)\.json',name))
        self.files.require_repository_lock()
        if self.root is None:
            need(name == 'operation-intent.json')
            with self.files.directory(self.ROOT.parent, private=True) as fd:
                try: os.mkdir(self.ROOT.name,0o700,dir_fd=fd)
                except FileExistsError: pass
                os.fsync(fd)
            with self.files.directory(self.ROOT,private=True) as fd:
                os.mkdir(transaction,0o700,dir_fd=fd); os.fsync(fd)
            self.tx = transaction; self.root = self.ROOT/transaction
        need(transaction == self.tx)
        raw = bundle.canonical(value); need(len(raw) <= catalog_module.MAX_RECORD)
        hydrate.exclusive(self.root/name,raw)
        return digest(value)

    def delete_exact(self, namespace, obj, request_id, *, recoverable, permanent):
        mode='conditional_permanent' if permanent is True and recoverable is False else 'recoverable_manual'
        need(namespace == NAMESPACE and recoverable is (mode=='recoverable_manual') and permanent is (mode=='conditional_permanent')
             and self.tx is not None and self.root is not None
             and obj['path'] == obj['sha256']+'/repository.tar')
        tx = self.transactions[obj['sha256']]
        client = graph_delete.Client(graph_delete.native_grant(tx,obj['sha256'],self.fds[-1],
            operation_transaction=self.tx,expiry_mode=mode,parent=self.parent))
        result=(client.permanent_delete(obj['sha256'],obj['object_id'],obj['size']) if permanent
            else client.soft_delete(obj['sha256'],obj['object_id'],obj['size']))
        need(result==('permanent_delete_accepted' if permanent else 'soft_delete_accepted'))
        return {'result':'deleted','object_id':obj['object_id'],'request_id':request_id,
                'recoverable':recoverable,'permanent':permanent,'expiry_mode':mode}

    def commit_catalog(self, transaction): return self.store.commit_retirement(transaction)
