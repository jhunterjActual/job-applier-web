"""Synthetic replacement fencing; native root fixtures exercise protected I/O."""
import os
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import admission_operations as ops
import identity_plan_store as store
import identity_plan_supersession as supersession
import identity_plan_transfer as transfer
import identity_selection_barrier as barrier
import identity_operator as operator
import identity_entry as entry
from test_identity_plan_store import StoreTests


class BarrierTests(StoreTests):
    def record(self):
        return dict(kind='identity_selection_barrier_v1',transaction=str(uuid4()),host='web',
            prior_selection_sha256='1'*64,target_archive_sha256='2'*64,
            journal_sha256='3'*64,state='blocked')

    def block(self):
        value=self.record()
        barrier.install(ops.encoded(value))
        return value

    def test_no_barrier_keeps_existing_selected_plan_contract(self):
        selected=store.select(self.digest,self.p,previous=None)
        self.assertIsNone(barrier.inspect())
        self.assertEqual((selected,self.value['switch']),store.load())
        store.require_current(selected)

    def test_durable_block_preserves_selection_and_allows_only_inspection(self):
        selected=store.select(self.digest,self.p,previous=None)
        value=self.block()
        for action in (store.load,lambda:store.require_current(selected),
                       lambda:store.select(self.digest,self.b,previous=selected),
                       lambda:store.register(ops.encoded(self.value))):
            with self.assertRaises(ops.OperationError): action()
        self.assertEqual(selected,store.location().read_bytes())
        inspected=store.inspect_selection()
        self.assertEqual(value,inspected['barrier'])
        self.assertEqual(self.p,inspected['selection']['operation'])
        self.assertNotIn('provider',inspected)
        # Reinstantiating the reader cannot clear the persistent fence.
        self.assertEqual(value,barrier.inspect())

    def test_exact_reinstall_does_not_rewrite_and_conflict_cannot_replace(self):
        value=self.block()
        before=barrier.location().stat().st_mtime_ns
        barrier.install(ops.encoded(value))
        self.assertEqual(before,barrier.location().stat().st_mtime_ns)
        with self.assertRaises(ops.OperationError): barrier.install(ops.encoded(self.record()))
        self.assertEqual(value,barrier.inspect())

    def test_malformed_barrier_never_counts_as_absence(self):
        store.select(self.digest,self.p,previous=None)
        for raw in (b'{}',b'null',b'{',ops.encoded({**self.record(),'state':'released'}),
                    ops.encoded({**self.record(),'transaction':'invalid'})):
            barrier.location().write_bytes(raw)
            barrier.location().chmod(0o600)
            with self.assertRaises(ops.OperationError): store.load()
            with self.assertRaises(ops.OperationError): store.inspect_selection()
        barrier.location().unlink()

    def test_dangling_symlink_barrier_never_counts_as_absence(self):
        store.select(self.digest,self.p,previous=None)
        try:
            barrier.location().symlink_to(self.root/'missing-barrier-target')
        except OSError:
            self.skipTest('symlink creation unavailable on this platform')
        with self.assertRaises(ops.OperationError): store.load()
        with self.assertRaises(ops.OperationError): barrier.install(ops.encoded(self.record()))

    def test_present_block_rejects_transfer_before_guard_or_archive_write(self):
        source=Path('/opt/careertrellis/staging/releases')/('c'*40)/'backend/admission_operations.py'
        selected=store.select(self.digest,self.p,previous=None)
        transfer.export(source)
        raw=(self.root/'identity-plan-transfer.outgoing.json').read_bytes()
        self.block()
        guard=Mock()
        with patch.object(ops,'write_protected') as write:
            with self.assertRaises(ops.OperationError): transfer.install(raw,selected,source,guard)
            with self.assertRaises(ops.OperationError): transfer.export(source)
        guard.assert_not_called(); write.assert_not_called()

    def test_block_rejects_supersession_before_database_connection(self):
        selected=store.select(self.digest,self.p,previous=None)
        self.block()
        with patch.object(supersession.operator,'secure_connection') as connect:
            with self.assertRaises(ops.OperationError):
                supersession.supersede(selected,Path('/unused'),Mock())
        connect.assert_not_called()

    def test_native_unsafe_barrier_file_rejected(self):
        if os.name!='posix' or os.geteuid()!=0:
            self.skipTest('native root protected-filesystem fixture required')
        store.select(self.digest,self.p,previous=None)
        self.block()
        barrier.location().chmod(0o644)
        with self.assertRaises(ops.OperationError): store.load()

    def test_block_rejects_standalone_attestation_and_entry_before_effects(self):
        self.block()
        with patch.object(operator,'locations') as locations, patch.object(entry,'SecureTerminal') as terminal:
            for action in ('attest','reconcile'):
                with self.assertRaises(ops.OperationError):
                    operator.process(action,self.b,'c'*40,Mock())
            with self.assertRaises(ops.OperationError): entry.enter(self.b,Mock())
        locations.assert_not_called(); terminal.assert_not_called()

    def test_parent_validation_failure_is_not_absent_barrier(self):
        for failure in (FileNotFoundError(),PermissionError(),ops.OperationError(ops.ERROR)):
            with patch.object(ops,'protected_parents',side_effect=failure):
                with self.assertRaises(ops.OperationError): barrier.inspect()

    def test_native_barrier_install_obeys_existing_host_lock(self):
        if os.name!='posix' or os.geteuid()!=0:
            self.skipTest('native root protected-filesystem fixture required')
        with patch.object(ops,'host_locks',self.real_locks), self.real_locks():
            with self.assertRaises(ops.OperationError): self.block()
        self.assertFalse(barrier.location().exists())

    def test_native_hardlink_and_unsafe_parent_fail_closed(self):
        if os.name!='posix' or os.geteuid()!=0:
            self.skipTest('native root protected-filesystem fixture required')
        self.block()
        linked=self.root/'barrier-link'
        os.link(barrier.location(),linked)
        with self.assertRaises(ops.OperationError): barrier.inspect()
        linked.unlink(); barrier.location().unlink()
        self.root.chmod(0o777)
        try:
            with self.assertRaises(ops.OperationError): barrier.inspect()
        finally: self.root.chmod(0o700)
