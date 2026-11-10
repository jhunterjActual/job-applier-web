"""Native local participant primitives. No standalone activation entrypoint.

The persistent transport owns this participant's locks. EOF is not a terminal
acknowledgment and must leave the owner alive; driver implementation is separate.
No installed helper, unit, timer, credential or provider operation is modified.
"""
from contextlib import ExitStack
from datetime import datetime,timezone,timedelta
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import socket
import stat
import time
from types import SimpleNamespace
import uuid
from protected_state import ProtectedState
from release_transaction import BASE,BUILDS,canonical,digest,need,new_policy,STATUS_KEYS
from terminal_validation import terminal,prior_intents
from policy_encoding import policy_canonical,policy_digest

STATE='/var/lib/careertrellis/paired-maintenance'
PACKAGE='/usr/local/libexec/careertrellis/paired_maintenance'
PRIVATE='/etc/careertrellis/paired-maintenance/policy.json'
PUBLIC=STATE+'/public-policy.json'
INTERLOCK=STATE+'/writer-interlock.json'
MANIFEST_SHA='7a5122cb39b34137d9b2539d0bf60c0e3720e2854ebe8dcc30f1f1b18d092fd5'
HOSTS={'web':'careertrellis-staging-web-1','db':'job-applier-db-staging-01'}
JOURNAL='/var/backups/careertrellis/app-release-transition-20261026'
VERIFIED_SOURCE_COMMIT=None

def decode(raw):
    def pairs(values):
        d={}
        for k,v in values:need(k not in d);d[k]=v
        return d
    return json.loads(raw,object_pairs_hook=pairs,
                      parse_constant=lambda _:need(False))


