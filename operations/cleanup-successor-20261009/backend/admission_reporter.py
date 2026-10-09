"""Local DB-host observer; fresh status on every attempt, no queued recovery.

The protected Healthchecks URL is a credential. Never print config or exceptions.
This helper performs no database write, purge, service change or provider setup.
"""
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

import admission_operations as ops
import admission_purge as purge
import admission_alert_contract as contract
import admission_healthchecks as healthchecks

CONFIG=ops.ROOT/'admission-healthchecks.json'
ENV={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
STATUS_TIMEOUT=12


def settings(raw,source):
    ops.require(type(raw) is bytes and len(raw)<=2048)
    value=ops.strict_json(raw)
    ops.require(type(value) is dict and set(value)=={'version','source','ping_url'}
        and type(value['version']) is int and value['version'] in (1,2) and value['source']==source
        and re.fullmatch('[0-9a-f]{40}',source)
        and type(value['ping_url']) is str and re.fullmatch(
            r'https://hc-ping\.com/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',value['ping_url']))
    return value


def observe(release, *, lane='admission'):
    """Fixed status-only command, bounded runtime and bounded output read."""
    try:
        ops.require(lane in ('admission','totp'))
        script = 'manage-admission-purge.sh' if lane=='admission' else 'manage-totp-cleanup.sh'
        with tempfile.TemporaryFile() as output:
            process=subprocess.Popen(['/bin/bash',str(release/'deploy/scripts'/script),'status'],
                stdout=output,stderr=subprocess.DEVNULL,env=ENV,start_new_session=True)
            try:
                code=process.wait(timeout=STATUS_TIMEOUT)
            except BaseException as failure:
                # The child owns this new group; never target an inherited group.
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=2)
                if isinstance(failure,subprocess.TimeoutExpired): return None,b''
                raise
            output.seek(0)
            return code,output.read(4097)
    except Exception:
        return None,b''


def report(release):
    source=release.name
    raw=ops.read_protected(CONFIG,limit=2048); value=settings(raw,source)
    with purge.state_lock(purge.STATE/'reporter.lock'):
        ops.require(not purge.rebind_pending())
        code,output=observe(release)
        now=math.floor(time.time())
        classification=contract.classify(code,output,source,now)
        if value['version']==2:
            import totp_cleanup_alert_contract as cleanup_contract
            cleanup_code,cleanup_output=observe(release,lane='totp')
            now=math.floor(time.time())
            # Recheck the first observation after the second bounded read.
            classification=cleanup_contract.combined(contract.classify(code,output,source,now,version=2),
                cleanup_contract.classify(cleanup_code,cleanup_output,source,now))
        # A configuration change during observation is not permission to adopt it.
        ops.require(not purge.rebind_pending() and ops.read_protected(CONFIG,limit=2048)==raw)
        acceptance=(healthchecks.submit(value['ping_url'],classification,version=2) if value['version']==2
            else healthchecks.submit(value['ping_url'],classification))
        ops.require(acceptance in ('accepted','unconfirmed'))
        ops.state_write(purge.STATE/'reporter.json',{'version':value['version'],'source':source,'observed_at':now,
            'classification':list(classification),'provider_acceptance':acceptance})
    return {'outcome':acceptance,'observation':'alert' if classification else 'healthy'}


def enter(release):
    from identity_entry import SecureTerminal
    ops.require(not CONFIG.exists() and not CONFIG.is_symlink())
    with SecureTerminal() as terminal:
        ops.require(terminal.ask('Confirm your visible secure terminal: type VISIBLE')=='VISIBLE')
        ops.require(terminal.ask('Dedicated live check uses approved free email, five-minute reports and two-minute grace: type VERIFIED')=='VERIFIED')
        url=terminal.ask('Dedicated live Healthchecks ping URL (hidden)')
        raw=ops.encoded({'version':1,'source':release.name,'ping_url':url})
        settings(raw,release.name)
        ops.require(terminal.ask('Store protected configuration only; no ping or timer start: type STORE')=='STORE')
    with purge.state_lock(purge.STATE/'reporter.lock'):
        ops.write_protected(CONFIG,raw)
    return {'outcome':'configured_no_ping_or_timer_start'}


def main(argv=None):
    try:
        args=sys.argv[1:] if argv is None else argv
        ops.require(args in (['report'],['enter']) and os.geteuid()==0
            and os.uname().nodename=='job-applier-db-staging-01')
        source=Path(__file__).absolute(); release=source.parent.parent
        ops.require(release.parent==Path('/opt/careertrellis/staging/releases') and re.fullmatch('[0-9a-f]{40}',release.name))
        for name in ('admission_reporter','admission_purge','admission_operations','admission_alert_contract','admission_healthchecks',
                'identity_entry','identity_evidence','identity_operator','session_authorization'):
            ops.read_protected(source.parent/(name+'.py'),modes=(0o600,0o640,0o644),limit=131072)
        ops.read_protected(release/'deploy/scripts/manage-admission-purge.sh',modes=(0o755,),limit=16384)
        # Validate successor dependencies before their lazy imports can execute.
        for name in ('totp_cleanup_alert_contract','totp_cleanup_operations','totp_cleanup'):
            ops.read_protected(source.parent/(name+'.py'),modes=(0o600,0o640,0o644),limit=131072)
        ops.read_protected(release/'deploy/scripts/manage-totp-cleanup.sh',modes=(0o755,),limit=16384)
        result=enter(release) if args==['enter'] else report(release)
        print(ops.encoded(result).decode().strip())
        return 0 if result['outcome']!='unconfirmed' else 1
    except BaseException:
        print('Admission email reporting unavailable; inspect protected state.',file=sys.stderr)
        return 2


if __name__=='__main__': sys.exit(main())
