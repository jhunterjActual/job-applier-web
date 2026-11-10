"""Synthetic provider facts tests; every host, file, process and network call mocked."""
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    from . import provider as p
    from .common import Refused
except ImportError:
    import provider as p
    from common import Refused


SECRET = 'SYNTHETIC_PRIVATE_VALUE_NOT_FOR_OUTPUT'
ACCOUNT = SimpleNamespace(pw_uid=42, pw_gid=43)
ENDPOINT = 'https://' + 'a' * 32 + '.r2.cloudflarestorage.com'
WEB_CONFIGURATION = ('[generated-object-onedrive]\ntype = onedrive\ntoken = ' + SECRET + '\n').encode()
DB_CONFIGURATION = ('[r2]\ntype = s3\nprovider = Cloudflare\nendpoint = ' + ENDPOINT
                    + '\nsecret_access_key = ' + SECRET
                    + '\n[onedrive]\ntype = onedrive\ntoken = ' + SECRET + '\n').encode()
WEB_ENVIRONMENT = ('CAREERTRELLIS_GENERATED_OBJECT_SOURCE_ENDPOINT=' + ENDPOINT + '\n'
    'CAREERTRELLIS_GENERATED_OBJECT_SOURCE_BUCKET=synthetic-objects\n'
    'CAREERTRELLIS_GENERATED_OBJECT_SOURCE_ACCESS_KEY_ID=' + 'k' * 20 + '\n'
    'CAREERTRELLIS_GENERATED_OBJECT_SOURCE_SECRET_ACCESS_KEY=' + 's' * 32 + '\n'
    'CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_REMOTE=generated-object-onedrive\n'
    'CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_ROOT=careertrellis/generated-objects\n').encode()
PG_CONFIGURATION = (b'[global]\nrepo1-retention-full=4\nrepo1-retention-diff=14\n'
    b'repo1-retention-archive=4\nrepo1-cipher-pass=synthetic-private-passphrase\n'
    b'[careertrellis]\npg1-path=/var/lib/postgresql/18/main\npg1-port=5432\n')


def snapshot(raw, identity=(1, 2)):
    return p.Snapshot(raw, identity)


def native_fcntl():
    values = dict(F_SEAL_WRITE=1, F_SEAL_SHRINK=2, F_SEAL_GROW=4, F_SEAL_SEAL=8,
                  F_ADD_SEALS=100, F_GET_SEALS=101, LOCK_EX=2, LOCK_NB=4)
    fake = SimpleNamespace(**values)
    fake.flock = Mock()
    fake.fcntl = Mock(side_effect=lambda fd, operation, *args: 15 if operation == 101 else 0)
    return fake


@contextmanager
def fake_sealed(raw, uid=0, gid=0):
    yield 51


@contextmanager
def fake_lock():
    yield 91, (1, 2)


