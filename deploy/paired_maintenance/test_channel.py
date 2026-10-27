import json
import stat
import subprocess
import types
import unittest
from unittest.mock import patch

from deploy.paired_maintenance import client, ssh_gate
from deploy.paired_maintenance.common import Refused, canonical
from deploy.paired_maintenance.db_control import SCHEMA


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.policy = types.SimpleNamespace(digest='a' * 64)
        self.request = {'schema': SCHEMA, 'transaction': 'b' * 32,
                        'policy_sha256': self.policy.digest, 'operation': 'status', 'payload': {}}
        self.envelope = {'schema': client.RESPONSE_SCHEMA, 'transaction': 'b' * 32,
                         'policy_sha256': self.policy.digest, 'payload': {'state': 'idle'}}
        self.result = types.SimpleNamespace(returncode=0, stdout=canonical(self.envelope), stderr=b'PRIVATE')

    def test_client_fixed_command_and_environment(self):
        with patch.object(client, '_trusted_file') as trust, patch.object(client.subprocess, 'run', return_value=self.result) as runner:
            self.assertEqual(client.Client(self.policy, 'b' * 32).call('status', {}), {'state': 'idle'})
        self.assertEqual(trust.call_count, 2)
        args = runner.call_args.args[0]
        self.assertEqual(args[-2:], ['careertrellis-backup-control@10.70.0.2', client.COMMAND])
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertIn('PasswordAuthentication=no', args)
        self.assertIn('KbdInteractiveAuthentication=no', args)
        self.assertEqual(args[args.index('-b') + 1], '10.70.0.1')
        self.assertEqual(runner.call_args.kwargs['timeout'], 35)
        self.assertEqual(runner.call_args.kwargs['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        self.assertNotIn('shell', runner.call_args.kwargs)

    def test_lost_acknowledgement_never_retries(self):
        with patch.object(client, '_trusted_file'), patch.object(client.subprocess, 'run', side_effect=subprocess.TimeoutExpired('PRIVATE', 35)) as runner:
            with self.assertRaisesRegex(Refused, '^peer_unavailable$'):
                client.Client(self.policy, 'b' * 32).call('status', {})
            self.assertEqual(runner.call_count, 1)

    def test_invalid_local_request_never_connects(self):
        for operation, payload in [('status;sh', {}), ('status', {'path': '/secret'})]:
            with patch.object(client.subprocess, 'run') as runner:
                with self.assertRaisesRegex(Refused, '^protocol_refused$'):
                    client.Client(self.policy, 'b' * 32).call(operation, payload)
                runner.assert_not_called()

    def test_wrong_binding_and_bad_responses(self):
        values = [b'PRIVATE', b'{}' * 40000, b'{"x":1,"x":2}', b'{"x":NaN}']
        for field in ['schema', 'transaction', 'policy_sha256', 'payload']:
            value = dict(self.envelope)
            value[field] = 'PRIVATE'
            values.append(canonical(value))
        for raw in values:
            with self.subTest(raw=raw[:30]), patch.object(client, '_trusted_file'), patch.object(client.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout=raw, stderr=b'PRIVATE')):
                with self.assertRaisesRegex(Refused, '^protocol_refused$'):
                    client.Client(self.policy, 'b' * 32).call('status', {})

    def test_remote_failure_hides_stderr(self):
        with patch.object(client, '_trusted_file'), patch.object(client.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1, stdout=b'PRIVATE', stderr=b'PRIVATE')):
            with self.assertRaisesRegex(Refused, '^peer_unavailable$'):
                client.Client(self.policy, 'b' * 32).call('status', {})

    def test_gate_fixed_root_command(self):
        runner = unittest.mock.Mock(return_value=self.result)
        value = ssh_gate.run({'SSH_ORIGINAL_COMMAND': client.COMMAND, 'LD_PRELOAD': 'PRIVATE'}, canonical(self.request), runner)
        self.assertEqual(json.loads(value), self.envelope)
        self.assertEqual(runner.call_args.args[0], ssh_gate.ROOT_COMMAND)
        self.assertEqual(runner.call_args.kwargs['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        self.assertEqual(runner.call_args.kwargs['timeout'], 35)

    def test_gate_rejects_command_tty_and_input_before_sudo(self):
        cases = [({}, canonical(self.request)), ({'SSH_ORIGINAL_COMMAND': client.COMMAND + ';sh'}, canonical(self.request)),
                 ({'SSH_ORIGINAL_COMMAND': client.COMMAND, 'SSH_TTY': ''}, canonical(self.request)),
                 ({'SSH_ORIGINAL_COMMAND': client.COMMAND}, b' ' * 16385),
                 ({'SSH_ORIGINAL_COMMAND': client.COMMAND}, b'{"operation":"status","operation":"begin"}')]
        for env, raw in cases:
            runner = unittest.mock.Mock()
            output = ssh_gate.run(env, raw, runner)
            runner.assert_not_called()
            self.assertTrue(json.loads(output)['payload']['operator_required'])
            self.assertNotIn(b'PRIVATE', output)

    def test_gate_failure_bound_and_private_single_attempt(self):
        for failure in [subprocess.TimeoutExpired('PRIVATE', 35), RuntimeError('PRIVATE')]:
            runner = unittest.mock.Mock(side_effect=failure)
            result = json.loads(ssh_gate.run({'SSH_ORIGINAL_COMMAND': client.COMMAND}, canonical(self.request), runner))
            self.assertEqual(result['transaction'], self.request['transaction'])
            self.assertEqual(result['policy_sha256'], self.policy.digest)
            self.assertEqual(result['payload'], {'state': 'unknown', 'operator_required': True})
            self.assertEqual(runner.call_count, 1)

    def test_metadata_guards(self):
        good = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_gid=0, st_nlink=1)
        directory = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        def lstat(path):
            return good if path == client.Path(client.KEY) else directory
        with patch.object(client.os, 'lstat', side_effect=lstat):
            client._trusted_file(client.KEY, {0o600})
            for field, value in [('st_uid', 1), ('st_gid', 1), ('st_nlink', 2), ('st_mode', stat.S_IFLNK | 0o600), ('st_mode', stat.S_IFREG | 0o644)]:
                old = getattr(good, field)
                setattr(good, field, value)
                with self.assertRaises(Refused):
                    client._trusted_file(client.KEY, {0o600})
                setattr(good, field, old)
            directory.st_mode |= 0o020
            with self.assertRaises(Refused):
                client._trusted_file(client.KEY, {0o600})


if __name__ == '__main__':
    unittest.main()
