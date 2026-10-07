"""Root-only owner bootstrap credential. No application or provider mutations.

The owner personally runs this in an unrecorded SSH terminal. This utility is
deliberately restricted to the preactivation 0014 account binding. A later
incarnation/environment bridge requires separate reviewed implementation.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import time
from uuid import UUID, uuid4

ROOT = Path('/etc/careertrellis')
STATE = Path('/var/lib/careertrellis-owner-recovery')
HOST = 'careertrellis-staging-web-1'
ERROR = 'Owner recovery operation refused. No account recovery was performed.'
SCOPE = 'owner-bootstrap-evidence-v1'
TRANSITION_SCOPE = 'owner-held-transition-evidence-v1'
CONTEXT_KEYS = {'operation', 'account_id', 'incarnation', 'epoch', 'old_binding', 'old_subject',
                'target_binding', 'target_subject', 'expected_role', 'owner_authorized',
                'started', 'deadline', 'predecessor'}


def require(value):
    if not value:
        raise ValueError(ERROR)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def context_times(context):
    require(type(context) is dict and set(context) == CONTEXT_KEYS)
    for key in ('operation', 'incarnation'):
        require(type(context[key]) is str and str(UUID(context[key])) == context[key])
    require(context['predecessor'] is None or (type(context['predecessor']) is str
            and str(UUID(context['predecessor'])) == context['predecessor']))
    for key in ('account_id', 'epoch'):
        require(type(context[key]) is int and 0 < context[key] < 9223372036854775807)
    for key in ('old_binding', 'target_binding'):
        require(type(context[key]) is str and re.fullmatch('[0-9a-f]{64}', context[key]))
    for key in ('old_subject', 'target_subject'):
        require(type(context[key]) is str and re.fullmatch('user_[A-Za-z0-9_]{6,120}', context[key]))
    require(context['expected_role'] == 'owner' and context['owner_authorized'] is True)
    times = []
    for key in ('started', 'deadline'):
        require(type(context[key]) is str)
        value = datetime.fromisoformat(context[key])
        require(value.tzinfo is not None and value.utcoffset().total_seconds() == 0)
        times.append(value.timestamp())
    require(0 < times[1] - times[0] <= 3600)
    return times


def transition_receipt(receipt, credential):
    require(type(receipt) is dict and set(receipt) == {'operation', 'scope', 'binding', 'issued',
            'expires', 'context', 'selection', 'schema', 'credential'})
    require(receipt['scope'] == TRANSITION_SCOPE and receipt['credential'] == credential['id']
            and receipt['binding'] == credential['binding']
            and receipt['schema'] in ('20260909_0015', '20260915_0016', '20260929_0021', '20261005_0023'))
    context = receipt['context']
    started, deadline = context_times(context)
    require(receipt['operation'] == context['operation'] and type(receipt['selection']) is str
            and re.fullmatch('[0-9a-f]{64}', receipt['selection']))
    require(type(receipt['issued']) is int and type(receipt['expires']) is int
            and credential['created'] <= credential['confirmed'] <= started
            and started <= receipt['issued'] < receipt['expires'] <= deadline
            and receipt['expires'] <= receipt['issued'] + 300)
    return receipt


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    def invalid(_):
        raise ValueError(ERROR)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def parents(path):
    require(path.is_absolute())
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def protected(path, modes=(0o600,), limit=65536):
    parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) in modes and info.st_size <= limit)
        raw = os.read(fd, limit + 1)
        require(len(raw) <= limit)
        return raw
    finally:
        os.close(fd)


def settings(path):
    result = {}
    for line in protected(path).decode().splitlines():
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Z][A-Z0-9_]*)=([^\s\x00-\x1f\x7f\'"`\\$;]*)', line)
        require(match and match[1] not in result)
        result[match[1]] = match[2]
    return result


def owner_snapshot():
    """Read-only exact source mapping. Never expose values to logs/terminal."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool
    require(not logging.getLogger('sqlalchemy.engine').isEnabledFor(logging.DEBUG)
            and not logging.getLogger('sqlalchemy.engine.Engine').isEnabledFor(logging.DEBUG))
    staging = settings(ROOT / 'staging.env')
    auth = settings(ROOT / 'auth-v2.env')
    require(auth['CAREERTRELLIS_AUTH_V2_ENABLED'] == '0')
    subject = staging['CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID']
    client = staging['WORKOS_CLIENT_ID']
    require(re.fullmatch(r'user_[A-Za-z0-9_]{6,120}', subject)
            and re.fullmatch(r'client_[A-Za-z0-9_]{6,120}', client))
    values = settings(ROOT / 'postgres-migration.env')
    require(set(values) == {'CAREERTRELLIS_MIGRATION_DATABASE_URL'})
    url = make_url(values['CAREERTRELLIS_MIGRATION_DATABASE_URL'])
    require(url.drivername in ('postgresql', 'postgresql+psycopg')
            and url.username == 'careertrellis_migration' and url.database == 'careertrellis')
    engine = create_engine(url.set(drivername='postgresql+psycopg'), poolclass=NullPool,
                           echo=False, hide_parameters=True, connect_args={'connect_timeout': 5})
    try:
        with engine.begin() as c:
            c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            c.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
            c.exec_driver_sql('SET LOCAL log_parameter_max_length = 0')
            c.exec_driver_sql('SET LOCAL log_parameter_max_length_on_error = 0')
            c.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
            require(c.scalar(text('SELECT version_num FROM careertrellis.alembic_version')) == '20260907_0014')
            c.exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
            require(c.scalar(text('SELECT NOT maintenance AND NOT signup_open AND minimum_session_version=1 '
                                  'FROM careertrellis_admission.policy WHERE singleton')) is True)
            rows = c.execute(text("SELECT id,workos_user_id,role,created_at,disabled_at "
                                  "FROM careertrellis.app_users WHERE role='owner'")).mappings().all()
            require(len(rows) == 1)
            row = rows[0]
            require(row['workos_user_id'] == subject and row['disabled_at'] is None)
            c.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
            require(c.scalar(text('SELECT NOT EXISTS(SELECT 1 FROM careertrellis.account_erasure_requests '
                                  'WHERE owner_user_id=:uid)'), {'uid': row['id']}) is True)
            database = [url.host, url.port or 5432, url.database,
                        str(c.scalar(text('SELECT oid FROM pg_database WHERE datname=current_database()')))]
            binding = {'host': HOST, 'database': database, 'client': client,
                       'subject': subject, 'account': row['id'],
                       'created': row['created_at'].isoformat(), 'role': 'owner',
                       'schema': '20260907_0014'}
            return hashlib.sha256(encoded(binding)).hexdigest()
    finally:
        engine.dispose()


