#!/usr/bin/env bash
set -Eeuo pipefail
set +x
if [[ ${EUID} -ne 0 || $# -ne 0 ]]; then
    echo "The worker environment delivery check did not pass." >&2
    exit 1
fi
# This operational guard is independent of the target release, including old
# rollback candidates. Never import candidate code or evaluate environment files.
exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/bin/python3 -I -B - <<'PY'
import os
from pathlib import Path
import re
import stat
import sys
try:
    root = Path('/etc/careertrellis')
    for name in ('search-worker.env', 'postgres-worker.env', 'provider-secrets.env',
                 'account-erasure-worker.env', 'postgres-erasure-worker.env', 'account-erasure-object-storage.env',
                 'serper-worker.env'):
        path = root / name
        if name == 'serper-worker.env' and not path.exists() and not path.is_symlink():
            continue
        for parent in path.parents:
            item = parent.lstat()
            if not stat.S_ISDIR(item.st_mode) or item.st_uid != 0 or item.st_mode & 0o022:
                raise ValueError()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            item = os.fstat(descriptor)
            if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or item.st_uid != 0 or stat.S_IMODE(item.st_mode) != 0o600 or item.st_size > 65536:
                raise ValueError()
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError()
        finally:
            os.close(descriptor)
        names = set()
        for line in raw.decode('utf-8').splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            match = re.fullmatch(r'([A-Z][A-Z0-9_]*)=([^\s\x00-\x1f\x7f\'"`\\$;]*)', line)
            if match is None or match[1] in names:
                raise ValueError()
            names.add(match[1])
            if match[1] == 'CAREERTRELLIS_ADMISSION_DATABASE_URL' or match[1].startswith('CAREERTRELLIS_AUTH_V2_'):
                raise ValueError()
            if name == 'serper-worker.env':
                if match[1] == 'CAREERTRELLIS_SERPER_ENABLED':
                    if match[2] not in ('0', '1'):
                        raise ValueError()
                elif match[1] == 'CAREERTRELLIS_SERPER_API_KEY':
                    if re.fullmatch(r'[A-Za-z0-9_-]{20,256}', match[2]) is None:
                        raise ValueError()
                else:
                    raise ValueError()
        if name == 'serper-worker.env' and names != {'CAREERTRELLIS_SERPER_ENABLED', 'CAREERTRELLIS_SERPER_API_KEY'}:
            raise ValueError()
except BaseException:
    print('The worker environment delivery check did not pass.', file=sys.stderr)
    sys.exit(1)
print('operation=worker_environment_delivery\nresult=ok\nvalues_disclosed=no')
PY
