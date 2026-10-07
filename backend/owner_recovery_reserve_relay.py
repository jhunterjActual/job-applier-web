"""Fresh convergence challenge protocol; no network or credential traffic.

Trust comes from the independently pinned operator-workstation relay reading
both hosts through existing SSH, NOT from possession of a root-owned JSON file.
The root and its parent reserve directory must already exist. Protocol locks
never acquire credential, host or database locks. Requests and responses remain
for reconciliation; an expired or answered nonce cannot authorize another check.
"""
from contextlib import contextmanager
import ctypes
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
from uuid import UUID,uuid4
import owner_recovery as recovery

ROOT=Path('/var/lib/careertrellis-owner-recovery-reserve/convergence')
LIMIT=256
require=recovery.require

def digest(raw):return hashlib.sha256(raw).hexdigest()
def instant(value):return type(value) in (int,float) and math.isfinite(value) and value>=0
def identifier(value):
    try:return type(value) is str and str(UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False
def hex64(value):return type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None

def root():
    recovery.parents(ROOT)
    parent=ROOT.parent.lstat()
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid==0 and stat.S_IMODE(parent.st_mode)==0o700)
    item=ROOT.lstat()
    require(stat.S_ISDIR(item.st_mode) and item.st_uid==0 and stat.S_IMODE(item.st_mode)==0o700)
    return ROOT

@contextmanager
def locked():
    import fcntl
    directory=root();fd=os.open(directory/'protocol.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        item=os.fstat(fd)
        require(stat.S_ISREG(item.st_mode) and item.st_uid==0 and item.st_nlink==1 and stat.S_IMODE(item.st_mode)==0o600)
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);yield
    finally:os.close(fd)

def path(kind,request_id):
    require(kind in ('request','response') and identifier(request_id))
    return root()/(kind+'-'+request_id+'.json')

def read(location):
    raw=recovery.protected(location,limit=8192);value=recovery.strict_json(raw)
    require(recovery.encoded(value)==raw);return value

def _rename_noreplace(source,target):
    """Linux atomic publication preserving single-link reader invariants.

    No replace/link fallback: unsupported libc/kernel/filesystem leaves the
    fully written temporary evidence intact and stops the operation.
    """
    require(sys.platform.startswith('linux') and source.parent==target.parent==root())
    library=ctypes.CDLL(None,use_errno=True)
    rename=getattr(library,'renameat2',None);require(rename is not None)
    rename.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint)
    rename.restype=ctypes.c_int
    result=rename(-100,os.fsencode(source),-100,os.fsencode(target),1) # AT_FDCWD, RENAME_NOREPLACE
    if result!=0:
        error=ctypes.get_errno()
        raise OSError(error,os.strerror(error))

