"""Exact-packet web entrypoint. Draft: no host invocation authorized yet."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types

def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',required=True)
    p.add_argument('--approved-plan-sha256',required=True);p.add_argument('--source-commit',required=True)
    a=p.parse_args()
    if not (sys.platform=='linux' and os.geteuid()==0 and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise RuntimeError('bootstrap_refused')
    folder=Path(__file__).resolve().parent
    # First bootstrap bytes must themselves match the exact tracked ops commit.
    path=folder/'peer_server.py';s=path.lstat()
    if not (s.st_uid==0 and s.st_nlink==1 and s.st_mode&0o777==0o600 and not path.is_symlink()):
        raise RuntimeError('bootstrap_refused')
    raw=path.read_bytes()
    result=subprocess.run(['/usr/bin/git','-C','/srv/careertrellis/job-applier-web','cat-file','blob',
        a.source_commit+':operations/release-transition-20261026/peer_server.py'],capture_output=True,timeout=15,
        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
    if result.returncode or raw!=result.stdout:raise RuntimeError('bootstrap_refused')
    peer=types.ModuleType('peer_server');peer.__file__=str(path);sys.modules['peer_server']=peer
    exec(compile(raw,str(path),'exec'),peer.__dict__)
    native,engine=peer.load_packet(folder,a.source_commit)
    raw=peer.protected(Path(a.plan),0o600);plan=peer.decode(raw)
    engine.need(engine.digest(plan)==a.approved_plan_sha256)
    engine.need(hashlib.sha256(peer.protected(folder/'packet_manifest.json',0o600)).hexdigest()==plan['packet_sha256'])
    backend=sys.modules['native_backend'].Backend(plan,folder)
    print(json.dumps({'kind':'owner','role':'web','pid':os.getpid(),'boot':backend.web.boot},separators=(',',':')),flush=True)
    with sys.modules['canonical_primitives'].controlled_signals():
        value=engine.execute(backend,plan,approved_digest=a.approved_plan_sha256)
    print(json.dumps({'kind':'terminal','receipt':value},separators=(',',':')),flush=True)

if __name__=='__main__':
    try:main()
    except BaseException:
        print('{"kind":"terminal","receipt":{"result":"refused_or_failed_inspect_no_replay"}}',flush=True)
        raise SystemExit(1)
