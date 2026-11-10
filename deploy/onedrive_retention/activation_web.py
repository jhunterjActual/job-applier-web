"""Finite same-process WEB activation orchestration with native authority gates.

No reviews are issued here. Fixed protected approval inputs and actual native
participants remain mandatory. Unknown dispatches block every later launch.
"""
import datetime as dt
import os
from pathlib import Path
import re
import socket
import stat
import time

from . import activation as a,activation_actor as actor,activation_controller as controller
from . import activation_finalize as finalizer,activation_transition as t
from . import baseline,bundle,coordinator,entry,hydrate,preservation

ROOT=coordinator.STATE/'activation-web'
SOURCE=entry.ROOT/'activation_web.py'
NAMES=frozenset(('dispatch.json','owner.json','terminal.json','unknown.json','unit-closure.json'))


def native_routes_available():
    """Implementation availability only; installed authority is still required."""
    return True


def _now():return dt.datetime.now(dt.timezone.utc)
def _command(tx):return ['/usr/bin/python3','-I','-B',str(entry.ROOT/'entry.py'),'activation-web-run','--transaction',tx]
def _private(path):return t._private(path)[1]
def _source():return a.sha(hydrate.protected(SOURCE,0o644)[0])


def _context():
    t.need(native_routes_available() is True and 'activation_web.py' in entry.FILES
        and Path(__file__).absolute()==SOURCE and os.geteuid()==os.getegid()==0
        and socket.gethostname()=='careertrellis-staging-web-1')
    revision=entry.verify_install();context=baseline.native_context()
    t.need(type(context) is baseline.AuthenticatedContext and context.authenticated
        and context.role=='web' and context.revision==revision
        and controller.require_transition_capabilities()==context.bindings['standalone_policy_sha256'])
    return context


def _same(context):t._unchanged(context,_context())
def _absent(path):actor._absent(path)


def _root():
    for path in (ROOT,*ROOT.parents):
        meta=path.lstat();t.need(stat.S_ISDIR(meta.st_mode) and meta.st_uid==meta.st_gid==0 and not meta.st_mode&0o022)
    t.need(stat.S_IMODE(ROOT.lstat().st_mode)==0o700)
    names=os.listdir(ROOT);t.need(len(names)<=10000 and all(re.fullmatch('[0-9a-f]{32}',name) for name in names))
    for name in names:
        meta=(ROOT/name).lstat();t.need(stat.S_ISDIR(meta.st_mode) and meta.st_uid==meta.st_gid==0
            and stat.S_IMODE(meta.st_mode)==0o700)
    return sorted(names)


def _reviews(context,tx):
    """Authenticate existing protected review bytes; never create approval."""
    root=t.REVIEW_ROOT/tx;local=_private(root/'review.json');intent=_private(root/'intent.json');now=_now()
    a.keys(local,('schema','role','transaction','baseline_sha256','baseline_bindings','installation_receipts',
        'intent_sha256','prepare_deadline','reviewed_at','documents','peer_baseline_sha256'))
    t.need(local['schema']==t.LOCAL_REVIEW and local['role']=='web' and local['transaction']==tx
        and intent['transaction']==tx and local['intent_sha256']==t.digest(intent)
        and local['baseline_sha256']==t.digest(context.document) and local['baseline_bindings']==context.bindings
        and local['installation_receipts']==intent['installation_receipts']
        and local['installation_receipts']['web']==context.document['acceptance_receipts']['ops'])
    reviewed=a.timestamp(local['reviewed_at']);deadline=a.timestamp(local['prepare_deadline'])
    t.need(reviewed<=now<deadline<=reviewed+dt.timedelta(seconds=t.MAX_SECONDS))
    a.validate(intent,{'schema':a.SCHEMA,'transaction':tx,'enabled':True,'activation_intent_sha256':t.digest(intent),
        'activated_at':now.isoformat(),'valid_until':intent['valid_until']},context.bindings,context.document,'web',now)
    a.keys(local['documents'],a.REVIEWS);digests={'review.json':t.digest(local),'intent.json':t.digest(intent)}
    for name in sorted(a.REVIEWS):
        document=_private(root/(name+'.json'))
        a.keys(document,('schema','subject_sha256','roles','baseline_sha256','result','reviewed_at','prepare_deadline','active_valid_until'))
        t.need(document['schema']==t.REVIEW_DOCUMENT and document['result']=='approved' and document['roles']==['db','web']
            and document['subject_sha256']==t.digest({key:value for key,value in intent.items() if key!='reviews'})
            and document['baseline_sha256']=={'web':t.digest(context.document),'db':local['peer_baseline_sha256']}
            and document['reviewed_at']==local['reviewed_at'] and document['prepare_deadline']==local['prepare_deadline']
            and document['active_valid_until']==intent['valid_until']
            and t.digest(document)==local['documents'][name]==intent['reviews'][name])
        digests[name+'.json']=t.digest(document)
    return {'intent_sha256':t.digest(intent),'review_sha256':digests,'prepare_deadline':local['prepare_deadline']}


