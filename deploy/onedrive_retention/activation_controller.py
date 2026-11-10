"""Protected activation evidence producers with installed native authority gates.

No review approval is generated here. Native methods take no supplied context,
readiness, acknowledgement or replacement proof. Pure validators remain claims.
Missing phase transport, review, owner or finalization integration is refusal.
"""
import copy
import datetime as dt
import os
from pathlib import Path
import re

from . import activation as a, activation_transition as t, activation_readiness as r
from . import baseline,bundle,entry,guard,health

REVIEW_ROOT=t.REVIEW_ROOT
ACK='careertrellis.retention-activation-commit-ack/v1'
RECONCILIATION='careertrellis.retention-activation-reconciliation/v1'
TERMINAL='careertrellis.retention-activation-controller-terminal/v1'
FINALIZED='careertrellis.retention-activation-finalized/v1'
PREPARE_ACK='careertrellis.retention-activation-prepare-ack/v1'
_SEAL=object()


def now():return dt.datetime.now(dt.timezone.utc)


def _owner():
    from .native_restore import process_identity
    return process_identity(os.getpid())


def provenance(role,owner,bindings):
    return {'kind':'paired-client' if role=='db' else 'native-local','owner':copy.deepcopy(owner),
        'source_manifest_sha256':bindings['standalone_manifest_sha256']}


def validate_commit(receipt,role,intent,selector,when):
    """Pure consistency only, never authentication of a supplied receipt."""
    a.keys(receipt,('transaction','role','phase','intent_sha256','selector_sha256','result','committed_at'))
    t.need(receipt['transaction']==intent['transaction'] and receipt['role']==role
        and receipt['phase']=='commit' and receipt['result']=='committed'
        and receipt['intent_sha256']==t.digest(intent) and receipt['selector_sha256']==t.digest(selector)
        and a.timestamp(selector['activated_at'])<=a.timestamp(receipt['committed_at'])<=when)


def prepare_request(role,intent,deadline,bindings):
    t.need(role in ('web','db'))
    return {'transaction':intent['transaction'],'role':role,'phase':'prepare','intent_sha256':t.digest(intent),
        'prepare_deadline':deadline,'baseline_bindings':copy.deepcopy(bindings)}


def validate_prepared(receipt,role,intent,deadline,bindings):
    request=prepare_request(role,intent,deadline,bindings)
    t.need(receipt=={'transaction':intent['transaction'],'role':role,'phase':'prepare',
        'intent_sha256':t.digest(intent),'request_sha256':t.digest(request),'result':'prepared'})


def validate_prepare_ack(value,role,intent,deadline,bindings,owner,when):
    a.keys(value,('schema','transaction','role','intent_sha256','request_sha256','receipt_sha256',
        'receipt','actor_owner','received_at','provenance'))
    validate_prepared(value['receipt'],role,intent,deadline,bindings)
    t.need(value['schema']==PREPARE_ACK and value['transaction']==intent['transaction'] and value['role']==role
        and value['intent_sha256']==t.digest(intent) and value['receipt_sha256']==t.digest(value['receipt'])
        and value['provenance']==provenance(role,owner,bindings)
        and a.timestamp(intent['prepared_at'])<=a.timestamp(value['received_at'])<=when<a.timestamp(deadline))
    a.hex_value(value['request_sha256']);a.keys(value['actor_owner'],('pid','start_ticks','boot_id'))
    actor=value['actor_owner']
    t.need(type(actor['pid']) is int and actor['pid']>1 and type(actor['start_ticks']) is int
        and actor['start_ticks']>0 and type(actor['boot_id']) is str and re.fullmatch(
            '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',actor['boot_id']))


def validate_actor_state(value,transaction,intent_sha,bindings,db_baseline_sha,prepare_wire_sha,source):
    """Pure observation consistency; only the fixed client authenticates it."""
    a.keys(value,('schema','transaction','intent_sha256','actor_owner','actor_invocation',
        'prepare_request_sha256','baseline_bindings','baseline_sha256','source'))
    a.keys(value['actor_owner'],('pid','start_ticks','boot_id'))
    actor=value['actor_owner']
    t.need(value['schema']=='careertrellis.retention-activation-actor-state/v1'
        and value['transaction']==transaction and value['intent_sha256']==intent_sha
        and value['baseline_bindings']==bindings and value['baseline_sha256']==db_baseline_sha
        and value['prepare_request_sha256']==prepare_wire_sha and value['source']==source
        and type(actor['pid']) is int and actor['pid']>1
        and type(actor['start_ticks']) is int and actor['start_ticks']>0
        and type(actor['boot_id']) is str and re.fullmatch(
            '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',actor['boot_id']))
    a.hex_value(value['actor_invocation'],32)


