"""Synthetic refusal/replay tests for the bounded supplemental verifier."""
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'deploy/scripts/reconcile-transfer-fixture-verification.py'
spec=importlib.util.spec_from_file_location('transfer_reconciliation',SCRIPT)
reconciliation=importlib.util.module_from_spec(spec); spec.loader.exec_module(reconciliation)


class ReconciliationTests(unittest.TestCase):
    def failure(self):
        policy={'allowed_skips':['synthetic_skip_'+str(i) for i in range(76)]}
        value={'returncode':1,'result':{'tests':880,'failures':0,'errors':9,
            'failed_tests':[],'error_tests':list(reconciliation.ERRORS),
            'skipped':list(policy['allowed_skips'])}}
        return value,policy

    def test_exact_failure_accepted_without_reclassifying_errors_as_skips(self):
        value,policy=self.failure(); before=deepcopy(value)
        reconciliation.validate_failure(value,policy)
        self.assertEqual(before,value)

    def test_unexpected_failure_or_missing_error_refused(self):
        for mutate in (
                lambda v:v.update(returncode=0),
                lambda v:v['result'].update(tests=879),
                lambda v:v['result'].update(errors=8),
                lambda v:v['result'].update(failures=1),
                lambda v:v['result'].update(failed_tests=['unexpected']),
                lambda v:v['result']['error_tests'].pop(),
                lambda v:v['result']['error_tests'].__setitem__(0,'different_test'),
                lambda v:v['result']['error_tests'].__setitem__(0,v['result']['error_tests'][1]),
                lambda v:v['result'].update(extra='unexpected')):
            with self.subTest(mutate=mutate):
                value,policy=self.failure(); mutate(value)
                with self.assertRaises(RuntimeError): reconciliation.validate_failure(value,policy)

    def test_skip_changes_refused(self):
        for mutate in (
                lambda v,p:v['result']['skipped'].pop(),
                lambda v,p:v['result']['skipped'].append('new_skip'),
                lambda v,p:v['result']['skipped'].__setitem__(0,'new_skip'),
                lambda v,p:p['allowed_skips'].append('new_skip')):
            with self.subTest(mutate=mutate):
                value,policy=self.failure(); mutate(value,policy)
                with self.assertRaises(RuntimeError): reconciliation.validate_failure(value,policy)

    def test_reviewed_test_repair_bytes_match_pin(self):
        repaired=Path(__file__).with_name('test_identity_plan_transfer.py').read_bytes()
        self.assertEqual(reconciliation.NEW_SHA,reconciliation.sha(repaired))

    def test_any_other_test_overlay_bytes_refused(self):
        with self.assertRaises(RuntimeError): reconciliation.repair_bytes(b'wrong original',b'wrong replacement')
        with patch.object(reconciliation,'OLD_SHA',reconciliation.sha(b'old')):
            with self.assertRaises(RuntimeError): reconciliation.repair_bytes(b'old',b'changed runtime')

    def test_claim_preserves_existing_attempt_even_without_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            evidence=Path(root); prior=evidence/reconciliation.ATTEMPT; prior.mkdir()
            marker=prior/'partial.json'; marker.write_bytes(b'preserve')
            with self.assertRaises(FileExistsError): reconciliation.claim(evidence)
            self.assertEqual(b'preserve',marker.read_bytes())

    @unittest.skipUnless(os.name=='posix','Native directory fsync required')
    def test_claim_is_exclusive_and_private(self):
        with tempfile.TemporaryDirectory() as root:
            evidence=Path(root); attempt=reconciliation.claim(evidence)
            self.assertEqual(0o700,attempt.stat().st_mode&0o777)
            with self.assertRaises(FileExistsError): reconciliation.claim(evidence)

    @unittest.skipUnless(os.name=='posix','Native symlink test required')
    def test_claim_rejects_existing_symlink_without_following_it(self):
        with tempfile.TemporaryDirectory() as root:
            evidence=Path(root); target=evidence/'target'; target.mkdir()
            (evidence/reconciliation.ATTEMPT).symlink_to(target,target_is_directory=True)
            with self.assertRaises(FileExistsError): reconciliation.claim(evidence)
            self.assertEqual([],list(target.iterdir()))


if __name__=='__main__': unittest.main()