class PublicFactsTests(unittest.TestCase):
    @contextmanager
    def environment(self, *, web_config=WEB_CONFIGURATION, web_environment=WEB_ENVIRONMENT,
                    db_config=DB_CONFIGURATION, pg_config=PG_CONFIGURATION):
        snapshots = {p.WEB_ENV: snapshot(web_environment), p.WEB_CONFIG: snapshot(web_config),
                     p.DB_CONFIG: snapshot(db_config), p.PG_CONFIG: snapshot(pg_config)}
        with ExitStack() as stack:
            role = stack.enter_context(patch.object(p, 'role'))
            binary = stack.enter_context(patch.object(p, 'verify_binary'))
            stack.enter_context(patch.object(p, 'pwd', SimpleNamespace(getpwnam=Mock(return_value=ACCOUNT))))
            protected = stack.enter_context(patch.object(p, 'protected', side_effect=lambda path, **kw: snapshots[path]))
            unchanged = stack.enter_context(patch.object(p, 'unchanged'))
            seal = stack.enter_context(patch.object(p, 'sealed', side_effect=fake_sealed))
            lock = stack.enter_context(patch.object(p, 'repository_lock', side_effect=fake_lock))
            check = stack.enter_context(patch.object(p, 'check_lock'))
            rclone = stack.enter_context(patch.object(p, 'rclone_json', side_effect=[
                {'count': 28, 'bytes': 793722}, {'free': 214748364}]))
            stack.enter_context(patch.object(p, 'include_state', side_effect=[None, None]))
            stack.enter_context(patch.dict(p.os.environ, {}, clear=True))
            yield SimpleNamespace(role=role, binary=binary, protected=protected,
                unchanged=unchanged, seal=seal, lock=lock, check=check, rclone=rclone)

    def test_web_facts_are_aggregate_only_and_check_both_original_files(self):
        with self.environment() as fake:
            result = p.web_facts(egress_reader=lambda: '8.8.8.8/32')
        self.assertEqual({'source_count': 28, 'source_bytes': 793722,
            'onedrive_free_bytes': 214748364, 'credential_files_unchanged': True,
            'egress_ipv4_cidr': '8.8.8.8/32'}, result)
        self.assertNotIn(SECRET, json.dumps(result))
        fake.role.assert_called_once_with(p.WEB_HOST)
        self.assertEqual([p.WEB_CONFIG, p.WEB_ENV], [c.args[0] for c in fake.unchanged.call_args_list])
        combined = fake.seal.call_args.args[0]
        self.assertTrue(combined.startswith(WEB_CONFIGURATION))
        self.assertIn(b'[generatedobjectsource]', combined)
        self.assertIn(b'secret_access_key = ' + b's' * 32, combined)
        self.assertEqual((42, 43), fake.seal.call_args.args[1:])
        self.assertEqual([('size', 'generatedobjectsource:synthetic-objects'),
                          ('about', 'generated-object-onedrive:')],
                         [c.args[:2] for c in fake.rclone.call_args_list])

    def test_web_failure_still_verifies_unchanged_credentials_and_hides_raw_error(self):
        out, err = io.StringIO(), io.StringIO()
        with self.environment() as fake:
            fake.rclone.side_effect = RuntimeError(SECRET)
            with redirect_stdout(out), redirect_stderr(err), self.assertRaises(Refused) as caught:
                p.web_facts(egress_reader=lambda: '8.8.8.8/32')
            self.assertEqual(2, fake.unchanged.call_count)
        self.assertEqual('provider_web_unavailable', str(caught.exception))
        self.assertEqual('', out.getvalue() + err.getvalue())
        self.assertNotIn(SECRET, str(caught.exception))

    def test_web_rejects_duplicate_or_injected_environment_before_provider_access(self):
        for raw in (WEB_ENVIRONMENT + b'UNREVIEWED=synthetic\n',
                    WEB_ENVIRONMENT + b'CAREERTRELLIS_GENERATED_OBJECT_SOURCE_BUCKET=other\n',
                    WEB_ENVIRONMENT.replace(b'synthetic-objects', b'--unsafe-target'),
                    WEB_ENVIRONMENT.replace(b'careertrellis/generated-objects', b'other/root')):
            with self.subTest(raw_size=len(raw)):
                with self.environment(web_environment=raw) as fake, self.assertRaises(Refused):
                    p.web_facts(egress_reader=lambda: '8.8.8.8/32')
                fake.rclone.assert_not_called()

    def test_web_rejects_unreviewed_remote_sections(self):
        with self.environment(web_config=WEB_CONFIGURATION + b'[other]\ntype=local\n') as fake:
            with self.assertRaises(Refused):
                p.web_facts(egress_reader=lambda: '8.8.8.8/32')
            fake.rclone.assert_not_called()

    def test_web_egress_must_be_public_ipv4_single_host(self):
        for value in ('10.0.0.1/32', '8.8.8.0/24', '::1/128', SECRET):
            with self.subTest(value=value):
                with self.environment(), self.assertRaises(Refused):
                    p.web_facts(egress_reader=lambda: value)

    def test_invalid_provider_aggregate_does_not_become_a_capacity_pass(self):
        for source, quota in (({'count': True, 'bytes': 1}, {'free': 2}),
                              ({'count': 1, 'bytes': -1}, {'free': 2}),
                              ({'count': 1, 'bytes': 1}, {'free': '2'})):
            with self.subTest(source=source, quota=quota):
                with self.environment() as fake:
                    fake.rclone.side_effect = [source, quota]
                    with self.assertRaises(Refused) as caught:
                        p.web_facts(egress_reader=lambda: '8.8.8.8/32')
                    self.assertEqual('provider_aggregate_invalid', caught.exception.code)
                    self.assertEqual(2, fake.unchanged.call_count)

    def test_db_storage_reads_use_fixed_targets_and_inherited_existing_lock(self):
        with self.environment() as fake:
            result = p.db_provider_facts()
        self.assertEqual({'source_count': 28, 'source_bytes': 793722,
            'onedrive_free_bytes': 214748364, 'credential_files_unchanged': True}, result)
        fake.role.assert_called_once_with(p.DB_HOST)
        self.assertEqual([('size', 'r2:job-applier-staging-db-backups/job-applier/staging/postgresql'),
                          ('about', 'onedrive:')], [c.args[:2] for c in fake.rclone.call_args_list])
        self.assertTrue(all(c.kwargs['lock'] == 91 for c in fake.rclone.call_args_list))
        self.assertEqual(3, fake.check.call_count)
        fake.unchanged.assert_called_once()

    def test_db_storage_busy_or_unknown_configuration_never_queries_provider(self):
        with self.environment() as fake:
            fake.lock.side_effect = Refused('provider_repository_busy')
            with self.assertRaises(Refused):
                p.db_provider_facts()
            fake.protected.assert_not_called()
            fake.rclone.assert_not_called()
        for raw in (DB_CONFIGURATION + b'[other]\ntype=local\n',
                    DB_CONFIGURATION.replace(b'provider = Cloudflare', b'provider = Other')):
            with self.environment(db_config=raw) as fake, self.assertRaises(Refused):
                p.db_provider_facts()
            fake.rclone.assert_not_called()

    def test_db_storage_checks_original_configuration_on_read_failure(self):
        with self.environment() as fake:
            fake.rclone.side_effect = RuntimeError(SECRET)
            with self.assertRaises(Refused) as caught:
                p.db_provider_facts()
            fake.unchanged.assert_called_once()
        self.assertEqual('provider_database_storage_unavailable', caught.exception.code)

    def test_retention_default_and_explicit_values_are_simple_typed_facts(self):
        for suffix, expected in ((b'', True), (b'expire-auto=n\n', False)):
            raw = PG_CONFIGURATION.replace(b'[careertrellis]', suffix + b'[careertrellis]')
            with self.subTest(expiry=expected), self.environment(pg_config=raw) as fake:
                self.assertEqual({'full': 4, 'differential': 14, 'archive': 4,
                                  'expire_auto': expected}, p.retention_facts())
                fake.unchanged.assert_called_once()

    def test_legacy_fallback_is_ignored_only_with_verified_unchanged_modern_primary(self):
        include = p.include_state
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0,
                                    st_dev=1, st_ino=2, st_mtime_ns=3)
        with self.environment() as fake, patch.object(p, 'include_state', side_effect=include), \
                patch.object(p, 'parents'), patch.object(p.Path, 'lstat', return_value=directory), \
                patch.object(p.Path, 'iterdir', side_effect=lambda: iter(())), \
                patch.object(p.os.path, 'lexists', side_effect=lambda path: path in (p.PG_LEGACY, p.PG_INCLUDES)) as exists:
            self.assertEqual({'full': 4, 'differential': 14, 'archive': 4, 'expire_auto': True}, p.retention_facts())
            fake.protected.assert_called_once_with(p.PG_CONFIG, gids=(0, ACCOUNT.pw_gid), modes=(0o600, 0o640))
            fake.unchanged.assert_called_once()
            self.assertEqual(fake.unchanged.call_args.args[0], p.PG_CONFIG)
            self.assertEqual(fake.unchanged.call_args.args[1].data, PG_CONFIGURATION)
            self.assertEqual(fake.unchanged.call_args.kwargs,
                             {'gids': (0, ACCOUNT.pw_gid), 'modes': (0o600, 0o640)})
            self.assertNotIn(p.PG_LEGACY, [call.args[0] for call in exists.call_args_list])

    def test_missing_unsafe_or_changed_modern_primary_never_uses_legacy(self):
        for error in (FileNotFoundError(), Refused('provider_file_metadata')):
            with self.environment() as fake, patch.object(p.os.path, 'lexists', return_value=True), patch.object(p, 'include_state') as includes:
                fake.protected.side_effect = error
                with self.assertRaises(Refused): p.retention_facts()
                fake.unchanged.assert_not_called()
                includes.assert_not_called()
        with self.environment() as fake, patch.object(p.os.path, 'lexists', return_value=True):
            fake.unchanged.side_effect = Refused('provider_credentials_changed')
            with self.assertRaises(Refused): p.retention_facts()
        with self.environment(), patch.object(p, 'PG_CONFIG', Path('/etc/pgbackrest.conf')), self.assertRaises(Refused):
            p.retention_facts()

    def test_retention_refuses_includes_stanza_command_and_other_repository_overrides(self):
        cases = (
            PG_CONFIGURATION + b'repo1-retention-full=9\n',
            PG_CONFIGURATION + b'[global:backup]\nexpire-auto=n\n',
            PG_CONFIGURATION.replace(b'[careertrellis]', b'config-include-path=/unreviewed\n[careertrellis]'),
            PG_CONFIGURATION.replace(b'[careertrellis]', b'repo10-retention-full=9\n[careertrellis]'),
            PG_CONFIGURATION.replace(b'pg1-port=5432', b'pg1-port=55432'),
            PG_CONFIGURATION.replace(b'[global]', b'[DEFAULT]\nexpire-auto=n\n[global]'),
        )
        for raw in cases:
            with self.subTest(size=len(raw)), self.environment(pg_config=raw), self.assertRaises(Refused):
                p.retention_facts()
        with self.environment(), patch.object(p, 'include_state', side_effect=Refused('provider_retention_ambiguous')):
            with self.assertRaises(Refused):
                p.retention_facts()

    def test_retention_environment_override_is_refused_without_exposing_value(self):
        with self.environment() as fake, patch.dict(p.os.environ, {'PGBACKREST_CONFIG': SECRET}):
            with self.assertRaises(Refused) as caught:
                p.retention_facts()
            fake.protected.assert_not_called()
        self.assertNotIn(SECRET, str(caught.exception))

    def test_retention_unknown_numbers_and_booleans_are_not_guessed(self):
        for raw in (PG_CONFIGURATION.replace(b'full=4', b'full=0'),
                    PG_CONFIGURATION.replace(b'full=4', b'full=unknown'),
                    PG_CONFIGURATION.replace(b'[careertrellis]', b'expire-auto=true\n[careertrellis]')):
            with self.environment(pg_config=raw), self.assertRaises(Refused):
                p.retention_facts()


