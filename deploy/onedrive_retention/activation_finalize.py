"""One-shot two-host finalization core, outside FILES and all shared routes.

The native sender reads its own protected terminal chain and uses the fixed
authenticated client. The receiver independently proves DB actor termination
and owns all three canonical fences. No supplied dictionary authenticates facts.
Unknown acknowledgements preserve WEB pending and never permit a resend.
"""
import datetime as dt
import os
from pathlib import Path

from . import activation as a,activation_transition as t,activation_controller as c
from . import activation_actor as actor,baseline,bundle,entry,hydrate,preservation,native_restore

SCHEMA='careertrellis.retention-activation-finalization-material/v1'
ACK='careertrellis.retention-activation-finalized-ack/v1'
OPERATION='retention_activation_finalize'
TERMINAL_STATUS='retention_activation_actor_terminal_status'
NAMES=('controller-terminal.json','committed-web.json','committed-db.json',
    'commit-ack-web.json','commit-ack-db.json','reconciliation-web.json','reconciliation-db.json')
MAXIMUM=16384


def now():return dt.datetime.now(dt.timezone.utc)


def _context(role):
    t.need(Path(__file__).absolute()==entry.ROOT/'activation_finalize.py'
        and {'activation_finalize.py','activation_actor.py','activation_controller.py','activation_transition.py'}<=entry.FILES)
    entry.verify_install();current=baseline.native_context()
    t.need(c.require_transition_capabilities()==current.bindings['standalone_policy_sha256'])
    t.need(type(current) is baseline.AuthenticatedContext and current.authenticated and current.role==role)
    return current


def _proofs(documents):
    return {role:{field:documents[stem+role+'.json'] for field,stem in
        (('commit','committed-'),('ack','commit-ack-'),('reconciliation','reconciliation-'))} for role in ('web','db')}


def validate_material(value,context,when):
    """Pure complete-chain consistency, not native transport authentication."""
    a.keys(value,('schema','transaction','retention_policy_sha256','intent','selector','finalized','documents'))
    a.keys(value['documents'],NAMES);intent=value['intent'];selector=value['selector']
    t.need(value['schema']==SCHEMA and value['transaction']==intent['transaction']
        and value['retention_policy_sha256']==context.bindings['standalone_policy_sha256']
        and len(bundle.canonical(value))<=MAXIMUM)
    a.validate(intent,selector,context.bindings,context.document,context.role,when)
    a.validate_finalized(value['finalized'],value['documents']['controller-terminal.json'],
        _proofs(value['documents']),intent,selector,context,when)
    return {'authenticated':False,'material_consistent':True}


def _same(context):t._unchanged(context,_context(context.role))


def _absent(path):
    try:path.lstat()
    except FileNotFoundError as error:t.need(error.errno==2)
    else:t.need(False)


def validate_terminal_status(status,value,expected,when):
    """Claims consistency only; the fixed actual Client supplies provenance."""
    a.keys(status,('schema','transaction','intent_sha256','baseline_bindings','baseline_sha256',
        'source','actor_owner','actor_invocation','observed_at','state','terminal'))
    t.need(status['schema']=='careertrellis.retention-activation-actor-terminal-status/v1'
        and status['transaction']==value['transaction'] and status['intent_sha256']==t.digest(value['intent'])
        and status['baseline_bindings']==value['intent']['baseline_bindings']
        and status['baseline_sha256']==value['finalized']['baseline_sha256']['db']
        and status['actor_owner']==expected['actor_owner'] and status['actor_invocation']==expected['actor_invocation']
        and status['source']==expected['source'] and status['state'] in ('running','terminal')
        and 0<=(when-a.timestamp(status['observed_at'])).total_seconds()<=120)
    if status['state']=='running':t.need(status['terminal'] is None);return False
    proof=status['terminal']
    a.keys(proof,('schema','transaction','intent_sha256','baseline_bindings','baseline_sha256','source',
        'actor_owner','actor_invocation','unit','claim_sha256','commit_receipt_sha256','response_sha256',
        'terminal','observed_at','observer_owner'))
    for key in ('transaction','intent_sha256','baseline_bindings','baseline_sha256','source','actor_owner','actor_invocation'):
        t.need(proof[key]==status[key])
    t.need(proof['schema']=='careertrellis.retention-activation-actor-terminal/v1'
        and proof['unit']==actor._unit(value['transaction'])
        and proof['commit_receipt_sha256']==t.digest(value['documents']['committed-db.json'])
        and 0<=(when-a.timestamp(proof['observed_at'])).total_seconds()<=120)
    a.hex_value(proof['claim_sha256']);a.hex_value(proof['response_sha256'])
    a.keys(proof['terminal'],('unit_state','cgroup'));state=proof['terminal']['unit_state']
    t.need(state['ActiveState']=='active' and state['SubState']=='exited'
        and state['MainPID']==state['ControlPID']=='0' and state['Job']==''
        and state['InvocationID']==expected['actor_invocation']
        and state['ExecMainPID']==str(expected['actor_owner']['pid'])
        and state['Result']=='success' and state['ExecMainCode']=='1' and state['ExecMainStatus']=='0'
        and state['RemainAfterExit']=='yes')
    group=proof['terminal']['cgroup'];a.keys(group,('path','present','directories','empty'))
    t.need(group['path']=='/system.slice/'+proof['unit'] and group['empty'] is True
        and type(group['present']) is bool and type(group['directories']) is int
        and (group['directories']>=1 if group['present'] else group['directories']==0))
    return True


