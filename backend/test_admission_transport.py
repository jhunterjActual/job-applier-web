"""Secret-free verifier tests; optional dedicated, auto-removed PostgreSQL18 rehearsal."""
import io
import json
import os
from pathlib import Path
import subprocess
import time
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

import admission_operations as ops


class TransportTests(unittest.TestCase):
    def test_network_failure_is_not_denial_and_engine_is_disposed(self):
        from sqlalchemy.exc import OperationalError
        engine = Mock()
        engine.connect.side_effect = OperationalError(None, None, RuntimeError('synthetic timeout'))
        url = make_url('postgresql+psycopg://' + ops.BROKER + ':' + 'A' * 64 + '@10.70.0.2/careertrellis')
        with patch('sqlalchemy.create_engine', return_value=engine):
            with self.assertRaises(ops.OperationError):
                ops.broker_transport_matrix(url, expected_source='10.70.0.1')
        engine.dispose.assert_called_once()

    def test_composed_web_success_and_mismatch_fail_closed(self):
        base = 'postgresql+psycopg://' + ops.BROKER + ':' + 'A' * 64 + '@10.70.0.2/careertrellis'
        files = {ops.AUTH_FILE: ('CAREERTRELLIS_AUTH_V2_ENABLED=0\nCAREERTRELLIS_ADMISSION_DATABASE_URL=' + base).encode(),
            ops.ROOT / 'postgres-runtime.env': ('CAREERTRELLIS_DATABASE_URL=' + base.replace(ops.BROKER, 'careertrellis_runtime')).encode(),
            ops.IDENTITY: b'synthetic-identity', ops.BUNDLE: b'synthetic-ciphertext'}
        material = {'version': 1, 'password': 'A' * 64, 'database_host': '10.70.0.2', 'transport': 'wireguard'}
        route = [{'dev': 'wg0', 'prefsrc': '10.70.0.1'}]
        for bad in (None, 'age', 'route', 'active'):
            def command(args, **kwargs):
                if args[0] == '/usr/bin/age':
                    self.assertEqual(b'synthetic-ciphertext', kwargs['data'])
                    return ops.encoded({**material, 'password': 'B' * 64} if bad == 'age' else material)
                return ops.encoded([{'dev': 'eth0', 'prefsrc': '10.70.0.1'}] if bad == 'route' else route)
            with patch.object(ops, 'stopped_services', side_effect=ops.OperationError(ops.ERROR) if bad == 'active' else None), \
                    patch.object(ops, 'check_worker_delivery'), patch.object(ops, 'read_protected', side_effect=lambda p: files[p]), \
                    patch.object(ops, 'command', side_effect=command), patch.object(ops, 'broker_transport_matrix') as matrix:
                if bad:
                    with self.assertRaises(ops.OperationError):
                        ops.verify_web_transport()
                    matrix.assert_not_called()
                else:
                    self.assertIn('wrong_password_rejected', ops.verify_web_transport())
                    matrix.assert_called_once()

    def test_cli_dispatch_binding_and_fixed_error(self):
        source = '/opt/careertrellis/staging/releases/' + 'b' * 40 + '/backend/admission_operations.py'
        for verb, side in (('web-verify-transport', 'web'), ('db-verify-transport', 'database')):
            for valid in (True, False):
                with tempfile.TemporaryDirectory() as temporary, \
                        patch.object(ops, '__file__', source if valid else '/untrusted/backend/admission_operations.py'), \
                        patch.object(ops, 'Path', side_effect=lambda p: Path(p).absolute()), \
                        patch.object(ops.os, 'geteuid', return_value=0, create=True), \
                        patch.object(ops, 'protected_parents'), patch.object(ops, 'read_protected', return_value=b''), \
                        patch.object(ops, 'STATE', Path(temporary)), patch.object(ops, 'host_locks'), \
                        patch.object(ops.stat, 'S_IMODE', return_value=0o700), \
                        patch.object(ops, 'LocalPostgres', return_value=Mock(query=Mock(return_value=True))), \
                        patch.object(ops, 'transport_receipt') as receipt, \
                        patch.object(ops.sys, 'path', list(ops.sys.path)), redirect_stdout(io.StringIO()) as output, redirect_stderr(output):
                    result = ops.main([verb, str(uuid4())])
                    self.assertEqual(0 if valid else 1, result)
                    self.assertEqual(valid, receipt.called)
                    if valid:
                        self.assertEqual(side, receipt.call_args.args[0])
                    else:
                        self.assertEqual(ops.ERROR + '\n', output.getvalue())

    def test_exact_rejections_only(self):
        prefix = 'connection to server at "127.0.0.1", port 5432 failed: FATAL:  '
        password = 'password authentication failed for user "' + ops.BROKER + '"'
        hba = 'pg_hba.conf rejects connection for host "127.0.0.1", user "' + ops.BROKER + '", database "careertrellis", no encryption'
        ops.transport_rejection(prefix + password, 'password')
        ops.transport_rejection(('psql: error: ' + prefix + hba + '\n').encode(), 'hba')
        ops.transport_rejection(prefix + hba + '\n' + prefix + hba, 'hba')
        for raw in ('', prefix + 'no password supplied', prefix + 'database "postgres" does not exist',
                'connection refused', 'timeout', prefix + password + '\nconnection refused',
                prefix + hba.replace(ops.BROKER, 'other'), prefix + hba.replace('careertrellis', 'other'),
                prefix + password + '\nDETAIL: synthetic-secret', 'x' * 8193):
            with self.subTest(raw=raw[:40]), self.assertRaises(ops.OperationError):
                ops.transport_rejection(raw, 'hba')

    def test_receipt_is_bound_sanitized_and_no_clobber(self):
        records = {}
        def write(path, raw, **kwargs):
            if path in records and not kwargs:
                raise ops.OperationError(ops.ERROR)
            records[path] = raw
        source = '/opt/careertrellis/staging/releases/' + 'a' * 40 + '/backend/admission_operations.py'
        operation = str(uuid4())
        with patch.object(ops, '__file__', source), patch.object(ops, 'write_protected', side_effect=write), \
                patch.object(ops, 'state_write', side_effect=lambda p, v: records.update({p: ops.encoded(v)})):
            ops.transport_receipt('web', operation, lambda: ['synthetic_pass'])
            receipt = json.loads(next(iter(records.values())))
            self.assertEqual('ok', receipt['result'])
            self.assertEqual('a' * 40, receipt['source_commit'])
            with self.assertRaises(ops.OperationError):
                ops.transport_receipt('web', operation, Mock())
            with self.assertRaisesRegex(RuntimeError, 'synthetic-secret'):
                ops.transport_receipt('database', operation, Mock(side_effect=RuntimeError('synthetic-secret')))
        self.assertNotIn('synthetic-secret', str(records))
        self.assertEqual('incomplete', json.loads(list(records.values())[-1])['result'])

    def test_unsafe_web_target_never_connects_or_decrypts(self):
        base = 'postgresql+psycopg://' + ops.BROKER + ':' + 'A' * 64 + '@10.70.0.2/careertrellis'
        for url in (base + '?host=127.0.0.1', base + '?sslmode=require&sslmode=disable',
                base.replace('10.70.0.2', '127.0.0.1'), base.replace('careertrellis', 'other'),
                base.replace('A' * 64, '')):
            files = {ops.AUTH_FILE: ('CAREERTRELLIS_AUTH_V2_ENABLED=0\nCAREERTRELLIS_ADMISSION_DATABASE_URL=' + url).encode(),
                ops.ROOT / 'postgres-runtime.env': ('CAREERTRELLIS_DATABASE_URL=' + base.replace(ops.BROKER, 'careertrellis_runtime')).encode()}
            with patch.object(ops, 'stopped_services'), patch.object(ops, 'check_worker_delivery'), \
                    patch.object(ops, 'read_protected', side_effect=lambda p: files[p]), \
                    patch.object(ops, 'command') as command, patch.object(ops, 'broker_transport_matrix') as matrix:
                with self.assertRaises((ops.OperationError, ValueError)):
                    ops.verify_web_transport()
                command.assert_not_called()
                matrix.assert_not_called()


