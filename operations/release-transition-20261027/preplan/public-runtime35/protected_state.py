"""Native protected-byte/CAS primitives, no standalone host entrypoint.

Only exact caller-reviewed allowlists are accepted. Root-owned paths and one
linked regular inode; no symlinks, path repair, permission repair or deletion.
"""
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import uuid

class StateRefused(RuntimeError):pass

def need(value):
    if not value:raise StateRefused('protected_state_refused_inspect_no_replay')

FIELDS=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
def identity(s):return tuple(getattr(s,k) for k in FIELDS)

class ProtectedState:
    def __init__(self, allowed, *, test_root=None):
        need(type(allowed) is dict and allowed)
        need(all(PurePosixPath(p).is_absolute() and '..' not in PurePosixPath(p).parts
                 and mode in (0o600,0o644) for p,mode in allowed.items()))
        self.allowed=dict(allowed)
        self.test_root=Path(test_root).resolve() if test_root else None
        need(self.test_root is not None or (os.name=='posix' and os.geteuid()==0))
    def path(self,absolute):
        need(absolute in self.allowed)
        p=Path(absolute)
        if self.test_root:p=self.test_root/str(p).lstrip('/\\').replace(':','')
        return p
    def parent(self,p):
        for folder in p.parents:
            s=folder.lstat()
            need(stat.S_ISDIR(s.st_mode) and not stat.S_ISLNK(s.st_mode))
            if self.test_root:
                if folder==self.test_root:break
            else:need(s.st_uid==0 and not s.st_mode&0o022)
    def read(self,absolute):
        p=self.path(absolute);self.parent(p);before=p.lstat()
        need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=131072)
        if not self.test_root:need(before.st_uid==0 and stat.S_IMODE(before.st_mode)==self.allowed[absolute])
        fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        try:
            need(identity(os.fstat(fd))==identity(before))
            with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(before.st_size+1)
            need(len(raw)==before.st_size and identity(os.fstat(fd))==identity(before)
                 and identity(p.lstat())==identity(before))
            return raw
        finally:os.close(fd)
    def sync(self,folder):
        if self.test_root and os.name=='nt':return
        fd=os.open(folder,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0))
        try:os.fsync(fd)
        finally:os.close(fd)
    def replace(self,absolute,raw,expected):
        need(type(raw) is bytes and len(raw)<=131072 and type(expected) is bytes)
        need(self.read(absolute)==expected)
        p=self.path(absolute);self.parent(p)
        # Caller continuously owns existing role control and canonical fences.
        # Unique staging file is retained on failure; no automatic cleanup.
        leaf=p.parent/('.release-transition-'+uuid.uuid4().hex)
        fd=os.open(leaf,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),self.allowed[absolute])
        try:
            if os.name=='posix':os.fchmod(fd,self.allowed[absolute])
            with os.fdopen(fd,'wb',closefd=False) as stream:
                stream.write(raw);stream.flush();os.fsync(fd)
        finally:os.close(fd)
        self.sync(p.parent)
        need(self.read(absolute)==expected)
        os.replace(leaf,p);self.sync(p.parent)
        need(self.read(absolute)==raw)
        # No secret-derived hash leaves this primitive or enters RPC receipts.
        return None

def known_pair(store,private,public,allowed_pairs):
    """Classify each leaf explicitly, allowing only reviewed interrupted pairs."""
    observed=(store.read(private),store.read(public))
    need(type(allowed_pairs) in (list,tuple) and observed in allowed_pairs)
    return observed
