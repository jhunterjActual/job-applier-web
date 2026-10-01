"""Trusted workstation relay. No credential input, peer SSH or automatic retries.

Call Relay(...).run(seconds=...) only with independently approved manifest and
scope. A failed claimed request is reconciliation-only. Private selections stay
in process memory/captured SSH pipes; the durable journal contains digests only.
"""
import base64
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import threading
import time
from uuid import UUID

OPS='bbbc4298b2ca80df107e64d2673d6c1f243c5e41'
HOSTS={'web':'hunter@129.212.153.245','db':'hunter@157.245.113.244'}
MODULES={'owner_recovery_reserve','owner_recovery_reserve_host','owner_recovery_reserve_cli',
    'owner_recovery_reserve_relay','owner_recovery_reserve_operator'}
ACTIONS={'consume','receipt','enter','attest'}
ERROR='Operator relay stopped; retain journal and reconcile without republishing.'

def require(value):
    if not value:raise ValueError(ERROR)
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def digest(raw):return hashlib.sha256(raw).hexdigest()
def hex64(value):return type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None
def identifier(value):
    try:return type(value) is str and str(UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False
def stamp(value):return type(value) in (int,float) and math.isfinite(value) and value>=0
def strict(raw):
    def pairs(items):
        result={}
        for key,value in items:require(key not in result);result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError(ERROR)))

# Small fixed command; all larger public source and private request data use stdin.
BOOT="import resource;resource.setrlimit(resource.RLIMIT_CORE,(0,0));import sys,json,base64;e=json.load(sys.stdin);exec(compile(base64.b64decode(e['code']),'reserve-relay','exec'),{'request':e['request']})"
PROTECTED=r'''
import base64,hashlib,io,json,os,stat,sys
from pathlib import Path
def require(value):
 if not value:raise ValueError()
def protected(path):
 for parent in path.parents:
  item=parent.lstat();require(stat.S_ISDIR(item.st_mode) and item.st_uid==0 and not item.st_mode&0o022)
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  item=os.fstat(fd);require(stat.S_ISREG(item.st_mode) and item.st_uid==0 and item.st_nlink==1 and not item.st_mode&0o022 and item.st_size<=1048576)
  with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(1048577)
  require(len(raw)<=1048576);return raw
 finally:os.close(fd)
require(os.geteuid()==0)
'''
WEB=PROTECTED+r'''
require(os.uname().nodename=='careertrellis-staging-web-1')
root=Path('/opt/careertrellis/owner-recovery-reserve')/request['commit']
raw=protected(root/'source-manifest.json');require(hashlib.sha256(raw).hexdigest()==request['manifest_sha256'])
manifest=json.loads(raw)
for name,pin in manifest['wrapper'].items():
 require(hashlib.sha256(protected(root/name)).hexdigest()==pin)
path=root/'backend/owner_recovery_reserve_cli.py'
namespace={'__file__':str(path),'__name__':'verified_reserve_cli'}
exec(compile(protected(path),str(path),'exec'),namespace)
verify=namespace['load_verified']()
result=namespace['relay_action'](request['argv'],verify,stdin=io.BytesIO(base64.b64decode(request['input'],validate=True)))
print(json.dumps(result,sort_keys=True,separators=(',',':')))
'''
DB=PROTECTED+r'''
import importlib.abc,importlib.util
require(os.uname().nodename=='job-applier-db-staging-01')
root=Path('/opt/careertrellis/staging/releases/bbbc4298b2ca80df107e64d2673d6c1f243c5e41')
mapping={}
for name,pin in request['pins'].items():
 require(name.startswith('backend/') and len(Path(name).parts)==2 and name.endswith('.py'))
 require(hashlib.sha256(protected(root/name)).hexdigest()==pin)
 mapping[Path(name).stem]=(root/name,pin)
require({'admission_operations','identity_plan_store','identity_plan_packet'}<=set(mapping))
require(all(name not in sys.modules for name in mapping))
class Loader(importlib.abc.Loader):
 def create_module(self,spec):return None
 def exec_module(self,module):
  path,pin=mapping[module.__name__];raw=protected(path);require(hashlib.sha256(raw).hexdigest()==pin)
  exec(compile(raw,str(path),'exec'),module.__dict__)
class Finder(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name in mapping:return importlib.util.spec_from_file_location(name,mapping[name][0],loader=Loader())
  candidate=root/'backend'/name.split('.')[0]
  require(not candidate.exists() and not candidate.with_suffix('.py').exists());return None
sys.meta_path.insert(0,Finder())
import identity_plan_store as store
import admission_operations as ops
first,pair=store.load(activation=True)
require(pair['provider']['operations']==pair['activation']['operations']=='bbbc4298b2ca80df107e64d2673d6c1f243c5e41')
require(store.load(activation=True)==(first,pair) and 0<len(first)<=1024)
for path,pin in mapping.values():require(hashlib.sha256(protected(path)).hexdigest()==pin)
print(json.dumps({'selection_hex':first.hex(),'selection_sha256':hashlib.sha256(first).hexdigest(),
 'activation_sha256':hashlib.sha256(ops.encoded(pair['activation'])).hexdigest()},sort_keys=True))
'''

