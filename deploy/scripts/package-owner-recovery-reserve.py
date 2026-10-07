"""Local source-only archive builder from exact Git revisions; no installation.

Usage: python package-owner-recovery-reserve.py <wrapper-commit> <new-output.tar>
The exact bbbc library closure is hashed from Git, never the working tree.
Output is exclusive; no overwrite/retry, dependencies, deployment or credentials.
"""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile

OPS = 'bbbc4298b2ca80df107e64d2673d6c1f243c5e41'
MODULES = ('owner_recovery_reserve', 'owner_recovery_reserve_host', 'owner_recovery_reserve_cli',
    'owner_recovery_reserve_relay', 'owner_recovery_reserve_operator')
REPOSITORY = Path(__file__).resolve().parents[2]
BASE = '/opt/careertrellis/owner-recovery-reserve/'


def require(value):
    if not value: raise ValueError('Reserve source packaging refused.')


def git(*args):
    return subprocess.check_output(['git', '-C', str(REPOSITORY), *args], stderr=subprocess.DEVNULL)


def sources(commit, *, read=git):
    require(type(commit) is str and re.fullmatch('[0-9a-f]{40}', commit) and commit != OPS)
    require(read('rev-parse', commit).strip().decode() == commit)
    require(read('rev-parse', OPS).strip().decode() == OPS)
    files = {'backend/'+name+'.py': read('show', commit+':backend/'+name+'.py') for name in MODULES}
    names = read('ls-tree', '-r', '--name-only', OPS, '--', 'backend', 'deploy/systemd').decode().splitlines()
    # All flat backend modules cover dynamic imports without guessing a smaller
    # closure. No bytecode, data, environments or runtime binaries are packaged.
    library = {name: hashlib.sha256(read('show', OPS+':'+name)).hexdigest() for name in names
        if (re.fullmatch(r'backend/[a-z0-9_]+\.py', name) and not Path(name).name.startswith('test_'))
        or re.fullmatch(r'deploy/systemd/[A-Za-z0-9_.@/-]+', name)}
    require('backend/owner_recovery.py' in library and 'backend/identity_plan_store.py' in library)
    require(all(name not in library for name in files))
    manifest = {'version':1, 'operations':OPS,
        'wrapper':{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}, 'library':library}
    files['source-manifest.json'] = json.dumps(manifest, sort_keys=True, separators=(',',':')).encode()+b'\n'
    python = '/opt/careertrellis/staging/releases/'+OPS+'/backend/venv/bin/python'
    files['careertrellis-owner-recovery-reserve'] = ('#!/bin/sh\nset -eu\nexec /usr/bin/env -i '
        'PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 CAREERTRELLIS_CONFIG_READ_ONLY=1 '
        'PYTHONDONTWRITEBYTECODE=1 '+python+' -I -B '+BASE+commit+
        '/backend/owner_recovery_reserve_cli.py "$@"\n').encode()
    return files


def build(commit, output):
    output = Path(output)
    require(output.suffix == '.tar' and output.parent.is_dir())
    files = sources(commit)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as bundle:
        for name, raw in sorted(files.items()):
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o600
            info.uid=info.gid=0;info.mtime=0
            bundle.addfile(info, io.BytesIO(raw))
    with output.open('xb') as stream: stream.write(archive.getvalue())
    return {'outcome':'reserve_source_archive_created_not_installed',
        'sha256':hashlib.sha256(archive.getvalue()).hexdigest()}


if __name__=='__main__':
    try:
        require(len(sys.argv)==3)
        print(json.dumps(build(sys.argv[1],sys.argv[2]),sort_keys=True))
    except BaseException:
        print('Reserve source packaging refused; preserve existing artifacts.',file=sys.stderr)
        raise SystemExit(1)
