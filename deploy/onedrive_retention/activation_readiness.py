"""Replacement evidence for an explicit activation review, never activation.

Pure candidates authenticate nothing. The fixed DB collector requires an
already-owned canonical repository lock and rechecks accepted native journals
and the current provider inventory. It writes no marker and changes no timer.
"""
import datetime as dt
import json
import os
import socket
from pathlib import Path

from . import baseline,bundle,catalog,coordinator,endpoint,guard,local_restore_plan as plans,native_restore

SCHEMA='careertrellis.onedrive-retention-replacement-evidence/v1'
BINDINGS={'standalone_manifest_sha256','paired_manifest_sha256',
          'paired_policy_sha256','standalone_policy_sha256'}
REVIEWS={'source_review','native_controls','paired_transition','rollback','schedule'}
BOOTSTRAP_SCHEMA='careertrellis.onedrive-retention-bootstrap-evidence/v1'
_BOOTSTRAP_SEAL=object()


class ReadinessRefused(ValueError):pass


def need(value):
    if not value:raise ReadinessRefused('retention_activation_evidence_unavailable')


def keys(value,expected):need(type(value) is dict and set(value)==set(expected))


def nonzero(value,length=64):
    need(catalog.hex_value(value,length) and value!='0'*length)


def replacement(value,now):
    keys(value,('archive_sha256','native_receipt_sha256','terminal_proof_sha256','backup_label','accepted_at'))
    for field in ('archive_sha256','native_receipt_sha256','terminal_proof_sha256'):nonzero(value[field])
    plans._label(value['backup_label'])
    need(catalog.timestamp(value['accepted_at'])<=now)
    return value


def validate_evidence(value,bindings,revision,release,now):
    """Validate supplied claims only; no injectable input becomes native proof."""
    keys(value,('schema','observed_at','artifact_revision','application_release',
        'baseline_bindings','catalog_sha256','replacement_bundles'))
    need(value['schema']==SCHEMA and value['baseline_bindings']==bindings
         and value['artifact_revision']==revision and value['application_release']==release)
    keys(bindings,BINDINGS)
    for digest in bindings.values():nonzero(digest)
    nonzero(revision,40);nonzero(release,40);nonzero(value['catalog_sha256'])
    need(type(now) is dt.datetime and now.utcoffset()==dt.timedelta(0)
         and 0<=(now-catalog.timestamp(value['observed_at'])).total_seconds()<=120)
    rows=value['replacement_bundles']
    need(type(rows) is list and 2<=len(rows)<=32)
    for row in rows:replacement(row,now)
    for field in ('archive_sha256','native_receipt_sha256','terminal_proof_sha256'):
        need(len({row[field] for row in rows})==len(rows))
    need(rows==sorted(rows,key=lambda row:(catalog.timestamp(row['accepted_at']),row['archive_sha256'])))
    return value


def intent_candidate(evidence,*,transaction,bindings,revision,release,installation_receipts,
                     reviews,now,valid_until):
    """Prepare reviewable bytes only. Caller claims never authorize a write."""
    validate_evidence(evidence,bindings,revision,release,now);nonzero(transaction,32)
    keys(installation_receipts,('web','db'));keys(reviews,REVIEWS)
    for digest in (*installation_receipts.values(),*reviews.values()):nonzero(digest)
    need(now<catalog.timestamp(valid_until)<=coordinator.EXPIRY)
    value={'schema':'careertrellis.onedrive-retention-activation-intent/v1',
        'transaction':transaction,'artifact_revision':revision,'application_release':release,
        'baseline_bindings':bindings,'installation_receipts':installation_receipts,
        'replacement_bundles':evidence['replacement_bundles'],'reviews':reviews,
        'prepared_at':now.isoformat(),'valid_until':valid_until}
    return {'authenticated':False,'activation_performed':False,'intent':catalog.decode(bundle.canonical(value))}


def restore_binding(request,transaction,policy_sha256,release):
    """Bind historical admission to this installed policy and app release.

    Freshness was enforced before the original dispatch. This check grants no
    new dispatch and cannot refresh or rewrite that historical witness.
    """
    keys(request,('schema','operation','retention_policy_sha256','capture_transaction','payload'))
    need(request['schema']==endpoint.SCHEMA and request['operation']=='bundle_restore'
         and request['capture_transaction']==transaction and request['retention_policy_sha256']==policy_sha256)
    payload=request['payload'];keys(payload,('stage','receipt_facts','web_preflight','web_history'))
    need(payload['stage']=='restore')
    proof=payload['web_preflight']
    coordinator.web_proof(proof,catalog.timestamp(proof['observed_at']),release)
    return proof


