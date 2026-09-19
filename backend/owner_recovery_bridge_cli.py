"""Protected WEB-only entry point; no credentials in arguments or output."""
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import stat
import sys


def checked_source():
    root = Path(__file__).absolute().parent.parent
    if (os.geteuid() != 0 or os.uname().nodename != 'careertrellis-staging-web-1'
            or root.parent != Path('/opt/careertrellis/owner-recovery-bridge')
            or not re.fullmatch('[0-9a-f]{40}', root.name)):
        raise ValueError()
    for path in (root, *root.parents):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError()
    manifest = root / 'source-manifest.json'
    info = manifest.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError()
    files = json.loads(manifest.read_bytes())
    for relative, digest in files.items():
        path = root / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError()
        for parent in path.parents:
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError()
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_mode & 0o022 or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError()
    if 'backend/owner_recovery_bridge_cli.py' not in files:
        raise ValueError()
    return root


def main():
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        root = checked_source()
        sys.path.insert(0, str(root / 'backend'))
        import owner_recovery as recovery
        import owner_recovery_bridge as bridge
        recovery.require(len(sys.argv) in (2, 3))
        action = sys.argv[1]
        if action == 'preactivation-check':
            recovery.require(len(sys.argv) == 2)
            binding = recovery.owner_snapshot()
            with recovery.Store().locked() as store:
                value = store.read()
                recovery.require(value is not None and value['state'] == 'active'
                    and value['binding'] == binding)
            print('bridge_installed; owner_credential_active; no_account_change')
            return 0
        recovery.require(len(sys.argv) == 3 and action in
            ('consume', 'receipt', 'enter', 'attest', 'reconcile', 'status'))
        operation = sys.argv[2]
        from uuid import UUID
        recovery.require(str(UUID(operation)) == operation)
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        engine = create_engine(bridge.ops.migration_connection_url(bridge.operator.DATABASE),
            poolclass=NullPool, hide_parameters=True, connect_args={'connect_timeout': 5})
        try:
            if action in ('attest', 'reconcile', 'status'):
                result = bridge.attest(action, operation, root.name, engine)
            else:
                # Human input is possible only in the user's controlling TTY.
                with recovery.Terminal() as terminal:
                    if action == 'enter': result = bridge.enter(operation, engine, terminal, source=root.name)
                    else:
                        bridge.run(action, operation, engine, terminal, source=root.name)
                        result = {'outcome': 'holder_proof_checked_account_held'}
            print(bridge.ops.encoded(result).decode().strip())
            return 1 if result.get('outcome') == 'recorded_cleanup_pending' else 0
        finally:
            engine.dispose()
    except BaseException:
        print('Owner bridge operation refused or acknowledgement uncertain; inspect operation status.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
