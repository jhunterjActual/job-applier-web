"""Root-operated admission installation and phased maintenance; never activation.

Run only through the reviewed wrapper from an immutable root-owned release.
No secret is accepted in argv/environment, printed, or shell-evaluated. Host
phases deliberately never SSH, start services, change release links or open signup.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from uuid import UUID, uuid4

ROOT = Path('/etc/careertrellis')
STATE = ROOT / 'admission-operations'
PLAN = ROOT / 'admission-install.json'
AUTH_FILE = ROOT / 'auth-v2.env'
BUNDLE = ROOT / 'admission-credential.age'
IDENTITY = ROOT / 'credential-transfer.agekey'
HBA = Path('/etc/postgresql/18/main/pg_hba.conf')
BROKER = 'careertrellis_admission_broker'
CALLERS = ('careertrellis_runtime', 'careertrellis_worker', 'careertrellis_erasure_worker', BROKER)
SERVICES = ('careertrellis-web.service', 'careertrellis-worker.service', 'careertrellis-erasure-worker.service')
WORKER_FILES = ('search-worker.env', 'postgres-worker.env', 'provider-secrets.env',
    'account-erasure-worker.env', 'postgres-erasure-worker.env', 'account-erasure-object-storage.env')
ERROR = 'Admission operation did not complete. Retain containment and inspect the protected phase before retrying.'
LOCK_KEY = 'careertrellis-admission-maintenance-v1'
INITIAL_SOURCE = '20260903_0013'
INITIAL_HEAD = '20260907_0014'
INITIAL_POLICY = {'singleton': True, 'phase': 'pilot', 'signup_open': False,
    'adapter_ready': False, 'minimum_session_version': 1, 'maintenance': False,
    'cap': 1, 'ttl': 600, 'generation': 0, 'realm': 'unconfigured'}


class OperationError(RuntimeError):
    pass


def require(value):
    if not value:
        raise OperationError(ERROR)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    def invalid_constant(_value):
        raise OperationError(ERROR)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def protected_parents(path, owners=(0,)):
    require(path.is_absolute())
    for parent in reversed(path.parents):
        item = parent.lstat()
        require(stat.S_ISDIR(item.st_mode) and item.st_uid in owners and not item.st_mode & 0o022)


def read_protected(path, *, uid=0, modes=(0o600,), limit=65536):
    protected_parents(path, (0, uid))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        item = os.fstat(descriptor)
        require(stat.S_ISREG(item.st_mode) and item.st_nlink == 1 and item.st_uid == uid
            and stat.S_IMODE(item.st_mode) in modes and item.st_size <= limit)
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            value = stream.read(limit + 1)
        require(len(value) <= limit)
        return value
    finally:
        os.close(descriptor)


def write_protected(path, value, *, previous=None, uid=0, gid=0, mode=0o600):
    """No clobber on creation; compare before replacement under the host lock."""
    protected_parents(path, (0, uid))
    if previous is not None:
        require(read_protected(path, uid=uid, modes=(mode,)) == previous)
    else:
        require(not path.exists() and not path.is_symlink())
    descriptor, temporary = tempfile.mkstemp(prefix='.admission-', dir=path.parent)
    try:
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, 'wb', closefd=False) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(descriptor)
        if previous is None:
            os.link(temporary, path, follow_symlinks=False)
        else:
            require(read_protected(path, uid=uid, modes=(mode,)) == previous)
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def state_write(path, value):
    previous = read_protected(path) if path.exists() else None
    write_protected(path, encoded(value), previous=previous)


def assignments(raw):
    """Strict common subset of systemd EnvironmentFile and application readers."""
    result = {}
    for line in raw.decode('utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Z][A-Z0-9_]*)=([^\s\x00-\x1f\x7f\'"`\\$;]*)', line)
        require(match is not None and match[1] not in result)
        result[match[1]] = match[2]
    return result


def check_worker_delivery():
    for name in WORKER_FILES:
        values = assignments(read_protected(ROOT / name))
        require(not any(key == 'CAREERTRELLIS_ADMISSION_DATABASE_URL' or
            key.startswith('CAREERTRELLIS_AUTH_V2_') for key in values))


def command(arguments, *, data=None):
    # Never inherit PG*, proxy, shell, systemd or Python overrides; stderr may
    # contain identities/configuration and never crosses the fixed-error boundary.
    result = subprocess.run(arguments, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}, timeout=120)
    require(result.returncode == 0)
    return result.stdout


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


class LocalPostgres:
    def query(self, sql):
        output = command(['/usr/sbin/runuser', '-u', 'postgres', '--', '/usr/bin/psql',
            '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-d', 'careertrellis'], data=(
            "SET log_statement='none'; SET log_min_error_statement='panic'; "
            "SET log_parameter_max_length=0; SET log_parameter_max_length_on_error=0; "
            "SET standard_conforming_strings=on; "
            "SET statement_timeout='60s'; SET lock_timeout='5s'; " + sql).encode())
        lines = output.decode().strip().splitlines()
        if not lines:
            return None
        return {'t': True, 'f': False}.get(lines[-1], None) if lines[-1] in ('t', 'f') else strict_json(lines[-1])


def policy(db):
    return db.query('SELECT row_to_json(p) FROM careertrellis_admission.policy p;')


def roles(db):
    return db.query("SELECT json_object_agg(rolname,rolcanlogin) FROM pg_roles WHERE rolname IN (" +
        ','.join(literal(role) for role in CALLERS) + ');')


def assert_roles(db):
    names = CALLERS + ('careertrellis_admission_owner',)
    require(db.query("SELECT count(*)=" + str(len(names)) + " FROM pg_roles WHERE rolname IN (" +
        ','.join(literal(role) for role in names) + ') AND NOT rolsuper AND NOT rolcreatedb '
        'AND NOT rolcreaterole AND NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls;') is True)
    require(db.query("SELECT NOT rolcanlogin FROM pg_roles WHERE rolname='careertrellis_admission_owner';") is True)
    require(db.query("SELECT NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member='" + BROKER + "'::regrole OR roleid='" + BROKER + "'::regrole);") is True)
    require(db.query("SELECT NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('careertrellis','careertrellis_admission','careertrellis_lifecycle') "
        "AND c.relkind IN ('r','p','v','m','f') AND has_table_privilege('" + BROKER +
        "',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'));") is True)
    require(db.query("SELECT NOT has_schema_privilege('" + BROKER + "','public','CREATE');") is True)


def assert_quiescent_sql():
    names = CALLERS + ('careertrellis_migration',)
    return "DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() " \
        "AND pid<>pg_backend_pid() AND usename IN (" + ','.join(literal(role) for role in names) + \
        ")) THEN RAISE EXCEPTION 'lifecycle callers remain'; END IF; END $$;"


def transaction(sql):
    return "BEGIN; SELECT pg_advisory_xact_lock(hashtextextended('" + LOCK_KEY + "',0)); " \
        "SELECT careertrellis_admission.lock_lifecycle(); " + sql + ' COMMIT;'


def assert_snapshot_sql(expected_policy, expected_roles):
    body = "BEGIN IF (SELECT to_jsonb(p) FROM careertrellis_admission.policy p) IS DISTINCT FROM " + \
        literal(encoded(expected_policy).decode()) + "::jsonb OR (SELECT jsonb_object_agg(rolname,rolcanlogin) FROM pg_roles WHERE rolname IN (" + \
        ','.join(literal(role) for role in CALLERS) + ")) IS DISTINCT FROM " + literal(encoded(expected_roles).decode()) + \
        "::jsonb THEN RAISE EXCEPTION 'maintenance state changed'; END IF; END"
    return 'DO ' + literal(body) + ';'


def revision(db):
    return db.query('SELECT to_json(version_num) FROM careertrellis.alembic_version;')


def initial_transaction(sql):
    # No call into admission functions: they do not exist at revision 0013.
    return "BEGIN; SELECT pg_advisory_xact_lock(hashtextextended('" + LOCK_KEY + "',0)); " \
        "SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-alembic-migrations-v1',0)); " + sql + ' COMMIT;'


def assert_initial_sql(expected_roles, *, pristine=False):
    body = "BEGIN IF (SELECT version_num FROM careertrellis.alembic_version) IS DISTINCT FROM " + \
        literal(INITIAL_SOURCE) + " OR (SELECT jsonb_object_agg(rolname,rolcanlogin) FROM pg_roles WHERE rolname IN (" + \
        ','.join(literal(role) for role in CALLERS) + ")) IS DISTINCT FROM " + literal(encoded(expected_roles).decode()) + \
        "::jsonb THEN RAISE EXCEPTION 'initial state changed'; END IF; "
    body += "IF (SELECT count(*) FROM pg_roles WHERE rolname IN ('careertrellis_runtime','careertrellis_worker'," \
        "'careertrellis_erasure_worker') AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole " \
        "AND NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls) <> 3 " \
        "THEN RAISE EXCEPTION 'initial caller role contract changed'; END IF; "
    if pristine:
        body += "IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ('careertrellis_admission_owner','careertrellis_admission_broker')) " \
            "OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspname IN ('careertrellis_admission','careertrellis_lifecycle')) " \
            "THEN RAISE EXCEPTION 'initial bootstrap collision'; END IF; "
    else:
        body += "IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE " \
            "n.nspname IN ('careertrellis_admission','careertrellis_lifecycle')) OR EXISTS(SELECT 1 FROM pg_proc p " \
            "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('careertrellis_admission','careertrellis_lifecycle')) " \
            "THEN RAISE EXCEPTION 'initial schemas changed'; END IF; "
    return 'DO ' + literal(body + 'END') + ';'


def initial_record(operation):
    require(str(UUID(operation)) == operation)
    record = strict_json(read_protected(STATE / 'initial.json'))
    require(record['operation'] == operation and record['source'] == INITIAL_SOURCE
        and record['head'] == INITIAL_HEAD and re.fullmatch(r'[0-9a-f]{40}', record['target'])
        and record['boot'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    require(set(record['roles']) == set(CALLERS[:-1])
        and all(type(value) is bool for value in record['roles'].values()))
    return record


def initial_receipt(record, phase):
    return {key: record[key] for key in ('operation', 'target', 'source', 'head')} | {'phase': phase}


def assert_passwordless_broker(db):
    require(db.query("SELECT rolpassword IS NULL AND NOT rolcanlogin FROM pg_authid WHERE rolname='" + BROKER + "';") is True)


def begin_initial(db, operation, target, bootstrap_sql):
    require(str(UUID(operation)) == operation and re.fullmatch(r'[0-9a-f]{40}', target))
    require(not (STATE / 'initial.json').exists())
    require(not list(STATE.glob('maintenance-*.json')) and not (STATE / 'install.json').exists())
    before = roles(db)
    require(isinstance(before, dict) and set(before) == set(CALLERS[:-1])
        and all(type(value) is bool for value in before.values()))
    # Repeat this snapshot check inside the actual bootstrap/fence transaction.
    db.query(initial_transaction(assert_initial_sql(before, pristine=True)))
    record = {'phase': 'prepared', 'operation': operation, 'target': target, 'roles': before,
        'source': INITIAL_SOURCE, 'head': INITIAL_HEAD,
        'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    write_protected(STATE / 'initial.json', encoded(record))
    db.query(initial_transaction(assert_initial_sql(before, pristine=True) + bootstrap_sql +
        ' '.join('ALTER ROLE ' + role + ' NOLOGIN;' for role in CALLERS)))
    record['phase'] = 'bootstrapped_fenced'
    state_write(STATE / 'initial.json', record)
    return quiesce_initial(db, operation)


def quiesce_initial(db, operation):
    record = initial_record(operation)
    require(record['phase'] in ('prepared', 'bootstrapped_fenced', 'initial_quiesced'))
    assert_roles(db)
    assert_passwordless_broker(db)
    db.query(initial_transaction(assert_initial_sql(dict.fromkeys(CALLERS, False)) + assert_quiescent_sql()))
    record['phase'] = 'initial_quiesced'
    state_write(STATE / 'initial.json', record)
    receipt = initial_receipt(record, 'initial_quiesced')
    path = STATE / ('database-initial-' + operation + '.json')
    if path.exists():
        require(strict_json(read_protected(path)) == receipt)
    else:
        write_protected(path, encoded(receipt))
    return receipt


def finish_initial(db, operation, acknowledgement):
    record = initial_record(operation)
    require(record['phase'] == 'initial_quiesced')
    require(acknowledgement == initial_receipt(record, 'initial_migrated'))
    require(revision(db) == INITIAL_HEAD and policy(db) == INITIAL_POLICY)
    assert_roles(db)
    assert_passwordless_broker(db)
    restored = {**record['roles'], BROKER: False}
    current = roles(db)
    require(current in (dict.fromkeys(CALLERS, False), restored))
    # Lost completion acknowledgement: reconcile the same successful web receipt
    # and exact default-off state, never repeat migration or change policy.
    restore = ' '.join('ALTER ROLE ' + role + (' LOGIN;' if login else ' NOLOGIN;')
        for role, login in record['roles'].items()) if current != restored else ''
    db.query(initial_transaction('SELECT careertrellis_admission.lock_lifecycle(); ' + assert_snapshot_sql(INITIAL_POLICY, current) +
        "DO $$ BEGIN IF (SELECT version_num FROM careertrellis.alembic_version) <> '20260907_0014' "
        "THEN RAISE EXCEPTION 'initial head changed'; END IF; END $$;" + assert_quiescent_sql() + restore))
    record['phase'] = 'released_services_still_stopped'
    state_write(STATE / 'initial.json', record)


def initial_finished():
    if (STATE / 'initial.json').exists():
        require(strict_json(read_protected(STATE / 'initial.json'))['phase'] == 'released_services_still_stopped')


def validate_plan(value):
    require(isinstance(value, dict) and set(value) == {'recipient', 'source_cidr', 'database_host', 'transport'})
    require(re.fullmatch(r'age1[023456789acdefghjklmnpqrstuvwxyz]{58}', value['recipient']))
    source = ipaddress.ip_network(value['source_cidr'], strict=True)
    host = ipaddress.ip_address(value['database_host'])
    require(source.prefixlen == source.max_prefixlen and host.version == source.version
        and host.is_private and not host.is_loopback and source.network_address.is_private
        and not source.network_address.is_loopback)
    require(value['transport'] in ('wireguard', 'tls'))
    # WireGuard-only deployment contract; not an arbitrary private-network bypass.
    if value['transport'] == 'wireguard':
        require(str(source) == '10.70.0.1/32' and str(host) == '10.70.0.2')
    return value


def hba_candidate(original, plan):
    require(b'CAREERTRELLIS ADMISSION' not in original)
    require(not any(line.lstrip().startswith(b'include') for line in original.splitlines()))
    allow = 'hostssl' if plan['transport'] == 'tls' else 'host'
    return (f'# BEGIN CAREERTRELLIS ADMISSION\n{allow} careertrellis {BROKER} '
        f"{plan['source_cidr']} scram-sha-256\nhost all {BROKER} all reject\n"
        f'local all {BROKER} reject\n# END CAREERTRELLIS ADMISSION\n').encode() + original


def scram(password):
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac('sha256', password.encode('ascii'), salt, 4096)
    client = hmac.digest(salted, b'Client Key', 'sha256')
    server = hmac.digest(salted, b'Server Key', 'sha256')
    b64 = lambda value: base64.b64encode(value).decode('ascii')
    return f'SCRAM-SHA-256$4096:{b64(salt)}${b64(hashlib.sha256(client).digest())}:{b64(server)}'


def install_database(db, plan, *, encrypt, hba_path=HBA, postgres_uid=None, postgres_gid=None):
    """Initial install only: ambiguous or existing material requires inspection."""
    import pwd
    initial_finished()
    plan = validate_plan(plan)
    assert_roles(db)
    require(revision(db) == '20260907_0014')
    require(db.query("SELECT rolpassword IS NULL AND NOT rolcanlogin FROM pg_authid WHERE rolname='" + BROKER + "';") is True)
    require(db.query("SELECT current_setting('pgaudit.log',true) IS NULL OR current_setting('pgaudit.log',true) IN ('','none');") is True)
    require(db.query('SELECT to_json(current_setting(\'hba_file\'));') == str(hba_path))
    db.query(transaction(assert_quiescent_sql()))
    postgres = pwd.getpwnam('postgres') if postgres_uid is None else None
    uid, gid = (postgres.pw_uid, postgres.pw_gid) if postgres else (postgres_uid, postgres_gid)
    original = read_protected(hba_path, uid=uid, modes=(0o600, 0o640), limit=32768)
    mode = stat.S_IMODE(hba_path.stat().st_mode)
    candidate = hba_candidate(original, plan)
    marker = STATE / 'install.json'
    require(not marker.exists() and not BUNDLE.exists() and not BUNDLE.is_symlink())
    state_write(marker, {'phase': 'prepared', 'hba_original': base64.b64encode(original).decode(),
        'policy': policy(db), 'operation': str(uuid4())})
    password = secrets.token_urlsafe(48)
    # Encrypt before database mutation. A retained bundle is never overwritten or
    # used as permission to rotate a credential after a lost acknowledgement.
    ciphertext = encrypt(encoded({'version': 1, 'password': password,
        'database_host': plan['database_host'], 'transport': plan['transport']}), plan['recipient'])
    require(ciphertext.startswith(b'age-encryption.org/v1'))
    write_protected(BUNDLE, ciphertext)
    write_protected(hba_path, candidate, previous=original, uid=uid, gid=gid, mode=mode)
    require(db.query('SELECT count(*)=0 FROM pg_hba_file_rules WHERE error IS NOT NULL;') is True)
    require(db.query('SELECT pg_reload_conf();') is True)
    # Keep NOLOGIN: web installation and explicit login enable are distinct.
    db.query(transaction(assert_quiescent_sql() + 'ALTER ROLE ' + BROKER + ' NOLOGIN PASSWORD ' + literal(scram(password)) + ';'))
    record = strict_json(read_protected(marker))
    require(policy(db) == record['policy'])
    record['phase'] = 'installed_nologin'
    state_write(marker, record)


def enable_broker(db):
    marker = STATE / 'install.json'
    record = strict_json(read_protected(marker))
    require(record['phase'] == 'installed_nologin')
    assert_roles(db)
    require(policy(db) == record['policy'])
    db.query(transaction(assert_quiescent_sql() + 'ALTER ROLE ' + BROKER + ' LOGIN;'))
    record['phase'] = 'login_enabled'
    state_write(marker, record)


def begin_maintenance(db, operation, target):
    initial_finished()
    require(str(UUID(operation)) == operation and re.fullmatch(r'[0-9a-f]{40}', target))
    path = STATE / ('maintenance-' + operation + '.json')
    require(not path.exists())
    for existing in STATE.glob('maintenance-*.json'):
        require(strict_json(read_protected(existing))['phase'] == 'released_services_still_stopped')
    assert_roles(db)
    previous = policy(db)
    require(previous['maintenance'] is False and previous['signup_open'] is False)
    record = {'phase': 'prepared', 'operation': operation, 'target': target, 'policy': previous,
        'roles': roles(db), 'revision': revision(db), 'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    state_write(path, record)
    # Commit NOLOGIN before testing quiescence; failure leaves a durable fence.
    db.query(transaction(assert_snapshot_sql(previous, record['roles']) + "UPDATE careertrellis_admission.policy SET maintenance=true; " +
        ' '.join('ALTER ROLE ' + role + ' NOLOGIN;' for role in CALLERS)))
    record['phase'] = 'fenced'
    state_write(path, record)
    quiesce_maintenance(db, operation)


def quiesce_maintenance(db, operation):
    """Explicit reconciliation after an owned client stopped; never kills it."""
    path = STATE / ('maintenance-' + str(UUID(operation)) + '.json')
    record = strict_json(read_protected(path))
    require(record['phase'] in ('prepared', 'fenced', 'quiesced')
        and record['boot'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    current = {**record['policy'], 'maintenance': True}
    require(policy(db) == current and roles(db) == dict.fromkeys(CALLERS, False))
    db.query(transaction(assert_snapshot_sql(current, dict.fromkeys(CALLERS, False)) + assert_quiescent_sql()))
    record['phase'] = 'quiesced'
    state_write(path, record)


def finish_maintenance(db, operation, acknowledgement):
    path = STATE / ('maintenance-' + str(UUID(operation)) + '.json')
    record = strict_json(read_protected(path))
    require(record['phase'] == 'quiesced' and record['boot'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    require(acknowledgement == {'phase': 'migrated', 'operation': operation, 'target': record['target'],
        'head': revision(db)})
    current = policy(db)
    if current == record['policy'] and roles(db) == record['roles']:
        # Reconcile a lost acknowledgement only against the complete expected
        # restored state and the same accepted migration receipt; no repeat SQL.
        db.query(transaction(assert_snapshot_sql(current, record['roles']) + assert_quiescent_sql()))
        record['phase'] = 'released_services_still_stopped'
        state_write(path, record)
        return
    require(current == {**record['policy'], 'maintenance': True})
    require(roles(db) == dict.fromkeys(CALLERS, False))
    assert_roles(db)
    # Caller must transfer the root-owned web success receipt explicitly. No
    # automatic service restart, signup reopening, floor lowering or key rotation.
    restore = ' '.join('ALTER ROLE ' + role + (' LOGIN;' if record['roles'][role] else ' NOLOGIN;') for role in CALLERS)
    db.query(transaction("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-alembic-migrations-v1',0)); " +
        assert_snapshot_sql(current, dict.fromkeys(CALLERS, False)) + assert_quiescent_sql() + restore +
        ' UPDATE careertrellis_admission.policy SET maintenance=false;'))
    record['phase'] = 'released_services_still_stopped'
    state_write(path, record)


def stopped_services():
    for service in SERVICES:
        state = command(['/usr/bin/systemctl', 'show', service, '--property=ActiveState', '--value']).strip()
        require(state == b'inactive')


def migration_connection_url(database):
    from postgres_database import postgres_url
    migration = assignments(read_protected(ROOT / 'postgres-migration.env'))
    runtime = assignments(read_protected(ROOT / 'postgres-runtime.env'))
    require(set(migration) == {'CAREERTRELLIS_MIGRATION_DATABASE_URL'}
        and set(runtime) == {'CAREERTRELLIS_DATABASE_URL'})
    urls = (postgres_url(migration['CAREERTRELLIS_MIGRATION_DATABASE_URL']),
        postgres_url(runtime['CAREERTRELLIS_DATABASE_URL']))
    for url, principal in zip(urls, ('careertrellis_migration', 'careertrellis_runtime')):
        # libpq query keys override authority fields. Allow only explicit TLS
        # configuration, never alternate host/db/user/password/service sources.
        require(url.username == principal and url.database == database and url.host and url.password
            and set(url.query) <= {'sslmode', 'sslrootcert'}
            and all(isinstance(value, str) and value for value in url.query.values()))
        if database == 'careertrellis':
            require((url.port or 5432) == 5432)
            host = ipaddress.ip_address(url.host)
            require(host.is_private and not host.is_loopback)
            if url.host != '10.70.0.2':
                require(url.query.get('sslmode') == 'verify-full' and url.query.get('sslrootcert', '').startswith('/'))
        else:
            require(database == 'ct_signup_integration' and url.host == '127.0.0.1' and not url.query)
    identity = lambda url: (url.host, url.port or 5432, url.database, dict(url.query))
    require(identity(urls[0]) == identity(urls[1]))
    return urls[0]


def migrate_web(operation, target, release, *, database='careertrellis', initial=False):
    """Web-host migration under the separate, already-established DB fence."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool
    from alembic import command as alembic
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from apply_postgres_runtime_grants import apply_runtime_grants
    import admission_migration_preflight
    require(str(UUID(operation)) == operation and release.name == target)
    stopped_services()
    check_worker_delivery()
    receipt = strict_json(read_protected(STATE / ('database-initial-quiesced.json' if initial else 'database-quiesced.json')))
    expected_receipt = {'phase': 'quiesced', 'operation': operation, 'target': target}
    if initial:
        expected_receipt.update(phase='initial_quiesced', source=INITIAL_SOURCE, head=INITIAL_HEAD)
    require(receipt == expected_receipt)
    url = migration_connection_url(database)
    attempt = STATE / ('web-attempt-' + operation + '.json')
    require(not attempt.exists())
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={'connect_timeout': 5})
    try:
        admission_migration_preflight.check(engine)
        with engine.begin() as connection:
            require(connection.scalar(text("SELECT count(*) FROM pg_roles WHERE rolname IN (" +
                ','.join(literal(role) for role in CALLERS) + ') AND NOT rolcanlogin')) == len(CALLERS))
            if initial:
                connection.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
                connection.exec_driver_sql(assert_initial_sql(dict.fromkeys(CALLERS, False)))
            else:
                connection.exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
                require(connection.scalar(text('SELECT maintenance FROM careertrellis_admission.policy')) is True)
        config = Config()
        config.set_main_option('script_location', str(release / 'backend/migrations'))
        config.attributes['migration_url'] = url
        expected = ScriptDirectory.from_config(config).get_current_head()
        require(not initial or expected == INITIAL_HEAD)
        write_protected(attempt, encoded({'phase': 'migration_started', 'operation': operation, 'target': target,
            'head': expected}))
        alembic.upgrade(config, 'head')
        apply_runtime_grants(release / 'deploy/postgres/grant-runtime.sql',
            migration_url=url.render_as_string(hide_password=False))
        with engine.connect() as connection:
            connection.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
            require(connection.scalar(text('SELECT version_num FROM careertrellis.alembic_version')) == expected)
        if initial:
            with engine.begin() as connection:
                connection.exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
                require(connection.scalar(text('SELECT to_jsonb(p) FROM careertrellis_admission.policy p')) == INITIAL_POLICY)
        result = {'phase': 'migrated', 'operation': operation, 'target': target, 'head': expected}
        if initial:
            result.update(phase='initial_migrated', source=INITIAL_SOURCE)
        write_protected(STATE / ('web-' + operation + '.json'), encoded(result))
        return result
    finally:
        engine.dispose()


