"""Publish one immutable encrypted candidate, then verify its OneDrive readback.

No retirement, timer changes, native PostgreSQL restore or replay is implemented.
Every partial output is retained. Run only a reviewed installed packet on the DB
host. Root-private intent precedes provider writes; child identities and terminal
states remain durable. A download receipt is explicitly not native acceptance.
"""
import configparser
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from . import bundle, capture_plan as plan, hydrate, policy


class PublishError(ValueError):
    pass


def need(value, category="candidate_publication_refused"):
    if not value:
        raise PublishError(category)


def publish(transaction, backend):
    need(type(transaction) is str and re.fullmatch(r"[0-9a-f]{32}", transaction))
    with backend.locked():
        backend.preflight()
        candidate = backend.candidate(transaction)
        backend.begin_publish(candidate)
        try:
            facts = backend.capacity_facts(candidate)
            # Until charged-byte observations exist, allow two candidate-sized
            # provider allocations, then apply the policy's separate 2x peak.
            # This is an explicit conservative bound, not a measured quota delta.
            increment_bound = 2 * candidate["archive_bytes"]
            assessment = policy.capacity(facts["total"], facts["free"], increment_bound,
                                         facts["namespace_bytes"], candidate["archive_bytes"])
            need(assessment["allowed"], "candidate_capacity_refused")
            backend.status("uploading", {"capacity": assessment,
                "increment_basis": "conservative_two_version_upper_bound",
                "conservative_increment_bytes": increment_bound})
            backend.upload(candidate)
            backend.status("downloading")
            backend.download(candidate)
            backend.status("verifying_download")
            manifest = backend.verify_download(candidate)
            backend.unchanged()
            after = backend.quota()
            need(after["free"] >= (after["total"] + 4) // 5, "account_reserve_breached")
            backend.unchanged()
            receipt = backend.receipt(candidate, manifest, after)
            backend.status("complete", {"receipt_sha256": receipt,
                                         "native_restore_accepted": False})
            return {"receipt_sha256": receipt, "native_restore_accepted": False}
        except BaseException as exc:
            category = str(exc) if isinstance(exc, PublishError) and str(exc) in (
                "candidate_capacity_refused", "account_reserve_breached") else "publication_requires_reconciliation"
            backend.status("failed", {"category": category})
            raise PublishError("candidate_publication_refused") from None


def identity(meta):
    return (meta.st_dev, meta.st_ino, meta.st_uid, meta.st_gid, meta.st_mode,
            meta.st_nlink, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)


def digest_file(path):
    # Archive hashing is streaming; directories are no-follow checked by bundle.
    path = Path(path)
    with bundle._directory(path.parent):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
                 and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                 and 0 < before.st_size <= policy.NAMESPACE_BUDGET)
            digest = hashlib.sha256(); size = 0
            while True:
                raw = os.read(fd, 1024**2)
                if not raw: break
                size += len(raw); need(size <= policy.NAMESPACE_BUDGET); digest.update(raw)
            need(size == before.st_size and identity(os.fstat(fd)) == identity(before)
                 and identity(path.lstat()) == identity(before))
            return digest.hexdigest(), size
        finally:
            os.close(fd)


def private_json(path):
    raw = hydrate.protected(path, 0o600)[0]
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result); result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    need(bundle.canonical(value) == raw)
    return value


def pg_config():
    """Fixed protected PG config permits its reviewed root:postgres 0640 mode."""
    import grp
    path = Path("/etc/pgbackrest/pgbackrest.conf")
    for parent in path.parents:
        meta = parent.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022)
    with bundle._directory(path.parent):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                 and (before.st_gid == 0 and stat.S_IMODE(before.st_mode) == 0o600
                      or before.st_gid == grp.getgrnam("postgres").gr_gid
                      and stat.S_IMODE(before.st_mode) == 0o640)
                 and before.st_size <= 65536)
            raw = os.read(fd, 65537); need(len(raw) <= 65536)
            need(identity(os.fstat(fd)) == identity(before)
                 and identity(path.lstat()) == identity(before))
            return raw, identity(before)
        finally:
            os.close(fd)