def bounded_run(args,payload,timeout):
    require(type(payload) is bytes and len(payload)<=524288 and 0<timeout<=15)
    process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    output=bytearray();failed=threading.Event()
    def drain(pipe,retain):
        size=0
        try:
            while True:
                raw=pipe.read(4096)
                if not raw:break
                size+=len(raw)
                if size>32768:failed.set();process.kill();break
                if retain:output.extend(raw)
                else:failed.set()
        except BaseException:failed.set()
        finally:pipe.close()
    def feed():
        try:process.stdin.write(payload);process.stdin.close()
        except BaseException:failed.set()
    threads=[threading.Thread(target=drain,args=(process.stdout,True),daemon=True),
        threading.Thread(target=drain,args=(process.stderr,False),daemon=True),threading.Thread(target=feed,daemon=True)]
    for thread in threads:thread.start()
    try:status=process.wait(timeout=timeout)
    except BaseException:
        process.kill();process.wait(timeout=5);raise ValueError(ERROR) from None
    finally:
        for thread in threads:thread.join(timeout=1)
    require(status==0 and not failed.is_set() and not any(thread.is_alive() for thread in threads))
    return bytes(output)

def local_file(path,limit=1048576):
    for item in (path,*path.parents):
        require(not item.is_symlink() and not (hasattr(item,'is_junction') and item.is_junction()))
    info=path.stat();require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=limit)
    raw=path.read_bytes();require(len(raw)<=limit);return raw

class Journal:
    """Dedicated operator-controlled local directory; immutable sanitized phases."""
    def __init__(self,root):
        self.root=Path(root)
        require(self.root.is_absolute() and self.root.is_dir())
        for item in (self.root,*self.root.parents):
            require(not item.is_symlink() and not (hasattr(item,'is_junction') and item.is_junction()))
        if os.name=='posix':
            info=self.root.stat();require(info.st_uid==os.geteuid() and not info.st_mode&0o077)
    def path(self,request_id,phase):
        require(identifier(request_id) and phase in ('claim','observation','published','reconciled'))
        return self.root/(request_id+'-'+phase+'.json')
    def read(self,request_id,phase):return strict(local_file(self.path(request_id,phase),4096))
    def write(self,request_id,phase,value):
        path=self.path(request_id,phase);raw=encoded(value);require(len(raw)<=4096)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        if os.name=='posix':
            fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        require(local_file(path,4096)==raw)

