"""Fixed synthetic capability renewal, not backup-expiry authorization.

Outside runtime FILES until review. Native renewal refuses until the installed
entry supplies expiry_capability_idle(fd): authenticated current DB history,
all-growth/native fences, and this exact renewal owner only. No ordinary backup
stage receipts or Graph backup grants are borrowed. Bootstrap packet remains
immutable. Its one known empty folder is reused with unique public fixtures.
"""
import configparser
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import types

from . import baseline,bundle,catalog,coordinator,hydrate,graph_delete,native_restore

ROOT=coordinator.STATE/'expiry-capability'
SOURCE=Path('/usr/local/libexec/careertrellis/onedrive_retention/expiry_capability.py')
BOOTSTRAP_TX='faf215c3a9d24a8b80d9e2a08e22c0d0'
BOOTSTRAP=Path('/var/backups/careertrellis/onedrive-policy-activation-20261026/permanent-expiry-capability')/BOOTSTRAP_TX
BOOTSTRAP_SHA='a5058380898140c40d3c6172d1bdb9c45d774e211f2888fa6d8f7b83aedbd7af'
TERMINAL_SHA='ac124117a7c3f3518b5f85808495f29d8567cfab29aa112eccd7b3b08fe27cdc'
MAX_AGE=86400
_SEAL=object()


class CapabilityRefused(ValueError):pass


def need(value):
    if not value:raise CapabilityRefused('expiry_capability_unavailable')


def digest(raw):return hashlib.sha256(raw).hexdigest()
def clock():return dt.datetime.now(dt.timezone.utc)
def document(path):
    raw=hydrate.protected(path,0o600,limit=131072)[0];value=json.loads(raw)
    need(bundle.canonical(value)==raw);return raw,value


class FreshWitness:
    __slots__=('_drive','_digest','_at','_bindings','_revision','_seal')
    def __init__(self,drive,sha,at,bindings,revision,seal=None):
        need(seal is _SEAL)
        self._drive,self._digest,self._at,self._bindings,self._revision,self._seal=drive,sha,at,dict(bindings),revision,seal
    def __repr__(self):return '<FreshWitness private provider binding>'
    @property
    def authenticated(self):return self._seal is _SEAL
    @property
    def drive_id(self):return self._drive
    @property
    def digest(self):return self._digest
    @property
    def observed_at(self):return self._at
    @property
    def bindings(self):return dict(self._bindings)
    @property
    def revision(self):return self._revision


def require_fresh(witness,drive_id,bindings,now=None):
    need(type(witness) is FreshWitness and witness.authenticated and witness.drive_id==drive_id
        and witness.bindings==bindings and catalog.hex_value(witness.digest))
    current=clock() if now is None else catalog.timestamp(now)
    need(0<=(current-catalog.timestamp(witness.observed_at)).total_seconds()<=MAX_AGE)


def _context():
    from . import entry
    need(os.name=='posix' and os.geteuid()==os.getegid()==0
        and socket.gethostname()=='job-applier-db-staging-01' and Path(__file__).absolute()==SOURCE)
    revision=entry.verify_install();context=baseline.native_context()
    need(type(context) is baseline.AuthenticatedContext and context.authenticated and context.role=='db'
        and context.revision==revision)
    return context


def _root():
    hydrate.protected(SOURCE,0o644) # Exact installed manifest checked by context.
    for path in (ROOT,*ROOT.parents):
        s=path.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0 and not s.st_mode&0o022)
    need(stat.S_IMODE(ROOT.lstat().st_mode)==0o700)
    names=os.listdir(ROOT);need(len(names)<=10001)
    need(all(name=='latest.json' or catalog.hex_value(name,32) for name in names))


def _mapping(raw):
    parser=configparser.ConfigParser(interpolation=None,strict=True);parser.read_string(raw.decode())
    need(not parser.defaults() and set(parser.sections())=={'r2','onedrive'})
    remote=dict(parser['onedrive']);need(remote.get('type')=='onedrive' and remote.get('root_folder_id','')==''
        and remote.get('region','global') in ('','global'))
    graph_delete.component(remote['drive_id']);return remote


def validate_complete(value,context,now):
    need(type(value) is dict and set(value)=={'schema','transaction','drive_id','observed_at','bindings',
        'revision','stale_status','current_status','exact_absence','children_terminal','bootstrap_source_sha256'})
    need(value['schema']=='careertrellis.expiry-capability-complete/v1' and catalog.hex_value(value['transaction'],32)
        and value['bindings']==context.bindings and value['revision']==context.revision
        and type(value['stale_status']) is int and value['stale_status']==412
        and type(value['current_status']) is int and value['current_status']==204
        and value['exact_absence'] is True and value['children_terminal'] is True
        and value['bootstrap_source_sha256']==BOOTSTRAP_SHA)
    graph_delete.component(value['drive_id'])
    need(0<=(now-catalog.timestamp(value['observed_at'])).total_seconds()<=MAX_AGE)
    return value