def recovery_point(record, now):
    completed=catalog.timestamp(native_restore.accepted_backup_completed_at(record))
    if not 0 <= (now-completed).total_seconds() <= 36*3600:
        raise ReadinessRefused('replacement_recovery_point_stale')
    return completed


def native_replacements(repository_lock_fd):
    """Read-only collector; never acquires/replaces another operation's lock."""
    context=baseline.native_context()
    need(type(context) is baseline.AuthenticatedContext and context.authenticated and context.role=='db')
    store=catalog.NativeReadStore(repository_lock_fd)
    with store.io.locked():
        original=store.records();retired=store.retired(original)
        need(len(set(original)-retired)>=2)
    observed=guard.native_catalog_inventory(repository_lock_fd)
    need(observed.get('authenticated') is True)
    now=dt.datetime.now(dt.timezone.utc)
    # The general census permits absence for retirement reconciliation. Initial
    # activation does not: every active accepted object must still match the
    # fresh provider inventory before either replacement can be selected.
    snapshot=store.snapshot(observed['inventory'])
    need(snapshot==observed['catalog'])
    with store.io.locked():
        records=store.records();retired=store.retired(records)
        live={entry['id']:entry for entry in observed['catalog']['entries']}
        need(len(live)>=2 and not set(live)&retired)
        selected=sorted(live,key=lambda key:(catalog.timestamp(records[key]['entry']['accepted_at']),key))[-2:]
        rows=[];transactions=set();generations=set()
        for identifier in selected:
            record=records[identifier];entry=record['entry'];evidence=record['evidence']
            original=dict(live[identifier]);original['current']=False
            need(original==entry)
            native=evidence['native'];tx=native['transaction'];attempt=native['attempt']
            nonzero(tx,32);need(tx not in transactions);transactions.add(tx)
            request=catalog.decode(native_restore.protected(
                coordinator.STATE/'db'/tx/'restore'/'intent.json',maximum=131072))
            proof=restore_binding(request,tx,context.bindings['standalone_policy_sha256'],
                                  context.document['application_release'])
            nonzero(native['generation_sha256']);need(native['generation_sha256'] not in generations)
            generations.add(native['generation_sha256'])
            root=catalog.RESTORE_ROOT/(tx+('' if attempt==1 else '-%02d'%attempt))
            native_raw=store.io.read(root/'complete.json');terminal_raw=store.io.read(root/'unit-terminal-proof.json')
            ownership=catalog.decode(store.io.read(root/'ownership.json'))
            need(catalog.decode(native_raw)==native
                 and catalog.sha(native_raw)==entry['native_restore_receipt_sha256']
                 and catalog.sha(terminal_raw)==evidence['terminal_proof_sha256']
                 and catalog.sha(bundle.canonical(proof))==ownership['web_preflight_sha256'])
            # Freshness was a condition of immutable admission. Revalidate at
            # that recorded time, so the first restore need not be repeated
            # merely because a later independent bundle took hours to copy.
            accepted=catalog.timestamp(entry['accepted_at']);need(accepted<=now)
            catalog.verify_journals(store.io,root,ownership,native,native_raw,terminal_raw,accepted)
            recovery_point(record,now)
            rows.append({'archive_sha256':identifier,'native_receipt_sha256':catalog.sha(native_raw),
                'terminal_proof_sha256':catalog.sha(terminal_raw),'backup_label':native['backup_label'],
                'accepted_at':entry['accepted_at']})
        store.io.require_repository_lock()
    need(baseline.native_context().bindings==context.bindings)
    value={'schema':SCHEMA,'observed_at':now.isoformat(),'artifact_revision':context.revision,
        'application_release':context.document['application_release'],'baseline_bindings':context.bindings,
        'catalog_sha256':catalog.sha(bundle.canonical(observed['catalog'])),'replacement_bundles':rows}
    validate_evidence(value,context.bindings,context.revision,context.document['application_release'],now)
    return value