def install_web(decrypt):
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool
    from cryptography.fernet import Fernet
    from postgres_database import postgres_url
    from workos_transport_candidate import ProviderSettings
    from account_session_candidate import SessionCodec
    check_worker_delivery()
    stopped_services()
    require(not AUTH_FILE.exists() and not AUTH_FILE.is_symlink())
    staging = assignments(read_protected(ROOT / 'staging.env'))
    objects = assignments(read_protected(ROOT / 'object-storage.env'))
    require(not any(key == 'CAREERTRELLIS_ADMISSION_DATABASE_URL' or key.startswith('CAREERTRELLIS_AUTH_V2_')
        for key in (*staging, *objects)))
    metadata = strict_json(read_protected(ROOT / 'admission-web.json'))
    require(isinstance(metadata, dict) and set(metadata) in (
        {'issuer', 'generation', 'key_id', 'session_seconds'},
        {'issuer', 'generation', 'key_id', 'session_seconds', 'audience'}))
    provider = ProviderSettings(staging['WORKOS_CLIENT_ID'], staging['WORKOS_API_KEY'],
        metadata['issuer'], metadata['generation'], metadata.get('audience'))
    key, flow_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    require(len({base64.urlsafe_b64decode(value) for value in (key, flow_key, staging['WORKOS_COOKIE_PASSWORD'])}) == 3)
    SessionCodec(key, staging['WORKOS_COOKIE_PASSWORD'], key_id=metadata['key_id'],
        generation=metadata['generation'], lifetime_seconds=metadata['session_seconds'], provider_binding=provider.binding)
    material = strict_json(decrypt(read_protected(BUNDLE)))
    require(set(material) == {'version', 'password', 'database_host', 'transport'} and material['version'] == 1
        and re.fullmatch(r'[A-Za-z0-9_-]{64}', material['password']))
    runtime_values = assignments(read_protected(ROOT / 'postgres-runtime.env'))
    require(set(runtime_values) == {'CAREERTRELLIS_DATABASE_URL'})
    runtime = postgres_url(runtime_values['CAREERTRELLIS_DATABASE_URL'])
    require(runtime.username == 'careertrellis_runtime' and runtime.host == material['database_host']
        and runtime.database == 'careertrellis' and (runtime.port or 5432) == 5432
        and set(runtime.query) <= {'sslmode', 'sslrootcert'})
    require(material['transport'] in ('wireguard', 'tls'))
    if material['transport'] == 'tls':
        require(runtime.query.get('sslmode') == 'verify-full' and runtime.query.get('sslrootcert'))
    else:
        require(runtime.host == '10.70.0.2')
    broker = runtime.set(username=BROKER, password=material['password'])
    # Mandatory DB proof before publishing any credential file. No provider call.
    engine = create_engine(broker, poolclass=NullPool, hide_parameters=True, connect_args={'connect_timeout': 5})
    try:
        with engine.connect() as connection:
            require(connection.scalar(text('SELECT session_user')) == BROKER)
            require(connection.scalar(text('SELECT careertrellis_admission.availability()'))['status'] == 'closed')
    finally:
        engine.dispose()
    values = {'CAREERTRELLIS_AUTH_V2_ENABLED': '0',
        'CAREERTRELLIS_ADMISSION_DATABASE_URL': broker.render_as_string(hide_password=False),
        'CAREERTRELLIS_AUTH_V2_ENVELOPE_KEY': key,
        'CAREERTRELLIS_AUTH_V2_FLOW_KEY': flow_key,
        'CAREERTRELLIS_AUTH_V2_ISSUER': metadata['issuer'],
        'CAREERTRELLIS_AUTH_V2_GENERATION': metadata['generation'],
        'CAREERTRELLIS_AUTH_V2_KEY_ID': metadata['key_id'],
        'CAREERTRELLIS_AUTH_V2_SESSION_SECONDS': str(metadata['session_seconds'])}
    if 'audience' in metadata:
        values['CAREERTRELLIS_AUTH_V2_AUDIENCE'] = metadata['audience']
    raw = ''.join(key + '=' + value + '\n' for key, value in values.items()).encode()
    require(assignments(raw) == values)
    # Every metadata value was explicit; installation never enables activation.
    write_protected(AUTH_FILE, raw)
    check_worker_delivery()