def _prior_history(context,require_current):
    """Old completed evidence permits renewal, never resets witness freshness."""
    _root();raw,latest=document(ROOT/'latest.json')
    need(set(latest)=={'schema','transaction','state','complete_sha256'}
        and latest['schema']=='careertrellis.expiry-capability-latest/v1'
        and latest['state']=='complete' and catalog.hex_value(latest['transaction'],32))
    complete_raw,value=document(ROOT/latest['transaction']/'complete.json')
    need(digest(complete_raw)==latest['complete_sha256'] and value['transaction']==latest['transaction'])
    validate_complete(value,context,clock() if require_current else catalog.timestamp(value['observed_at']))
    need(catalog.timestamp(value['observed_at'])<=clock())
    config=hydrate.protected(Path('/etc/careertrellis/rclone.conf'),0o600)[0]
    need(_mapping(config)['drive_id']==value['drive_id'])
    # Every retained attempt must be complete. Orphan/failed/checking evidence is
    # never forgiven by a newer pointer or a free canonical lock.
    for name in os.listdir(ROOT):
        if name=='latest.json':continue
        _,row=document(ROOT/name/'complete.json');need(row['transaction']==name)
        validate_complete(row,context,catalog.timestamp(row['observed_at']))
        need(catalog.timestamp(row['observed_at'])<=clock())
        need(not any('failed' in item or 'unknown' in item for item in os.listdir(ROOT/name)))
    need(document(ROOT/'latest.json')[0]==raw and _context().bindings==context.bindings)
    return complete_raw,value


def native_current():
    """No provider call, caller proof, credential refresh, or lock acquisition."""
    context=_context();complete_raw,value=_prior_history(context,True)
    return FreshWitness(value['drive_id'],digest(complete_raw),value['observed_at'],context.bindings,context.revision,_SEAL)


def _bootstrap():
    from . import preservation
    source=hydrate.protected(BOOTSTRAP/'native_probe.py',0o644,limit=131072)[0];need(digest(source)==BOOTSTRAP_SHA)
    raw,terminal=document(BOOTSTRAP/'terminal-projection.json');need(digest(raw)==TERMINAL_SHA)
    need(terminal['source_sha256']==BOOTSTRAP_SHA and terminal['transaction']==BOOTSTRAP_TX
        and terminal['child_terminal'] is True and terminal['classification']=='synthetic_conditional_behavior_observed')
    preservation._terminal_birth(terminal['child_owner'])
    _,owned=document(BOOTSTRAP/'journals'/'owned-folder.json')
    for phase,code in (('wrong-etag',412),('right-etag',204)):
        need(document(BOOTSTRAP/'journals'/(phase+'-ack.json'))[1]['status']==code)
    need(document(BOOTSTRAP/'journals'/'complete.json')[1]['classification']=='synthetic_conditional_behavior_observed')
    for name in os.listdir(BOOTSTRAP/'journals'):
        need('failed' not in name and 'unknown' not in name and not name.endswith('.bin'))
        if name.endswith('-child.json'):
            child=document(BOOTSTRAP/'journals'/name)[1]
            preservation._terminal_birth(child)
    module=types.ModuleType('_verified_synthetic_expiry');module.__file__=str(BOOTSTRAP/'native_probe.py')
    exec(compile(source,str(BOOTSTRAP/'native_probe.py'),'exec'),module.__dict__)
    return module,owned


def _replace_latest(value):
    raw=bundle.canonical(value);path=ROOT/'latest-new.json'
    hydrate.exclusive(path,raw);os.replace(path,ROOT/'latest.json');native_restore.fsync_directory(ROOT)


