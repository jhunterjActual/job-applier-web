"""Synthetic operator handoff; native descriptor checks use root-only temp dirs."""
from contextlib import ExitStack, nullcontext
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import admission_operations as ops
import identity_operator as operator
from test_identity_evidence import packet

SOURCE='c'*40


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.proof=packet(); self.op=self.proof['context']['operation']
        self.path=self.root/(self.op+'.json'); self.record=self.root/'journal.json'
        self.raw=ops.encoded(self.proof); self.path.write_bytes(self.raw)
        self.stack.enter_context(patch.object(operator,'locations',return_value=(self.path,self.record)))
        self.stack.enter_context(patch.object(operator,'preflight'))
        self.stack.enter_context(patch.object(ops,'read_protected',side_effect=lambda p,**kw:p.read_bytes()))
        self.stack.enter_context(patch.object(ops,'write_protected',side_effect=self.write))
        self.remover=self.stack.enter_context(patch.object(operator,'remove_exact',side_effect=lambda p,*args:p.unlink()))
        self.recorded=False
        self.stack.enter_context(patch.object(operator,'inspect',side_effect=lambda *args:{'state':'verified' if self.recorded else 'held','recorded':self.recorded}))
        self.submit=self.stack.enter_context(patch.object(operator.evidence,'submit',side_effect=self.commit))

    def write(self,path,raw,previous=None,**kwargs):
        if previous is not None: self.assertEqual(previous,path.read_bytes())
        else: self.assertFalse(path.exists())
        path.write_bytes(raw)

    def commit(self,*args): self.recorded=True

    def run_action(self,action): return operator.process(action,self.op,SOURCE,Mock())

    def test_lost_commit_ack_keeps_input_then_reconcile_only_cleans(self):
        def lost(*args): self.recorded=True; raise RuntimeError('synthetic lost acknowledgement')
        self.submit.side_effect=lost
        with self.assertRaises(RuntimeError): self.run_action('attest')
        self.assertEqual(self.raw,self.path.read_bytes())
        self.assertEqual({'outcome':'recorded_input_removed'},self.run_action('reconcile'))
        self.assertFalse(self.path.exists()); self.assertEqual(1,self.submit.call_count)
        self.assertEqual({'outcome':'recorded_input_removed'},self.run_action('reconcile'))

    def test_unrecorded_reconcile_never_submits_or_removes(self):
        self.submit.side_effect=RuntimeError('synthetic before commit')
        with self.assertRaises(RuntimeError): self.run_action('attest')
        with self.assertRaises(ops.OperationError): self.run_action('reconcile')
        self.assertTrue(self.path.exists()); self.assertEqual(1,self.submit.call_count)

    def test_changed_input_and_source_keep_evidence(self):
        self.submit.side_effect=RuntimeError('synthetic unknown outcome')
        with self.assertRaises(RuntimeError): self.run_action('attest')
        self.proof['target']['factor']='password_totp'; self.path.write_bytes(ops.encoded(self.proof))
        with self.assertRaises(ops.OperationError): self.run_action('reconcile')
        with self.assertRaises(ops.OperationError): operator.process('status',self.op,'d'*40,Mock())
        self.assertTrue(self.path.exists())

    def test_cleanup_failure_reports_recorded_without_resubmission(self):
        self.remover.side_effect=OSError('synthetic denied cleanup')
        self.assertEqual({'outcome':'recorded_cleanup_pending'},self.run_action('attest'))
        self.assertTrue(self.path.exists())
        self.assertEqual('recorded',ops.strict_json(self.record.read_bytes())['phase'])
        self.assertTrue(self.run_action('status')['recorded'])

    def test_status_does_not_write_or_submit(self):
        self.assertEqual({'state':'held','recorded':False},self.run_action('status'))
        self.assertFalse(self.record.exists()); self.submit.assert_not_called()

    def test_bridge_reconcile_does_not_revalidate_expired_holder(self):
        import owner_recovery_bridge as bridge
        def lost(*args): self.recorded=True; raise RuntimeError('synthetic lost acknowledgement')
        self.submit.side_effect=lost
        with self.assertRaises(RuntimeError): self.run_action('attest')
        store=Mock(); store.locked.side_effect=nullcontext
        with patch.object(ops,'host_locks',side_effect=nullcontext), \
                patch.object(bridge,'verify',side_effect=AssertionError('must not validate expired proof')) as verify:
            result=bridge.attest('reconcile',self.op,SOURCE,Mock(),store=store)
        self.assertEqual({'outcome':'recorded_input_removed'},result)
        verify.assert_not_called()
        self.assertEqual(1,self.submit.call_count)

    def test_recorded_journal_write_failure_keeps_input_and_reports_pending(self):
        with patch.object(ops,'state_write',side_effect=OSError('synthetic disk full')):
            self.assertEqual({'outcome':'recorded_cleanup_pending'},self.run_action('attest'))
        self.remover.assert_not_called(); self.assertTrue(self.path.exists())
        self.assertTrue(self.run_action('status')['recorded'])
        self.assertEqual({'outcome':'recorded_input_removed'},self.run_action('reconcile'))


@unittest.skipUnless(os.name=='posix' and getattr(os,'geteuid',lambda:-1)()==0,'Native root Linux fixture required')
class NativeOperatorTests(unittest.TestCase):
    def test_exact_cleanup_refuses_changed_link_and_permissions(self):
        with tempfile.TemporaryDirectory(dir='/',prefix='ct-operator-') as directory:
            root=Path(directory); path=root/'packet.json'; raw=b'synthetic'
            path.write_bytes(raw); path.chmod(0o600); original=operator.fingerprint(path)
            path.write_bytes(b'changed')
            with self.assertRaises(ops.OperationError): operator.remove_exact(path,raw,original)
            path.write_bytes(raw); path.chmod(0o644); original=operator.fingerprint(path)
            with self.assertRaises(ops.OperationError): operator.remove_exact(path,raw,original)
            path.chmod(0o600); alias=root/'alias'; os.link(path,alias)
            original=operator.fingerprint(path)
            with self.assertRaises(ops.OperationError): operator.remove_exact(path,raw,original)
            alias.unlink(); original=operator.fingerprint(path)
            operator.remove_exact(path,raw,original); self.assertFalse(path.exists())

    def test_fixed_paths_and_private_directory(self):
        with tempfile.TemporaryDirectory(dir='/',prefix='ct-operator-') as directory:
            root=Path(directory)
            with patch.object(operator,'INBOX',root):
                op=packet()['context']['operation']
                self.assertEqual(root/(op+'.json'),operator.locations(op)[0])
                for bad in ('../packet','',op.upper()):
                    with self.assertRaises(Exception): operator.locations(bad)
                root.chmod(0o755)
                with self.assertRaises(ops.OperationError): operator.locations(op)