def _unselected():
    t.need(a.routing_state() is None)
    for path in (a.INTENT,a.SELECTOR,a.FINALIZED,a.PENDING):_absent(path)


def _finite(tx,expected=None):
    state=actor._unit_state(tx);owner=actor._birth(os.getpid())
    t.need(Path('/proc/self/cmdline').read_bytes().split(b'\0')[:-1]==[part.encode() for part in _command(tx)]
        and Path('/proc/self/cgroup').read_text('ascii').strip()=='0::/system.slice/'+actor._unit(tx)
        and state['ActiveState']=='active' and state['SubState']=='running'
        and state['MainPID']==str(os.getpid()) and state['ControlPID']=='0')
    if expected is not None:t.need(expected['owner']==owner and expected['invocation']==state['InvocationID'])
    return {'owner':owner,'invocation':state['InvocationID'],'unit':actor._unit(tx)}


def _held():
    maintenance=preservation.require_owned_lock('/run/lock/careertrellis-maintenance.lock')
    control=coordinator.STATE/'web'/'control.lock';raw,identity=hydrate.protected(control,0o600);t.need(raw==b'')
    pid,_line=preservation._kernel_lock_line(control);t.need(pid==os.getpid())
    found=False
    for name in os.listdir('/proc/self/fd'):
        try:meta=os.fstat(int(name))
        except OSError:continue
        if (meta.st_dev,meta.st_ino)==(identity[0],identity[1]):found=True
    t.need(found and hydrate.protected(control,0o600)==(raw,identity))
    return {'maintenance':maintenance,'control':{'device':identity[0],'inode':identity[1],'owner_pid':pid}}


def _dispatch(tx,context,review):
    return {'schema':'careertrellis.retention-activation-web-dispatch/v1','transaction':tx,
        'baseline_sha256':t.digest(context.document),'baseline_bindings':context.bindings,
        'source_sha256':_source(),'requester_owner':actor._birth(os.getpid()),'review':review,
        'unit':actor._unit(tx),'command':_command(tx),'issued_at':_now().isoformat()}


def _holder(transaction):
    policy=coordinator.Policy(coordinator.read_private(coordinator.PRIVATE_POLICY));coordinator.native_policy_binding(policy)
    # NativeWEB.control must never create a missing canonical lock here.
    t.need(hydrate.protected(coordinator.STATE/'web'/'control.lock',0o600)[0]==b'')
    holder=coordinator.NativeWEB(policy);holder.lock_transaction=transaction
    return holder


def dispatch(transaction):
    context=_context();a.hex_value(transaction,32);_unselected();review=_reviews(context,transaction)
    holder=_holder(transaction)
    with holder.locked():
        _held();_same(context);_unselected();t.need(_reviews(context,transaction)==review)
        names=_root();t.need(transaction not in names)
        t.need(all(history_complete(name) is True for name in names))
        raw=hydrate.command(['/usr/bin/systemctl','show',actor._unit(transaction),
            '--property=LoadState,ActiveState,MainPID,ControlPID'],output=True,timeout=10)
        t.need(dict(line.split('=',1) for line in raw.decode('ascii').splitlines())
            =={'LoadState':'not-found','ActiveState':'inactive','MainPID':'0','ControlPID':'0'})
        _same(context);t.need(_reviews(context,transaction)==review);_unselected()
        with bundle._directory(ROOT) as fd:
            os.mkdir(transaction,0o700,dir_fd=fd);os.fsync(fd)
        value=_dispatch(transaction,context,review)
        t._exclusive_marker(ROOT/transaction/'dispatch.json',value,deadline=a.timestamp(review['prepare_deadline']))
    # Release both canonical fences before the child acquires them. The durable
    # unresolved claim now blocks every other transaction's admission census.
    try:
        hydrate.command(['/usr/bin/systemd-run','--quiet','--no-block','--unit='+actor._unit(transaction),
            *['--property='+key+'='+item for key,item in actor.PROFILE.items()],*_command(transaction)],timeout=30)
    except Exception:
        t._exclusive_marker(ROOT/transaction/'unknown.json',{'schema':'careertrellis.retention-activation-web-unknown/v1',
            'transaction':transaction,'dispatch_sha256':t.digest(value),'phase':'launch','observed_at':_now().isoformat()})
        raise t.TransitionRefused('activation_web_requires_reconciliation') from None
    return {'transaction':transaction,'state':'dispatched','dispatch_sha256':t.digest(value),'activation_established':False}


