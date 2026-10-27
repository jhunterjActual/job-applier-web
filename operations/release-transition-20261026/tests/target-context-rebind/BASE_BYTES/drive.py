"""Primary-only fixed SSH bridge, draft not invoked. RPC bodies never logged.

Does not install/upload/fetch anything. Inputs are nonsecret exact source/plan
digests. Existing reviewed packet/plan must already be installed on both hosts.
On transport ambiguity, retain receipts/PIDs and inspect; never automatic retry.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import queue
import threading

SSH=r'C:\Windows\System32\OpenSSH\ssh.exe'
HOSTS={'web':'hunter@129.212.153.245','db':'hunter@157.245.113.244'}
REMOTE='/opt/careertrellis/staging/pr98-117-release-ops-20261026'
TARGET='56c8b137fd6c6e778a9f09e3b04b925edea6270e'

def need(value):
    if not value:raise RuntimeError('bridge_refused_inspect_no_replay')
def decode(raw):
    def pairs(items):
        result={}
        for k,v in items:need(k not in result);result[k]=v
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))

def command(role,commit,digest):
    need(role in HOSTS and re.fullmatch('[0-9a-f]{40}',commit) and re.fullmatch('[0-9a-f]{64}',digest))
    script='web_coordinator.py' if role=='web' else 'peer_server.py'
    argv=[SSH,'-T','-o','BatchMode=yes','-o','ConnectTimeout=15','-o','ForwardAgent=no',
        '-o','ClearAllForwardings=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no',
        '-o','HostKeyAlgorithms=ssh-ed25519','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2',
        HOSTS[role],'sudo','-n','/usr/bin/python3','-I','-S','-B',REMOTE+'/'+script,
        '--plan',REMOTE+'/plan.json','--approved-plan-sha256',digest,'--source-commit',commit]
    if role=='db':argv+=['--role','db']
    return argv

def start(role,commit,digest):
    # Never print stderr: provider/config diagnostics cannot enter public logs.
    return subprocess.Popen(command(role,commit,digest),stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',bufsize=1)

class Reader:
    def __init__(self,stream):
        self.items=queue.Queue(maxsize=4)
        def collect():
            try:
                while True:
                    raw=stream.readline(65537);self.items.put(raw)
                    if not raw:return
            except BaseException:self.items.put(None)
        threading.Thread(target=collect,daemon=True).start()
    def read(self):
        # This deadline ends bridge observation only. It NEVER kills/signals a
        # remote owner or treats SSH transport exit as host-terminal proof.
        try:return self.items.get(timeout=180)
        except queue.Empty:raise RuntimeError('transport_observation_unknown') from None

def drive(commit,digest,receipt):
    need(not receipt.exists())
    web=None;db=None;seq=0;terminal=False
    with receipt.open('x',encoding='utf8',newline='\n') as log:
        def record(value):
            log.write(json.dumps({'observed_utc':datetime.now(timezone.utc).isoformat(),**value})+'\n');log.flush()
        try:
            record({'phase':'intent','source_commit':commit,'plan_sha256':digest})
            web=start('web',commit,digest)
            web_reader=Reader(web.stdout);db_reader=None
            record({'phase':'started','web_transport_pid':web.pid})
            while True:
                raw=web_reader.read()
                need(raw and len(raw)<=65536 and raw.endswith('\n'))
                frame=decode(raw)
                if frame.get('kind')=='owner':
                    need(set(frame)=={'kind','role','pid','boot'} and frame['role']=='web'
                         and type(frame['pid']) is int and frame['pid']>0 and type(frame['boot']) is str)
                    record({'phase':'remote_web_owner','pid':frame['pid'],'boot':frame['boot']});continue
                if frame.get('kind')=='terminal':
                    value=frame['receipt'];need(set(frame)=={'kind','receipt'} and type(value) is dict)
                    need(value.get('result') in ('target_healthy','refused_or_failed_inspect_no_replay'))
                    if value['result']=='target_healthy':
                        need(value=={'result':'target_healthy','release':TARGET,'build':'20261026.97'})
                    else:need(set(value)=={'result'})
                    record({'phase':'terminal','result':value['result']});terminal=True;break
                need(set(frame)=={'kind','request'} and frame['kind']=='db_rpc')
                request=frame['request'];need(set(request)=={'seq','plan_sha256','action','args'}
                    and request['seq']==seq and request['plan_sha256']==digest)
                if db is None:
                    db=start('db',commit,digest);record({'phase':'db_transport_started','db_transport_pid':db.pid})
                    db_reader=Reader(db.stdout)
                # Confidential frame is forwarded in memory only. No journal,
                # print, error interpolation or command-line policy values.
                db.stdin.write(json.dumps(request,separators=(',',':'))+'\n');db.stdin.flush()
                response=db_reader.read()
                need(response and len(response)<=65536 and response.endswith('\n'))
                value=decode(response);need(value.get('seq')==seq and type(value.get('ok')) is bool)
                if request['action']=='ping' and value['ok'] is True:
                    owner=value['value'];need(set(owner)=={'pid','role','boot'} and owner['role']=='db'
                        and type(owner['pid']) is int and owner['pid']>0 and type(owner['boot']) is str)
                    record({'phase':'remote_db_owner','pid':owner['pid'],'boot':owner['boot']})
                web.stdin.write(response);web.stdin.flush();seq+=1
                record({'phase':'db_rpc_completed','sequence':seq,'ok':value['ok']})
            code=web.wait(timeout=20)
            dbcode=0
            if db is not None:
                db.stdin.close()
                # A successfully acknowledged release should exit; an ambiguous
                # claimed server intentionally holds its owner on EOF. Do not
                # kill it or label a transport exit host-terminal evidence.
                try:dbcode=db.wait(timeout=5)
                except subprocess.TimeoutExpired:dbcode=None
                record({'phase':'db_transport_observation','exit_code':dbcode})
            record({'phase':'web_transport_exit','exit_code':code})
            return 0 if code==0 and dbcode==0 and terminal and value['result']=='target_healthy' else 1
        except BaseException:
            record({'phase':'transport_or_protocol_unknown','no_replay':True})
            # Closing SSH input is notification, not a signal/kill of the remote
            # owner. Native server holds after a claimed EOF for reconciliation.
            if db is not None:
                try:db.stdin.close()
                except OSError:pass
            if web is not None:
                try:web.stdin.close()
                except OSError:pass
            return 1

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-commit',required=True)
    p.add_argument('--approved-plan-sha256',required=True);p.add_argument('--receipt',type=Path,required=True)
    a=p.parse_args();raise SystemExit(drive(a.source_commit,a.approved_plan_sha256,a.receipt))
