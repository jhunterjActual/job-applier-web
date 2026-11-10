"""Bounded native preflight facts; imports perform no host/provider operation.

Credentials stay in protected process memory and sealed anonymous descriptors.
Only aggregate facts escape. The caller verifies policy/artifact bindings and
budgets before stopping writers; these reads never grant mutation authority.
"""
import configparser
from contextlib import contextmanager
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import urllib.request

try:
    import fcntl
    import pwd
except ImportError:  # Portable policy/test discovery; native entry points refuse.
    fcntl = None
    pwd = None

try:
    from .common import Refused
except ImportError:
    from common import Refused


WEB_HOST = 'careertrellis-staging-web-1'
DB_HOST = 'job-applier-db-staging-01'
STATE = Path('/var/lib/careertrellis/generated-object-recovery')
WEB_CONFIG = STATE / 'rclone.conf'
WEB_ENV = Path('/etc/careertrellis/generated-object-recovery.env')
DB_CONFIG = Path('/etc/careertrellis/rclone.conf')
PG_CONFIG = Path('/etc/pgbackrest/pgbackrest.conf')
PG_INCLUDES = Path('/etc/pgbackrest/conf.d')
PG_LEGACY = Path('/etc/pgbackrest.conf')
REPOSITORY_LOCK = Path('/run/lock/careertrellis-pgbackrest-repository.lock')
RCLONE = Path('/usr/local/bin/rclone')
EGRESS_URL = 'https://api.ipify.org'
BASE_ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}
RCLONE_OPTIONS = ['--stats', '0', '--log-level', 'ERROR', '--retries', '1',
                 '--low-level-retries', '1', '--contimeout', '15s', '--timeout', '45s']
REPOSITORY_UNITS = tuple('careertrellis-pgbackrest-' + name + '.service' for name
                        in ('full', 'differential', 'onedrive', 'inventory', 'restore')) + (
                            'careertrellis-backup-health.service',)


def need(value, code):
    if not value:
        raise Refused(code)


def role(host):
    need(hasattr(os, 'geteuid') and hasattr(os, 'uname') and os.geteuid() == 0
         and os.uname().nodename == host, 'provider_host_refused')
    need(fcntl is not None and pwd is not None, 'provider_native_unavailable')


class Snapshot:
    __slots__ = ('data', 'identity')

    def __init__(self, data, identity):
        self.data, self.identity = data, identity

    def __repr__(self):
        return '<protected snapshot>'


def identity(meta):
    return (meta.st_dev, meta.st_ino, meta.st_uid, meta.st_gid,
            stat.S_IMODE(meta.st_mode), meta.st_nlink, meta.st_size,
            meta.st_mtime_ns, meta.st_ctime_ns)


def parents(path, owned_parent=None):
    for parent in path.parents:
        meta = parent.lstat()
        uid = owned_parent[1] if owned_parent and parent == owned_parent[0] else 0
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == uid
             and not meta.st_mode & 0o022, 'provider_path_metadata')


def protected(path, *, uid=0, gids=(0,), modes=(0o600,), limit=65536,
              owned_parent=None):
    """Read one fixed regular file with path/descriptor identity checks."""
    path = Path(path)
    parents(path, owned_parent)
    before = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        meta = os.fstat(descriptor)
        need(identity(before) == identity(meta), 'provider_file_replaced')
        need(stat.S_ISREG(meta.st_mode) and meta.st_uid == uid and meta.st_gid in gids
             and stat.S_IMODE(meta.st_mode) in modes and meta.st_nlink == 1
             and 0 < meta.st_size <= limit, 'provider_file_metadata')
        data = os.read(descriptor, limit + 1)
        need(len(data) == meta.st_size and identity(os.fstat(descriptor)) == identity(meta)
             and identity(path.lstat()) == identity(meta), 'provider_file_changed')
        return Snapshot(data, identity(meta))
    finally:
        os.close(descriptor)


def unchanged(path, snapshot, **options):
    current = protected(path, **options)
    need(current.identity == snapshot.identity and current.data == snapshot.data,
         'provider_credentials_changed')


