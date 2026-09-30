"""Synthetic two-directory plan delivery, with no network or credentials."""
from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4
from unittest.mock import patch

import admission_operations as ops
import identity_plan_store as store
import identity_plan_transfer as transfer
from test_identity_plan_store import StoreTests


class TransferTests(StoreTests):
    def incoming(self):
        raw=self.outgoing(); destination=self.destination()
        path=destination/'identity-plan-transfer.incoming.json'
        ops.write_protected(path,raw)
        return path

    def terminal(self,answers):
        terminal=self.stack.enter_context(patch('identity_entry.SecureTerminal'))
        terminal.return_value.__enter__.return_value.ask.side_effect=answers
        return terminal

    def test_attended_import_rechecks_database_and_emits_only_fixed_result(self):
        self.incoming(); self.terminal(['VISIBLE',self.p,'SOURCE-VERIFIED','IMPORT'])
        output=io.StringIO()
        with patch.object(transfer,'db_guard') as guard, redirect_stdout(output):
            self.assertEqual(0,transfer.run('import',self.source()))
        self.assertEqual(2,guard.call_count)
        self.assertEqual({'outcome':'plan_imported_no_database_or_service_change'},ops.strict_json(output.getvalue()))
        self.assertEqual(self.value['switch'],store.load()[1])

    def test_cancelled_confirmation_cannot_publish_or_disclose_input(self):
        path=self.incoming(); original=path.read_bytes()
        self.terminal(['VISIBLE',self.p,InterruptedError('synthetic private input')])
        output=io.StringIO()
        with patch.object(transfer,'db_guard'), redirect_stdout(output):
            with self.assertRaises(ops.OperationError) as rejected: transfer.run('import',self.source())
        self.assertEqual(ops.ERROR,str(rejected.exception))
        self.assertEqual('',output.getvalue()); self.assertFalse(store.exists())
        self.assertEqual(original,path.read_bytes())

    def test_input_changed_during_confirmation_is_rejected_before_import(self):
        path=self.incoming(); original=path.read_bytes(); answers=iter(['VISIBLE',self.p,'SOURCE-VERIFIED','IMPORT'])
        def answer(prompt):
            result=next(answers)
            if result=='IMPORT': ops.write_protected(path,original+b' ',previous=original)
            return result
        self.terminal(answer)
        with patch.object(transfer,'db_guard'), patch.object(transfer,'install') as install:
            with self.assertRaises(ops.OperationError): transfer.run('import',self.source())
        install.assert_not_called(); self.assertFalse(store.exists())

    def test_database_drift_after_confirmation_cannot_publish(self):
        self.incoming(); self.terminal(['VISIBLE',self.p,'SOURCE-VERIFIED','IMPORT'])
        with patch.object(transfer,'db_guard',side_effect=[None,ops.OperationError(ops.ERROR)]):
            with self.assertRaises(ops.OperationError): transfer.run('import',self.source())
        self.assertFalse(store.exists())

    def test_db_import_dependency_closure_without_site_packages(self):
        result=subprocess.run([sys.executable,'-S','-B','-c',
            'import identity_plan_transfer; from identity_entry import SecureTerminal'],
            cwd=Path(__file__).resolve().parent,capture_output=True,timeout=15)
        self.assertEqual(0,result.returncode,'DB import path requires unavailable dependencies')
        self.assertEqual(b'',result.stdout)
        self.assertEqual(b'',result.stderr)

    def source(self):
        return Path('/opt/careertrellis/staging/releases')/('c'*40)/'backend/admission_operations.py'

    def outgoing(self):
        store.select(self.digest,self.p,previous=None)
        transfer.export(self.source())
        return (self.root/'identity-plan-transfer.outgoing.json').read_bytes()

    def destination(self):
        native=os.name=='posix' and os.geteuid()==0
        path=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/' if native else None)))
        self.stack.enter_context(patch.object(ops,'ROOT',path))
        self.stack.enter_context(patch.object(ops,'STATE',path))
        return path

    def test_nonroot_posix_destination_uses_writable_temporary_root(self):
        created=[]
        temporary_directory=tempfile.TemporaryDirectory
        def temporary(*,dir):
            created.append(dir)
            return temporary_directory(dir=dir)
        # Keep Path's platform selection unchanged; only this fixture's POSIX
        # privilege check is simulated, including on a Windows test runner.
        with patch(__name__+'.os') as operating_system, \
                patch.object(tempfile,'TemporaryDirectory',side_effect=temporary):
            operating_system.name='posix'
            operating_system.geteuid.return_value=1000
            destination=self.destination()
        self.assertEqual([None],created)
        self.assertEqual(destination,ops.ROOT)
        self.assertEqual(destination,ops.STATE)
        probe=destination/'synthetic-probe'
        probe.write_bytes(b'fixture writable')
        self.assertEqual(b'fixture writable',probe.read_bytes())

    def test_export_import_exact_retry_and_no_other_files(self):
        raw=self.outgoing(); destination=self.destination(); calls=[]
        result=transfer.install(raw,None,self.source(),lambda:calls.append('read-only guard'))
        self.assertEqual('plan_imported_no_database_or_service_change',result['outcome'])
        previous=store.load()[0]
        self.assertEqual('exact_plan_transfer_already_present',transfer.install(raw,previous,self.source(),lambda:None)['outcome'])
        self.assertEqual({'identity-selected-plan.json','identity-plan-'+self.digest+'.json'},set(p.name for p in destination.iterdir()))
        self.assertEqual(['read-only guard'],calls)

    def test_interrupted_archive_then_retry_publishes_selection_last(self):
        raw=self.outgoing(); self.destination(); write=ops.write_protected
        def interrupted(path,*args,**kwargs):
            write(path,*args,**kwargs)
            if path!=store.location(): raise OSError('synthetic interrupted transfer')
        with patch.object(ops,'write_protected',side_effect=interrupted):
            with self.assertRaises(OSError): transfer.install(raw,None,self.source(),lambda:None)
        self.assertFalse(store.exists())
        transfer.install(raw,None,self.source(),lambda:None)
        self.assertEqual(self.value['switch'],store.load()[1])

    def test_malformed_extra_payload_b_operation_and_stale_revision_refused(self):
        raw=self.outgoing(); value=ops.strict_json(raw)
        for mutate in (lambda v:v.update(credentials='synthetic'),
                lambda v:v['selection'].update(operation=self.b),
                lambda v:v['archives'][self.digest]['switch']['provider'].update(owner_proof='f'*64)):
            bad=deepcopy(value); mutate(bad)
            with self.assertRaises(Exception): transfer.validate(ops.encoded(bad))
        self.destination(); transfer.install(raw,None,self.source(),lambda:None)
        previous=store.load()[0]; stale=deepcopy(value); stale['selection']['sequence']=0
        with self.assertRaises(Exception): transfer.install(ops.encoded(stale),previous,self.source(),lambda:None)
        self.assertEqual(previous,store.load()[0])

    def test_failed_database_guard_prevents_import(self):
        raw=self.outgoing(); destination=self.destination()
        def denied(): raise ops.OperationError(ops.ERROR)
        with self.assertRaises(ops.OperationError): transfer.install(raw,None,self.source(),denied)
        self.assertFalse(list(destination.iterdir()))

    def test_successor_import_advances_same_p_and_refuses_old_transfer(self):
        raw=self.outgoing(); value=ops.strict_json(raw)
        successor=deepcopy(self.value)
        successor.update(version=2,predecessor=self.digest)
        successor['switch']['provider'].update(source_application='b'*40,expected_generation=3,transitions=[str(uuid4())])
        digest=hashlib.sha256(ops.encoded(successor)).hexdigest()
        updated=deepcopy(value); updated['archives'][digest]=successor
        updated['selection'].update(digest=digest,sequence=value['selection']['sequence']+1)
        self.destination(); transfer.install(raw,None,self.source(),lambda:None)
        previous=store.load()[0]
        transfer.install(ops.encoded(updated),previous,self.source(),lambda:None)
        current,pair=store.load()
        self.assertEqual(successor['switch'],pair)
        self.assertTrue(store.archive(self.digest).exists())
        with self.assertRaises(ops.OperationError): transfer.install(raw,current,self.source(),lambda:None)