def validate_ack(value,role,intent,selector,owner,bindings,when):
    a.keys(value,('schema','transaction','role','intent_sha256','selector_sha256','request_sha256',
        'receipt_sha256','receipt','received_at','provenance'))
    validate_commit(value['receipt'],role,intent,selector,when)
    t.need(value['schema']==ACK and value['transaction']==intent['transaction'] and value['role']==role
        and value['intent_sha256']==t.digest(intent) and value['selector_sha256']==t.digest(selector)
        and value['receipt_sha256']==t.digest(value['receipt'])
        and value['provenance']==provenance(role,owner,bindings)
        and a.timestamp(value['receipt']['committed_at'])<=a.timestamp(value['received_at'])<=when)
    a.hex_value(value['request_sha256'])


def validate_reconciliation(value,role,intent,selector,receipt,owner,bindings,when):
    a.keys(value,('schema','transaction','role','intent_sha256','selector_sha256','observed_at',
        'committed_receipt_sha256','observation_sha256','provenance'))
    t.need(value['schema']==RECONCILIATION and value['transaction']==intent['transaction'] and value['role']==role
        and value['intent_sha256']==t.digest(intent) and value['selector_sha256']==t.digest(selector)
        and value['committed_receipt_sha256']==t.digest(receipt)
        and value['provenance']==provenance(role,owner,bindings)
        and a.timestamp(receipt['committed_at'])<=a.timestamp(value['observed_at'])<=when)
    a.hex_value(value['observation_sha256'])


def validate_terminal(value,*,intent,selector,bindings,baseline_sha256,installation_receipts,
        committed_receipts,commit_acknowledgements,reconciliations,now):
    """Pure terminal-chain validation; native callers must protected-read inputs.

    Each receipt/ACK/reconciliation argument is the two-role document map,
    not a caller-asserted digest map. This function authenticates no provenance.
    """
    from .catalog import process_identity
    a.keys(value,('schema','transaction','intent_sha256','selector_sha256','baseline_bindings','baseline_sha256',
        'installation_receipts','committed_receipts','commit_acknowledgements','reconciliations','owner',
        'source_manifest_sha256','result','unknown_outcome','completed_at','prepare_deadline'))
    for mapping in (baseline_sha256,installation_receipts,committed_receipts,commit_acknowledgements,reconciliations):
        a.keys(mapping,('web','db'))
    for key in ('baseline_sha256','installation_receipts','committed_receipts','commit_acknowledgements','reconciliations'):
        a.keys(value[key],('web','db'))
    for mapping in (baseline_sha256,installation_receipts):
        for digest in mapping.values():a.hex_value(digest)
    process_identity(value['owner'])
    completed=a.timestamp(value['completed_at']);deadline=a.timestamp(value['prepare_deadline'])
    t.need(value['schema']==TERMINAL and value['transaction']==intent['transaction']
        and value['intent_sha256']==t.digest(intent) and value['selector_sha256']==t.digest(selector)
        and value['baseline_bindings']==bindings and value['baseline_sha256']==baseline_sha256
        and value['installation_receipts']==installation_receipts==intent['installation_receipts']
        and value['source_manifest_sha256']==bindings['standalone_manifest_sha256']
        and value['result']=='both_commits_verified' and value['unknown_outcome'] is False
        and a.timestamp(intent['prepared_at'])<=a.timestamp(selector['activated_at'])<=completed<=now
        and completed<deadline<=a.timestamp(intent['prepared_at'])+dt.timedelta(seconds=t.MAX_SECONDS))
    for role in ('web','db'):
        receipt=committed_receipts[role];ack=commit_acknowledgements[role];state=reconciliations[role]
        validate_commit(receipt,role,intent,selector,completed)
        validate_ack(ack,role,intent,selector,value['owner'],bindings,completed)
        validate_reconciliation(state,role,intent,selector,receipt,value['owner'],bindings,completed)
        t.need(value['committed_receipts'][role]==t.digest(receipt)
            and value['commit_acknowledgements'][role]==t.digest(ack)
            and value['reconciliations'][role]==t.digest(state) and ack['receipt']==receipt
            and a.timestamp(ack['received_at'])<=a.timestamp(state['observed_at']))
    return {'authenticated':False,'terminal_consistent':True}


def _native_context():
    t.need(Path(__file__).absolute()==entry.ROOT/'activation_controller.py'
        and {'activation_controller.py','activation_transition.py'}<=entry.FILES)
    entry.verify_install();context=baseline.native_context()
    t.need(require_transition_capabilities()==context.bindings['standalone_policy_sha256'])
    t.need(type(context) is baseline.AuthenticatedContext and context.authenticated)
    # Writing selectors is unsafe until ordinary jobs require finalization.
    t.need(hasattr(a,'FINALIZED') and callable(getattr(a,'require_finalized',None)))
    return context


def require_transition_capabilities():
    """Do not publish markers for a retention lifecycle still unavailable."""
    t.need({'activation_finalize.py','pre_growth_native.py','expiry_capability.py'}<=entry.FILES)
    from . import pre_growth_native,graph_delete
    t.need(pre_growth_native.native_routes_available() is True
        and graph_delete.native_permanent_expiry_available() is True)
    policy=r.coordinator.Policy(r.coordinator.read_private(r.coordinator.PRIVATE_POLICY))
    t.need(policy.expiry_mode=='conditional_permanent')
    return policy.digest


