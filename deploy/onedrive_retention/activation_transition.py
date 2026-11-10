"""Two-host activation reducer and fixed-root local native participant.

The reducer produces reviewable requests, never authenticated evidence or marker
writes. The local native participant separately writes only activation markers.
Native admission is deliberately separate from caller-supplied fixtures.
No timer, provider, backup, restore, or rollback action is implemented here.
"""
import copy
import datetime as dt
import os
from pathlib import Path
import stat

from . import activation as a, activation_readiness as readiness, baseline, bundle, entry

SCHEMA='careertrellis.retention-activation-transition/v1'
REVIEW_SCHEMA='careertrellis.retention-activation-review/v1'
MAX_SECONDS=900
OPERATIONS=('retention_activation_evidence','retention_activation_prepare',
            'retention_activation_commit','retention_activation_state')
SEQUENCE=('db_prepare','web_prepare','db_commit','web_commit')
REVIEW_ROOT=Path('/etc/careertrellis/onedrive-retention/activation-reviews')
LOCAL_REVIEW='careertrellis.retention-local-activation-review/v1'
REVIEW_DOCUMENT='careertrellis.retention-activation-review-document/v1'
READINESS='careertrellis.retention-activation-readiness/v1'
_NATIVE=object()


class TransitionRefused(ValueError):pass


def need(value):
    if not value:raise TransitionRefused('activation_transition_unavailable')


def digest(value):return a.sha(bundle.canonical(value))


def review(value,context,installation_receipts,evidence,now):
    """Validate a review packet; validation does not authenticate its provenance."""
    a.keys(value,('schema','transaction','baseline_sha256','baseline_bindings',
        'installation_receipts','replacement_evidence_sha256','reviews','reviewed_at','valid_until',
        'active_valid_until'))
    need(value['schema']==REVIEW_SCHEMA and value['baseline_sha256']==digest(context.document)
         and value['baseline_bindings']==context.bindings
         and value['installation_receipts']==installation_receipts
         and value['replacement_evidence_sha256']==digest(evidence))
    a.hex_value(value['transaction'],32);a.keys(value['reviews'],a.REVIEWS)
    for item in value['reviews'].values():a.hex_value(item)
    reviewed=a.timestamp(value['reviewed_at']);expiry=a.timestamp(value['valid_until'])
    need(reviewed<=now<expiry<=reviewed+dt.timedelta(seconds=MAX_SECONDS)
         and expiry<=a.timestamp(context.document['valid_until']))
    active_expiry=a.timestamp(value['active_valid_until'])
    need(expiry<=active_expiry<=a.timestamp(context.document['valid_until'])
         and active_expiry<=readiness.coordinator.EXPIRY)
    return copy.deepcopy(value)


def proposal(context,evidence,installation_receipts,review_packet,now):
    """Pure prepare bytes. Even a valid proposal grants no write authority."""
    need(context.role in ('web','db'))
    readiness.validate_evidence(evidence,context.bindings,context.revision,
        context.document['application_release'],now)
    a.keys(installation_receipts,('web','db'))
    for value in installation_receipts.values():a.hex_value(value)
    need(installation_receipts[context.role]==context.document['acceptance_receipts']['ops'])
    approved=review(review_packet,context,installation_receipts,evidence,now)
    intent=readiness.intent_candidate(evidence,transaction=approved['transaction'],
        bindings=context.bindings,revision=context.revision,release=context.document['application_release'],
        installation_receipts=installation_receipts,reviews=approved['reviews'],now=now,
        valid_until=approved['active_valid_until'])['intent']
    return {'schema':SCHEMA,'authenticated':False,'activation_performed':False,
        'baseline_sha256':digest(context.document),'review_sha256':digest(approved),
        'evidence_sha256':digest(evidence),'prepare_deadline':approved['valid_until'],'intent':intent}


