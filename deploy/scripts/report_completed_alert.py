#!/usr/bin/env python3
"""Dispatch only a completed current objects alert; never report old health."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

UNIT = 'careertrellis-generated-object-backup-health.service'
STATUS = Path('/var/lib/careertrellis/backup-health/objects/objects-status.json')


def run(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if p.returncode:
        raise ValueError('command failed')
    return p.stdout.strip()


def eligible(status, properties, start, finish, now):
    if properties.get('Result') != 'exit-code' or properties.get('ExecMainStatus') != '1':
        return False
    reporter = properties.get('OnSuccess', '')
    if not re.fullmatch(r'careertrellis-backup-reporter@[0-9a-f]{40}\.service', reporter):
        return False
    if status.get('surface') != 'objects' or status.get('result') != 'alert':
        return False
    codes = status.get('failure_codes')
    if not isinstance(codes, list) or not codes or any(not isinstance(x, str) for x in codes):
        return False
    try:
        evaluated = dt.datetime.fromisoformat(status['evaluated_at'].replace('Z', '+00:00'))
        return (evaluated.tzinfo is not None and start <= evaluated <= finish
                and finish <= now and (now - evaluated).total_seconds() <= 300)
    except (ValueError, TypeError, KeyError):
        return False


def current_eligible(status, properties, started_ns, finished_ns, modified_ns, now):
    if not started_ns <= modified_ns <= finished_ns:
        return False
    start = dt.datetime.fromtimestamp(started_ns // 1000000000, dt.timezone.utc)
    finish = dt.datetime.fromtimestamp(finished_ns // 1000000000, dt.timezone.utc)
    return eligible(status, properties, start, finish, now)


def protected_status():
    for path in STATUS.parents:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError('unsafe parent')
    fd = os.open(STATUS, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 32768:
            raise ValueError('unsafe status')
        raw = stream.read(32769)
        if len(raw) > 32768:
            raise ValueError('oversized status')
    return raw, json.loads(raw), info.st_mtime_ns


def main():
    try:
        if os.geteuid() != 0 or os.uname().nodename != 'careertrellis-staging-web-1':
            raise ValueError('wrong execution context')
        command = ['systemctl', '--timestamp=us', 'show', UNIT, '--property=Result,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp,OnSuccess']
        original = run(command)
        props = dict(line.split('=', 1) for line in original.splitlines())
        def timestamp(name):
            value = run(['date', '--date=' + props[name], '+%s.%N'])
            seconds, nanos = value.split('.')
            return int(seconds) * 1000000000 + int(nanos)
        started_ns = timestamp('ExecMainStartTimestamp')
        finished_ns = timestamp('ExecMainExitTimestamp')
        raw, status, modified_ns = protected_status()
        # Receipt timestamps are second precision; file mtime additionally binds
        # this write to the microsecond-precision service invocation.
        if not current_eligible(status, props, started_ns, finished_ns, modified_ns, dt.datetime.now(dt.timezone.utc)):
            raise ValueError('no completed current alert')
        # Fail closed if another evaluation changed either observed input.
        if run(command) != original or protected_status() != (raw, status, modified_ns):
            raise ValueError('observation changed')
        run(['systemctl', 'start', '--no-block', props['OnSuccess']])
        print('{"outcome":"current_alert_reporter_queued"}')
        return 0
    except Exception:
        print('{"outcome":"dispatch_withheld"}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