def configuration(raw):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string(raw.decode('utf-8'))
    need(not parser.defaults(), 'provider_config_scope')
    return parser


def verify_binary(path):
    parents(path)
    meta = path.lstat()
    need(stat.S_ISREG(meta.st_mode) and meta.st_uid == 0 and meta.st_nlink == 1
         and not meta.st_mode & 0o022 and meta.st_mode & 0o111,
         'provider_binary_metadata')


@contextmanager
def sealed(raw, uid=0, gid=0):
    descriptor = os.memfd_create('careertrellis-provider-readonly',
                                os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
    try:
        offset = 0
        while offset < len(raw):
            amount = os.write(descriptor, raw[offset:])
            need(type(amount) is int and amount > 0, 'provider_seal_write')
            offset += amount
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.fchmod(descriptor, 0o400)
        os.fchown(descriptor, uid, gid)
        flags = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SEAL
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, flags)
        need(fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) == flags, 'provider_seal_unverified')
        yield descriptor
    finally:
        os.close(descriptor)


def run_json(args, *, descriptors=(), timeout=120, input_data=None, code='provider_command_failed'):
    proc = subprocess.run(args, input=input_data, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=timeout,
                          pass_fds=descriptors, env=dict(BASE_ENV))
    need(proc.returncode == 0 and isinstance(proc.stdout, bytes)
         and 0 < len(proc.stdout) <= 16384, code)
    value = json.loads(proc.stdout)
    need(type(value) is dict, code)
    return value


def aggregates(source, quota):
    need(all(type(source.get(key)) is int and source[key] >= 0 for key in ('count', 'bytes'))
         and type(quota.get('free')) is int and quota['free'] >= 0,
         'provider_aggregate_invalid')
    return {'source_count': source['count'], 'source_bytes': source['bytes'],
            'onedrive_free_bytes': quota['free'], 'credential_files_unchanged': True}


def rclone_json(operation, target, descriptor, *, lock=None, account=None):
    args = [str(RCLONE), operation, target, '--json', '--config',
            '/proc/self/fd/' + str(descriptor), *RCLONE_OPTIONS]
    if account is not None:
        args = ['/usr/bin/setpriv', '--reuid=' + str(account.pw_uid),
                '--regid=' + str(account.pw_gid), '--init-groups', '--reset-env', '--', *args]
    descriptors = (descriptor,) if lock is None else (descriptor, lock)
    return run_json(args, descriptors=descriptors)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise Refused('provider_egress_redirect')


def egress_ipv4_cidr():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(EGRESS_URL, headers={'Accept': 'text/plain'})
        with opener.open(request, timeout=15) as response:
            need(response.status == 200 and response.geturl() == EGRESS_URL,
                 'provider_egress_response')
            raw = response.read(65)
        need(isinstance(raw, bytes) and 0 < len(raw) <= 64, 'provider_egress_response')
        address = ipaddress.IPv4Address(raw.decode('ascii').strip())
        need(address.is_global, 'provider_egress_response')
        return str(address) + '/32'
    except Refused:
        raise
    except Exception:
        raise Refused('provider_egress_unavailable') from None


