"""WEB-led retention stages. A lost acknowledgement never permits redispatch.

Native installation/manifest validation and DB endpoint routing are supplied by
the separately installed fixed entry point. No application writers are stopped.
"""
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import time

from . import bundle, hydrate

RELEASE = "7a4d3f077c4da21f2a315cbbcf4fd926d91151f4"
EXPIRY = dt.datetime(2027, 1, 24, tzinfo=dt.timezone.utc)
PRIVATE_POLICY = Path("/etc/careertrellis/onedrive-retention/policy.json")
STATE = Path("/var/lib/careertrellis/onedrive-retention")
PUBLIC = Path("/var/lib/careertrellis/onedrive-retention-public")
STAGES = ("hydrate", "publish", "restore", "prepare", "retire")
HEX = re.compile(r"[0-9a-f]{64}\Z")
TX = re.compile(r"[0-9a-f]{32}\Z")


class Refused(ValueError):
    pass


def need(value):
    if not value: raise Refused("retention_coordinator_requires_reconciliation")


def timestamp(value):
    need(type(value) is str)
    try: parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError: raise Refused("retention_coordinator_requires_reconciliation") from None
    need(parsed.utcoffset() == dt.timedelta(0))
    return parsed


class Policy:
    def __init__(self, value):
        required={"schema", "enabled", "application_release",
            "valid_until", "reserve_percent", "peak_multiplier", "namespace_budget_bytes",
            "keep_verified", "grace_seconds", "schedule_hour_utc", "schedule_minute_utc",
            "artifact_revision", "paired_policy_sha256"}
        need(type(value) is dict and required<=set(value)<=required|{'expiry_mode','schedule_days'})
        weekdays=('Mon','Tue','Wed','Thu','Fri','Sat','Sun')
        days=value.get('schedule_days',list(weekdays))
        need(type(days) is list and days and all(type(day) is str and day in weekdays for day in days)
            and days==[day for day in weekdays if day in days] and tuple(days) in (weekdays,weekdays[:6]))
        self.schedule_days=tuple(days)
        self.expiry_mode=value.get('expiry_mode','recoverable_manual')
        need(type(self.expiry_mode) is str and self.expiry_mode in ('recoverable_manual','conditional_permanent'))
        need(value["schema"] == "careertrellis.onedrive-retention-policy/v1"
             and type(value["enabled"]) is bool
             and type(value["application_release"]) is str
             and re.fullmatch(r"[0-9a-f]{40}", value["application_release"])
             and value["application_release"] != "0"*40)
        need(timestamp(value["valid_until"]) <= EXPIRY)
        for key, low, high in (("reserve_percent", 20, 99), ("peak_multiplier", 4, 100),
                ("namespace_budget_bytes", 1, 512*1024**2), ("keep_verified", 2, 100),
                ("grace_seconds", 172800, 365*86400), ("schedule_hour_utc", 0, 23),
                ("schedule_minute_utc", 0, 59)):
            need(type(value[key]) is int and low <= value[key] <= high)
        need(type(value["artifact_revision"]) is str and re.fullmatch(r"[0-9a-f]{40}", value["artifact_revision"])
             and value["artifact_revision"] != "0"*40
             and type(value["paired_policy_sha256"]) is str and HEX.fullmatch(value["paired_policy_sha256"]))
        self.data = json.loads(bundle.canonical(value))
        self.digest = hashlib.sha256(bundle.canonical(self.data)).hexdigest()

    def require_active(self, now):
        need(now.utcoffset() == dt.timedelta(0) and self.data["enabled"] is True
             and now < timestamp(self.data["valid_until"]))


def native_policy_binding(policy):
    private = hydrate.protected(PRIVATE_POLICY, 0o600)[0]
    public = hydrate.protected(PUBLIC / "policy.json", 0o644)[0]
    need(private == public and bundle.canonical(policy.data) == private)


def policy_calendar(policy):
    need(type(policy) is Policy)
    days=policy.schedule_days
    prefix='' if len(days)==7 else 'Mon..Sat ' if days==('Mon','Tue','Wed','Thu','Fri','Sat') else ','.join(days)+' '
    return prefix+'*-*-* %02d:%02d:00 UTC'%(policy.data['schedule_hour_utc'],policy.data['schedule_minute_utc'])


