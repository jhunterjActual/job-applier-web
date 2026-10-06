"""Protected dispatch only; no actual terminal, secrets, host or provider access."""
from contextlib import ExitStack
import io
from pathlib import PurePosixPath, Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import admission_operations as ops


class FixedPath(PurePosixPath):
    def absolute(self):
        return self


class DispatchTests(unittest.TestCase):
    def invoke(self, argv, *, source=None, root=True, readable=True):
        source = source or '/opt/careertrellis/staging/releases/' + 'a' * 40 + '/backend/admission_operations.py'
        target = AsyncMock(return_value={'outcome': 'recovery_released_fresh_sign_in_required'})
        output, errors = io.StringIO(), io.StringIO()
        resource = SimpleNamespace(RLIMIT_CORE=4, setrlimit=Mock())
        with ExitStack() as stack:
            stack.enter_context(patch.object(ops, '__file__', source))
            stack.enter_context(patch.object(ops, 'Path', FixedPath))
            stack.enter_context(patch.object(ops.os, 'geteuid', return_value=0 if root else 1, create=True))
            stack.enter_context(patch.object(ops, 'protected_parents'))
            reader = stack.enter_context(patch.object(ops, 'read_protected',
                side_effect=None if readable else ops.OperationError(), return_value=b''))
            stack.enter_context(patch.dict(sys.modules, {
                'resource': resource, 'recovery_attended_entry': SimpleNamespace(attended=target)}))
            stack.enter_context(patch.object(sys, 'path', list(sys.path)))
            stack.enter_context(patch('sys.stdout', output))
            stack.enter_context(patch('sys.stderr', errors))
            status = ops.main(argv)
        return status, target, reader, resource, output.getvalue(), errors.getvalue()

    def test_exact_immutable_entry_dispatches_and_disables_core(self):
        status, target, reader, resource, output, _ = self.invoke(['web-recovery-attended'])
        self.assertEqual(0, status)
        target.assert_awaited_once_with()
        resource.setrlimit.assert_called_once_with(4, (0, 0))
        self.assertIn('recovery_released_fresh_sign_in_required', output)
        self.assertTrue(any(call.args[0].name == 'recovery_runtime.py' for call in reader.call_args_list))

    def test_no_argv_inputs_or_untrusted_source(self):
        for argv, options in ((['web-recovery-attended', 'secret'], {}),
                (['web-recovery-attended'], {'source': '/tmp/backend/admission_operations.py'}),
                (['web-recovery-attended'], {'root': False}),
                (['web-recovery-attended'], {'readable': False})):
            with self.subTest(argv=argv, options=options):
                status, target, _, _, output, errors = self.invoke(argv, **options)
                self.assertEqual(1, status)
                target.assert_not_awaited()
                self.assertNotIn('secret', output + errors)

    def test_shell_uses_release_runtime_and_isolated_clean_environment(self):
        wrapper = (Path(__file__).parent.parent / 'deploy/scripts/manage-admission-operations.sh').read_text()
        line = next(line for line in wrapper.splitlines() if line.strip().startswith('web-recovery-attended|'))
        self.assertIn('python=${release}/backend/venv/bin/python', line)
        self.assertIn('/usr/bin/env -i', wrapper)
        self.assertIn('"${python}" -I -B', wrapper)


if __name__ == '__main__':
    unittest.main()