class CommandAndIdentityTests(unittest.TestCase):
    def test_wrong_host_and_non_root_are_refused(self):
        for uid, host in ((1000, p.WEB_HOST), (0, 'synthetic-other-host')):
            with patch.object(p.os, 'geteuid', return_value=uid, create=True), \
                    patch.object(p.os, 'uname', return_value=SimpleNamespace(nodename=host), create=True):
                with self.assertRaises(Refused) as caught:
                    p.role(p.WEB_HOST)
                self.assertEqual('provider_host_refused', caught.exception.code)

    def test_rclone_uid_drop_uses_reset_env_and_both_sealed_and_lock_descriptors(self):
        with patch.object(p, 'run_json', return_value={'free': 1}) as run:
            p.rclone_json('about', 'onedrive:', 51, lock=91, account=ACCOUNT)
        args = run.call_args.args[0]
        self.assertEqual(['/usr/bin/setpriv', '--reuid=42', '--regid=43', '--init-groups',
                          '--reset-env', '--', str(p.RCLONE)], args[:7])
        self.assertEqual((51, 91), run.call_args.kwargs['descriptors'])
        self.assertNotIn(SECRET, ' '.join(args))
        self.assertIn('/proc/self/fd/51', args)

    def test_json_runner_discards_stderr_restricts_environment_and_bounds_output(self):
        with patch.object(p.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'{"count":1}')) as run:
            self.assertEqual({'count': 1}, p.run_json(['/synthetic/read'], descriptors=(51, 91)))
        self.assertIs(run.call_args.kwargs['stderr'], p.subprocess.DEVNULL)
        self.assertEqual(p.BASE_ENV, run.call_args.kwargs['env'])
        self.assertEqual((51, 91), run.call_args.kwargs['pass_fds'])
        for result in (SimpleNamespace(returncode=1, stdout=SECRET.encode()),
                       SimpleNamespace(returncode=0, stdout=b' ' * 16385)):
            with patch.object(p.subprocess, 'run', return_value=result), self.assertRaises(Refused) as caught:
                p.run_json(['/synthetic/read'])
            self.assertNotIn(SECRET, str(caught.exception))

    def test_database_facts_use_readonly_bounded_local_psql_with_no_password_prompt(self):
        facts = {'schema': '20261005_0023', 'policy_rows': 1, 'maintenance': False,
                 'signup_open': True, 'search_queue': 0, 'active_searches': 0,
                 'erasure_queue': 0, 'clients': 7, 'prepared': 0, 'database_bytes': 10000}
        with patch.object(p, 'role'), patch.object(p.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(facts).encode())) as run:
            self.assertEqual(facts, p.database_facts())
        args, options = run.call_args.args[0], run.call_args.kwargs
        self.assertIn('--reset-env', args)
        self.assertIn('-w', args)
        self.assertIn('/var/run/postgresql', args)
        self.assertEqual(8, options['timeout'])
        self.assertIs(options['stderr'], p.subprocess.DEVNULL)
        for required in (b'BEGIN READ ONLY', b'ROLLBACK;', b"statement_timeout='3s'",
                         b"lock_timeout='250ms'", b"transaction_timeout='5s'", b"log_statement='none'"):
            self.assertIn(required, options['input'])

    def test_database_result_types_and_private_errors_fail_closed(self):
        with patch.object(p, 'role'), patch.object(p, 'run_json', return_value={'private': SECRET}):
            with self.assertRaises(Refused) as caught:
                p.database_facts()
            self.assertEqual('provider_database_result', caught.exception.code)
        output, error = io.StringIO(), io.StringIO()
        with patch.object(p, 'role'), patch.object(p, 'run_json', side_effect=RuntimeError(SECRET)), \
                redirect_stdout(output), redirect_stderr(error), self.assertRaises(Refused) as caught:
            p.database_facts()
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual('', output.getvalue() + error.getvalue())

    def test_repository_unit_activity_refuses_and_discards_private_stderr(self):
        idle = b'ActiveState=inactive\nMainPID=0\nControlPID=0\nJob=\n'
        with patch.object(p.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=idle)) as run:
            p.repository_idle()
        self.assertEqual(len(p.REPOSITORY_UNITS), run.call_count)
        self.assertTrue(all(c.kwargs['stderr'] is p.subprocess.DEVNULL for c in run.call_args_list))
        busy = idle.replace(b'inactive', b'activating').replace(b'MainPID=0', b'MainPID=52')
        with patch.object(p.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=busy)):
            with self.assertRaises(Refused) as caught:
                p.repository_idle()
            self.assertEqual('provider_repository_busy', caught.exception.code)


