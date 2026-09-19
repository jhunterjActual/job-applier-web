"""Synthetic receipt production/delivery between isolated protected host roots."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import admission_operations as ops
import identity_plan_store as store
import identity_receipt_transfer as transfer
from test_identity_plan_store import StoreTests


class ReceiptTests(StoreTests):
    def source(self):
        return Path('/opt/careertrellis/staging/releases')/('c'*40)/'backend/admission_operations.py'

    def prepare(self,phase):
        snapshot=store.select(self.digest,self.p,previous=None)
        p=self.value['switch']['activation']
        source_name,destination_name,value=transfer.contract(p,phase)
        ops.write_protected(ops.STATE/source_name,ops.encoded(value))
        transfer.export(self.source(),transfer.DIRECTIONS[phase][0],phase)
        raw=transfer.transfer_file(phase,'outgoing').read_bytes()
        root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/' if os.name=='posix' else None)))
        self.stack.enter_context(patch.object(ops,'ROOT',root))
        self.stack.enter_context(patch.object(ops,'STATE',root))
        store.register(ops.encoded(self.value))
        store.select(self.digest,self.p,previous=None)
        ops.write_protected(transfer.transfer_file(phase,'incoming'),raw)
        return snapshot,raw,root/destination_name

    def deliver(self,phase,snapshot,raw):
        return transfer.install(self.source(),transfer.DIRECTIONS[phase][1],phase,raw,snapshot)

    def test_four_directions_map_exact_files_and_retry(self):
        # Each subcase has independent producer/consumer state.
        for phase in transfer.DIRECTIONS:
            with self.subTest(phase=phase):
                case=ReceiptTests(); case.setUp()
                try:
                    snapshot,raw,path=case.prepare(phase)
                    case.deliver(phase,snapshot,raw)
                    before=path.stat().st_mtime_ns
                    case.deliver(phase,snapshot,raw)
                    self.assertEqual(before,path.stat().st_mtime_ns)
                    self.assertEqual(transfer.contract(case.value['switch']['activation'],phase)[2],ops.strict_json(path.read_bytes()))
                    if phase=='quiesced': self.assertEqual('database-quiesced.json',path.name)
                finally: case.doCleanups()

    def test_wrong_envelope_and_direction_never_publish(self):
        snapshot,raw,path=self.prepare('armed'); value=ops.strict_json(raw)
        changes=[{'credentials':'synthetic'}, {'version':True}, {'phase':'enabled'}, {'operations':'d'*40},
            {'selection':{**value['selection'],'sequence':9}},
            {'receipt':{**value['receipt'],'phase':'prepared'}},
            {'receipt':{'plan':{**value['receipt']['plan'],'operation':self.b},'phase':'armed'}}]
        for change in changes:
            bad=ops.encoded({**deepcopy(value),**change})
            with self.assertRaises(ops.OperationError): transfer.validate(bad,snapshot,self.value['switch']['activation'],'armed')
        with self.assertRaises(ops.OperationError): transfer.install(self.source(),'db','armed',raw,snapshot)
        self.assertFalse(path.exists())

    def test_changed_selection_and_inbox_refused(self):
        snapshot,raw,path=self.prepare('armed')
        inbox=transfer.transfer_file('armed','incoming')
        ops.write_protected(inbox,raw+b' ',previous=raw)
        with self.assertRaises(ops.OperationError): self.deliver('armed',snapshot,raw)
        ops.write_protected(inbox,raw,previous=raw+b' ')
        store.select(self.digest,self.p,previous=snapshot)
        with self.assertRaises(ops.OperationError): self.deliver('armed',snapshot,raw)
        self.assertFalse(path.exists())

    def test_conflicting_destination_preserved(self):
        snapshot,raw,path=self.prepare('quiesced')
        ops.write_protected(path,b'{"historical":true}\n')
        with self.assertRaises(ops.OperationError): self.deliver('quiesced',snapshot,raw)
        self.assertEqual(b'{"historical":true}\n',path.read_bytes())

    def test_lost_publication_acknowledgement_exact_retry(self):
        snapshot,raw,path=self.prepare('enabled'); write=ops.write_protected
        with patch.object(ops,'write_protected',side_effect=OSError('before')):
            with self.assertRaises(OSError): self.deliver('enabled',snapshot,raw)
        self.assertFalse(path.exists())
        def lost(*args,**kwargs):
            write(*args,**kwargs); raise OSError('after')
        with patch.object(ops,'write_protected',side_effect=lost):
            with self.assertRaises(OSError): self.deliver('enabled',snapshot,raw)
        self.deliver('enabled',snapshot,raw)

    def test_attended_cancellation_and_changed_input(self):
        snapshot,raw,path=self.prepare('migrated')
        with patch('identity_entry.SecureTerminal') as terminal, redirect_stdout(io.StringIO()) as output:
            ask=terminal.return_value.__enter__.return_value.ask
            ask.side_effect=['VISIBLE',self.p,'migrated',InterruptedError('private synthetic')]
            with self.assertRaises(ops.OperationError) as rejected: transfer.run('import',self.source(),'db','migrated')
            self.assertEqual(ops.ERROR,str(rejected.exception)); self.assertFalse(path.exists())
            answers=iter(['VISIBLE',self.p,'migrated','SOURCE-VERIFIED','IMPORT'])
            def answer(prompt):
                value=next(answers)
                if value=='IMPORT': ops.write_protected(transfer.transfer_file('migrated','incoming'),raw+b' ',previous=raw)
                return value
            ask.side_effect=answer
            with self.assertRaises(ops.OperationError): transfer.run('import',self.source(),'db','migrated')
            self.assertFalse(path.exists()); self.assertEqual('',output.getvalue())

    def test_no_site_packages_required(self):
        result=subprocess.run([sys.executable,'-S','-B','-c','import identity_receipt_transfer; from identity_entry import SecureTerminal'],
            cwd=Path(__file__).resolve().parent,capture_output=True,timeout=15)
        self.assertEqual((0,b'',b''),(result.returncode,result.stdout,result.stderr))

    def test_native_unsafe_mode_owner_and_link_refused(self):
        if os.name!='posix' or os.geteuid()!=0: self.skipTest('root Linux protected I/O')
        snapshot,raw,path=self.prepare('armed'); inbox=transfer.transfer_file('armed','incoming')
        inbox.chmod(0o644)
        with self.assertRaises(ops.OperationError): self.deliver('armed',snapshot,raw)
        inbox.chmod(0o600); os.chown(inbox,12345,12345)
        try:
            with self.assertRaises(ops.OperationError): self.deliver('armed',snapshot,raw)
        finally: os.chown(inbox,0,0)
        path.symlink_to(inbox)
        with self.assertRaises(OSError): self.deliver('armed',snapshot,raw)

    def test_native_abrupt_link_publication_remains_fail_closed(self):
        if os.name!='posix' or os.geteuid()!=0: self.skipTest('root Linux protected I/O')
        snapshot,raw,path=self.prepare('enabled')
        receipt=transfer.validate(raw,snapshot,self.value['switch']['activation'],'enabled')
        temporary=path.parent/'synthetic-interrupted-publication'
        ops.write_protected(temporary,receipt)
        os.link(temporary,path)
        with self.assertRaises(ops.OperationError): self.deliver('enabled',snapshot,raw)
        self.assertEqual(2,path.stat().st_nlink)
        self.assertEqual(receipt,path.read_bytes())
