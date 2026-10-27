"""Synthetic process identity boundaries; no proc, user or native commands."""
import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from . import runtime


class ProcessIdentityTests(unittest.TestCase):
    def test_fixed_uid_drop_command_and_no_new_capability(self):
        users = SimpleNamespace(getpwnam=Mock(return_value=SimpleNamespace(pw_uid=123)))
        groups = SimpleNamespace(getgrnam=Mock(return_value=SimpleNamespace(gr_gid=456)))
        with patch.dict(sys.modules, {'pwd': users, 'grp': groups}):
            for unit, user in runtime.PROCESS_USERS.items():
                args = runtime.process_identity_args(unit, '789', 'a' * 40)
                users.getpwnam.assert_called_with(user)
                self.assertEqual(args[:7], ['/usr/bin/setpriv', '--reuid=123', '--regid=456', '--clear-groups', '--reset-env', '--', '/usr/bin/python3'])
                self.assertEqual(args[7:10], ['-I', '-B', '-c'])
                self.assertEqual(args[-1], '/opt/careertrellis/staging/releases/' + 'a' * 40 + '/backend')
                self.assertNotIn('--ambient-caps', args)
            for unit, pid in [('other.service', '789'), ('careertrellis-web.service', '1;sh'), ('careertrellis-web.service', '0')]:
                with self.assertRaises(runtime.Refused): runtime.process_identity_args(unit, pid, 'a' * 40)

    def test_pid_or_invocation_drift_and_child_output_refuse(self):
        before = {'ActiveState': 'active', 'SubState': 'running', 'MainPID': '789', 'Job': '', 'InvocationID': 'a' * 32}
        observer = object.__new__(runtime.Runtime)
        observer.run = Mock(return_value=b'')
        observer.state = Mock(return_value=dict(before))
        with patch.object(runtime, 'process_identity_args', return_value=['fixed-probe']):
            observer.process_release(runtime.SERVICES[0], before)
            for changes in ({'MainPID': '790'}, {'InvocationID': 'b' * 32}, {'Job': 'queued'}, {'ActiveState': 'inactive'}):
                observer.state.return_value = dict(before, **changes)
                with self.assertRaises(runtime.Refused): observer.process_release(runtime.SERVICES[0], before)
            observer.state.return_value = dict(before)
            observer.run.return_value = b'PRIVATE unexpected output'
            with self.assertRaises(runtime.Refused): observer.process_release(runtime.SERVICES[0], before)

    def test_native_probe_child_is_byte_identical_and_reads_no_private_config(self):
        source = Path(__file__).with_name('native_process_identity_probe.py').read_text()
        tree = ast.parse(source)
        values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(name, ast.Name) and name.id == 'CHILD_PROBE' for name in node.targets)]
        self.assertEqual(values, [runtime.PROCESS_IDENTITY_PROBE])
        self.assertNotIn('CAP_SYS_PTRACE', source)
        self.assertNotIn('/etc/careertrellis', source)

    def test_accepted_restore_checks_active_process_through_same_uid_adapter(self):
        observer = Mock()
        observer.live_group.return_value = False
        observer.state.return_value = {'ActiveState': 'active'}
        observer.process_release.side_effect = runtime.Refused('synthetic_process_binding')
        with self.assertRaises(runtime.Refused): runtime.restore_web(observer, recovery=True)
        observer.process_release.assert_called_once_with(runtime.SERVICES[0], {'ActiveState': 'active'})
        observer.run.assert_not_called()

    def test_child_exit_code_checks_uid_cwd_and_pid_start_reuse(self):
        import os
        import io
        namespace = {'__name__': '__synthetic_probe__'}
        def execute(starts, cwd='/fixed/backend', uid=123):
            readings = iter(starts)
            with patch.object(sys, 'argv', ['-c', '789', '123', '456', '/fixed/backend']), \
                 patch.object(os, 'geteuid', return_value=uid, create=True), patch.object(os, 'getegid', return_value=456, create=True), \
                 patch.object(os, 'stat', return_value=SimpleNamespace(st_uid=123)), patch.object(os, 'readlink', return_value=cwd), \
                 patch('builtins.open', side_effect=lambda *args: io.StringIO('789 (worker) ' + ' '.join(['S'] + ['0'] * 18 + [str(next(readings))]))):
                with self.assertRaises(SystemExit) as result: exec(runtime.PROCESS_IDENTITY_PROBE, namespace)
                return result.exception.code
        self.assertEqual(execute([100, 100]), 0)
        self.assertEqual(execute([100, 101]), 1)
        self.assertEqual(execute([100], cwd='/wrong/backend'), 1)
        self.assertEqual(execute([], uid=0), 1)


if __name__ == '__main__': unittest.main()