class EgressTests(unittest.TestCase):
    def test_only_fixed_verified_bounded_https_response_is_accepted(self):
        response = Mock(status=200)
        response.geturl.return_value = p.EGRESS_URL
        response.read.return_value = b'8.8.8.8\n'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(p.urllib.request, 'build_opener', return_value=opener):
            self.assertEqual('8.8.8.8/32', p.egress_ipv4_cidr())
        self.assertEqual(p.EGRESS_URL, opener.open.call_args.args[0].full_url)
        self.assertEqual(15, opener.open.call_args.kwargs['timeout'])
        response.read.assert_called_once_with(65)

    def test_egress_redirect_private_ipv6_and_oversized_data_refuse(self):
        for raw in (b'10.0.0.1', b'::1', b'x' * 65):
            response = Mock(status=200)
            response.geturl.return_value = p.EGRESS_URL
            response.read.return_value = raw
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            opener = Mock(); opener.open.return_value = response
            with patch.object(p.urllib.request, 'build_opener', return_value=opener), self.assertRaises(Refused):
                p.egress_ipv4_cidr()
        with self.assertRaises(Refused) as caught:
            p.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://synthetic.invalid/')
        self.assertEqual('provider_egress_redirect', caught.exception.code)


class NativeDescriptorTests(unittest.TestCase):
    @contextmanager
    def file_environment(self, *, leaf_changes=None, parent_changes=None, changed_after_open=False):
        target = Path('/synthetic/secrets.conf')
        leaf_values = dict(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_gid=0,
            st_nlink=1, st_dev=1, st_ino=2, st_size=len(SECRET.encode()),
            st_mtime_ns=100, st_ctime_ns=100)
        leaf = SimpleNamespace(**dict(leaf_values, **(leaf_changes or {})))
        directory = SimpleNamespace(**dict(dict(st_mode=stat.S_IFDIR | 0o755, st_uid=0),
                                           **(parent_changes or {})))
        after = SimpleNamespace(**dict(vars(leaf), st_ino=3)) if changed_after_open else leaf
        with ExitStack() as stack:
            stack.enter_context(patch.object(p.Path, 'lstat', lambda path: leaf if path == target else directory))
            stack.enter_context(patch.object(p.os, 'O_NOFOLLOW', 0, create=True))
            stack.enter_context(patch.object(p.os, 'O_NONBLOCK', 0, create=True))
            opened = stack.enter_context(patch.object(p.os, 'open', return_value=71))
            stack.enter_context(patch.object(p.os, 'fstat', side_effect=[after, after]))
            read = stack.enter_context(patch.object(p.os, 'read', return_value=SECRET.encode()))
            close = stack.enter_context(patch.object(p.os, 'close'))
            yield SimpleNamespace(target=target, opened=opened, read=read, close=close)

    def test_protected_file_read_checks_metadata_and_closes_descriptor(self):
        with self.file_environment() as fake:
            value = p.protected(fake.target)
            self.assertEqual(SECRET.encode(), value.data)
            self.assertEqual(2, value.identity[1])
            fake.close.assert_called_once_with(71)

    def test_untrusted_parent_is_refused_before_opening(self):
        for changes in ({'st_uid': 1000}, {'st_mode': stat.S_IFDIR | 0o777},
                        {'st_mode': stat.S_IFLNK | 0o755}):
            with self.subTest(changes=changes), self.file_environment(parent_changes=changes) as fake:
                with self.assertRaises(Refused) as caught:
                    p.protected(fake.target)
                self.assertEqual('provider_path_metadata', caught.exception.code)
                fake.opened.assert_not_called()
                fake.close.assert_not_called()

    def test_protected_leaf_owner_group_type_mode_links_and_size_are_strict(self):
        changes = ({'st_uid': 1000}, {'st_gid': 1000}, {'st_mode': stat.S_IFREG | 0o644},
                   {'st_mode': stat.S_IFLNK | 0o600}, {'st_nlink': 2},
                   {'st_size': 0}, {'st_size': 65537})
        for value in changes:
            with self.subTest(changes=value), self.file_environment(leaf_changes=value) as fake:
                with self.assertRaises(Refused) as caught:
                    p.protected(fake.target)
                self.assertEqual('provider_file_metadata', caught.exception.code)
                fake.read.assert_not_called()
                fake.close.assert_called_once_with(71)

    def test_replaced_inode_or_short_read_cannot_return_a_snapshot(self):
        with self.file_environment(changed_after_open=True) as fake:
            with self.assertRaises(Refused) as caught:
                p.protected(fake.target)
            self.assertEqual('provider_file_replaced', caught.exception.code)
            fake.read.assert_not_called()
            fake.close.assert_called_once_with(71)
        with self.file_environment() as fake:
            fake.read.return_value = b'short'
            with self.assertRaises(Refused) as caught:
                p.protected(fake.target)
            self.assertEqual('provider_file_changed', caught.exception.code)
            fake.close.assert_called_once_with(71)

    def test_sealed_config_is_readonly_sealed_and_closed(self):
        native = native_fcntl()
        with ExitStack() as stack:
            stack.enter_context(patch.object(p, 'fcntl', native))
            created = stack.enter_context(patch.object(p.os, 'memfd_create', return_value=51, create=True))
            stack.enter_context(patch.object(p.os, 'MFD_ALLOW_SEALING', 1, create=True))
            stack.enter_context(patch.object(p.os, 'MFD_CLOEXEC', 2, create=True))
            stack.enter_context(patch.object(p.os, 'write', side_effect=lambda fd, raw: len(raw)))
            stack.enter_context(patch.object(p.os, 'lseek'))
            chmod = stack.enter_context(patch.object(p.os, 'fchmod', create=True))
            chown = stack.enter_context(patch.object(p.os, 'fchown', create=True))
            close = stack.enter_context(patch.object(p.os, 'close'))
            with p.sealed(SECRET.encode(), 42, 43) as fd:
                self.assertEqual(51, fd)
            created.assert_called_once_with('careertrellis-provider-readonly', 3)
            chmod.assert_called_once_with(51, 0o400)
            chown.assert_called_once_with(51, 42, 43)
            close.assert_called_once_with(51)
            self.assertEqual([(51, 100, 15), (51, 101)], [c.args for c in native.fcntl.call_args_list])

    def test_changed_secret_bytes_or_inode_refuse_without_returning_fingerprint(self):
        original = snapshot(SECRET.encode())
        self.assertNotIn(SECRET, repr(original))
        for changed in (snapshot(b'other'), snapshot(SECRET.encode(), (1, 3))):
            with patch.object(p, 'protected', return_value=changed), self.assertRaises(Refused) as caught:
                p.unchanged(p.DB_CONFIG, original)
            self.assertEqual('provider_credentials_changed', caught.exception.code)

    @contextmanager
    def lock_environment(self):
        meta = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_gid=0,
                               st_nlink=1, st_dev=1, st_ino=2)
        native = native_fcntl()
        with ExitStack() as stack:
            idle = stack.enter_context(patch.object(p, 'repository_idle'))
            stack.enter_context(patch.object(p, 'repository_lock_parents'))
            stack.enter_context(patch.object(p.Path, 'lstat', return_value=meta))
            stack.enter_context(patch.object(p.os, 'O_NOFOLLOW', 0, create=True))
            stack.enter_context(patch.object(p.os, 'O_NONBLOCK', 0, create=True))
            stack.enter_context(patch.object(p.os, 'open', return_value=91))
            fstat = stack.enter_context(patch.object(p.os, 'fstat', return_value=meta))
            close = stack.enter_context(patch.object(p.os, 'close'))
            stack.enter_context(patch.object(p, 'fcntl', native))
            yield SimpleNamespace(meta=meta, native=native, fstat=fstat, idle=idle, close=close)

    def test_repository_lock_is_nonblocking_rechecked_and_released(self):
        with self.lock_environment() as fake:
            with p.repository_lock() as (fd, key):
                self.assertEqual((91, (1, 2)), (fd, key))
            self.assertEqual(2, fake.idle.call_count)
            fake.native.flock.assert_called_once_with(91, 6)
            fake.close.assert_called_once_with(91)

    def test_canonical_repository_lock_accepts_exact_root_sticky_directory_only(self):
        directories = {Path('/run/lock'): 0o1777, Path('/run'): 0o755, Path('/'): 0o755}
        def metadata(path):
            return SimpleNamespace(st_mode=stat.S_IFDIR | directories[path], st_uid=0, st_gid=0)
        with patch.object(p.Path, 'lstat', side_effect=metadata, autospec=True):
            p.repository_lock_parents()
            # Generic credential/configuration parent validation still refuses
            # this writable directory; the exception is lock-specific only.
            with self.assertRaises(Refused): p.parents(p.REPOSITORY_LOCK)
        for path, mode in [(Path('/run/lock'), 0o777), (Path('/run/lock'), 0o755),
                           (Path('/run'), 0o1777), (Path('/'), 0o777)]:
            changed = dict(directories, **{})
            changed[path] = mode
            def bad_metadata(selected):
                return SimpleNamespace(st_mode=stat.S_IFDIR | changed[selected], st_uid=0, st_gid=0)
            with self.subTest(path=path, mode=mode), patch.object(p.Path, 'lstat', side_effect=bad_metadata, autospec=True), self.assertRaises(Refused):
                p.repository_lock_parents()
        for field in ('st_uid', 'st_gid'):
            def bad_owner(path):
                meta = metadata(path)
                if path == Path('/run/lock'): setattr(meta, field, 1000)
                return meta
            with self.subTest(field=field), patch.object(p.Path, 'lstat', side_effect=bad_owner, autospec=True), self.assertRaises(Refused):
                p.repository_lock_parents()
        with patch.object(p, 'REPOSITORY_LOCK', Path('/tmp/careertrellis-pgbackrest-repository.lock')), self.assertRaises(Refused):
            p.repository_lock_parents()

    def test_busy_replaced_or_unsafe_lock_never_yields(self):
        with self.lock_environment() as fake:
            fake.native.flock.side_effect = BlockingIOError(SECRET)
            with self.assertRaises(Refused) as caught:
                with p.repository_lock():
                    self.fail('Busy lock yielded')
            self.assertEqual('provider_repository_busy', caught.exception.code)
            fake.close.assert_called_once_with(91)
        with self.lock_environment() as fake:
            fake.fstat.return_value = SimpleNamespace(**dict(vars(fake.meta), st_dev=2))
            with self.assertRaises(Refused) as caught:
                with p.repository_lock():
                    self.fail('Replaced lock yielded')
            self.assertEqual('provider_lock_identity', caught.exception.code)
            fake.native.flock.assert_not_called()
            fake.close.assert_called_once_with(91)