def native_current_backup(repository_lock_fd):
    """One bounded source-info read under the caller's existing repository fd.

    No lock acquisition, retry, raw info persistence or caller-selected command.
    Completion time is the selected healthy latest backup's timestamp.stop.
    """
    from . import builder, hydrate
    need(Path(__file__).absolute()==Path('/usr/local/libexec/careertrellis/onedrive_retention/activation_readiness.py')
         and os.name=='posix' and os.geteuid()==os.getegid()==0
         and socket.gethostname()=='job-applier-db-staging-01')
    context=baseline.native_context()
    need(type(context) is baseline.AuthenticatedContext and context.authenticated and context.role=='db')
    files=catalog.NativeFiles(repository_lock_fd);files.require_repository_lock()
    raw=hydrate.command(['/usr/bin/setpriv','--reuid=postgres','--regid=postgres','--init-groups',
        '--reset-env','--','/usr/bin/pgbackrest','--stanza=careertrellis','--output=json','info'],
        output=True,lock_descriptor=repository_lock_fd,timeout=120)
    info=json.loads(raw);observed=dt.datetime.now(dt.timezone.utc)
    sizes=builder.database_sizes(info,observed_at=observed.isoformat(),now=observed)
    selected=[row for row in info[0]['backup'] if row['label']==sizes['backup_label']]
    need(len(selected)==1)
    completed=dt.datetime.fromtimestamp(selected[0]['timestamp']['stop'],dt.timezone.utc)
    files.require_repository_lock()
    current=baseline.native_context()
    need(type(current) is baseline.AuthenticatedContext and current.authenticated and current.role=='db'
         and current.bindings==context.bindings and current.revision==context.revision)
    return {'schema':'careertrellis.onedrive-current-backup/v1','backup_label':sizes['backup_label'],
        'backup_completed_at':completed.isoformat(),'observed_at':observed.isoformat()}


def validate_bootstrap_evidence(value,transaction,bindings,revision,release,now):
    """Validate claims only; a supplied dictionary never authenticates a peer."""
    keys(value,('schema','transaction','retention_policy_sha256','db_install_receipt_sha256',
                'db_baseline_sha256','replacement_evidence'))
    nonzero(transaction,32)
    need(value['schema']==BOOTSTRAP_SCHEMA and value['transaction']==transaction
         and value['retention_policy_sha256']==bindings['standalone_policy_sha256'])
    nonzero(value['db_install_receipt_sha256']);nonzero(value['db_baseline_sha256'])
    validate_evidence(value['replacement_evidence'],bindings,revision,release,now)
    return value


def native_activation_state(transaction,intent_sha256):
    """Observe immutable actor evidence; never acquire locks or query a provider.

    The activation actor must publish the phase-specific packet after its own
    fenced collection. Missing, stale or partial publication remains unknown.
    """
    from . import activation as a,activation_transition as transition,entry,health
    nonzero(transaction,32);nonzero(intent_sha256)
    context=entry._native_dependencies(entry.verify_install(),'db')
    need(type(context) is baseline.AuthenticatedContext and context.authenticated and context.role=='db')
    root=transition.REVIEW_ROOT/transaction
    snapshots={}
    def read(path):
        raw,value=entry._protected_document(path,0o600)
        need(0<len(raw)<=65536 and raw==bundle.canonical(value))
        snapshots[path]=raw
        return value
    intent=read(a.INTENT);need(catalog.sha(bundle.canonical(intent))==intent_sha256)
    # Only positive ENOENT denotes an uncommitted phase. Other I/O errors refuse.
    try:a.SELECTOR.lstat()
    except FileNotFoundError as error:
        import errno
        need(error.errno==errno.ENOENT);selector=None;phase='prepare'
    else:selector=read(a.SELECTOR);phase='commit'
    now=dt.datetime.now(dt.timezone.utc)
    candidate=selector or {'schema':a.SCHEMA,'transaction':transaction,'enabled':True,
        'activation_intent_sha256':intent_sha256,'activated_at':now.isoformat(),
        'valid_until':intent['valid_until']}
    a.validate(intent,candidate,context.bindings,context.document,'db',now)
    need(intent['transaction']==transaction)
    review=read(root/'review.json');prepared=read(root/'prepared-db.json')
    need(review.get('transaction')==transaction and review.get('role')=='db'
        and review.get('baseline_sha256')==catalog.sha(bundle.canonical(context.document))
        and review.get('baseline_bindings')==context.bindings
        and review.get('intent_sha256')==intent_sha256
        and review.get('installation_receipts')==intent['installation_receipts'])
    request=read(root/'prepare-request-db.json')
    need(request=={'transaction':transaction,'role':'db','phase':'prepare','intent_sha256':intent_sha256,
        'prepare_deadline':review['prepare_deadline'],'baseline_bindings':context.bindings})
    need(prepared=={'transaction':transaction,'role':'db','phase':'prepare','intent_sha256':intent_sha256,
        'request_sha256':catalog.sha(bundle.canonical(request)),'result':'prepared'})
    packet=read(root/('peer-state-'+phase+'.json'))
    keys(packet,('schema','transaction','phase','observed_at','baseline_sha256','baseline_bindings',
        'installation_receipt_sha256','intent_sha256','prepared_receipt_sha256','review_sha256','replacement_evidence'))
    _plan,installed=health._installation(context)
    need(packet['schema']=='careertrellis.retention-activation-peer-observation/v1'
        and packet['transaction']==transaction and packet['phase']==phase
        and packet['baseline_sha256']==catalog.sha(bundle.canonical(context.document))
        and packet['baseline_bindings']==context.bindings and packet['installation_receipt_sha256']==installed
        and installed==context.document['acceptance_receipts']['ops']
        and packet['intent_sha256']==intent_sha256
        and packet['prepared_receipt_sha256']==catalog.sha(bundle.canonical(prepared))
        and packet['review_sha256']==catalog.sha(bundle.canonical(review))
        and 0<=(now-a.timestamp(packet['observed_at'])).total_seconds()<=120)
    evidence=packet['replacement_evidence']
    validate_evidence(evidence,context.bindings,context.revision,context.document['application_release'],now)
    need(evidence['replacement_bundles']==intent['replacement_bundles'])
    if selector is not None:
        committed=read(root/'committed-db.json')
        keys(committed,('transaction','role','phase','intent_sha256','selector_sha256','result','committed_at'))
        need(committed['transaction']==transaction and committed['role']=='db' and committed['phase']=='commit'
            and committed['intent_sha256']==intent_sha256 and committed['result']=='committed'
            and committed['selector_sha256']==catalog.sha(bundle.canonical(selector)))
    current=entry._native_dependencies(entry.verify_install(),'db')
    need(current.document==context.document and current.bindings==context.bindings)
    for path,raw in snapshots.items():need(entry._protected_document(path,0o600)[0]==raw)
    if selector is None:
        try:a.SELECTOR.lstat()
        except FileNotFoundError as error:need(error.errno==2)
        else:need(False)
    return {'schema':'careertrellis.retention-activation-peer-state/v1','transaction':transaction,
        'intent_sha256':intent_sha256,'baseline_bindings':context.bindings,
        'db_baseline_sha256':packet['baseline_sha256'],'installation_receipt_sha256':installed,
        'replacement_evidence':evidence,'prepared':True,'selector':selector}