def receive(raw):
    """Fixed authenticated DB endpoint only; exclusive writes and no retries.

    The future paired route must invoke this directly in its root db-endpoint
    process, without an outer control/repository lock. This function acquires
    existing-only canonical locks after the original actor has terminated.
    """
    context=_context('db');observer=actor._endpoint_owner()
    t.need(type(raw) is bytes and 0<len(raw)<=MAXIMUM)
    value=entry.decode(raw,MAXIMUM);t.need(raw==bundle.canonical(value))
    validate_material(value,context,now());tx=value['transaction'];a.hex_value(tx,32)
    root=t.REVIEW_ROOT/tx
    backend=hydrate.Native()
    with backend.locked():
        for path,fd in backend.canonical_fds.items():preservation.require_owned_lock(path,fd)
        _same(context);_absent(a.FINALIZED);_absent(root/'finalize-ingress.json')
        actual_intent=t._private(a.INTENT)[1];actual_selector=t._private(a.SELECTOR)[1]
        t.need(actual_intent==value['intent'] and actual_selector==value['selector']
            and t._private(root/'committed-db.json')[1]==value['documents']['committed-db.json'])
        _raw,review=t._private(root/'review.json')
        t.need(review['peer_baseline_sha256']==value['finalized']['baseline_sha256']['web']
            and review['installation_receipts']==value['finalized']['installation_receipts'])
        terminal=actor.observe_terminal(tx,t.digest(actual_intent))
        t.need(type(terminal) is actor.AuthenticatedActorTerminal and terminal.authenticated
            and terminal.role=='db' and terminal.bindings==context.bindings
            and terminal.value['transaction']==tx and terminal.value['intent_sha256']==t.digest(actual_intent)
            and terminal.value['baseline_sha256']==t.digest(context.document)
            and terminal.value['commit_receipt_sha256']==t.digest(value['documents']['committed-db.json']))
        # No generic overwrite/equal-bytes replay. Only the DB actor's existing
        # own commit receipt is reused, after exact comparison above.
        for name in NAMES:
            if name!='committed-db.json':_absent(root/name)
        deadline=a.timestamp(value['documents']['controller-terminal.json']['prepare_deadline'])
        t.need(now()<deadline)
        ingress={'schema':'careertrellis.retention-activation-finalize-ingress/v1','transaction':tx,
            'material_sha256':a.sha(raw),'actor_terminal_sha256':terminal.digest,'observer_owner':observer,
            'baseline_sha256':t.digest(context.document),'received_at':now().isoformat()}
        t._exclusive_marker(root/'finalize-ingress.json',ingress,deadline=deadline)
        for name in NAMES:
            if name!='committed-db.json':t._exclusive_marker(root/name,value['documents'][name],deadline=deadline)
        for name in NAMES:t.need(t._private(root/name)[1]==value['documents'][name])
        repeated=actor.observe_terminal(tx,t.digest(actual_intent));t.need(repeated.digest==terminal.digest)
        validate_material(value,context,now());_same(context)
        for path,fd in backend.canonical_fds.items():preservation.require_owned_lock(path,fd)
        t.need(t._private(a.INTENT)[1]==actual_intent and t._private(a.SELECTOR)[1]==actual_selector)
        finalized_sha=t._exclusive_marker(a.FINALIZED,value['finalized'],deadline=deadline)
        # Read through the normal native activation gate before acknowledging.
        selected=a.require_activated(context);t.need(selected.digest==t.digest(actual_selector))
        ack={'schema':ACK,'transaction':tx,'intent_sha256':t.digest(actual_intent),
            'selector_sha256':t.digest(actual_selector),'finalized_sha256':finalized_sha,
            'controller_terminal_sha256':t.digest(value['documents']['controller-terminal.json']),
            'db_baseline_sha256':t.digest(context.document),'actor_terminal_sha256':terminal.digest,
            'source_manifest_sha256':context.bindings['standalone_manifest_sha256'],'accepted_at':now().isoformat()}
        t._exclusive_marker(root/'finalize-db.ack.json',ack)
        _same(context)
        return ack


