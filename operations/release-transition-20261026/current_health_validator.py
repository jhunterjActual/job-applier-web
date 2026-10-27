"""Read-only current health evidence from exact installed reviewed cores.

Never calls the shell health wrapper: its DB status route may write state.
Must run on the fixed host while the participant owns the canonical fences.
No status refresh, provider traversal, backup, restore, timer or service write.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import types

CORE = Path('/usr/local/libexec/careertrellis/backup_health.py')
STATUS_ROOTS = {
    'web': Path('/var/lib/careertrellis/backup-health/objects'),
    'db': Path('/var/lib/careertrellis/backup-health/database'),
}
SPECS = {
    'web': ('careertrellis-staging-web-1', 'objects',
            'bb2434763298845c0b44fcc5793fdc7f3d4521174e3691e60cf283f5d8fc8f6c'),
    'db': ('job-applier-db-staging-01', 'database',
           '8d2facf6d71bf985c1c0ffe8b6a661fc9a9699548b6bb81163cd787f53ba97fa'),
}


class HealthRefused(RuntimeError):
    pass


def need(value):
    if not value:
        raise HealthRefused('current_health_refused')


def stamp(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(value.tzinfo is not None)
    return value


def protected_source():
    def identity(s):
        return tuple(getattr(s, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
                     'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))
    for p in (CORE.parent, *CORE.parent.parents):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022)
    before = CORE.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
         and stat.S_IMODE(before.st_mode) == 0o644 and before.st_size < 1048576)
    fd = os.open(CORE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        need(identity(os.fstat(fd)) == identity(before))
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(1048577)
        need(len(raw) == before.st_size and identity(os.fstat(fd)) == identity(before)
             and identity(CORE.lstat()) == identity(before))
        return raw
    finally:
        os.close(fd)


def read_surface(role):
    """Authenticate canonical status AND all committed receipt generations.

    Refuses if any receipt postdates the evaluation, even a successful receipt:
    existing evaluator must evaluate it normally before deployment can proceed.
    This preserves latest-failure/invalid-inventory behavior without rewriting
    status or inventing a snapshot. No claim of current WAL health is added.
    """
    need(role in SPECS and os.geteuid() == 0 and sys.platform == 'linux')
    host, surface, expected = SPECS[role]
    need(socket.gethostname() == host)
    raw = protected_source()
    need(hashlib.sha256(raw).hexdigest() == expected)
    name = '_exact_installed_health_' + role
    module = types.ModuleType(name)
    module.__file__ = str(CORE)
    sys.modules[name] = module
    exec(compile(raw, str(CORE), 'exec'), module.__dict__)
    now = datetime.now(timezone.utc)
    status = module.checked_status(root=STATUS_ROOTS[role], surface=surface, now=now)
    need(status['result'] == 'ok' and status['failure_codes'] == []
         and status['failure_count'] == 0 and status['surface'] == surface)
    evaluated = stamp(status['evaluated_at'])
    need(0 <= (now - evaluated).total_seconds() <= 10800)
    inventory = module.scan_receipts()
    operations = module.SURFACE_OPERATIONS[surface]
    need(not set(inventory.invalid_operations).intersection(operations))
    summaries = {}
    for operation in operations:
        rows = inventory.receipts[operation]
        need(bool(rows))
        need(all(stamp(row['completed_at']) <= evaluated for row in rows))
        latest = max(rows, key=lambda row: stamp(row['completed_at']))
        latest_time = stamp(latest['completed_at'])
        need(all(row['result'] == 'ok' for row in rows
                 if stamp(row['completed_at']) == latest_time))
        summaries[operation] = {k: latest[k] for k in
                                ('result', 'completed_at', 'backup_label') if k in latest}
        summaries[operation]['receipt_sha256'] = hashlib.sha256(
            module.serialize_operation_receipt(latest)).hexdigest()
    again = module.scan_receipts()
    need(again == inventory and module.checked_status(
        root=STATUS_ROOTS[role], surface=surface, now=now) == status)
    result = {'schema': 'careertrellis.current-health-readback/v1', 'role': role,
              'host': host, 'observed_at': now.isoformat(), 'surface': surface,
              'core_sha256': expected, 'status': status, 'latest_receipts': summaries,
              'inventory_verified': True, 'status_generation_current': True,
              'repository_label_observed': False}
    if role == 'db':
        # Immutable accepted full label is not the same as current repository
        # metadata. The primary/participant must supply a fresh independently
        # verified pgBackRest info observation; this reader does not fabricate it.
        result['latest_accepted_full_label'] = summaries['full_backup']['backup_label']
    return result