def normalize(code):
    value = code.replace('-', '').replace(' ', '').strip().lower()
    require(re.fullmatch('[0-9a-f]{64}', value) is not None)
    return value


def verifier(code, salt, binding):
    return hashlib.sha256(b'careertrellis-owner-recovery-v1\x00' + bytes.fromhex(salt)
                          + bytes.fromhex(binding) + bytes.fromhex(normalize(code))).hexdigest()


class Store:
    """Caller must hold the single host lock for every read/modify/write."""
    def __init__(self, root=STATE):
        self.root = root
        self.path = root / 'credential.json'

    @contextmanager
    def locked(self):
        import fcntl
        parents(self.root)
        info = self.root.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        path = self.root / 'lock'
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield self
        finally:
            os.close(fd)

    def read(self):
        if not self.path.exists() and not self.path.is_symlink():
            return None
        value = strict_json(protected(self.path))
        require(type(value) is dict and set(value) == {'version', 'id', 'binding', 'salt', 'hash',
                'state', 'created', 'confirmed', 'attempts', 'last_attempt', 'receipt'})
        require(type(value['version']) is int and value['version'] in (1, 2)
                and str(UUID(value['id'])) == value['id'])
        for key in ('binding', 'salt', 'hash'):
            require(type(value[key]) is str and re.fullmatch('[0-9a-f]{64}', value[key]))
        require(value['state'] in ('pending', 'active', 'consumed', 'revoked'))
        require(all(type(value[k]) is int and value[k] >= 0 for k in ('created', 'confirmed', 'attempts', 'last_attempt')))
        require(value['attempts'] <= 5)
        receipt = value['receipt']
        if value['state'] == 'consumed':
            if value['version'] == 2:
                transition_receipt(receipt, value)
            else:
                require(type(receipt) is dict and set(receipt) == {'operation', 'scope', 'binding', 'issued', 'expires'})
                require(str(UUID(receipt['operation'])) == receipt['operation'] and receipt['scope'] == SCOPE
                        and receipt['binding'] == value['binding'] and type(receipt['issued']) is int
                        and type(receipt['expires']) is int and receipt['expires'] == receipt['issued'] + 300)
        else:
            require(value['version'] == 1 and receipt is None)
        return value

    def write(self, value):
        previous = self.read()
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=self.root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb', closefd=False) as out:
                out.write(encoded(value)); out.flush(); os.fsync(fd)
            require(self.read() == previous)
            os.replace(temporary, self.path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.close(fd)
            if os.path.exists(temporary):
                os.unlink(temporary)

    def issue(self, binding, now):
        require(self.read() is None and re.fullmatch('[0-9a-f]{64}', binding))
        return self.new_generation(binding, now)

    def replace_pending(self, binding, now):
        """Explicit administrative replacement; never reset an existing code."""
        previous = self.read()
        require(previous is not None and previous['state'] == 'pending'
                and previous['confirmed'] == 0 and previous['binding'] == binding
                and now > previous['created'] + 300)
        revoked = {**previous, 'state': 'revoked'}
        path = self.root / ('revoked-' + previous['id'] + '.json')
        raw = encoded(revoked)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            require(protected(path) == raw)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                        and stat.S_IMODE(info.st_mode) == 0o600 and os.read(fd, 65537) == raw)
                os.fsync(fd)
            finally:
                os.close(fd)
        else:
            with os.fdopen(fd, 'wb') as out:
                out.write(raw); out.flush(); os.fsync(out.fileno())
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return self.new_generation(binding, now)

    def new_generation(self, binding, now):
        code = secrets.token_hex(32)
        salt = secrets.token_hex(32)
        value = {'version': 1, 'id': str(uuid4()), 'binding': binding, 'salt': salt,
                 'hash': verifier(code, salt, binding), 'state': 'pending', 'created': now,
                 'confirmed': 0, 'attempts': 0, 'last_attempt': 0, 'receipt': None}
        self.write(value)
        return '-'.join(code[i:i+8] for i in range(0, 64, 8))

    def authenticate(self, code, binding, now, *, confirm=False, operation=None):
        value = self.read()
        require(value is not None and value['binding'] == binding)
        if not confirm:
            require(type(operation) is str and str(UUID(operation)) == operation)
        require(value['state'] == ('pending' if confirm else 'active'))
        require(now >= value['created'] and now >= value['last_attempt'] + (30 if value['attempts'] else 0)
                and value['attempts'] < 5)
        if confirm:
            require(now <= value['created'] + 300)
        # Charge the attempt durably before evaluating the secret, including crashes.
        value['attempts'] += 1
        value['last_attempt'] = now
        self.write(value)
        try:
            valid = hmac.compare_digest(value['hash'], verifier(code, value['salt'], binding))
        except ValueError:
            valid = False
        require(valid)
        if confirm:
            value.update(state='active', confirmed=now, attempts=0, last_attempt=0)
        else:
            value.update(state='consumed', receipt={'operation': operation, 'scope': SCOPE,
                         'binding': binding, 'issued': now, 'expires': now + 300})
        self.write(value)
        return value['state']

    def receipt(self, binding, operation, now):
        value = self.read()
        require(value is not None and value['version'] == 1 and value['state'] == 'consumed' and value['binding'] == binding)
        receipt = value['receipt']
        require(receipt['operation'] == operation and receipt['issued'] <= now < receipt['expires'])
        return dict(receipt)


