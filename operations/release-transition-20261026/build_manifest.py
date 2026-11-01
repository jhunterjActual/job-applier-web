"""Local-only exact operational-commit packet manifest; no install/network.

Writes a NEW manifest only after every runtime source byte matches its tracked
blob in the exact hosted-main-descended operational source commit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from peer_server import MODULES,SCRIPTS,READINESS_FILES

def need(value):
    if not value:raise RuntimeError('manifest_refused')
def git(root,*args):
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True,timeout=30)
    need(p.returncode==0);return p.stdout
def closed_json(raw):
    def pairs(items):
        value={}
        for k,v in items:need(k not in value);value[k]=v
        return value
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))

def build(root,commit,source,output):
    need(re.fullmatch('[0-9a-f]{40}',commit) is not None and not output.exists())
    need(git(root,'remote','get-url','web').strip()==b'https://github.com/jhunterjActual/job-applier-web.git')
    need(git(root,'rev-parse',commit+'^{commit}').decode().strip()==commit)
    git(root,'merge-base','--is-ancestor','web/main',commit)
    modules={};scripts={};verified_raw={}
    for name in [*(m+'.py' for m in MODULES),*SCRIPTS]:
        raw=(source/name).read_bytes();verified_raw[name]=raw
        need(raw==git(root,'cat-file','blob',commit+':operations/release-transition-20261026/'+name))
        target=modules if name[:-3] in MODULES else scripts
        target[name[:-3] if target is modules else name]=hashlib.sha256(raw).hexdigest()
    readiness={}
    for name in READINESS_FILES:
        relative='one_shot_readiness/'+name
        raw=(source/relative).read_bytes();verified_raw[relative]=raw
        need(raw==git(root,'cat-file','blob',commit+':operations/release-transition-20261026/'+relative))
        readiness[name]=hashlib.sha256(raw).hexdigest()
    transaction=closed_json(verified_raw['one_shot_readiness/transaction04-pins.json'])
    need(type(transaction) is dict and set(transaction)=={'files'})
    need(transaction['files']=={**{n+'.py':h for n,h in modules.items()},**scripts})
    nested=closed_json(verified_raw['one_shot_readiness/SOURCE_MANIFEST.json'])
    need(set(nested)=={'schema','files'} and nested['schema']=='careertrellis.one-shot-readiness-source/v1')
    need(nested['files']=={n:h for n,h in readiness.items() if n!='SOURCE_MANIFEST.json'})
    value={'schema':'careertrellis.app-release-packet/v1','source_commit':commit,'modules':modules,'scripts':scripts,'readiness':readiness}
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    need(all((source/name).read_bytes()==blob for name,blob in verified_raw.items()))
    with output.open('xb') as f:f.write(raw)
    return {'packet_sha256':hashlib.sha256(raw).hexdigest(),'source_commit':commit,'files':len(modules)+len(scripts)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--source-commit',required=True);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print(json.dumps(build(a.repository,a.source_commit,a.source,a.output)))
