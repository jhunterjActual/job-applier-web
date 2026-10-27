"""Read-only native same-UID process identity probe; no app or provider writes."""
import grp
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

USERS = {'careertrellis-web.service': 'careertrellis',
         'careertrellis-worker.service': 'careertrellis-worker',
         'careertrellis-erasure-worker.service': 'careertrellis-erasure-worker'}
CHILD_PROBE = '''import os,sys
def main():
    try:
        pid,uid,gid=int(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3])
        expected=sys.argv[4]
        if not (0<pid<2147483648 and uid>0 and gid>0 and os.geteuid()==uid and os.getegid()==gid): return 1
        base='/proc/'+str(pid)
        def started():
            with open(base+'/stat','r') as stream: return int(stream.read().rsplit(')',1)[1].split()[19])
        before=started()
        if before<=0 or os.stat(base).st_uid!=uid or os.readlink(base+'/cwd')!=expected: return 1
        return 0 if started()==before else 1
    except Exception: return 1
raise SystemExit(main())
'''


def need(value):
    if not value: raise RuntimeError('process_identity_refused')


def state(unit):
    result = subprocess.run(['/usr/bin/systemctl', 'show', unit,
        '--property=ActiveState,SubState,MainPID,Job,InvocationID'], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=15, check=False,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'})
    need(result.returncode == 0 and len(result.stdout) <= 1024)
    lines = [line.split('=', 1) for line in result.stdout.decode('ascii').splitlines()]
    need(all(len(row) == 2 for row in lines))
    value = dict(lines)
    need(len(lines) == len(value) == 5 and set(value) == {'ActiveState', 'SubState', 'MainPID', 'Job', 'InvocationID'}
         and value['ActiveState'] == 'active' and value['SubState'] == 'running' and not value['Job']
         and re.fullmatch('[1-9][0-9]{0,9}', value['MainPID']) and int(value['MainPID']) < 2147483648
         and re.fullmatch('[0-9a-f]{32}', value['InvocationID']))
    return value


def main():
    try:
        need(os.geteuid() == 0 and os.uname().nodename == 'careertrellis-staging-web-1')
        pointer = Path('/opt/careertrellis/staging/current')
        for parent in pointer.parents:
            info = parent.lstat()
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022)
        meta = pointer.lstat()
        need(stat.S_ISLNK(meta.st_mode) and meta.st_uid == meta.st_gid == 0)
        release = pointer.resolve(strict=True)
        need(release.parent == Path('/opt/careertrellis/staging/releases') and re.fullmatch('[0-9a-f]{40}', release.name))
        for unit, user in USERS.items():
            before = state(unit)
            uid, gid = pwd.getpwnam(user).pw_uid, grp.getgrnam('careertrellis').gr_gid
            need(uid > 0 and gid > 0)
            args = ['/usr/bin/setpriv', '--reuid=' + str(uid), '--regid=' + str(gid),
                '--clear-groups', '--reset-env', '--', '/usr/bin/python3', '-I', '-B', '-c',
                CHILD_PROBE, before['MainPID'], str(uid), str(gid), str(release / 'backend')]
            result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, check=False, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'})
            need(result.returncode == 0 and state(unit) == before)
        need(pointer.resolve(strict=True) == release)
        print(json.dumps({'schema': 'careertrellis.paired-maintenance-process-probe/v1',
            'result': 'pass', 'same_uid_identity_verified': True, 'unit_count': 3, 'read_only': True}))
        return 0
    except Exception:
        print('{"schema":"careertrellis.paired-maintenance-process-probe/v1","result":"refused","read_only":true}')
        return 1


if __name__ == '__main__': raise SystemExit(main())
