"""Bounded DB-host admission cleanup and independent, token-free health state.

Operations-only: no application imports, credentials, provider calls or schema
changes. Invoke from the reviewed immutable root-owned launcher, never at import.
"""
from contextlib import contextmanager
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

import admission_operations as ops


STATE = Path('/var/lib/careertrellis/admission-purge')
LOCK = STATE / 'purge.lock'
TABLES = ('flows', 'http_budgets', 'http_refresh_claims', 'http_revocations')
BATCH = 500
ROUNDS = 4
RUN_SECONDS = 90
GRACE_SECONDS = 120
SCHEMA = 'careertrellis.admission-purge/v1'
SCHEMA_V2 = 'careertrellis.admission-purge/v2'
RESULTS = ('running', 'ok', 'backlog', 'failed')


def schema_gate_sql():
    # 0014 retains its accepted contract. 0015/0016 must have completed closed-v2
    # release and populated its private identity ledger before cleanup can run.
    # PL/pgSQL plans the identity branch only on 0015/0016 (absent on 0014).
    return ("DO $$ DECLARE head text; BEGIN SELECT version_num INTO STRICT head FROM careertrellis.alembic_version; "
        "IF head NOT IN ('20260907_0014','20260909_0015','20260915_0016') OR head IS NULL "
        "OR (SELECT maintenance FROM careertrellis_admission.policy) IS DISTINCT FROM false "
        "THEN RAISE EXCEPTION 'purge unavailable'; END IF; "
        "IF head IN ('20260909_0015','20260915_0016') THEN "
        "IF (SELECT minimum_session_version=2 AND adapter_ready FROM careertrellis_admission.policy) IS DISTINCT FROM true "
        "OR (SELECT binding FROM careertrellis_admission.identity_provider) IS NULL "
        "OR EXISTS(SELECT 1 FROM careertrellis.app_users u LEFT JOIN careertrellis_admission.identity_accounts a ON a.id=u.id WHERE a.id IS NULL) "
        "THEN RAISE EXCEPTION 'purge identity contract unavailable'; END IF; END IF; END $$; ")