def _wait_db(ctrl,tx,context,deadline):
    root=t.REVIEW_ROOT/tx;intent=_private(root/'intent.json');local=_private(root/'review.json')
    expected=_private(root/'db-actor-state-prepare.json')
    projection={'transaction':tx,'intent':intent,'finalized':{'baseline_sha256':{'db':local['peer_baseline_sha256']}},
        'documents':{'committed-db.json':_private(root/'committed-db.json')}}
    peer=ctrl._client()
    while _now()<deadline:
        status=peer.call(finalizer.TERMINAL_STATUS,{'retention_policy_sha256':context.bindings['standalone_policy_sha256'],
            'intent_sha256':t.digest(intent)})
        if finalizer.validate_terminal_status(status,projection,expected,_now()):return
        _finite(tx);_held();_same(context);time.sleep(.2)
    raise t.TransitionRefused('activation_web_actor_terminal_deadline')


def run(transaction):
    context=_context();a.hex_value(transaction,32);root=ROOT/transaction;_root();_unselected()
    dispatch=_private(root/'dispatch.json');review=_reviews(context,transaction)
    t.need(dispatch['schema']=='careertrellis.retention-activation-web-dispatch/v1' and dispatch['transaction']==transaction
        and dispatch['baseline_sha256']==t.digest(context.document) and dispatch['baseline_bindings']==context.bindings
        and dispatch['source_sha256']==_source() and dispatch['review']==review
        and dispatch['unit']==actor._unit(transaction) and dispatch['command']==_command(transaction))
    t.need(set(os.listdir(root))=={'dispatch.json'})
    owner=_finite(transaction);owner.update(schema='careertrellis.retention-activation-web-owner/v1',transaction=transaction,
        dispatch_sha256=t.digest(dispatch),baseline_sha256=t.digest(context.document),source_sha256=_source())
    t._exclusive_marker(root/'owner.json',owner)
    try:
        holder=_holder(transaction)
        with holder.locked():
            _held();_finite(transaction,owner);_same(context)
            t.need(all(history_complete(name) is True for name in _root() if name!=transaction))
            ctrl=controller.native(transaction)
            ctrl.publish_pending()  # Produces its own pending readiness once.
            ctrl.prepare_once('db');ctrl.prepare_once('web')
            ctrl.produce_commit_authorities();ctrl.transfer_commit_material()
            ctrl.commit_once('db');ctrl.commit_once('web')
            ctrl.reconcile('db');ctrl.reconcile('web');ctrl.terminal_evidence()
            _wait_db(ctrl,transaction,context,a.timestamp(review['prepare_deadline']))
            result=finalizer.send(transaction)
            t.need(result.get('finalized_sha256')==t.digest(_private(a.FINALIZED)))
            _finite(transaction,owner);_held();_same(context)
            value={'schema':'careertrellis.retention-activation-web-terminal/v1','transaction':transaction,
                'owner_sha256':t.digest(owner),'dispatch_sha256':t.digest(dispatch),'result':'finalized',
                'finalized_sha256':result['finalized_sha256'],'db_ack_sha256':result['db_ack_sha256'],
                'completed_at':_now().isoformat()}
            t._exclusive_marker(root/'terminal.json',value);return value
    except Exception:
        t._exclusive_marker(root/'unknown.json',{'schema':'careertrellis.retention-activation-web-unknown/v1',
            'transaction':transaction,'dispatch_sha256':t.digest(dispatch),'phase':'controller','observed_at':_now().isoformat()})
        raise t.TransitionRefused('activation_web_requires_reconciliation') from None