def recovery_schedule_projection(recovery_point,opportunities,acceptance_bound_seconds):
    """Explicit conservative timing assumptions only; never native acceptance.

    Each opportunity supplies latest start (including jitter) and the earliest
    source recovery point guaranteed available then. Missed/failed opportunities
    invalidate the assumptions; actual monitoring remains authoritative.
    """
    need(type(acceptance_bound_seconds) is int and 0<acceptance_bound_seconds<=36*3600
        and type(opportunities) is list and 1<=len(opportunities)<=400)
    point=timestamp(recovery_point);last=point;worst=0
    for row in opportunities:
        need(type(row) is dict and set(row)=={'latest_start','source_point_not_before'})
        start=timestamp(row['latest_start']);next_point=timestamp(row['source_point_not_before'])
        need(last<=start and point<=next_point<=start)
        completion=start+dt.timedelta(seconds=acceptance_bound_seconds)
        worst=max(worst,int((completion-point).total_seconds()));point=next_point;last=start
    return {'schema':'careertrellis.recovery-schedule-projection/v1','assumptions_only':True,
        'maximum_projected_recovery_point_age_seconds':worst,'fits_36h_assumptions':worst<=36*3600,
        'native_timing_accepted':False,'acceptance_latency_bound_seconds':acceptance_bound_seconds}


def web_proof(value, now, expected_release=None):
    need(type(value) is dict and set(value) == {"schema", "host", "authority", "pending_phase",
        "activation_lease_absent", "services_healthy", "release_binding_verified", "release", "observed_at"})
    need(value["schema"] == "careertrellis.onedrive-restore-web-preflight/v1"
         and value["host"] == "careertrellis-staging-web-1"
         and value["authority"] == "postgres-r2-authoritative" and value["pending_phase"] == "activated"
         and all(value[key] is True for key in ("activation_lease_absent", "services_healthy", "release_binding_verified"))
         and type(value["release"]) is str and re.fullmatch(r"[0-9a-f]{40}", value["release"])
         and value["release"] != "0"*40
         and (expected_release is None or value["release"] == expected_release)
         and 0 <= (now-timestamp(value["observed_at"])).total_seconds() <= 300)
    return value


def run(transaction, policy, backend):
    from . import endpoint
    need(type(transaction) is str and TX.fullmatch(transaction))
    if type(backend) is NativeWEB:backend.lock_transaction=transaction
    with backend.locked():
        policy.require_active(backend.now())
        backend.preflight()
        if type(backend) is NativeWEB and backend.scheduled and backend.scheduled_not_due is not None:
            # A real current native acceptance can cover this scheduled slot.
            # Keep the actual recovery timestamp; do not fabricate a new run or
            # refresh monitoring status merely because the timer fired.
            return backend.scheduled_not_due
        if type(backend) is NativeWEB:backend.pre_growth(transaction)
        backend.begin(transaction, policy)
        previous = None
        try:
            for stage in STAGES:
                policy.require_active(backend.now())
                facts = {} if previous is None else {"predecessor_receipt_sha256": previous["receipt_sha256"]}
                payload = {"stage": stage, "receipt_facts": facts}
                if type(backend) is NativeWEB:
                    payload['web_history'] = backend.web_history(transaction, stage)
                if stage == "restore": payload["web_preflight"] = web_proof(
                    backend.web_preflight(), backend.now(), policy.data["application_release"])
                request = endpoint.request(policy.digest, transaction, stage, payload)
                backend.once(stage + "-intent.json", request)
                response = backend.call(request)  # One dispatch only, even on lost acknowledgement.
                endpoint.validate_response(response, request)
                backend.once(stage + "-ack.json", response)
                terminal = response if response["state"] == "complete" else backend.wait(request)
                endpoint.validate_response(terminal, request)
                need(terminal["state"] == "complete")
                if stage == 'hydrate' and getattr(backend,'expected_backup_label',None) is not None:
                    need(terminal['facts']['backup_label'] == backend.expected_backup_label)
                if stage == "restore": need(terminal["facts"].get("native_restore_accepted") is True)
                if previous is not None and stage in ("publish", "restore"):
                    need(all(terminal["facts"][key] == previous["facts"][key] for key in
                        ("archive_sha256", "archive_bytes", "generation_sha256", "backup_label")))
                backend.once(stage + "-complete.json", terminal)
                previous = terminal
            backend.finish("complete", previous)
            return previous
        except BaseException:
            backend.finish("failed", None)
            raise Refused("retention_coordinator_requires_reconciliation") from None


