"""Typed systemd metadata fixtures only; no commands or providers are executed."""
import unittest

from deploy.onedrive_retention import common_io as common

UNIT = 'careertrellis-onedrive-bundle-restore-' + 'a'*32 + '-02.service'


class Capture:
    def __init__(self, scalar=b'LoadState=loaded\nMainPID=0\nJob=\n', hook=common.EMPTY_EXEC_ARRAY):
        self.scalar, self.hook, self.calls = scalar, hook, []
    def __call__(self, args):
        self.calls.append(args)
        return self.scalar if args[0] == '/usr/bin/systemctl' else self.hook


class TypedPropertyTests(unittest.TestCase):
    def test_only_fixed_scheduler_unit_and_closed_identity_scalars_are_added(self):
        name = 'careertrellis-onedrive-retention.service'
        capture = Capture(scalar=b'User=root\nGroup=root\nUMask=0077\n')
        self.assertEqual(common.read_unit_properties(name,('User','Group','UMask'),capture),
                         {'User':'root','Group':'root','UMask':'0077'})
        for other in (name+'x',name.replace('retention','retention-preview'),
                      'careertrellis-onedrive-retention@abc.service'):
            with self.assertRaises(common.UnitPropertyRefused):common.unit_object_path(other)
    def test_scalar_query_excludes_hook_arrays_and_each_empty_hook_is_typed(self):
        capture = Capture()
        fields = ('LoadState', 'MainPID', 'Job', *sorted(common.HOOK_PROPERTIES))
        result = common.read_unit_properties(UNIT, fields, capture)
        self.assertEqual(result['Job'], '')
        self.assertEqual({key: result[key] for key in common.HOOK_PROPERTIES}, dict.fromkeys(common.HOOK_PROPERTIES, ''))
        self.assertEqual(capture.calls[0][-1], '--property=LoadState,MainPID,Job')
        self.assertEqual(len(capture.calls), 1+len(common.HOOK_PROPERTIES))
        self.assertTrue(all(call[0] == '/usr/bin/busctl' for call in capture.calls[1:]))
    def test_object_path_uses_ascii_alnum_and_lowercase_hex_escaping(self):
        expected = '/org/freedesktop/systemd1/unit/careertrellis_2donedrive_2dbundle_2drestore_2d' + 'a'*32 + '_2d02_2eservice'
        self.assertEqual(common.unit_object_path(UNIT), expected)
        for unit in ('other.service', UNIT+'/x', UNIT+'\n', UNIT.replace('-02.', '-01.'),
                     UNIT.replace('restore', 'restöre'), 'careertrellis-onedrive-bundle-identity-'+'a'*32+'-00.service'):
            with self.assertRaises(common.UnitPropertyRefused): common.unit_object_path(unit)
    def test_missing_scalar_or_extra_array_output_never_means_empty(self):
        for raw in (b'LoadState=loaded\n', b'LoadState=loaded\nMainPID=0\nJob=\nExecStop=\n',
                    b'LoadState=loaded\nMainPID=0\nJob=\nMainPID=0\n', b'', b'x'*16385):
            with self.subTest(raw=raw[:30]), self.assertRaises(common.UnitPropertyRefused):
                common.read_unit_properties(UNIT, ('LoadState', 'MainPID', 'Job', 'ExecStop'), Capture(scalar=raw))
    def test_wrong_nonempty_missing_or_extra_typed_response_refuses(self):
        for raw in (b'', b'as 0\n', b'a(sasbttttuii) 1 x\n', b'a(sasbttttuii) 0\nextra\n',
                    b'a(sasbttttuii) 0', b'a(sasbttttuii) 0\r\n'):
            with self.subTest(raw=raw), self.assertRaises(common.UnitPropertyRefused):
                common.read_unit_properties(UNIT, ('ExecStop',), Capture(hook=raw))
    def test_unknown_fields_or_duplicate_requests_refuse_before_capture(self):
        for fields in ((), ('Environment',), ('ExecStop', 'ExecStop'), ('Job', True)):
            capture=Capture()
            with self.assertRaises(common.UnitPropertyRefused): common.read_unit_properties(UNIT, fields, capture)
            self.assertEqual(capture.calls, [])
    def test_capture_failure_never_exposes_raw_error_or_falls_back(self):
        calls=[]
        def failed(args): calls.append(args); raise RuntimeError('private detail')
        with self.assertRaisesRegex(common.UnitPropertyRefused, '^typed_unit_property_refused$'):
            common.read_unit_properties(UNIT, ('ExecStop',), failed)
        self.assertEqual(len(calls), 1)
