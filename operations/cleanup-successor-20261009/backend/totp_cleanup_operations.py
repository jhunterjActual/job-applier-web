"""Independent, versioned DB-host enrollment ciphertext cleanup operations.

No provider calls, credentials, record deletion or admission-count changes.
Installation and scheduled execution require separate operational authority.
"""
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

import admission_operations as ops
import admission_purge as purge
import totp_cleanup

STATE = Path('/var/lib/careertrellis/totp-cleanup')
SCHEMA = 'careertrellis.totp-cleanup/v1'
SCHEMA_V2 = 'careertrellis.totp-cleanup/v2'
RUN_SECONDS = 40
MAX_AGE = 420
SLOT_OFFSET = 300  # Minute05, after the admission purge's100-second bound.
ISSUES = frozenset(('never_completed', 'hourly_run_missed', 'expired_ciphertext_remaining',
    'last_run_failed', 'run_in_progress', 'run_incomplete', 'state_unavailable',
    'cleanup_timer_unavailable', 'cleanup-monitor_timer_unavailable', 'monitor_evidence_unavailable',
    'source_unverified'))


def validate(value, source, now):
    ops.require(isinstance(value, dict))
    fields = {'schema', 'source', 'initialized_at', 'last_success', 'last_attempt', 'last_failure'}
    if value.get('schema') == SCHEMA_V2:
        fields |= {'source_bound_at', 'source_success'}
        ops.require(purge.integer(value.get('source_bound_at')) and value['source_bound_at'] <= now)
        fresh = value.get('source_success')
        ops.require(fresh is None or (purge.integer(fresh) and value['source_bound_at'] <= fresh <= now
            and fresh == value.get('last_success')))
    ops.require(set(value) == fields)
    ops.require(value['schema'] in (SCHEMA, SCHEMA_V2) and value['source'] == source
        and purge.integer(value['initialized_at']) and value['initialized_at'] <= now)
    last = value['last_success']
    ops.require(last is None or (purge.integer(last) and value['initialized_at'] <= last <= now))
    for name in ('last_attempt', 'last_failure'):
        item = value[name]
        if item is None:
            continue
        ops.require(isinstance(item, dict) and set(item) == {'started_at', 'finished_at', 'result', 'scrubbed'}
            and purge.integer(item['started_at']) and value['initialized_at'] <= item['started_at'] <= now
            and item['result'] in ('running', 'ok', 'backlog', 'failed'))
        ops.require(item['scrubbed'] is None if item['result'] == 'failed' else
            purge.integer(item['scrubbed']) and item['scrubbed'] <= totp_cleanup.BATCH * totp_cleanup.ROUNDS)
        if item['result'] == 'running':
            ops.require(name == 'last_attempt' and item['finished_at'] is None and item['scrubbed'] == 0)
        else:
            ops.require(purge.integer(item['finished_at']) and item['started_at'] <= item['finished_at'] <= now)
        if name == 'last_failure':
            ops.require(item['result'] in ('failed', 'backlog'))
    attempt, failure = value['last_attempt'], value['last_failure']
    ops.require(last is None or (attempt is not None and last <= attempt['started_at']))
    if attempt and attempt['result'] == 'ok':
        ops.require(last == attempt['started_at'])
    if failure:
        ops.require(attempt is not None and failure['started_at'] <= attempt['started_at'])
    if value['schema'] == SCHEMA_V2:
        ops.require(value['initialized_at'] <= value['source_bound_at'])
        fresh = value['source_success']
        if fresh is not None:
            ops.require(attempt is not None and fresh <= attempt['started_at']
                and (attempt['result'] != 'ok' or fresh == attempt['started_at']))
    return value


def read_state(source, now):
    return validate(ops.strict_json(ops.read_protected(STATE / 'state.json')), source, now)