def verify_terminal(transaction):
    """Explicit independent exit-proof sealing; never a status poll or cleanup."""
    context=_context();a.hex_value(transaction,32);root=ROOT/transaction
    owner=_private(root/'owner.json');terminal=_private(root/'terminal.json')
    t.need(owner['owner']['pid']!=os.getpid() and terminal['owner_sha256']==t.digest(owner)
        and terminal['dispatch_sha256']==owner['dispatch_sha256'] and terminal['result']=='finalized'
        and owner['source_sha256']==_source() and owner['baseline_sha256']==t.digest(context.document))
    actual=actor._terminal_state(owner,context,transaction)
    value={'schema':'careertrellis.retention-activation-web-unit-closure/v1','transaction':transaction,
        'owner_sha256':t.digest(owner),'terminal_sha256':t.digest(terminal),'unit':actor._unit(transaction),
        'invocation':owner['invocation'],'actual_terminal':actual,'observed_at':_now().isoformat(),
        'observer_owner':actor._birth(os.getpid())}
    _same(context);t.need(actor._terminal_state(owner,context,transaction)==actual)
    t._exclusive_marker(root/'unit-closure.json',value)
    _same(context);t.need(actor._terminal_state(owner,context,transaction)==actual)
    return value


def _saved_closure(transaction,dispatch,owner,terminal,closure):
    """Validate immutable success evidence, never infer success from a reboot."""
    a.keys(dispatch,('schema','transaction','baseline_sha256','baseline_bindings','source_sha256',
        'requester_owner','review','unit','command','issued_at'))
    a.keys(owner,('owner','invocation','unit','schema','transaction','dispatch_sha256','baseline_sha256','source_sha256'))
    a.keys(terminal,('schema','transaction','owner_sha256','dispatch_sha256','result','finalized_sha256','db_ack_sha256','completed_at'))
    a.keys(closure,('schema','transaction','owner_sha256','terminal_sha256','unit','invocation','actual_terminal','observed_at','observer_owner'))
    t.need(dispatch['schema']=='careertrellis.retention-activation-web-dispatch/v1'
        and owner['schema']=='careertrellis.retention-activation-web-owner/v1'
        and terminal['schema']=='careertrellis.retention-activation-web-terminal/v1'
        and closure['schema']=='careertrellis.retention-activation-web-unit-closure/v1'
        and all(document['transaction']==transaction for document in (dispatch,owner,terminal,closure))
        and dispatch['unit']==owner['unit']==closure['unit']==actor._unit(transaction)
        and closure['invocation']==owner['invocation'] and terminal['result']=='finalized'
        and owner['dispatch_sha256']==terminal['dispatch_sha256']==t.digest(dispatch)
        and terminal['owner_sha256']==closure['owner_sha256']==t.digest(owner)
        and closure['terminal_sha256']==t.digest(terminal))
    for value in (owner['invocation'],):a.hex_value(value,32)
    for value in (terminal['finalized_sha256'],terminal['db_ack_sha256']):a.hex_value(value)
    for value in (owner['owner'],dispatch['requester_owner'],closure['observer_owner']):
        a.keys(value,('pid','start_ticks','boot_id'))
        t.need(type(value['pid']) is int and value['pid']>1 and type(value['start_ticks']) is int and value['start_ticks']>0
            and type(value['boot_id']) is str and re.fullmatch(
                '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',value['boot_id']))
    t.need(closure['observer_owner']['pid']!=owner['owner']['pid']
        and closure['observer_owner']['boot_id']==dispatch['requester_owner']['boot_id']==owner['owner']['boot_id']
        and a.timestamp(dispatch['issued_at'])<=a.timestamp(terminal['completed_at'])
        <=a.timestamp(closure['observed_at'])<=_now())
    actual=closure['actual_terminal'];a.keys(actual,('unit_state','cgroup'));state=actual['unit_state']
    expected={'ActiveState':'active','SubState':'exited','Type':'exec','User':'root','Group':'root',
        'MainPID':'0','ControlPID':'0','InvocationID':owner['invocation'],'ExecMainPID':str(owner['owner']['pid']),
        'Restart':'no','RemainAfterExit':'yes','RuntimeMaxUSec':'30min','TimeoutStartUSec':'30s','TimeoutStopUSec':'30s',
        'KillMode':'control-group','SendSIGKILL':'yes','PrivateNetwork':'no','UMask':'0077','Result':'success',
        'ExecMainCode':'1','ExecMainStatus':'0','Slice':'system.slice','LoadState':'loaded','Job':''}
    a.keys(state,set(expected)|{'ControlGroup'})
    t.need(all(state[key]==value for key,value in expected.items())
        and state['ControlGroup'] in ('','/system.slice/'+actor._unit(transaction)))
    group=actual['cgroup'];a.keys(group,('path','present','directories','empty'))
    t.need(group['path']=='/system.slice/'+actor._unit(transaction) and group['empty'] is True
        and type(group['present']) is bool and type(group['directories']) is int
        and (group['directories']>=1 if group['present'] else group['directories']==0))