def native_check(repository_fd):
    """Renew once under caller's canonical fd; missing entry integration refuses.

    No timer/CLI integration exists. The fixed installed entry must implement the
    full owner/history exception before this can issue its first provider call.
    """
    from . import entry
    context=_context();files=catalog.NativeFiles(repository_fd);files.require_repository_lock();_root()
    idle=getattr(entry,'expiry_capability_idle',None)
    need(callable(idle) and idle(repository_fd) is True)
    # Refuse any unresolved prior renewal, including an interrupted pointer write.
    if os.listdir(ROOT):_prior_history(context,False)
    module,owned=_bootstrap()
    snapshot=hydrate.protected(Path('/etc/careertrellis/rclone.conf'),0o600);remote=_mapping(snapshot[0])
    # The accepted source transaction is derived from authenticated native catalog
    # evidence, never a caller field or the synthetic transaction.
    store=catalog.NativeReadStore(repository_fd)
    with store.io.locked():
        records=store.records();retired=store.retired(records);live=[r for key,r in records.items() if key not in retired]
        need(len(live)>=2);accepted=max(live,key=lambda r:catalog.timestamp(r['entry']['accepted_at']))
        source_tx=accepted['evidence']['download']['transaction'];need(catalog.hex_value(source_tx,32))
    tx=secrets.token_hex(16);root=ROOT/tx;root.mkdir(mode=0o700);native_restore.fsync_directory(ROOT)
    journal=module.Journal.__new__(module.Journal);journal.root=root
    journal.once('renewal-intent',{'schema':'careertrellis.expiry-renewal/v1','transaction':tx,
        'owner':native_restore.process_identity(os.getpid()),'accepted_source_transaction':source_tx})
    _replace_latest({'schema':'careertrellis.expiry-capability-latest/v1','transaction':tx,'state':'checking','complete_sha256':None})
    def token_ready(value):
        token=graph_delete.bearer(value,clock())
        expiry=catalog.timestamp(json.loads(value['token'])['expiry'])
        if (expiry-clock()).total_seconds()<1200:raise graph_delete.Refused('existing_token_window_insufficient')
        return token
    try:token=token_ready(remote)
    except graph_delete.Refused:
        remote=graph_delete.refresh_existing_remote(remote,source_tx,repository_fd,snapshot)
        token=token_ready(remote)
    need(remote['drive_id']==owned['drive_id'])
    parser=configparser.ConfigParser(interpolation=None);parser['onedrive']=remote
    import io
    stream=io.StringIO();parser.write(stream);isolated=stream.getvalue().encode()
    def unchanged():
        files.require_repository_lock();need(hydrate.protected(Path('/etc/careertrellis/rclone.conf'),0o600)==snapshot)
        need(_context().bindings==context.bindings)
    try:
        module.NAME='conditional-delete-probe-'+tx+'.txt'
        reader=module.Readback(BOOTSTRAP_TX,isolated,remote['drive_id'],journal,unchanged)
        probe=module.Probe(tx,remote['drive_id'],module.HTTP(token),journal,unchanged,reader)
        probe.folder='careertrellis-permanent-expiry-probe-'+BOOTSTRAP_TX
        probe.parent=owned['parent_id'];probe.rootid=owned['root_id']
        code,raw=probe.read(probe.base+'/items/'+module.quote(probe.parent));need(code==200)
        need(probe.check(probe.document(raw),probe.folder,probe.rootid,True)==probe.parent)
        children=probe.base+'/items/'+module.quote(probe.parent)+'/children'
        def empty_parent():
            code,raw=probe.read(children);need(code==200)
            row=probe.document(raw);need(set(row)<={'value','@odata.context'} and row.get('value')==[])
        empty_parent()
        code,_=probe.read(probe.base+'/items/'+module.quote(probe.parent)+':/'+module.quote(module.NAME));need(code==404)
        first=('PUBLIC SYNTHETIC EXPIRY FIXTURE '+tx+' VERSION 1\n').encode()
        second=('PUBLIC SYNTHETIC EXPIRY FIXTURE '+tx+' VERSION 2\n').encode()
        need(len(first)<=1024 and len(second)<=1024)
        code,raw=probe.dispatch('create-file','PUT',probe.base+'/items/'+module.quote(probe.parent)+':/'+
            module.quote(module.NAME)+':/content?@microsoft.graph.conflictBehavior=fail',first,
            {'If-None-Match':'*','Content-Type':'text/plain'});need(code==201)
        row=probe.document(raw);probe.item=probe.check(row,module.NAME,probe.parent)
        journal.once('owned-file',{'item_id':probe.item,'parent_id':probe.parent,'drive_id':remote['drive_id']})
        old=probe.verify(first);need(old==module.etag(row['eTag']))
        endpoint=probe.base+'/items/'+module.quote(probe.item)
        code,_=probe.dispatch('write-two','PUT',endpoint+'/content',second,
            {'If-Match':old,'Content-Type':'text/plain'});need(code==200)
        tag=probe.verify(second);need(tag!=old)
        code,_=probe.dispatch('wrong-etag','POST',endpoint+'/permanentDelete',headers={'If-Match':old})
        need(code==412 and probe.verify(second)==tag)
        code,_=probe.dispatch('right-etag','POST',endpoint+'/permanentDelete',headers={'If-Match':tag});need(code==204)
        probe.exact_absence();empty_parent();unchanged()
        need(reader.count==3)
        for name in ('one','two','three'):
            terminal=document(root/('readback-'+name+'-terminal.json'))[1]
            need(terminal['terminal'] is True and terminal['exit_code']==0
                and not Path('/proc/%d'%terminal['pid']).exists())
        value={'schema':'careertrellis.expiry-capability-complete/v1','transaction':tx,
            'drive_id':remote['drive_id'],'observed_at':clock().isoformat(),'bindings':context.bindings,
            'revision':context.revision,'stale_status':412,'current_status':204,'exact_absence':True,
            'children_terminal':True,'bootstrap_source_sha256':BOOTSTRAP_SHA}
        validate_complete(value,context,clock());journal.once('complete',value)
        _replace_latest({'schema':'careertrellis.expiry-capability-latest/v1','transaction':tx,
            'state':'complete','complete_sha256':digest(bundle.canonical(value))})
        return {'result':'synthetic_capability_renewed','capability_sha256':digest(bundle.canonical(value)),
            'observed_at':value['observed_at'],'backup_expiry_authorized':False}
    except Exception:
        journal.once('renewal-failed',{'classification':'renewal_unavailable_no_retry','resources_retained':True})
        raise CapabilityRefused('expiry_capability_requires_reconciliation') from None
