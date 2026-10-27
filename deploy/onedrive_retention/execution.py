"""Fixed installed execution and provider-free observation boundaries.

Observation never acquires a lock, creates a journal, starts a collector, or
contacts a provider. Dispatch is a separate attended operation with durable
intent before either unit is submitted; unknown acknowledgement is not replayed.
"""
import datetime as dt
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat
import time

from . import bundle, collector, common_io, coordinator as c, hydrate
from . import local_restore_plan as plans, native_restore as native

FIELDS = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'Job', 'ControlGroup')
UNIT = re.compile(r'careertrellis-onedrive-bundle-(?:(?:hydrate|publish|prepare|retire)-collector-|(?:hydrate|publish|restore|prepare|retire|collector|identity)-)[0-9a-f]{32}(?:-[0-9]{2})?\.service\Z')
STAGE_SECONDS = {'hydrate':32400, 'publish':21600, 'prepare':32400, 'retire':3600}


def need(value):
    if not value: raise c.Refused('retention_execution_requires_reconciliation')


def authenticated():
    from . import entry
    revision = entry.verify_install()
    return entry._native_dependencies(revision, 'db')


def show(unit):
    need(UNIT.fullmatch(unit))
    return common_io.read_unit_properties(unit, FIELDS,
        lambda args: hydrate.command(args, output=True, timeout=10))


def group_empty(unit):
    """Absence is explicit; unreadable or malformed cgroups are never empty."""
    need(UNIT.fullmatch(unit))
    root = Path('/sys/fs/cgroup/system.slice') / unit
    try: meta = root.lstat()
    except FileNotFoundError: return True
    need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022)
    import itertools
    groups = list(itertools.islice(root.rglob('cgroup.procs'), 65))
    need(0 < len(groups) <= 64 and root / 'cgroup.procs' in groups)
    for path in groups:
        meta = path.lstat()
        need(stat.S_ISREG(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022)
        raw = path.read_bytes(); need(len(raw) <= 65536)
        need(all(re.fullmatch(rb'[1-9][0-9]*', item) for item in raw.split()))
        if raw.split(): return False
    return True


def terminal(unit):
    state = show(unit)
    need(state['LoadState'] in ('loaded', 'not-found')
         and state['ActiveState'] in ('inactive', 'failed')
         and state['MainPID'] == state['ControlPID'] == '0' and state['Job'] == ''
         and state['ControlGroup'] in ('', '/system.slice/' + unit)
         and group_empty(unit))
    return True


def terminal_or_running(unit):
    state = show(unit)
    if state['LoadState'] == 'loaded' and state['ActiveState'] in ('active','activating','deactivating'):
        return False
    return terminal(unit)


def census():
    raw = hydrate.command(['/usr/bin/systemctl', 'list-units', '--all', '--no-legend',
        '--plain', 'careertrellis-onedrive-bundle-*.service'], output=True, timeout=10)
    need(len(raw) <= 1024*1024)
    names = []
    for line in raw.decode('ascii').splitlines():
        fields = line.split(); need(fields and UNIT.fullmatch(fields[0]))
        names.append(fields[0])
    need(len(names) <= 10000 and len(set(names)) == len(names))
    # A collected unit must not conceal an orphaned owned cgroup. Enumerate
    # only this namespace; unknown entries or permission errors refuse.
    import itertools
    parent = Path('/sys/fs/cgroup/system.slice')
    meta = parent.lstat()
    need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022)
    groups = list(itertools.islice(parent.glob('careertrellis-onedrive-bundle-*'),10001))
    need(len(groups) <= 10000)
    for group in groups:
        need(UNIT.fullmatch(group.name))
        if group.name not in names: names.append(group.name)
    return names