def _previous_boot_closed(transaction,owner):
    """Original birth ended; the exact unit name is absent and cgroup empty."""
    t.need(preservation._terminal_birth(owner['owner'])=='previous_boot_terminal')
    fields=('LoadState','ActiveState','SubState','MainPID','ControlPID','Job','ControlGroup','InvocationID')
    raw=hydrate.command(['/usr/bin/systemctl','show',actor._unit(transaction),'--all',
        '--property='+','.join(fields)],output=True,timeout=10)
    t.need(type(raw) is bytes and len(raw)<=16384 and raw.endswith(b'\n') and b'\r' not in raw)
    state={}
    for line in raw.decode('ascii').splitlines():
        key,separator,value=line.partition('=');t.need(separator and key not in state);state[key]=value
    t.need(state=={'LoadState':'not-found','ActiveState':'inactive','SubState':'dead','MainPID':'0',
        'ControlPID':'0','Job':'','ControlGroup':'','InvocationID':''})
    empty=actor._empty_cgroup(transaction,'')
    a.keys(empty,('path','present','directories','empty'))
    t.need(empty['path']=='/system.slice/'+actor._unit(transaction) and empty['empty'] is True
        and type(empty['present']) is bool and type(empty['directories']) is int
        and (empty['directories']>=1 if empty['present'] else empty['directories']==0))


def history_complete(transaction,current_transaction=None):
    """Read-only current-owner exception or independently closed past history."""
    try:
        context=_context();a.hex_value(transaction,32);_root();root=ROOT/transaction
        names=set(os.listdir(root));t.need(names<=NAMES and 'unknown.json' not in names)
        dispatch=_private(root/'dispatch.json');owner=_private(root/'owner.json')
        t.need(dispatch['transaction']==owner['transaction']==transaction
            and owner['dispatch_sha256']==t.digest(dispatch) and owner['source_sha256']==dispatch['source_sha256']==_source()
            and owner['baseline_sha256']==dispatch['baseline_sha256']==t.digest(context.document)
            and dispatch['baseline_bindings']==context.bindings and dispatch['command']==_command(transaction))
        if transaction==current_transaction:
            _finite(transaction,owner);_held();return True
        t.need({'terminal.json','unit-closure.json'}<=names)
        terminal=_private(root/'terminal.json');closure=_private(root/'unit-closure.json')
        _saved_closure(transaction,dispatch,owner,terminal,closure)
        t.need(terminal['result']=='finalized' and terminal['owner_sha256']==closure['owner_sha256']==t.digest(owner)
            and terminal['dispatch_sha256']==t.digest(dispatch) and closure['terminal_sha256']==t.digest(terminal)
            and closure['schema']=='careertrellis.retention-activation-web-unit-closure/v1'
            and closure['transaction']==transaction and closure['unit']==actor._unit(transaction)
            and closure['invocation']==owner['invocation'])
        boot=Path('/proc/sys/kernel/random/boot_id').read_text('ascii').strip()
        t.need(re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',boot))
        if boot==owner['owner']['boot_id']:
            t.need(closure['actual_terminal']==actor._terminal_state(owner,context,transaction))
        else:_previous_boot_closed(transaction,owner)
        _same(context)
        return True
    except Exception:return False


def status(transaction):
    """Read protected phase only; no provider, locks, writes or dispatch."""
    context=_context();a.hex_value(transaction,32);_root();root=ROOT/transaction
    names=set(os.listdir(root));t.need(names<=NAMES)
    if 'unknown.json' in names:return {'transaction':transaction,'state':'operator_required','activation_established':False}
    if 'terminal.json' in names:
        value=_private(root/'terminal.json')
        return {'transaction':transaction,'state':'verification_required','activation_established':False,
            'terminal_sha256':t.digest(value)}
    return {'transaction':transaction,'state':'running','activation_established':False}
