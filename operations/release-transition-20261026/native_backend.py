"""Concrete web coordinator adapter; staged draft, not host-authorized.

Uses verified canonical app-only source/authority/service/probe primitives and
new local/native persistent DB policy participants. No generic deploy activation.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import select
import stat
import subprocess
import sys
import time
import canonical_primitives as legacy
from native_peer import NativePeer,PRIVATE,PUBLIC,INTERLOCK,STATE,JOURNAL,decode
from release_transaction import BASE,PREVIOUS,TARGET,canonical,digest,need,timestamp,GlobalTerminalUnknown

PREPARED=Path('/var/backups/careertrellis/app-release-preparation-20261026')
FRESH=Path('/var/backups/careertrellis/app-release-proof-20261026.json')

class DBProxy:
    def __init__(self,plan):self.plan=plan;self.seq=0;self.claimed=False;self.terminal=None;self.unknown=False
    def call(self,action,_deadline=None,**args):
        need(not self.unknown)
        value={'seq':self.seq,'plan_sha256':digest(self.plan),'action':action,'args':args}
        deadline=time.monotonic()+120 if _deadline is None else _deadline
        try:
            need(deadline>time.monotonic())
            frame=(json.dumps({'kind':'db_rpc','request':value},separators=(',',':'),ensure_ascii=True)+'\n').encode('ascii')
            need(len(frame)<=3145728)
            offset=0
            while offset<len(frame):
                count=sys.stdout.buffer.write(frame[offset:])
                need(type(count) is int and 0<count<=len(frame)-offset)
                offset+=count
            sys.stdout.buffer.flush()
            raw=b'';fd=sys.stdin.fileno()
            while b'\n' not in raw:
                remaining=deadline-time.monotonic();need(remaining>0)
                need(bool(select.select([fd],[],[],remaining)[0]))
                chunk=os.read(fd,3145729-len(raw));need(bool(chunk));raw+=chunk;need(len(raw)<=3145728)
            need(raw.isascii() and b'\r' not in raw and raw.endswith(b'\n') and raw.count(b'\n')==1)
            response=decode(raw);need(set(response)=={'seq','ok','value'}
                and response['seq']==self.seq and response['ok'] is True)
        except BaseException:
            self.unknown=True
            raise
        self.seq+=1;return response['value']

class Backend(legacy.LinuxHost):
    def __init__(self,plan,packet):
        self.plan=plan;self.packet=Path(packet);self.web=NativePeer('web',plan)
        self.db=DBProxy(plan);self.saved=None;self.states=None;self.policies=None
        self.release_authorized=None;self.finishes={}
        self.completion_digests={};self.recovery_record=None
        self.release_progress={'db':'not_attempted','web':'not_attempted','pilot':'not_attempted'}
        self.pilot_identity=None
        self.terminal_readiness_binding=None;self.terminal_readiness=None;self.release_decision=None
    def runtime_digest(self,path):
        # Installed dependency binaries may exceed the legacy protected-read
        # allocation cap. Stream only this public runtime inventory, retaining
        # strict root/path/inode checks and a fixed per-file upper bound.
        path=Path(path)
        for parent in path.parents:
            s=parent.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022)
        before=path.lstat();need(stat.S_ISREG(before.st_mode) and before.st_uid==0
            and before.st_nlink==1 and not before.st_mode&0o022 and before.st_size<=512*1024**2)
        fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
        identity=lambda s:tuple(getattr(s,k) for k in fields)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);h=hashlib.sha256();size=0
        try:
            need(identity(os.fstat(fd))==identity(before))
            with os.fdopen(fd,'rb',closefd=False) as stream:
                while True:
                    block=stream.read(1024**2)
                    if not block:break
                    size+=len(block);need(size<=before.st_size);h.update(block)
            need(size==before.st_size and identity(os.fstat(fd))==identity(before)
                 and identity(path.lstat())==identity(before))
            return h.hexdigest()
        finally:os.close(fd)
    @contextmanager
    def web_locks(self):
        self.web.acquire()
        pilot=Path('/run/lock/careertrellis-pilot-auth-acceptance.lock')
        self.web.store.parent(pilot);info=pilot.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and not info.st_mode&0o022)
        pilot_fd=os.open(pilot,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
        opened=os.fstat(pilot_fd);need((opened.st_dev,opened.st_ino)==(info.st_dev,info.st_ino))
        fcntl.flock(pilot_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.pilot_identity={'fd':pilot_fd,'path':str(pilot),'dev':info.st_dev,'ino':info.st_ino}
        try:yield
        finally:
            retain=False
            if self.release_authorized:
                try:
                    # B: authenticated DB ACK + immutable decision, NOT a new TTL
                    # predicate after DB's confirmed first irreversible unlock.
                    import readiness_bridge as bridge
                    bridge.validate_db_release_ack(self.web.db_release_ack,self.release_decision)
                    self.release_progress['web']='attempted_outcome_unknown'
                    ack=self.web.release_terminal(self.release_authorized[0])
                    need(ack['decision_sha256']==self.release_authorized[2])
                    self.release_progress['web']='all_fds_closed'
                    a=os.fstat(pilot_fd);b=pilot.lstat()
                    need((a.st_dev,a.st_ino)==(info.st_dev,info.st_ino)==(b.st_dev,b.st_ino))
                    need(all(stat.S_ISREG(v.st_mode) and v.st_uid==0 and v.st_nlink==1
                             and stat.S_IMODE(v.st_mode)==stat.S_IMODE(info.st_mode) for v in (a,b)))
                    self.web.decision_identity_guard(require_fresh=False) # main control already released; auxiliary identity only
                    self.release_progress['pilot']='attempted_outcome_unknown'
                    os.close(pilot_fd);self.release_progress['pilot']='closed'
                except BaseException:
                    retain=True;self.terminal_release_failure('web_or_pilot_release_unknown')
            elif self.web.claimed:retain=True
            else:
                for fd,*_ in reversed(self.web.fds):os.close(fd)
                self.web.fds=[];os.close(pilot_fd)
            if retain:
                self.retained_pilot_fd=pilot_fd
                from peer_server import hold
                hold(self.web)
                raise GlobalTerminalUnknown('remaining_web_ownership_retained_or_unknown')
    @contextmanager
    def db_locks(self):
        self.db.call('ping')
        try:yield
        finally:
            if self.release_authorized:
                result,revision,decision_sha=self.release_authorized
                predecessor=TARGET if revision==self.plan['correction'] else BASE
                try:
                    self.assert_terminal_readiness_current(revision,predecessor)
                    self.release_progress['db']='attempted_outcome_unknown'
                    ack=self.db.call('release',result=result)
                    import readiness_bridge as bridge
                    bridge.validate_db_release_ack(ack,self.release_decision)
                    self.web.db_release_ack=deepcopy(ack)
                    self.release_progress['db']='authenticated_all_fds_closed'
                except BaseException:
                    self.terminal_release_failure('db_release_unknown')
                    raise GlobalTerminalUnknown('db_release_unknown_web_retained') from None
            elif not self.db.claimed:self.db.call('abort_unclaimed')
            # A claimed unknown DB process is never sent release. EOF causes
            # that participant to hold descriptors; primary reconciles owner.
    def verify_packet(self,expected):
        raw=self.protected(self.packet/'packet_manifest.json')
        need(hashlib.sha256(raw).hexdigest()==expected)
        self.source_commit=decode(raw)['source_commit']
        need(self.web.source_commit==self.source_commit)
        self.saved=self.snapshot()
        for marker in legacy.MARKERS:need(not os.path.lexists(marker))
        self.source(BASE,None,self.plan['builds'][BASE]);self.authority(BASE)
        self.health(self.plan['builds'][BASE])
    def baseline(self,role):return self.web.baseline() if role=='web' else self.db.call('baseline')
    def verify_prepared(self,plan):
        from prepared_evidence import inspect_prepared
        from prepared_registries import REGISTRY98,REGISTRY97
        registry_raw=REGISTRY98;registry97_raw=REGISTRY97
        def git_blob(commit,path):
            return self.run('git','-C',legacy.REPOSITORY,'cat-file','blob',commit+':'+path)
        for revision in (TARGET,plan['correction']):
            proof=inspect_prepared(plan,revision,self.protected,git_blob,registry_raw,registry97_raw=registry97_raw)
            need(proof['historical_metadata_verified'] is True
                 and proof['requires_original_current_source_authority_runtime_guards'] is True)
            self.source(revision,None,plan['builds'][revision],plan['source_diffs'][revision])
            self.authority(revision)
            runtime=proof['runtime_map'];need(type(runtime) is dict and runtime)
            release=legacy.ROOT/'releases'/revision
            # Every venv/cache file is bound, not merely selected import files.
            actual={}
            for base in (release/'backend/venv',):
                for root,dirs,files in os.walk(base,followlinks=False):
                    for name in dirs:need(not (Path(root)/name).is_symlink())
                    for name in files:
                        path=Path(root)/name
                        actual[path.relative_to(release).as_posix()]=self.runtime_digest(path)
            for path in release.rglob('__pycache__'):
                if 'venv' in path.parts:continue
                need(not path.is_symlink())
                for leaf in path.iterdir():
                    need(leaf.is_file() and not leaf.is_symlink())
                    actual[leaf.relative_to(release).as_posix()]=self.runtime_digest(leaf)
            need(actual==runtime)
    def verify_fresh_proofs(self,plan,phase,revision,predecessor):
        # No OK-wrapper fallback. Read original fixed protected bytes and invoke
        # exact source-pinned installed health-core validators on their own hosts.
        from accepted_pair_validator import validate_pair,LABEL,TRANSACTION,METADATA,MANIFEST,FULL
        import readiness_bridge as readiness
        from peer_server import protected
        started=time.monotonic();issued=datetime.now(timezone.utc).isoformat()
        request=readiness.binding(plan,self.source_commit,secrets.token_hex(32),phase,revision,predecessor,issued)
        web_health=readiness.collect_local('web',plan,self.source_commit,request)
        web_raw=protected('/var/lib/careertrellis/paired-acceptance-support/'+TRANSACTION+'/plaintext-acceptance.json',0o600)
        originals={
            '/var/lib/careertrellis/paired-acceptance-support/'+TRANSACTION+'/object-metadata.json':METADATA,
            '/var/lib/careertrellis/paired-object-acceptance/'+TRANSACTION+'/snapshot-manifest.json':MANIFEST}
        for path,expected in originals.items():
            original=self.protected(path);need(len(original)<=4*1024**2)
            need(hashlib.sha256(original).hexdigest()==expected and type(decode(original)) is dict)
        db=self.db.call('backup_proofs',binding=request,_deadline=started+30)
        need(type(db) is dict and set(db)=={'health','receipt_hex','repository','cleanup_owner_absent'})
        need(db['cleanup_owner_absent'] is True)
        current=db['repository']
        # Retain original ANY equality, accepted full receipt SHA and actual
        # FULL binding. Do not substitute FULL for a newer ANY differential.
        readiness.verify_repository_binding(current,db['health'],LABEL,FULL)
        need(db['health']['latest_receipts']['full_backup']['receipt_sha256']==FULL)
        now=datetime.now(timezone.utc)
        age=(now-timestamp(current['checked_at'])).total_seconds();need(0<=age<=30)
        # Current-health output is source-authenticated through the fixed peer;
        # repeated checks also bind inventories to their unchanged generation.
        for role,value in (('web',web_health),('db',db['health'])):
            # All original inventory/nonempty/tie/future/latest-SHA checks
            # are proved by the source-bound fresh generation, not deleted.
            # Whole original checked_status is equal before/after and retained;
            # only exact sole DB legacy full-timer alert may qualify this route.
            readiness.verify_generation_structure(value,plan,request,role,
                readiness.load(plan,self.source_commit)['collector'],now=now)
            age=(now-timestamp(value['observed_at'])).total_seconds();need(0<=age<=30)
        validate_pair(web_raw,bytes.fromhex(db['receipt_hex']),{
            'baseline':BASE,'target':TARGET,'correction':plan['correction'],
            'accepted_backup_label':LABEL,'checked_at':current['checked_at']},now=now)
        readiness.verify_generation(web_health,plan,request,'web',readiness.load(plan,self.source_commit)['collector'],now=datetime.now(timezone.utc))
        db_ready=db['health']
        if db_ready['result']!='ok':
            qualified=self.db.call('qualify_backup_proofs',binding=request,web_health=web_health,_deadline=started+30)
            need(type(qualified) is dict and set(qualified)=={'health'})
            db_ready=qualified['health']
        self.last_readiness=readiness.admit(web_health,db_ready,plan,self.source_commit,request,started_monotonic=started)
        self.last_readiness_binding=request
        self.last_readiness_deadline_monotonic=started+30
        self.last_generation_hashes={'web':web_health['evaluation_generation_sha256'],'db':db_ready['evaluation_generation_sha256']}
        self.assert_preserved()
    def assert_readiness_current(self,phase,revision,predecessor):
        b=self.last_readiness_binding;r=self.last_readiness;now=datetime.now(timezone.utc)
        need(b['phase']==phase and b['release']==revision and b['predecessor']==predecessor
            and b['plan_sha256']==digest(self.plan) and b['boots']==self.plan['boots']
            and b['source_commit']==self.source_commit and r['nonce']==b['nonce']
            and r['plan_sha256']==b['plan_sha256'] and r['phase']==phase
            and 0<=(now-timestamp(b['issued_at'])).total_seconds()<=30
            and now<timestamp(r['expires_at']) and time.monotonic()<self.last_readiness_deadline_monotonic)
    def assert_preserved(self):
        if self.saved is None:return
        if self.web.original is not None:self.web.assert_preserved()
        if self.db.seq>1:self.db.call('assert_preserved')
        mutable={PRIVATE,PUBLIC,INTERLOCK,STATE+'/web/status.json'}
        current=self.snapshot()
        need({k:v for k,v in self.saved.items() if k not in mutable}
             =={k:v for k,v in current.items() if k not in mutable})
    def assert_owned_state(self,states,policies,plan):
        self.web.assert_known();self.db.call('assert_known')
    def claim(self,role,plan_digest,state):
        if role=='web':self.web.claim(plan_digest)
        else:self.db.call('claim',plan_sha256=plan_digest);self.db.claimed=True
    def phase(self,role,phase,revision):
        if role=='web':self.web.create(JOURNAL+'/'+phase+'-'+revision+'.json',canonical({
            'phase':phase,'release':revision,'observed_at':datetime.now(timezone.utc).isoformat()}))
        else:self.db.call('phase',phase=phase,revision=revision)
    def block_starts(self):
        policy=self.web.policy_readback();rev=policy['application_release']
        self.web.write_marker(self.web.marker('blocked',rev))
    def assert_starts_blocked(self):
        # Run actual installed fixed gate; only its documented refusal code
        # is acceptable. No service or provider call is made by writer-gate.
        p=subprocess.run(['/usr/bin/python3','-I','-B','/usr/local/libexec/careertrellis/paired_maintenance/entry.py','writer-gate'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,
            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
        need(p.returncode==1)
    def assert_writer_gate(self,revision):
        need(self.pointer('current')==revision)
        self.run('/usr/bin/python3','-I','-B','/usr/local/libexec/careertrellis/paired_maintenance/entry.py','writer-gate',timeout=15)
    def assert_stopped(self):
        need(all(self.state(s)['ActiveState']=='inactive' for s in legacy.SERVICES))
        self.markers_clear()
    def point(self,revision,expected):
        need(self.pointer('current')==expected and revision in (TARGET,self.plan['correction']))
        super().point(revision,expected)
    def write_policies(self,role,policy):
        rev=policy['application_release']
        if role=='web':need(policy==self.web.policies[rev]);self.web.select_policy(rev)
        else:self.db.call('select_policy',revision=rev)
    def policy_readback(self,role):return self.web.policy_readback() if role=='web' else self.db.call('read_policy')
    def write_interlock(self,marker):self.web.write_marker(marker)
    def interlock_readback(self):return decode(self.web.store.read(INTERLOCK))
    def start_and_stabilize(self,revision,build):
        self.assert_writer_gate(revision);self.start(build)
        self.stable_selection=(revision,build)
        states={s:self.state(s) for s in legacy.SERVICES}
        self.stable_units={s:{k:v[k] for k in ('ActiveState','MainPID','NRestarts')}
                           for s,v in states.items()}
    def project_verifying(self,revision,build):
        need(self.stable_selection==(revision,build) and self.pointer('current')==revision)
        self.assert_preserved();self.assert_writer_gate(revision)
        states={s:self.state(s) for s in legacy.SERVICES}
        observed={s:{k:v[k] for k in ('ActiveState','MainPID','NRestarts')}
                  for s,v in states.items()}
        need(observed==self.stable_units)
        proof={'schema':'careertrellis.app-stable-services/v1','plan_sha256':digest(self.plan),
            'release':revision,'build':build,'web_boot':self.plan['boots']['web'],'units':observed}
        self.web.project_verifying(revision,proof)
        need(self.db.call('project_verifying',revision=revision,proof=proof)=={'result':'nonterminal_verifying'})
        self.assert_preserved()
    def assert_healthy(self,revision,build):
        need(self.pointer('current')==revision);self.health(build)
        result=self.run(str(legacy.ROOT/'releases'/revision/'backend/venv/bin/python'),'-I','-B',
            str(self.packet/'reviewed_probe.py'),'--revision',revision,'--stage','running',timeout=45)
        legacy.validate_probe(decode(result),{},revision,'running')
    def restore_original(self,role,state):
        if role=='web':self.web.restore()
        else:self.db.call('restore')
    def finish(self,role,revision,policy,result):
        self.assert_healthy(revision,self.plan['builds'][revision])
        if role=='web':
            self.web.finish(revision,result,True)
            receipt=self.web.completion_digest(revision,result)
        else:
            value=self.db.call('finish',revision=revision,result=result,healthy_proven=True)
            need(type(value) is dict and set(value)=={'completion_sha256'})
            receipt=value['completion_sha256'];self.db.terminal=result
        self.finishes[role]=(result,revision)
        self.completion_digests[role]=receipt
    def assert_terminal_readiness_current(self,revision,predecessor):
        need(self.terminal_readiness_binding==self.last_readiness_binding
            and self.terminal_readiness['nonce']==self.last_readiness['nonce'])
        self.assert_readiness_current('final',revision,predecessor)
        self.web.claim_window()
        need(datetime.now(timezone.utc)<timestamp(self.terminal_readiness['expires_at']))

    def terminal_release_failure(self,stage):
        # No RPC/reacquire/health refresh: DB may already admit normal policy-valid
        # backups. Only remaining WEB control can establish the actual writer deny.
        self.revoke_terminal();blocked=False;stopped=False
        main_complete=self.release_progress.get('db')=='authenticated_all_fds_closed' and self.release_progress.get('web')=='all_fds_closed'
        if not main_complete:
            try:blocked=self.web.deny_under_retained_control() is True
            except BaseException:pass
            try:self.stop();self.assert_stopped();stopped=True
            except BaseException:pass
        try:fds=self.web.remaining_fd_state()
        except BaseException:fds={'closed':list(getattr(self.web,'release_closed',[])),
            'held':[],'unknown':[{'path':str(item[1])} for item in self.web.fds]}
        pilot={'identity':deepcopy(getattr(self,'pilot_identity',None)),'state':'not_acquired'}
        if pilot['identity'] is not None:
            pilot['state']='unknown'
            try:
                p=pilot['identity'];a=os.fstat(p['fd']);b=Path(p['path']).lstat()
                need((a.st_dev,a.st_ino)==(p['dev'],p['ino'])==(b.st_dev,b.st_ino))
                key=f"{os.major(p['dev']):02x}:{os.minor(p['dev']):02x}:{p['ino']}"
                need(any(len(v:=line.split())>=6 and v[1:4]==['FLOCK','ADVISORY','WRITE']
                    and v[4]==str(os.getpid()) and v[5].lower()==key.lower()
                    for line in Path('/proc/locks').read_text().splitlines()))
                pilot['state']='held_verified'
            except BaseException:pass
        value={'result':'terminal_release_unknown','stage':stage,'no_replay':True,
            'release_progress':dict(self.release_progress),'web_fds':fds,
            'pilot_fd':pilot,
            'web_blocked_readback':blocked,'web_producers_stopped_verified':stopped,
            'containment':'committed_main_release_complete_auxiliary_unknown' if main_complete else 'verified_web_only' if blocked and stopped else 'UNCERTAIN',
            'db_admission':'may_admit_normal_backups_if_repository_closed; no both-host fence claim'}
        self.terminal_release_uncertainty=value
        try:self.web.create(JOURNAL+'/contained-inspection-required.json',canonical(value))
        except BaseException:pass # unknown existing/failed record is never overwritten
        return value
    def authorize_terminal(self,result,revision):
        need(self.finishes=={'web':(result,revision),'db':(result,revision)})
        predecessor=TARGET if revision==self.plan['correction'] else BASE
        self.verify_prepared(self.plan);self.verify_fresh_proofs(self.plan,'final',revision,predecessor)
        self.terminal_readiness_binding=deepcopy(self.last_readiness_binding)
        self.terminal_readiness=deepcopy(self.last_readiness)
        self.terminal_generations=deepcopy(self.last_generation_hashes)
        original_deadline=timestamp(self.web.intent['started_at'])+__import__('datetime').timedelta(
            seconds=min(900,self.web.original['private_policy']['planned_stop_budget_seconds']))
        self.terminal_readiness['expires_at']=min(timestamp(self.terminal_readiness['expires_at']),
            timestamp(self.plan['expires_at']),original_deadline).isoformat()
        self.assert_owned_state(None,None,self.plan)
        self.assert_preserved();self.assert_writer_gate(revision)
        self.assert_healthy(revision,self.plan['builds'][revision])
        common={'schema':'careertrellis.app-global-terminal-authorization/v1',
            'plan_sha256':digest(self.plan),'packet_sha256':self.plan['packet_sha256'],
            'source_commit':self.source_commit,'outcome':result,'selected_release':revision,
            'selected_build':self.plan['builds'][revision],
            'epoch':{'target_healthy':'target','corrected':'correction','aborted_before_switch':'baseline-abort'}[result],
            'boots':self.plan['boots'],'local_completion_sha256':dict(self.completion_digests),
            'authorization_id':secrets.token_hex(32),'authorized_at':datetime.now(timezone.utc).isoformat(),
            'recovery_epoch_sha256':digest(self.recovery_record) if self.recovery_record else None}
        expected=digest(common)
        try:
            self.assert_terminal_readiness_current(revision,predecessor) # FIRST prepared mutation
            need(self.web.authorize_global(common)==expected)
            self.assert_terminal_readiness_current(revision,predecessor)
            need(self.db.call('global_terminal',record=common)=={'sha256':expected})
            need(hashlib.sha256(self.web.read_global(common)).hexdigest()==expected)
            need(self.db.call('global_readback',record=common)=={'sha256':expected})
            self.assert_preserved();self.assert_healthy(revision,self.plan['builds'][revision])
            self.assert_terminal_readiness_current(revision,predecessor)
            reply=self.db.call('commit_release_decision',prepared=common,binding=self.terminal_readiness_binding,
                generations=self.terminal_generations,expires_at=self.terminal_readiness['expires_at'])
            need(set(reply)=={'decision','sha256'} and reply['sha256']==digest(reply['decision']))
            self.web.accept_release_decision(reply['decision'],common,self.terminal_readiness_binding,
                self.terminal_generations,self.terminal_readiness['expires_at'])
            self.release_decision=deepcopy(reply['decision'])
            self.terminal_readiness['expires_at']=reply['decision']['readiness_expires_at'] # narrowing only
            self.assert_terminal_readiness_current(revision,predecessor) # BEFORE memory authority
        except BaseException:
            self.terminal_release_failure('prepare_or_decision_unknown')
            raise GlobalTerminalUnknown('release_decision_unknown_no_unlock') from None
        self.release_authorized=(result,revision,digest(self.release_decision))
    def begin_recovery(self):
        self.revoke_terminal()
        record={'schema':'careertrellis.app-recovery-epoch/v1','plan_sha256':digest(self.plan),
            'observed_at':datetime.now(timezone.utc).isoformat(),'predecessor':self.pointer('current')}
        expected=digest(record)
        need(self.web.recovery_started(record)==expected)
        need(self.db.call('recovery_started',record=record)=={'sha256':expected})
        self.recovery_record=record
    def revoke_terminal(self):self.release_authorized=None
    @contextmanager
    def recovery_signals(self):
        previous=legacy._RECOVERING;legacy._RECOVERING=True
        try:yield
        finally:legacy._RECOVERING=previous
    def finish_original(self,role,state):
        self.finish(role,BASE,state['private_policy'],'aborted_before_switch')
    def contain(self):
        # Caller must inspect if durable deny or stop is ambiguous; no success
        # receipt is fabricated. Peer descriptors remain held without terminal.
        try:self.block_starts();self.assert_starts_blocked()
        finally:super().contain()
    def record_containment(self):
        self.web.create(JOURNAL+'/contained-inspection-required.json',canonical({'result':'inspection_required','no_replay':True}))
    def audit(self,stage,plan):
        nonce=secrets.token_hex(32);since=datetime.now(timezone.utc)
        value=self.db.call('audit',nonce=nonce,stage=stage)
        legacy.validate_audit(value,{'target':TARGET,'recovery':plan['correction']},nonce,since)
        if stage!='preflight':self.assert_stopped()
