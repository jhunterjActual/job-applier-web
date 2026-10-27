"""Fixed protected activation selector; no writer, timer, or provider mutation.

The separate prepare/commit operation authenticates replacement recovery and
review evidence before creating these documents. Pure validation of supplied
claims never authenticates them. A partial activation never falls back to the
legacy writer; only absence of both fixed files selects the legacy contract.
"""
import datetime as dt
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat

from . import baseline, bundle, entry

SELECTOR=Path('/etc/careertrellis/onedrive-retention/activation.json')
INTENT=SELECTOR.parent/'activation-intent.json'
FINALIZED=SELECTOR.parent/'activation-finalized.json'
PENDING=SELECTOR.parent/'activation-pending.json'
PENDING_SCHEMA='careertrellis.retention-activation-pending/v1'
INTENT_SCHEMA='careertrellis.onedrive-retention-activation-intent/v1'
SCHEMA='careertrellis.onedrive-retention-activation/v1'
BINDINGS=frozenset(('standalone_manifest_sha256','paired_manifest_sha256',
    'paired_policy_sha256','standalone_policy_sha256'))
REVIEWS=frozenset(('source_review','native_controls','paired_transition','rollback','schedule'))
_SEAL=object()


class ActivationRefused(ValueError):pass


def need(value):
    if not value:raise ActivationRefused('retention_activation_unavailable')


def keys(value,expected):need(type(value) is dict and set(value)==set(expected))


def sha(raw):return hashlib.sha256(raw).hexdigest()


def hex_value(value,length=64):
    need(type(value) is str and re.fullmatch('[0-9a-f]{%d}'%length,value) and value!='0'*length)


def timestamp(value):
    need(type(value) is str)
    try:result=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except Exception:raise ActivationRefused('retention_activation_unavailable') from None
    need(result.utcoffset()==dt.timedelta(0));return result


def validate(intent,selector,bindings,document,role,now):
    """Pure structural claim validation; return value is not authentication."""
    try:
        keys(bindings,BINDINGS)
        for digest in bindings.values():hex_value(digest)
        need(role in ('web','db') and type(now) is dt.datetime and now.utcoffset()==dt.timedelta(0))
        keys(intent,('schema','transaction','artifact_revision','application_release','baseline_bindings',
            'installation_receipts','replacement_bundles','reviews','prepared_at','valid_until'))
        need(intent['schema']==INTENT_SCHEMA and intent['baseline_bindings']==bindings
             and intent['artifact_revision']==document['standalone_revision']
             and intent['application_release']==document['application_release'])
        hex_value(intent['transaction'],32);hex_value(intent['artifact_revision'],40)
        hex_value(intent['application_release'],40)
        keys(intent['installation_receipts'],('web','db'))
        for digest in intent['installation_receipts'].values():hex_value(digest)
        need(intent['installation_receipts'][role]==document['acceptance_receipts']['ops'])
        keys(intent['reviews'],REVIEWS)
        for digest in intent['reviews'].values():hex_value(digest)
        replacements=intent['replacement_bundles']
        need(type(replacements) is list and 2<=len(replacements)<=32)
        prepared=timestamp(intent['prepared_at']);seen=set();native_seen=set();terminal_seen=set()
        for item in replacements:
            keys(item,('archive_sha256','native_receipt_sha256','terminal_proof_sha256','backup_label','accepted_at'))
            for name in ('archive_sha256','native_receipt_sha256','terminal_proof_sha256'):hex_value(item[name])
            need(item['archive_sha256'] not in seen);seen.add(item['archive_sha256'])
            need(item['native_receipt_sha256'] not in native_seen
                 and item['terminal_proof_sha256'] not in terminal_seen)
            native_seen.add(item['native_receipt_sha256']);terminal_seen.add(item['terminal_proof_sha256'])
            need(type(item['backup_label']) is str and re.fullmatch(
                '[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?',item['backup_label'])
                 and timestamp(item['accepted_at'])<=prepared)
        need(replacements==sorted(replacements,key=lambda item:(timestamp(item['accepted_at']),item['archive_sha256'])))
        keys(selector,('schema','transaction','activation_intent_sha256','enabled','activated_at','valid_until'))
        need(selector['schema']==SCHEMA and selector['enabled'] is True
             and selector['transaction']==intent['transaction']
             and selector['activation_intent_sha256']==sha(bundle.canonical(intent))
             and selector['valid_until']==intent['valid_until'])
        expiry=timestamp(intent['valid_until']);activated=timestamp(selector['activated_at'])
        need(prepared<=activated<=now<expiry<=timestamp(document['valid_until']))
        return {'result':'consistent_activation_claims','authenticated':False,
            'dispatch_authorized':False,'deletion_authorized':False,'replacement_count':len(replacements),
            'selector_sha256':sha(bundle.canonical(selector))}
    except Exception:raise ActivationRefused('retention_activation_unavailable') from None