def command(arguments, *, data=None):
    result = subprocess.run(arguments, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'}, timeout=8)
    ops.require(result.returncode == 0)
    return result.stdout


class LocalPostgres:
    def query(self, sql):
        output = command(['/usr/sbin/runuser','-u','postgres','--','/usr/bin/psql',
            '-X','-qAt','-v','ON_ERROR_STOP=1','-h','/var/run/postgresql','-p','5432','-d','careertrellis'],
            data=("SET log_statement='none'; SET log_min_error_statement='panic'; "
                "SET log_parameter_max_length=0; SET log_parameter_max_length_on_error=0; "
                "SET standard_conforming_strings=on; SET statement_timeout='4s'; SET lock_timeout='500ms'; " + sql).encode())
        lines = output.decode().strip().splitlines()
        if not lines:
            return None
        return {'t':True,'f':False}[lines[-1]] if lines[-1] in ('t','f') else ops.strict_json(lines[-1])


def integer(value):
    return type(value) is int and value >= 0


def validate(value, source, now):
    ops.require(isinstance(value, dict))
    fields = {'schema', 'source', 'initialized_at', 'last_success', 'last_attempt', 'last_failure'}
    if value.get('schema') == SCHEMA_V2:
        fields |= {'source_bound_at', 'source_success'}
        ops.require(integer(value.get('source_bound_at')) and value['source_bound_at'] <= now)
        fresh = value.get('source_success')
        ops.require(fresh is None or (integer(fresh) and value['source_bound_at'] <= fresh <= now
            and fresh == value.get('last_success')))
    ops.require(set(value) == fields)
    ops.require(value['schema'] in (SCHEMA, SCHEMA_V2) and value['source'] == source
        and integer(value['initialized_at']) and value['initialized_at'] <= now)
    success = value['last_success']
    ops.require(success is None or (integer(success) and value['initialized_at'] <= success <= now))
    for name in ('last_attempt', 'last_failure'):
        record = value[name]
        if record is None:
            continue
        ops.require(isinstance(record, dict) and set(record) == {'started_at', 'finished_at', 'result', 'deleted'})
        ops.require(integer(record['started_at']) and value['initialized_at'] <= record['started_at'] <= now
            and record['result'] in RESULTS and isinstance(record['deleted'], dict)
            and set(record['deleted']) == set(TABLES))
        ops.require(all((n is None if record['result'] == 'failed' else integer(n) and n <= BATCH * ROUNDS)
            for n in record['deleted'].values()))
        if record['result'] == 'running':
            ops.require(record['finished_at'] is None and name == 'last_attempt')
        else:
            ops.require(integer(record['finished_at']) and record['started_at'] <= record['finished_at'] <= now)
        if name == 'last_failure':
            ops.require(record['result'] in ('failed', 'backlog'))
    if value['schema'] == SCHEMA_V2:
        ops.require(value['initialized_at'] <= value['source_bound_at'])
        fresh = value['source_success']
        attempt = value['last_attempt']
        if fresh is not None:
            ops.require(attempt is not None and fresh <= attempt['started_at']
                and (attempt['result'] != 'ok' or fresh == attempt['started_at']))
    return value


def rebind_pending():
    path = STATE / 'rebind-pending.json'
    return path.exists() or path.is_symlink()


def read_state(source, now):
    return validate(ops.strict_json(ops.read_protected(STATE / 'state.json')), source, now)


def health(value, now):
    """Hourly UTC slots; only completed, fully drained runs count as success."""
    last = value['last_success']
    baseline = value['initialized_at'] if last is None else last
    missed = max(0, (now - GRACE_SECONDS) // 3600 - baseline // 3600)
    issues = []
    if value['schema'] == SCHEMA_V2 and value['source_success'] is None:
        issues.append('source_unverified')
    if missed >= 2:
        issues.append('two_hourly_runs_missed')
    attempt = value['last_attempt']
    failure = value['last_failure']
    if failure and (last is None or last <= failure['started_at']):
        issues.append('expired_backlog_remaining' if failure['result'] == 'backlog' else 'last_run_failed')
    if attempt:
        if attempt['result'] == 'backlog':
            if 'expired_backlog_remaining' not in issues:
                issues.append('expired_backlog_remaining')
        elif attempt['result'] == 'failed':
            if 'last_run_failed' not in issues:
                issues.append('last_run_failed')
        elif attempt['result'] == 'running' and now - attempt['started_at'] > RUN_SECONDS + 30:
            issues.append('run_incomplete')
    return {'schema': value['schema'], 'source': value['source'], 'checked_at': now,
        'result': 'alert' if issues else 'ok', 'issues': issues}


def batch_sql(table):
    ops.require(table in TABLES)
    # Same lifecycle lock as account admission and maintenance. HTTP lanes
    # acquire only their own lock today; take them after lifecycle, never reverse.
    lane = ('careertrellis-http-budget-v1' if table == 'http_budgets'
        else 'careertrellis-http-session-v1' if table != 'flows' else None)
    locks = "SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-admission-maintenance-v1',0)); " \
        "SELECT careertrellis_admission.lock_lifecycle(); "
    if lane:
        locks += "SELECT pg_advisory_xact_lock(hashtextextended('" + lane + "',0)); "
    predicate = "expires < statement_timestamp()"
    if table == 'flows':
        predicate += ' AND deadline < statement_timestamp()'
    # No caller-controlled SQL names/cutoff/limits, no raw rows returned. LIMIT
    # bounds deletes, while statement/transaction timeouts bound scan and lock work.
    return ("BEGIN; SET LOCAL statement_timeout='4s'; SET LOCAL lock_timeout='500ms'; "
        "SET LOCAL transaction_timeout='6s'; " + locks +
        schema_gate_sql() +
        'WITH candidates AS MATERIALIZED (SELECT ctid FROM careertrellis_admission.' + table +
        ' WHERE ' + predicate + ' LIMIT 500 FOR UPDATE), deleted AS ('
        'DELETE FROM careertrellis_admission.' + table + ' t USING candidates c WHERE t.ctid=c.ctid '
        'RETURNING 1) SELECT json_build_object(\'deleted\',count(*)) FROM deleted; COMMIT;')


def purge(db, *, monotonic=time.monotonic):
    start = monotonic()
    deleted = dict.fromkeys(TABLES, 0)
    pending = set(TABLES)
    for _ in range(ROUNDS):
        for table in TABLES:
            if table not in pending:
                continue
            if monotonic() - start >= RUN_SECONDS - 8:
                return deleted, 'backlog'
            answer = db.query(batch_sql(table))
            ops.require(isinstance(answer, dict) and set(answer) == {'deleted'}
                and integer(answer['deleted']) and answer['deleted'] <= BATCH)
            count = answer['deleted']
            deleted[table] += count
            if count < BATCH:
                pending.remove(table)
        if not pending:
            return deleted, 'ok'
    # Conservatively alert on a full final batch. Do not scan or delete more
    # merely to distinguish exactly-at-limit from residual backlog.
    return deleted, 'backlog'


@contextmanager
def state_lock(path=None):
    import fcntl
    path = LOCK if path is None else path
    ops.protected_parents(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        item = os.fstat(fd)
        ops.require(stat.S_ISREG(item.st_mode) and item.st_uid == 0 and item.st_nlink == 1
            and stat.S_IMODE(item.st_mode) == 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def initialize(source, now):
    ops.require(not rebind_pending())
    ops.require(not (STATE / 'state.json').exists())
    ops.write_protected(STATE / 'state.json', ops.encoded({'schema': SCHEMA, 'source': source,
        'initialized_at': now, 'last_success': None, 'last_attempt': None, 'last_failure': None}))


def run(source, db, *, clock=time.time):
    ops.require(not rebind_pending())
    now = math.floor(clock())
    value = read_state(source, now)
    previous = value['last_attempt']
    # A killed or ambiguous attempt is not blindly retried, even on the next
    # timer tick. A separate acknowledge command requires terminal-state review.
    ops.require(previous is None or previous['result'] != 'running')
    attempt = {'started_at': now, 'finished_at': None, 'result': 'running', 'deleted': dict.fromkeys(TABLES, 0)}
    value['last_attempt'] = attempt
    ops.state_write(STATE / 'state.json', value)
    try:
        with ops.host_locks():
            # Local socket postgres only; no URL, key, broker login or provider.
            ops.require(db.query("SELECT current_database()='careertrellis' AND session_user='postgres' "
                "AND current_setting('server_version_num')::int BETWEEN 180000 AND 189999 "
                "AND current_setting('data_directory')='/var/lib/postgresql/18/main';") is True)
            attempt['deleted'], attempt['result'] = purge(db)
    except BaseException:
        # Counts in a failed run are not asserted: SQL may have committed before
        # acknowledgement was lost. Terminal failure never advances success.
        attempt['result'] = 'failed'
        attempt['deleted'] = dict.fromkeys(TABLES, None)
        raise
    finally:
        attempt['finished_at'] = math.floor(clock())
        ops.require(attempt['finished_at'] >= now)
        if attempt['result'] == 'ok':
            value['last_success'] = now
            if value['schema'] == SCHEMA_V2:
                value['source_success'] = now
        elif attempt['result'] in ('failed', 'backlog'):
            value['last_failure'] = dict(attempt)
        ops.state_write(STATE / 'state.json', value)
    return attempt['result']


def acknowledge(source, now):
    # This explicit operation does no SQL and grants no success credit.
    ops.require(not rebind_pending())
    value = read_state(source, now)
    ops.require(value['last_attempt'] is not None and value['last_attempt']['result'] == 'running')
    value['last_attempt']['result'] = 'failed'
    value['last_attempt']['finished_at'] = now
    value['last_attempt']['deleted'] = dict.fromkeys(TABLES, None)
    value['last_failure'] = dict(value['last_attempt'])
    ops.state_write(STATE / 'state.json', value)


def timer_issues(source):
    issues = []
    for name in ('purge', 'purge-monitor'):
        try:
            raw = command(['/usr/bin/systemctl', 'show', 'careertrellis-admission-' + name + '@' + source + '.timer',
                '--property=LoadState,ActiveState,UnitFileState']).decode()
            values = dict(line.split('=', 1) for line in raw.splitlines())
            if values != {'LoadState': 'loaded', 'ActiveState': 'active', 'UnitFileState': 'enabled'}:
                issues.append(name + '_timer_unavailable')
        except Exception:
            issues.append(name + '_timer_unavailable')
    return issues


def validate_health(value, source, now, *, max_age=None):
    ops.require(isinstance(value, dict) and set(value) == {'schema','source','checked_at','result','issues'}
        and value['schema'] in (SCHEMA, SCHEMA_V2) and value['source'] == source
        and integer(value['checked_at']) and value['checked_at'] <= now
        and (max_age is None or now-value['checked_at'] <= max_age)
        and value['result'] in ('ok','alert') and isinstance(value['issues'], list)
        and all(isinstance(code, str) and code in ('two_hourly_runs_missed','expired_backlog_remaining',
            'last_run_failed','run_incomplete','state_unavailable','purge_timer_unavailable',
            'purge-monitor_timer_unavailable') + (('source_unverified',) if value['schema'] == SCHEMA_V2 else ()) for code in value['issues'])
        and (value['result'] == 'alert') == bool(value['issues']))
    return value


def observe(source, now, *, persist):
    try:
        ops.require(not rebind_pending())
        result = health(read_state(source, now), now)
    except Exception:
        result = {'schema': SCHEMA, 'source': source, 'checked_at': now, 'result': 'alert', 'issues': ['state_unavailable']}
    result['issues'].extend(timer_issues(source))
    if not persist:
        try:
            saved = validate_health(ops.strict_json(ops.read_protected(STATE / 'health.json')), source, now, max_age=420)
            ops.require(saved['schema'] == result['schema'])
            if result['schema'] == SCHEMA_V2:
                result['issues'].extend(code for code in saved['issues'] if code not in result['issues'])
        except Exception:
            result['issues'].append('monitor_evidence_unavailable')
    result['result'] = 'alert' if result['issues'] else 'ok'
    if persist:
        ops.require(not rebind_pending())
        path = STATE / 'health.json'
        old = validate_health(ops.strict_json(ops.read_protected(path)), source, now) if path.exists() else None
        ops.state_write(path, result)
        # Log only changes; the fixed health record is always refreshed.
        if old is None or (old.get('result'), old.get('issues')) != (result['result'], result['issues']):
            print('admission_purge_health=' + result['result'] + ' issues=' + ','.join(result['issues']))
    else:
        print(ops.encoded(result).decode().strip())
    return 1 if result['issues'] else 0


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        ops.require(args in (['initialize'], ['run'], ['monitor'], ['status'], ['acknowledge']))
        ops.require(getattr(os, 'geteuid', lambda: -1)() == 0)
        source = Path(__file__).absolute()
        ops.require(source.parent.parent.parent == Path('/opt/careertrellis/staging/releases')
            and re.fullmatch('[0-9a-f]{40}', source.parent.parent.name))
        for name in ('admission_purge.py', 'admission_operations.py'):
            ops.read_protected(source.parent / name, modes=(0o600, 0o640, 0o644), limit=131072)
        ops.protected_parents(STATE / 'sentinel')
        ops.require(stat.S_IMODE(STATE.stat().st_mode) == 0o700)
        revision = source.parent.parent.name
        now = math.floor(time.time())
        if args == ['status']:
            return observe(revision, now, persist=False)
        if args == ['monitor']:
            # Atomic state snapshots let monitoring continue while purge owns
            # its lock; a hung purge must not prevent missed-run detection.
            with state_lock(LOCK.with_name('monitor.lock')):
                return observe(revision, now, persist=True)
        with state_lock():
            if args == ['initialize']:
                initialize(revision, now)
            elif args == ['acknowledge']:
                acknowledge(revision, now)
            else:
                result = run(revision, LocalPostgres())
                print('admission_purge_run=' + result)
                return 0 if result == 'ok' else 1
        print('admission_purge_operation=ok')
        return 0
    except BaseException:
        print('Admission purge operation unavailable; inspect protected state.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
