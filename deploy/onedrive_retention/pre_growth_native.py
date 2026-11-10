"""Distinct finite retirement-only lifecycle, deliberately not promoted.

The sibling pre-growth journal never fabricates normal prepare/capture/restore
receipts. Actual parent/identity capabilities bind protected authenticated
ingress, original kernel births, typed units and the upfront watcher. Parent
fences close before identity children acquire their own canonical fences.
Conditional expiry delegates to the existing native retirement backend; actual
quota is measured afterward and grants no upload credit. Status is provider-free
and read-only; independent terminal sealing is a separate one-shot action.
"""
import copy
import datetime as dt
import hashlib
import os
from pathlib import Path
import socket
import re
import time

from . import activation, baseline, bundle, catalog, coordinator, policy, retire

ROOT = Path('/usr/local/libexec/careertrellis/onedrive_retention')
JOURNAL_ROOT = coordinator.STATE / 'pre-growth'
PURPOSE = 'retire_existing_before_growth'
SCHEMA = 'careertrellis.pre-growth-retirement-only-request/v1'
_SEAL = object()
_CHILD_SEAL = object()
OWNER_SECONDS=32400
WATCHER_SECONDS=OWNER_SECONDS+300
IDENTITY_SECONDS=900


class PreGrowthNativeRefused(ValueError):
    pass


def native_routes_available():
    """Installed-source capability declaration, never evidence or authority.

    Fixed installed source, profiles and actual authority remain mandatory. A
    caller, environment flag or supplied evidence cannot flip this declaration.
    """
    return True


def need(condition):
    if not condition: raise PreGrowthNativeRefused('pre_growth_native_authority_unavailable')


def unavailable():
    raise PreGrowthNativeRefused('pre_growth_native_adapters_unavailable')


def sha(value): return hashlib.sha256(bundle.canonical(value)).hexdigest()


def request(value):
    policy._keys(value, ('schema', 'operation', 'transaction', 'retention_policy_sha256',
                        'activation_sha256', 'web_history_sha256', 'allocation'))
    need(value['schema'] == SCHEMA and value['operation'] == PURPOSE)
    need(catalog.hex_value(value['transaction'], 32))
    for field in ('retention_policy_sha256', 'activation_sha256', 'web_history_sha256'):
        need(catalog.hex_value(value[field]))
    allocation = value['allocation']
    policy._keys(allocation, ('source_bytes', 'candidate_bytes', 'namespace_new_bytes'))
    for size in allocation.values(): need(type(size) is int and 0 <= size <= policy.NAMESPACE_BUDGET)
    need(allocation['candidate_bytes'] > 0 and allocation['namespace_new_bytes'] >= allocation['candidate_bytes'])
    return copy.deepcopy(value)


def coordinates(transaction):
    need(catalog.hex_value(transaction, 32))
    return {'owner_unit': 'careertrellis-onedrive-pre-growth-retire-' + transaction + '.service',
        'watcher_unit': 'careertrellis-onedrive-pre-growth-watch-' + transaction + '.service',
        'intent_root': str(JOURNAL_ROOT / transaction)}


def identity_unit(transaction,source,verification):
    need(catalog.hex_value(transaction,32) and catalog.hex_value(source,32)
        and type(verification) is int and 1<=verification<=99)
    return 'careertrellis-onedrive-pre-growth-identity-'+transaction+'-'+source+'-%02d.service'%verification


def _properties(unit):
    from . import common_io,execution,hydrate,native_restore
    fields=tuple(dict.fromkeys((*execution.FIELDS,*native_restore.OWNER_FIELDS,'ExecReload',
        'InvocationID','Result','ExecMainPID','ExecMainStatus','User','Group','UMask')))
    return common_io.read_unit_properties(unit,fields,lambda args:hydrate.command(args,output=True,timeout=10))


def validate_profile(state,role,unit,pid=None,terminal=False):
    """Pure typed-profile consistency; no passed snapshot issues authority."""
    from . import native_restore
    need(role in ('owner','watcher','identity'))
    seconds={'owner':OWNER_SECONDS,'watcher':WATCHER_SECONDS,'identity':IDENTITY_SECONDS}[role]
    need(state['LoadState']=='loaded' and state['Type']=='exec' and state['Restart']=='no'
        and state['RemainAfterExit']==('no' if role=='identity' else 'yes')
        and state['PrivateNetwork']==('yes' if role=='watcher' else 'no')
        and state['User']==state['Group']=='root' and state['UMask']=='0077'
        and state['KillMode']=='control-group' and state['SendSIGKILL']=='yes'
        and state['ControlPID']=='0' and state['Job']=='' and state['ControlGroup']=='/system.slice/'+unit
        and catalog.hex_value(state['InvocationID'],32)
        and native_restore.span_microseconds(state['RuntimeMaxUSec'])==seconds*1000000
        and native_restore.span_microseconds(state['TimeoutStartUSec'])==30000000
        and native_restore.span_microseconds(state['TimeoutStopUSec'])==(30 if role=='identity' else 90)*1000000
        and all(state[name]=='' for name in (*native_restore.OWNER_HOOK_FIELDS,'ExecReload')))
    if terminal:
        need(role!='identity' and state['ActiveState']=='active' and state['SubState']=='exited'
            and state['MainPID']=='0' and state['Result']=='success' and state['ExecMainStatus']=='0'
            and state['ExecMainPID']==str(pid))
    else:
        need(state['ActiveState']=='active' and state['SubState']=='running'
            and (pid is None or state['MainPID']==str(pid)))
    return True


def command(role,transaction,source=None,verification=None):
    """Fixed finite commands; construction neither dispatches nor authenticates."""
    need(role in ('owner','watcher','identity'));coords=coordinates(transaction)
    unit=identity_unit(transaction,source,verification) if role=='identity' else coords[role+'_unit']
    args=['/usr/bin/systemd-run','--wait' if role=='identity' else '--no-block','--unit='+unit,
        '--property=Type=exec','--property=User=root','--property=Group=root','--property=UMask=0077',
        '--property=Restart=no','--property=RemainAfterExit='+('no' if role=='identity' else 'yes'),
        '--property=RuntimeMaxSec='+str({'owner':OWNER_SECONDS,'watcher':WATCHER_SECONDS,'identity':IDENTITY_SECONDS}[role])+'s',
        '--property=TimeoutStartSec=30s','--property=TimeoutStopSec='+('30s' if role=='identity' else '90s'),
        '--property=KillMode=control-group','--property=SendSIGKILL=yes',
        '--property=PrivateNetwork='+('yes' if role=='watcher' else 'no'),
        '--property=StandardOutput=null','--property=StandardError=null',
        '/usr/bin/python3','-I','-B',str(ROOT/'entry.py'),
        {'owner':'pre-growth-owner','watcher':'pre-growth-watch','identity':'pre-growth-identity'}[role],
        '--transaction',transaction]
    if role=='identity':args+=['--source-transaction',source,'--verification',str(verification)]
    return args


def _current(role,transaction,source=None,verification=None):
    from . import native_restore
    _verify_source()
    unit=identity_unit(transaction,source,verification) if role=='identity' else coordinates(transaction)[role+'_unit']
    need(Path('/proc/self/cgroup').read_text('ascii').strip()=='0::/system.slice/'+unit)
    expected=command(role,transaction,source,verification)
    index=expected.index('/usr/bin/python3');wanted=expected[index:]
    need(Path('/proc/self/cmdline').read_bytes().split(b'\0')[:-1]==[value.encode() for value in wanted])
    state=_properties(unit);validate_profile(state,role,unit,os.getpid())
    need(os.environ.get('INVOCATION_ID')==state['InvocationID'])
    return state,native_restore.process_identity(os.getpid())


