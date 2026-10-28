"""Exact readonly preflight05 AST closure; no HTTP or carrier entrypoint."""
import os,stat,ast
from pathlib import Path
from native_io import need
FIELDS=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')

BASE='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'

PREVIOUS='4095417909a8b13ad252be9420e422af3662bef5'

def identity(info):
    return tuple((getattr(info, k) for k in FIELDS))

def parents(path):
    for p in Path(path).parents:
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and (not s.st_mode & 18))

def read_protected(path, limit=16777216, modes=(384,)):
    path = Path(path)
    parents(path)
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and (before.st_nlink == 1) and (not before.st_mode & 18) and (modes is None or stat.S_IMODE(before.st_mode) in modes) and (before.st_size <= limit))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        need(identity(os.fstat(fd)) == identity(before))
        with os.fdopen(os.dup(fd), 'rb') as f:
            raw = f.read(before.st_size + 1)
        need(len(raw) == before.st_size and identity(os.fstat(fd)) == identity(before) == identity(path.lstat()))
        return raw
    finally:
        os.close(fd)

def pointer_state():
    root=Path('/opt/careertrellis/staging');current=(root/'current').resolve(strict=True);previous=(root/'previous').resolve(strict=True)
    need(current==root/'releases'/BASE and previous==root/'releases'/PREVIOUS)
    raw=read_protected(current/'backend/version.py',16384,modes=(0o640,))
    tree=ast.parse(raw);values=[]
    for node in tree.body:
        if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant) and node.value.value=='20261017.96':values.append(node.value.value)
    need(values)
    return {'current':BASE,'previous':PREVIOUS,'build':'20261017.96'}

