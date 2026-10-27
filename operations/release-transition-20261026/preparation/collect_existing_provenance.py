"""Bounded READ ONLY baseline software provenance observation.

No write/open-for-write, temporary file, snapshot, lock, subprocess, package
import, provider call, environment read or service operation. Stdout contains
public software inventories/hashes only. Primary must review this exact source
and authorize its one observation separately; it is not preparation authority.
"""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import stat
import sys
import time

BASE='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
PREVIOUS='4095417909a8b13ad252be9420e422af3662bef5'
ROOT=Path('/opt/careertrellis/staging')
STAGE=ROOT/'title-offweb-import-7a4d3f077c4d'
BROWSER=Path('/var/lib/careertrellis/shared/ms-playwright')
PINS={
 'manifest.json':'3f98375baed2cf3c3f0fc657eb3e7e1c985c6ed4ac90a686207fdb359234903a',
 'success.json':'5398ddb66edcbe7d1a4c2a443352a8672eabd3f6a64278c36a757536d0f8152a',
 'venv-provenance.json':'82ab88e5d3f76a21ae3ee8d4fc51cd64a6d24c09faae33edbda4067d9526af63'}
FIELDS=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
MAX_FILES=30000
MAX_TREE=8*1024**3

class Refused(RuntimeError):pass
def need(value):
    if not value:raise Refused('baseline_provenance_refused_no_changes')
def same(s):return tuple(getattr(s,k) for k in FIELDS)
def decode(raw):
    def pairs(values):
        d={}
        for k,v in values:need(k not in d);d[k]=v
        return d
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
def parents(path,tree=None,owner=0):
    for parent in path.parents:
        s=parent.lstat();allowed={0,owner} if tree is not None and (parent==tree or parent.is_relative_to(tree)) else {0}
        need(stat.S_ISDIR(s.st_mode) and s.st_uid in allowed and not s.st_mode&0o022)
def regular(path,*,tree=None,owner=0,body=False):
    parents(path,tree,owner);s=path.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid in {0,owner} and s.st_nlink==1
         and not s.st_mode&0o022 and s.st_size<=(16*1024**2 if body else 1024**3))
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        need(same(os.fstat(fd))==same(s))
        with os.fdopen(fd,'rb',closefd=False) as f:
            if body:value=f.read(s.st_size+1)
            else:
                h=hashlib.sha256();remaining=s.st_size
                while remaining:
                    block=f.read(min(1024*1024,remaining));need(block)
                    h.update(block);remaining-=len(block)
                need(f.read(1)==b'');value=h.hexdigest()
        need(same(os.fstat(fd))==same(s)==same(path.lstat()))
        if body:need(len(value)==s.st_size)
        return value,s
    finally:os.close(fd)
def pointer(name):
    p=ROOT/name;parents(p);s=p.lstat();need(stat.S_ISLNK(s.st_mode) and s.st_uid==0 and s.st_nlink==1)
    result=p.resolve(strict=True);need(result.parent==ROOT/'releases');return result.name
def inventory(root,*,owner=0,venv=False):
    parents(root);r=root.lstat();need(stat.S_ISDIR(r.st_mode) and r.st_uid in {0,owner} and not r.st_mode&0o022)
    files={};descriptors={};total=0;entries=0;deadline=time.monotonic()+300
    for folder,dirs,names in os.walk(root,followlinks=False):
        entries+=len(dirs)+len(names);need(entries<=60000 and time.monotonic()<deadline)
        for name in dirs:
            p=Path(folder)/name
            if p.is_symlink():
                need(venv and p==root/'lib64' and os.readlink(p)=='lib' and p.resolve()==root/'lib')
                s=p.lstat();need(s.st_uid==0 and s.st_nlink==1)
                descriptors['backend/venv/'+p.relative_to(root).as_posix()]={'symlink':'lib'}
            else:
                s=p.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid in {0,owner} and not s.st_mode&0o022)
        for name in names:
            p=Path(folder)/name;relative=p.relative_to(root).as_posix()
            if p.is_symlink():
                need(venv and p.parent==root/'bin' and p.resolve()==Path('/usr/bin/python3.12').resolve())
                s=p.lstat();need(s.st_uid==0 and s.st_nlink==1)
                descriptors['backend/venv/'+relative]={'symlink':os.readlink(p)}
                continue
            value,s=regular(p,tree=root,owner=owner);total+=s.st_size
            need(len(files)+len(descriptors)<MAX_FILES and total<=MAX_TREE)
            files[relative]={'sha256':value,'bytes':s.st_size,'executable':bool(s.st_mode&0o111)}
            if venv:descriptors['backend/venv/'+relative]=value
    return files,descriptors
def verify_accepted(values):
    need(set(values)==set(PINS))
    for name,raw in values.items():need(hashlib.sha256(raw).hexdigest()==PINS[name])
    manifest=decode(values['manifest.json']);success=decode(values['success.json'])
    need(manifest['revision']==BASE and success['revision']==BASE
         and success['manifest_sha256']==PINS['manifest.json'] and success['result']=='inactive_imported'
         and success['activation_performed'] is False and success['destination']==str(ROOT/'releases'/BASE))
    provenance=decode(values['venv-provenance.json']);need(type(provenance) is dict and len(provenance)==7947)
    return provenance
def collect():
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname()=='careertrellis-staging-web-1'
         and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)
    need(pointer('current')==BASE and pointer('previous')==PREVIOUS)
    before={name:regular(STAGE/name,body=True)[0] for name in PINS}
    accepted=verify_accepted(before);base=ROOT/'releases'/BASE
    files,complete=inventory(base/'backend/venv',venv=True);need(complete==accepted)
    prefix='lib/python3.12/site-packages/'
    packages={name[len(prefix):]:value for name,value in files.items() if name.startswith(prefix)};need(packages)
    browser,_=inventory(BROWSER)
    interpreter=Path('/usr/bin/python3.12');need(Path(sys.executable).resolve()==interpreter.resolve())
    fingerprint,_=regular(interpreter)
    result={'schema':'careertrellis.existing-runtime-provenance/v1','baseline':BASE,
       'requirements':{name:regular(base/name)[0] for name in ('backend/requirements.in','backend/requirements.txt')},
       'interpreter':{'path':str(interpreter),'sha256':fingerprint,'python_version':platform.python_version()},
       'site_packages':packages,'browser':browser,
       'platform':{'system':platform.system(),'machine':platform.machine()},
       'reviewed_at':datetime.now(timezone.utc).isoformat(),
       'accepted_venv_provenance':{'path':str(STAGE/'venv-provenance.json'),'sha256':PINS['venv-provenance.json']}}
    need(pointer('current')==BASE and pointer('previous')==PREVIOUS)
    need(all(regular(STAGE/name,body=True)[0]==raw for name,raw in before.items()))
    # Historical accepted venv and current complete venv are checked twice;
    # browser inventory is also stable over the complete observation window.
    need(inventory(base/'backend/venv',venv=True)[1]==accepted)
    need(inventory(BROWSER)[0]==browser)
    return result
if __name__=='__main__':
    try:print(json.dumps(collect(),sort_keys=True,separators=(',',':')))
    except BaseException:
        print('{"result":"baseline_provenance_refused_no_changes"}')
        raise SystemExit(1)