def initial(candidate):
    a.keys(candidate,('schema','authenticated','activation_performed','baseline_sha256',
        'review_sha256','evidence_sha256','prepare_deadline','intent'))
    need(candidate.get('schema')==SCHEMA
         and candidate.get('authenticated') is False and candidate.get('activation_performed') is False)
    for key in ('baseline_sha256','review_sha256','evidence_sha256'):a.hex_value(candidate[key])
    prepared=a.timestamp(candidate['intent']['prepared_at'])
    deadline=a.timestamp(candidate['prepare_deadline'])
    need(prepared<deadline<=prepared+dt.timedelta(seconds=MAX_SECONDS)
         and deadline<=a.timestamp(candidate['intent']['valid_until']))
    return {'proposal_sha256':digest(candidate),'completed':[], 'pending':None,'unknown':False}


def next_request(candidate,state,current_context,now):
    """Pure single-dispatch protocol. Unknown acknowledgement requires read-only reconciliation.

    A caller must persist the returned pending state BEFORE sending its request.
    Re-entering with a pending/unknown request never sends that mutation again.
    """
    initial(candidate)
    a.keys(state,('proposal_sha256','completed','pending','unknown'))
    need(state['proposal_sha256']==digest(candidate) and state['pending'] is None
         and state['unknown'] is False and type(state['completed']) is list)
    done=state['completed'];need(done==list(SEQUENCE[:len(done)]) and len(done)<len(SEQUENCE))
    intent=candidate['intent']
    need(now<a.timestamp(candidate['prepare_deadline']))
    a.validate(intent,{'schema':a.SCHEMA,'transaction':intent['transaction'],
        'activation_intent_sha256':digest(intent),'enabled':True,
        'activated_at':now.isoformat(),'valid_until':intent['valid_until']},
        current_context.bindings,current_context.document,current_context.role,now)
    need(candidate['baseline_sha256']==digest(current_context.document)
         and intent['baseline_bindings']==current_context.bindings
         and intent['artifact_revision']==current_context.revision
         and intent['installation_receipts'][current_context.role]==current_context.document['acceptance_receipts']['ops']
         and a.timestamp(intent['prepared_at'])<=now<a.timestamp(intent['valid_until']))
    step=SEQUENCE[len(done)];role,phase=step.split('_')
    request={'phase':phase,'role':role,'transaction':intent['transaction'],
        'proposal_sha256':digest(candidate),'intent_sha256':digest(intent),
        'prepare_deadline':candidate['prepare_deadline'],
        'baseline_bindings':copy.deepcopy(intent['baseline_bindings'])}
    pending=copy.deepcopy(state);pending['pending']=digest(request)
    return {'authenticated':False,'request':request,'persist_before_dispatch':pending}


def acknowledge(state,request,receipt):
    """Pure receipt matching; transport/root provenance must be proved by native endpoint."""
    need(state['pending']==digest(request) and state['unknown'] is False)
    a.keys(receipt,('transaction','role','phase','request_sha256','intent_sha256','result'))
    need(receipt=={'transaction':request['transaction'],'role':request['role'],
        'phase':request['phase'],'request_sha256':digest(request),
        'intent_sha256':request['intent_sha256'],'result':{'prepare':'prepared','commit':'committed'}[request['phase']]})
    result=copy.deepcopy(state);result['completed'].append(request['role']+'_'+request['phase'])
    result['pending']=None
    return result


def lost_acknowledgement(state):
    need(state['pending'] is not None)
    result=copy.deepcopy(state);result['unknown']=True
    return result


def native_context():
    """Native admission only, never authenticate a passed context or proof dictionary.

    The module is intentionally outside FILES until separately reviewed install
    and endpoint integration. Copying this file onto a host cannot enable it.
    """
    need(Path(__file__)==entry.ROOT/'activation_transition.py'
         and 'activation_transition.py' in entry.FILES)
    entry.verify_install()
    context=baseline.native_context()
    need(type(context) is baseline.AuthenticatedContext and context.authenticated
         and context.role in ('web','db'))
    return context


def _private(path):
    raw,value=entry._protected_document(path,0o600)
    need(0<len(raw)<=65536 and raw==bundle.canonical(value))
    return raw,value


def _unchanged(before,after):
    need(type(after) is baseline.AuthenticatedContext and after.authenticated
         and before.role==after.role and before.document==after.document
         and before.bindings==after.bindings)