def read_private(path):
    raw = hydrate.protected(path, 0o600)[0]
    value = json.loads(raw)
    need(bundle.canonical(value) == raw)
    return value


def directory(path):
    path = Path(path)
    for parent in (path, *path.parents):
        meta = parent.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0 and not meta.st_mode & 0o022)
    need(stat.S_IMODE(path.lstat().st_mode) == 0o700)


def replace_private(path, value):
    temporary = Path(str(path) + ".new")
    hydrate.exclusive(temporary, bundle.canonical(value))
    os.replace(temporary, path)
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def _bound_history_policy(sha):
    """Limits come from current accepted bytes or an authenticated archive."""
    from . import baseline,guard
    context=baseline.native_context()
    need(type(context) is baseline.AuthenticatedContext and context.authenticated)
    current=Policy(read_private(PRIVATE_POLICY));native_policy_binding(current)
    if current.digest==sha:
        need(context.bindings['standalone_policy_sha256']==sha);return current
    old=guard.authenticated_policy_archive(sha)
    need(type(old) is guard.AuthenticatedPolicyArchive and old.authenticated
        and old.archive_policy_sha==sha and old.current_bindings==context.bindings)
    result=Policy(old.policy);need(result.digest==sha);return result


def _capacity_with_policy(value,policy):
    from . import guard
    allocation=value['allocation'];quota=value['quota']
    return guard.capacity(quota['total'],quota['free'],allocation['source_bytes'],allocation['candidate_bytes'],
        value['namespace_bytes'],allocation['namespace_new_bytes'],reserve_percent=policy.data['reserve_percent'],
        peak_multiplier=policy.data['peak_multiplier'],namespace_budget_bytes=policy.data['namespace_budget_bytes'])


