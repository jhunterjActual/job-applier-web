"""Single-host operational credit budget, separate from application storage.

Provision once as an operator. Runtime never creates, refills or resets state.
This is not a security boundary against a compromised worker owning the key.
Keep the ledger across releases; never restore/clone it with Serper enabled.
"""

from datetime import datetime, timezone
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import stat
from uuid import UUID

POLICY_PATH = Path('/etc/careertrellis/serper-budget.json')
LEDGER_PATH = Path('/var/lib/careertrellis/worker/serper/budget.sqlite3')
POLICY_FIELDS = {'version', 'allocation', 'host', 'key_digest', 'lifetime', 'daily', 'owner_daily'}
SCHEMA = """
CREATE TABLE allocation (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    identity TEXT NOT NULL, used INTEGER NOT NULL CHECK(used>=0),
    last_day TEXT NOT NULL
);
CREATE TABLE daily (day TEXT PRIMARY KEY, used INTEGER NOT NULL CHECK(used>=0));
CREATE TABLE owner_daily (
    owner INTEGER NOT NULL CHECK(owner>0), day TEXT NOT NULL,
    used INTEGER NOT NULL CHECK(used>=0), PRIMARY KEY(owner,day)
);
CREATE TABLE runs (
    run TEXT PRIMARY KEY, owner INTEGER NOT NULL CHECK(owner>0),
    used INTEGER NOT NULL CHECK(used BETWEEN 1 AND 6)
);
"""


class BudgetUnavailable(Exception):
    """No provider request may be sent without a durable reservation."""


def host_identity():
    return Path('/etc/machine-id').read_text(encoding='ascii').strip()


def policy_identity(policy):
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS or policy['version'] != 1:
        raise BudgetUnavailable()
    if str(UUID(policy['allocation'])) != policy['allocation']:
        raise BudgetUnavailable()
    if not isinstance(policy['host'], str) or not policy['host']:
        raise BudgetUnavailable()
    if not isinstance(policy['key_digest'], str) or len(policy['key_digest']) != 64:
        raise BudgetUnavailable()
    for field in ('lifetime', 'daily', 'owner_daily'):
        if type(policy[field]) is not int or not 1 <= policy[field] <= 1_000_000:
            raise BudgetUnavailable()
    if not policy['owner_daily'] <= policy['daily'] <= policy['lifetime']:
        raise BudgetUnavailable()


def create_ledger(path, policy):
    """Offline provisioning only; an existing file is never replaced."""
    validate_policy(policy)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    # Failed initialization leaves the file in place and runtime refuses it.
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('PRAGMA synchronous=FULL')
        connection.executescript(SCHEMA)
        connection.execute('INSERT INTO allocation VALUES(1, ?, 0, ?)',
                           (policy_identity(policy), '0000-00-00'))
        connection.commit()
    if os.name == 'posix':
        descriptor = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def validate_runtime_paths(policy_path, ledger_path):
    if os.name != 'posix':
        raise BudgetUnavailable()
    for path in (policy_path, ledger_path):
        if path.resolve(strict=True) != path:
            raise BudgetUnavailable()
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BudgetUnavailable()
    policy_stat = policy_path.stat()
    ledger_stat = ledger_path.stat()
    if policy_stat.st_uid != 0 or policy_stat.st_mode & 0o022:
        raise BudgetUnavailable()
    if ledger_stat.st_uid != os.geteuid() or ledger_stat.st_mode & 0o077:
        raise BudgetUnavailable()
    for parent_path in policy_path.parents:
        parent_info = parent_path.stat()
        if parent_info.st_uid != 0 or parent_info.st_mode & 0o022:
            raise BudgetUnavailable()
    for parent_path in ledger_path.parents:
        parent_info = parent_path.stat()
        if parent_info.st_uid not in (0, os.geteuid()) or parent_info.st_mode & 0o022:
            raise BudgetUnavailable()
    parent = ledger_path.parent.stat()
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise BudgetUnavailable()


def reserve(owner, run_id, key, checkpoint, *, policy_path=POLICY_PATH,
            ledger_path=LEDGER_PATH, now=None):
    """Commit one credit before HTTP; false means no transport, never retry here.

    Owner/run come only from the PostgreSQL claim. Lease exceptions propagate.
    Failed and uncertain HTTP calls are not refunded. Daily rows and run IDs are
    bounded by the finite allocation and retained until explicit retirement.
    """
    checkpoint()
    connection = None
    try:
        if type(owner) is not int or owner <= 0 or str(UUID(run_id)) != run_id:
            return False
        if not isinstance(key, str) or not key.strip():
            return False
        validate_runtime_paths(policy_path, ledger_path)
        if policy_path.stat().st_size > 4096 or ledger_path.stat().st_size > 64 * 1024**2:
            return False
        policy = json.loads(policy_path.read_text(encoding='utf-8'))
        validate_policy(policy)
        if policy['host'] != host_identity() or not hmac.compare_digest(
                policy['key_digest'], hashlib.sha256(key.encode()).hexdigest()):
            return False
        connection = sqlite3.connect(ledger_path.as_uri() + '?mode=rw', uri=True, timeout=2)
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('BEGIN IMMEDIATE')
        # Recheck the live claim after waiting for the local budget lock.
        checkpoint()
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            return False
        day = current.astimezone(timezone.utc).date().isoformat()
        allocation = connection.execute('SELECT identity,used,last_day FROM allocation WHERE singleton=1').fetchone()
        if (not allocation or allocation[0] != policy_identity(policy) or
                type(allocation[1]) is not int or not 0 <= allocation[1] < policy['lifetime'] or
                not isinstance(allocation[2], str) or day < allocation[2]):
            return False
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            return False
        # All counter dimensions describe the same retained reservations. Lost
        # rows must not silently recreate daily or per-run allowance.
        for table in ('daily', 'owner_daily', 'runs'):
            total = connection.execute('SELECT coalesce(sum(used),0) FROM ' + table).fetchone()[0]
            if total != allocation[1]:
                return False
        if connection.execute('SELECT count(*) FROM allocation').fetchone()[0] != 1:
            return False
        daily = connection.execute('SELECT used FROM daily WHERE day=?', (day,)).fetchone()
        owned = connection.execute('SELECT used FROM owner_daily WHERE owner=? AND day=?', (owner, day)).fetchone()
        run = connection.execute('SELECT owner,used FROM runs WHERE run=?', (run_id,)).fetchone()
        if any(row and (type(row[0]) is not int or row[0] < 0) for row in (daily, owned)):
            return False
        if ((daily and daily[0] >= policy['daily']) or
                (owned and owned[0] >= policy['owner_daily']) or
                (run and (run[0] != owner or type(run[1]) is not int or not 1 <= run[1] < 6))):
            return False
        connection.execute('UPDATE allocation SET used=used+1,last_day=? WHERE singleton=1', (day,))
        connection.execute('INSERT INTO daily VALUES(?,1) ON CONFLICT(day) DO UPDATE SET used=used+1', (day,))
        connection.execute('INSERT INTO owner_daily VALUES(?,?,1) ON CONFLICT(owner,day) DO UPDATE SET used=used+1', (owner, day))
        connection.execute('INSERT INTO runs VALUES(?,?,1) ON CONFLICT(run) DO UPDATE SET used=used+1', (run_id, owner))
        connection.commit()
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, BudgetUnavailable):
        return False
    finally:
        if connection is not None:
            connection.close()
    # A cancellation here consumes the reservation conservatively.
    checkpoint()
    return True