def web_facts(*, egress_reader=None):
    try:
        role(WEB_HOST)
        verify_binary(RCLONE)
        account = pwd.getpwnam('careertrellis-object-recovery')
        need(account.pw_uid > 0 and account.pw_gid > 0, 'provider_account_refused')
        options = {'uid': account.pw_uid, 'gids': (account.pw_gid,),
                   'limit': 32768, 'owned_parent': (STATE, account.pw_uid)}
        source_env = protected(WEB_ENV, limit=16384)
        stored = protected(WEB_CONFIG, **options)
        values = {}
        for line in source_env.data.decode('utf-8').splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            key, separator, value = line.partition('=')
            need(separator == '=' and key not in values and value
                 and all(ord(c) >= 32 and ord(c) != 127 for c in value),
                 'provider_environment_scope')
            values[key] = value
        prefix = 'CAREERTRELLIS_GENERATED_OBJECT_'
        need(set(values) == {prefix + key for key in ('SOURCE_ENDPOINT', 'SOURCE_BUCKET',
             'SOURCE_ACCESS_KEY_ID', 'SOURCE_SECRET_ACCESS_KEY', 'ONEDRIVE_REMOTE', 'ONEDRIVE_ROOT')},
             'provider_environment_scope')
        bucket, remote = values[prefix + 'SOURCE_BUCKET'], values[prefix + 'ONEDRIVE_REMOTE']
        need(re.fullmatch('[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket) is not None
             and re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,63}', remote) is not None
             and remote.lower() not in ('generatedobjectsource', 'generatedobjectrestore')
             and values[prefix + 'ONEDRIVE_ROOT'] == 'careertrellis/generated-objects'
             and re.fullmatch(r'https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com/?',
                              values[prefix + 'SOURCE_ENDPOINT']) is not None
             and re.fullmatch('[A-Za-z0-9]{8,128}', values[prefix + 'SOURCE_ACCESS_KEY_ID']) is not None
             and re.fullmatch('[A-Za-z0-9_+/=-]{16,256}', values[prefix + 'SOURCE_SECRET_ACCESS_KEY']) is not None,
             'provider_target_scope')
        config = configuration(stored.data)
        need(config.sections() == [remote] and config[remote].get('type') == 'onedrive',
             'provider_config_scope')
        # A sealed combined config permits --reset-env without putting credentials
        # into process arguments or retaining provider variables in the environment.
        additions = '\n[generatedobjectsource]\ntype = s3\nprovider = Cloudflare\nno_check_bucket = true\n'
        for option, key in (('endpoint', 'SOURCE_ENDPOINT'), ('access_key_id', 'SOURCE_ACCESS_KEY_ID'),
                            ('secret_access_key', 'SOURCE_SECRET_ACCESS_KEY')):
            additions += option + ' = ' + values[prefix + key] + '\n'
        try:
            with sealed(stored.data + additions.encode('utf-8'), account.pw_uid, account.pw_gid) as descriptor:
                source = rclone_json('size', 'generatedobjectsource:' + bucket, descriptor, account=account)
                quota = rclone_json('about', remote + ':', descriptor, account=account)
            result = aggregates(source, quota)
            result['egress_ipv4_cidr'] = (egress_reader or egress_ipv4_cidr)()
            network = ipaddress.ip_network(result['egress_ipv4_cidr'], strict=True)
            need(isinstance(network, ipaddress.IPv4Network) and network.prefixlen == 32
                 and network.network_address.is_global, 'provider_egress_response')
            return result
        finally:
            unchanged(WEB_CONFIG, stored, **options)
            unchanged(WEB_ENV, source_env, limit=16384)
    except Refused:
        raise
    except Exception:
        raise Refused('provider_web_unavailable') from None


DATABASE_SQL = """SET log_statement='none'; SET log_min_error_statement='panic';
SET log_parameter_max_length=0; SET log_parameter_max_length_on_error=0;
SET default_transaction_read_only=on;
BEGIN READ ONLY;
SET LOCAL statement_timeout='3s'; SET LOCAL lock_timeout='250ms';
SET LOCAL transaction_timeout='5s'; SET LOCAL search_path=pg_catalog;
SELECT json_build_object(
 'schema',(SELECT version_num FROM careertrellis.alembic_version),
 'policy_rows',(SELECT count(*) FROM careertrellis_admission.policy),
 'maintenance',(SELECT maintenance FROM careertrellis_admission.policy),
 'signup_open',(SELECT signup_open FROM careertrellis_admission.policy),
 'search_queue',(SELECT count(*) FROM careertrellis.search_run_claim_queue),
 'active_searches',(SELECT count(*) FROM careertrellis.search_runs WHERE status IN ('preparing','queued','running','cancel_requested')),
 'erasure_queue',(SELECT count(*) FROM careertrellis.account_erasure_claim_queue),
 'clients',(SELECT count(*) FROM pg_stat_activity WHERE datname='careertrellis' AND pid<>pg_backend_pid() AND backend_type='client backend'),
 'prepared',(SELECT count(*) FROM pg_prepared_xacts WHERE database='careertrellis'),
 'database_bytes',pg_database_size(current_database()));
ROLLBACK;"""