def pre_growth_web_history(transaction,current_transaction=None):
    """Protected provider-free census; never dispatch/verify/repair as a poll."""
    from . import native_restore,guard,pre_growth_native as pg
    need(type(transaction) is str and TX.fullmatch(transaction))
    root=STATE/'pre-growth-web'/transaction;directory(root)
    names={path.name for path in root.iterdir()}
    allowed={'owner.json','capacity-before.json','submit-intent.json','submit-ack.json','terminal-observation.json',
        'verify-intent.json','verify-ack.json','capacity-after.json','complete.json'}
    need(names<=allowed and 'owner.json' in names)
    chain=['owner.json','capacity-before.json','submit-intent.json','submit-ack.json','terminal-observation.json',
        'verify-intent.json','verify-ack.json','capacity-after.json','complete.json']
    owner=read_private(root/'owner.json')
    need(type(owner) is dict and set(owner)=={'schema','transaction','capture_transaction','retention_policy_sha256',
        'activation_sha256','owner','observed_at'} and owner['schema']=='careertrellis.pre-growth-web-owner/v1'
        and owner['transaction']==transaction and type(owner['capture_transaction']) is str and TX.fullmatch(owner['capture_transaction']))
    for field in ('retention_policy_sha256','activation_sha256'):need(type(owner[field]) is str and HEX.fullmatch(owner[field]))
    if transaction==current_transaction and 'complete.json' not in names:
        need(any(names==set(chain[:index]) for index in range(1,len(chain))))
        need(owner['owner']==native_restore.process_identity(os.getpid()))
        return {'transaction':transaction,'state':'current','owner':owner['owner'],'provider_calls':0}
    done=read_private(root/'complete.json')
    need(type(done) is dict and set(done)=={'schema','transaction','capture_transaction','capacity_allowed',
        'growth_authorized','retirement_dispatched','observed_at'}
        and done['schema']=='careertrellis.pre-growth-web-complete/v1' and done['transaction']==transaction
        and done['capture_transaction']==owner['capture_transaction'] and done['capacity_allowed'] is True
        and done['growth_authorized'] is False and type(done['retirement_dispatched']) is bool)
    dispatched='submit-intent.json' in names;need(dispatched==done['retirement_dispatched'])
    need(names==(set(chain) if dispatched else {'owner.json','capacity-before.json','complete.json'}))
    bound_policy=_bound_history_policy(owner['retention_policy_sha256'])
    before=read_private(root/'capacity-before.json')
    need(before['baseline_bindings']['standalone_policy_sha256']==bound_policy.digest)
    if dispatched:
        need({'submit-ack.json','terminal-observation.json','verify-intent.json','verify-ack.json','capacity-after.json'}<=names)
        submitted=read_private(root/'submit-intent.json');need(type(submitted) is dict and set(submitted)=={'request','web_history'})
        request=pg.request(submitted['request'])
        need(request['transaction']==transaction and request['activation_sha256']==owner['activation_sha256']
            and request['retention_policy_sha256']==owner['retention_policy_sha256']
            and request['web_history_sha256']==hashlib.sha256(bundle.canonical(submitted['web_history'])).hexdigest()
            and request['allocation']==before['allocation']
            and (bound_policy.expiry_mode=='conditional_permanent' or not _capacity_with_policy(before,bound_policy)['allowed']))
        intent_sha=hashlib.sha256(bundle.canonical({'schema':'careertrellis.pre-growth-ingress/v1',**submitted})).hexdigest()
        ack=read_private(root/'submit-ack.json')
        need(type(ack) is dict and set(ack)=={'schema','transaction','intent_sha256','state','growth_authorized'}
            and ack['schema']=='careertrellis.pre-growth-dispatched/v1' and ack['transaction']==transaction
            and ack['intent_sha256']==intent_sha and ack['state']=='dispatched' and ack['growth_authorized'] is False)
        observed=read_private(root/'terminal-observation.json')
        need(observed=={'transaction':transaction,'state':'verification_required','growth_authorized':False})
        verify_request=read_private(root/'verify-intent.json')
        need(verify_request=={'transaction':transaction,'retention_policy_sha256':bound_policy.digest})
        verified=read_private(root/'verify-ack.json')
        need(type(verified) is dict and set(verified)=={'transaction','state','terminal_proof_sha256','growth_authorized'}
            and verified['transaction']==transaction and verified['state']=='complete' and verified['growth_authorized'] is False
            and type(verified['terminal_proof_sha256']) is str and HEX.fullmatch(verified['terminal_proof_sha256']))
    capacity=read_private(root/('capacity-after.json' if dispatched else 'capacity-before.json'))
    need(capacity['baseline_bindings']['standalone_policy_sha256']==bound_policy.digest
        and capacity['activation_sha256']==owner['activation_sha256']
        and before['activation_sha256']==owner['activation_sha256']
        and timestamp(owner['observed_at'])<=timestamp(before['observed_at'])<=timestamp(capacity['observed_at'])<=timestamp(done['observed_at']))
    need(_capacity_with_policy(capacity,bound_policy)['allowed'])
    return {'transaction':transaction,'state':'complete','provider_calls':0,
        'complete_sha256':hashlib.sha256(bundle.canonical(done)).hexdigest()}


def public_status(role, policy_sha, phase, native=None):
    """Only explicitly non-secret facts leave the private operational roots."""
    need(role in ("web", "db") and phase in ("running", "complete", "failed")
         and type(policy_sha) is str and HEX.fullmatch(policy_sha))
    for parent in (PUBLIC, *PUBLIC.parents):
        meta = parent.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0 and not meta.st_mode & 0o022)
    path = PUBLIC / (role + ".json")
    previous = {}
    if os.path.lexists(path): previous = json.loads(hydrate.protected(path, 0o644)[0])
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    success = previous.get("last_successful_at")
    archive, receipt = previous.get("archive_sha256"), previous.get("native_receipt_sha256")
    if success is not None: timestamp(success)
    for digest in (archive, receipt): need(digest is None or type(digest) is str and HEX.fullmatch(digest))
    if phase == "complete":
        need(type(native) is dict and native["facts"].get("native_restore_accepted") is True)
        archive, receipt, success = native["facts"]["archive_sha256"], native["receipt_sha256"], now
        need(HEX.fullmatch(archive) and HEX.fullmatch(receipt))
    value = {"schema": "careertrellis.onedrive-retention-status/v1", "phase": phase,
        "result": "ok" if phase == "complete" else phase, "observed_at": now,
        "retention_policy_sha256": policy_sha, "last_successful_at": success,
        "native_receipt_sha256": receipt, "archive_sha256": archive}
    temporary = Path(str(path) + ".new")
    hydrate.exclusive(temporary, bundle.canonical(value)); os.chmod(temporary, 0o644)
    fd = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)
    os.replace(temporary, path); fsync_directory(PUBLIC)