class Relay:
    def __init__(self,*,bundle_directory,manifest_sha256,scope,journal_directory,
                 runner=bounded_run,now=time.time,monotonic=time.monotonic,sleep=time.sleep):
        self.bundle=Path(bundle_directory);self.manifest_sha256=manifest_sha256
        require(self.bundle.is_absolute() and re.fullmatch('[a-f0-9]{40}',self.bundle.name) and hex64(manifest_sha256))
        require(type(scope) is dict and set(scope)=={'operation','selection_sha256','activation_sha256',
            'enrollment_sha256','closure_sha256','actions'} and identifier(scope['operation']))
        require(all(hex64(scope[key]) for key in ('selection_sha256','activation_sha256','enrollment_sha256','closure_sha256')))
        require(scope['closure_sha256']==manifest_sha256 and type(scope['actions']) is list
            and 0<len(scope['actions'])<=4 and all(type(action) is str and action in ACTIONS for action in scope['actions'])
            and len(set(scope['actions']))==len(scope['actions']))
        self.scope=strict(encoded(scope));self.runner=runner;self.now=now;self.monotonic=monotonic;self.sleep=sleep
        self.journal=Journal(journal_directory);self.deadline=None
        self.verify_bundle()
    def verify_bundle(self):
        raw=local_file(self.bundle/'source-manifest.json');require(digest(raw)==self.manifest_sha256)
        manifest=strict(raw)
        require(type(manifest) is dict and set(manifest)=={'version','operations','wrapper','library'}
            and type(manifest['version']) is int and manifest['version']==1 and manifest['operations']==OPS)
        require(type(manifest['wrapper']) is dict and set(manifest['wrapper'])=={'backend/'+name+'.py' for name in MODULES})
        for name,pin in manifest['wrapper'].items():
            require(hex64(pin) and digest(local_file(self.bundle/name))==pin)
        require(digest(local_file(Path(__file__).absolute()))==manifest['wrapper']['backend/owner_recovery_reserve_operator.py'])
        require(type(manifest['library']) is dict and manifest['library'])
        for name,pin in manifest['library'].items():
            require(type(name) is str and re.fullmatch(r'(backend/[a-z0-9_]+\.py|deploy/systemd/[A-Za-z0-9_.@/-]+)',name)
                and '..' not in Path(name).parts and hex64(pin))
        self.pins={name:pin for name,pin in manifest['library'].items() if name.startswith('backend/')}
        require({'backend/admission_operations.py','backend/identity_plan_store.py','backend/identity_plan_packet.py'}<=set(self.pins))
    def call(self,role,code,request):
        self.verify_bundle()
        remaining=15 if self.deadline is None else min(15,self.deadline-self.monotonic())
        require(remaining>0)
        # Fixed error boundary prevents private exception values reaching pipes.
        safe='try:\n'+'\n'.join(' '+line for line in code.splitlines())+'\nexcept BaseException:\n raise SystemExit(1)\n'
        payload=encoded({'code':base64.b64encode(safe.encode()).decode(),'request':request})
        python='/opt/careertrellis/staging/releases/'+OPS+'/backend/venv/bin/python' if role=='web' else '/usr/bin/python3'
        command='sudo -n /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 CAREERTRELLIS_CONFIG_READ_ONLY=1 PYTHONDONTWRITEBYTECODE=1 '+python+' -I -B -c '+shlex.quote(BOOT)
        args=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5','-o','ForwardAgent=no',
            '-o','ClearAllForwardings=yes','-o','StrictHostKeyChecking=yes',HOSTS[role],command]
        raw=self.runner(args,payload,remaining)
        require(type(raw) is bytes and 0<len(raw)<=32768)
        value=strict(raw);require(type(value) is dict);return value
    def web(self,action,*args,body=None):
        require(action in ('relay-peek','relay-status','relay-publish'))
        return self.call('web',WEB,{'commit':self.bundle.name,'manifest_sha256':self.manifest_sha256,
            'argv':[action,*args],'input':base64.b64encode(b'' if body is None else encoded(body)).decode()})
    def validate_request(self,req,*,fresh=True):
        require(type(req) is dict and set(req)=={'version','id','created','expires','context'}
            and type(req['version']) is int and req['version']==1 and identifier(req['id'])
            and stamp(req['created']) and stamp(req['expires']) and 0<req['expires']-req['created']<=30)
        context=req['context'];require(type(context) is dict and set(context)=={'action','stage','operation',
            'enrollment_sha256','selection_hex','selection_sha256','activation_sha256','closure_sha256'})
        require(context['action'] in self.scope['actions'] and context['stage'] in ('pre','post'))
        require(all(context[key]==self.scope[key] for key in ('operation','enrollment_sha256','selection_sha256','activation_sha256','closure_sha256')))
        value=context['selection_hex'];require(type(value) is str and 0<len(value)<=2048 and re.fullmatch('(?:[a-f0-9]{2})+',value))
        require(digest(bytes.fromhex(value))==context['selection_sha256'])
        if fresh:require(stamp(self.now()) and req['created']<=self.now()<req['expires'])
        return context
    def once(self):
        try:return self._once()
        except BaseException:raise ValueError(ERROR) from None
    def _once(self):
        value=self.web('relay-peek')
        if value=={'status':'idle'}:return 'idle'
        require(set(value)=={'status','request'} and value['status']=='pending')
        req=value['request'];context=self.validate_request(req);request_hash=digest(encoded(req))
        self.journal.write(req['id'],'claim',{'request_sha256':request_hash,'scope_sha256':digest(encoded(self.scope))})
        observed=self.call('db',DB,{'pins':self.pins})
        require(set(observed)=={'selection_hex','selection_sha256','activation_sha256'}
            and all(observed[key]==context[key] for key in observed))
        self.validate_request(req)
        require(self.web('relay-peek')==value)
        self.validate_request(req)
        observation={key:context[key] for key in ('selection_hex','selection_sha256','activation_sha256',
            'closure_sha256','enrollment_sha256','operation')}
        observation['request_sha256']=request_hash
        self.journal.write(req['id'],'observation',{'request_sha256':request_hash,'observation_sha256':digest(encoded(observation))})
        require(self.web('relay-publish',body={'request_id':req['id'],'observation':observation})=={'status':'published'})
        self.journal.write(req['id'],'published',{'request_sha256':request_hash,'acknowledged':True})
        return 'published'
    def reconcile(self,request_id):
        """One status read only; never re-observe DB or republish a lost request."""
        claim=self.journal.read(request_id,'claim')
        path=self.journal.path(request_id,'observation')
        observed=self.journal.read(request_id,'observation') if path.exists() or path.is_symlink() else None
        require(type(claim) is dict and set(claim)=={'request_sha256','scope_sha256'}
            and all(hex64(value) for value in claim.values()))
        if observed is not None:
            require(type(observed) is dict and set(observed)=={'request_sha256','observation_sha256'}
                and all(hex64(value) for value in observed.values()) and observed['request_sha256']==claim['request_sha256'])
        require(claim['scope_sha256']==digest(encoded(self.scope)))
        status=self.web('relay-status',request_id)
        require(set(status)=={'status','request','response','currently_fresh','authorization'}
            and status['authorization'] is False and type(status['currently_fresh']) is bool)
        self.validate_request(status['request'],fresh=False)
        require(status['request']['id']==request_id and digest(encoded(status['request']))==claim['request_sha256'])
        require(status['status'] in ('answered','pending','expired'))
        if status['status']=='answered':
            require(observed is not None)
            response=status['response'];require(type(response) is dict and set(response)=={'version','request_id','observed','observation'}
                and type(response['version']) is int and response['version']==1 and response['request_id']==request_id
                and stamp(response['observed']) and status['request']['created']<=response['observed']<status['request']['expires']
                and digest(encoded(response['observation']))==observed['observation_sha256'])
        else:require(status['response'] is None)
        result={'status':status['status'],'authorization':False}
        self.journal.write(request_id,'reconciled',result);return result
    def run(self,*,seconds=900,poll=0.5):
        require(type(seconds) in (int,float) and 0<seconds<=900 and type(poll) in (int,float) and 0.1<=poll<=5)
        require(self.deadline is None);self.deadline=self.monotonic()+seconds
        count=0
        try:
            while self.monotonic()<self.deadline:
                if self.once()=='published':count+=1
                self.sleep(min(poll,max(0,self.deadline-self.monotonic())))
            return {'outcome':'relay_window_ended','published':count,'authorization':False}
        except BaseException:raise ValueError(ERROR) from None