def exclusive(location,value):
    raw=recovery.encoded(value);require(len(raw)<=8192)
    temporary=root()/('.pending-'+str(uuid4()))
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    # No replace: interrupted publication preserves the old evidence and temp.
    _rename_noreplace(temporary,location)
    fd=os.open(root(),os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def wire_context(context):
    require(type(context) is dict and set(context)=={'action','stage','operation','enrollment_sha256',
        'selection','activation_sha256','closure_sha256'})
    selected=context['selection'];require(type(selected) is bytes and 0<len(selected)<=1024)
    result={k:v for k,v in context.items() if k!='selection'}
    result.update(selection_hex=selected.hex(),selection_sha256=digest(selected))
    check_context(result);return result

def check_context(value):
    require(type(value) is dict and set(value)=={'action','stage','operation','enrollment_sha256',
        'selection_hex','selection_sha256','activation_sha256','closure_sha256'})
    require(value['action'] in ('consume','receipt','enter','attest') and value['stage'] in ('pre','post')
        and identifier(value['operation']))
    require(all(hex64(value[n]) for n in ('enrollment_sha256','selection_sha256','activation_sha256','closure_sha256')))
    selected=value['selection_hex'];require(type(selected) is str and 0<len(selected)<=2048 and re.fullmatch('(?:[a-f0-9]{2})+',selected))
    require(digest(bytes.fromhex(selected))==value['selection_sha256'])

def request(request_id):
    value=read(path('request',request_id))
    require(type(value) is dict and set(value)=={'version','id','created','expires','context'}
        and type(value['version']) is int and value['version']==1 and value['id']==request_id
        and instant(value['created']) and instant(value['expires']) and 0<value['expires']-value['created']<=30)
    check_context(value['context']);return value

def response(req):
    value=read(path('response',req['id']))
    require(type(value) is dict and set(value)=={'version','request_id','observed','observation'}
        and type(value['version']) is int and value['version']==1 and value['request_id']==req['id']
        and instant(value['observed']) and req['created']<=value['observed']<req['expires'])
    check_observation(req,value['observation']);return value

def check_observation(req,value):
    require(type(value) is dict and set(value)=={'request_sha256','selection_hex','selection_sha256',
        'activation_sha256','closure_sha256','enrollment_sha256','operation'})
    require(value['request_sha256']==digest(recovery.encoded(req)))
    require(all(value[key]==req['context'][key] for key in set(value)-{'request_sha256'}))

def exists(location):return location.exists() or location.is_symlink()

def inventory():
    found=[];count=0
    for entry in root().iterdir():
        count+=1;require(count<=3*LIMIT+1)
        if entry.name.startswith('request-') and entry.name.endswith('.json'):
            request_id=entry.name[8:-5];require(identifier(request_id));found.append(request_id)
            require(len(found)<=LIMIT)
    return found

def peek(*,now=time.time):
    """Bounded read-only discovery; does not create a lock or consume a request."""
    stamp=now();require(instant(stamp));pending=[]
    for request_id in inventory():
        req=request(request_id)
        if req['created']<=stamp<req['expires'] and not exists(path('response',request_id)):pending.append(req)
    if not pending:return {'status':'idle'}
    return {'status':'pending','request':max(pending,key=lambda req:(req['created'],req['id']))}

def publish(request_id,observation,*,now=time.time):
    with locked():
        req=request(request_id);stamp=now()
        require(instant(stamp) and req['created']<=stamp<req['expires'])
        check_observation(req,observation)
        require(not exists(path('response',request_id)))
        value={'version':1,'request_id':request_id,'observed':stamp,'observation':dict(observation)}
        exclusive(path('response',request_id),value)
        require(request(request_id)==req and response(req)==value)
        stamp=now();require(instant(stamp) and value['observed']<=stamp<req['expires'])
    return {'status':'published'}

def inspect(request_id,*,now=time.time):
    """Historical readback only. An answer here grants no continuing authority."""
    req=request(request_id);stamp=now();require(instant(stamp))
    present=exists(path('response',request_id))
    return {'status':'answered' if present else ('expired' if stamp>=req['expires'] else 'pending'),
        'request':req,'response':response(req) if present else None,
        'currently_fresh':req['created']<=stamp<req['expires'],'authorization':False}

def request_observation(context,*,now=time.time,wait=time.sleep,poll=0.25,monotonic=time.monotonic):
    require(type(poll) in (int,float) and 0.05<=poll<=1)
    wire=wire_context(context);stamp=now();require(instant(stamp))
    req={'version':1,'id':str(uuid4()),'created':stamp,'expires':stamp+30,'context':wire}
    with locked():
        require(len(inventory())<LIMIT);exclusive(path('request',req['id']),req)
    previous=stamp;deadline=monotonic()+30
    while True:
        require(monotonic()<deadline)
        stamp=now();require(instant(stamp) and previous<=stamp<req['expires']);previous=stamp
        require(request(req['id'])==req)
        if exists(path('response',req['id'])):
            value=response(req)
            stamp=now();require(instant(stamp) and previous<=stamp<req['expires'] and value['observed']<=stamp)
            return bytes.fromhex(value['observation']['selection_hex'])
        wait(min(poll,req['expires']-stamp))