class NativePeer:
    def __init__(self,role,plan):
        need(role in HOSTS and os.geteuid()==0 and socket.gethostname()==HOSTS[role])
        self.role=role;self.plan=plan;self.stack=ExitStack();self.fds=[]
        self.boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        need(self.boot==plan['boots'][role])
        self.status=STATE+'/'+role+'/status.json'
        self.store=ProtectedState({PRIVATE:0o600,PUBLIC:0o644,self.status:0o600,
                                  **({INTERLOCK:0o644} if role=='web' else {})})
        self.original=None;self.selected=None;self.claimed=False;self.released=False
        self.source_commit=VERIFIED_SOURCE_COMMIT;self.global_certificate=None;self.recovery_record=None
        self.release_decision=None;self.release_started=False;self.release_expected=[];self.release_closed=[];self.acquired_fd_identities=[]
        self.final_readiness=None;self.final_readiness_deadline=None;self.db_release_ack=None
    def acquire(self):
        names=[(STATE+'/'+self.role+'/control.lock',0o600),
               ('/run/lock/careertrellis-maintenance.lock',0o644 if self.role=='web' else 0o600),
               ('/run/lock/'+('careertrellis-generated-object-recovery.lock' if self.role=='web'
                             else 'careertrellis-pgbackrest-repository.lock'),0o600)]
        for name,mode in names:
            p=Path(name);self.store.parent(p);s=p.lstat()
            need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1
                 and stat.S_IMODE(s.st_mode)==mode)
            fd=os.open(p,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
            self.fds.append((fd,p,s.st_dev,s.st_ino))
            a=os.fstat(fd);need((a.st_dev,a.st_ino)==(s.st_dev,s.st_ino))
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.acquired_fd_identities=[{'fd':fd,'path':str(p),'dev':dev,'ino':ino} for fd,p,dev,ino in self.fds]
        return self
    def check_locks(self):
        need(len(self.fds)==3)
        for fd,p,dev,ino in self.fds:
            a=os.fstat(fd);b=p.lstat();need((a.st_dev,a.st_ino)==(dev,ino)==(b.st_dev,b.st_ino))
    def release_terminal(self,ack):
        need(ack in ('target_healthy','corrected','aborted_before_switch') and self.terminal_receipt==ack)
        self.check_locks();need(self.release_decision is not None and self.global_certificate['outcome']==ack)
        if self.role=='web':
            import readiness_bridge as bridge
            bridge.validate_db_release_ack(self.db_release_ack,self.release_decision)
        self.release_expected=list(self.fds);self.release_closed=[]
        for item in reversed(self.release_expected):
            self.check_release_remaining()
            # Before FIRST DB close only: exact original proof+claim clocks. Once a
            # first close succeeds, TTL alone cannot undo the durable decision.
            self.decision_guard(require_fresh=self.role=='db' and not self.release_started)
            fd,p,dev,ino=item
            os.close(fd)
            self.release_closed.append({'fd':fd,'path':str(p),'dev':dev,'ino':ino})
            self.fds.remove(item);self.release_started=True
            self.check_release_remaining()
        self.released=True
        return {'role':self.role,'result':ack,'decision_sha256':digest(self.release_decision),
                'closed_fds':list(self.release_closed)}
    def check_release_remaining(self):
        original=[{'fd':fd,'path':str(p),'dev':dev,'ino':ino} for fd,p,dev,ino in self.release_expected]
        need(original==self.acquired_fd_identities and len(original)==3
            and len({v['fd'] for v in original})==3 and len({v['path'] for v in original})==3
            and len({(v['dev'],v['ino']) for v in original})==3)
        expected_paths=[STATE+'/'+self.role+'/control.lock','/run/lock/careertrellis-maintenance.lock',
            '/run/lock/'+('careertrellis-pgbackrest-repository.lock' if self.role=='db' else 'careertrellis-generated-object-recovery.lock')]
        need([v['path'] for v in original]==expected_paths)
        if self.role=='db':need(original==self.release_decision['db_fd_identities'])
        count=len(self.release_closed);need(0<=count<=3)
        need(self.release_closed==list(reversed(original))[:count])
        remaining=[{'fd':fd,'path':str(p),'dev':dev,'ino':ino} for fd,p,dev,ino in self.fds]
        need(remaining==original[:3-count]) # exact ordered partition; control LAST
        for fd,p,dev,ino in self.fds:
            a=os.fstat(fd);b=p.lstat();need((a.st_dev,a.st_ino)==(dev,ino)==(b.st_dev,b.st_ino))
            mode=0o644 if self.role=='web' and str(p)=='/run/lock/careertrellis-maintenance.lock' else 0o600
            need(stat.S_ISREG(a.st_mode) and stat.S_ISREG(b.st_mode) and a.st_uid==b.st_uid==0
                and a.st_nlink==b.st_nlink==1 and stat.S_IMODE(a.st_mode)==stat.S_IMODE(b.st_mode)==mode)
        state=self.remaining_fd_state();need(not state['unknown'] and len(state['held'])==len(self.fds))

    def claim_window(self):
        need(self.store.read(JOURNAL+'/intent.json')==canonical(self.intent)
            and self.intent['boot']==self.boot and self.intent['pid']==os.getpid()
            and Path('/proc/sys/kernel/random/boot_id').read_text().strip()==self.boot)
        budget=min(900,self.original['private_policy']['planned_stop_budget_seconds'])
        started=datetime.fromisoformat(self.intent['started_at']);now=datetime.now(timezone.utc)
        need(started.tzinfo is not None and 0<=(now-started).total_seconds()<budget
            and now<datetime.fromisoformat(self.intent['deadline'])
            and 0<=time.monotonic_ns()//1000-self.intent['started_monotonic_us']<budget*1000000)

    def decision_path(self,value):
        need(re.fullmatch('[0-9a-f]{64}',value['authorization_id']))
        return JOURNAL+'/release-decision-'+value['authorization_id']+'.json'

    def commit_release_decision(self,prepared,binding,generations,expires_at):
        import readiness_bridge as bridge
        need(self.role=='db' and self.release_decision is None)
        self.assert_known();self.claim_window()
        need(self.global_certificate==prepared and self.read_global(prepared)==canonical(prepared))
        need(self.final_readiness is not None and self.final_readiness['binding']==binding
             and self.final_readiness['evaluation_generation_sha256']==generations['db'])
        bridge.verify_generation(self.final_readiness,self.plan,binding,'db',
            bridge.load(self.plan,self.source_commit)['collector'],now=datetime.now(timezone.utc))
        original_deadline=datetime.fromisoformat(self.intent['started_at'])+timedelta(
            seconds=min(900,self.original['private_policy']['planned_stop_budget_seconds']))
        expires_at=min(bridge.release_stamp(expires_at),original_deadline).isoformat()
        bridge.validate_final_binding(self.plan,binding,prepared,self.source_commit,expires_at,
            now=datetime.now(timezone.utc),require_fresh=True)
        need(time.monotonic()<self.final_readiness_deadline)
        value={'schema':'careertrellis.app-release-decision/v1','authorization_id':prepared['authorization_id'],
            'prepared_certificate_sha256':digest(prepared),'plan_sha256':digest(self.plan),
            'packet_sha256':self.plan['packet_sha256'],'source_commit':self.source_commit,
            'outcome':prepared['outcome'],'selected_release':prepared['selected_release'],
            'selected_build':prepared['selected_build'],'boots':self.plan['boots'],
            'readiness_nonce':binding['nonce'],'readiness_binding_sha256':digest(binding),
            'readiness_issued_at':binding['issued_at'],'readiness_expires_at':expires_at,
            'generation_sha256':dict(generations),'db_intent_sha256':digest(self.intent),
            'db_fd_identities':[{'fd':fd,'path':str(p),'dev':dev,'ino':ino} for fd,p,dev,ino in self.fds],
            'committed_at':datetime.now(timezone.utc).isoformat()}
        bridge.validate_decision(value,prepared,self.plan,binding,generations,self.source_commit,expires_at)
        self.create(self.decision_path(value),canonical(value)) # O_EXCL+file/dir fsync
        self.store.allowed[self.decision_path(value)]=0o600
        need(self.store.read(self.decision_path(value))==canonical(value))
        # An existing file or lost write/fsync ACK is NOT replay authority.
        self.claim_window()
        bridge.validate_final_binding(self.plan,binding,prepared,self.source_commit,expires_at,
            now=datetime.now(timezone.utc),require_fresh=True)
        need(time.monotonic()<self.final_readiness_deadline)
        self.release_decision=value
        return {'decision':value,'sha256':digest(value)}

    def accept_release_decision(self,value,prepared,binding,generations,maximum_expiry):
        import readiness_bridge as bridge
        need(self.role=='web' and self.global_certificate==prepared and self.release_decision is None)
        expiry=value['readiness_expires_at'];need(bridge.release_stamp(expiry)<=bridge.release_stamp(maximum_expiry))
        bridge.validate_decision(value,prepared,self.plan,binding,generations,self.source_commit,expiry)
        self.release_decision=decode(canonical(value))

    def selected_release_state(self):
        need(self.release_decision is not None)
        revision=self.release_decision['selected_release'];need(revision in self.policies)
        approved=policy_canonical(self.policies[revision])
        need(self.store.read(PRIVATE)==approved==self.store.read(PUBLIC))
        expected=getattr(self,'finished_status',None);need(type(expected) is bytes)
        actual=self.store.read(self.status);need(actual==expected)
        status=decode(actual)
        need(status['schema']=='careertrellis.paired-maintenance-status/v1' and status['release']==revision
             and status['policy_sha256']==policy_digest(self.policies[revision]) and status['boot_id']==self.boot
             and status['phase']=='idle' and status['result']=='ok' and status['services_restored'] is True)
        if self.role=='web':
            need(self.store.read(INTERLOCK)==canonical(self.marker('idle',revision)))
            need(os.readlink('/opt/careertrellis/staging/current')=='/opt/careertrellis/staging/releases/'+revision)

    def decision_guard(self,require_fresh=False):
        # No len(fds)==3 requirement: remaining owned control makes exact local
        # reads valid during partial completion. No WEB query from DB.
        self.selected_release_state()
        self.decision_identity_guard(require_fresh=require_fresh)
    def decision_identity_guard(self,require_fresh=False):
        import readiness_bridge as bridge
        need(self.release_decision is not None and self.global_certificate is not None
             and self.read_global(self.global_certificate)==canonical(self.global_certificate)
             and self.release_decision['prepared_certificate_sha256']==digest(self.global_certificate)
             and self.release_decision['plan_sha256']==digest(self.plan)
             and self.release_decision['source_commit']==self.source_commit
             and self.release_decision['boots']==self.plan['boots']
             and self.release_decision['boots'][self.role]==self.boot
             and Path('/proc/sys/kernel/random/boot_id').read_text().strip()==self.boot)
        need(self.store.read(JOURNAL+'/intent.json')==canonical(self.intent)
            and self.intent['pid']==os.getpid() and self.intent['boot']==self.boot
            and self.intent['plan_sha256']==digest(self.plan) and self.intent['packet_sha256']==self.plan['packet_sha256'])
        need(not os.path.lexists(JOURNAL+'/contained-inspection-required.json'))
        path=JOURNAL+'/recovery-started.json'
        if self.global_certificate['recovery_epoch_sha256'] is None:need(not os.path.lexists(path))
        else:need(digest(decode(self.store.read(path)))==self.global_certificate['recovery_epoch_sha256'])
        if self.role=='db':
            need(self.store.read(self.decision_path(self.release_decision))==canonical(self.release_decision))
            need(self.release_decision['db_intent_sha256']==digest(self.intent))
            if require_fresh:
                self.claim_window();need(time.monotonic()<self.final_readiness_deadline)
                bridge.validate_final_binding(self.plan,self.final_readiness['binding'],self.global_certificate,
                    self.source_commit,self.release_decision['readiness_expires_at'],
                    now=datetime.now(timezone.utc),require_fresh=True)
        else:bridge.validate_db_release_ack(self.db_release_ack,self.release_decision)

    def remaining_fd_state(self):
        result={'closed':list(self.release_closed),'held':[],'unknown':[]}
        rows=Path('/proc/locks').read_text().splitlines()
        for fd,p,dev,ino in self.fds:
            value={'fd':fd,'path':str(p),'dev':dev,'ino':ino}
            try:
                a=os.fstat(fd);b=p.lstat();need((a.st_dev,a.st_ino)==(dev,ino)==(b.st_dev,b.st_ino))
                key=f'{os.major(dev):02x}:{os.minor(dev):02x}:{ino}'
                need(any(len(v:=line.split())>=6 and v[1:4]==['FLOCK','ADVISORY','WRITE']
                    and v[4]==str(os.getpid()) and v[5].lower()==key.lower() for line in rows))
                result['held'].append(value)
            except BaseException:result['unknown'].append(value)
        return result

    def deny_under_retained_control(self):
        need(self.role=='web')
        state=self.remaining_fd_state();control=STATE+'/web/control.lock'
        need(any(v['path']==control for v in state['held']) and not any(v['path']==control for v in state['unknown']))
        current=self.policy_readback()
        matches=[rev for rev,value in self.policies.items() if policy_canonical(value)==policy_canonical(current)]
        need(len(matches)==1);revision=matches[0]
        observed=self.store.read(INTERLOCK)
        need(observed in (canonical(self.marker('idle',revision)),canonical(self.marker('blocked',revision))))
        blocked=canonical(self.marker('blocked',revision))
        self.store.replace(INTERLOCK,blocked,observed)
        need(self.store.read(INTERLOCK)==blocked)
        return True
    def read(self,path,mode=0o600,limit=131072):
        need(path.startswith(STATE+'/'+self.role+'/runs/') or path.startswith(STATE+'/web/slots/')
             or path.startswith(STATE+'/'+self.role+'/policy-transitions/'))
        need('..' not in Path(path).parts and len(path)<=1024)
        self.store.allowed[path]=mode
        raw=self.store.read(path);need(len(raw)<=limit);return raw
    def json(self,path,mode=0o600):return decode(self.read(path,mode))
    def path(self,path):return Path(path)
    def directory(self,path,mode):
        self.store.parent(Path(path));s=Path(path).lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==mode)
    def verify_install(self):
        probe=ProtectedState({PACKAGE+'/manifest.json':0o644})
        raw=probe.read(PACKAGE+'/manifest.json')
        need(hashlib.sha256(raw).hexdigest()==MANIFEST_SHA)
        manifest=decode(raw)
        need(set(manifest)=={'schema','revision','files'}
             and manifest['schema']=='careertrellis.paired-maintenance-install/v1')
        for name,expected in manifest['files'].items():
            need(type(name) is str and Path(name).name==name and name.endswith('.py'))
            path=PACKAGE+'/'+name;probe.allowed[path]=0o644
            need(hashlib.sha256(probe.read(path)).hexdigest()==expected)
        return manifest['revision']
    def baseline(self):
        self.check_locks();revision=self.verify_install()
        self.preserved=self.snapshot_preserved()
        policy=decode(self.store.read(PRIVATE));public=decode(self.store.read(PUBLIC))
        need(policy_canonical(policy)==policy_canonical(public) and policy_digest(policy)==self.plan['old_policy_digests'][self.role])
        need(policy['manifest_revision']==revision)
        status=decode(self.store.read(self.status))
        old=SimpleNamespace(data=policy,digest=policy_digest(policy))
        terminal(self,old,status,datetime.now(timezone.utc))
        prior_intents(self)
        marker=decode(self.store.read(INTERLOCK)) if self.role=='web' else None
        state={'boot':self.boot,'private_policy':policy,'public_policy':public,
            'status':status,'interlock':marker,'installed_verified':True,
            'terminal_verified':True,'no_unresolved_intents':not Path(JOURNAL).exists()}
        self.original=state
        self.original_bytes={p:self.store.read(p) for p in (PRIVATE,PUBLIC,self.status)}
        if self.role=='web':self.original_bytes[INTERLOCK]=self.store.read(INTERLOCK)
        self.policies={BASE:policy}
        for rev in (self.plan['target'],self.plan['correction']):
            self.policies[rev]=new_policy(policy,rev,self.plan['builds'][rev],datetime.now(timezone.utc))
            need(policy_digest(self.policies[rev])==self.plan['new_policy_digests'][rev][self.role])
        return state
    def snapshot_preserved(self):
        # Secret-derived hashes remain process memory only, never RPC/receipt.
        from canonical_primitives import LinuxHost
        reader=LinuxHost();paths=list(Path('/etc/careertrellis').rglob('*'))
        paths+=list(Path('/usr/local/sbin').glob('careertrellis-*'))
        paths+=list(Path('/usr/local/libexec/careertrellis').rglob('*'))
        for pattern in ('careertrellis-*.service','careertrellis-*.timer','careertrellis-*.service.d/*.conf'):
            paths+=list(Path('/etc/systemd/system').glob(pattern))
        mutable={PRIVATE,PUBLIC,self.status,INTERLOCK}
        return {str(p):hashlib.sha256(reader.protected(p)).digest() for p in sorted(set(paths))
            if not p.is_dir() and '__pycache__' not in p.parts and str(p) not in mutable}
    def assert_preserved(self):
        self.check_locks();need(self.preserved==self.snapshot_preserved())
    def create(self,path,raw):
        p=Path(path);self.store.parent(p)
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,'wb',closefd=False) as out:out.write(raw);out.flush();os.fsync(fd)
        finally:os.close(fd)
        self.store.sync(p.parent)
    def claim(self,plan_digest):
        self.check_locks();need(self.original is not None and plan_digest==digest(self.plan))
        p=Path(JOURNAL);self.store.parent(p)
        # Before the first potentially durable mutation; failure cannot classify
        # an ambiguous partially created claim as an unclaimed safe exit.
        self.claimed=True
        p.mkdir(mode=0o700);self.store.sync(p.parent)
        self.intent={'schema':'careertrellis.app-release-intent/v1',
            'plan_sha256':plan_digest,'role':self.role,'boot':self.boot,
            'started_at':datetime.now(timezone.utc).isoformat(),'deadline':self.plan['expires_at'],
            'started_monotonic_us':time.monotonic_ns()//1000,
            'pid':os.getpid(),'parent_pid':os.getppid(),
            'process_start_ticks':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19],
            'packet_sha256':self.plan['packet_sha256'],
            'base':BASE,'target':self.plan['target'],'correction':self.plan['correction']}
        self.create(JOURNAL+'/intent.json',canonical(self.intent))
        self.store.allowed[JOURNAL+'/intent.json']=0o600
        for number,(path,raw) in enumerate(self.original_bytes.items()):
            self.create(JOURNAL+'/original-'+str(number)+'.json',canonical({'path':path,'raw_hex':raw.hex()}))
    def assert_known(self):
        self.check_locks();need(self.claimed)
        self.assert_preserved()
        observed={}
        for path in (PRIVATE,PUBLIC):
            raw=self.store.read(path)
            allowed=[self.original_bytes[path],*[policy_canonical(self.policies[rev])
                for rev in (self.plan['target'],self.plan['correction'])]]
            need(raw in allowed)
            observed[path]=raw
        observed[self.status]=self.store.read(self.status)
        need(observed[self.status]==self.original_bytes[self.status]
             or observed[self.status]==getattr(self,'verifying_status',None)
             or observed[self.status]==getattr(self,'finished_status',None))
        if self.role=='web':
            observed[INTERLOCK]=self.store.read(INTERLOCK)
            markers=[canonical(self.marker(phase,rev)) for phase in ('idle','blocked') for rev in self.policies]
            need(observed[INTERLOCK] in [self.original_bytes[INTERLOCK],*markers])
        return observed
    def select_policy(self,revision):
        observed=self.assert_known();need(revision in self.policies)
        raw=policy_canonical(self.policies[revision])
        for path in (PRIVATE,PUBLIC):self.store.replace(path,raw,observed[path])
        need(self.store.read(PRIVATE)==raw==self.store.read(PUBLIC));self.selected=revision
    def policy_readback(self):
        a=decode(self.store.read(PRIVATE));b=decode(self.store.read(PUBLIC))
        need(policy_canonical(a)==policy_canonical(b));return a
    def marker(self,phase,revision):
        need(self.role=='web' and phase in ('idle','blocked') and revision in self.policies)
        return {'schema':'careertrellis.paired-maintenance-interlock/v1',
            'policy_sha256':policy_digest(self.policies[revision]),'boot_id':self.boot,
            'release':revision,'phase':phase}
    def write_marker(self,value):
        observed=self.assert_known();need(self.role=='web')
        allowed=[self.marker(phase,rev) for phase in ('idle','blocked') for rev in self.policies]
        current=decode(observed[INTERLOCK]);need(current in allowed and value in allowed)
        self.store.replace(INTERLOCK,canonical(value),observed[INTERLOCK])
    def restore(self):
        observed=self.assert_known()
        for path,raw in self.original_bytes.items():self.store.replace(path,raw,observed[path])
        need(all(self.store.read(p)==raw for p,raw in self.original_bytes.items()))
    def project_verifying(self,revision,proof):
        observed=self.assert_known();need(revision in self.policies)
        need(self.policy_readback()==self.policies[revision])
        intent=self.intent
        need(self.store.read(JOURNAL+'/intent.json')==canonical(intent)
             and intent['boot']==self.boot and intent['role']==self.role)
        now=datetime.now(timezone.utc);started=datetime.fromisoformat(intent['started_at'])
        budget=min(900,self.policies[revision]['planned_stop_budget_seconds'])
        need(started.tzinfo is not None and 0<=(now-started).total_seconds()<=budget
             and now<datetime.fromisoformat(intent['deadline'])
             and 0<=time.monotonic_ns()//1000-intent['started_monotonic_us']<=budget*1000000)
        services={'careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service'}
        need(type(proof) is dict and set(proof)=={'schema','plan_sha256','release','build','web_boot','units'}
             and proof['schema']=='careertrellis.app-stable-services/v1'
             and proof['plan_sha256']==digest(self.plan) and proof['release']==revision
             and proof['build']==self.plan['builds'][revision] and proof['web_boot']==self.plan['boots']['web']
             and set(proof['units'])==services)
        for value in proof['units'].values():
            need(type(value) is dict and set(value)=={'ActiveState','MainPID','NRestarts'}
                 and value['ActiveState']=='active' and value['NRestarts']=='0'
                 and type(value['MainPID']) is str and value['MainPID'].isdigit() and int(value['MainPID'])>0)
        value={k:self.original['status'][k] for k in STATUS_KEYS}
        value.update(policy_sha256=policy_digest(self.policies[revision]),release=revision,
            phase='verifying',result='running',observed_at=intent['started_at'],services_restored=True)
        self.create(JOURNAL+'/verifying-'+revision+'-intent.json',canonical({
            'schema':'careertrellis.app-verifying-projection/v1','plan_sha256':digest(self.plan),
            'release':revision,'role':self.role,'original_started_at':intent['started_at'],
            'original_started_monotonic_us':intent['started_monotonic_us'],'service_proof':proof}))
        raw=canonical(value);self.store.replace(self.status,raw,observed[self.status])
        need(self.store.read(self.status)==raw)
        self.verifying_status=raw
    def finish(self,revision,result,healthy_proven):
        observed=self.assert_known();need(healthy_proven is True and revision in self.policies)
        need(result in ('target_healthy','corrected','aborted_before_switch'))
        need(revision=={'target_healthy':self.plan['target'],'corrected':self.plan['correction'],
                        'aborted_before_switch':BASE}[result])
        need(self.policy_readback()==self.policies[revision])
        value={k:self.original['status'][k] for k in STATUS_KEYS}
        value.update(policy_sha256=policy_digest(self.policies[revision]),release=revision,phase='idle',
            result='ok',observed_at=datetime.now(timezone.utc).isoformat(),services_restored=True)
        stem=JOURNAL+'/completion-'+revision+'-'+result
        self.create(stem+'-intent.json',canonical({'result':result,'release':revision,'role':self.role}))
        raw=canonical(value);self.store.replace(self.status,raw,observed[self.status]);self.finished_status=raw
        self.create(stem+'-success.json',canonical({'result':result,'release':revision,
            'role':self.role,'plan_sha256':digest(self.plan),'services_restored':True}))
        self.terminal_receipt=result
    def completion_digest(self,revision,result):
        path=JOURNAL+'/completion-'+revision+'-'+result+'-success.json'
        self.store.allowed[path]=0o600;raw=self.store.read(path);value=decode(raw)
        need(value=={'result':result,'release':revision,'role':self.role,
            'plan_sha256':digest(self.plan),'services_restored':True})
        return hashlib.sha256(raw).hexdigest()
    def recovery_started(self,value):
        need(self.release_decision is None and not self.release_started)
        self.assert_known()
        need(set(value)=={'schema','plan_sha256','observed_at','predecessor'}
             and value['schema']=='careertrellis.app-recovery-epoch/v1'
             and value['plan_sha256']==digest(self.plan)
             and value['predecessor'] in (BASE,self.plan['target']))
        self.create(JOURNAL+'/recovery-started.json',canonical(value))
        self.store.allowed[JOURNAL+'/recovery-started.json']=0o600
        need(self.store.read(JOURNAL+'/recovery-started.json')==canonical(value))
        self.recovery_record=value;self.global_certificate=None
        return hashlib.sha256(canonical(value)).hexdigest()
    def global_path(self,value):
        need((value['epoch'],value['outcome'],value['selected_release']) in (
            ('target','target_healthy',self.plan['target']),
            ('correction','corrected',self.plan['correction']),('baseline-abort','aborted_before_switch',BASE)))
        return JOURNAL+'/global-terminal-'+value['epoch']+'-'+value['selected_release']+'-'+value['outcome']+'.json'
    def authorize_global(self,value):
        self.assert_known()
        need(type(value) is dict and set(value)=={'schema','plan_sha256','packet_sha256','source_commit',
            'outcome','selected_release','selected_build','epoch','boots','local_completion_sha256',
            'authorization_id','authorized_at','recovery_epoch_sha256'})
        need(value['schema']=='careertrellis.app-global-terminal-authorization/v1'
             and value['plan_sha256']==digest(self.plan) and value['packet_sha256']==self.plan['packet_sha256']
             and self.source_commit is not None and value['source_commit']==self.source_commit)
        expected={'target_healthy':('target',self.plan['target']),
            'corrected':('correction',self.plan['correction']),'aborted_before_switch':('baseline-abort',BASE)}
        need(value['outcome'] in expected)
        epoch,revision=expected[value['outcome']]
        need((value['epoch'],value['selected_release'])==(epoch,revision)
             and value['selected_build']==self.plan['builds'][revision]
             and value['boots']==self.plan['boots']
             and self.terminal_receipt==value['outcome'])
        need(set(value['local_completion_sha256'])=={'web','db'}
             and all(type(x) is str and re.fullmatch('[0-9a-f]{64}',x) for x in value['local_completion_sha256'].values())
             and value['local_completion_sha256'][self.role]==self.completion_digest(revision,value['outcome']))
        need(type(value['authorization_id']) is str and re.fullmatch('[0-9a-f]{64}',value['authorization_id']))
        stamp=datetime.fromisoformat(value['authorized_at']);need(stamp.tzinfo is not None)
        need(0<=(datetime.now(timezone.utc)-stamp).total_seconds()<=30)
        recovery=getattr(self,'recovery_record',None)
        need(value['recovery_epoch_sha256']==(hashlib.sha256(canonical(recovery)).hexdigest() if recovery else None))
        need((recovery is None)==(value['outcome']=='target_healthy'))
        need(self.policy_readback()==self.policies[revision])
        self.create(self.global_path(value),canonical(value))
        need(self.read_global(value)==canonical(value))
        self.global_certificate=value
        return hashlib.sha256(canonical(value)).hexdigest()
    def read_global(self,value):
        path=self.global_path(value);self.store.allowed[path]=0o600
        raw=self.store.read(path);need(raw==canonical(decode(raw)))
        return raw