def _same_native_context(context,selected):
    _verify_source();current=baseline.native_context()
    need(type(current) is baseline.AuthenticatedContext and current.authenticated
        and current.document==context.document and current.bindings==context.bindings and current.role=='db')
    need(activation.require_activated(current).digest==selected.digest)


def _once(root,name,value):
    from . import hydrate
    hydrate.exclusive(root/name,bundle.canonical(value));return sha(value)


def _root(transaction):
    from . import coordinator
    coordinator.directory(JOURNAL_ROOT)
    root=JOURNAL_ROOT/transaction;os.mkdir(root,0o700)
    fd=os.open(JOURNAL_ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    coordinator.directory(root)
    return root


def existing_claims(records, retired, replacements, now):
    """Structural fixture validation, never authentication of native claims."""
    need(type(records) is dict and len(records) <= catalog.MAX_ENTRIES
         and type(retired) in (set, frozenset) and retired <= set(records)
         and type(replacements) is list and 2 <= len(replacements) <= 32)
    source_by_id = {}; native_seen = set(); terminal_seen = set()
    for identifier, row in records.items():
        need(catalog.hex_value(identifier) and type(row) is dict)
        accepted, evidence = row['entry'], row['evidence']
        native_row, download = evidence['native'], evidence['download']
        need(accepted['id'] == accepted['archive_sha256'] == identifier
             and accepted['complete'] is True and accepted['dependencies_resolved'] is True
             and accepted['download_sha256'] == accepted['restore_archive_sha256'] == identifier
             and native_row.get('schema') in ('careertrellis.onedrive-native-local-restore/v1',
                                              'careertrellis.onedrive-native-local-restore/v2')
             and native_row.get('result') == 'verified'
             and all(native_row.get(key) is True for key in ('native_restore_accepted', 'postmaster_terminal', 'checksums_verified'))
             and native_row['archive_sha256'] == identifier
             and accepted['native_restore_receipt_sha256'] == sha(native_row)
             and catalog.hex_value(evidence['terminal_proof_sha256'])
             and catalog.hex_value(download['transaction'], 32)
             and native_row['transaction'] == download['transaction']
             and policy.timestamp(accepted['accepted_at']) <= now)
        if identifier not in retired:
            need(accepted['native_restore_receipt_sha256'] not in native_seen
                 and evidence['terminal_proof_sha256'] not in terminal_seen)
            native_seen.add(accepted['native_restore_receipt_sha256']); terminal_seen.add(evidence['terminal_proof_sha256'])
            source_by_id[identifier] = download['transaction']
    need(len(source_by_id) >= 2 and len(set(source_by_id.values())) == len(source_by_id))
    selector_ids = set()
    for replacement in replacements:
        need(type(replacement) is dict and replacement['archive_sha256'] in records
             and replacement['archive_sha256'] not in selector_ids)
        identifier = replacement['archive_sha256']; row = records[identifier]
        need(replacement['native_receipt_sha256'] == row['entry']['native_restore_receipt_sha256']
             and replacement['terminal_proof_sha256'] == row['evidence']['terminal_proof_sha256']
             and replacement['backup_label'] == row['evidence']['native']['backup_label'])
        selector_ids.add(identifier)
    return {'schema': 'careertrellis.pre-growth-existing-checkpoints-claims/v1',
        'authenticated': False, 'native_actions': False, 'active_bundle_ids': sorted(source_by_id),
        'existing_sources': source_by_id}


def proposal(value, existing_catalog, now):
    """Plan against existing acceptance only, preserving normal retirement rules."""
    value = request(value)
    need(type(existing_catalog) is dict and len(existing_catalog.get('entries', [])) >= 2)
    plan = retire.prepare(existing_catalog, now.isoformat())
    return {'schema': 'careertrellis.pre-growth-retirement-only-proposal/v1', 'request': value,
        'coordinates': coordinates(value['transaction']), 'retirement': plan,
        'authenticated': False, 'dispatch_authorized': False, 'native_growth_authorized': False}


class ParentCapability:
    """Opaque native authority; parsed fixture dictionaries cannot issue it."""
    __slots__ = ('_request', '_sources', '_seal', '_binding')
    def __init__(self, value, sources, seal=None, binding=None):
        need(seal is _SEAL)
        self._request, self._sources, self._seal = bundle.canonical(request(value)), dict(sources), seal
        self._binding=binding
    @property
    def authenticated(self): return self._seal is _SEAL
    @property
    def transaction(self):
        import json
        return json.loads(self._request)['transaction']
    @property
    def parent_transaction(self):return self.transaction
    @property
    def unit(self):return coordinates(self.transaction)['owner_unit']
    @property
    def existing_sources(self):return dict(self._sources)
    @property
    def bindings(self):return dict(self._binding['context'].bindings)
    def require_identity(self, source_transaction, verification):
        require_parent(self)
        need(source_transaction in set(self._sources.values()) and type(verification) is int and 1 <= verification <= 99)
        # The live parent may plan a child, but cannot stand in for that child's
        # actual kernel/unit authority at the provider boundary.
        need(False)
    def require_retirement(self, transaction):
        require_parent(self); need(transaction == self.transaction)
        _state,owner=_current('owner',transaction)
        need(owner==self._binding['owner'])
        _children_closed(JOURNAL_ROOT/transaction,self._binding['owner'])
        need(_authenticated_ingress(self.transaction,self._binding['context'],self._binding['selector'])['intent_sha256']
            ==self._binding['intent_sha256'])
        return self._binding['context']


class IdentityCapability:
    """Distinct child authority, authenticated only in its actual finite unit."""
    __slots__=('_parent','_source','_verification','_owner','_seal')
    def __init__(self,parent,source,verification,owner,seal=None):
        need(seal is _CHILD_SEAL)
        self._parent,self._source,self._verification,self._owner,self._seal=parent,source,verification,owner,seal
    @property
    def authenticated(self):return self._seal is _CHILD_SEAL
    @property
    def transaction(self):return self._parent.transaction
    @property
    def parent_transaction(self):return self.transaction
    @property
    def unit(self):return identity_unit(self.transaction,self._source,self._verification)
    @property
    def bindings(self):return self._parent.bindings
    def require_identity(self,source_transaction,verification):
        require_parent(self)
        need((source_transaction,verification)==(self._source,self._verification))
        _identity_custody(self._parent,self._source,self._verification,self._owner)
        return self._parent._binding['context']
    def require_retirement(self,transaction):need(False)


def require_parent(value):
    need(type(value) in (ParentCapability,IdentityCapability) and value.authenticated is True)
    parent=value if type(value) is ParentCapability else value._parent
    need(parent._binding is not None and type(parent._binding['context']) is baseline.AuthenticatedContext
        and parent._binding['context'].authenticated)
    _same_native_context(parent._binding['context'],parent._binding['selector'])


def _verify_source():
    from . import entry, hydrate
    need(os.name == 'posix' and os.geteuid() == os.getegid() == 0
         and socket.gethostname() == 'job-applier-db-staging-01'
         and Path(__file__).absolute() == ROOT / 'pre_growth_native.py')
    revision = entry.verify_install()
    need('pre_growth_native.py' in entry.FILES)
    raw, manifest = entry._protected_document(ROOT / 'manifest.json', 0o644)
    need(manifest['revision'] == revision and bundle.canonical(manifest) == raw
         and 'pre_growth_native.py' in manifest['files'])
    source, _ = hydrate.protected(ROOT / 'pre_growth_native.py', 0o644)
    need(hashlib.sha256(source).hexdigest() == manifest['files']['pre_growth_native.py'])


def _authenticated_ingress(transaction, context, selected):
    """Read fixed protected ingress only; unsupported owner/history is a fence.

    The paired authenticated endpoint must create these documents, never this
    reader. A root-owned file alone does not authenticate a peer witness.
    """
    from . import common_io, execution
    need(type(context) is baseline.AuthenticatedContext and context.authenticated
        and type(selected) is activation.AuthenticatedSelector and selected.authenticated)
    root = JOURNAL_ROOT / transaction
    documents = {}
    for name in ('intent', 'owner-start', 'dispatch', 'watcher-start'):
        raw, value = execution.document(root / (name + '.json'))
        need(raw == bundle.canonical(value))
        documents[name] = value
    units = coordinates(transaction)
    common_io.unit_object_path(units['owner_unit'])
    common_io.unit_object_path(units['watcher_unit'])
    _raw,provenance=execution.document(root/'ingress-provenance.json')
    policy._keys(provenance,('schema','transaction','intent_sha256','outer_request_sha256',
        'endpoint_owner','paired_entry_sha256','baseline_bindings','received_at'))
    from . import entry,hydrate,native_restore
    need(provenance['schema']=='careertrellis.pre-growth-authenticated-ingress/v1'
        and provenance['transaction']==transaction and provenance['intent_sha256']==sha(documents['intent'])
        and provenance['baseline_bindings']==context.bindings
        and provenance['paired_entry_sha256']==hashlib.sha256(hydrate.protected(entry.PAIRED_ROOT/'entry.py',0o644)[0]).hexdigest())
    catalog.process_identity(provenance['endpoint_owner'])
    _raw,admission=execution.document(root/'admission.json')
    need(admission=={'schema':'careertrellis.pre-growth-owner-admission/v1','transaction':transaction,
        'intent_sha256':sha(documents['intent']),'owner':documents['owner-start']['owner'],
        'admitted_at':admission['admitted_at']})
    admitted=policy.timestamp(admission['admitted_at'])
    need(policy.timestamp(provenance['received_at'])<=admitted
        and 0<=(admitted-policy.timestamp(documents['intent']['web_history']['observed_at'])).total_seconds()<=120)
    owner_state=_properties(units['owner_unit']);watcher_state=_properties(units['watcher_unit'])
    owner=documents['owner-start']['owner']
    need(native_restore.process_identity(owner['pid'])==owner)
    watcher=documents['watcher-start']['owner'];need(native_restore.process_identity(watcher['pid'])==watcher)
    result=ingress_claims(documents,owner_state,watcher_state,owner,context.bindings,selected.digest,admitted)
    need(result['request']['transaction']==transaction)
    return result


def ingress_claims(documents, owner_state, watcher_state, current_owner, bindings,
                   activation_sha256, now):
    """Closed evidence contract for the future reader; never grants authority.

    Inputs are unauthenticated fixtures until endpoint provenance AND complete
    persistent-history collection are implemented. No synthesized normal-stage
    documents or caller-controlled paths are accepted by the native reader.
    """
    from . import catalog, guard, native_restore
    policy._keys(documents, ('intent', 'owner-start', 'dispatch', 'watcher-start'))
    intent = documents['intent']
    policy._keys(intent, ('schema', 'request', 'web_history'))
    need(intent['schema'] == 'careertrellis.pre-growth-ingress/v1')
    value = request(intent['request']); tx = value['transaction']; units = coordinates(tx)
    need(value['activation_sha256'] == activation_sha256
         and value['retention_policy_sha256'] == bindings['standalone_policy_sha256'])
    witness = guard.validate_peer_history(intent['web_history'], tx, PURPOSE,
        bindings['standalone_policy_sha256'], now=now)
    need(witness['baseline_bindings'] == bindings and sha(witness) == value['web_history_sha256'])
    digest = sha(intent)
    for name, role, state, remain, network, seconds in (
            ('owner-start', 'owner', owner_state, 'yes', 'no', OWNER_SECONDS),
            ('watcher-start', 'watcher', watcher_state, 'yes', 'yes', WATCHER_SECONDS)):
        birth = documents[name]
        policy._keys(birth, ('schema', 'transaction', 'purpose', 'unit', 'owner',
                            'invocation_id', 'intent_sha256', 'policy_sha256'))
        need(birth['schema'] == 'careertrellis.pre-growth-' + role + '-birth/v1'
             and birth['transaction'] == tx and birth['purpose'] == PURPOSE
             and birth['unit'] == units[role + '_unit'] and birth['intent_sha256'] == digest
             and birth['policy_sha256'] == bindings['standalone_policy_sha256'])
        catalog.process_identity(birth['owner'])
        need(state['LoadState'] == 'loaded' and state['ActiveState'] == 'active'
             and state['SubState'] == 'running' and state['MainPID'] == str(birth['owner']['pid'])
             and state['ControlPID'] == '0' and state['Job'] == ''
             and state['InvocationID'] == birth['invocation_id'] and catalog.hex_value(birth['invocation_id'], 32)
             and state['ControlGroup'] == '/system.slice/' + birth['unit']
             and state['Type'] == 'exec' and state['Restart'] == 'no'
             and state['RemainAfterExit'] == remain and state['PrivateNetwork'] == network
             and state['KillMode'] == 'control-group' and state['SendSIGKILL'] == 'yes'
             and native_restore.span_microseconds(state['RuntimeMaxUSec']) == seconds*1000000
             and native_restore.span_microseconds(state['TimeoutStartUSec']) == 30000000
             and native_restore.span_microseconds(state['TimeoutStopUSec']) == 90000000
             and all(state[field] == '' for field in native_restore.OWNER_HOOK_FIELDS))
    need(documents['owner-start']['owner'] == current_owner
         and documents['owner-start']['owner'] != documents['watcher-start']['owner'])
    dispatch = documents['dispatch']
    policy._keys(dispatch, ('schema', 'transaction', 'purpose', 'intent_sha256', 'owner_unit', 'watcher_unit'))
    need(dispatch == {'schema': 'careertrellis.pre-growth-dispatch/v1', 'transaction': tx,
        'purpose': PURPOSE, 'intent_sha256': digest, 'owner_unit': units['owner_unit'],
        'watcher_unit': units['watcher_unit']})
    return {'schema': 'careertrellis.pre-growth-ingress-claims/v1', 'request': value,
        'intent_sha256': digest, 'authenticated': False, 'history_authenticated': False,
        'dispatch_authorized': False}


def _existing_under_fences(context, selected):
    from . import entry,hydrate
    # Reuse exact existing ordered lock implementation only for protected local
    # catalog reads. No normal-stage snapshot, provider call or admission occurs.
    holder = hydrate.Native()
    with holder.locked():
        store=catalog.NativeReadStore(holder.lock_fd)
        with store.io.locked():
            records = store.records(); retired_ids = store.retired(records)
        _, selector_intent = entry._protected_document(activation.INTENT, 0o600)
        fresh = activation.require_activated(context)
        need(fresh.digest == selected.digest)
        return existing_claims(records, retired_ids, selector_intent['replacement_bundles'], dt.datetime.now(dt.timezone.utc))


def native_parent(transaction):
    """No native capability is issued while required route ingress is missing."""
    need(catalog.hex_value(transaction, 32))
    try:
        _verify_source()
        context = baseline.native_context()
        need(type(context) is baseline.AuthenticatedContext and context.authenticated and context.role == 'db')
        selected = activation.require_activated(context)
        need(type(selected) is activation.AuthenticatedSelector and selected.authenticated and selected.role == 'db')
        _state,owner=_current('owner',transaction)
        ingress = _authenticated_ingress(transaction, context, selected)  # Explicitly unavailable today.
        claimed = _existing_under_fences(context, selected)
        need(ingress['request']['transaction'] == transaction
             and ingress['request']['activation_sha256'] == selected.digest
             and ingress['request']['retention_policy_sha256'] == context.bindings['standalone_policy_sha256'])
        return ParentCapability(ingress['request'], claimed['existing_sources'], _SEAL,
            {'context':context,'selector':selected,'intent_sha256':ingress['intent_sha256'],'owner':owner})
    except Exception:
        raise PreGrowthNativeRefused('pre_growth_native_authority_unavailable') from None


def run(transaction):
    return owner_main(transaction)


def _identity_custody(parent,source,verification,owner):
    from . import execution,native_restore
    require_parent(parent)
    state,current=_current('identity',parent.transaction,source,verification);need(current==owner)
    need(source in set(parent._sources.values()))
    ingress=_authenticated_ingress(parent.transaction,parent._binding['context'],parent._binding['selector'])
    need(ingress['intent_sha256']==parent._binding['intent_sha256'])
    root=JOURNAL_ROOT/parent.transaction;prefix='child-'+source+'-%02d'%verification
    _raw,intent=execution.document(root/(prefix+'-intent.json'))
    policy._keys(intent,('schema','transaction','source_transaction','verification','unit','intent_sha256',
        'parent_owner','watcher_owner','policy_sha256'))
    _raw,parent_birth=execution.document(root/'owner-start.json');_raw,watch_birth=execution.document(root/'watcher-start.json')
    need(intent=={'schema':'careertrellis.pre-growth-identity-dispatch/v1','transaction':parent.transaction,
        'source_transaction':source,'verification':verification,'unit':identity_unit(parent.transaction,source,verification),
        'intent_sha256':parent._binding['intent_sha256'],'parent_owner':parent_birth['owner'],
        'watcher_owner':watch_birth['owner'],'policy_sha256':parent.bindings['standalone_policy_sha256']})
    _raw,birth=execution.document(root/(prefix+'-start.json'))
    need(birth=={'schema':'careertrellis.pre-growth-identity-birth/v1','transaction':parent.transaction,
        'source_transaction':source,'verification':verification,'unit':intent['unit'],'owner':owner,
        'invocation_id':state['InvocationID'],'dispatch_sha256':sha(intent)})
    need(native_restore.process_identity(parent_birth['owner']['pid'])==parent_birth['owner'])
    return True


def native_identity(transaction,source,verification):
    """Issue child capability only from fixed ingress and actual current unit."""
    from . import execution
    _state,owner=_current('identity',transaction,source,verification)
    context=baseline.native_context();selected=activation.require_activated(context)
    ingress=_authenticated_ingress(transaction,context,selected)
    _raw,saved=execution.document(JOURNAL_ROOT/transaction/'existing-sources.json')
    _raw,parent_birth=execution.document(JOURNAL_ROOT/transaction/'owner-start.json')
    need(saved['intent_sha256']==ingress['intent_sha256'] and saved['owner']==parent_birth['owner']
        and saved['baseline_bindings']==context.bindings and saved['policy_sha256']==context.bindings['standalone_policy_sha256'])
    parent=ParentCapability(ingress['request'],saved['sources'],_SEAL,
        {'context':context,'selector':selected,'intent_sha256':ingress['intent_sha256'],'owner':parent_birth['owner']})
    result=IdentityCapability(parent,source,verification,owner,_CHILD_SEAL)
    result.require_identity(source,verification);return result


native_child=native_identity


STATIC_FILES=frozenset(('intent.json','ingress-provenance.json','dispatch.json','watcher-start.json',
    'owner-start.json','admission.json','existing-sources.json','expiry-check.json','allocation.json','quota-before.json',
    'quota-after.json','retirement-result.json','complete.json','failed.json','watcher-terminal.json','terminal-proof.json'))
CHILD_FILE=re.compile(r'child-([0-9a-f]{32})-([0-9]{2})-(intent|start|launch-ack|terminal|unknown)\.json\Z')


def _names(root):
    import itertools
    names=[path.name for path in itertools.islice(root.iterdir(),10001)]
    need(len(names)<=10000 and len(set(names))==len(names))
    need(all(name in STATIC_FILES or CHILD_FILE.fullmatch(name) for name in names))
    need('failed.json' not in names and not any(name.endswith('-unknown.json') for name in names))
    from . import execution
    for name in names:execution.document(root/name)
    return set(names)


def _children_closed(root,parent_owner,*,selected=None):
    from . import execution,native_restore,local_restore_plan
    names=_names(root);result=[]
    prefixes={name[:-len('-intent.json')] for name in names if CHILD_FILE.fullmatch(name) and name.endswith('-intent.json')}
    all_prefixes=set(prefixes)
    if selected is not None:
        wanted='child-'+selected[0]+'-%02d'%selected[1];need(wanted in prefixes);prefixes={wanted}
    for prefix in sorted(prefixes):
        need({prefix+'-'+suffix+'.json' for suffix in ('intent','start','launch-ack','terminal')}<=names)
        _,intent=execution.document(root/(prefix+'-intent.json'));_,birth=execution.document(root/(prefix+'-start.json'))
        _,ack=execution.document(root/(prefix+'-launch-ack.json'));_,terminal=execution.document(root/(prefix+'-terminal.json'))
        tx=root.name;source=intent['source_transaction'];verification=intent['verification'];unit=identity_unit(tx,source,verification)
        need(prefix=='child-'+source+'-%02d'%verification and intent['parent_owner']==parent_owner
            and intent['transaction']==tx and intent['unit']==unit
            and birth['dispatch_sha256']==sha(intent) and birth['unit']==unit
            and ack=={'schema':'careertrellis.pre-growth-child-exit/v1','transaction':tx,'dispatch_sha256':sha(intent),
                'birth_sha256':sha(birth),'exit_code':0,'parent_owner':parent_owner}
            and type(ack['exit_code']) is int
            and terminal['schema']=='careertrellis.pre-growth-identity-terminal/v1' and terminal['transaction']==tx
            and terminal['source_transaction']==source and terminal['verification']==verification
            and terminal['dispatch_sha256']==sha(intent) and terminal['birth_sha256']==sha(birth)
            and terminal['parent_owner']==parent_owner)
        _,identity=execution.document(Path(local_restore_plan.ROOT)/source/('bundle-object-identity-%02d.json'%verification))
        need(terminal['identity_sha256']==sha(identity) and execution.group_empty(unit))
        state=_properties(unit)
        if state['LoadState']=='loaded':
            need(state['MainPID']==state['ControlPID']=='0' and state['Job']=='' and state['Result']=='success'
                and state['ExecMainStatus']=='0' and state['ExecMainPID']==str(birth['owner']['pid'])
                and state['InvocationID']==birth['invocation_id'])
        else:need(state['LoadState']=='not-found')
        try:current=native_restore.process_identity(birth['owner']['pid'])
        except (FileNotFoundError,ProcessLookupError):current=None
        need(current!=birth['owner'])
        result.append({'source_transaction':source,'verification':verification,'terminal_sha256':sha(terminal)})
    need(all(CHILD_FILE.fullmatch(name) is None or any(name.startswith(prefix+'-') for prefix in all_prefixes) for name in names))
    return result


def identity_history_complete(transaction,source,verification,current_transaction=None,repository_lock_fd=None,*,identity_owner=None):
    """One closed child under the authenticated live parent, or old terminal parent."""
    from . import execution
    need(catalog.hex_value(transaction,32) and catalog.hex_value(source,32)
        and type(verification) is int and 1<=verification<=99)
    history_complete(transaction,current_transaction,repository_lock_fd)
    root=JOURNAL_ROOT/transaction;_,parent=execution.document(root/'owner-start.json')
    _children_closed(root,parent['owner'],selected=(source,verification))
    _,birth=execution.document(root/('child-'+source+'-%02d-start.json'%verification))
    if identity_owner is not None:
        need(identity_owner=={'pid':birth['owner']['pid'],'start_ticks':birth['owner']['start_ticks'],
            'boot_id':birth['owner']['boot_id'],'unit':birth['unit'],'invocation_id':birth['invocation_id']})
    return True


def history_complete(transaction,current_transaction=None,repository_lock_fd=None,*,require_live=False):
    """Provider-free dedicated sibling history; parsed current-TX claims do not exempt it.

    A current exception additionally proves this actual PID's owner/identity
    unit, original live parent/watcher and immutable ingress. Other unfinished
    transactions always refuse, including dispatches with unknown outcomes.
    """
    from . import execution,native_restore
    need(catalog.hex_value(transaction,32));root=JOURNAL_ROOT/transaction;names=_names(root)
    for required in ('intent.json','dispatch.json','ingress-provenance.json'):need(required in names)
    if current_transaction==transaction and 'complete.json' not in names:
        group=Path('/proc/self/cgroup').read_text('ascii').strip()
        parent_unit=coordinates(transaction)['owner_unit']
        if group=='0::/system.slice/'+parent_unit:
            _current('owner',transaction)
        else:
            match=re.fullmatch('0::/system.slice/careertrellis-onedrive-pre-growth-identity-'+transaction+
                r'-([0-9a-f]{32})-([0-9]{2})\.service',group)
            need(match is not None);native_identity(transaction,match[1],int(match[2]))
        context=baseline.native_context();selected=activation.require_activated(context)
        _authenticated_ingress(transaction,context,selected)
        if repository_lock_fd is not None:
            from . import preservation
            preservation.require_owned_lock('/run/lock/careertrellis-pgbackrest-repository.lock',repository_lock_fd)
        return True
    need({'complete.json','watcher-terminal.json','owner-start.json','watcher-start.json','admission.json',
        'existing-sources.json','expiry-check.json','allocation.json','quota-after.json','retirement-result.json'}<=names)
    _,done=execution.document(root/'complete.json');_,watch=execution.document(root/'watcher-terminal.json')
    _,intent=execution.document(root/'intent.json')
    need(done['schema']=='careertrellis.pre-growth-complete/v1' and done['transaction']==transaction
        and done['intent_sha256']==sha(intent) and done['growth_authorized'] is False
        and done['quota_after_sha256']==sha(execution.document(root/'quota-after.json')[1])
        and done['retirement_sha256']==sha(execution.document(root/'retirement-result.json')[1])
        and done['allocation_sha256']==sha(execution.document(root/'allocation.json')[1])
        and watch['owner_complete_sha256']==sha(done) and watch['transaction']==transaction)
    need(done['children']==_children_closed(root,done['owner']))
    need(watch['schema']=='careertrellis.pre-growth-watcher-terminal/v1' and watch['intent_sha256']==sha(intent)
        and watch['owner']==done['owner'] and watch['growth_authorized'] is False)
    proof=None
    if 'terminal-proof.json' in names:
        _,proof=execution.document(root/'terminal-proof.json')
        policy._keys(proof,('schema','transaction','intent_sha256','complete_sha256','watcher_terminal_sha256',
            'birth_sha256','snapshots','observer','observed_at','baseline_bindings'))
        need(proof['schema']=='careertrellis.pre-growth-independent-terminal/v1' and proof['transaction']==transaction
            and proof['intent_sha256']==sha(intent) and proof['complete_sha256']==sha(done)
            and proof['watcher_terminal_sha256']==sha(watch))
        policy._keys(proof['birth_sha256'],('owner','watcher'));policy._keys(proof['snapshots'],('owner','watcher'))
        _,origin=execution.document(root/'ingress-provenance.json')
        need(proof['baseline_bindings']==origin['baseline_bindings']
            and policy.timestamp(done['completed_at'])<=policy.timestamp(proof['observed_at'])<=dt.datetime.now(dt.timezone.utc))
        catalog.process_identity(proof['observer'])
    else:need(require_live)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text('ascii').strip()
    for role in ('owner','watcher'):
        _,birth=execution.document(root/(role+'-start.json'));unit=coordinates(transaction)[role+'_unit']
        need((birth['owner']==done['owner']) if role=='owner' else (birth['owner']==watch['watcher_owner']))
        state=_properties(unit)
        if proof is not None:
            saved=proof['snapshots'][role];validate_profile(saved,role,unit,birth['owner']['pid'],terminal=True)
            need(proof['birth_sha256'][role]==sha(birth) and saved['InvocationID']==birth['invocation_id'])
            if birth['owner']['boot_id']!=boot and state['LoadState']=='not-found':state=saved
            else:need(state==saved)
        validate_profile(state,role,unit,birth['owner']['pid'],terminal=True)
        if role=='owner':need(watch['owner_snapshot']==state)
        need(state['InvocationID']==birth['invocation_id'] and execution.group_empty(unit))
        try:current=native_restore.process_identity(birth['owner']['pid'])
        except (FileNotFoundError,ProcessLookupError):current=None
        need(current!=birth['owner'])
    for name in names:
        match=CHILD_FILE.fullmatch(name)
        if match:
            prefix='child-'+match[1]+'-'+match[2]
            need({prefix+'-'+suffix+'.json' for suffix in ('intent','start','launch-ack','terminal')}<=names)
    return True


def verify_complete(transaction):
    """Independent one-shot terminal sealing; never invoked by status polling."""
    _verify_source()
    from . import execution,entry,native_restore
    wanted=['/usr/bin/python3','-I','-B',str(entry.PAIRED_ROOT/'entry.py'),'db-endpoint']
    need(Path('/proc/self/cmdline').read_bytes().split(b'\0')[:-1]==[value.encode() for value in wanted])
    context=baseline.native_context();activation.require_activated(context)
    root=JOURNAL_ROOT/transaction;need(not os.path.lexists(root/'terminal-proof.json'))
    history_complete(transaction,require_live=True)
    _,intent=execution.document(root/'intent.json');_,done=execution.document(root/'complete.json')
    _,watch=execution.document(root/'watcher-terminal.json')
    _,origin=execution.document(root/'ingress-provenance.json');need(origin['baseline_bindings']==context.bindings)
    births={role:execution.document(root/(role+'-start.json'))[1] for role in ('owner','watcher')}
    snapshots={role:_properties(coordinates(transaction)[role+'_unit']) for role in births}
    for role in births:validate_profile(snapshots[role],role,coordinates(transaction)[role+'_unit'],births[role]['owner']['pid'],terminal=True)
    value={'schema':'careertrellis.pre-growth-independent-terminal/v1','transaction':transaction,
        'intent_sha256':sha(intent),'complete_sha256':sha(done),'watcher_terminal_sha256':sha(watch),
        'birth_sha256':{role:sha(birth) for role,birth in births.items()},'snapshots':snapshots,
        'observer':native_restore.process_identity(os.getpid()),'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'baseline_bindings':context.bindings}
    _once(root,'terminal-proof.json',value);history_complete(transaction)
    return {'transaction':transaction,'state':'complete','terminal_proof_sha256':sha(value),'growth_authorized':False}


def retirement_history_complete(transaction,current_transaction=None,repository_lock_fd=None):
    """Exact pre-growth retirement ledger, never a fabricated normal stage."""
    from . import execution
    need(catalog.hex_value(transaction,32));root=retire.Native.ROOT/transaction
    raw,operation=execution.document(root/'operation-intent.json',32*1024**2)
    need(operation['schema']=='careertrellis.bundle-retirement-native-intent/v1'
        and operation['transaction']==transaction and operation['namespace']==retire.NAMESPACE
        and operation['purpose']=='pre_growth_retirement' and operation['simulation'] is False
        and operation['expiry_mode']=='conditional_permanent')
    plan=retire.prepare(operation['catalog'],operation['plan']['planned_at'])
    need(plan==operation['plan'] and operation['catalog_sha256']==plan['catalog_sha256'])
    names={path.name for path in root.iterdir()}
    need(0<len(names)<=10000 and all(name in ('operation-intent.json','operation-complete.json')
        or re.fullmatch(r'object-[0-9]{5}-(intent|dispatch|ack|terminal)\.json',name) for name in names))
    current=False
    if current_transaction==transaction:
        _state,owner=_current('owner',transaction)
        context=baseline.native_context();selected=activation.require_activated(context)
        _authenticated_ingress(transaction,context,selected);_names(JOURNAL_ROOT/transaction)
        current=True
    expected=[(candidate['bundle_id'],obj) for candidate in plan['candidates'] for obj in candidate['objects']]
    terminals=[]
    for index,(bundle_id,obj) in enumerate(expected):
        prefix='object-%05d-'%index;required={prefix+phase+'.json' for phase in ('intent','dispatch','ack','terminal')}
        if not required<=names:
            need(current and 'operation-complete.json' not in names)
            phases=('intent','dispatch','ack','terminal')
            present=[phase for phase in phases if prefix+phase+'.json' in names]
            need(present==list(phases[:len(present)]))
            if 'intent' in present:
                partial_raw,partial=execution.document(root/(prefix+'intent.json'))
                need(partial['transaction']==transaction and partial['sequence']==index
                    and partial['object']==obj and partial['bundle_id']==bundle_id
                    and partial['operation_intent_sha256']==hashlib.sha256(raw).hexdigest()
                    and partial['simulation'] is False and partial['expiry_mode']=='conditional_permanent')
            if 'dispatch' in present:
                dispatch=execution.document(root/(prefix+'dispatch.json'))[1]
                need(dispatch=={'intent_sha256':hashlib.sha256(partial_raw).hexdigest(),'simulation':False}
                    and dispatch['simulation'] is False)
            if 'ack' in present:
                ack=execution.document(root/(prefix+'ack.json'))[1]
                need(ack.get('recoverable') is False and ack.get('permanent') is True
                    and ack.get('expiry_mode')=='conditional_permanent'
                    and ack.get('request_id')==hashlib.sha256(partial_raw).hexdigest()
                    and ack.get('object_id')==obj['object_id'] and ack.get('result')=='deleted')
            # The exact still-live owner may be between durable dispatch and
            # its sole response. This grants no retry or missing-object credit.
            need(not any(name.startswith('object-') and int(name[7:12])>index for name in names))
            return True
        intent_raw,intent=execution.document(root/(prefix+'intent.json'))
        dispatch=execution.document(root/(prefix+'dispatch.json'))[1]
        ack_raw,ack=execution.document(root/(prefix+'ack.json'))
        terminal_raw,terminal=execution.document(root/(prefix+'terminal.json'))
        catalog.validate_retirement_object_mode(operation,intent,ack,terminal,obj['object_id'],hashlib.sha256(intent_raw).hexdigest())
        need(intent['transaction']==transaction and intent['sequence']==index and intent['bundle_id']==bundle_id
            and intent['schema']=='careertrellis.bundle-object-retirement-intent/v1' and intent['namespace']==retire.NAMESPACE
            and type(intent['sequence']) is int
            and intent['object']==obj and intent['operation_intent_sha256']==hashlib.sha256(raw).hexdigest()
            and intent['simulation'] is False and intent['expiry_mode']=='conditional_permanent'
            and dispatch=={'intent_sha256':hashlib.sha256(intent_raw).hexdigest(),'simulation':False} and dispatch['simulation'] is False
            and ack=={'result':'deleted','object_id':obj['object_id'],'request_id':hashlib.sha256(intent_raw).hexdigest(),
                'recoverable':False,'permanent':True,'expiry_mode':'conditional_permanent'}
            and terminal['transaction']==transaction and terminal['sequence']==index and terminal['object']==obj
            and terminal['schema']=='careertrellis.bundle-object-retirement-terminal/v1'
            and terminal['namespace']==retire.NAMESPACE and terminal['bundle_id']==bundle_id and type(terminal['sequence']) is int
            and terminal['intent_sha256']==hashlib.sha256(intent_raw).hexdigest()
            and terminal['dispatch_ack_sha256']==hashlib.sha256(ack_raw).hexdigest()
            and terminal['absent'] is True and terminal['simulation'] is False
            and terminal['expiry_mode']=='conditional_permanent')
        terminals.append(hashlib.sha256(terminal_raw).hexdigest())
    need({int(name[7:12]) for name in names if name.startswith('object-')}==set(range(len(expected))))
    if current and 'operation-complete.json' not in names:return True
    _,complete=execution.document(root/'operation-complete.json',32*1024**2)
    need(complete['schema']=='careertrellis.bundle-retirement-native/v1' and complete['result']=='complete'
        and complete['transaction']==transaction and complete['native_retirement_accepted'] is True
        and complete['expiry_mode']=='conditional_permanent' and complete['deletions_performed']==len(expected)
        and type(complete['deletions_performed']) is int
        and complete['operation_intent_sha256']==hashlib.sha256(raw).hexdigest()
        and complete['terminal_receipt_sha256']==terminals)
    if current:return True
    history_complete(transaction)
    need(execution.document(JOURNAL_ROOT/transaction/'retirement-result.json')[1]==complete)
    need(repository_lock_fd is not None)
    store=catalog.NativeReadStore(repository_lock_fd)
    with store.io.locked():need({row['bundle_id'] for row in plan['candidates']}<=store.retired(store.records()))
    return True


def expiry_capability_idle(repository_fd):
    """Actual initial parent admission, not a caller's idle assertion."""
    from . import execution,preservation
    group=Path('/proc/self/cgroup').read_text('ascii').strip()
    match=re.fullmatch(r'0::/system.slice/careertrellis-onedrive-pre-growth-retire-([0-9a-f]{32})\.service',group)
    need(match is not None);tx=match[1];_current('owner',tx)
    for path in preservation.HISTORICAL_LOCKS:
        preservation.require_owned_lock(path,repository_fd if path.endswith('repository.lock') else None)
    need(not any(CHILD_FILE.fullmatch(name) for name in _names(JOURNAL_ROOT/tx)))
    need(not os.path.lexists(JOURNAL_ROOT/tx/'expiry-check.json'))
    context=baseline.native_context();selected=activation.require_activated(context)
    _authenticated_ingress(tx,context,selected)
    for name in execution.census():execution.terminal(name)
    history_census(tx,repository_fd)
    from . import guard
    observed=guard.native_history_snapshot(tx,PURPOSE,repository_fd)
    need(observed['complete'] is True and observed['unresolved']==[])
    return True


def history_census(current_transaction=None,repository_lock_fd=None):
    """Exhaustive fixed-root/unit census; no provider, lock acquisition or writes."""
    from . import execution,hydrate,native_restore,common_io
    import itertools
    coordinator.directory(JOURNAL_ROOT)
    roots=list(itertools.islice(JOURNAL_ROOT.iterdir(),10001));need(len(roots)<=10000)
    transactions=set()
    for root in roots:
        if root.name=='control.lock':
            need(hydrate.protected(root,0o600,limit=0)[0]==b'');continue
        need(catalog.hex_value(root.name,32));transactions.add(root.name)
        history_complete(root.name,current_transaction,repository_lock_fd)
    raw=hydrate.command(['/usr/bin/systemctl','list-units','--all','--no-legend','--plain',
        'careertrellis-onedrive-pre-growth-*.service'],output=True,timeout=10)
    need(len(raw)<=1024**2)
    def check_unit(unit):
        common_io.unit_object_path(unit)
        match=re.fullmatch(r'careertrellis-onedrive-pre-growth-(?:retire|watch)-([0-9a-f]{32})\.service',unit)
        if match is not None:need(match[1] in transactions);return
        match=re.fullmatch(r'careertrellis-onedrive-pre-growth-identity-([0-9a-f]{32})-([0-9a-f]{32})-([0-9]{2})\.service',unit)
        need(match is not None and match[1] in transactions
            and os.path.lexists(JOURNAL_ROOT/match[1]/('child-'+match[2]+'-'+match[3]+'-intent.json')))
    units=set()
    for line in raw.decode('ascii').splitlines():
        fields=line.split();need(fields);unit=fields[0];check_unit(unit);units.add(unit)
    parent=Path('/sys/fs/cgroup/system.slice');native_restore.directory(parent)
    groups=list(itertools.islice(parent.glob('careertrellis-onedrive-pre-growth-*'),10001));need(len(groups)<=10000)
    for group in groups:
        check_unit(group.name)
        need(group.name in units or execution.group_empty(group.name))
    return {'schema':'careertrellis.pre-growth-history-census/v1','transactions':sorted(transactions),
        'unit_count':len(units),'complete':True,'growth_authorized':False}


def submit(raw):
    """Authenticated WEB ingress, one upfront watcher and one finite owner."""
    need(native_routes_available());_verify_source()
    from . import entry,execution,guard,hydrate,native_restore
    context=baseline.native_context();selected=activation.require_activated(context)
    wanted=['/usr/bin/python3','-I','-B',str(entry.PAIRED_ROOT/'entry.py'),'db-endpoint']
    need(Path('/proc/self/cmdline').read_bytes().split(b'\0')[:-1]==[item.encode() for item in wanted])
    need(type(raw) is bytes and 0<len(raw)<=16384)
    outer=entry.decode(raw,16384);need(raw==bundle.canonical(outer))
    from paired_maintenance import db_control
    db_control.validate_request(outer)
    need(outer['policy_sha256']==context.bindings['paired_policy_sha256'] and outer['operation']==PURPOSE)
    payload=outer['payload'];policy._keys(payload,('request','web_history'))
    value=request(payload['request']);tx=value['transaction']
    need(outer['transaction']==tx and value['activation_sha256']==selected.digest
        and value['retention_policy_sha256']==context.bindings['standalone_policy_sha256']
        and value['web_history_sha256']==sha(payload['web_history']))
    guard.validate_peer_history(payload['web_history'],tx,PURPOSE,value['retention_policy_sha256'])
    holder=hydrate.Native()
    with holder.locked():
        observed=guard.native_history_snapshot(tx,PURPOSE,holder.lock_fd)
        need(observed['complete'] is True and observed['unresolved']==[])
        coordinator.directory(JOURNAL_ROOT);need(not os.path.lexists(JOURNAL_ROOT/tx))
        root=_root(tx);intent={'schema':'careertrellis.pre-growth-ingress/v1','request':value,'web_history':payload['web_history']}
        _once(root,'intent.json',intent)
        _once(root,'ingress-provenance.json',{'schema':'careertrellis.pre-growth-authenticated-ingress/v1',
            'transaction':tx,'intent_sha256':sha(intent),'outer_request_sha256':sha(outer),
            'endpoint_owner':native_restore.process_identity(os.getpid()),'paired_entry_sha256':hashlib.sha256(
                hydrate.protected(entry.PAIRED_ROOT/'entry.py',0o644)[0]).hexdigest(),
            'baseline_bindings':context.bindings,'received_at':dt.datetime.now(dt.timezone.utc).isoformat()})
        coords=coordinates(tx)
        _once(root,'dispatch.json',{'schema':'careertrellis.pre-growth-dispatch/v1','transaction':tx,
            'purpose':PURPOSE,'intent_sha256':sha(intent),'owner_unit':coords['owner_unit'],'watcher_unit':coords['watcher_unit']})
    try:
        hydrate.command(command('watcher',tx),timeout=10)
        deadline=time.monotonic()+10
        while not os.path.lexists(root/'watcher-start.json'):
            need(time.monotonic()<deadline);time.sleep(.1)
        _,ready=execution.document(root/'watcher-start.json')
        validate_profile(_properties(coords['watcher_unit']),'watcher',coords['watcher_unit'],ready['owner']['pid'])
        need(native_restore.process_identity(ready['owner']['pid'])==ready['owner'])
        hydrate.command(command('owner',tx),timeout=10)
        return {'schema':'careertrellis.pre-growth-dispatched/v1','transaction':tx,'intent_sha256':sha(intent),
            'state':'dispatched','growth_authorized':False}
    except BaseException:
        if not os.path.lexists(root/'failed.json'):_once(root,'failed.json',{'schema':'careertrellis.pre-growth-failed/v1',
            'transaction':tx,'intent_sha256':sha(intent),'result':'unknown_dispatch'})
        raise PreGrowthNativeRefused('pre_growth_dispatch_requires_reconciliation') from None


def _birth_record(role,tx,state,owner,intent):
    return {'schema':'careertrellis.pre-growth-'+role+'-birth/v1','transaction':tx,'purpose':PURPOSE,
        'unit':coordinates(tx)[role+'_unit'],'owner':owner,'invocation_id':state['InvocationID'],
        'intent_sha256':sha(intent),'policy_sha256':intent['request']['retention_policy_sha256']}


def _next_verification(source):
    from . import execution,local_restore_plan
    root=Path(local_restore_plan.ROOT)/source
    names=[path.name for path in root.iterdir()]
    attempts={int(name[-2:]) for name in names if re.fullmatch(r'identity-verification-[0-9]{2}',name)}
    receipts={int(name[-7:-5]) for name in names if re.fullmatch(r'bundle-object-identity-[0-9]{2}\.json',name)}
    need(attempts==receipts and all(1<=value<=99 for value in attempts))
    for number in attempts:
        attempt=root/('identity-verification-%02d'%number)
        need(not os.path.lexists(attempt/'failed.json'))
        a,_=execution.document(root/('bundle-object-identity-%02d.json'%number));b,_=execution.document(attempt/'receipt.json')
        need(a==b)
    result=max(attempts,default=0)+1;need(result<=99);return result


def _parent_released():
    """Independent OFDs must acquire all three, then close before child launch."""
    import fcntl
    from . import preservation
    descriptors=[]
    try:
        for path in preservation.HISTORICAL_LOCKS:
            fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK);descriptors.append(fd)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);preservation.require_owned_lock(path,fd)
    finally:
        for fd in reversed(descriptors):os.close(fd)


