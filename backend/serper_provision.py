"""Operator-started, one-time Serper credential and finite-credit provisioning.

Run only in the operator's visible secure terminal. Never accepts a key in argv,
stdin, environment, logs or receipts. Does not enable Serper or restart services.
"""
import json
import os
from pathlib import Path
import re
import stat
import sys


def require(condition):
    if not condition:
        raise RuntimeError('Serper provisioning refused; preserve state for inspection.')


def protected_parent(path):
    require(path.resolve(strict=True) == path)
    for parent in (path, *path.parents):
        info = parent.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def exclusive_file(path, content, mode, uid=0, gid=0):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(content)
        output.flush()
        os.fchmod(output.fileno(), mode)
        os.fchown(output.fileno(), uid, gid)
        os.fsync(output.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main():
    import fcntl
    import grp
    import hashlib
    import pwd
    import resource
    import socket
    from uuid import uuid4

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    require(os.geteuid() == 0 and socket.gethostname() == 'careertrellis-staging-web-1')
    require(len(sys.argv) == 2 and sys.argv[1] == 'prepare')
    source = Path(__file__).resolve().parent
    protected_parent(source)
    sys.path.insert(0, str(source))
    from identity_entry import SecureTerminal
    from serper_budget import POLICY_PATH, LEDGER_PATH, create_ledger, host_identity

    config_root = POLICY_PATH.parent
    protected_parent(config_root)
    environment = config_root / 'serper-worker.env'
    lock_path = Path('/run/lock/careertrellis-maintenance.lock')
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(lock_fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(all(not p.exists() and not p.is_symlink() for p in (environment, POLICY_PATH, LEDGER_PATH.parent)))
        worker = pwd.getpwnam('careertrellis-worker')
        group = grp.getgrnam('careertrellis').gr_gid
        parent = LEDGER_PATH.parent.parent
        info = parent.lstat()
        require(parent.resolve(strict=True) == parent and stat.S_ISDIR(info.st_mode)
                and info.st_uid == worker.pw_uid and not info.st_mode & 0o022)
        # The observed allowance is a ceiling. Reconciliation is required if
        # the balance changed since the operator's dashboard check.
        with SecureTerminal() as terminal:
            balance = terminal.ask('Current Serper credits left (maximum 2494)', limit=4)
            require(re.fullmatch(r'[1-9][0-9]{0,3}', balance) is not None and int(balance) <= 2494)
            confirmation = terminal.ask('Type NO-TOPUPS after confirming auto top-ups are off', limit=9)
            require(confirmation == 'NO-TOPUPS')
            key = terminal.ask('Existing Serper API key', limit=256)
            require(re.fullmatch(r'[A-Za-z0-9_-]{20,256}', key) is not None)
        allowance = int(balance)
        policy = dict(version=1, allocation=str(uuid4()), host=host_identity(),
                      key_digest=hashlib.sha256(key.encode()).hexdigest(), lifetime=allowance,
                      daily=min(300, allowance), owner_daily=min(60, allowance))
        # Create policy first; any interruption leaves recognizable state and
        # an automatic retry refuses rather than creating another allocation.
        exclusive_file(POLICY_PATH, (json.dumps(policy, sort_keys=True) + '\n').encode(), 0o640, gid=group)
        LEDGER_PATH.parent.mkdir(mode=0o700)
        create_ledger(LEDGER_PATH, policy)
        os.chown(LEDGER_PATH, worker.pw_uid, group)
        os.chown(LEDGER_PATH.parent, worker.pw_uid, group)
        exclusive_file(environment, ('CAREERTRELLIS_SERPER_ENABLED=0\nCAREERTRELLIS_SERPER_API_KEY=' + key + '\n').encode(), 0o600)
        del key
        print(json.dumps({'status': 'serper_prepared_disabled', 'lifetime_credits': allowance,
                          'daily_requests': policy['daily'], 'owner_daily_requests': policy['owner_daily']}))
    finally:
        os.close(lock_fd)


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print('Serper provisioning refused or interrupted; do not repeat until state is inspected.', file=sys.stderr)
        sys.exit(1)