def _birth():
    raw=Path('/proc/self/stat').read_text('ascii')
    return {'pid':os.getpid(),'start_ticks':int(raw[raw.rindex(')')+2:].split()[19]),
        'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text('ascii').strip()}


def _exclusive_marker(path,value,*,deadline=None):
    """Create once, fsync directory, then authenticate exact private readback.

    Any failure retains all bytes and is an unknown outcome. Existing markers
    are never overwritten, removed, accepted as replay, or repaired here.
    """
    if deadline is not None:need(dt.datetime.now(dt.timezone.utc)<deadline)
    raw=bundle.canonical(value);need(0<len(raw)<=65536)
    parent=path.parent
    for directory in (parent,*parent.parents):
        meta=directory.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid==meta.st_gid==0 and not meta.st_mode&0o022)
    directory=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        before=os.fstat(directory);need((before.st_dev,before.st_ino)==(parent.lstat().st_dev,parent.lstat().st_ino))
        if deadline is not None:need(dt.datetime.now(dt.timezone.utc)<deadline)
        fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            meta=os.fstat(fd)
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid==meta.st_gid==0
                 and stat.S_IMODE(meta.st_mode)==0o600 and meta.st_nlink==1)
            with os.fdopen(fd,'wb',closefd=False) as stream:
                stream.write(raw);stream.flush();os.fsync(fd)
        finally:os.close(fd)
        os.fsync(directory)
        need((before.st_dev,before.st_ino)==(parent.lstat().st_dev,parent.lstat().st_ino))
        returned,document=_private(path);need(returned==raw and document==value)
    finally:os.close(directory)
    return digest(value)


def _references(evidence):
    return sorted((row['archive_sha256'],row['native_receipt_sha256'],row['terminal_proof_sha256'],
        row['backup_label'],row['accepted_at']) for row in evidence['replacement_bundles'])


def planned_selector(authority,intent,context,prepare_deadline,now):
    """Pure validation of producer-planned bytes; grants no native authority."""
    a.keys(authority,('schema','transaction','intent_sha256','prepare_deadline','dispatcher_owner',
        'prepared','selector','selector_sha256'))
    need(authority['schema']=='careertrellis.retention-activation-commit-authority/v1'
         and authority['transaction']==intent['transaction'] and authority['intent_sha256']==digest(intent)
         and authority['prepare_deadline']==prepare_deadline
         and now<a.timestamp(prepare_deadline)
         and authority['selector_sha256']==digest(authority['selector']))
    selector=authority['selector']
    need(a.timestamp(selector['activated_at'])<a.timestamp(prepare_deadline))
    a.validate(intent,selector,context.bindings,context.document,context.role,now)
    return copy.deepcopy(selector)


def matching_peer_selector(planned,observed):
    """Pure comparison after authenticated transport; caller claims stay claims."""
    need(bundle.canonical(planned)==bundle.canonical(observed) and digest(planned)==digest(observed))


