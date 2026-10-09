"""Portable synthetic archive and baseline guards; no host/provider operations."""
import copy
import importlib.util
import io
from pathlib import Path
import tarfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import stat

spec = importlib.util.spec_from_file_location('installer', Path(__file__).with_name('install-db-cleanup-successor.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def payloads():
    names = installer.REQUIRED | {'backend/' + n + '.py' for n in installer.TESTS}
    return {name: b'# synthetic fixture\n' for name in names}


def archive(files, extra=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as output:
        for name, raw in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw); member.mode = 0o644
            output.addfile(member, io.BytesIO(raw))
        if extra is not None:
            output.addfile(extra, io.BytesIO(b''))
    return stream.getvalue()


def baseline():
    return {'database': 'careertrellis', 'user': 'postgres', 'version': '180001',
            'data': '/var/lib/postgresql/18/main', 'heads': ['20261005_0023'],
            'policies': [{'minimum_session_version': 2, 'maintenance': False,
                          'signup_open': True, 'adapter_ready': True}],
            'roles': {name: {'rolcanlogin': True} for name in
                      ('careertrellis_runtime', 'careertrellis_worker',
                       'careertrellis_erasure_worker', 'careertrellis_admission_broker')}}


class InstallerTests(unittest.TestCase):
    def test_exact_files_only_archive(self):
        files = payloads()
        self.assertEqual(installer.archive_files(archive(files), {n: installer.digest(v) for n,v in files.items()}), files)

    def test_changed_or_missing_bytes_refuse(self):
        files = payloads(); manifest = {n: installer.digest(v) for n,v in files.items()}
        name = sorted(files)[0]
        files[name] = b'changed'
        with self.assertRaises(installer.Refused):
            installer.archive_files(archive(files), manifest)
        del files[name]
        with self.assertRaises(installer.Refused):
            installer.archive_files(archive(files), manifest)

    def test_paths_and_secret_content_names_refuse(self):
        for name in ('../escape.py', '/absolute.py', 'backend/../escape.py',
                     'backend/.env.py', 'backend/data/private.py', 'backend\\escape.py'):
            with self.subTest(name=name):
                files = payloads(); files[name] = b''
                with self.assertRaises(installer.Refused):
                    installer.archive_files(archive(files), {n: installer.digest(v) for n,v in files.items()})

    def test_link_directory_and_duplicate_refuse(self):
        files = payloads(); manifest = {n: installer.digest(v) for n,v in files.items()}
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.REGTYPE):
            member = tarfile.TarInfo(sorted(files)[0]); member.type = kind
            member.linkname = '/etc/passwd'; member.mode = 0o644
            with self.subTest(kind=kind), self.assertRaises(installer.Refused):
                installer.archive_files(archive(files, member), manifest)

    def test_required_rebind_contract_cannot_be_omitted(self):
        files = payloads(); del files['backend/monitoring_rebind.py']
        with self.assertRaises(installer.Refused):
            installer.archive_files(archive(files), {n: installer.digest(v) for n,v in files.items()})

    def test_exact_current_policy_passes(self):
        installer.validate_database(baseline())

    def test_legacy_or_duplicate_head_refuses(self):
        for heads in (['20260907_0014'], ['20261005_0023', '20261005_0023']):
            value = baseline(); value['heads'] = heads
            with self.assertRaises(installer.Refused):
                installer.validate_database(value)

    def test_policy_and_role_refusals(self):
        for key, wrong in (('minimum_session_version', 1), ('maintenance', True),
                           ('signup_open', False), ('adapter_ready', False)):
            value = baseline(); value['policies'][0][key] = wrong
            with self.subTest(key=key), self.assertRaises(installer.Refused):
                installer.validate_database(value)
        value = baseline(); value['roles']['careertrellis_runtime']['rolcanlogin'] = False
        with self.assertRaises(installer.Refused):
            installer.validate_database(value)

    def test_duplicate_manifest_keys_refuse(self):
        with self.assertRaises(installer.Refused):
            installer.strict_json('{"same":"one","same":"two"}')

    def test_lock_order_and_failure_release(self):
        for busy in (False, True):
            opened, closed, acquired = [], [], []
            def metadata(path):
                name = str(path).replace('\\', '/')
                mode = (stat.S_IFDIR | (0o1777 if name == '/run/lock' else 0o755)) if name in ('/run', '/run/lock') else stat.S_IFREG | 0o600
                return SimpleNamespace(st_mode=mode, st_uid=0, st_gid=0, st_nlink=1,
                                       st_dev=1, st_ino=12 if name.endswith('migration.lock') else 11)
            def opened_fd(path, flags):
                opened.append(str(path)); return 10 + len(opened)
            def flock(fd, flags):
                acquired.append(fd)
                if busy and fd == 12:
                    raise BlockingIOError('synthetic contention')
            with self.subTest(busy=busy), patch.dict('sys.modules', {'fcntl': SimpleNamespace(flock=flock, LOCK_EX=2, LOCK_NB=4)}), \
                 patch.object(installer.Path, 'lstat', autospec=True, side_effect=metadata), \
                 patch.object(installer.os, 'open', side_effect=opened_fd), \
                 patch.object(installer.os, 'fstat', side_effect=lambda fd: metadata('/run/lock/' + ('careertrellis-postgres-migration.lock' if fd == 12 else 'careertrellis-maintenance.lock'))), \
                 patch.object(installer.os, 'close', side_effect=closed.append), \
                 patch.object(installer.os, 'O_NOFOLLOW', 0, create=True), \
                 patch.object(installer.os, 'O_NONBLOCK', 0, create=True):
                if busy:
                    with self.assertRaises(BlockingIOError), installer.host_locks():
                        self.fail('must not reach protected operation')
                else:
                    with installer.host_locks() as continuity:
                        continuity()
                        self.assertEqual(closed, [])
            self.assertEqual([Path(p).name for p in opened],
                             ['careertrellis-maintenance.lock', 'careertrellis-postgres-migration.lock'])
            self.assertEqual(acquired, [11, 12])
            self.assertEqual(closed, [12, 11])


if __name__ == '__main__':
    unittest.main()