def database_facts():
    try:
        role(DB_HOST)
        result = run_json(['/usr/bin/setpriv', '--reuid=postgres', '--regid=postgres',
             '--init-groups', '--reset-env', '--', '/usr/bin/psql', '-X', '-qAt', '-w',
             '-v', 'ON_ERROR_STOP=1', '-h', '/var/run/postgresql', '-p', '5432', '-d', 'careertrellis'],
             timeout=8, input_data=DATABASE_SQL.encode('utf-8'), code='provider_database_query')
        numbers = {'policy_rows', 'search_queue', 'active_searches', 'erasure_queue',
                   'clients', 'prepared', 'database_bytes'}
        need(set(result) == numbers | {'schema', 'maintenance', 'signup_open'}
             and isinstance(result['schema'], str)
             and re.fullmatch('[0-9]{8}_[0-9]{4}', result['schema']) is not None
             and all(type(result[key]) is int and result[key] >= 0 for key in numbers)
             and all(type(result[key]) is bool for key in ('maintenance', 'signup_open')),
             'provider_database_result')
        return result
    except Refused:
        raise
    except Exception:
        raise Refused('provider_database_unavailable') from None


def repository_idle():
    for unit in REPOSITORY_UNITS:
        proc = subprocess.run(['/usr/bin/systemctl', 'show', unit,
            '--property=ActiveState,MainPID,ControlPID,Job'], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=10, env=dict(BASE_ENV))
        need(proc.returncode == 0 and len(proc.stdout) <= 4096, 'provider_repository_units')
        state = dict(line.split('=', 1) for line in proc.stdout.decode('ascii').splitlines())
        need(set(state) == {'ActiveState', 'MainPID', 'ControlPID', 'Job'}, 'provider_repository_units')
        need(state['ActiveState'] in ('inactive', 'failed') and state['MainPID'] == '0'
             and state['ControlPID'] == '0' and state['Job'] == '', 'provider_repository_busy')


def check_lock(descriptor, expected):
    opened, current = os.fstat(descriptor), REPOSITORY_LOCK.lstat()
    for value in (opened, current):
        need(stat.S_ISREG(value.st_mode) and value.st_uid == value.st_gid == 0
             and stat.S_IMODE(value.st_mode) == 0o600 and value.st_nlink == 1
             and (value.st_dev, value.st_ino) == expected, 'provider_lock_identity')


def repository_lock_parents():
    """The one canonical lock may live in root's exact sticky lock directory.

    This exception never applies to provider credentials/configuration paths or
    caller-selected locations. The existing leaf identity checks remain strict.
    """
    need(REPOSITORY_LOCK == Path('/run/lock/careertrellis-pgbackrest-repository.lock'),
         'provider_path_metadata')
    exact = {Path('/run/lock'): 0o1777, Path('/run'): 0o755}
    for parent in REPOSITORY_LOCK.parents:
        meta = parent.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0,
             'provider_path_metadata')
        mode = stat.S_IMODE(meta.st_mode)
        need(mode == exact[parent] if parent in exact else not mode & 0o022,
             'provider_path_metadata')