class NativeLocal:
    """Local native participant; requires an already locked finite controller.

    Public construction cannot authenticate fixtures. The factory accepts only
    a transaction name; context, evidence, reviews and locks are collected from
    fixed native paths. The protected readiness/peer journal producers and
    paired mutations are separate integration contracts, not fabricated here.
    """
    def __init__(self,transaction,context,seal):
        need(seal is _NATIVE);a.hex_value(transaction,32)
        self.transaction=transaction;self.context=context
        self.root=REVIEW_ROOT/transaction;self.bootstrap=None

    def _fences(self):
        from . import preservation
        paths=['/run/lock/careertrellis-maintenance.lock']
        if self.context.role=='db':paths=list(preservation.HISTORICAL_LOCKS)
        descriptors={}
        for path in paths:
            preservation.require_owned_lock(path)
            wanted=Path(path).lstat();matches=[]
            for name in os.listdir('/proc/self/fd'):
                try:
                    fd=int(name);meta=os.fstat(fd)
                    if (meta.st_dev,meta.st_ino)==(wanted.st_dev,wanted.st_ino):matches.append(fd)
                except OSError:continue
            need(matches);descriptors[path]=min(matches)
            preservation.require_owned_lock(path,descriptors[path])
        return descriptors

    def _finite_owner(self):
        from . import hydrate
        unit='careertrellis-onedrive-activation@'+self.transaction+'.service'
        need(Path('/proc/self/cgroup').read_text('ascii').strip()=='0::/system.slice/'+unit)
        fields=('ActiveState','SubState','Type','User','Group','MainPID','ControlPID',
            'Restart','RemainAfterExit','KillMode','SendSIGKILL','RuntimeMaxUSec')
        raw=hydrate.command(['/usr/bin/systemctl','show',unit,'--property='+','.join(fields)],output=True,timeout=10)
        state=dict(line.split('=',1) for line in raw.decode('ascii').splitlines())
        need(set(state)==set(fields) and state['ActiveState']=='active' and state['SubState']=='running'
             and state['Type']=='exec' and state['User']==state['Group']=='root'
             and state['MainPID']==str(os.getpid()) and state['ControlPID']=='0'
             and state['Restart']=='no' and state['RemainAfterExit']=='yes'
             and state['KillMode']=='control-group' and state['SendSIGKILL']=='yes'
             and state['RuntimeMaxUSec']=='30min')

    def _peer(self,local_review,phase):
        """Real Client observation only; absent controller/state RPC refuses."""
        if self.context.role=='web':
            entry._paired_namespace()
            from paired_maintenance import entry as paired_entry
            from paired_maintenance.client import Client
            paired_entry.verify_install();policy=paired_entry.load_policy()
            need(policy.digest==self.context.bindings['paired_policy_sha256'])
            need(type(self.bootstrap) is readiness.AuthenticatedBootstrapEvidence and self.bootstrap.authenticated
                 and self.bootstrap.bindings==self.context.bindings and self.bootstrap.role=='web')
            original=self.bootstrap.value
            need(original['db_install_receipt_sha256']==local_review['installation_receipts']['db']
                 and original['db_baseline_sha256']==local_review['peer_baseline_sha256'])
            # The future read-only state endpoint must independently recheck
            # current DB installation, baseline and real replacements under its
            # canonical fences. Bootstrap cannot be replayed after DB prepare.
            payload=Client(policy,self.transaction).call('retention_activation_state',{
                'retention_policy_sha256':self.context.bindings['standalone_policy_sha256'],
                'intent_sha256':local_review['intent_sha256']})
            a.keys(payload,('schema','transaction','intent_sha256','baseline_bindings','db_baseline_sha256',
                'installation_receipt_sha256','replacement_evidence','prepared','selector'))
            need(payload['schema']=='careertrellis.retention-activation-peer-state/v1'
                 and payload['transaction']==self.transaction and payload['intent_sha256']==local_review['intent_sha256']
                 and payload['installation_receipt_sha256']==local_review['installation_receipts']['db']
                 and payload['db_baseline_sha256']==local_review['peer_baseline_sha256']
                 and payload['baseline_bindings']==self.context.bindings and payload['prepared'] is True
                 and _references(payload['replacement_evidence'])==_references(original['replacement_evidence']))
            if phase=='prepare':need(payload['selector'] is None)
            else:
                # WEB is the last commit. A prepared acknowledgement alone
                # cannot prove DB commit or authorize WEB's selector write.
                _raw,intent=_private(self.root/'intent.json')
                _authority_raw,authority=_private(self.root/'commit-authority.json')
                planned=planned_selector(authority,intent,self.context,local_review['prepare_deadline'],
                    dt.datetime.now(dt.timezone.utc))
                matching_peer_selector(planned,payload['selector'])
            return payload['replacement_evidence']
        # DB has no reverse SSH credential. Its peer proof must be the exact
        # durable authenticated forced-command request journal, produced by the
        # reviewed controller under the existing DB control lock.
        _raw,origin=_private(self.root/('paired-'+phase+'-request.json'))
        a.keys(origin,('schema','request','dispatcher_owner','paired_entry_sha256'))
        need(origin['schema']=='careertrellis.retention-activation-paired-origin/v1'
             and origin['dispatcher_owner']==_birth())
        installed=entry.PAIRED_ROOT/'entry.py'
        need(origin['paired_entry_sha256']==a.sha(installed.read_bytes()))
        request=origin['request']
        a.keys(request,('schema','transaction','policy_sha256','operation','payload'))
        need(request['transaction']==self.transaction and request['policy_sha256']==self.context.bindings['paired_policy_sha256']
             and request['schema']=='careertrellis.paired-maintenance-request/v1'
             and request['operation']=='retention_activation_'+phase)
        payload=request['payload']
        fields={'baseline_bindings','installation_receipts','intent_sha256','web_baseline_sha256'}
        if phase=='commit':fields.add('commit_authority_sha256')
        a.keys(payload,fields)
        need(payload['baseline_bindings']==self.context.bindings
             and payload['installation_receipts']==local_review['installation_receipts']
             and payload['intent_sha256']==local_review['intent_sha256']
             and payload['web_baseline_sha256']==local_review['peer_baseline_sha256'])
        if phase=='commit':need(payload['commit_authority_sha256']==a.sha(_private(self.root/'commit-authority.json')[0]))
        return None

    def _review_snapshot(self,phase):
        need(phase in ('prepare','commit'))
        names=['review.json','intent.json','readiness-'+phase+'.json']+[name+'.json' for name in sorted(a.REVIEWS)]
        if self.context.role=='db':names.append('paired-'+phase+'-request.json')
        else:names.append(phase+'-web.dispatch.json')
        if phase=='commit':
            names+=['commit-authority.json','prepared-web.json','prepared-db.json',
                'prepare-request-web.json','prepare-request-db.json','readiness-prepare.json',
                'material-accepted.json' if self.context.role=='db' else 'commit-material.ack.json']
        return {name:a.sha(_private(self.root/name)[0]) for name in names}

    def _readiness_binding(self,phase):
        need(phase in ('prepare','commit'));role=self.context.role
        _raw,intent=_private(self.root/'intent.json')
        name=('paired-'+phase+'-request.json' if role=='db' else phase+'-web.dispatch.json')
        _raw,origin=_private(self.root/name);request=origin['request']
        if role=='db':
            need(origin['schema']=='careertrellis.retention-activation-paired-origin/v1'
                and origin['dispatcher_owner']==_birth() and request['transaction']==self.transaction
                and request['operation']=='retention_activation_'+phase
                and request['payload']['intent_sha256']==digest(intent)
                and request['payload']['baseline_bindings']==self.context.bindings)
        else:
            from . import catalog,native_restore
            catalog.process_identity(origin['owner'])
            need(origin['transaction']==self.transaction and origin['owner']==native_restore.process_identity(os.getpid())
                and origin['request_sha256']==digest(request) and request['transaction']==self.transaction
                and request['role']=='web' and request['phase']==phase and request['intent_sha256']==digest(intent))
        binding={'phase':phase,'origin_sha256':digest(origin),'prepared_receipt_sha256':None,'material_sha256':None}
        if phase=='commit':
            _raw,prepared=_private(self.root/('prepared-'+role+'.json'))
            _raw,prepare_request=_private(self.root/('prepare-request-'+role+'.json'))
            need(prepared=={'transaction':self.transaction,'role':role,'phase':'prepare','intent_sha256':digest(intent),
                'request_sha256':digest(prepare_request),'result':'prepared'})
            _raw,prior=_private(self.root/'readiness-prepare.json')
            need(prior['phase']=='prepare' and prior['transaction']==self.transaction and prior['role']==role
                and prior['dispatcher_owner']==_birth() and prior['intent_sha256']==digest(intent))
            self._validate_readiness(prior,'prepare',self.context,intent,a.timestamp(prior['observed_at']))
            material_name='material-accepted.json' if role=='db' else 'commit-material.ack.json'
            _raw,material=_private(self.root/material_name)
            need(material['transaction']==self.transaction and material['schema']=='careertrellis.retention-activation-material-accepted/v1')
            if role=='db':need(material['actor_owner']==_birth())
            binding.update(prepared_receipt_sha256=digest(prepared),material_sha256=digest(material))
        return binding

    def _validate_readiness(self,ready,phase,current,intent,when):
        a.keys(ready,('schema','transaction','role','baseline_sha256','intent_sha256','observed_at',
            'dispatcher_owner','authority','application_release','idle','policy_sha256','source_manifest_sha256',
            'phase','origin_sha256','prepared_receipt_sha256','material_sha256'))
        need(ready['schema']==READINESS and ready['transaction']==self.transaction and ready['role']==current.role
             and ready['baseline_sha256']==digest(current.document) and ready['intent_sha256']==digest(intent)
             and ready['dispatcher_owner']==_birth() and ready['authority']=='postgres-r2-authoritative'
             and ready['application_release']==current.document['application_release'] and ready['idle'] is True
             and ready['policy_sha256']==current.bindings['standalone_policy_sha256']
             and ready['source_manifest_sha256']==current.bindings['standalone_manifest_sha256']
             and 0<=(when-a.timestamp(ready['observed_at'])).total_seconds()<=120)
        need({key:ready[key] for key in ('phase','origin_sha256','prepared_receipt_sha256','material_sha256')}==self._readiness_binding(phase))
        if phase=='commit':
            _raw,prior=_private(self.root/'readiness-prepare.json')
            need(a.timestamp(prior['observed_at'])<=a.timestamp(ready['observed_at']))

    def _checked_history(self,descriptors):
        from . import guard,native_restore
        # The existing activation history admission stage is prepare on both
        # phases; immutable readiness separately binds the actual commit origin.
        value=guard.native_history_snapshot(self.transaction,'prepare',
            descriptors.get('/run/lock/careertrellis-pgbackrest-repository.lock'))
        guard.validate_peer_history(value,self.transaction,'prepare',self.context.bindings['standalone_policy_sha256'],
            expected_role=self.context.role,now=dt.datetime.now(dt.timezone.utc).isoformat())
        need(value['complete'] is True and value['unresolved']==[] and value['baseline_bindings']==self.context.bindings
            and value['owner']==native_restore.process_identity(os.getpid()) and value['reservation']['held'] is True)
        return value

    def _write_boundary(self,phase,intent,review_sha,deadline,snapshot,*,check_history=False):
        """Recheck the SAME proof after slow collection; bound actual O_EXCL too."""
        _unchanged(self.context,native_context());self._finite_owner();descriptors=self._fences()
        if check_history:self._checked_history(descriptors)
        need(a.sha(_private(self.root/'review.json')[0])==review_sha and self._review_snapshot(phase)==snapshot)
        raw,ready=_private(self.root/('readiness-'+phase+'.json'))
        need(a.sha(raw)==snapshot['readiness-'+phase+'.json'])
        self._validate_readiness(ready,phase,self.context,intent,dt.datetime.now(dt.timezone.utc))
        # This final deadline is checked again inside _exclusive_marker after
        # parent traversal and immediately before creation, not just on entry.
        effective=min(deadline,a.timestamp(ready['observed_at'])+dt.timedelta(seconds=120))
        need(dt.datetime.now(dt.timezone.utc)<effective)
        return effective

    def _collect(self,phase):
        from . import health,guard
        current=native_context();_unchanged(self.context,current)
        need(phase in ('prepare','commit'))
        self._finite_owner();descriptors=self._fences();now=dt.datetime.now(dt.timezone.utc)
        snapshot=self._review_snapshot(phase)
        _plan,installed=health._installation(current)
        review_raw,local=_private(self.root/'review.json')
        a.keys(local,('schema','role','transaction','baseline_sha256','baseline_bindings',
            'installation_receipts','intent_sha256','prepare_deadline','reviewed_at','documents','peer_baseline_sha256'))
        need(local['schema']==LOCAL_REVIEW and local['role']==current.role and local['transaction']==self.transaction
             and local['baseline_sha256']==digest(current.document) and local['baseline_bindings']==current.bindings
             and local['installation_receipts'][current.role]==installed)
        a.keys(local['installation_receipts'],('web','db'))
        for value in local['installation_receipts'].values():a.hex_value(value)
        a.hex_value(local['peer_baseline_sha256'])
        intent_raw,intent=_private(self.root/'intent.json')
        need(digest(intent)==local['intent_sha256'] and intent['transaction']==self.transaction
             and intent['installation_receipts']==local['installation_receipts'])
        deadline=a.timestamp(local['prepare_deadline']);reviewed=a.timestamp(local['reviewed_at'])
        need(reviewed<=now<deadline<=reviewed+dt.timedelta(seconds=MAX_SECONDS))
        a.validate(intent,{'schema':a.SCHEMA,'transaction':self.transaction,'enabled':True,
            'activation_intent_sha256':digest(intent),'activated_at':now.isoformat(),'valid_until':intent['valid_until']},
            current.bindings,current.document,current.role,now)
        a.keys(local['documents'],a.REVIEWS)
        for name in sorted(a.REVIEWS):
            raw,document=_private(self.root/(name+'.json'))
            need(a.sha(raw)==local['documents'][name]==intent['reviews'][name])
            a.keys(document,('schema','subject_sha256','roles','baseline_sha256','result',
                'reviewed_at','prepare_deadline','active_valid_until'))
            # Review documents bind a common subject without hashing themselves.
            subject={key:value for key,value in intent.items() if key!='reviews'}
            need(document['schema']==REVIEW_DOCUMENT and document['result']=='approved'
                 and document['subject_sha256']==digest(subject) and document['roles']==['db','web']
                 and type(document['baseline_sha256']) is dict
                 and set(document['baseline_sha256'])=={'web','db'}
                 and document['baseline_sha256'][current.role]==digest(current.document)
                 and document['baseline_sha256']['db' if current.role=='web' else 'web']==local['peer_baseline_sha256']
                 and document['reviewed_at']==local['reviewed_at']
                 and document['prepare_deadline']==local['prepare_deadline']
                 and document['active_valid_until']==intent['valid_until'])
        _raw,ready=_private(self.root/('readiness-'+phase+'.json'))
        self._validate_readiness(ready,phase,current,intent,now)
        peer=self._peer(local,phase)
        if current.role=='db':
            evidence=readiness.native_replacements(descriptors['/run/lock/careertrellis-pgbackrest-repository.lock'])
            expected={'replacement_bundles':intent['replacement_bundles']}
            need(_references(evidence)==_references(expected))
        else:
            readiness.validate_evidence(peer,current.bindings,current.revision,current.document['application_release'],now)
            need(_references(peer)==_references({'replacement_bundles':intent['replacement_bundles']}))
        # Independent provider-free census proves unfinished operations absent;
        # the fixed readiness packet adds authority/release/finite-owner proof.
        self._checked_history(descriptors)
        _unchanged(current,native_context());self._finite_owner();self._fences()
        need(self._review_snapshot(phase)==snapshot)
        return intent,local,a.sha(review_raw),deadline,snapshot

    def prepare(self):
        need(not os.path.lexists(a.FINALIZED))
        intent,local,review_sha,deadline,snapshot=self._collect('prepare')
        need(not os.path.lexists(a.INTENT) and not os.path.lexists(a.SELECTOR)
             and not os.path.lexists(a.FINALIZED))
        # Recheck the protected approval bytes and deadline immediately at the
        # irreversible boundary. No marker is created on missing evidence.
        need(a.sha(_private(self.root/'review.json')[0])==review_sha
             and self._review_snapshot('prepare')==snapshot
             and dt.datetime.now(dt.timezone.utc)<deadline)
        _unchanged(self.context,native_context());self._finite_owner();self._fences()
        need(not os.path.lexists(a.FINALIZED))
        boundary=self._write_boundary('prepare',intent,review_sha,deadline,snapshot,check_history=True)
        written=_exclusive_marker(a.INTENT,intent,deadline=boundary)
        request={'transaction':self.transaction,'role':self.context.role,'phase':'prepare',
            'intent_sha256':written,'prepare_deadline':local['prepare_deadline'],'baseline_bindings':self.context.bindings}
        receipt={'transaction':self.transaction,'role':self.context.role,'phase':'prepare',
            'intent_sha256':written,'request_sha256':digest(request),'result':'prepared'}
        boundary=self._write_boundary('prepare',intent,review_sha,deadline,snapshot)
        _exclusive_marker(self.root/('prepare-request-'+self.context.role+'.json'),request,deadline=boundary)
        boundary=self._write_boundary('prepare',intent,review_sha,deadline,snapshot)
        _exclusive_marker(self.root/('prepared-'+self.context.role+'.json'),receipt,deadline=boundary)
        return receipt

    def commit(self):
        need(not os.path.lexists(a.FINALIZED))
        intent,local,review_sha,deadline,snapshot=self._collect('commit')
        intent_raw,actual=_private(a.INTENT);need(actual==intent and not os.path.lexists(a.SELECTOR))
        _raw,authority=_private(self.root/'commit-authority.json')
        now=dt.datetime.now(dt.timezone.utc)
        selector=planned_selector(authority,intent,self.context,local['prepare_deadline'],now)
        need(authority['dispatcher_owner']==_birth())
        a.keys(authority['prepared'],('web','db'))
        for role in ('web','db'):
            _receipt_raw,receipt=_private(self.root/('prepared-'+role+'.json'))
            need(digest(receipt)==authority['prepared'][role])
            a.keys(receipt,('transaction','role','phase','intent_sha256','request_sha256','result'))
            _request_raw,request=_private(self.root/('prepare-request-'+role+'.json'))
            need(receipt=={'transaction':self.transaction,'role':role,'phase':'prepare',
                'intent_sha256':digest(intent),'request_sha256':digest(request),'result':'prepared'})
            need(request=={'transaction':self.transaction,'role':role,'phase':'prepare',
                'intent_sha256':digest(intent),'prepare_deadline':local['prepare_deadline'],
                'baseline_bindings':self.context.bindings})
        need(dt.datetime.now(dt.timezone.utc)<deadline)
        need(a.sha(_private(self.root/'review.json')[0])==review_sha and _private(a.INTENT)[0]==intent_raw
             and self._review_snapshot('commit')==snapshot)
        _unchanged(self.context,native_context());self._finite_owner();self._fences()
        need(dt.datetime.now(dt.timezone.utc)<deadline)
        need(not os.path.lexists(a.FINALIZED))
        boundary=self._write_boundary('commit',intent,review_sha,deadline,snapshot,check_history=True)
        written=_exclusive_marker(a.SELECTOR,selector,deadline=boundary)
        receipt={'transaction':self.transaction,'role':self.context.role,'phase':'commit',
            'intent_sha256':digest(intent),'selector_sha256':written,'result':'committed',
            'committed_at':dt.datetime.now(dt.timezone.utc).isoformat()}
        boundary=self._write_boundary('commit',intent,review_sha,deadline,snapshot)
        _exclusive_marker(self.root/('committed-'+self.context.role+'.json'),receipt,deadline=boundary)
        return receipt


