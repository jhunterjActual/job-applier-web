"""DB-only read-only source hydration. Never uploads, restores or deletes.

Existing attempts require attended reconciliation. Native callers must install
this packet with reviewed byte bindings; no application/service changes occur.
The canonical repository lock remains owned throughout capture and bundling.
"""
from contextlib import contextmanager
import configparser
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import subprocess
import sys
import time
import types

from . import bundle, capture_plan as plan

PINS = {"backup_health": "8d2facf6d71bf985c1c0ffe8b6a661fc9a9699548b6bb81163cd787f53ba97fa",
        "pgbackrest_inventory": "8ce0743446e390c27f5dafa9ad0ead30f41bf9162ea508a1b7fc7469f4a79aa5"}
CORE = Path("/usr/local/libexec/careertrellis")
UNITS = tuple("careertrellis-pgbackrest-" + n + ".service" for n in
              ("full", "differential", "onedrive", "inventory", "restore"))
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
MAX_OUTPUT = 32 * 1024**2


class HydrateError(ValueError):
    pass


def need(value):
    if not value:
        raise HydrateError("repository_hydration_refused")


def hydrate(transaction, backend):
    """Injectable orchestration; backend owns all native trust boundaries."""
    need(type(transaction) is str and re.fullmatch(r"[0-9a-f]{32}", transaction))
    with backend.locked():
        backend.preflight()
        backend.begin(transaction)
        try:
            backend.status("capturing")
            generation = backend.capture()
            plan.validate_generation(generation)
            backend.accept_label(generation["backup_label"])
            backend.save_generation(generation)
            backend.status("hydrating")
            backend.copy_source(generation)
            backend.status("verifying")
            backend.verify_source(generation)
            backend.status("bundling")
            result = backend.build(generation)
            backend.unchanged()
            backend.status("complete", result)
            return result
        except BaseException:
            backend.status("failed", {"category": "hydration_failed_requires_reconciliation"})
            raise HydrateError("repository_hydration_refused") from None


def protected(path, mode=None, limit=MAX_OUTPUT):
    path = Path(path)
    for parent in path.parents:
        meta = parent.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0
             and not meta.st_mode & 0o022)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
             and before.st_nlink == 1 and not before.st_mode & 0o022)
        if mode is not None:
            need(stat.S_IMODE(before.st_mode) == mode)
        need(before.st_size <= limit)
        raw = bytearray()
        while len(raw) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        def identity(meta):
            return (meta.st_dev, meta.st_ino, meta.st_mode, meta.st_uid, meta.st_gid,
                    meta.st_nlink, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)
        need(len(raw) <= limit and identity(os.fstat(fd)) == identity(before)
             and identity(path.lstat()) == identity(before))
        return bytes(raw), identity(before)
    finally:
        os.close(fd)


def command(args, *, descriptor=None, lock_descriptor=None, output=False, timeout=120):
    """Bound private output while produced; timeout never retries a child."""
    passed = tuple(fd for fd in (descriptor, lock_descriptor) if fd is not None)
    with subprocess.Popen(args, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if output else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=ENV, pass_fds=passed) as child:
        try:
            chunks, size, deadline = [], 0, time.monotonic() + timeout
            if output:
                while True:
                    remaining = deadline - time.monotonic(); need(remaining > 0)
                    ready, _, _ = select.select([child.stdout], [], [], min(remaining, 1))
                    if not ready:
                        continue
                    raw = os.read(child.stdout.fileno(), 65536)
                    if not raw:
                        break
                    size += len(raw); need(size <= MAX_OUTPUT); chunks.append(raw)
            need(child.wait(timeout=max(0.001, deadline-time.monotonic())) == 0)
            return b"".join(chunks)
        finally:
            if child.poll() is None:
                child.kill(); child.wait()


