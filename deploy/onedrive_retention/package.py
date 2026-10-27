"""Build an exact public operational packet without reading provider secrets.

This module prepares bytes only. An immutable packet is not installation,
acceptance, timer activation, or permission to replace a shared package.
"""
import ast
import hashlib
import json
from pathlib import Path
import re
import stat


class PacketRefused(ValueError):pass


def need(value):
    if not value:raise PacketRefused('retention_packet_refused')


def canonical(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('ascii')


def digest(raw):return hashlib.sha256(raw).hexdigest()


def file_set(raw, name='FILES'):
    """Read a literal/frozenset source inventory without importing it."""
    tree=ast.parse(raw)
    values=[]
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets):
            value=node.value
            if isinstance(value,ast.Call):
                need(isinstance(value.func,ast.Name) and value.func.id=='frozenset'
                     and len(value.args)==1 and not value.keywords)
                value=value.args[0]
            values.append(ast.literal_eval(value))
    need(len(values)==1 and type(values[0]) in (tuple,list,set,frozenset))
    result=frozenset(values[0])
    need(len(result)==len(values[0]) and 0<len(result)<=64
         and all(type(n) is str and re.fullmatch('[A-Za-z_][A-Za-z0-9_]*\\.py',n) for n in result))
    return result


def source(path):
    path=Path(path); before=path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<=before.st_size<=2*1024**2)
    raw=path.read_bytes(); after=path.lstat()
    identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    need(identity(before)==identity(after) and len(raw)==before.st_size and b'\r' not in raw)
    ast.parse(raw)
    return raw


def build(repository, revision):
    need(type(revision) is str and re.fullmatch('[0-9a-f]{40}',revision) and revision!='0'*40)
    root=Path(repository)/'deploy'; sections={}; manifests={}
    for package,schema in (('onedrive_retention','careertrellis.onedrive-retention-install/v1'),
                           ('paired_maintenance','careertrellis.paired-maintenance-install/v1')):
        folder=root/package
        names=file_set(source(folder/'entry.py'))
        files={name:source(folder/name) for name in sorted(names)}
        need(sum(map(len,files.values()))<=64*1024**2)
        manifest={'schema':schema,'revision':revision,
                  'files':{name:digest(raw) for name,raw in files.items()}}
        sections[package]=files; manifests[package]=canonical(manifest)
    return sections,manifests


def write(repository, revision, destination):
    """Exclusive directory and files; partial evidence is retained on failure."""
    sections,manifests=build(repository,revision)
    root=Path(destination); root.mkdir(mode=0o700)
    for package,files in sections.items():
        folder=root/package; folder.mkdir(mode=0o755)
        for name,raw in dict(files,**{'manifest.json':manifests[package]}).items():
            with (folder/name).open('xb') as stream:stream.write(raw)
    receipt={'schema':'careertrellis.retention-source-packet/v1','revision':revision,
             'manifests':{name:digest(raw) for name,raw in manifests.items()},
             'installation_performed':False,'native_acceptance':False}
    with (root/'source-packet.json').open('xb') as stream:stream.write(canonical(receipt))
    return receipt
