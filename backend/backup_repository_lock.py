"""Root-only, narrow repair of the volatile repository-lock boot prerequisite.

No service, credential, repository, release pointer or existing lock is replaced.
Run from an immutable root-owned source bundle. No configurable CLI paths.
"""
import os
from pathlib import Path
import stat
import sys

RULE_NAME = 'careertrellis-pgbackrest.conf'
LOCK_NAME = 'careertrellis-pgbackrest-repository.lock'
RULE = b'f /run/lock/careertrellis-pgbackrest-repository.lock 0600 root root - -\n'
ERROR = 'Repository lock installation refused; inspect retained state before retrying.'


def require(value):
    if not value:
        raise ValueError(ERROR)


def parents(path, *, sticky_lock_parent=None):
    for parent in reversed(path.parents):
        item = parent.lstat()
        require(stat.S_ISDIR(item.st_mode) and item.st_uid == 0)
        if item.st_mode & 0o022:
            require(parent == sticky_lock_parent and item.st_mode & stat.S_ISVTX)


def metadata(item, mode):
    require(stat.S_ISREG(item.st_mode) and item.st_uid == item.st_gid == 0
            and stat.S_IMODE(item.st_mode) == mode and item.st_nlink == 1)


def same_inode(path, item):
    current = path.lstat()
    require((current.st_dev, current.st_ino) == (item.st_dev, item.st_ino))


def read_file(path, mode):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        item = os.fstat(descriptor)
        metadata(item, mode)
        require(item.st_size <= 65536)
        value = os.read(descriptor, 65537)
        same_inode(path, item)
        return value
    finally:
        os.close(descriptor)


def install(rule_source, *, root=Path('/')):
    """The root override is exclusively a synthetic native-test seam."""
    import fcntl
    parents(rule_source)
    source_mode = stat.S_IMODE(rule_source.lstat().st_mode)
    require(source_mode in (0o600, 0o640, 0o644))
    raw = read_file(rule_source, source_mode)
    lines = [line for line in raw.splitlines() if line and not line.startswith(b'#')]
    require(lines == [RULE.rstrip(b'\n')])
    lock = root / 'run/lock' / LOCK_NAME
    target = root / 'etc/tmpfiles.d' / RULE_NAME
    parents(lock, sticky_lock_parent=root / 'run/lock')
    parents(target)
    if target.exists() or target.is_symlink():
        require(read_file(target, 0o644) == raw)
    # O_EXCL prevents a raced creation from truncating or following anything.
    try:
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        item = os.fstat(descriptor)
        metadata(item, 0o600)
        same_inode(lock, item)
        # Busy means no config write and no content/mode/inode change.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        same_inode(lock, item)
        if not target.exists():
            # Publish an entire fsynced file without overwriting another config.
            import tempfile
            fd, temporary = tempfile.mkstemp(prefix='.ct-repository-lock-', dir=target.parent)
            try:
                os.fchown(fd, 0, 0)
                os.fchmod(fd, 0o644)
                with os.fdopen(fd, 'wb', closefd=False) as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(fd)
                same_inode(lock, item)
                os.link(temporary, target, follow_symlinks=False)
            finally:
                os.close(fd)
                os.unlink(temporary)
            parent_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        require(read_file(target, 0o644) == raw)
        same_inode(lock, item)
        metadata(os.fstat(descriptor), 0o600)
    finally:
        os.close(descriptor)


def main():
    try:
        require(os.getuid() == os.geteuid() == 0 and len(sys.argv) == 1)
        source = Path(__file__).absolute()
        parents(source)
        source_mode = stat.S_IMODE(source.lstat().st_mode)
        require(source_mode in (0o600, 0o640, 0o644))
        read_file(source, source_mode)
        install(source.parent.parent / 'deploy/tmpfiles' / RULE_NAME)
        print('operation=install_repository_lock_boot_rule\nresult=ok\nservices_changed=no')
        return 0
    except BlockingIOError:
        print('Repository lock is busy; no configuration was changed.', file=sys.stderr)
        return 75
    except Exception:
        print(ERROR, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