class AuthenticatedSelector:
    __slots__=('_value','_digest','_role','_bindings','_seal')
    def __init__(self,value,role,bindings,seal=None):
        need(seal is _SEAL and role in ('web','db'))
        self._value,self._digest,self._role,self._bindings,self._seal=(
            bundle.canonical(value),sha(bundle.canonical(value)),role,bundle.canonical(bindings),seal)
    @property
    def authenticated(self):return self._seal is _SEAL
    @property
    def value(self):return json.loads(self._value)
    @property
    def digest(self):return self._digest
    @property
    def role(self):return self._role
    @property
    def bindings(self):return json.loads(self._bindings)


def _native_role():
    need(os.name=='posix' and os.geteuid()==os.getegid()==0
         and Path(__file__).absolute()==entry.ROOT/'activation.py')
    role={'careertrellis-staging-web-1':'web','job-applier-db-staging-01':'db'}.get(socket.gethostname())
    need(role in ('web','db'));entry.verify_install()
    for parent in (SELECTOR.parent,*SELECTOR.parent.parents):
        meta=parent.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid==meta.st_gid==0 and not meta.st_mode&0o022)
    return role


def require_activated(context):
    """Authenticate only fixed root-private documents and the current baseline."""
    role=_native_role()
    need(type(context) is baseline.AuthenticatedContext and context.authenticated is True and context.role==role)
    current=baseline.native_context()
    need(type(current) is baseline.AuthenticatedContext and current.authenticated is True
         and current.role==role and current.document==context.document and current.bindings==context.bindings)
    intent_raw,intent=entry._protected_document(INTENT,0o600)
    selector_raw,selector=entry._protected_document(SELECTOR,0o600)
    need(bundle.canonical(intent)==intent_raw and bundle.canonical(selector)==selector_raw)
    validate(intent,selector,current.bindings,current.document,role,dt.datetime.now(dt.timezone.utc))
    if role=='web':
        pending_raw,pending=entry._protected_document(PENDING,0o600)
        need(bundle.canonical(pending)==pending_raw)
        validate_pending(pending,intent,selector,current.bindings)
    else:need(not os.path.lexists(PENDING))
    require_finalized(current,intent,selector)
    if role=='web':need(entry._protected_document(PENDING,0o600)==(pending_raw,pending))
    fresh=baseline.native_context()
    need(fresh.document==current.document and fresh.bindings==current.bindings)
    return AuthenticatedSelector(selector,role,current.bindings,_SEAL)


def validate_pending(value,intent,selector,bindings):
    """The WEB fence precedes the first remote mutation and is never removed."""
    keys(value,('schema','transaction','intent_sha256','baseline_bindings','source_manifest_sha256','started_at'))
    need(value['schema']==PENDING_SCHEMA and value['transaction']==intent['transaction']
         and value['intent_sha256']==sha(bundle.canonical(intent)) and value['baseline_bindings']==bindings
         and value['source_manifest_sha256']==bindings['standalone_manifest_sha256']
         and timestamp(intent['prepared_at'])<=timestamp(value['started_at'])<=timestamp(selector['activated_at']))


def validate_finalized(value,terminal,proofs,intent,selector,context,now):
    """Consistency of supplied terminal claims; this never authenticates inputs."""
    from . import activation_controller as controller
    keys(value,('schema','transaction','intent_sha256','selector_sha256','baseline_bindings',
        'baseline_sha256','installation_receipts','committed_receipts','controller_terminal_sha256',
        'finalized_at','valid_until'))
    keys(terminal,('schema','transaction','intent_sha256','selector_sha256','baseline_bindings',
        'baseline_sha256','installation_receipts','committed_receipts','commit_acknowledgements',
        'reconciliations','owner','source_manifest_sha256','result','unknown_outcome','completed_at',
        'prepare_deadline'))
    need(value['schema']==controller.FINALIZED and terminal['schema']==controller.TERMINAL
         and terminal['result']=='both_commits_verified' and terminal['unknown_outcome'] is False
         and terminal['source_manifest_sha256']==context.bindings['standalone_manifest_sha256'])
    for name,expected in (('transaction',intent['transaction']),('intent_sha256',sha(bundle.canonical(intent))),
            ('selector_sha256',sha(bundle.canonical(selector))),('baseline_bindings',context.bindings),
            ('installation_receipts',intent['installation_receipts'])):
        need(value[name]==terminal[name]==expected)
    for name in ('baseline_sha256','installation_receipts','committed_receipts'):
        keys(value[name],('web','db'));need(value[name]==terminal[name])
        for digest in value[name].values():hex_value(digest)
    need(value['baseline_sha256'][context.role]==sha(bundle.canonical(context.document))
         and value['installation_receipts'][context.role]==context.document['acceptance_receipts']['ops']
         and value['controller_terminal_sha256']==sha(bundle.canonical(terminal))
         and value['valid_until']==selector['valid_until']==intent['valid_until'])
    completed=timestamp(terminal['completed_at']);finalized=timestamp(value['finalized_at'])
    need(timestamp(intent['prepared_at'])<=completed<=finalized<=now
         and finalized<timestamp(terminal['prepare_deadline'])<=timestamp(intent['valid_until'])
         and now<timestamp(value['valid_until']))
    keys(proofs,('web','db'))
    for role in ('web','db'):
        row=proofs[role];keys(row,('commit','ack','reconciliation'))
        for field,mapping in (('commit','committed_receipts'),('ack','commit_acknowledgements'),
                              ('reconciliation','reconciliations')):
            need(sha(bundle.canonical(row[field]))==terminal[mapping][role])
    controller.validate_terminal(terminal,intent=intent,selector=selector,bindings=context.bindings,
        baseline_sha256=value['baseline_sha256'],installation_receipts=value['installation_receipts'],
        committed_receipts={role:proofs[role]['commit'] for role in proofs},
        commit_acknowledgements={role:proofs[role]['ack'] for role in proofs},
        reconciliations={role:proofs[role]['reconciliation'] for role in proofs},now=now)
    return {'authenticated':False,'dispatch_authorized':False,'result':'consistent_finalization_claims'}