def exclusive(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    parent = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


class Native:
    def __init__(self):
        self.root = None
        self.config = None
        self.fd = None
        self.sequence = 0

    def idle(self):
        listing = command(["/usr/bin/systemctl", "list-units", "--all", "--no-legend",
            "--plain", "careertrellis-paired-db*.service"], output=True, timeout=10)
        paired = []
        for line in listing.decode("ascii").splitlines():
            unit = line.split()[0]
            need(re.fullmatch(r"careertrellis-paired-db(?:-recovery)?@[0-9a-f]{32}\.service", unit))
            paired.append(unit)
        for unit in UNITS + tuple(paired):
            raw = command(["/usr/bin/systemctl", "show", unit,
                "--property=ActiveState,MainPID,ControlPID,Job"], output=True, timeout=10)
            state = dict(line.split("=", 1) for line in raw.decode("ascii").splitlines())
            need(set(state) == {"ActiveState", "MainPID", "ControlPID", "Job"}
                 and state["ActiveState"] in ("inactive", "failed")
                 and state["MainPID"] == state["ControlPID"] == "0" and state["Job"] == "")

    @contextmanager
    def locked(self):
        import fcntl
        need(os.geteuid() == os.getegid() == 0
             and socket.gethostname() == "job-applier-db-staging-01")
        self.idle()
        lock = Path(plan.LOCK)
        for parent in lock.parents:
            meta = parent.lstat()
            need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0)
            need(stat.S_IMODE(meta.st_mode) == 0o1777 if str(parent) == "/run/lock"
                 else not meta.st_mode & 0o022)
        fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            expected = os.fstat(fd)
            def check():
                now = lock.lstat(); opened = os.fstat(fd)
                need(stat.S_ISREG(now.st_mode) and now.st_uid == now.st_gid == 0
                     and stat.S_IMODE(now.st_mode) == 0o600 and now.st_nlink == 1
                     and (now.st_dev, now.st_ino) == (opened.st_dev, opened.st_ino)
                     == (expected.st_dev, expected.st_ino))
            check(); fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); check(); self.idle()
            self.check_lock = check
            self.lock_fd = fd
            yield
            check()
        finally:
            try:
                if self.config is not None:
                    try:
                        self.unchanged()
                    except BaseException:
                        if self.root is not None:
                            self.status("failed", {"category": "final_guard_requires_reconciliation"})
                        raise
            finally:
                if self.fd is not None:
                    os.close(self.fd); self.fd = None
                os.close(fd)

    def preflight(self):
        os.umask(0o077)
        need(hashlib.sha256(protected(plan.BINARY, limit=256 * 1024**2)[0]).hexdigest() == plan.BINARY_SHA256)
        self.config = protected(plan.CONFIG, 0o600)
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.read_string(self.config[0].decode("utf-8"))
        need(set(parser.sections()) == {"r2", "onedrive"} and not parser.defaults()
             and parser["r2"].get("type") == "s3"
             and parser["r2"].get("provider") == "Cloudflare"
             and re.fullmatch(r"https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com/?",
                              parser["r2"].get("endpoint", "")))
        sealed = configparser.ConfigParser(interpolation=None)
        sealed["r2"] = dict(parser["r2"])
        output = io.StringIO(); sealed.write(output)
        import fcntl
        self.fd = os.memfd_create("repository-source-config", os.MFD_ALLOW_SEALING)
        raw = output.getvalue().encode(); need(os.write(self.fd, raw) == len(raw))
        os.fchmod(self.fd, 0o600); os.lseek(self.fd, 0, 0)
        fcntl.fcntl(self.fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK
                    | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
        for name, digest in PINS.items():
            path = CORE / (name + ".py"); raw = protected(path)[0]
            need(hashlib.sha256(raw).hexdigest() == digest)
            module = types.ModuleType(name); module.__file__ = str(path)
            sys.modules[name] = module
            exec(compile(raw, str(path), "exec"), module.__dict__)
            setattr(self, name, module)
        self.unchanged()

    def begin(self, transaction):
        parent = Path(plan.ROOT)
        # Create only the fixed namespace; unknown existing parents never repaired.
        for ancestor in parent.parents:
            meta = ancestor.lstat()
            need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0
                 and not meta.st_mode & 0o022)
        if not parent.exists():
            parent.mkdir(mode=0o700)
        meta = parent.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0
             and stat.S_IMODE(meta.st_mode) == 0o700)
        root = parent / transaction
        root.mkdir(mode=0o700)  # O_EXCL equivalent; existing attempts refuse.
        self.root = root
        self.base = {"schema": "careertrellis.repository-hydration/v1",
            "transaction": transaction, "pid": os.getpid(),
            "process_start_ticks": int(Path("/proc/self/stat").read_text().rsplit(") ", 1)[1].split()[19]),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        exclusive(root / "intent.json", bundle.canonical(self.base))
        (root / "source").mkdir(mode=0o700)

    def status(self, phase, result=None):
        self.sequence += 1
        value = dict(self.base, phase=phase, observed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        if result is not None:
            value["result"] = result
        # Append-only phases preserve original operation identity after interruption.
        exclusive(self.root / ("status-%02d.json" % self.sequence), bundle.canonical(value))

    def remote(self):
        owner = self
        class SourceRemote(self.pgbackrest_inventory.Remote):
            def run(self, *args):
                need(args[0] in ("lsjson", "cat") and args[1].startswith(plan.SOURCE)
                     and (args[1] == plan.SOURCE or args[1].startswith(plan.SOURCE + "/")))
                owner.check_lock()
                return command([plan.BINARY, *args, "--config", "/proc/self/fd/" + str(owner.fd),
                    "--log-level", "ERROR"], descriptor=owner.fd,
                    lock_descriptor=owner.lock_fd, output=True, timeout=7200)
        return SourceRemote()

    def accept_label(self, label):
        inventory = self.backup_health.scan_receipts()
        operations = ("full_backup", "differential_backup")
        need(not set(operations) & inventory.invalid_operations)
        candidates = [receipt for operation in operations for receipt in inventory.receipts[operation]]
        need(candidates)
        latest = max(candidates, key=lambda row: (row["completed_at"], row["started_at"]))
        need(latest["result"] == "ok" and latest["backup_label"] == label)

    def capture(self):
        # Source catalog observation contains no provider credentials.
        raw = command(["/usr/bin/setpriv", "--reuid=postgres", "--regid=postgres", "--init-groups",
            "--reset-env", "--", "/usr/bin/pgbackrest", "--stanza=careertrellis", "--output=json", "info"],
            output=True, lock_descriptor=self.lock_fd, timeout=120)
        info = json.loads(raw); need(type(info) is list and len(info) == 1)
        stanza = info[0]; need(stanza["name"] == "careertrellis" and stanza["status"]["code"] == 0)
        latest = max(stanza["backup"], key=lambda row: row["timestamp"]["stop"])
        label = latest["label"]; self.accept_label(label)
        return self.pgbackrest_inventory.prepare(self.remote(), label,
            dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def save_generation(self, generation):
        exclusive(self.root / "generation.json", bundle.canonical(generation))
        exclusive(self.root / "files.txt", "".join(path + "\n" for path in sorted(generation["files"])).encode("ascii"))
        needed = 2 * sum(generation["files"].values()) + 5 * 1024**3
        import shutil
        need(shutil.disk_usage(self.root).free >= needed)

    def copy_source(self, generation):
        self.check_lock()
        command([plan.BINARY, "copy", plan.SOURCE, str(self.root / "source"), "--immutable",
            "--files-from-raw", str(self.root / "files.txt"), "--config", "/proc/self/fd/" + str(self.fd),
            "--log-level", "ERROR", "--stats", "0"], descriptor=self.fd,
            lock_descriptor=self.lock_fd, timeout=14400)

    def verify_source(self, generation):
        self.check_lock()
        remote = self.remote()
        self.pgbackrest_inventory.require_files(generation["files"], remote.list(plan.SOURCE))
        self.pgbackrest_inventory.verify_metadata(remote, generation, plan.SOURCE)
        command([plan.BINARY, "check", plan.SOURCE, str(self.root / "source"),
            "--files-from-raw", str(self.root / "files.txt"), "--download", "--one-way",
            "--config", "/proc/self/fd/" + str(self.fd), "--log-level", "ERROR", "--stats", "0"],
            descriptor=self.fd, lock_descriptor=self.lock_fd, timeout=14400)
        self.pgbackrest_inventory.verify_metadata(remote, generation, plan.SOURCE)
        self.accept_label(generation["backup_label"])

    def build(self, generation):
        value = bundle.build_bundle(self.root / "source", self.root / "repository.tar", generation)
        exclusive(self.root / "bundle-manifest.json", bundle.canonical(value["manifest"]))
        return {"sha256": value["sha256"], "archive_bytes": value["archive_bytes"],
                "generation_sha256": value["manifest"]["generation_sha256"],
                "backup_label": generation["backup_label"], "file_count": len(generation["files"])}

    def unchanged(self):
        need(protected(plan.CONFIG, 0o600) == self.config)
        self.check_lock()


def main():
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument("--transaction", required=True)
    args = parser.parse_args()
    try:
        result = hydrate(args.transaction, Native())
        print(json.dumps({"result": "hydrated_bundle_only", **result}, sort_keys=True))
        return 0
    except BaseException:
        print('{"result":"refused","category":"hydration_requires_reconciliation"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
