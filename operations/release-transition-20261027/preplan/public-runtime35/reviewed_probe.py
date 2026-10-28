"""Read-only identity/configuration probe. No global queue-count claims.

The separate DB-host nonce-bound handshake owns the global visibility gate.
No provider client is constructed; configuration and preflight only are reused.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import subprocess
import sys


async def inspect(revision, stage):
    require = lambda ok: None if ok else (_ for _ in ()).throw(RuntimeError('probe refused'))
    require(re.fullmatch('[0-9a-f]{40}', revision))
    sys.path.insert(0, str(Path('/opt/careertrellis/staging/releases') / revision / 'backend'))
    import recovery_runtime as rr
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    logging.disable(logging.CRITICAL)
    settings, url, raw = rr.configuration()
    engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True,
        echo=False, echo_pool=False, connect_args={'connect_timeout': 5,
        'options': '-c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0'})
    try:
        await rr.preflight(engine, settings.binding)
        async with engine.begin() as c:
            await c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            await c.exec_driver_sql("SET LOCAL statement_timeout='5s'")
            await c.exec_driver_sql("SET LOCAL lock_timeout='2s'")
            await c.exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
            policy = await c.scalar(text('SELECT to_jsonb(p) FROM careertrellis_admission.policy p'))
            require(all(rr.ops.read_protected(rr.ops.ROOT / name) == value for name, value in raw.items()))
        backup = subprocess.run(['/usr/local/sbin/careertrellis-backup-health', 'status'],
                                capture_output=True, text=True, timeout=30)
        status = json.loads(backup.stdout)
        units = []
        for unit in ('careertrellis-web', 'careertrellis-worker', 'careertrellis-erasure-worker'):
            p = subprocess.run(['systemctl', 'show', unit, '--property=ActiveState', '--value'],
                               capture_output=True, text=True, timeout=10)
            require(p.returncode == 0)
            units.append(p.stdout.strip())
        require(stage not in ('stopped', 'recovery') or units == ['inactive'] * 3)
        return {'version': 1, 'revision': revision, 'stage': stage,
            'observed_at': datetime.now(timezone.utc).isoformat(), 'read_only': True,
            'identity_bound': True,
            'configuration_stable': True, 'policy_open_beta': policy['maintenance'] is False,
            'policy': {k: policy[k] for k in ('maintenance', 'signup_open', 'phase', 'cap', 'minimum_session_version')},
            'schema': '20261005_0023',
            # This proves only the known three services. Complete producer inventory
            # remains a documented review prerequisite; no claim about unknown hosts.
            'known_producers_stopped': units == ['inactive'] * 3,
            'object_backup': {'exit_code': backup.returncode,
                **{k: status.get(k) for k in ('result', 'evaluated_at', 'failure_codes')}}}
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--revision', required=True)
    parser.add_argument('--stage', choices=('preflight', 'stopped', 'recovery', 'running'), required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(inspect(args.revision, args.stage)), sort_keys=True))
    except BaseException:
        print('{"status":"probe_refused","details_suppressed":true}')
        raise SystemExit(1)