def validate_ack(ack,value,when):
    """Pure response check after the actual Client call; not an ACK producer."""
    a.keys(ack,('schema','transaction','intent_sha256','selector_sha256','finalized_sha256',
        'controller_terminal_sha256','db_baseline_sha256','actor_terminal_sha256','source_manifest_sha256','accepted_at'))
    terminal=value['documents']['controller-terminal.json']
    t.need(ack['schema']==ACK and ack['transaction']==value['transaction']
        and ack['intent_sha256']==t.digest(value['intent']) and ack['selector_sha256']==t.digest(value['selector'])
        and ack['finalized_sha256']==t.digest(value['finalized'])
        and ack['controller_terminal_sha256']==t.digest(terminal)
        and ack['db_baseline_sha256']==value['finalized']['baseline_sha256']['db']
        and ack['source_manifest_sha256']==value['intent']['baseline_bindings']['standalone_manifest_sha256']
        and a.timestamp(value['finalized']['finalized_at'])<=a.timestamp(ack['accepted_at'])<=when)
    a.hex_value(ack['actor_terminal_sha256'])


def send(transaction):
    """One actual DB call; local FINALIZED follows only its genuine exact ACK."""
    context=_context('web');a.hex_value(transaction,32);root=t.REVIEW_ROOT/transaction
    owner=t.NativeLocal(transaction,context,t._NATIVE);owner._finite_owner();owner._fences()
    _absent(a.FINALIZED);_absent(root/'finalize-db.dispatch.json')
    intent=t._private(a.INTENT)[1];selector=t._private(a.SELECTOR)[1]
    a.validate_pending(t._private(a.PENDING)[1],intent,selector,context.bindings)
    documents={name:t._private(root/name)[1] for name in NAMES};terminal=documents['controller-terminal.json']
    t.need(terminal['owner']==native_restore.process_identity(os.getpid()))
    finalized={'schema':c.FINALIZED,'transaction':transaction,'intent_sha256':t.digest(intent),
        'selector_sha256':t.digest(selector),'baseline_bindings':context.bindings,
        'baseline_sha256':terminal['baseline_sha256'],'installation_receipts':terminal['installation_receipts'],
        'committed_receipts':terminal['committed_receipts'],'controller_terminal_sha256':t.digest(terminal),
        'finalized_at':now().isoformat(),'valid_until':intent['valid_until']}
    value={'schema':SCHEMA,'transaction':transaction,'retention_policy_sha256':context.bindings['standalone_policy_sha256'],
        'intent':intent,'selector':selector,'finalized':finalized,'documents':documents}
    validate_material(value,context,now());deadline=a.timestamp(terminal['prepare_deadline'])
    entry._paired_namespace()
    from paired_maintenance import entry as pe,client,db_control
    pe.verify_install();policy=pe.load_policy();t.need(policy.digest==context.bindings['paired_policy_sha256'])
    request={'schema':db_control.SCHEMA,'transaction':transaction,'policy_sha256':policy.digest,
        'operation':OPERATION,'payload':value}
    # Unsupported shared route refuses before a dispatch marker or native call.
    db_control.validate_request(request)
    # Poll only the original retained actor. A still-running owner consumes no
    # finalization attempt and writes no local or remote progress receipt.
    peer=client.Client(policy,transaction)
    observed=peer.call(TERMINAL_STATUS,{'retention_policy_sha256':context.bindings['standalone_policy_sha256'],
        'intent_sha256':t.digest(intent)})
    expected=t._private(root/'db-actor-state-prepare.json')[1]
    t.need(expected['transaction']==transaction and expected['intent_sha256']==t.digest(intent)
        and expected['baseline_bindings']==context.bindings
        and expected['baseline_sha256']==finalized['baseline_sha256']['db'])
    if not validate_terminal_status(observed,value,expected,now()):
        return {'result':'awaiting_actor_terminal','dispatch_performed':False}
    _same(context);owner._finite_owner();owner._fences()
    t._exclusive_marker(root/'finalize-db.dispatch.json',{
        'schema':'careertrellis.retention-activation-finalize-dispatch/v1','transaction':transaction,
        'request_sha256':t.digest(request),'material_sha256':t.digest(value),'owner':native_restore.process_identity(os.getpid()),
        'actor_terminal_observation_sha256':t.digest(observed),'issued_at':now().isoformat()},deadline=deadline)
    try:
        ack=peer.call(OPERATION,value)
        validate_ack(ack,value,now())
        t._exclusive_marker(root/'finalize-db.ack.json',ack)
        _same(context);owner._finite_owner();owner._fences()
        t.need(t._private(a.INTENT)[1]==intent and t._private(a.SELECTOR)[1]==selector)
        for name in NAMES:t.need(t._private(root/name)[1]==documents[name])
        validate_material(value,context,now())
        result=t._exclusive_marker(a.FINALIZED,finalized,deadline=deadline)
        a.require_activated(context);return {'finalized_sha256':result,'db_ack_sha256':t.digest(ack)}
    except Exception:
        # Even a failure to append this record leaves the existing dispatch as
        # an unresolved attempt. All prior markers and WEB pending are retained.
        t._exclusive_marker(root/'finalize-db.unknown.json',{
            'schema':'careertrellis.retention-activation-finalize-unknown/v1','transaction':transaction,
            'request_sha256':t.digest(request),'observed_at':now().isoformat()})
        raise t.TransitionRefused('activation_finalization_requires_reconciliation') from None