def main(argv=None):
    """Invoke as python -I -B <verified bundle>/backend/<this file>, never pyc.

    The independently approved scope hash is mandatory. Scope files contain
    only approved operation/digests/actions; never selected plan bytes or codes.
    """
    class Parser(argparse.ArgumentParser):
        def error(self,message):raise ValueError(ERROR)
    try:
        parser=Parser(add_help=False)
        for name in ('bundle','manifest-sha256','scope-file','scope-sha256','journal-dir'):
            parser.add_argument('--'+name,required=True)
        parser.add_argument('--seconds',type=float,default=900)
        parser.add_argument('--reconcile')
        args=parser.parse_args(argv)
        require(hex64(args.scope_sha256) and 0<args.seconds<=900)
        raw=local_file(Path(args.scope_file).absolute(),8192)
        require(digest(raw)==args.scope_sha256)
        relay=Relay(bundle_directory=Path(args.bundle).absolute(),manifest_sha256=args.manifest_sha256,
            scope=strict(raw),journal_directory=Path(args.journal_dir).absolute())
        if args.reconcile is None:
            result=relay.run(seconds=args.seconds)
            require(set(result)=={'outcome','published','authorization'}
                and result['outcome']=='relay_window_ended' and type(result['published']) is int
                and result['published']>=0 and result['authorization'] is False)
        else:
            require(identifier(args.reconcile));result=relay.reconcile(args.reconcile)
            require(set(result)=={'status','authorization'} and result['status'] in ('answered','pending','expired')
                and result['authorization'] is False)
        print(encoded(result).decode());return 0
    except BaseException:
        print(ERROR,file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