def initialize(source, now):
    ops.require(not purge.rebind_pending())
    ops.write_protected(STATE / 'state.json', ops.encoded({'schema': SCHEMA, 'source': source,
        'initialized_at': now, 'last_success': None, 'last_attempt': None, 'last_failure': None}))


def run(source, db, *, clock=time.time):
    ops.require(not purge.rebind_pending())
    now = math.floor(clock())
    value = read_state(source, now)
    previous = value['last_attempt']
    ops.require(previous is None or previous['result'] != 'running')
    attempt = {'started_at': now, 'finished_at': None, 'result': 'running', 'scrubbed': 0}
    value['last_attempt'] = attempt
    # Durable claim before any SQL. Lost final write leaves a held attempt.
    ops.state_write(STATE / 'state.json', value)
    try:
        with ops.host_locks():
            ops.require(db.query("SELECT current_database()='careertrellis' AND session_user='postgres' "
                "AND current_setting('server_version_num')::int BETWEEN 180000 AND 189999 "
                "AND current_setting('data_directory')='/var/lib/postgresql/18/main';") is True)
            result = totp_cleanup.scrub(db)
            ops.require(isinstance(result, dict) and set(result) == {'result', 'scrubbed'}
                and result['result'] in ('ok', 'backlog') and purge.integer(result['scrubbed'])
                and result['scrubbed'] <= totp_cleanup.BATCH * totp_cleanup.ROUNDS)
            attempt.update(result)
    except BaseException:
        attempt.update(result='failed', scrubbed=None)
        raise
    finally:
        attempt['finished_at'] = math.floor(clock())
        ops.require(attempt['finished_at'] >= now)
        if attempt['result'] == 'ok':
            value['last_success'] = now
            if value['schema'] == SCHEMA_V2:
                value['source_success'] = now
        else:
            value['last_failure'] = dict(attempt)
        ops.state_write(STATE / 'state.json', value)
    return attempt['result']


def acknowledge(source, now):
    """After independently proving terminal state; never grants success credit."""
    ops.require(not purge.rebind_pending())
    value = read_state(source, now)
    ops.require(value['last_attempt'] is not None and value['last_attempt']['result'] == 'running')
    value['last_attempt'].update(result='failed', finished_at=now, scrubbed=None)
    value['last_failure'] = dict(value['last_attempt'])
    ops.state_write(STATE / 'state.json', value)


def health(value, now):
    issues = []
    if value['schema'] == SCHEMA_V2 and value['source_success'] is None:
        issues.append('source_unverified')
    last = value['last_success']
    if last is None:
        issues.append('never_completed')
    baseline = value['initialized_at'] if last is None else last
    if (now - SLOT_OFFSET - 120) // 3600 > (baseline - SLOT_OFFSET) // 3600:
        issues.append('hourly_run_missed')
    attempt, failure = value['last_attempt'], value['last_failure']
    if failure and (last is None or last <= failure['started_at']):
        issues.append('expired_ciphertext_remaining' if failure['result'] == 'backlog' else 'last_run_failed')
    if attempt:
        if attempt['result'] in ('failed', 'backlog'):
            code = 'last_run_failed' if attempt['result'] == 'failed' else 'expired_ciphertext_remaining'
            if code not in issues:
                issues.append(code)
        elif attempt['result'] == 'running':
            issues.append('run_incomplete' if now - attempt['started_at'] > RUN_SECONDS else 'run_in_progress')
    return {'schema': value['schema'], 'source': value['source'], 'checked_at': now,
        'result': 'alert' if issues else 'ok', 'issues': issues}


def timer_issues(source):
    issues = []
    for lane in ('cleanup', 'cleanup-monitor'):
        try:
            raw = purge.command(['/usr/bin/systemctl', 'show', 'careertrellis-totp-' + lane + '@' + source + '.timer',
                '--property=LoadState,ActiveState,UnitFileState']).decode()
            ops.require(dict(line.split('=', 1) for line in raw.splitlines()) == {
                'LoadState': 'loaded', 'ActiveState': 'active', 'UnitFileState': 'enabled'})
        except Exception:
            issues.append(lane + '_timer_unavailable')
    return issues