def idle(transaction, stage, *, identity_verification=None):
    context = authenticated()
    need(type(transaction) is str and c.TX.fullmatch(transaction) and stage in c.STAGES)
    caller = Path('/proc/self/cgroup').read_text().strip()
    allowed = ('careertrellis-onedrive-bundle-' + stage + '-' + transaction + '.service')
    if identity_verification is not None:
        need(type(identity_verification) is int and 1 <= identity_verification <= 99)
        allowed = 'careertrellis-onedrive-bundle-identity-' + transaction + '-%02d.service' % identity_verification
    units = census()
    identity_parents = set()
    if identity_verification is not None:
        identity_parents = identity_parent_units(transaction,identity_verification,units,context)
    for unit in units:
        if unit in identity_parents: continue
        if caller == '0::/system.slice/' + allowed and unit == allowed:
            state = show(unit)
            need(state['LoadState'] == 'loaded' and state['ActiveState'] == 'active'
                 and state['SubState'] == 'running' and state['MainPID'] == str(os.getpid())
                 and state['ControlPID'] == '0' and state['Job'] == '')
        elif identity_verification is None and stage in STAGE_SECONDS and unit == stage_collector(transaction,stage):
            # This owner was dispatched only after this exact collector's
            # protected ready intent appeared. Other collectors remain fences.
            journal = c.STATE / 'db' / transaction / stage
            _, intent = document(journal / 'collector-intent.json')
            _, dispatch = document(journal / 'stage-dispatch-intent.json')
            state = stage_properties(unit)
            need(intent.get('transaction') == transaction and intent.get('stage') == stage
                 and dispatch.get('collector_unit') == unit and dispatch.get('owner_unit') == allowed
                 and intent.get('owner') == native.process_identity(int(state['MainPID']))
                 and state['ActiveState'] == 'active' and state['SubState'] == 'running'
                 and state['ControlPID'] == '0' and state['Job'] == ''
                 and state['Type'] == 'exec' and state['Restart'] == 'no'
                 and state['RemainAfterExit'] == 'no' and state['PrivateNetwork'] == 'yes'
                 and state['KillMode'] == 'control-group' and state['SendSIGKILL'] == 'yes'
                 and all(state[key] == '' for key in native.OWNER_HOOK_FIELDS)
                 and native.span_microseconds(state['RuntimeMaxUSec']) == (STAGE_SECONDS[stage]+300)*1000000
                 and native.span_microseconds(state['TimeoutStartUSec']) == 30*1000000
                 and native.span_microseconds(state['TimeoutStopUSec']) == 90*1000000)
        elif identity_verification is None and stage == 'restore' and unit == collector.coordinates(transaction,1)['collector_unit']:
            journal = c.STATE / 'db' / transaction
            _, intent = document(journal / 'collector' / 'intent.json')
            _, dispatch = document(journal / 'restore' / 'native-dispatch-intent.json')
            state = stage_properties(unit)
            need(intent.get('transaction') == transaction and intent.get('attempt') == 1
                 and intent.get('collector_unit') == unit and intent.get('owner_unit') == allowed
                 and dispatch.get('collector_unit') == unit and dispatch.get('owner_unit') == allowed
                 and intent.get('collector_owner') == native.process_identity(int(state['MainPID']))
                 and state['ActiveState'] == 'active' and state['SubState'] == 'running'
                 and state['ControlPID'] == '0' and state['Job'] == ''
                 and state['Type'] == 'exec' and state['Restart'] == 'no'
                 and state['RemainAfterExit'] == 'no' and state['PrivateNetwork'] == 'yes'
                 and state['KillMode'] == 'control-group' and state['SendSIGKILL'] == 'yes'
                 and all(state[key] == '' for key in native.OWNER_HOOK_FIELDS)
                 and native.span_microseconds(state['RuntimeMaxUSec']) == collector.COLLECTOR_RUNTIME_SECONDS*1000000
                 and native.span_microseconds(state['TimeoutStartUSec']) == 30*1000000
                 and native.span_microseconds(state['TimeoutStopUSec']) == 90*1000000)
        else: terminal(unit)
    # A surviving postmaster marker is reconciliation work even if the original
    # transient unit has been collected. Do not read live PG/application data.
    root = Path(plans.RESTORE_ROOT)
    try: native.directory(root)
    except FileNotFoundError: return True
    import itertools
    entries = list(itertools.islice(root.iterdir(), 10001)); need(len(entries) <= 10000)
    for path in entries:
        need(re.fullmatch('[0-9a-f]{32}(?:-0[2-9])?', path.name))
        native.directory(path)
        need(not os.path.lexists(path / 'data' / 'postmaster.pid'))
    return True


def document(path, maximum=131072):
    raw = native.protected(path, maximum=maximum)
    value = native.decode(raw); need(bundle.canonical(value) == raw)
    return raw, value


def persisted_intent(raw, value, transaction, stage, policy_sha256):
    """Read-only provenance validation, never an authorization for an action.

    A completed operation's original WEB witness is historical evidence. Status
    uses protected request bytes bound to the original dispatch/owner, then
    independently proves unit termination and receipt identity. This function
    neither makes a freshness claim nor feeds any dispatch/operation preflight.
    Incoming actions still use endpoint.validate_request/require_native_peer.
    """
    need(type(raw) is bytes and bundle.canonical(value) == raw and type(value) is dict
         and set(value) == {'schema','operation','retention_policy_sha256','capture_transaction','payload'}
         and value['schema'] == 'careertrellis.onedrive-retention-request/v1'
         and value['operation'] == 'bundle_'+stage and value['capture_transaction'] == transaction
         and value['retention_policy_sha256'] == policy_sha256)
    payload = value['payload']
    expected = {'stage','receipt_facts','web_history'} | ({'web_preflight'} if stage == 'restore' else set())
    need(type(payload) is dict and set(payload) == expected and payload['stage'] == stage
         and type(payload['web_history']) is dict)
    facts = payload['receipt_facts']
    need(type(facts) is dict and set(facts) == (set() if stage == 'hydrate' else {'predecessor_receipt_sha256'})
         and all(type(item) is str and c.HEX.fullmatch(item) for item in facts.values()))
    if stage == 'restore': need(type(payload['web_preflight']) is dict)
    return hashlib.sha256(raw).hexdigest()


def observe(transaction, stage, policy_sha256):
    context = authenticated()
    need(type(transaction) is str and c.TX.fullmatch(transaction) and stage in c.STAGES
         and context.bindings['standalone_policy_sha256'] == policy_sha256)
    return _observe_records(transaction,stage,policy_sha256)


def observe_completed_history(transaction, stage, archive):
    """Provider-free completed history under an authenticated archived policy.

    This does not validate an incoming request, establish a current reservation,
    or grant dispatch. Old policy provenance must come from the fixed protected
    archive factory and remain bound to the current accepted installed baseline.
    """
    from . import guard
    context = authenticated()
    need(type(archive) is guard.AuthenticatedPolicyArchive and archive.authenticated is True
         and archive.current_bindings == context.bindings
         and type(transaction) is str and c.TX.fullmatch(transaction) and stage in c.STAGES)
    result = _observe_records(transaction,stage,archive.archive_policy_sha)
    need(result['state'] == 'complete')
    return result