class Terminal:
    """All sensitive I/O uses the user's controlling terminal, never stdio."""
    def __enter__(self):
        import termios
        import signal
        self.fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
        self.original = None
        self.handlers = {}
        try:
            require(os.isatty(self.fd) and os.tcgetpgrp(self.fd) == os.getpgrp())
            def cancel(*_):
                raise InterruptedError(ERROR)
            for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGTSTP,
                           signal.SIGTTIN, signal.SIGTTOU):
                self.handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, cancel)
            self.original = termios.tcgetattr(self.fd)
            hidden = termios.tcgetattr(self.fd)
            hidden[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON)
            hidden[6][termios.VMIN] = 1
            hidden[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSAFLUSH, hidden)
            self.deadline = time.monotonic() + 300
            return self
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *args):
        import termios
        import signal
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, set(self.handlers))
        try:
            if self.original is not None:
                termios.tcsetattr(self.fd, termios.TCSAFLUSH, self.original)
        finally:
            try:
                for signum, handler in self.handlers.items():
                    signal.signal(signum, handler)
                os.close(self.fd)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous)

    def show(self, text):
        os.write(self.fd, text.encode())

    def ask(self, prompt):
        import select
        self.show(prompt + ' (hidden): ')
        value = bytearray()
        pasted = False
        def read_char():
            left = self.deadline - time.monotonic()
            require(left > 0 and select.select([self.fd], [], [], left)[0])
            char = os.read(self.fd, 1)
            require(bool(char))
            return char
        while True:
            char = read_char()
            if char == b'\x1b':
                marker = b''.join(read_char() for _ in range(5))
                require(marker == (b'[201~' if pasted else b'[200~'))
                pasted = not pasted
                continue
            if char in (b'\r', b'\n'):
                require(not pasted)  # A multiline paste must never submit a prompt.
                self.show('\n')
                return value.decode('ascii')
            if char in (b'\x08', b'\x7f'):
                require(not pasted)
                if value:
                    value.pop()
            else:
                require(32 <= char[0] <= 126 and len(value) < 128)
                value.extend(char)


