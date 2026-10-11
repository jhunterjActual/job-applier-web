import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('coordinator', HERE.parent / 'scripts/object_prefix_rehearsal.py')
coordinator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordinator)
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('recovery', ROOT / 'backend/generated_object_recovery.py')
recovery = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = recovery
spec.loader.exec_module(recovery)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.manifest = recovery.GeneratedObjectManifest('a'*40, '20261009-143304F',
            'a'*40+'-20261009-143304F', 1, 10,
            (recovery.CiphertextManifestEntry('v1/'+'b'*32, 10, 'c'*64),))
        self.seal = dict(self.manifest.sanitized_summary(), authority='postgres-r2-authoritative',
                         capture_started_epoch=100, captured_epoch=101)

    def validate(self, seal=None, raw=None):
        return coordinator.validate_seal(seal or self.seal, raw or self.manifest.serialized(),
                                          recovery._manifest_from_document, 1000)

    def test_trusted_historical_manifest(self):
        self.assertEqual(self.validate(), self.seal)

    def test_mismatched_digest_rejected(self):
        bad = dict(self.seal, manifest_sha256='0'*64)
        with self.assertRaises(RuntimeError): self.validate(bad)

    def test_noncanonical_manifest_rejected(self):
        with self.assertRaises(RuntimeError):
            self.validate(raw=json.dumps(self.manifest.document()).encode())

    def test_future_seal_rejected(self):
        with self.assertRaises(RuntimeError): self.validate(dict(self.seal, captured_epoch=1001))

    def test_wrong_authority_rejected(self):
        with self.assertRaises(RuntimeError): self.validate(dict(self.seal, authority='other'))

    def test_ambiguous_latest_rejected(self):
        with self.assertRaises(RuntimeError): coordinator.choose_latest([self.seal, self.seal])

    def test_latest_selected(self):
        older = dict(self.seal, captured_epoch=99)
        self.assertEqual(coordinator.choose_latest([older, self.seal]), self.seal)

    def test_caps_fail_closed(self):
        for args in [(12, 0, 10, 10**10), (1, coordinator.MAX_RESERVED, 10, 10**10),
                     (1, 0, 10, 3*1024**3)]:
            with self.subTest(args=args), self.assertRaises(RuntimeError): coordinator.capacity(*args)

    def test_capacity_pass(self):
        coordinator.capacity(1, 10, 10, 10**10)

    def test_retained_old_capture_does_not_block_new(self):
        old = dict(self.seal, capture_started_epoch=100, captured_epoch=101)
        now = coordinator.MAX_AGE + 1000
        new = dict(self.seal, capture_started_epoch=now-100, captured_epoch=now-99)
        # Both historical seals remain structurally valid; freshness applies after selection.
        coordinator.validate_seal(old, self.manifest.serialized(), recovery._manifest_from_document, now)
        coordinator.validate_seal(new, self.manifest.serialized(), recovery._manifest_from_document, now)
        self.assertEqual(coordinator.choose_latest([old, new]), new)

    def test_actual_wrapper_output_contract(self):
        output = dict(self.seal, result='disposable_r2_restore_verified_and_left_intact',
                      target_prefix='rehearsals/test')
        self.assertTrue(coordinator.verified_output(output, self.seal, 'rehearsals/test', 0))
        self.assertFalse(coordinator.verified_output(output, self.seal, 'rehearsals/other', 0))
        self.assertFalse(coordinator.verified_output(output, self.seal, 'rehearsals/test', 1))

    def test_failure_publication_sanitized(self):
        with patch.object(coordinator, 'protected'), patch.object(coordinator.time, 'time', return_value=200), \
             patch.object(coordinator.subprocess, 'run') as run:
            coordinator.publish_failure(100)
        payload = json.loads(run.call_args.kwargs['input'])
        self.assertEqual(payload['result'], 'failed')
        self.assertFalse(payload['manifest_verified'])
        self.assertEqual(payload['elapsed_seconds'], 100)


if __name__ == '__main__':
    unittest.main()