class NativeController:
    def __init__(self,participant,seal):
        t.need(seal is _SEAL and type(participant) is t.NativeLocal)
        self.participant=participant;self.context=participant.context
        self.transaction=participant.transaction;self.root=participant.root
        self._actual_acks={};self._actual_states={};self._actual_prepares={};self._actual_material=None
        self._actual_pending=None
        self._actual_authorities=None

    def _check(self):
        current=_native_context();t._unchanged(self.context,current)
        self.participant._finite_owner();self.participant._fences()
        if getattr(self,'_actual_pending',None) is not None:
            t.need(t.digest(t._private(a.PENDING)[1])==self._actual_pending)
        return current

    def _intent(self):
        _raw,intent=t._private(self.root/'intent.json')
        _raw,local=t._private(self.root/'review.json')
        t.need(intent['transaction']==self.transaction and local['intent_sha256']==t.digest(intent)
            and local['baseline_sha256']==t.digest(self.context.document)
            and local['baseline_bindings']==self.context.bindings)
        return intent,local

    def _client(self):
        entry._paired_namespace()
        from paired_maintenance import entry as pe,client
        t.need(Path(pe.__file__).absolute()==entry.PAIRED_ROOT/'entry.py'
            and Path(client.__file__).absolute()==entry.PAIRED_ROOT/'client.py')
        pe.verify_install();policy=pe.load_policy()
        t.need(policy.digest==self.context.bindings['paired_policy_sha256'])
        return client.Client(policy,self.transaction)

    def collect_web_readiness(self,phase='prepare'):
        """Actual source/runtime/authority/health/history reads; no issued approval.

        Each phase collects its own fresh exclusive proof. The immutable prepare
        packet remains historical evidence and cannot serve a later commit.
        """
        current=self._check();t.need(current.role=='web' and phase in ('pending','prepare','commit'))
        intent,_local=self._intent();entry._paired_namespace()
        original={name:t._private(self.root/name)[0] for name in ('review.json','intent.json')}
        from paired_maintenance import entry as pe,runtime
        pe.verify_install();policy=pe.load_policy()
        t.need(policy.digest==current.bindings['paired_policy_sha256'])
        live=runtime.preflight_runtime(policy,'web');live.verify_sources();live.health()
        from . import hydrate
        pending=hydrate.protected('/etc/careertrellis/authority/pending-cutover')[0]
        t.need([line.partition('=')[2] for line in pending.decode('ascii').splitlines()
            if line.startswith('phase=')]==['activated'])
        observed=guard.native_history_snapshot(self.transaction,'prepare')
        guard.validate_peer_history(observed,self.transaction,'prepare',current.bindings['standalone_policy_sha256'])
        value={'schema':t.READINESS,'transaction':self.transaction,'role':'web',
            'baseline_sha256':t.digest(current.document),'intent_sha256':t.digest(intent),'observed_at':now().isoformat(),
            'dispatcher_owner':t._birth(),'authority':'postgres-r2-authoritative',
            'application_release':current.document['application_release'],'idle':True,
            'policy_sha256':current.bindings['standalone_policy_sha256'],
            'source_manifest_sha256':current.bindings['standalone_manifest_sha256']}
        binding=({'phase':'pending','origin_sha256':t.digest(intent),'prepared_receipt_sha256':None,'material_sha256':None}
            if phase=='pending' else self.participant._readiness_binding(phase))
        value.update(binding)
        name='readiness-'+phase+'.json'
        self._check()
        t.need(original=={name:t._private(self.root/name)[0] for name in original})
        return t._exclusive_marker(self.root/name,value)

    def publish_pending(self):
        """Fence WEB before DB-first prepare can have an uncertain outcome."""
        current=self._check();t.need(current.role=='web' and hasattr(a,'PENDING'))
        t.need(self._actual_pending is None)
        intent,local=self._intent();time=now();deadline=a.timestamp(local['prepare_deadline'])
        a.keys(local,('schema','role','transaction','baseline_sha256','baseline_bindings','installation_receipts',
            'intent_sha256','prepare_deadline','reviewed_at','documents','peer_baseline_sha256'))
        t.need(local['schema']==t.LOCAL_REVIEW and local['role']=='web' and local['transaction']==self.transaction
            and local['installation_receipts']==intent['installation_receipts']
            and a.timestamp(local['reviewed_at'])<=time<deadline
            and deadline<=a.timestamp(local['reviewed_at'])+dt.timedelta(seconds=t.MAX_SECONDS))
        _plan,installed=health._installation(current)
        t.need(local['installation_receipts']['web']==installed==current.document['acceptance_receipts']['ops'])
        boot=self.participant.bootstrap
        t.need(type(boot) is r.AuthenticatedBootstrapEvidence and boot.authenticated
            and boot.role=='web' and boot.bindings==current.bindings)
        evidence=boot.value
        r.validate_bootstrap_evidence(evidence,self.transaction,current.bindings,current.revision,
            current.document['application_release'],time)
        t.need(evidence['db_install_receipt_sha256']==local['installation_receipts']['db']
            and evidence['db_baseline_sha256']==local['peer_baseline_sha256']
            and t._references(evidence['replacement_evidence'])==t._references({'replacement_bundles':intent['replacement_bundles']}))
        a.validate(intent,{'schema':a.SCHEMA,'transaction':self.transaction,'enabled':True,
            'activation_intent_sha256':t.digest(intent),'activated_at':time.isoformat(),'valid_until':intent['valid_until']},
            current.bindings,current.document,'web',time)
        names=['review.json','intent.json']+[name+'.json' for name in sorted(a.REVIEWS)]
        snapshot={name:a.sha(t._private(self.root/name)[0]) for name in names}
        a.keys(local['documents'],a.REVIEWS)
        for name in sorted(a.REVIEWS):
            raw,document=t._private(self.root/(name+'.json'))
            t.need(a.sha(raw)==local['documents'][name]==intent['reviews'][name])
            a.keys(document,('schema','subject_sha256','roles','baseline_sha256','result',
                'reviewed_at','prepare_deadline','active_valid_until'))
            t.need(document['schema']==t.REVIEW_DOCUMENT and document['result']=='approved'
                and document['roles']==['db','web']
                and document['subject_sha256']==t.digest({key:value for key,value in intent.items() if key!='reviews'})
                and document['baseline_sha256']=={'web':t.digest(current.document),'db':local['peer_baseline_sha256']}
                and document['reviewed_at']==local['reviewed_at'] and document['prepare_deadline']==local['prepare_deadline']
                and document['active_valid_until']==intent['valid_until'])
        self.collect_web_readiness('pending')
        self._check()
        t.need(snapshot=={name:a.sha(t._private(self.root/name)[0]) for name in names})
        r.validate_bootstrap_evidence(evidence,self.transaction,current.bindings,current.revision,
            current.document['application_release'],now())
        pending={'schema':'careertrellis.retention-activation-pending/v1','transaction':self.transaction,
            'intent_sha256':t.digest(intent),'baseline_bindings':current.bindings,
            'source_manifest_sha256':current.bindings['standalone_manifest_sha256'],'started_at':now().isoformat()}
        self._actual_pending=t._exclusive_marker(a.PENDING,pending,deadline=deadline)
        return self._actual_pending

    def _require_pending(self):
        t.need(getattr(self,'_actual_pending',None) is not None and hasattr(a,'PENDING'))
        t.need(t.digest(t._private(a.PENDING)[1])==self._actual_pending)

    def _observe_actor(self,intent,local,request):
        t.need('activation_actor.py' in entry.FILES)
        answer=self._client().call('retention_activation_actor_state',{
            'retention_policy_sha256':self.context.bindings['standalone_policy_sha256'],
            'intent_sha256':t.digest(intent)})
        # Both sources belong to the manifest-verified fixed namespaces; the
        # returned SHA cannot choose another actor or endpoint implementation.
        source={'paired_entry_sha256':a.sha((entry.PAIRED_ROOT/'entry.py').read_bytes()),
            'actor_sha256':a.sha((entry.ROOT/'activation_actor.py').read_bytes())}
        validate_actor_state(answer,self.transaction,t.digest(intent),self.context.bindings,
            local['peer_baseline_sha256'],t.digest(request),source)
        return answer

    def prepare_once(self,role):
        """Prepare DB first, then WEB; every response is from actual native work.

        A failed or lost response leaves the immutable dispatch unresolved. This
        method cannot be used to redispatch or turn a status claim into an ACK.
        """
        current=self._check();t.need(current.role=='web' and role in ('db','web'))
        self._require_pending()
        intent,local=self._intent();deadline=a.timestamp(local['prepare_deadline'])
        common=prepare_request(role,intent,local['prepare_deadline'],current.bindings)
        if role=='db':
            request={'schema':'careertrellis.paired-maintenance-request/v1','transaction':self.transaction,
                'policy_sha256':current.bindings['paired_policy_sha256'],'operation':'retention_activation_prepare',
                'payload':{'baseline_bindings':current.bindings,'installation_receipts':local['installation_receipts'],
                    'intent_sha256':t.digest(intent),'web_baseline_sha256':t.digest(current.document)}}
            peer=self._client()
            from paired_maintenance.db_control import validate_request
            validate_request(request)
        else:
            _raw,prior=t._private(self.root/'prepare-ack-db.json')
            t.need(self._actual_prepares.get('db')==t.digest(prior))
            request=common;peer=None
        stem='prepare-'+role
        dispatch={'schema':'careertrellis.retention-activation-dispatch/v1','transaction':self.transaction,
            'request':request,'request_sha256':t.digest(request),'owner':_owner(),
            'source_manifest_sha256':current.bindings['standalone_manifest_sha256'],'dispatched_at':now().isoformat()}
        t._exclusive_marker(self.root/(stem+'.dispatch.json'),dispatch,deadline=deadline)
        try:
            if role=='web':self.collect_web_readiness()
            receipt=(self.participant.prepare() if role=='web' else peer.call(request['operation'],request['payload']))
            validate_prepared(receipt,role,intent,local['prepare_deadline'],current.bindings)
            if role=='db':
                actor=self._observe_actor(intent,local,request)
                t._exclusive_marker(self.root/'db-actor-state-prepare.json',actor,deadline=deadline)
                t._exclusive_marker(self.root/'prepare-request-db.json',common,deadline=deadline)
                t._exclusive_marker(self.root/'prepared-db.json',receipt,deadline=deadline)
                actor_owner=actor['actor_owner']
            else:
                t.need(t._private(self.root/'prepared-web.json')[1]==receipt)
                actor_owner=t._birth()
            ack={'schema':PREPARE_ACK,'transaction':self.transaction,'role':role,'intent_sha256':t.digest(intent),
                'request_sha256':t.digest(request),'receipt_sha256':t.digest(receipt),'receipt':receipt,
                'actor_owner':actor_owner,'received_at':now().isoformat(),
                'provenance':provenance(role,_owner(),current.bindings)}
            self._check()
            t._exclusive_marker(self.root/(stem+'.ack.json'),ack,deadline=deadline)
            t._exclusive_marker(self.root/('prepare-ack-'+role+'.json'),ack,deadline=deadline)
            self._actual_prepares[role]=t.digest(ack)
            return self._actual_prepares[role]
        except Exception:
            t._exclusive_marker(self.root/(stem+'.unknown.json'),{
                'schema':'careertrellis.retention-activation-unknown/v1','transaction':self.transaction,
                'request_sha256':t.digest(request),'owner':_owner(),'observed_at':now().isoformat()})
            raise t.TransitionRefused('activation_controller_requires_reconciliation') from None

    def produce_commit_authorities(self):
        """One canonical selector; actual DB owner comes from a fresh client call.

        Local protected outputs are not delivery to DB. The actor bridge must
        separately authenticate and consume the exact DB authority/WEB receipt.
        """
        current=self._check();t.need(current.role=='web')
        intent,local=self._intent();deadline=a.timestamp(local['prepare_deadline'])
        t.need(now()<deadline)
        prepared={};actor_owners={}
        for role in ('db','web'):
            _raw,ack=t._private(self.root/('prepare-ack-'+role+'.json'))
            t.need(self._actual_prepares.get(role)==t.digest(ack)
                and ack['provenance']==provenance(role,_owner(),current.bindings))
            validate_prepare_ack(ack,role,intent,local['prepare_deadline'],current.bindings,_owner(),now())
            _raw,receipt=t._private(self.root/('prepared-'+role+'.json'))
            validate_prepared(receipt,role,intent,local['prepare_deadline'],current.bindings)
            t.need(ack['receipt']==receipt and ack['receipt_sha256']==t.digest(receipt))
            prepared[role]=t.digest(receipt);actor_owners[role]=ack['actor_owner']
        _raw,dispatch=t._private(self.root/'prepare-db.dispatch.json')
        actor=self._observe_actor(intent,local,dispatch['request'])
        _raw,original=t._private(self.root/'db-actor-state-prepare.json')
        t.need(actor==original and actor_owners['db']==actor['actor_owner'] and actor_owners['web']==t._birth())
        activated=now()
        selector={'schema':a.SCHEMA,'transaction':self.transaction,'activation_intent_sha256':t.digest(intent),
            'enabled':True,'activated_at':activated.isoformat(),'valid_until':intent['valid_until']}
        base={'schema':'careertrellis.retention-activation-commit-authority/v1','transaction':self.transaction,
            'intent_sha256':t.digest(intent),'prepare_deadline':local['prepare_deadline'],'prepared':prepared,
            'selector':selector,'selector_sha256':t.digest(selector)}
        web=dict(base,dispatcher_owner=t._birth());db=dict(base,dispatcher_owner=actor['actor_owner'])
        t.planned_selector(web,intent,current,local['prepare_deadline'],activated)
        self._check()
        t._exclusive_marker(self.root/'db-actor-state-authority.json',actor,deadline=deadline)
        t._exclusive_marker(self.root/'commit-authority-db.json',db,deadline=deadline)
        t._exclusive_marker(self.root/'commit-authority.json',web,deadline=deadline)
        self._actual_authorities={'selector_sha256':t.digest(selector),'web_authority_sha256':t.digest(web),
            'db_authority_sha256':t.digest(db)}
        return dict(self._actual_authorities,db_delivery_performed=False)

    def _material(self,intent,local,authority):
        _raw,receipt=t._private(self.root/'prepared-web.json')
        _raw,request=t._private(self.root/'prepare-request-web.json')
        validate_prepared(receipt,'web',intent,local['prepare_deadline'],self.context.bindings)
        t.need(request==prepare_request('web',intent,local['prepare_deadline'],self.context.bindings))
        return {'transaction':self.transaction,'retention_policy_sha256':self.context.bindings['standalone_policy_sha256'],
            'intent_sha256':t.digest(intent),'documents':{'commit-authority.json':authority,
                'prepared-web.json':receipt,'prepare-request-web.json':request}}

    def transfer_commit_material(self):
        """One real authenticated delivery; copying local bytes is not delivery."""
        current=self._check();t.need(current.role=='web')
        intent,local=self._intent();deadline=a.timestamp(local['prepare_deadline'])
        _raw,web=t._private(self.root/'commit-authority.json')
        _raw,db=t._private(self.root/'commit-authority-db.json')
        t.need(self._actual_authorities is not None
            and self._actual_authorities['web_authority_sha256']==t.digest(web)
            and self._actual_authorities['db_authority_sha256']==t.digest(db))
        planned=t.planned_selector(web,intent,current,local['prepare_deadline'],now())
        t.matching_peer_selector(planned,t.planned_selector(db,intent,current,local['prepare_deadline'],now()))
        _raw,dispatch=t._private(self.root/'prepare-db.dispatch.json')
        actor=self._observe_actor(intent,local,dispatch['request'])
        t.need(actor['actor_owner']==db['dispatcher_owner'])
        value=self._material(intent,local,db);peer=self._client()
        request={'schema':'careertrellis.paired-maintenance-request/v1','transaction':self.transaction,
            'policy_sha256':current.bindings['paired_policy_sha256'],'operation':'retention_activation_material','payload':value}
        from paired_maintenance.db_control import validate_request
        validate_request(request)
        t._exclusive_marker(self.root/'commit-material.dispatch.json',{
            'schema':'careertrellis.retention-activation-dispatch/v1','transaction':self.transaction,
            'request':request,'request_sha256':t.digest(request),'owner':_owner(),
            'source_manifest_sha256':current.bindings['standalone_manifest_sha256'],'dispatched_at':now().isoformat()},deadline=deadline)
        try:
            ack=peer.call(request['operation'],value)
            a.keys(ack,('schema','transaction','material_sha256','value_sha256','actor_owner','actor_invocation'))
            t.need(ack['schema']=='careertrellis.retention-activation-material-accepted/v1'
                and ack['transaction']==self.transaction and ack['value_sha256']==t.digest(value)
                and ack['actor_owner']==actor['actor_owner'] and ack['actor_invocation']==actor['actor_invocation'])
            a.hex_value(ack['material_sha256']);self._check()
            self._actual_material=t._exclusive_marker(self.root/'commit-material.ack.json',ack,deadline=deadline)
            return self._actual_material
        except Exception:
            t._exclusive_marker(self.root/'commit-material.unknown.json',{
                'schema':'careertrellis.retention-activation-unknown/v1','transaction':self.transaction,
                'request_sha256':t.digest(request),'owner':_owner(),'observed_at':now().isoformat()})
            raise t.TransitionRefused('activation_controller_requires_reconciliation') from None

    def publish_db_peer_state(self,phase):
        """Fresh real DB proof producer, not the read-only polling endpoint."""
        current=self._check();t.need(current.role=='db' and phase in ('prepare','commit'))
        intent,local,review_sha,deadline,_snapshot=self.participant._collect(phase)
        _raw,prepared=t._private(self.root/'prepared-db.json')
        request={'transaction':self.transaction,'role':'db','phase':'prepare','intent_sha256':t.digest(intent),
            'prepare_deadline':local['prepare_deadline'],'baseline_bindings':current.bindings}
        t.need(prepared=={'transaction':self.transaction,'role':'db','phase':'prepare','intent_sha256':t.digest(intent),
            'request_sha256':t.digest(request),'result':'prepared'})
        _raw,actual=t._private(a.INTENT);t.need(actual==intent)
        if phase=='commit':
            _raw,selector=t._private(a.SELECTOR);_raw,receipt=t._private(self.root/'committed-db.json')
            _raw,authority=t._private(self.root/'commit-authority.json')
            planned=t.planned_selector(authority,intent,current,local['prepare_deadline'],now())
            t.matching_peer_selector(planned,selector);validate_commit(receipt,'db',intent,selector,now())
        else:t.need(not a.SELECTOR.exists())
        _plan,installation=health._installation(current)
        fd=self.participant._fences()['/run/lock/careertrellis-pgbackrest-repository.lock']
        evidence=r.native_replacements(fd)
        t.need(t._references(evidence)==t._references({'replacement_bundles':intent['replacement_bundles']}))
        packet={'schema':'careertrellis.retention-activation-peer-observation/v1','transaction':self.transaction,
            'phase':phase,'observed_at':now().isoformat(),'baseline_sha256':t.digest(current.document),
            'baseline_bindings':current.bindings,'installation_receipt_sha256':installation,
            'intent_sha256':t.digest(intent),'prepared_receipt_sha256':t.digest(prepared),
            'review_sha256':review_sha,'replacement_evidence':evidence}
        self._check()
        return t._exclusive_marker(self.root/('peer-state-'+phase+'.json'),packet,deadline=deadline)

    def commit_once(self,role):
        """Single-attempt commit and actual ACK producer, never a replay API.

        The paired receiver/authority producer is currently absent. Validate its
        declared request contract before creating even a dispatch journal; an
        unsupported route is not attempted and produces no success evidence.
        """
        current=self._check();t.need(current.role=='web' and role in ('db','web'))
        intent,local=self._intent();_raw,authority=t._private(self.root/'commit-authority.json')
        t.need(self._actual_authorities is not None
            and self._actual_authorities['web_authority_sha256']==t.digest(authority))
        selector=t.planned_selector(authority,intent,current,local['prepare_deadline'],now())
        if role=='web':
            _raw,ack=t._private(self.root/'commit-ack-db.json')
            t.need(self._actual_acks.get('db')==t.digest(ack))
            request={'transaction':self.transaction,'role':'web','phase':'commit',
                'intent_sha256':t.digest(intent),'selector_sha256':t.digest(selector)}
            peer=None
        else:
            _raw,db_authority=t._private(self.root/'commit-authority-db.json')
            db_selector=t.planned_selector(db_authority,intent,current,local['prepare_deadline'],now())
            t.matching_peer_selector(selector,db_selector)
            _raw,material=t._private(self.root/'commit-material.ack.json')
            t.need(self._actual_material==t.digest(material)
                and material['value_sha256']==t.digest(self._material(intent,local,db_authority)))
            request={'schema':'careertrellis.paired-maintenance-request/v1','transaction':self.transaction,
                'policy_sha256':current.bindings['paired_policy_sha256'],'operation':'retention_activation_commit',
                'payload':{'baseline_bindings':current.bindings,'installation_receipts':local['installation_receipts'],
                    'intent_sha256':t.digest(intent),'web_baseline_sha256':t.digest(current.document),
                    'commit_authority_sha256':t.digest(db_authority)}}
            peer=self._client()
            from paired_maintenance.db_control import validate_request
            validate_request(request)
        stem='commit-'+role
        dispatch={'schema':'careertrellis.retention-activation-dispatch/v1','transaction':self.transaction,
            'request':request,'request_sha256':t.digest(request),'owner':_owner(),
            'source_manifest_sha256':current.bindings['standalone_manifest_sha256'],'dispatched_at':now().isoformat()}
        # Existing dispatch is an unresolved previous attempt even if remote
        # state appears successful. Never call again after an uncertain ACK.
        t._exclusive_marker(self.root/(stem+'.dispatch.json'),dispatch,
            deadline=a.timestamp(local['prepare_deadline']))
        try:
            if role=='web':self.collect_web_readiness('commit')
            receipt=(self.participant.commit() if role=='web' else peer.call(request['operation'],request['payload']))
            validate_commit(receipt,role,intent,selector,now())
            if role=='db':t._exclusive_marker(self.root/'committed-db.json',receipt)
            else:t.need(t._private(self.root/'committed-web.json')[1]==receipt)
            value={'schema':ACK,'transaction':self.transaction,'role':role,'intent_sha256':t.digest(intent),
                'selector_sha256':t.digest(selector),'request_sha256':t.digest(request),'receipt_sha256':t.digest(receipt),
                'receipt':receipt,'received_at':now().isoformat(),'provenance':provenance(role,_owner(),current.bindings)}
            self._check()
            t._exclusive_marker(self.root/(stem+'.ack.json'),value)
            t._exclusive_marker(self.root/('commit-ack-'+role+'.json'),value)
            self._actual_acks[role]=t.digest(value)
            return self._actual_acks[role]
        except Exception:
            # Failure to write the unknown record still leaves the durable
            # dispatch without an ACK. Neither state permits another attempt.
            t._exclusive_marker(self.root/(stem+'.unknown.json'),{
                'schema':'careertrellis.retention-activation-unknown/v1','transaction':self.transaction,
                'request_sha256':t.digest(request),'owner':_owner(),'observed_at':now().isoformat()})
            raise t.TransitionRefused('activation_controller_requires_reconciliation') from None

    def reconcile(self,role):
        """One read-only reconciliation, never a retry of a mutating phase."""
        current=self._check();t.need(current.role=='web' and role in ('web','db'))
        intent,local=self._intent();_raw,authority=t._private(self.root/'commit-authority.json')
        selector=t.planned_selector(authority,intent,current,local['prepare_deadline'],now())
        _raw,receipt=t._private(self.root/('committed-'+role+'.json'))
        _raw,ack=t._private(self.root/('commit-ack-'+role+'.json'))
        validate_ack(ack,role,intent,selector,_owner(),current.bindings,now())
        t.need(ack['receipt']==receipt and self._actual_acks.get(role)==t.digest(ack))
        if role=='db':
            observed=self._client().call('retention_activation_state',{
                'retention_policy_sha256':current.bindings['standalone_policy_sha256'],'intent_sha256':t.digest(intent)})
            t.need(observed['transaction']==self.transaction and observed['intent_sha256']==t.digest(intent)
                and observed['baseline_bindings']==current.bindings
                and observed['db_baseline_sha256']==local['peer_baseline_sha256']
                and observed['installation_receipt_sha256']==local['installation_receipts']['db'])
            t.matching_peer_selector(selector,observed['selector'])
        else:
            _raw,observed=t._private(a.SELECTOR);t.matching_peer_selector(selector,observed)
            _raw,actual=t._private(a.INTENT);t.need(actual==intent)
        value={'schema':RECONCILIATION,'transaction':self.transaction,'role':role,
            'intent_sha256':t.digest(intent),'selector_sha256':t.digest(selector),'observed_at':now().isoformat(),
            'committed_receipt_sha256':t.digest(receipt),'observation_sha256':t.digest(observed),
            'provenance':provenance(role,_owner(),current.bindings)}
        self._check();written=t._exclusive_marker(self.root/('reconciliation-'+role+'.json'),value)
        self._actual_states[role]=written
        return written

    def terminal_evidence(self):
        """Publish only after both ACKs and independent state reads exist.

        This is protocol terminal evidence, not proof the service has exited.
        Publishing FINALIZED on either host remains a separate missing route.
        """
        current=self._check();t.need(current.role=='web')
        intent,local=self._intent();_raw,authority=t._private(self.root/'commit-authority.json')
        selector=t.planned_selector(authority,intent,current,local['prepare_deadline'],now())
        # Every dispatch without an ACK, and every recorded unknown outcome,
        # fences finalization. No missing ACK is inferred from remote state.
        names={path.name for path in self.root.iterdir()}
        t.need(not any(name.endswith('.unknown.json') for name in names))
        for name in names:
            if name.endswith('.dispatch.json'):
                t.need(name[:-len('.dispatch.json')]+'.ack.json' in names)
        commits={};acks={};states={};owner=_owner();completed=now()
        receipt_documents={};ack_documents={};state_documents={}
        for role in ('web','db'):
            raw,receipt=t._private(self.root/('committed-'+role+'.json'));commits[role]=a.sha(raw)
            validate_commit(receipt,role,intent,selector,completed)
            raw,ack=t._private(self.root/('commit-ack-'+role+'.json'));acks[role]=a.sha(raw)
            t.need(self._actual_acks.get(role)==acks[role])
            validate_ack(ack,role,intent,selector,owner,current.bindings,completed)
            t.need(ack['receipt']==receipt)
            raw,state=t._private(self.root/('reconciliation-'+role+'.json'));states[role]=a.sha(raw)
            t.need(self._actual_states.get(role)==states[role])
            validate_reconciliation(state,role,intent,selector,receipt,owner,current.bindings,completed)
            receipt_documents[role]=receipt;ack_documents[role]=ack;state_documents[role]=state
        value={'schema':TERMINAL,'transaction':self.transaction,'intent_sha256':t.digest(intent),
            'selector_sha256':t.digest(selector),'baseline_bindings':current.bindings,
            'baseline_sha256':{'web':t.digest(current.document),'db':local['peer_baseline_sha256']},
            'installation_receipts':local['installation_receipts'],'committed_receipts':commits,
            'commit_acknowledgements':acks,'reconciliations':states,'owner':owner,
            'source_manifest_sha256':current.bindings['standalone_manifest_sha256'],
            'result':'both_commits_verified','unknown_outcome':False,'completed_at':completed.isoformat(),
            'prepare_deadline':local['prepare_deadline']}
        validate_terminal(value,intent=intent,selector=selector,bindings=current.bindings,
            baseline_sha256=value['baseline_sha256'],installation_receipts=local['installation_receipts'],
            committed_receipts=receipt_documents,commit_acknowledgements=ack_documents,
            reconciliations=state_documents,now=completed)
        self._check()
        return t._exclusive_marker(self.root/'controller-terminal.json',value,
            deadline=a.timestamp(local['prepare_deadline']))


def native(transaction):
    context=_native_context();participant=t.native(transaction)
    t._unchanged(context,participant.context)
    return NativeController(participant,_SEAL)


def capabilities():
    return {'native_dispatch_available':True,'activation_authority_established':False,
        'implemented':['WEB read-only readiness producer',
        'durable WEB pending fence before first remote prepare dispatch',
        'DB immutable native peer-state producer','single-attempt prepare ACK producer',
        'one canonical selector authority producer bound to actual live DB actor',
        'single-attempt authenticated commit-material delivery producer',
        'single-attempt commit ACK producer with no replay',
        'authenticated read-only reconciliation',
        'protected terminal evidence after existing genuine commit ACKs',
        'paired prepare/commit/actor-state/material receiver routes',
        'protected paired-origin ingress consumer','two-host FINALIZED publication and ACK route'],
        'required_native_evidence':['installed manifest and finite activation unit',
            'protected review/standing approval inputs','current authenticated baseline',
            'two current provider-present native restore-tested replacement bundles']}