def main():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        require(os.geteuid() == 0 and os.uname().nodename == HOST)
        require(len(sys.argv) == 2 and sys.argv[1] in ('preflight', 'status', 'enroll', 'confirm',
                                                    'replace-pending', 'consume', 'receipt'))
        action = sys.argv[1]
        binding = owner_snapshot()
        if action == 'preflight':
            print('active_owner_binding=verified; schema=0014; application_recovery=not_enabled')
            return 0
        with Store().locked() as store:
            value = store.read()
            if action == 'status':
                require(value is None or value['binding'] == binding)
                print('owner_credential=' + ('not_enrolled' if value is None else value['state']))
                return 0
            with Terminal() as tty:
                require(tty.ask('Personal unrecorded SSH terminal; account is yours; type OWNER') == 'OWNER')
                if action in ('enroll', 'confirm', 'replace-pending'):
                    if action in ('enroll', 'replace-pending'):
                        if action == 'enroll':
                            require(value is None)
                            code = store.issue(binding, int(time.time()))
                        else:
                            require(tty.ask('Revoke expired UNCONFIRMED code and issue a new one: type REPLACE-PENDING') == 'REPLACE-PENDING')
                            require(owner_snapshot() == binding)
                            code = store.replace_pending(binding, int(time.time()))
                        tty.show('Save this owner bootstrap recovery code privately. Do not send it to Codex.\n'
                                 + code + '\n')
                    entered = tty.ask('Re-enter saved code to confirm custody')
                    require(owner_snapshot() == binding)
                    store.authenticate(entered, binding, int(time.time()), confirm=True)
                    tty.show('Owner recovery credential enrolled. No application account was changed.\n')
                else:
                    operation = tty.ask('Exact reviewed recovery operation UUID')
                    require(str(UUID(operation)) == operation)
                    if action == 'consume':
                        entered = tty.ask('Single-use recovery code; this consumes it permanently')
                        require(owner_snapshot() == binding)
                        store.authenticate(entered, binding, int(time.time()), operation=operation)
                    require(owner_snapshot() == binding)
                    store.receipt(binding, operation, int(time.time()))
                    tty.show('Protected bootstrap evidence receipt valid for this operation only.\n'
                             'No factor, session, hold or account was changed.\n')
        return 0
    except BaseException:
        print(ERROR, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