@contextmanager
def repository_lock():
    repository_idle()
    repository_lock_parents()
    before = REPOSITORY_LOCK.lstat()
    expected = (before.st_dev, before.st_ino)
    descriptor = os.open(REPOSITORY_LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        check_lock(descriptor, expected)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('provider_repository_busy') from None
        check_lock(descriptor, expected)
        repository_idle()
        yield descriptor, expected
        check_lock(descriptor, expected)
    finally:
        os.close(descriptor)


def db_provider_facts():
    try:
        role(DB_HOST)
        verify_binary(RCLONE)
        with repository_lock() as (lock, expected):
            stored = protected(DB_CONFIG)
            config = configuration(stored.data)
            need(set(config.sections()) == {'r2', 'onedrive'} and config['r2'].get('type') == 's3'
                 and config['r2'].get('provider') == 'Cloudflare'
                 and re.fullmatch(r'https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com/?',
                                  config['r2'].get('endpoint', '')) is not None
                 and config['onedrive'].get('type') == 'onedrive', 'provider_config_scope')
            try:
                with sealed(stored.data) as descriptor:
                    check_lock(lock, expected)
                    source = rclone_json('size', 'r2:job-applier-staging-db-backups/job-applier/staging/postgresql',
                                         descriptor, lock=lock)
                    check_lock(lock, expected)
                    quota = rclone_json('about', 'onedrive:', descriptor, lock=lock)
                    check_lock(lock, expected)
                return aggregates(source, quota)
            finally:
                unchanged(DB_CONFIG, stored)
    except Refused:
        raise
    except Exception:
        raise Refused('provider_database_storage_unavailable') from None


def include_state():
    if not os.path.lexists(PG_INCLUDES):
        return None
    parents(PG_INCLUDES)
    before = PG_INCLUDES.lstat()
    need(stat.S_ISDIR(before.st_mode) and before.st_uid == 0
         and not before.st_mode & 0o022, 'provider_retention_ambiguous')
    need(next(PG_INCLUDES.iterdir(), None) is None, 'provider_retention_ambiguous')
    after = PG_INCLUDES.lstat()
    need((before.st_dev, before.st_ino, before.st_mtime_ns) ==
         (after.st_dev, after.st_ino, after.st_mtime_ns), 'provider_retention_ambiguous')
    return before.st_dev, before.st_ino, before.st_mtime_ns


def retention_facts():
    """Simple local configuration only, under the pinned reset-env backup wrapper.

    The official pgBackRest configuration reference documents modern default
    config precedence (legacy is considered only if modern is missing), and
    expire-auto enabled: https://pgbackrest.org/configuration.html (2026-10-26).
    Unknown overrides refuse rather than becoming an invented effective policy.
    """
    try:
        role(DB_HOST)
        need(PG_CONFIG == Path('/etc/pgbackrest/pgbackrest.conf')
             and PG_INCLUDES == Path('/etc/pgbackrest/conf.d'), 'provider_retention_ambiguous')
        need(not any(key.upper().startswith('PGBACKREST_') for key in os.environ),
             'provider_retention_ambiguous')
        account = pwd.getpwnam('postgres')
        options = {'gids': (0, account.pw_gid), 'modes': (0o600, 0o640)}
        stored = protected(PG_CONFIG, **options)
        # A verified modern primary is mandatory. Its presence excludes the
        # legacy fallback without reading, deleting or changing that file.
        included = include_state()
        config = configuration(stored.data)
        need(set(config.sections()) == {'global', 'careertrellis'}, 'provider_retention_ambiguous')
        need(set(config['careertrellis']) == {'pg1-path', 'pg1-port'}
             and config['careertrellis']['pg1-path'] == '/var/lib/postgresql/18/main'
             and config['careertrellis']['pg1-port'] == '5432', 'provider_retention_ambiguous')
        global_config = config['global']
        for key in global_config:
            indexed = re.match(r'repo([0-9]+)-', key)
            need('config' not in key and (indexed is None or indexed.group(1) == '1')
                 and re.match(r'(?:repo[0-9]+|pg[0-9]+)-host', key) is None,
                 'provider_retention_ambiguous')
        need(global_config.get('repo1-retention-full-type', 'count') == 'count'
             and global_config.get('repo1-retention-archive-type', 'full') == 'full',
             'provider_retention_ambiguous')
        result = {}
        for key, name in (('full', 'repo1-retention-full'), ('differential', 'repo1-retention-diff'),
                          ('archive', 'repo1-retention-archive')):
            value = global_config.get(name, '')
            need(re.fullmatch('[0-9]{1,5}', value) is not None and 1 <= int(value) <= 10000,
                 'provider_retention_unknown')
            result[key] = int(value)
        expiry = global_config.get('expire-auto', 'y')
        need(expiry in ('y', 'n'), 'provider_retention_unknown')
        result['expire_auto'] = expiry == 'y'
        unchanged(PG_CONFIG, stored, **options)
        need(include_state() == included, 'provider_retention_ambiguous')
        return result
    except Refused:
        raise
    except Exception:
        raise Refused('provider_retention_unavailable') from None