@contextmanager
def control(root):
    import fcntl
    directory(root); path = root / "control.lock"
    if not os.path.lexists(path): hydrate.exclusive(path, b"")
    _, expected = hydrate.protected(path, 0o600)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        def check():
            for meta in (os.fstat(fd), path.lstat()):
                need((meta.st_dev, meta.st_ino, meta.st_mode, meta.st_uid, meta.st_gid,
                    meta.st_nlink, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns) == expected)
        check(); fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); check()
        yield
        check()
    finally: os.close(fd)


class NativeWEB:
    """Requires new installed entry.verify_install() before privileged imports."""
    def __init__(self, policy, expected_backup_label=None, *, scheduled=False):
        need(expected_backup_label is None or type(expected_backup_label) is str and
             re.fullmatch(r'[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?',expected_backup_label))
        self.policy, self.root, self.transaction = policy, STATE / "web", None
        self.expected_backup_label = expected_backup_label
        need(type(scheduled) is bool)
        self.scheduled=scheduled;self.scheduled_eligibility_sha256=None
        self.scheduled_not_due=None

    def now(self): return dt.datetime.now(dt.timezone.utc)

    @contextmanager
    def locked(self):
        need(os.geteuid() == os.getegid() == 0 and socket.gethostname() == "careertrellis-staging-web-1")
        # All OneDrive writers on WEB share this existing canonical fence.
        # Keep it for the entire coordinator lifetime, including DB waits.
        # Other writers must inspect DB histories after acquiring it: if this
        # coordinator dies, a still-running DB job remains a blocking attempt.
        import fcntl
        path = Path('/run/lock/careertrellis-maintenance.lock')
        for parent in path.parents:
            meta = parent.lstat()
            need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0
                 and (stat.S_IMODE(meta.st_mode) == 0o1777 if parent == Path('/run/lock')
                      else not meta.st_mode & 0o022))
        before = path.lstat()
        need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
             and stat.S_IMODE(before.st_mode) == 0o644 and before.st_nlink == 1 and before.st_size == 0)
        from . import baseline,guard
        context = baseline.native_context()
        handoff = guard.native_paired_handoff(self.lock_transaction,context,initial=True)
        fd = handoff.maintenance_fd if handoff is not None else os.open(
            path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            identity = lambda meta:(meta.st_dev, meta.st_ino, meta.st_mode, meta.st_uid,
                meta.st_gid, meta.st_nlink, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)
            expected = identity(before)
            need(identity(os.fstat(fd)) == expected)
            if handoff is None:fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:guard._paired_inherited_maintenance(handoff)
            with control(self.root):
                need(identity(os.fstat(fd)) == identity(path.lstat()) == expected)
                yield
                if handoff is not None:guard._paired_inherited_maintenance(handoff)
                need(identity(os.fstat(fd)) == identity(path.lstat()) == expected)
        finally: os.close(fd)

    def web_history(self, transaction, stage):
        from . import guard
        value = guard.native_history_snapshot(transaction, stage)
        guard.validate_peer_history(value, transaction, stage, self.policy.digest)
        return value

    def preflight(self):
        from . import activation
        # Manual bootstrap is allowed before selection. A partial transition
        # fences new work just as it fences either legacy writer.
        selected=activation.routing_state()
        from . import entry  # Absent/unreviewed entry fails closed; no fallback.
        revision=entry.verify_install()
        need(revision == self.policy.data["artifact_revision"])
        context=entry._native_dependencies(revision,'web')
        if selected is not None:
            current=activation.require_activated(context)
            need(current.digest==selected.digest and current.role=='web' and current.bindings==context.bindings)
        else:need(self.scheduled is False)
        native_policy_binding(self.policy)
        from paired_maintenance import entry as paired_entry, runtime
        paired_entry.verify_install()
        self.paired = paired_entry.load_policy()
        need(self.paired.digest == self.policy.data["paired_policy_sha256"])
        self.runtime = runtime.preflight_runtime(self.paired, "web")
        self.runtime.verify_sources(); self.runtime.health()
        self._prior_terminal()
        if self.scheduled:self._scheduled_eligibility(context,current)
        final=activation.routing_state()
        need((selected is None and final is None) or (selected is not None and final is not None
             and final.digest==selected.digest and final.bindings==context.bindings))
        self.retention_context=context;self.retention_selector=selected

    def _pre_growth_call(self,transaction,operation,payload):
        from paired_maintenance.client import Client
        return Client(self.paired,transaction).call(operation,payload)

    def _pre_growth_capacity(self,transaction):
        from . import guard,pre_growth_native as pg
        value=self._pre_growth_call(transaction,'retention_capacity_snapshot',
            {'retention_policy_sha256':self.policy.digest,'stage':pg.PURPOSE})
        need(type(value) is dict and set(value)=={'schema','history','quota','namespace_bytes','account_scope',
            'observed_at','baseline_bindings','allocation','activation_sha256'}
            and value['schema']=='careertrellis.retention-capacity-preflight/v1'
            and value['baseline_bindings']==self.retention_context.bindings
            and value['activation_sha256']==self.retention_selector.digest)
        guard.validate_peer_history(value['history'],transaction,pg.PURPOSE,self.policy.digest,expected_role='db',now=self.now().isoformat())
        need(value['history']['baseline_bindings']==self.retention_context.bindings
            and 0<=(self.now()-timestamp(value['observed_at'])).total_seconds()<=120)
        allocation=value['allocation']
        need(type(allocation) is dict and set(allocation)=={'source_bytes','candidate_bytes','namespace_new_bytes'}
            and all(type(size) is int and 0<=size<=512*1024**2 for size in allocation.values())
            and allocation['candidate_bytes']>0 and allocation['namespace_new_bytes']==allocation['candidate_bytes'])
        quota=value['quota'];need(type(quota) is dict and {'total','free'}<=set(quota))
        capacity=_capacity_with_policy(value,self.policy)
        return value,capacity

    def pre_growth(self,capture_transaction):
        """Separate retirement owner before begin; no deletion credit or replay."""
        from . import activation,pre_growth_native as pg,native_restore
        need(hasattr(self,'retention_selector'))
        if self.retention_selector is None:return # Unselected bootstrap never expires.
        tx=secrets.token_hex(16);root=STATE/'pre-growth-web';directory(root)
        for old in root.iterdir():pre_growth_web_history(old.name)
        task=root/tx;task.mkdir(mode=0o700);fsync_directory(root)
        def once(name,value):hydrate.exclusive(task/name,bundle.canonical(value))
        once('owner.json',{'schema':'careertrellis.pre-growth-web-owner/v1','transaction':tx,
            'capture_transaction':capture_transaction,'retention_policy_sha256':self.policy.digest,
            'activation_sha256':self.retention_selector.digest,'owner':native_restore.process_identity(os.getpid()),
            'observed_at':self.now().isoformat()})
        try:
            before,capacity=self._pre_growth_capacity(tx);once('capacity-before.json',before)
            if self.policy.expiry_mode=='conditional_permanent' or not capacity['allowed']:
                history=self.web_history(tx,pg.PURPOSE)
                request=pg.request({'schema':pg.SCHEMA,'operation':pg.PURPOSE,'transaction':tx,
                    'retention_policy_sha256':self.policy.digest,'activation_sha256':self.retention_selector.digest,
                    'web_history_sha256':hashlib.sha256(bundle.canonical(history)).hexdigest(),'allocation':before['allocation']})
                once('submit-intent.json',{'request':request,'web_history':history})
                result=self._pre_growth_call(tx,pg.PURPOSE,{'request':request,'web_history':history})
                need(type(result) is dict and set(result)=={'schema','transaction','intent_sha256','state','growth_authorized'}
                    and result['schema']=='careertrellis.pre-growth-dispatched/v1' and result['transaction']==tx
                    and result['growth_authorized'] is False and result.get('state')=='dispatched'
                    and result.get('intent_sha256')==hashlib.sha256(bundle.canonical(
                    {'schema':'careertrellis.pre-growth-ingress/v1','request':request,'web_history':history})).hexdigest())
                once('submit-ack.json',result)
                deadline=time.monotonic()+24*3600
                while True:
                    need(type(result) is dict and result.get('transaction')==tx and result.get('growth_authorized') is False
                        and result.get('state') in ('dispatched','running','verification_required','complete'))
                    if result['state'] not in ('dispatched','running'):break
                    need(time.monotonic()<deadline);time.sleep(60)
                    # Status is observation ONLY, never verify/dispatch/recollection.
                    result=self._pre_growth_call(tx,pg.PURPOSE+'_status',{'retention_policy_sha256':self.policy.digest})
                once('terminal-observation.json',result)
                need(result=={'transaction':tx,'state':'verification_required','growth_authorized':False})
                once('verify-intent.json',{'transaction':tx,'retention_policy_sha256':self.policy.digest})
                verified=self._pre_growth_call(tx,pg.PURPOSE+'_verify',{'retention_policy_sha256':self.policy.digest})
                need(type(verified) is dict and set(verified)=={'transaction','state','terminal_proof_sha256','growth_authorized'}
                    and verified['transaction']==tx and verified['state']=='complete' and verified['growth_authorized'] is False
                    and type(verified['terminal_proof_sha256']) is str and HEX.fullmatch(verified['terminal_proof_sha256']))
                once('verify-ack.json',verified)
                after,capacity=self._pre_growth_capacity(tx);once('capacity-after.json',after)
                need(capacity['allowed']) # Fresh measured state; never subtract retired bytes.
            current=activation.require_activated(self.retention_context)
            need(current.digest==self.retention_selector.digest)
            once('complete.json',{'schema':'careertrellis.pre-growth-web-complete/v1','transaction':tx,
                'capture_transaction':capture_transaction,'capacity_allowed':True,'growth_authorized':False,
                'retirement_dispatched':os.path.lexists(task/'submit-intent.json'),'observed_at':self.now().isoformat()})
        except BaseException:
            once('failed.json',{'transaction':tx,'result':'requires_reconciliation','replay_authorized':False})
            raise Refused('retention_pre_growth_requires_reconciliation') from None

    def _scheduled_eligibility(self,context,selector):
        """Use only the authenticated fixed client, never caller-supplied proof."""
        self.scheduled_not_due=None
        from . import activation_readiness
        from paired_maintenance.client import Client
        transaction=getattr(self,'lock_transaction',None)
        need(type(transaction) is str and TX.fullmatch(transaction)
             and selector.bindings==context.bindings and selector.role=='web')
        evidence=Client(self.paired,transaction).call('retention_activation_evidence',{
            'retention_policy_sha256':self.policy.digest,'activation_sha256':selector.digest})
        need(type(evidence) is dict and set(evidence)=={'schema','transaction','retention_policy_sha256',
            'activation_sha256','replacement_evidence','current_backup'}
             and evidence['schema']=='careertrellis.onedrive-retention-scheduled-eligibility/v1'
             and evidence['transaction']==transaction and evidence['retention_policy_sha256']==self.policy.digest
             and evidence['activation_sha256']==selector.digest)
        activation_readiness.validate_evidence(evidence['replacement_evidence'],context.bindings,context.revision,
            context.document['application_release'],self.now())
        self.scheduled_eligibility_sha256=hashlib.sha256(bundle.canonical(evidence)).hexdigest()
        minimum=self.paired.data['minimum_capture_age_seconds']
        need(type(minimum) is int and 1<=minimum<=12*3600)
        current=evidence['current_backup']
        need(type(current) is dict and set(current)=={'schema','backup_label','backup_completed_at','observed_at'}
             and current['schema']=='careertrellis.onedrive-current-backup/v1'
             and type(current['backup_label']) is str and re.fullmatch(
                 r'[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?',current['backup_label']))
        observed=timestamp(current['observed_at']);completed=timestamp(current['backup_completed_at'])
        need(0<=(self.now()-observed).total_seconds()<=120 and completed<=observed)
        newest_row=max(evidence['replacement_evidence']['replacement_bundles'],
                       key=lambda row:(timestamp(row['accepted_at']),row['archive_sha256']))
        newest=timestamp(newest_row['accepted_at'])
        age=(self.now()-newest).total_seconds();need(age>=0)
        if (age<minimum and newest_row['backup_label']==current['backup_label']
                and 0<=(self.now()-completed).total_seconds()<minimum):
            self.scheduled_not_due={'schema':'careertrellis.onedrive-scheduled-result/v1',
                'state':'skipped','reason':'recent_verified_native_backup',
                'eligibility_sha256':self.scheduled_eligibility_sha256,
                'latest_native_accepted_at':newest.isoformat(),
                'backup_label':current['backup_label'],'new_backup_created':False}

    def _prior_terminal(self):
        entries = list(self.root.iterdir()); need(len(entries) <= 10000)
        for prior in entries:
            if prior.name in ("control.lock", "latest.json"): continue
            need(TX.fullmatch(prior.name) is not None)
            directory(prior)
            terminal = read_private(prior / "coordinator-terminal.json")
            need(terminal["transaction"] == prior.name and terminal["state"] == "complete")
        latest = self.root / "latest.json"
        if os.path.lexists(latest): need(read_private(latest)["state"] == "complete")

    def begin(self, transaction, policy):
        from . import baseline,guard
        context=baseline.native_context()
        handoff=guard.native_paired_handoff(transaction,context,initial=True)
        if handoff is not None:
            need(self.expected_backup_label == handoff.required_backup_label)
        self._prior_terminal()
        self.transaction = transaction
        self.task = self.root / transaction
        self.task.mkdir(mode=0o700)
        fsync_directory(self.root)
        self.once("coordinator-intent.json", {"schema": "careertrellis.retention-coordinator/v1",
            "transaction": transaction, "retention_policy_sha256": policy.digest,
            'expected_backup_label':self.expected_backup_label,
            'scheduled':self.scheduled,'scheduled_eligibility_sha256':self.scheduled_eligibility_sha256,
            'paired_handoff_sha256':handoff.handoff_sha256 if handoff is not None else None,
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "pid": os.getpid(),
            "process_start_ticks": int(Path("/proc/self/stat").read_text().rsplit(") ", 1)[1].split()[19])})
        replace_private(self.root/'latest.json', {"transaction": transaction, "state": "running"})
        public_status("web", self.policy.digest, "running")

    def once(self, name, value):
        hydrate.exclusive(self.task / name, bundle.canonical(value))
        if name == "restore-complete.json": self.native = value

    def web_preflight(self):
        self.runtime.verify_sources(); self.runtime.health()
        caddy = self.runtime.state("caddy.service")
        need(caddy["ActiveState"] == "active" and caddy["SubState"] == "running"
             and int(caddy["MainPID"]) > 0)
        raw = hydrate.protected("/etc/careertrellis/authority/pending-cutover")[0]
        phases = [line.partition("=")[2] for line in raw.decode("ascii").splitlines() if line.startswith("phase=")]
        need(phases == ["activated"] and not os.path.lexists("/run/careertrellis-hosted-activation.lease"))
        return {"schema": "careertrellis.onedrive-restore-web-preflight/v1",
            "host": "careertrellis-staging-web-1", "authority": "postgres-r2-authoritative",
            "pending_phase": "activated", "activation_lease_absent": True, "services_healthy": True,
            "release_binding_verified": True, "release": self.policy.data["application_release"],
            "observed_at": self.now().isoformat()}

    def call(self, request):
        from paired_maintenance.client import Client
        payload = {key: value for key, value in request.items() if key not in ("schema", "operation")}
        return Client(self.paired, self.transaction).call(request["operation"], payload)

    def wait(self, request):
        from . import endpoint
        deadline = time.monotonic() + 24*3600
        while time.monotonic() < deadline:
            query = endpoint.request(self.policy.digest, self.transaction, "status",
                {"stage": request["payload"]["stage"], "receipt_facts": {}})
            response = self.call(query)
            endpoint.validate_response(response, request)
            if response["state"] != "running": return response
            time.sleep(60)
        raise Refused("retention_coordinator_requires_reconciliation")

    def finish(self, state, result):
        value = {"transaction": self.transaction, "state": state,
                 "observed_at": self.now().isoformat()}
        self.once("coordinator-terminal.json", dict(value, result=result))
        replace_private(self.root / "latest.json", value)
        public_status("web", self.policy.digest, state, getattr(self, "native", None))