def _quota_record(tx,holder):
    from . import guard
    result=guard.collect_provider_facts(holder.lock_fd)
    need(type(result) is guard.AuthenticatedProviderFacts and result.authenticated)
    value=result.value
    return {'schema':'careertrellis.pre-growth-actual-quota/v1','transaction':tx,'quota':value['quota'],
        'namespace_bytes':value['namespace_bytes'],'observed_at':value['observed_at'],
        'baseline_bindings':value['baseline_bindings'],'growth_authorized':False}


def owner_main(transaction):
    need(native_routes_available())
    from . import execution,guard,hydrate,identity_collect,expiry_capability
    state,owner=_current('owner',transaction);root=JOURNAL_ROOT/transaction
    _,intent=execution.document(root/'intent.json');context=baseline.native_context()
    guard.validate_peer_history(intent['web_history'],transaction,PURPOSE,context.bindings['standalone_policy_sha256'])
    _once(root,'owner-start.json',_birth_record('owner',transaction,state,owner,intent))
    _once(root,'admission.json',{'schema':'careertrellis.pre-growth-owner-admission/v1','transaction':transaction,
        'intent_sha256':sha(intent),'owner':owner,'admitted_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    try:
        parent=native_parent(transaction);holder=hydrate.Native()
        with holder.locked():
            provider=guard.NativeProvider(holder.lock_fd)
            try:source_bytes,candidate_bytes=provider.source_allocation()
            finally:provider.close()
            allocation_sha=_once(root,'allocation.json',{'schema':'careertrellis.pre-growth-actual-allocation/v1',
                'transaction':transaction,'allocation':{'source_bytes':source_bytes,'candidate_bytes':candidate_bytes,
                    'namespace_new_bytes':candidate_bytes},'requested_allocation_sha256':sha(intent['request']['allocation']),
                'baseline_bindings':context.bindings,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()})
            expiry_capability.native_check(holder.lock_fd)
            witness=expiry_capability.native_current()
            need(type(witness) is expiry_capability.FreshWitness and witness.authenticated and witness.bindings==context.bindings)
            _once(root,'expiry-check.json',{'schema':'careertrellis.pre-growth-expiry-check/v1','transaction':transaction,
                'witness_sha256':witness.digest,'observed_at':witness.observed_at,'baseline_bindings':context.bindings})
            _once(root,'existing-sources.json',{'schema':'careertrellis.pre-growth-existing-sources/v1','transaction':transaction,
                'sources':parent.existing_sources,'owner':owner,'intent_sha256':sha(intent),
                'baseline_bindings':context.bindings,'policy_sha256':context.bindings['standalone_policy_sha256']})
            _once(root,'quota-before.json',_quota_record(transaction,holder))
        children=[]
        for source in sorted(parent.existing_sources.values()):
            with holder.locked():
                verification=_next_verification(source);prefix='child-'+source+'-%02d'%verification
                _,watch=execution.document(root/'watcher-start.json')
                child={'schema':'careertrellis.pre-growth-identity-dispatch/v1','transaction':transaction,
                    'source_transaction':source,'verification':verification,'unit':identity_unit(transaction,source,verification),
                    'intent_sha256':sha(intent),'parent_owner':owner,'watcher_owner':watch['owner'],
                    'policy_sha256':context.bindings['standalone_policy_sha256']}
                _once(root,prefix+'-intent.json',child)
            _parent_released()
            hydrate.command(command('identity',transaction,source,verification),timeout=960)
            _,birth=execution.document(root/(prefix+'-start.json'))
            need(execution.group_empty(child['unit']))
            _once(root,prefix+'-launch-ack.json',{'schema':'careertrellis.pre-growth-child-exit/v1','transaction':transaction,
                'dispatch_sha256':sha(child),'birth_sha256':sha(birth),'exit_code':0,'parent_owner':owner})
            with holder.locked():
                store=catalog.NativeReadStore(holder.lock_fd);identity=catalog.read_identity(store.io,source)
                terminal={'schema':'careertrellis.pre-growth-identity-terminal/v1','transaction':transaction,
                    'source_transaction':source,'verification':verification,'dispatch_sha256':sha(child),
                    'birth_sha256':sha(birth),'identity_sha256':sha(identity),'parent_owner':owner}
                digest=_once(root,prefix+'-terminal.json',terminal)
            children.append({'source_transaction':source,'verification':verification,'terminal_sha256':digest})
        parent.require_retirement(transaction)
        result=retire.run_existing_for_parent(transaction,parent)
        retirement_sha=_once(root,'retirement-result.json',result)
        with holder.locked():quota_sha=_once(root,'quota-after.json',_quota_record(transaction,holder))
        _same_native_context(context,parent._binding['selector'])
        done={'schema':'careertrellis.pre-growth-complete/v1','transaction':transaction,'purpose':PURPOSE,
            'intent_sha256':sha(intent),'owner':owner,'children':children,'quota_after_sha256':quota_sha,
            'retirement_sha256':retirement_sha,'allocation_sha256':allocation_sha,
            'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'growth_authorized':False}
        _once(root,'complete.json',done);return done
    except BaseException:
        if not os.path.lexists(root/'failed.json'):_once(root,'failed.json',{'schema':'careertrellis.pre-growth-failed/v1',
            'transaction':transaction,'intent_sha256':sha(intent),'result':'owner_requires_reconciliation'})
        raise PreGrowthNativeRefused('pre_growth_owner_requires_reconciliation') from None


def identity_main(transaction,source,verification):
    need(native_routes_available())
    from . import execution,identity_collect
    state,owner=_current('identity',transaction,source,verification);root=JOURNAL_ROOT/transaction
    prefix='child-'+source+'-%02d'%verification
    _,intent=execution.document(root/(prefix+'-intent.json'))
    _once(root,prefix+'-start.json',{'schema':'careertrellis.pre-growth-identity-birth/v1','transaction':transaction,
        'source_transaction':source,'verification':verification,'unit':identity_unit(transaction,source,verification),
        'owner':owner,'invocation_id':state['InvocationID'],'dispatch_sha256':sha(intent)})
    child=native_identity(transaction,source,verification)
    return identity_collect.collect_existing_for_parent(source,verification,child)


def watcher_main(transaction):
    need(native_routes_available())
    from . import execution
    state,owner=_current('watcher',transaction);root=JOURNAL_ROOT/transaction
    _,intent=execution.document(root/'intent.json')
    _once(root,'watcher-start.json',_birth_record('watcher',transaction,state,owner,intent))
    deadline=time.monotonic()+WATCHER_SECONDS-30
    while time.monotonic()<deadline:
        need(not os.path.lexists(root/'failed.json'))
        if os.path.lexists(root/'owner-start.json'):
            _,birth=execution.document(root/'owner-start.json');unit=coordinates(transaction)['owner_unit']
            observed=_properties(unit)
            if observed['MainPID']=='0':
                validate_profile(observed,'owner',unit,birth['owner']['pid'],terminal=True)
                need(observed['InvocationID']==birth['invocation_id'] and execution.group_empty(unit))
                _,done=execution.document(root/'complete.json')
                receipt={'schema':'careertrellis.pre-growth-watcher-terminal/v1','transaction':transaction,
                    'intent_sha256':sha(intent),'owner':birth['owner'],'watcher_owner':owner,
                    'owner_snapshot':observed,'owner_complete_sha256':sha(done),'growth_authorized':False}
                _once(root,'watcher-terminal.json',receipt);return receipt
        time.sleep(1)
    raise PreGrowthNativeRefused('pre_growth_watcher_requires_reconciliation')


def observe(transaction):
    """Only already-running units/receipts; no locks, dispatch or provider work."""
    _verify_source();need(catalog.hex_value(transaction,32))
    from . import execution
    root=JOURNAL_ROOT/transaction;_,intent=execution.document(root/'intent.json')
    if os.path.lexists(root/'failed.json'):
        return {'transaction':transaction,'state':'unknown','growth_authorized':False}
    if os.path.lexists(root/'watcher-terminal.json'):
        if _properties(coordinates(transaction)['watcher_unit'])['MainPID']!='0':
            return {'transaction':transaction,'state':'running','growth_authorized':False}
        if not os.path.lexists(root/'terminal-proof.json'):
            history_complete(transaction,require_live=True)
            return {'transaction':transaction,'state':'verification_required','growth_authorized':False}
        history_complete(transaction)
        return {'transaction':transaction,'state':'complete','intent_sha256':sha(intent),
            'completion_sha256':sha(execution.document(root/'complete.json')[1]),'growth_authorized':False}
    return {'transaction':transaction,'state':'running','owner':_properties(coordinates(transaction)['owner_unit'])['MainPID'],
        'growth_authorized':False}