@contextmanager
def host_locks():
    import fcntl
    descriptors = []
    try:
        for name in ('careertrellis-maintenance.lock', 'careertrellis-postgres-migration.lock', 'careertrellis-admission-operation.lock'):
            path = Path('/run/lock') / name
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            descriptors.append(descriptor)
            item = os.fstat(descriptor)
            require(stat.S_ISREG(item.st_mode) and item.st_uid == 0 and item.st_nlink == 1)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def main(argv=None):
    try:
        require(getattr(os, 'geteuid', lambda: -1)() == 0)
        args = list(sys.argv[1:] if argv is None else argv)
        require(args and args[0] in ('check-worker-files', 'db-install', 'db-enable-login', 'web-install',
            'db-begin', 'db-quiesce', 'web-migrate', 'db-finish',
            'db-initial-begin', 'db-initial-quiesce', 'web-initial-migrate', 'db-initial-finish'))
        operation = args[0]
        source = Path(__file__).absolute()
        protected_parents(source)
        read_protected(source, modes=(0o600, 0o640, 0o644), limit=131072)
        if operation in ('web-install', 'web-migrate', 'web-initial-migrate',
                'db-initial-begin', 'db-initial-quiesce', 'db-initial-finish'):
            require(re.fullmatch(r'[0-9a-f]{40}', source.parent.parent.name)
                and source.parent.parent.parent == Path('/opt/careertrellis/staging/releases'))
            sys.path.insert(0, str(source.parent))
        require(len(args) == (3 if operation in ('db-begin', 'web-migrate', 'db-initial-begin', 'web-initial-migrate')
            else 2 if operation in ('db-finish', 'db-quiesce', 'db-initial-quiesce', 'db-initial-finish') else 1))
        if operation == 'check-worker-files':
            check_worker_delivery()
            print('operation=check-worker-files\nresult=ok\nvalues_disclosed=no')
            return 0
        with host_locks():
            if operation == 'check-worker-files':
                check_worker_delivery()
            else:
                if not STATE.exists():
                    protected_parents(STATE)
                    STATE.mkdir(mode=0o700)
                protected_parents(STATE / 'sentinel')
                require(stat.S_IMODE(STATE.stat().st_mode) == 0o700)
                db = LocalPostgres()
                if operation.startswith('db-'):
                    require(db.query("SELECT current_database()='careertrellis' AND session_user='postgres' "
                        "AND current_setting('server_version_num')::int BETWEEN 180000 AND 189999 "
                        "AND current_setting('data_directory')='/var/lib/postgresql/18/main';") is True)
                if operation == 'db-initial-begin':
                    require(source.parent.parent.name == args[2])
                    sql = read_protected(source.parent.parent / 'deploy/postgres/bootstrap-admission-roles.sql',
                        modes=(0o600, 0o640, 0o644), limit=32768).decode('utf-8')
                    begin_initial(db, args[1], args[2], sql)
                elif operation == 'db-initial-quiesce':
                    require(source.parent.parent.name == initial_record(args[1])['target'])
                    quiesce_initial(db, args[1])
                elif operation == 'db-initial-finish':
                    require(source.parent.parent.name == initial_record(args[1])['target'])
                    finish_initial(db, args[1], strict_json(read_protected(STATE / 'web-initial-migrated.json')))
                elif operation == 'db-install':
                    install_database(db, strict_json(read_protected(PLAN)), encrypt=lambda raw, recipient:
                        command(['/usr/bin/age', '--encrypt', '--recipient', recipient], data=raw))
                elif operation == 'db-enable-login':
                    enable_broker(db)
                elif operation == 'web-install':
                    read_protected(IDENTITY)
                    install_web(lambda raw: command(['/usr/bin/age', '--decrypt', '--identity', str(IDENTITY)], data=raw))
                elif operation == 'db-begin':
                    begin_maintenance(db, args[1], args[2])
                    write_protected(STATE / ('database-' + args[1] + '.json'), encoded(
                        {'phase': 'quiesced', 'operation': args[1], 'target': args[2]}))
                elif operation == 'db-quiesce':
                    quiesce_maintenance(db, args[1])
                    record = strict_json(read_protected(STATE / ('maintenance-' + args[1] + '.json')))
                    result = {'phase': 'quiesced', 'operation': args[1], 'target': record['target']}
                    receipt_path = STATE / ('database-' + args[1] + '.json')
                    if receipt_path.exists():
                        require(strict_json(read_protected(receipt_path)) == result)
                    else:
                        write_protected(receipt_path, encoded(result))
                elif operation == 'db-finish':
                    finish_maintenance(db, args[1], strict_json(read_protected(STATE / 'web-migrated.json')))
                else:
                    require(re.fullmatch(r'[0-9a-f]{40}', args[2]))
                    release = Path('/opt/careertrellis/staging/releases') / args[2]
                    protected_parents(release / 'backend/admission_operations.py')
                    require(Path(__file__).resolve() == release / 'backend/admission_operations.py')
                    migrate_web(args[1], args[2], release, initial=operation == 'web-initial-migrate')
        print('operation=' + operation + '\nresult=ok\nservices_started=no\nvalues_disclosed=no')
        return 0
    except (Exception, KeyboardInterrupt):
        print(ERROR, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