def validate_health(value, source, now, *, max_age=None):
    ops.require(isinstance(value, dict) and set(value) == {'schema', 'source', 'checked_at', 'result', 'issues'}
        and value['schema'] in (SCHEMA, SCHEMA_V2) and value['source'] == source and purge.integer(value['checked_at'])
        and value['checked_at'] <= now and (max_age is None or now-value['checked_at'] <= max_age)
        and isinstance(value['issues'], list) and all(isinstance(x, str) and x in ISSUES for x in value['issues'])
        and len(set(value['issues'])) == len(value['issues'])
        and value['result'] == ('alert' if value['issues'] else 'ok'))
    ops.require(value['schema'] == SCHEMA_V2 or 'source_unverified' not in value['issues'])
    return value


def observe(source, now, *, persist):
    try:
        ops.require(not purge.rebind_pending())
        result = health(read_state(source, now), now)
    except Exception:
        result = {'schema': SCHEMA, 'source': source, 'checked_at': now, 'result': 'alert', 'issues': ['state_unavailable']}
    result['issues'].extend(timer_issues(source))
    if not persist:
        try:
            saved = validate_health(ops.strict_json(ops.read_protected(STATE / 'health.json')), source, now, max_age=MAX_AGE)
            ops.require(saved['schema'] == result['schema'])
            # A fresh current snapshot cannot erase an unresolved monitor alert.
            result['issues'].extend(x for x in saved['issues'] if x not in result['issues'])
        except Exception:
            result['issues'].append('monitor_evidence_unavailable')
    result['result'] = 'alert' if result['issues'] else 'ok'
    if persist:
        ops.require(not purge.rebind_pending())
        path = STATE / 'health.json'
        old = validate_health(ops.strict_json(ops.read_protected(path)), source, now) if path.exists() else None
        ops.state_write(path, result)
        if old is None or (old['result'], old['issues']) != (result['result'], result['issues']):
            print('totp_cleanup_health=' + result['result'] + ' issues=' + ','.join(result['issues']))
    else:
        print(ops.encoded(result).decode().strip())
    return 1 if result['issues'] else 0


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        ops.require(args in (['initialize'], ['run'], ['monitor'], ['status'], ['acknowledge']))
        ops.require(getattr(os, 'geteuid', lambda: -1)() == 0 and os.uname().nodename == 'job-applier-db-staging-01')
        source = Path(__file__).absolute()
        ops.require(source.parent.parent.parent == Path('/opt/careertrellis/staging/releases')
            and re.fullmatch('[0-9a-f]{40}', source.parent.parent.name))
        for name in ('totp_cleanup_operations.py', 'totp_cleanup.py', 'admission_purge.py', 'admission_operations.py'):
            ops.read_protected(source.parent / name, modes=(0o600, 0o640, 0o644), limit=131072)
        ops.protected_parents(STATE / 'sentinel')
        ops.require(stat.S_IMODE(STATE.stat().st_mode) == 0o700)
        revision, now = source.parent.parent.name, math.floor(time.time())
        if args == ['status']:
            return observe(revision, now, persist=False)
        with purge.state_lock(STATE / ('monitor.lock' if args == ['monitor'] else 'cleanup.lock')):
            if args == ['monitor']:
                return observe(revision, now, persist=True)
            if args == ['initialize']:
                initialize(revision, now)
            elif args == ['acknowledge']:
                acknowledge(revision, now)
            else:
                result = run(revision, purge.LocalPostgres())
                print('totp_cleanup_run=' + result)
                return 0 if result == 'ok' else 1
        print('totp_cleanup_operation=ok')
        return 0
    except BaseException:
        print('Authenticator cleanup unavailable; inspect protected state.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