@unittest.skipUnless(os.environ.get('CAREERTRELLIS_TRANSPORT_REHEARSAL') == '1',
    'Explicit isolated Docker PostgreSQL18 rehearsal required')
class PostgreSQLTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name = 'careertrellis-transport-' + uuid4().hex[:12]
        cls.run_command = staticmethod(subprocess.run)
        cls.docker('run', '--rm', '-d', '--name', cls.name, '--label', 'careertrellis.scope=transport-rehearsal',
            '--tmpfs', '/var/lib/postgresql:rw', '-p', '127.0.0.1::5432',
            '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=careertrellis', 'postgres:18')
        cls.addClassCleanup(cls.cleanup)
        info = json.loads(cls.docker('inspect', cls.name))[0]
        cls.assert_container(info)
        port = int(info['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort'])
        cls.url = make_url('postgresql+psycopg://postgres@127.0.0.1:' + str(port) + '/careertrellis?sslmode=disable')
        cls.admin = create_engine(cls.url, poolclass=NullPool)
        cls.addClassCleanup(cls.admin.dispose)
        for attempt in range(50):
            try:
                with cls.admin.connect() as connection:
                    cls.source, cls.server = connection.execute(text('SELECT host(inet_client_addr()), host(inet_server_addr())')).one()
                break
            except Exception:
                if attempt == 49:
                    raise RuntimeError('Synthetic PostgreSQL did not become ready') from None
                time.sleep(.2)
        with cls.admin.begin() as connection:
            connection.exec_driver_sql('CREATE ROLE ' + ops.BROKER + " LOGIN PASSWORD '" + 'A' * 64 + "'")
            connection.exec_driver_sql('CREATE SCHEMA careertrellis_admission')
            connection.exec_driver_sql("CREATE FUNCTION careertrellis_admission.availability() RETURNS jsonb LANGUAGE sql AS $$ SELECT '{\"status\":\"closed\"}'::jsonb $$")
            connection.exec_driver_sql('GRANT USAGE ON SCHEMA careertrellis_admission TO ' + ops.BROKER)
            cls.hba = connection.scalar(text('SHOW hba_file'))
            if cls.hba != '/var/lib/postgresql/18/docker/pg_hba.conf':
                raise RuntimeError('Unexpected disposable HBA path')
            cls.original = bytes(connection.scalar(text("SELECT pg_read_binary_file(current_setting('hba_file'))")))
        cls.restrict_hba()

    @classmethod
    def docker(cls, *args, data=None):
        result = cls.run_command(['docker', *args], input=data, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Synthetic container command failed; output suppressed')
        return result.stdout

    @classmethod
    def assert_container(cls, info):
        ports = info['NetworkSettings']['Ports']['5432/tcp']
        if (info['Name'] != '/' + cls.name or not info['HostConfig']['AutoRemove'] or info['Mounts']
                or info['Config']['Labels'].get('careertrellis.scope') != 'transport-rehearsal'
                or len(ports) != 1 or ports[0]['HostIp'] != '127.0.0.1'):
            raise RuntimeError('Disposable container identity mismatch')

    @classmethod
    def cleanup(cls):
        cls.assert_container(json.loads(cls.docker('inspect', cls.name))[0])
        cls.docker('stop', cls.name)

    @classmethod
    def restrict_hba(cls):
        prefix = ('host careertrellis ' + ops.BROKER + ' ' + cls.source + '/32 scram-sha-256\n'
            'host all ' + ops.BROKER + ' all reject\nlocal all ' + ops.BROKER + ' reject\n')
        cls.docker('exec', '-i', cls.name, 'dd', 'of=' + cls.hba, 'status=none', data=prefix.encode() + cls.original)
        with cls.admin.connect() as connection:
            errors = connection.execute(text('SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL')).scalars().all()
            if errors:
                raise AssertionError('Synthetic HBA errors: ' + repr(errors))
            connection.execute(text('SELECT pg_reload_conf()'))
        time.sleep(.3)

    def test_real_positive_wrong_password_and_other_database(self):
        broker = self.url.set(username=ops.BROKER, password='A' * 64)
        ops.broker_transport_matrix(broker, expected_source=self.source, expected_server=self.server)
        with self.assertRaises(ops.OperationError):
            ops.broker_transport_matrix(broker.set(password='B' * 64), expected_source=self.source, expected_server=self.server)
        with self.assertRaises(ops.OperationError):
            ops.broker_transport_matrix(broker, expected_source='10.70.0.1', expected_server=self.server)
        with self.admin.connect() as connection:
            self.assertEqual(0, connection.scalar(text('SELECT count(*) FROM pg_stat_activity WHERE usename=:role'), {'role': ops.BROKER}))

    def test_false_success_under_trust_is_rejected(self):
        self.docker('exec', '-i', self.name, 'dd', 'of=' + self.hba, 'status=none', data=self.original)
        with self.admin.connect() as connection:
            connection.execute(text('SELECT pg_reload_conf()'))
        time.sleep(.3)
        try:
            with self.assertRaises(ops.OperationError):
                ops.broker_transport_matrix(self.url.set(username=ops.BROKER, password='A' * 64),
                    expected_source=self.source, expected_server=self.server)
        finally:
            self.restrict_hba()

    def test_real_other_source_and_local_socket(self):
        import base64
        import types
        plan = {'recipient': 'age1' + 'a' * 58, 'database_host': '10.70.0.2',
            'source_cidr': '10.70.0.1/32', 'transport': 'wireguard'}
        record = {'phase': 'login_enabled', 'policy': ops.INITIAL_POLICY,
            'hba_original': base64.b64encode(self.original).decode()}
        with self.admin.connect() as connection:
            current_hba = bytes(connection.scalar(text("SELECT pg_read_binary_file(current_setting('hba_file'))")))
        files = {ops.PLAN: ops.encoded(plan), ops.STATE / 'install.json': ops.encoded(record), ops.HBA: current_hba}
        def query(sql):
            with self.admin.connect() as connection:
                result = connection.exec_driver_sql(sql)
                return result.scalar() if result.returns_rows else None
        def execute(args, **kwargs):
            self.assertEqual('/usr/bin/psql', args[0])
            self.assertIn('passfile=/dev/null', args[5])
            self.assertEqual({'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, kwargs['env'])
            return self.run_command(['docker', 'exec', self.name, 'env', '-i', 'PATH=/usr/bin:/bin', 'LANG=C.UTF-8',
                *args], capture_output=True, timeout=10)
        # Native HBA matching, role/schema and initial phase were separately
        # covered; substitute only host facts around the real psql calls here.
        with patch.dict('sys.modules', {'pwd': types.SimpleNamespace(getpwnam=lambda _: types.SimpleNamespace(pw_uid=999))}), \
                patch.object(ops, 'initial_finished'), patch.object(ops, 'assert_roles'), \
                patch.object(ops, 'revision', return_value=ops.INITIAL_HEAD), \
                patch.object(ops, 'policy', return_value=ops.INITIAL_POLICY), \
                patch.object(ops, 'read_protected', side_effect=lambda p, **kw: files[p]), \
                patch.object(ops, 'hba_candidate', return_value=current_hba), \
                patch.object(ops.subprocess, 'run', side_effect=execute):
            checks = ops.verify_database_transport(Mock(query=Mock(side_effect=query)))
            self.assertIn('local_socket_hba_rejected', checks)
            files[ops.STATE / 'install.json'] = ops.encoded({**record, 'phase': 'prepared'})
            with self.assertRaises(ops.OperationError):
                ops.verify_database_transport(Mock(query=Mock(side_effect=query)))


if __name__ == '__main__':
    unittest.main()