class IncludeConfigurationTests(unittest.TestCase):
    @contextmanager
    def environment(self, *, legacy=False, entries=(), directory_changes=None):
        meta = SimpleNamespace(**dict(dict(st_mode=stat.S_IFDIR | 0o755, st_uid=0,
            st_dev=1, st_ino=2, st_mtime_ns=3), **(directory_changes or {})))
        with ExitStack() as stack:
            stack.enter_context(patch.object(p.os.path, 'lexists',
                side_effect=lambda path: legacy if path == p.PG_LEGACY else path == p.PG_INCLUDES))
            stack.enter_context(patch.object(p, 'parents'))
            stack.enter_context(patch.object(p.Path, 'lstat', return_value=meta))
            stack.enter_context(patch.object(p.Path, 'iterdir', return_value=iter(entries)))
            yield

    def test_only_empty_protected_include_directory_is_accepted(self):
        with self.environment():
            self.assertEqual((1, 2, 3), p.include_state())

    def test_legacy_fallback_does_not_make_an_empty_modern_include_directory_ambiguous(self):
        with self.environment(legacy=True):
            self.assertEqual((1, 2, 3), p.include_state())

    def test_nonempty_includes_and_untrusted_directories_are_refused(self):
        cases = ({'entries': [Path('/synthetic/unreviewed.conf')]},
                 {'directory_changes': {'st_uid': 1000}},
                 {'directory_changes': {'st_mode': stat.S_IFLNK | 0o755}},
                 {'directory_changes': {'st_mode': stat.S_IFDIR | 0o777}})
        for options in cases:
            with self.subTest(options=options), self.environment(**options), self.assertRaises(Refused):
                p.include_state()


if __name__ == '__main__':
    unittest.main()
