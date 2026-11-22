"""Mocked native primitive contracts; these do not establish native acceptance."""
import errno
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    from . import runtime as r
except ImportError:
    import runtime as r


def leaf(inode=2, mode=0o600):
    return SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=0, st_gid=0,
                           st_dev=1, st_ino=inode, st_nlink=1)


def metadata(path, mode=0o600):
    if path.name in ('run', 'lock'):
        return SimpleNamespace(st_mode=stat.S_IFDIR | (0o755 if path.name == 'run' else 0o1777), st_uid=0, st_gid=0)
    return leaf(mode=mode)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        # Synthetic Linux constants only; every descriptor operation is mocked.
        for name, value in (('O_NOFOLLOW', 0x20000), ('O_NONBLOCK', 0x800)):
            item = patch.object(r.os, name, getattr(r.os, name, value), create=True)
            item.start(); self.addCleanup(item.stop)

    def fence_patches(self, flock=None, fstat=None):
        fake = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=flock or Mock())
        return (patch.dict(sys.modules, {'fcntl': fake}),
                patch.object(r.Path, 'lstat', metadata), patch.object(r.os, 'open', return_value=42),
                patch.object(r.os, 'fstat', side_effect=fstat or (lambda fd: leaf())),
                patch.object(r.os, 'close'))

    def test_failed_flock_closes_only_opened_descriptor(self):
        patches = self.fence_patches(flock=Mock(side_effect=BlockingIOError()))
        with patches[0], patches[1], patches[2], patches[3], patches[4] as close:
            fence = r.Fence('/run/lock/careertrellis-maintenance.lock', 0o600)
            with self.assertRaises(BlockingIOError): fence.acquire()
            close.assert_called_once_with(42)
            self.assertIsNone(fence.fd)

    def test_failed_identity_or_fstat_closes_descriptor(self):
        for probe in (lambda fd: leaf(999), Mock(side_effect=OSError('synthetic'))):
            with self.subTest(probe=probe):
                patches = self.fence_patches(fstat=probe)
                with patches[0], patches[1], patches[2], patches[3], patches[4] as close:
                    fence = r.Fence('/run/lock/careertrellis-maintenance.lock', 0o600)
                    with self.assertRaises((r.Refused, OSError)): fence.acquire()
                    close.assert_called_once_with(42)
                    self.assertIsNone(fence.fd)

    def test_fence_identity_replacement_refuses_without_unsafe_close(self):
        fence = r.Fence('/run/lock/careertrellis-maintenance.lock', 0o600)
        fence.fd = 42; fence.identity = (1, 2)
        with patch.dict(sys.modules, {'fcntl': SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())}), \
             patch.object(r.Path, 'lstat', return_value=leaf(999)), \
             patch.object(r.os, 'fstat', return_value=leaf()), patch.object(r.os, 'close') as close:
            with self.assertRaises(r.Refused): fence.check()
            close.assert_not_called()
            self.assertEqual(42, fence.fd)

    def test_inherit10_refuses_occupied_descriptor_without_replacing_it(self):
        fence = r.Fence('/run/lock/careertrellis-maintenance.lock', 0o600); fence.fd = 9
        with patch.object(r.os, 'fstat', return_value=leaf()), patch.object(r.os, 'dup2') as duplicate, \
             patch.object(r.os, 'close') as close:
            with self.assertRaises(r.Refused): fence.inherit10()
            duplicate.assert_not_called(); close.assert_not_called()

    def test_terminal_requires_control_process_and_pending_job_absent(self):
        runtime = object.__new__(r.HostRuntime)
        base = {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0', 'Job': '', 'ControlGroup': ''}
        runtime.state = Mock(return_value=base)
        self.assertEqual(base, runtime.terminal('fixed.service'))
        for field, value in (('ControlPID', '21'), ('MainPID', '22'), ('Job', '99'), ('ActiveState', 'deactivating')):
            runtime.state.return_value = dict(base, **{field: value})
            with self.subTest(field=field), self.assertRaises(r.Refused): runtime.terminal('fixed.service')

    def test_state_query_includes_control_pid(self):
        runtime = object.__new__(r.HostRuntime)
        runtime.run = Mock(return_value=b'ControlPID=0\n')
        self.assertEqual('0', runtime.state('fixed.service')['ControlPID'])
        self.assertIn('ControlPID', runtime.run.call_args.args[0][-1])

    def test_long_phase_check_keeps_owned_fence_and_has_no_global_timeout_release(self):
        runtime = object.__new__(r.HostRuntime); runtime.started = 0
        runtime.fence = Mock()
        with patch.object(r.time, 'monotonic', return_value=10**12): runtime.check()
        runtime.fence.check.assert_called_once(); runtime.fence.close.assert_not_called()

    def test_peer_operator_marker_wins_over_matching_ready_state(self):
        runtime = object.__new__(r.HostRuntime); runtime.fence = Mock()
        client = SimpleNamespace(call=Mock(return_value={'state': 'sealed', 'operator_required': True}))
        with self.assertRaises(r.Refused): runtime.wait_peer(client, {'sealed'}, 20)
        runtime.fence.close.assert_not_called()

    def test_peer_wait_checks_fence_and_timeout_never_releases_it(self):
        runtime = object.__new__(r.HostRuntime); runtime.fence = Mock()
        client = SimpleNamespace(call=Mock(return_value={'state': 'pair_ready'}))
        self.assertEqual('pair_ready', runtime.wait_peer(client, {'pair_ready'}, 20)['state'])
        self.assertGreaterEqual(runtime.fence.check.call_count, 1)
        runtime.fence.reset_mock()
        client.call.return_value = {'state': 'backup_running'}
        with patch.object(r.time, 'monotonic', side_effect=[0, 30]), patch.object(r.time, 'sleep'):
            with self.assertRaises(r.Refused): runtime.wait_peer(client, {'pair_ready'}, 20)
        runtime.fence.close.assert_not_called()

    def test_lost_peer_or_replaced_fence_never_closes_or_restores(self):
        runtime = object.__new__(r.HostRuntime); runtime.fence = Mock()
        runtime.fence.check.side_effect = r.Refused('fence_replaced')
        client = SimpleNamespace(call=Mock(return_value={'state': 'pair_ready'}))
        with self.assertRaises(r.Refused): runtime.wait_peer(client, {'pair_ready'}, 20)
        runtime.fence.close.assert_not_called()

    def test_fence_tuple_mode_accepts_allowed_and_rejects_disallowed_with_close(self):
        for allowed in (0o600, 0o644):
            with self.subTest(allowed=oct(allowed)):
                fake = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
                with patch.dict(sys.modules, {'fcntl': fake}), \
                     patch.object(r.Path, 'lstat', lambda path: metadata(path, mode=allowed)), \
                     patch.object(r.os, 'open', return_value=42), \
                     patch.object(r.os, 'fstat', return_value=leaf(mode=allowed)), \
                     patch.object(r.os, 'close') as close:
                    fence = r.Fence('/run/lock/careertrellis-maintenance.lock', (0o600, 0o644))
                    self.assertIs(fence.acquire(), fence)
                    self.assertEqual(allowed, fence.acquired_mode)
                    fence.check()
                    fence.close()
                    close.assert_called_once_with(42)
                    self.assertIsNone(fence.fd)
                    self.assertIsNone(fence.acquired_mode)

        for disallowed in (0o640, 0o664, 0o666, 0o755):
            with self.subTest(disallowed=oct(disallowed)):
                fake = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
                with patch.dict(sys.modules, {'fcntl': fake}), \
                     patch.object(r.Path, 'lstat', lambda path: metadata(path, mode=disallowed)), \
                     patch.object(r.os, 'open', return_value=42), \
                     patch.object(r.os, 'fstat', return_value=leaf(mode=disallowed)), \
                     patch.object(r.os, 'close') as close:
                    fence = r.Fence('/run/lock/careertrellis-maintenance.lock', (0o600, 0o644))
                    with self.assertRaisesRegex(r.Refused, '^lock_identity$'):
                        fence.acquire()
                    close.assert_called_once_with(42)
                    self.assertIsNone(fence.fd)
                    self.assertIsNone(fence.acquired_mode)

    def test_fence_check_refuses_mid_lock_mode_change(self):
        for initial, changed in ((0o600, 0o644), (0o644, 0o600)):
            with self.subTest(initial=oct(initial), changed=oct(changed)):
                current = [initial]
                fake = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
                with patch.dict(sys.modules, {'fcntl': fake}), \
                     patch.object(r.Path, 'lstat', lambda path: metadata(path, mode=current[0])), \
                     patch.object(r.os, 'open', return_value=42), \
                     patch.object(r.os, 'fstat', side_effect=lambda fd: leaf(mode=current[0])), \
                     patch.object(r.os, 'close') as close:
                    fence = r.Fence('/run/lock/careertrellis-maintenance.lock', (0o600, 0o644))
                    fence.acquire()
                    self.assertEqual(initial, fence.acquired_mode)
                    current[0] = changed
                    with self.assertRaisesRegex(r.Refused, '^fence_metadata_changed$'):
                        fence.check()
                    close.assert_not_called()
                    self.assertEqual(42, fence.fd)
                    self.assertEqual(initial, fence.acquired_mode)

        fence = r.Fence('/run/lock/careertrellis-maintenance.lock', (0o600, 0o644))
        fence.fd = 42; fence.identity = (1, 2)
        with patch.dict(sys.modules, {'fcntl': SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())}), \
             patch.object(r.Path, 'lstat', return_value=leaf(mode=0o644)), \
             patch.object(r.os, 'fstat', return_value=leaf(mode=0o644)):
            fence.check()
        with patch.dict(sys.modules, {'fcntl': SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())}), \
             patch.object(r.Path, 'lstat', return_value=leaf(mode=0o640)), \
             patch.object(r.os, 'fstat', return_value=leaf(mode=0o640)):
            with self.assertRaisesRegex(r.Refused, '^fence_metadata_changed$'):
                fence.check()

    def test_fence_rejects_invalid_mode_specifications(self):
        for bad in ((), [], set(), frozenset(), True, False, (True,), (0o600, False),
                    '0600', ('0600',), b'0600', -1, (0o600, -1), 0o1000, (0o600, 0o1777),
                    None, {0o600: True}):
            with self.subTest(bad=bad), self.assertRaisesRegex(r.Refused, '^lock_mode$'):
                r.Fence('/run/lock/careertrellis-maintenance.lock', bad)

        for spec, expected in ((0o600, frozenset({0o600})),
                               (0, frozenset({0})),
                               (0o777, frozenset({0o777})),
                               ([0o600, 0o644], frozenset({0o600, 0o644})),
                               ({0o600, 0o644}, frozenset({0o600, 0o644})),
                               (frozenset({0o600, 0o644}), frozenset({0o600, 0o644}))):
            with self.subTest(spec=spec):
                fence = r.Fence('/run/lock/careertrellis-maintenance.lock', spec)
                self.assertEqual(expected, fence.modes)
                self.assertIsNone(fence.acquired_mode)


if __name__ == '__main__': unittest.main()