def _observe_records(transaction, stage, policy_sha256):
    """Internal read-only records; callers authenticate policy provenance first."""
    value = {'schema':'careertrellis.onedrive-stage-status/v1', 'capture_transaction':transaction,
        'retention_policy_sha256':policy_sha256, 'stage':stage, 'state':'unknown',
        'receipt_sha256':'', 'facts':{}}
    journal = c.STATE / 'db' / transaction / stage
    try: intent_raw, intent = document(journal / 'intent.json')
    except FileNotFoundError: return value
    from . import endpoint
    request_sha256 = persisted_intent(intent_raw,intent,transaction,stage,policy_sha256)
    owner = endpoint.unit(stage, transaction)
    state = show(owner)
    if state['LoadState'] == 'loaded' and state['ActiveState'] in ('active', 'activating', 'deactivating'):
        value['state'] = 'running'
        # A restore success retained by RemainAfterExit is still running until
        # the already-dispatched collector has committed terminal proof.
    if os.path.lexists(journal / 'failed.json'):
        document(journal / 'failed.json'); value['state'] = 'failed'; return value
    root = Path(plans.ROOT) / transaction
    if stage != 'restore':
        try: _, completion = document(journal / 'terminal.json')
        except FileNotFoundError: return value
        need(completion.get('schema') == 'careertrellis.onedrive-stage-terminal/v1'
             and completion.get('transaction') == transaction and completion.get('stage') == stage
             and completion.get('unit') == owner and completion.get('policy_sha256') == policy_sha256)
        snapshot = completion.get('retained_success')
        need(type(snapshot) is dict)
        validate_stage_success(snapshot, owner, stage)
        _, birth = document(journal/'owner-start.json')
        _, stop = document(journal/'unit-stop-intent.json')
        need(completion.get('owner') == birth.get('owner') == stop.get('owner')
             and birth.get('transaction') == transaction and birth.get('stage') == stage
             and birth.get('unit') == owner and birth.get('invocation_id') == snapshot['InvocationID']
             and birth.get('request_sha256') == request_sha256
             and stop.get('retained_success') == snapshot and stop.get('unit') == owner
             and stop.get('transaction') == transaction and stop.get('stage') == stage
             and str(birth['owner']['pid']) == snapshot['ExecMainPID'])
        terminal(owner)
    if stage == 'hydrate':
        # Hydration completion is append-only status-05; never select a phase
        # by filename age, mtime, or an unbounded directory walk.
        try: raw, complete = document(root / 'status-05.json')
        except FileNotFoundError: return value
        need(complete.get('schema') == 'careertrellis.repository-hydration/v1'
             and complete.get('transaction') == transaction and complete.get('phase') == 'complete')
        facts = complete.get('result'); need(type(facts) is dict)
        facts = {key: facts[key] for key in ('archive_bytes','generation_sha256','backup_label')} | {'archive_sha256':facts['sha256']}
    elif stage == 'publish':
        try: raw, complete = document(root / 'downloaded-receipt.json', 16384)
        except FileNotFoundError: return value
        need(complete.get('schema') == 'careertrellis.onedrive-bundle-download/v1'
             and complete.get('transaction') == transaction and complete.get('result') == 'download_verified'
             and complete.get('native_restore_accepted') is False)
        facts = {key:complete[key] for key in ('archive_sha256','archive_bytes','generation_sha256','backup_label','native_restore_accepted')}
    elif stage == 'restore':
        attempt = Path(plans.RESTORE_ROOT) / transaction
        try:
            raw, complete = document(attempt / 'complete.json')
            _, proof = document(attempt / 'unit-terminal-proof.json')
            _, ownership = document(attempt / 'ownership.json')
            _, stop = document(attempt / 'unit-stop-intent.json')
        except FileNotFoundError: return value
        native.validate_terminal_proof(proof, complete, ownership['owner'], stop_intent=stop)
        need(ownership.get('web_preflight_sha256') == hashlib.sha256(
            bundle.canonical(intent['payload']['web_preflight'])).hexdigest())
        _, download = document(root / 'downloaded-receipt.json', 16384)
        need(complete['transaction'] == transaction and complete['attempt'] == 1)
        facts = {key:complete[key] for key in ('archive_sha256','generation_sha256','backup_label','native_restore_accepted')}
        facts['archive_bytes'] = download['archive_bytes']
    elif stage == 'prepare':
        try: raw, complete = document(journal/'preparation-complete.json')
        except FileNotFoundError: return value
        need(complete.get('schema') == 'careertrellis.onedrive-retirement-preparation/v1'
             and complete.get('transaction') == transaction and complete.get('admission',{}).get('native_authenticated') is True)
        facts = {'catalog_sha256':complete['catalog_sha256'],'prepared_count':len(complete['refreshed_sources'])}
    else:
        try: raw, complete = document(c.STATE / 'db' / 'retirements' / transaction / 'operation-complete.json')
        except FileNotFoundError: return value
        need(complete.get('result') == 'complete' and complete.get('native_retirement_accepted') is True
             and complete.get('retention_policy_sha256') == policy_sha256)
        facts = {'catalog_sha256':complete['catalog_sha256'], 'retired_count':complete['deletions_performed']}
    terminal(owner)
    watcher = (collector.coordinates(transaction,1)['collector_unit'] if stage == 'restore'
               else stage_collector(transaction,stage))
    if not terminal_or_running(watcher):
        value.update(state='running',receipt_sha256='',facts={})
        return value
    value.update(state='complete', receipt_sha256=hashlib.sha256(raw).hexdigest(), facts=facts)
    return endpoint.validate_response(value, intent)


