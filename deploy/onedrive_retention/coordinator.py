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
        need(type(value) is dict and set(value) == {"schema", "enabled", "application_release",
            "valid_until", "reserve_percent", "peak_multiplier", "namespace_budget_bytes",
            "keep_verified", "grace_seconds", "schedule_hour_utc", "schedule_minute_utc",
            "artifact_revision", "paired_policy_sha256"})
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
    with backend.locked():
        policy.require_active(backend.now())
        backend.preflight()
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
    def __init__(self, policy):
        self.policy, self.root, self.transaction = policy, STATE / "web", None

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
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            identity = lambda meta:(meta.st_dev, meta.st_ino, meta.st_mode, meta.st_uid,
                meta.st_gid, meta.st_nlink, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)
            expected = identity(before)
            need(identity(os.fstat(fd)) == expected)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with control(self.root):
                need(identity(os.fstat(fd)) == identity(path.lstat()) == expected)
                yield
                need(identity(os.fstat(fd)) == identity(path.lstat()) == expected)
        finally: os.close(fd)

    def web_history(self, transaction, stage):
        from . import guard
        value = guard.native_history_snapshot(transaction, stage)
        guard.validate_peer_history(value, transaction, stage, self.policy.digest)
        return value

    def preflight(self):
        from . import entry  # Absent/unreviewed entry fails closed; no fallback.
        need(entry.verify_install() == self.policy.data["artifact_revision"])
        native_policy_binding(self.policy)
        from paired_maintenance import entry as paired_entry, runtime
        paired_entry.verify_install()
        self.paired = paired_entry.load_policy()
        need(self.paired.digest == self.policy.data["paired_policy_sha256"])
        self.runtime = runtime.preflight_runtime(self.paired, "web")
        self.runtime.verify_sources(); self.runtime.health()

    def begin(self, transaction, policy):
        entries = list(self.root.iterdir()); need(len(entries) <= 10000)
        for prior in entries:
            if prior.name in ("control.lock", "latest.json"): continue
            need(TX.fullmatch(prior.name) is not None)
            directory(prior)
            terminal = read_private(prior / "coordinator-terminal.json")
            need(terminal["transaction"] == prior.name and terminal["state"] == "complete")
        latest = self.root / "latest.json"
        if os.path.lexists(latest): need(read_private(latest)["state"] == "complete")
        self.transaction = transaction
        self.task = self.root / transaction
        self.task.mkdir(mode=0o700)
        fsync_directory(self.root)
        self.once("coordinator-intent.json", {"schema": "careertrellis.retention-coordinator/v1",
            "transaction": transaction, "retention_policy_sha256": policy.digest,
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "pid": os.getpid(),
            "process_start_ticks": int(Path("/proc/self/stat").read_text().rsplit(") ", 1)[1].split()[19])})
        replace_private(latest, {"transaction": transaction, "state": "running"})
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
