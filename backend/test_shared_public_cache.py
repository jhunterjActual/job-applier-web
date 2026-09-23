"""Synthetic hosted-cache contracts; no provider requests or private app data."""
import json
import multiprocessing
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import public_job_feeds as feeds


PAYLOAD = json.dumps({'jobs': [{
    'jobTitle': 'Engineer', 'url': 'https://jobicy.com/jobs/123-engineer',
    'companyName': 'Example', 'jobGeo': 'USA',
    'jobDescription': 'Build useful things.', 'jobType': ['full-time'],
}]})


def _refresh_process(directory, started, release, results, fail):
    """Hold actual process lock while a synthetic transport is in progress."""
    def fetch(*args):
        started.set()
        if not release.wait(15):
            raise RuntimeError('parent did not release synthetic transport')
        if fail:
            raise feeds.SourceError('network')
        return PAYLOAD

    try:
        with patch.object(feeds, '_directory', return_value=Path(directory)), \
                patch.object(feeds, '_fetch', side_effect=fetch):
            rows = feeds._snapshot('jobicy', lambda: None)
        results.put(('ok', len(rows)))
    except feeds.SourceError as error:
        results.put(('error', error.code))


class SharedPublicCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.fake_config = SimpleNamespace(
            DATA_DIR=self.directory / 'private-worker',
            get_runtime_environment=lambda: 'staging',
        )
        original_config = sys.modules.get('config')
        sys.modules['config'] = self.fake_config
        def restore_config():
            if original_config is None:
                sys.modules.pop('config', None)
            else:
                sys.modules['config'] = original_config
        self.addCleanup(restore_config)
        for replacement in (
                patch.object(feeds, 'HOSTED_CACHE_DIRECTORY', self.directory),
                patch.object(feeds, '_fetch', side_effect=AssertionError('network forbidden'))):
            replacement.start()
            self.addCleanup(replacement.stop)

    def seed(self, age=0, success=True):
        stamp = time.time() - age
        record = {'version': 1, 'provider': 'jobicy', 'attempted_at': stamp,
                  'succeeded_at': stamp if success else None,
                  'payload': PAYLOAD if success else None}
        (self.directory / 'jobicy.json').write_text(json.dumps(record), encoding='utf-8')

    def test_distinct_private_directories_share_hosted_snapshot(self):
        self.seed()
        for private_name in ('private-worker', 'private-web'):
            with self.subTest(private_name=private_name):
                self.fake_config.DATA_DIR = self.directory / private_name
                self.assertEqual(feeds._directory(), self.directory)
                self.assertEqual(feeds._snapshot('jobicy', lambda: None, refresh=False)[0]['company'], 'Example')
                self.assertFalse(self.fake_config.DATA_DIR.exists())

    def test_hosted_read_never_mkdir_lock_write_or_transport(self):
        self.seed()
        before = (self.directory / 'jobicy.json').read_bytes()
        with patch.object(Path, 'mkdir', side_effect=AssertionError('mkdir forbidden')), \
                patch.object(feeds, '_lock', side_effect=AssertionError('lock forbidden')), \
                patch.object(feeds, '_write', side_effect=AssertionError('write forbidden')):
            detail = feeds.details_for_url('https://jobicy.com/jobs/123-engineer')
        self.assertEqual(detail['title'], 'Engineer')
        self.assertEqual((self.directory / 'jobicy.json').read_bytes(), before)
        self.assertEqual([item.name for item in self.directory.iterdir()], ['jobicy.json'])

    def test_missing_expired_and_failed_snapshots_cannot_refresh_from_reader(self):
        with patch.object(feeds, '_lock', side_effect=AssertionError('lock forbidden')):
            self.assertIsNone(feeds.details_for_url('https://jobicy.com/jobs/123-engineer'))
            for age, success in ((feeds.REFRESH_SECONDS, True), (0, False)):
                with self.subTest(age=age, success=success):
                    self.seed(age=age, success=success)
                    with self.assertRaisesRegex(feeds.SourceError, 'snapshot_expired'):
                        feeds._snapshot('jobicy', lambda: None, refresh=False)
                    self.assertIsNone(feeds.details_for_url('https://jobicy.com/jobs/123-engineer'))

    def test_hosted_directory_must_be_provisioned(self):
        with patch.object(feeds, 'HOSTED_CACHE_DIRECTORY', self.directory / 'missing'), \
                patch.object(Path, 'mkdir', side_effect=AssertionError('mkdir forbidden')):
            with self.assertRaisesRegex(feeds.SourceError, 'cache_unavailable'):
                feeds._directory()

    def test_local_directory_remains_under_local_data(self):
        self.fake_config.get_runtime_environment = lambda: 'local'
        expected = self.fake_config.DATA_DIR / 'public-job-feeds'
        self.assertEqual(feeds._directory(), expected)
        self.assertTrue(expected.is_dir())

    @unittest.skipIf(os.name == 'nt', 'POSIX ownership and mode contract')
    def test_only_shared_snapshots_are_group_readable_under_private_umask(self):
        shared = self.directory / 'jobicy.json'
        private = self.directory / 'private'
        private.mkdir()
        previous_umask = os.umask(0o077)
        try:
            feeds._write(shared, {'public': True})
            feeds._write(private / 'jobicy.json', {'public': True})
            self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE((private / 'jobicy.json').stat().st_mode), 0o600)
        finally:
            os.umask(previous_umask)

    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0,
                         'distinct UID permission contract requires isolated POSIX root')
    def test_web_identity_reads_but_cannot_write_worker_snapshot(self):
        worker_uid, web_uid, shared_gid = 21001, 21002, 21003
        os.chown(self.directory, worker_uid, shared_gid)
        self.directory.chmod(0o750)

        def under_identity(uid, action):
            child = os.fork()
            if child == 0:
                try:
                    os.setgroups([])
                    os.setgid(shared_gid)
                    os.setuid(uid)
                    action()
                except BaseException:
                    os._exit(1)
                os._exit(0)
            _, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)

        def write_snapshot():
            with patch.object(feeds, '_fetch', return_value=PAYLOAD):
                feeds._snapshot('jobicy', lambda: None)

        under_identity(worker_uid, write_snapshot)

        def read_snapshot():
            self.assertEqual(len(feeds._snapshot('jobicy', lambda: None, refresh=False)), 1)
            for target in (self.directory / 'jobicy.json', self.directory / 'new-file'):
                with self.assertRaises(PermissionError):
                    target.write_text('forbidden', encoding='utf-8')
            with self.assertRaises(PermissionError):
                (self.directory / 'jobicy.json').unlink()

        under_identity(web_uid, read_snapshot)

    def _check_process_reservation(self, fail):
        context = multiprocessing.get_context('spawn')
        started, release = context.Event(), context.Event()
        results = context.Queue()
        process = context.Process(target=_refresh_process,
                                  args=(str(self.directory), started, release, results, fail))
        process.start()
        try:
            self.assertTrue(started.wait(15), 'refresh process never reached synthetic transport')
            reserved = json.loads((self.directory / 'jobicy.json').read_text(encoding='utf-8'))
            self.assertIsNone(reserved['succeeded_at'])
            with self.assertRaisesRegex(feeds.SourceError, 'cache_unavailable'):
                feeds._snapshot('jobicy', lambda: None)
            release.set()
            process.join(15)
            self.assertFalse(process.is_alive(), 'refresh process did not finish')
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(results.get(timeout=5), ('error', 'network') if fail else ('ok', 1))
            if fail:
                with self.assertRaisesRegex(feeds.SourceError, 'refresh_cooldown'):
                    feeds._snapshot('jobicy', lambda: None)
            else:
                self.assertEqual(len(feeds._snapshot('jobicy', lambda: None)), 1)
        finally:
            release.set()
            if process.is_alive():
                process.terminate()
            process.join(5)
            results.close()
            results.join_thread()

    def test_cross_process_success_blocks_duplicate_and_reuses_snapshot(self):
        self._check_process_reservation(fail=False)

    def test_cross_process_failure_preserves_cooldown(self):
        self._check_process_reservation(fail=True)


if __name__ == '__main__':
    unittest.main()