def stage_collector(transaction, stage):
    need(stage in STAGE_SECONDS and c.TX.fullmatch(transaction))
    return 'careertrellis-onedrive-bundle-' + stage + '-collector-' + transaction + '.service'


def stage_collector_command(transaction, stage, journal):
    return ['/usr/bin/systemd-run','--no-block','--unit=' + stage_collector(transaction,stage),
        '--property=Type=exec','--property=Restart=no','--property=RemainAfterExit=no',
        '--property=UMask=0077','--property=TimeoutStartSec=30s',
        '--property=RuntimeMaxSec=' + str(STAGE_SECONDS[stage]+300) + 's',
        '--property=TimeoutStopSec=90s','--property=KillMode=control-group',
        '--property=SendSIGKILL=yes','--property=PrivateNetwork=yes',
        '--property=StandardOutput=append:' + str(journal / 'collector-output.log'),
        '--property=StandardError=append:' + str(journal / 'collector-output.log'),
        '/usr/bin/python3','-I','-B','/usr/local/libexec/careertrellis/onedrive_retention/entry.py',
        'collect-stage',stage,'--transaction',transaction]


def dispatch_stage(transaction, stage):
    authenticated(); idle(transaction, stage)
    from . import endpoint
    owner, watcher = endpoint.unit(stage,transaction), stage_collector(transaction,stage)
    for unit in (owner,watcher):
        need(show(unit)['LoadState'] == 'not-found'); terminal(unit)
    journal = c.STATE / 'db' / transaction / stage; c.directory(journal)
    hydrate.exclusive(journal / 'collector-output.log', b'')
    hydrate.exclusive(journal / 'stage-dispatch-intent.json', bundle.canonical({
        'schema':'careertrellis.onedrive-stage-dispatch/v1','transaction':transaction,
        'stage':stage,'owner_unit':owner,'collector_unit':watcher,
        'dispatched_at':dt.datetime.now(dt.timezone.utc).isoformat()}))
    hydrate.command(stage_collector_command(transaction,stage,journal), timeout=10)
    # Bounded dispatch readiness, not a progress/status side effect. Failure
    # retains the single intent and never starts or retries the owner.
    until = time.monotonic() + 10
    while not os.path.lexists(journal / 'collector-intent.json'):
        need(time.monotonic() < until); time.sleep(.1)
    _, ready = document(journal / 'collector-intent.json')
    need(ready.get('transaction') == transaction and ready.get('stage') == stage)
    hydrate.command(endpoint.command_plan(stage,transaction,journal), timeout=10)
    return True


def stage_properties(unit):
    fields = tuple(dict.fromkeys((*FIELDS, *native.OWNER_FIELDS, 'Result','ExecMainStatus','ExecMainPID','InvocationID')))
    return common_io.read_unit_properties(unit, fields,
        lambda args: hydrate.command(args, output=True, timeout=10))


def validate_stage_success(state, unit, stage):
    need(stage in STAGE_SECONDS and UNIT.fullmatch(unit) and type(state) is dict)
    need(state.get('LoadState') == 'loaded' and state.get('ActiveState') == 'active'
         and state.get('SubState') == 'exited' and state.get('MainPID') == state.get('ControlPID') == '0'
         and state.get('Job') == '' and state.get('Result') == 'success' and state.get('ExecMainStatus') == '0'
         and state.get('Type') == 'exec' and state.get('Restart') == 'no'
         and state.get('RemainAfterExit') == 'yes' and state.get('KillMode') == 'control-group'
         and state.get('SendSIGKILL') == 'yes' and state.get('PrivateNetwork') == 'no'
         and native.span_microseconds(state.get('RuntimeMaxUSec')) == STAGE_SECONDS[stage]*1000000
         and native.span_microseconds(state.get('TimeoutStartUSec')) == 30*1000000
         and native.span_microseconds(state.get('TimeoutStopUSec')) == 90*1000000
         and all(state.get(key) == '' for key in native.OWNER_HOOK_FIELDS)
         and type(state.get('InvocationID')) is str and re.fullmatch('[0-9a-f]{32}',state['InvocationID'])
         and type(state.get('ExecMainPID')) is str and re.fullmatch('[1-9][0-9]*',state['ExecMainPID'])
         and state.get('ControlGroup') in ('','/system.slice/' + unit))
    return state


def stage_owner_profile(transaction, stage):
    from . import endpoint
    owner = endpoint.unit(stage,transaction)
    need(Path('/proc/self/cgroup').read_text().strip() == '0::/system.slice/' + owner)
    state = stage_properties(owner)
    need(state['LoadState'] == 'loaded' and state['ActiveState'] == 'active'
         and state['SubState'] == 'running' and state['MainPID'] == str(os.getpid())
         and state['ControlPID'] == '0' and state['Job'] == '' and state['Type'] == 'exec'
         and state['Restart'] == 'no' and state['RemainAfterExit'] == 'yes'
         and state['KillMode'] == 'control-group' and state['SendSIGKILL'] == 'yes'
         and state['PrivateNetwork'] == ('yes' if stage == 'restore' else 'no')
         and native.span_microseconds(state['RuntimeMaxUSec']) == (7200 if stage == 'restore' else STAGE_SECONDS[stage])*1000000
         and native.span_microseconds(state['TimeoutStartUSec']) == 30*1000000
         and native.span_microseconds(state['TimeoutStopUSec']) == 90*1000000
         and all(state[key] == '' for key in native.OWNER_HOOK_FIELDS))
    return state


