"""Synthetic atomic selection rehearsal; root Linux exercises real protected I/O."""
from contextlib import ExitStack, nullcontext
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import patch

import admission_operations as ops
import identity_plan_store as store
import session_v2_transition as transition
from test_identity_plan_packet import fixture


class StoreTests(unittest.TestCase):
    def test_arm_baseline_uses_selected_bind_generation_and_rejects_other_activation(self):
        value=fixture()
        value['bind']['provider']['expected_generation']=6
        value['switch']['provider']['expected_generation']=7+len(value['switch']['provider']['transitions'])
        digest=store.register(ops.encoded(value))
        store.select(digest,value['switch']['provider']['operation'],previous=None)
        activation=value['switch']['activation']
        self.assertEqual(6,transition.arm_baseline(activation))
        with self.assertRaises(ops.OperationError): transition.arm_baseline(value['bind']['activation'])
        with self.assertRaises(ops.OperationError): transition.arm_baseline({**activation,'realm':'different'})

    def test_maintenance_baseline_allows_only_exact_bound_generation(self):
        transition.require_maintenance_baseline({**ops.INITIAL_POLICY,'generation':6},6)
        for value,expected in ((0,6),(6,0),(True,True),(-1,-1),(2**63,2**63)):
            with self.assertRaises(ops.OperationError):
                transition.require_maintenance_baseline({**ops.INITIAL_POLICY,'generation':value},expected)
        for field,changed in (('phase','beta'),('cap',25),('minimum_session_version',2),('signup_open',True),('maintenance',True)):
            with self.assertRaises(ops.OperationError):
                transition.require_maintenance_baseline({**ops.INITIAL_POLICY,'generation':6,field:changed},6)

    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        native=os.name=='posix' and os.geteuid()==0
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/' if native else None)))
        self.stack.enter_context(patch.object(ops,'ROOT',self.root))
        self.stack.enter_context(patch.object(ops,'STATE',self.root))
        # Native tests never contend with or create the host's real locks.
        self.lock_root=self.root/'locks'; self.lock_root.mkdir(mode=0o700)
        self.stack.enter_context(patch.object(ops,'LOCK_ROOT',self.lock_root))
        self.real_locks=ops.host_locks
        self.stack.enter_context(patch.object(ops,'host_locks',side_effect=nullcontext))
        if not native:
            self.stack.enter_context(patch.object(ops,'protected_parents'))
            self.stack.enter_context(patch.object(ops,'read_protected',side_effect=lambda p,**k:p.read_bytes()))
            self.stack.enter_context(patch.object(ops,'write_protected',side_effect=self.write))
        self.value=fixture(); self.digest=store.register(ops.encoded(self.value))
        self.b=self.value['bind']['provider']['operation']; self.p=self.value['switch']['provider']['operation']

    def write(self,path,raw,*,previous=None,**kwargs):
        if previous is None: self.assertFalse(path.exists())
        else: self.assertEqual(previous,path.read_bytes())
        path.write_bytes(raw)

    def test_archive_retry_does_not_rewrite_and_selected_pair_is_complete(self):
        original=store.archive(self.digest).stat().st_mtime_ns
        self.assertEqual(self.digest,store.register(ops.encoded(self.value)))
        self.assertEqual(original,store.archive(self.digest).stat().st_mtime_ns)
        raw=store.select(self.digest,self.b,previous=None)
        self.assertEqual((raw,self.value['bind']),store.load())
        with self.assertRaises(ops.OperationError): transition.plan()
        raw=store.select(self.digest,self.p,previous=raw)
        self.assertEqual(self.value['switch']['activation'],transition.plan())

    def test_stale_snapshot_and_aba_cannot_revalidate(self):
        original=store.select(self.digest,self.b,previous=None)
        next_raw=store.select(self.digest,self.p,previous=original)
        final=store.select(self.digest,self.b,previous=next_raw)
        self.assertNotEqual(original,final)
        with self.assertRaises(ops.OperationError): store.require_current(original)
        with self.assertRaises(ops.OperationError): store.select(self.digest,self.p,previous=original)
        store.require_current(final)

    def test_lost_ack_before_and_after_atomic_write(self):
        old=store.select(self.digest,self.b,previous=None)
        original=ops.write_protected
        with patch.object(ops,'write_protected',side_effect=OSError('synthetic before write')):
            with self.assertRaises(ops.OperationError): store.select(self.digest,self.p,previous=old)
        self.assertEqual(old,store.load()[0])
        def lost(*args,**kwargs):
            original(*args,**kwargs)
            raise OSError('synthetic lost acknowledgement')
        with patch.object(ops,'write_protected',side_effect=lost):
            with self.assertRaises(ops.OperationError): store.select(self.digest,self.p,previous=old)
        current,pair=store.load()
        self.assertEqual(self.value['switch'],pair)
        self.assertNotEqual(old,current)
        with self.assertRaises(ops.OperationError): store.select(self.digest,self.p,previous=old)

    def test_missing_corrupt_or_legacy_selection_never_falls_back(self):
        for name in ('provider-transition.json','session-v2-transition.json'):
            ops.write_protected(self.root/name,ops.encoded(self.value['switch']['activation']))
        with self.assertRaises(ops.OperationError): transition.plan()
        with self.assertRaises(ops.OperationError): store.select(self.digest,self.p,previous=None)
        for name in ('provider-transition.json','session-v2-transition.json'): (self.root/name).unlink()
        raw=store.select(self.digest,self.p,previous=None)
        ops.write_protected(store.location(),b'{}',previous=raw)
        with self.assertRaises(ops.OperationError): transition.plan()

    def test_archive_tamper_and_cross_packet_switch_rejected(self):
        raw=store.select(self.digest,self.b,previous=None)
        another=store.register(ops.encoded(fixture()))
        with self.assertRaises(ops.OperationError): store.select(another,self.p,previous=raw)
        path=store.archive(self.digest)
        ops.write_protected(path,b'{}',previous=path.read_bytes())
        with self.assertRaises(ops.OperationError): store.load()

    def test_successor_selection_requires_original_immutable_lineage(self):
        previous=store.select(self.digest,self.p,previous=None)
        value=deepcopy(self.value); value.update(version=2,predecessor=self.digest)
        value['switch']['provider'].update(source_application='b'*40,expected_generation=3,transitions=[str(uuid4())])
        raw=ops.encoded(value); digest=hashlib.sha256(raw).hexdigest()
        with self.assertRaises(ops.OperationError): store.register(raw)
        ops.write_protected(store.archive(digest),raw)
        with self.assertRaises(ops.OperationError): store.select(digest,self.p,previous=previous)
        # Only the separately guarded supersession caller performs this write.
        selected=ops.encoded({'version':1,'digest':digest,'operation':self.p,'sequence':2})
        ops.write_protected(store.location(),selected,previous=previous)
        self.assertEqual(value['switch'],store.load()[1])
        self.assertEqual(self.value['bind']['provider']['expected_generation'],
            transition.arm_baseline(value['switch']['activation']))
        store.archive(self.digest).unlink()
        with self.assertRaises(ops.OperationError): store.load()
        with self.assertRaises(ops.OperationError): transition.arm_baseline(value['switch']['activation'])

    @unittest.skipUnless(os.name=='posix','native protected filesystem')
    def test_selection_refuses_overlapping_host_operation(self):
        if os.geteuid()!=0: self.skipTest('root fixture required')
        with patch.object(ops,'host_locks',self.real_locks), self.real_locks():
            with self.assertRaises(ops.OperationError): store.select(self.digest,self.b,previous=None)
        self.assertFalse(store.exists())

    @unittest.skipUnless(os.name=='posix','native protected filesystem')
    def test_unsafe_mode_and_symlink_rejected(self):
        if os.geteuid()!=0: self.skipTest('root fixture required')
        raw=store.select(self.digest,self.b,previous=None)
        store.location().chmod(0o644)
        with self.assertRaises(ops.OperationError): store.load()
        store.location().chmod(0o600)
        store.location().unlink()
        store.location().symlink_to(store.archive(self.digest))
        with self.assertRaises(ops.OperationError): store.load()