def require_finalized(context,intent,selector):
    """Missing final commit acknowledgements fence both new and legacy writers."""
    from . import activation_controller as controller
    need({'activation_controller.py','activation_transition.py'}<=entry.FILES)
    root=controller.REVIEW_ROOT/intent['transaction']
    paths=[FINALIZED,root/'controller-terminal.json']
    for role in ('web','db'):
        paths.extend(root/(stem+role+'.json') for stem in ('committed-','commit-ack-','reconciliation-'))
    snapshot={}
    for path in paths:
        raw,value=entry._protected_document(path,0o600)
        need(bundle.canonical(value)==raw);snapshot[path]=(raw,value)
    proofs={role:{field:snapshot[root/(stem+role+'.json')][1]
        for field,stem in (('commit','committed-'),('ack','commit-ack-'),('reconciliation','reconciliation-'))}
        for role in ('web','db')}
    validate_finalized(snapshot[FINALIZED][1],snapshot[root/'controller-terminal.json'][1],
        proofs,intent,selector,context,dt.datetime.now(dt.timezone.utc))
    for path,(raw,value) in snapshot.items():
        need(entry._protected_document(path,0o600)==(raw,value))


def routing_state():
    """A WEB pending fence blocks legacy even before the remote prepare ACK."""
    try:
        role=_native_role()
        def identity(meta):
            return (meta.st_dev,meta.st_ino,meta.st_mode,meta.st_uid,meta.st_gid,
                    meta.st_nlink,meta.st_size,meta.st_mtime_ns,meta.st_ctime_ns)
        def parent():
            meta=SELECTOR.parent.lstat()
            need(stat.S_ISDIR(meta.st_mode) and meta.st_uid==meta.st_gid==0 and not meta.st_mode&0o022)
            return identity(meta)
        def existing(path):
            try:meta=path.lstat()
            except FileNotFoundError as exc:
                need(exc.errno==errno.ENOENT);return None
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid==meta.st_gid==0
                 and stat.S_IMODE(meta.st_mode)==0o600 and meta.st_nlink==1
                 and 0<meta.st_size<=entry.MAX_MANIFEST)
            return identity(meta)
        before=parent()
        first=(existing(INTENT),existing(SELECTOR),existing(FINALIZED),existing(PENDING))
        # Recheck every exact name and the protected directory before choosing
        # legacy. Appearance/disappearance or replacement during this read is
        # unresolved activation, never a legacy fallback.
        second=(existing(INTENT),existing(SELECTOR),existing(FINALIZED),existing(PENDING))
        need(parent()==before and first==second)
        if first==(None,None,None,None):return None
        need(all(value is not None for value in first[:3]))
        need((role=='web' and first[3] is not None) or (role=='db' and first[3] is None))
        return require_activated(baseline.native_context())
    except OSError:raise ActivationRefused('retention_activation_unavailable') from None


def selection_for_paired(paired_policy):
    selector=routing_state()
    if selector is None:return {'mode':'legacy-mirror'}
    need(selector.role=='web' and selector.bindings['paired_policy_sha256']==paired_policy.digest
         and baseline.native_context().document['application_release']==paired_policy.data['application_release'])
    return {'mode':'native-bundle-v2','retention_policy_sha256':selector.bindings['standalone_policy_sha256'],
            'activation_sha256':selector.digest}