def require_stage_owner(transaction, stage):
    stage_owner_profile(transaction,stage)
    from . import endpoint
    # The originating request is already protected and policy-bound; a direct
    # shell invocation has neither the owning cgroup nor this fixed intent.
    _, intent = document(c.STATE / 'db' / transaction / stage / 'intent.json')
    endpoint.validate_request(intent)
    need(intent['capture_transaction'] == transaction and intent['operation'] == 'bundle_' + stage)


def preparation_owner_continuation(transaction):
    """Continue readonly child work within the SAME already-started operation.

    This is not a new-action authorization and is never used by dispatch. The
    initial entry required a fresh WEB witness before writing owner-start.
    Later child reads may continue only under that original finite owner/birth,
    original request digest and its upfront watcher. Retirement uses a new
    request and must independently pass current freshness validation.
    """
    context = authenticated(); state = stage_owner_profile(transaction,'prepare')
    journal = c.STATE/'db'/transaction/'prepare'; c.directory(journal)
    raw,request = document(journal/'intent.json')
    request_sha256 = persisted_intent(raw,request,transaction,'prepare',context.bindings['standalone_policy_sha256'])
    _, birth = document(journal/'owner-start.json')
    need(birth.get('schema') == 'careertrellis.onedrive-stage-owner/v1'
         and birth.get('transaction') == transaction and birth.get('stage') == 'prepare'
         and birth.get('request_sha256') == request_sha256
         and birth.get('policy_sha256') == context.bindings['standalone_policy_sha256']
         and birth.get('owner') == native.process_identity(os.getpid())
         and birth.get('invocation_id') == state['InvocationID'])
    _, dispatch = document(journal/'stage-dispatch-intent.json')
    owner = 'careertrellis-onedrive-bundle-prepare-'+transaction+'.service'
    watcher = stage_collector(transaction,'prepare')
    need(dispatch.get('transaction') == transaction and dispatch.get('stage') == 'prepare'
         and dispatch.get('owner_unit') == owner and dispatch.get('collector_unit') == watcher)
    _, watch_intent = document(journal/'collector-intent.json')
    watching = stage_properties(watcher)
    need(watch_intent.get('transaction') == transaction and watch_intent.get('stage') == 'prepare'
         and watch_intent.get('policy_sha256') == context.bindings['standalone_policy_sha256']
         and native.process_identity(int(watching['MainPID'])) == watch_intent.get('owner')
         and watching['ActiveState'] == 'active' and watching['SubState'] == 'running'
         and watching['Type'] == 'exec' and watching['Restart'] == 'no'
         and watching['RemainAfterExit'] == 'no' and watching['PrivateNetwork'] == 'yes'
         and watching['ControlPID'] == '0' and watching['Job'] == ''
         and watching['KillMode'] == 'control-group' and watching['SendSIGKILL'] == 'yes'
         and native.span_microseconds(watching['RuntimeMaxUSec']) == (STAGE_SECONDS['prepare']+300)*1000000
         and native.span_microseconds(watching['TimeoutStartUSec']) == 30*1000000
         and native.span_microseconds(watching['TimeoutStopUSec']) == 90*1000000
         and all(watching[key] == '' for key in native.OWNER_HOOK_FIELDS))
    import itertools
    names = [path.name for path in itertools.islice(journal.iterdir(),1001)]
    need(len(names) <= 1000 and not any(name.startswith(('failed','failure','contained','unconfirmed'))
                                      or name == 'preparation-complete.json' for name in names))
    return context


def identity_parent_units(transaction, verification, units, context):
    """Authenticate only a live prepared parent and its upfront watcher."""
    allowed = set()
    for unit in units:
        match = re.fullmatch('careertrellis-onedrive-bundle-prepare-([0-9a-f]{32})\\.service',unit)
        if not match: continue
        parent_tx = match[1]
        path = c.STATE/'db'/parent_tx/'prepare'/('preparation-identity-'+transaction+'-%02d.json'%verification)
        try: _, mapping = document(path)
        except FileNotFoundError: continue
        need(type(mapping) is dict and set(mapping) == {'schema','parent_transaction','source_transaction',
            'verification','parent_owner','parent_snapshot','watcher_owner','watcher_snapshot','policy_sha256'})
        need(mapping['schema'] == 'careertrellis.onedrive-identity-preparation/v1'
             and mapping['parent_transaction'] == parent_tx and mapping['source_transaction'] == transaction
             and mapping['verification'] == verification
             and mapping['policy_sha256'] == context.bindings['standalone_policy_sha256'])
        watcher = stage_collector(parent_tx,'prepare')
        for name,key in ((unit,'parent'),(watcher,'watcher')):
            state = stage_properties(name)
            need(state == mapping[key+'_snapshot'] and state['ActiveState'] == 'active'
                 and state['SubState'] == 'running' and state['ControlPID'] == '0' and state['Job'] == ''
                 and native.process_identity(int(state['MainPID'])) == mapping[key+'_owner']
                 and state['Type'] == 'exec' and state['Restart'] == 'no'
                 and state['KillMode'] == 'control-group' and state['SendSIGKILL'] == 'yes'
                 and state['RemainAfterExit'] == ('yes' if key == 'parent' else 'no')
                 and state['PrivateNetwork'] == ('no' if key == 'parent' else 'yes')
                 and native.span_microseconds(state['RuntimeMaxUSec']) == (STAGE_SECONDS['prepare']+(0 if key=='parent' else 300))*1000000
                 and native.span_microseconds(state['TimeoutStartUSec']) == 30*1000000
                 and native.span_microseconds(state['TimeoutStopUSec']) == 90*1000000
                 and all(state[field] == '' for field in native.OWNER_HOOK_FIELDS))
        need(not allowed)
        allowed.update((unit,watcher))
    return allowed