def require_encryption(raw):
    """Validate presence only; never return, hash or report the passphrase."""
    pg = configparser.ConfigParser(interpolation=None, strict=True)
    pg.read_string(raw.decode())
    need(not pg.defaults())
    effective = dict(pg["global"])
    if pg.has_section("careertrellis"): effective.update(pg["careertrellis"])
    need(not any(key.startswith("repo1-cipher-") for section in pg.sections()
                 if section not in ("global", "careertrellis") for key in pg[section]))
    need(not any("config-include-path" in pg[section] for section in pg.sections()))
    need(effective.get("repo1-cipher-type") == "aes-256-cbc"
         and bool(effective.get("repo1-cipher-pass", "").strip()), "unencrypted_repository")


class Native(hydrate.Native):
    def __init__(self):
        super().__init__(); self.pg_snapshot = None; self.pg_includes = None; self.publish_started = False

    def includes(self):
        path = Path("/etc/pgbackrest/conf.d")
        if not os.path.lexists(path): return None
        with bundle._directory(path):
            meta = path.lstat()
            need(meta.st_uid == 0 and not meta.st_mode & 0o022)
            names = tuple(sorted(child.name for child in path.iterdir()))
            need(not any(name.endswith(".conf") for name in names))
            return identity(meta), names

    def preflight(self):
        super().preflight()  # Fixed source helper pins; no R2 calls.
        self.pg_snapshot = pg_config()
        require_encryption(self.pg_snapshot[0])
        # Unknown includes could override the encryption contract: refuse them.
        self.pg_includes = self.includes()
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.read_string(self.config[0].decode())
        need(parser["onedrive"].get("type") == "onedrive")
        one = configparser.ConfigParser(interpolation=None)
        one["onedrive"] = dict(parser["onedrive"])
        value = io.StringIO(); one.write(value)
        import fcntl
        os.close(self.fd)
        self.fd = os.memfd_create("repository-destination-config", os.MFD_ALLOW_SEALING)
        raw = value.getvalue().encode(); need(os.write(self.fd, raw) == len(raw))
        os.fchmod(self.fd, 0o600); os.lseek(self.fd, 0, 0)
        fcntl.fcntl(self.fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK
                    | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)

    def unchanged(self):
        super().unchanged()
        if self.pg_snapshot is not None:
            need(pg_config() == self.pg_snapshot and self.includes() == self.pg_includes)

    def candidate(self, transaction):
        root = Path(plan.ROOT) / transaction
        with bundle._directory(root):
            meta = root.lstat()
            need(meta.st_uid == meta.st_gid == 0 and stat.S_IMODE(meta.st_mode) == 0o700)
            intent = private_json(root / "intent.json")
            need(intent["transaction"] == transaction
                 and intent["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip())
            names = sorted(path.name for path in root.iterdir() if re.fullmatch(r"status-[0-9]{2}\.json", path.name))
            need(names and names == ["status-%02d.json" % n for n in range(1, len(names)+1)])
            status = private_json(root / names[-1])
            need(status["phase"] == "complete" and status["transaction"] == transaction
                 and status["pid"] == intent["pid"] and status["boot_id"] == intent["boot_id"]
                 and status["process_start_ticks"] == intent["process_start_ticks"])
            unit = "careertrellis-onedrive-bundle-hydrate-" + transaction + ".service"
            raw = hydrate.command(["/usr/bin/systemctl", "show", unit,
                "--property=ActiveState,MainPID,ControlPID,Job,Result,ExecMainStatus,ExecMainPID,ControlGroup"],
                output=True, timeout=10)
            state = dict(line.split("=", 1) for line in raw.decode().splitlines())
            need(state["ActiveState"] == "inactive" and state["MainPID"] == state["ControlPID"] == "0"
                 and state["Job"] == "" and state["Result"] == "success" and state["ExecMainStatus"] == "0"
                 and state["ExecMainPID"] == str(intent["pid"]))
            group = state["ControlGroup"]
            if group:
                need(group == "/system.slice/" + unit)
                cgroup = Path("/sys/fs/cgroup") / group.lstrip("/")
                if cgroup.exists():
                    import itertools
                    groups = list(itertools.islice(cgroup.rglob("cgroup.procs"), 65))
                    need(len(groups) <= 64 and all(not p.read_text().strip() for p in groups))
            result = status["result"]
            generation = private_json(root / "generation.json"); plan.validate_generation(generation)
            need(result["generation_sha256"] == hashlib.sha256(bundle.canonical(generation)).hexdigest()
                 and result["backup_label"] == generation["backup_label"])
            digest, size = digest_file(root / "repository.tar")
            need(result["sha256"] == digest and result["archive_bytes"] == size)
            manifest = private_json(root / "bundle-manifest.json"); bundle.validate_manifest(manifest)
            need(manifest["generation_sha256"] == result["generation_sha256"]
                 and manifest["backup_label"] == result["backup_label"])
            self.root = root; self.transaction = transaction
            return {"archive_sha256": digest, "archive_bytes": size,
                "generation_sha256": result["generation_sha256"], "backup_label": result["backup_label"]}

    def begin_publish(self, candidate):
        self.base = {"schema": "careertrellis.onedrive-publication/v1", "transaction": self.transaction,
            "pid": os.getpid(), "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "process_start_ticks": int(Path("/proc/self/stat").read_text().rsplit(") ", 1)[1].split()[19]),
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), **candidate}
        hydrate.exclusive(self.root / "publish-intent.json", bundle.canonical(self.base))
        self.publish_started = True

    def status(self, phase, result=None):
        if not self.publish_started: return  # Never alter another attempt's journal.
        self.sequence += 1
        value = dict(self.base, phase=phase, observed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        if result is not None: value["result"] = result
        hydrate.exclusive(self.root / ("publish-status-%02d.json" % self.sequence), bundle.canonical(value))

    def read_remote(self, args):
        self.unchanged()
        return hydrate.command([plan.BINARY, *args, "--config", "/proc/self/fd/" + str(self.fd),
            "--log-level", "ERROR", "--stats", "0", "--retries", "1",
            "--low-level-retries", "1", "--contimeout", "15s", "--timeout", "45s"], descriptor=self.fd,
            lock_descriptor=self.lock_fd, output=True, timeout=600)

    def quota(self):
        value = json.loads(self.read_remote(["about", "onedrive:", "--json"]))
        need(type(value) is dict and type(value.get("total")) is int
             and type(value.get("free")) is int and 0 <= value["free"] <= value["total"]
             and value["total"] > 0)
        return {"total": value["total"], "free": value["free"]}

    def capacity_facts(self, candidate):
        parent = "onedrive:job-applier-staging"
        rows = json.loads(self.read_remote(["lsjson", parent, "--dirs-only", "--no-modtime", "--no-mimetype"]))
        need(type(rows) is list and len(rows) <= 10000)
        namespace = []
        for row in rows:
            need(type(row) is dict and type(row.get("Path")) is str and row.get("IsDir") is True)
            if row["Path"].casefold() == "postgresql-bundles":
                need(row["Path"] == "postgresql-bundles")
                namespace.append(row)
        need(len(namespace) <= 1)
        size = 0; paths = set()
        if namespace:
            rows = json.loads(self.read_remote(["lsjson", plan.DESTINATION, "--recursive", "--files-only",
                                               "--no-modtime", "--no-mimetype"]))
            need(type(rows) is list and len(rows) <= 10000)
            for row in rows:
                path, count = row.get("Path"), row.get("Size")
                need(type(path) is str and re.fullmatch(r"[0-9a-f]{64}/repository\.tar", path)
                     and path not in paths and row.get("IsDir") is False and type(count) is int and count >= 0)
                paths.add(path); size += count
        need(candidate["archive_sha256"] + "/repository.tar" not in paths)
        return dict(self.quota(), namespace_bytes=size)

    def transfer(self, phase, source, destination, hard_delete=False):
        self.unchanged()
        intent = dict(self.base, phase=phase, dispatched_at=dt.datetime.now(dt.timezone.utc).isoformat())
        hydrate.exclusive(self.root / ("publish-" + phase + "-intent.json"), bundle.canonical(intent))
        args = [plan.BINARY, "copyto", source, destination, "--immutable", "--config",
            "/proc/self/fd/" + str(self.fd), "--log-level", "ERROR", "--stats", "0",
            "--retries", "1", "--low-level-retries", "1", "--transfers", "1",
            "--checkers", "2", "--contimeout", "15s", "--timeout", "45s",
            "--max-size", "512M", "--max-transfer", "512M", "--cutoff-mode", "HARD"]
        if hard_delete: args.append("--onedrive-hard-delete=false")
        started = time.monotonic()
        with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=hydrate.ENV, pass_fds=(self.fd, self.lock_fd)) as child:
            try:
                ticks = int(Path("/proc/%d/stat" % child.pid).read_text().rsplit(") ", 1)[1].split()[19])
                hydrate.exclusive(self.root / ("publish-" + phase + "-child.json"),
                    bundle.canonical({"pid": child.pid, "process_start_ticks": ticks,
                                      "boot_id": self.base["boot_id"]}))
                code = child.wait(timeout=18000)
                hydrate.exclusive(self.root / ("publish-" + phase + "-terminal.json"),
                    bundle.canonical({"pid": child.pid, "returncode": code,
                                      "elapsed_seconds": int(time.monotonic()-started)}))
                need(code == 0)
            finally:
                if child.poll() is None: child.kill(); child.wait()
                terminal = self.root / ("publish-" + phase + "-terminal.json")
                if not os.path.lexists(terminal):
                    hydrate.exclusive(terminal, bundle.canonical({"pid": child.pid,
                        "returncode": child.returncode,
                        "elapsed_seconds": int(time.monotonic()-started)}))

    def target(self, candidate):
        return plan.DESTINATION + "/" + candidate["archive_sha256"] + "/repository.tar"

    def upload(self, candidate):
        need(digest_file(self.root / "repository.tar") ==
             (candidate["archive_sha256"], candidate["archive_bytes"]))
        self.transfer("upload", str(self.root / "repository.tar"), self.target(candidate), True)

    def download(self, candidate):
        need(not os.path.lexists(self.root / "downloaded-repository.tar"))
        self.transfer("download", self.target(candidate), str(self.root / "downloaded-repository.tar"))

    def verify_download(self, candidate):
        need(digest_file(self.root / "downloaded-repository.tar") ==
             (candidate["archive_sha256"], candidate["archive_bytes"]))
        manifest = bundle.extract_bundle(self.root / "downloaded-repository.tar",
            self.root / "downloaded-repository", candidate["archive_sha256"])
        need(manifest == private_json(self.root / "bundle-manifest.json"))
        hydrate.exclusive(self.root / "downloaded-manifest.json", bundle.canonical(manifest))
        return manifest

    def receipt(self, candidate, manifest, after):
        value = {"schema": "careertrellis.onedrive-bundle-download/v1", "result": "download_verified",
            "transaction": self.transaction, **candidate,
            "manifest_sha256": hashlib.sha256(bundle.canonical(manifest)).hexdigest(),
            "native_restore_accepted": False, "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "destination": self.target(candidate), "quota_after": after}
        raw = bundle.canonical(value)
        hydrate.exclusive(self.root / "downloaded-receipt.json", raw)
        return hashlib.sha256(raw).hexdigest()


def main():
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument("--transaction", required=True)
    args = parser.parse_args()
    try:
        result = publish(args.transaction, Native())
        print(json.dumps({"result": "candidate_download_verified", **result}, sort_keys=True)); return 0
    except BaseException:
        print('{"result":"refused","category":"publication_requires_reconciliation"}'); return 1


if __name__ == "__main__": raise SystemExit(main())