def native(transaction):
    """No passed context, review, proof, filesystem adapter or authentication flag."""
    context=native_context()
    participant=NativeLocal(transaction,context,_NATIVE)
    participant._finite_owner();participant._fences()
    if context.role=='web':
        participant.bootstrap=readiness.collect_bootstrap_evidence(transaction)
    return participant


def integration_requirements():
    """Exact prerequisites, not a readiness or activation assertion."""
    return {'native_dispatch_available':True,'activation_authority_established':False,
        'paired_operations':list(OPERATIONS),
        'sequence':list(SEQUENCE),'maximum_prepare_seconds':MAX_SECONDS,
        'required_before_any_marker':[
            'manifest-verified installed module and current accepted baseline on both hosts',
            'exact protected installation plan, seal, restoration and completion on both hosts',
            'protected review packet binds both installation receipts and fresh replacement evidence',
            'WEB authenticated paired evidence request; DB recollects >=2 native replacements under repository lock',
            'WEB maintenance and DB control, maintenance and repository fences with finite owner proofs',
            'exclusive durable intent creation plus fsync and byte readback; no overwrite or replay'],
        'required_commit_checks':[
            'native endpoint checks prepare_deadline immediately before each marker write; selector valid_until uses separately approved active_valid_until',
            'protected commit authority carries one canonical selector and digest for both roles; local committed_at is receipt-only',
            'each host revalidates unchanged baseline, policy, source, installation, review and expiry',
            'DB prepare acknowledgement readback before WEB intent; both intents before DB selector',
            'DB selector readback before WEB selector; fresh final selector readback on both hosts',
            'lost acknowledgement stops mutations; read-only state reconciliation never deletes intent',
            'partial, expired or mismatched markers fence both legacy and new growth'],
        'unchanged':['timers','full and differential R2 backups','R2 restore verification','original recovery holds']}