@contextmanager
def catalog_store():
    """Repository lock is held only during local admission/catalog snapshots."""
    from . import catalog
    owner = hydrate.Native()
    with owner.locked():
        yield catalog.NativeStore(owner.lock_fd)


def identity_command(source, verification, log):
    need(c.TX.fullmatch(source) and type(verification) is int and 1 <= verification <= 99)
    unit = 'careertrellis-onedrive-bundle-identity-' + source + '-%02d.service'%verification
    return ['/usr/bin/systemd-run','--wait','--unit='+unit,
        '--property=Type=exec','--property=Restart=no','--property=RemainAfterExit=no',
        '--property=UMask=0077','--property=TimeoutStartSec=30s','--property=RuntimeMaxSec=15min',
        '--property=TimeoutStopSec=30s','--property=KillMode=control-group','--property=SendSIGKILL=yes',
        '--property=PrivateNetwork=no','--property=StandardOutput=append:'+str(log),
        '--property=StandardError=append:'+str(log),'/usr/bin/python3','-I','-B',
        '/usr/local/libexec/careertrellis/onedrive_retention/entry.py','collect-identity',
        '--transaction',source,'--verification',str(verification)]


def fresh_identity(parent_transaction, source_transaction):
    """One child submission, no retries. Any partial history blocks a new child."""
    from . import catalog
    context = preparation_owner_continuation(parent_transaction)
    source = Path(plans.ROOT)/source_transaction; native.directory(source)
    import itertools
    names = [path.name for path in itertools.islice(source.iterdir(),100001)]
    need(len(names) <= 100000)
    receipts = {int(name[-7:-5]) for name in names if re.fullmatch('bundle-object-identity-[0-9]{2}\\.json',name)}
    attempts = {int(name[-2:]) for name in names if re.fullmatch('identity-verification-[0-9]{2}',name)}
    need(receipts == attempts and all(1 <= number <= 99 for number in attempts))
    for number in attempts:
        attempt_root = source/('identity-verification-%02d'%number)
        native.directory(attempt_root)
        need(not os.path.lexists(attempt_root/'failed.json'))
        raw,_ = document(source/('bundle-object-identity-%02d.json'%number))
        receipt,_ = document(attempt_root/'receipt.json')
        need(raw == receipt)
    verification = max(attempts,default=0)+1; need(verification <= 99)
    unit = 'careertrellis-onedrive-bundle-identity-'+source_transaction+'-%02d.service'%verification
    need(show(unit)['LoadState'] == 'not-found'); terminal(unit)
    parent = 'careertrellis-onedrive-bundle-prepare-'+parent_transaction+'.service'
    watcher = stage_collector(parent_transaction,'prepare')
    parent_snapshot,watcher_snapshot = stage_properties(parent),stage_properties(watcher)
    mapping = {'schema':'careertrellis.onedrive-identity-preparation/v1',
        'parent_transaction':parent_transaction,'source_transaction':source_transaction,'verification':verification,
        'parent_owner':native.process_identity(int(parent_snapshot['MainPID'])),'parent_snapshot':parent_snapshot,
        'watcher_owner':native.process_identity(int(watcher_snapshot['MainPID'])),'watcher_snapshot':watcher_snapshot,
        'policy_sha256':context.bindings['standalone_policy_sha256']}
    journal = c.STATE/'db'/parent_transaction/'prepare'
    path = journal/('preparation-identity-'+source_transaction+'-%02d.json'%verification)
    log = journal/('identity-'+source_transaction+'-%02d-output.log'%verification)
    hydrate.exclusive(log,b'')
    hydrate.exclusive(path,bundle.canonical(mapping))
    # Exit status is the actual --wait result of this one unit, not a status
    # poll inferred from GC defaults. Partial journals stay reconciliation work.
    hydrate.command(identity_command(source_transaction,verification,log), timeout=960)
    terminal(unit)
    raw, witness = document(source/('bundle-object-identity-%02d.json'%verification))
    saved,_ = document(source/('identity-verification-%02d'%verification)/'receipt.json')
    need(raw == saved and witness.get('schema') == 'careertrellis.onedrive-bundle-identity/v2'
         and witness.get('etag_verified_before_after_download') is True)
    hydrate.exclusive(journal/('identity-'+source_transaction+'-%02d-terminal.json'%verification),
        bundle.canonical({'schema':'careertrellis.onedrive-preparation-identity-terminal/v1',
            'source_transaction':source_transaction,'verification':verification,
            'witness_sha256':hashlib.sha256(raw).hexdigest(),'unit_wait_exit_code':0,
            'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}))
    return witness


def prepare_retirement(transaction):
    """Admit the new recovered bundle and refresh exact retained candidates.

    Parent holds no repository locks while a child reads the provider. Every
    child has an immutable parent/birth mapping. Admission and catalog reads
    own the canonical repository lock in separate short scopes.
    """
    require_stage_owner(transaction,'prepare'); authenticated()
    _, download = document(Path(plans.ROOT)/transaction/'downloaded-receipt.json',16384)
    with catalog_store() as store:
        with store.io.locked(): records = store.records(); retired = store.retired(records)
    need(download['archive_sha256'] not in records)
    active = {key:value for key,value in records.items() if key not in retired}
    need(len(active) < 32)
    sources = [record['evidence']['download']['transaction'] for record in active.values()]
    need(len(set(sources)) == len(sources) and all(c.TX.fullmatch(source) for source in sources))
    # Refresh previous archives before the candidate. All witnesses are checked
    # again by the retirement guard for the 15-minute freshness limit; long
    # provider operations refuse rather than weakening that window.
    for source in sorted(sources): fresh_identity(transaction,source)
    fresh_identity(transaction,transaction)
    preparation_owner_continuation(transaction)
    with catalog_store() as store:
        admitted = store.accept(transaction)
        from . import guard
        observed = guard.native_catalog_inventory(store.io.repository_lock_fd)
        need(observed.get('authenticated') is True)
        catalog_sha256 = hashlib.sha256(bundle.canonical(observed['catalog'])).hexdigest()
    need(admitted.get('native_authenticated') is True)
    hydrate.exclusive(c.STATE/'db'/transaction/'prepare'/'preparation-complete.json',bundle.canonical({
        'schema':'careertrellis.onedrive-retirement-preparation/v1','transaction':transaction,
        'admission':admitted,'catalog_sha256':catalog_sha256,'refreshed_sources':sorted(sources+[transaction]),
        'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}))
    return admitted


def collect_stage(transaction, stage):
    context = authenticated()
    from . import endpoint
    owner, watcher = endpoint.unit(stage,transaction), stage_collector(transaction,stage)
    journal = c.STATE / 'db' / transaction / stage; c.directory(journal)
    need(Path('/proc/self/cgroup').read_text().strip() == '0::/system.slice/' + watcher)
    mine = stage_properties(watcher)
    need(mine.get('MainPID') == str(os.getpid()) and mine.get('Type') == 'exec'
         and mine.get('Restart') == 'no' and mine.get('RemainAfterExit') == 'no'
         and mine.get('PrivateNetwork') == 'yes' and mine.get('KillMode') == 'control-group'
         and mine.get('SendSIGKILL') == 'yes' and mine.get('Job') == ''
         and all(mine.get(key) == '' for key in native.OWNER_HOOK_FIELDS)
         and native.span_microseconds(mine.get('RuntimeMaxUSec')) == (STAGE_SECONDS[stage]+300)*1000000
         and native.span_microseconds(mine.get('TimeoutStartUSec')) == 30*1000000
         and native.span_microseconds(mine.get('TimeoutStopUSec')) == 90*1000000)
    _, intent = document(journal / 'stage-dispatch-intent.json')
    need(intent.get('transaction') == transaction and intent.get('stage') == stage
         and intent.get('owner_unit') == owner and intent.get('collector_unit') == watcher)
    hydrate.exclusive(journal / 'collector-intent.json', bundle.canonical({
        'schema':'careertrellis.onedrive-stage-collector/v1','transaction':transaction,'stage':stage,
        'owner':native.process_identity(os.getpid()),'policy_sha256':context.bindings['standalone_policy_sha256']}))
    started = time.monotonic(); invocation = None; original_owner = None
    while time.monotonic() - started < STAGE_SECONDS[stage] + 120:
        state = show(owner)
        if state['LoadState'] == 'not-found':
            need(invocation is None and time.monotonic()-started < 30)
        else:
            need(state['LoadState'] == 'loaded' and state['ActiveState'] in ('activating','active'))
            try: _, started_owner = document(journal/'owner-start.json')
            except FileNotFoundError:
                need(time.monotonic()-started < 30); time.sleep(.1); continue
            need(started_owner.get('transaction') == transaction and started_owner.get('stage') == stage
                 and started_owner.get('policy_sha256') == context.bindings['standalone_policy_sha256'])
            actual = stage_properties(owner)
            need(started_owner.get('unit') == owner and started_owner.get('invocation_id') == actual['InvocationID'])
            if invocation is None:
                invocation,original_owner = actual['InvocationID'],started_owner['owner']
            need(invocation == actual['InvocationID'] and original_owner == started_owner['owner']
                 and actual['ExecMainPID'] == str(original_owner['pid']))
            if state['ActiveState'] == 'active' and state['SubState'] == 'exited':
                snapshot = validate_stage_success(actual, owner, stage)
                need(group_empty(owner))
                try: now = native.process_identity(original_owner['pid'])
                except (FileNotFoundError,ProcessLookupError): now = None
                need(now is None or (now['boot_id'],now['start_ticks']) !=
                     (original_owner['boot_id'],original_owner['start_ticks']))
                break
            need(state['SubState'] in ('start','running'))
        time.sleep(1)
    else: raise c.Refused('retention_execution_requires_reconciliation')
    stop = {'schema':'careertrellis.onedrive-stage-stop/v1','transaction':transaction,'stage':stage,
        'unit':owner,'retained_success':snapshot,'owner':original_owner,
        'policy_sha256':context.bindings['standalone_policy_sha256']}
    hydrate.exclusive(journal / 'unit-stop-intent.json', bundle.canonical(stop))
    with c.control(c.STATE / 'db'):
        authenticated()
        need(stage_properties(owner) == snapshot and group_empty(owner))
        hydrate.command(['/usr/bin/systemctl','stop',owner], timeout=100)
    terminal(owner)
    hydrate.exclusive(journal / 'terminal.json',bundle.canonical(dict(stop,
        schema='careertrellis.onedrive-stage-terminal/v1',observed_at=dt.datetime.now(dt.timezone.utc).isoformat())))
    return {'result':'stage_terminal_collected','native_restore_accepted':False}


def collector_command(transaction, journal):
    paths = collector.coordinates(transaction, 1)
    return ['/usr/bin/systemd-run','--no-block','--unit=' + paths['collector_unit'],
        '--property=Type=exec','--property=Restart=no','--property=RemainAfterExit=no',
        '--property=UMask=0077','--property=TimeoutStartSec=30s','--property=RuntimeMaxSec=7500s',
        '--property=TimeoutStopSec=90s','--property=KillMode=control-group',
        '--property=SendSIGKILL=yes','--property=PrivateNetwork=yes',
        '--property=StandardOutput=append:' + str(journal / 'collector-output.log'),
        '--property=StandardError=append:' + str(journal / 'collector-output.log'),
        '/usr/bin/python3','-I','-B','/usr/local/libexec/careertrellis/onedrive_retention/entry.py',
        'collect-native','--transaction',transaction,'--attempt','1']


def dispatch_restore(transaction):
    """Called under endpoint control lock. Never resumed after any dispatch intent."""
    authenticated(); idle(transaction, 'restore')
    from . import builder, endpoint
    paths = collector.coordinates(transaction, 1)
    for unit in (paths['owner_unit'], paths['collector_unit']):
        state = show(unit)
        need(state['LoadState'] == 'not-found'); terminal(unit)
    journal = c.STATE / 'db' / transaction / 'restore'; c.directory(journal)
    need(not os.path.lexists(journal / 'native-dispatch-intent.json'))
    builder.write_plan(transaction)
    raw, plan = document(Path(plans.ROOT) / transaction / 'native-plan.json')
    plans.validate_plan(plan); need(plan['transaction'] == transaction and plan['attempt'] == 1)
    hydrate.exclusive(journal / 'collector-output.log', b'')
    hydrate.exclusive(journal / 'native-dispatch-intent.json', bundle.canonical({
        'schema':'careertrellis.onedrive-native-dispatch/v1','transaction':transaction,'attempt':1,
        'plan_sha256':hashlib.sha256(raw).hexdigest(),'owner_unit':paths['owner_unit'],
        'collector_unit':paths['collector_unit'],'dispatched_at':dt.datetime.now(dt.timezone.utc).isoformat()}))
    # Collector waits up to 30 seconds for the first owner appearance. If either
    # submission acknowledgement is lost, preserve both intents and reconcile.
    hydrate.command(collector_command(transaction, journal), timeout=10)
    ready_path = c.STATE / 'db' / transaction / 'collector' / 'intent.json'
    until = time.monotonic() + 10
    while not os.path.lexists(ready_path):
        need(time.monotonic() < until); time.sleep(.1)
    _, ready = document(ready_path)
    need(ready.get('transaction') == transaction and ready.get('attempt') == 1
         and ready.get('owner_unit') == paths['owner_unit'] and ready.get('collector_unit') == paths['collector_unit'])
    hydrate.command(endpoint.command_plan('restore', transaction, journal), timeout=10)
    return True


def run_command(command):
    """Authenticated installed CLI only; no injected native IO or paths."""
    operation, tx = command['operation'], command['transaction']
    if operation in ('health-web','health-db','schedule-check'):
        from . import health
        if operation == 'health-web':return health.run_web_health()
        if operation == 'schedule-check':return health.schedule_status()
        from paired_maintenance import provider
        with provider.repository_lock() as (fd,_identity):return health.database_health(fd)
    if operation == 'collect-identity':
        from . import identity_collect
        return identity_collect.collect(tx,command['attempt'],identity_collect.Native())
    if operation == 'collect-stage': return collect_stage(tx,command['stage'])
    if operation == 'collect-native': return collector.collect(tx, command['attempt'])
    if operation == 'web-run':
        policy = c.Policy(c.read_private(c.PRIVATE_POLICY))
        return c.run(tx, policy, c.NativeWEB(policy))
    need(operation == 'db-stage')
    stage = command['stage']
    require_stage_owner(tx, stage)
    if stage != 'restore':
        from . import endpoint
        unit = endpoint.unit(stage,tx); state = stage_properties(unit)
        context = authenticated()
        request_raw,_ = document(c.STATE/'db'/tx/stage/'intent.json')
        hydrate.exclusive(c.STATE/'db'/tx/stage/'owner-start.json',bundle.canonical({
            'schema':'careertrellis.onedrive-stage-owner/v1','transaction':tx,'stage':stage,
            'unit':unit,'owner':native.process_identity(os.getpid()),'invocation_id':state['InvocationID'],
            'request_sha256':hashlib.sha256(request_raw).hexdigest(),
            'policy_sha256':context.bindings['standalone_policy_sha256']}))
    if stage == 'hydrate': return hydrate.hydrate(tx, hydrate.Native())
    if stage == 'publish':
        from . import publish
        return publish.publish(tx, publish.Native())
    if stage == 'restore':
        _, plan = document(Path(plans.ROOT) / tx / 'native-plan.json')
        plans.validate_plan(plan); need(plan['transaction'] == tx and plan['attempt'] == 1)
        return native.execute(plan, plan['web_preflight_path'])
    if stage == 'prepare': return prepare_retirement(tx)
    from . import retire
    return retire.run(tx)