class AuthenticatedBootstrapEvidence:
    """Read-only observations from the fixed authenticated client, not a grant."""
    __slots__=('_raw','_bindings','_seal')
    def __init__(self,value,bindings,seal=None):
        need(seal is _BOOTSTRAP_SEAL)
        self._raw,self._bindings,self._seal=bundle.canonical(value),bundle.canonical(bindings),seal
    @property
    def authenticated(self):return self._seal is _BOOTSTRAP_SEAL
    @property
    def value(self):return json.loads(self._raw)
    @property
    def digest(self):return catalog.sha(self._raw)
    @property
    def bindings(self):return json.loads(self._bindings)
    @property
    def role(self):return 'web'


def _bootstrap_web_context():
    from . import entry
    need(Path(__file__).absolute()==entry.ROOT/'activation_readiness.py')
    context=entry._native_dependencies(entry.verify_install(),'web')
    need(type(context) is baseline.AuthenticatedContext and context.authenticated and context.role=='web')
    return context


def _bootstrap_client(context,transaction):
    from . import entry
    from paired_maintenance import client,entry as paired_entry
    need(Path(client.__file__).absolute()==entry.PAIRED_ROOT/'client.py'
         and Path(paired_entry.__file__).absolute()==entry.PAIRED_ROOT/'entry.py')
    paired_entry.verify_install();paired=paired_entry.load_policy()
    need(paired.digest==context.bindings['paired_policy_sha256']
         and paired.data['application_release']==context.document['application_release'])
    return client.Client(paired,transaction)


def collect_bootstrap_evidence(transaction):
    """Collect first-activation evidence while both hosts remain unselected.

    No caller-provided response, path, client or context is accepted. The fixed
    DB factory owns its canonical repository FD and authenticates real accepted
    native journals. This helper writes no state and cannot activate a timer.
    """
    from . import activation
    nonzero(transaction,32);context=_bootstrap_web_context()
    need(activation.routing_state() is None)
    answer=_bootstrap_client(context,transaction).call('retention_bootstrap_evidence',{
        'retention_policy_sha256':context.bindings['standalone_policy_sha256']})
    validate_bootstrap_evidence(answer,transaction,context.bindings,context.revision,
        context.document['application_release'],dt.datetime.now(dt.timezone.utc))
    current=_bootstrap_web_context()
    need(current.document==context.document and current.bindings==context.bindings
         and activation.routing_state() is None)
    return AuthenticatedBootstrapEvidence(answer,context.bindings,_BOOTSTRAP_SEAL)
