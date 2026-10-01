"""Offline host/transport contract tests; no SSH, database or private files."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import owner_recovery_reserve_host as host


class ViewTests(unittest.TestCase):
    def make(self):
        return host.Host(SimpleNamespace(), verify_sources=lambda:True,
            metadata_reader=lambda:{}, approval_reader=lambda:b'selected',
            primary_plan_reader=lambda:b'primary', library_pins={}, action='receipt',
            closure_sha256='a'*64, convergence=lambda context:context['selection'])
    def test_observer_binds_action_stage_and_exact_context(self):
        adapter=self.make();adapter.verify_sources=Mock(return_value=True)
        adapter.convergence=Mock(return_value=b'selected')
        metadata={'original':'enrollment'};activation={'version':1}
        adapter.observe('operation',b'selected',{'activation':activation},metadata,'pre')
        adapter.convergence.assert_called_once_with({'action':'receipt','stage':'pre',
            'operation':'operation','selection':b'selected','closure_sha256':'a'*64,
            'enrollment_sha256':hashlib.sha256(host.recovery.encoded(metadata)).hexdigest(),
            'activation_sha256':hashlib.sha256(host.ops.encoded(activation)).hexdigest()})
        self.assertEqual(adapter.verify_sources.call_count,2)
    def test_observer_failure_or_mismatch_has_no_retry(self):
        for failure in (False,True):
            adapter=self.make();adapter.convergence=Mock(return_value=b'other',
                side_effect=ValueError('private diagnostic') if failure else None)
            with self.assertRaises(ValueError):adapter.observe('op',b'selected',{'activation':{}},{},'pre')
            self.assertEqual(adapter.convergence.call_count,1)
    def test_missing_action_refused_before_observation(self):
        adapter=self.make();adapter.action=None;adapter.convergence=Mock()
        with self.assertRaises(ValueError):adapter.observe('op',b'selected',{'activation':{}},{},'pre')
        adapter.convergence.assert_not_called()
    def test_context_conversion_preserves_exact_utc_and_uuid(self):
        from test_owner_recovery_reserve import PolicyTests
        policy=PolicyTests();policy.setUp()
        row=dict(policy.old)
        for key in ('started','deadline'):row[key]=datetime.fromisoformat(row[key])
        self.assertEqual(host.context_from_row(row),policy.old)
    def test_supplied_submit_connection_is_reused_no_engine_or_host_lock(self):
        adapter=self.make();connection=object();view={'bridge':{},'lineage':{}}
        adapter._successor=Mock(return_value=(view,b'selected',{'synthetic':'files'}))
        with patch.object(host.handoff,'locks',side_effect=AssertionError('nested locks')),\
             patch.object(host.operator,'secure_connection',side_effect=AssertionError('new transaction')),\
             patch.object(host.plans,'require_current'),\
             patch.object(host.handoff,'read_files',return_value={'synthetic':'files'}),\
             patch.object(host.plans,'load',return_value=(b'selected',{'activation':{}})):
            with adapter.transaction_view(connection,'operation') as result:self.assertIs(result,view)
        adapter._successor.assert_called_once_with(connection,'operation')
    def test_db_plan_drift_after_view_refused(self):
        adapter=self.make();adapter._successor=Mock(return_value=({},b'selected',{}))
        adapter.convergence=Mock(return_value=b'other')
        with patch.object(host.plans,'require_current'),patch.object(host.handoff,'read_files',return_value={}),\
             patch.object(host.plans,'load',return_value=(b'selected',{'activation':{}})),self.assertRaises(ValueError):
            with adapter.transaction_view(object(),'operation'):pass


class AcknowledgementTests(unittest.TestCase):
    def test_no_evidence_requires_absent_journal_and_intake(self):
        connection=Mock()
        connection.execute.return_value.mappings.return_value.one.return_value={
            'evidence_digest':None,'verified_at':None,'evidence_expires':None}
        path=Mock();journal=Mock()
        for item in (path,journal):item.exists.return_value=False;item.is_symlink.return_value=False
        with patch.object(host.operator,'locations',return_value=(path,journal)):
            self.assertEqual(host.acknowledgement(connection,{'operation':'synthetic'},{}),'none')
            path.exists.return_value=True
            with patch.object(host.ops,'read_protected',side_effect=ValueError('invalid raw')):
                with self.assertRaises(ValueError):host.acknowledgement(connection,{'operation':'synthetic'},{})
    def test_prepared_journal_is_not_resolved_acknowledgement(self):
        connection=Mock();stamp=datetime.now(timezone.utc)
        connection.execute.return_value.mappings.return_value.one.return_value={
            'evidence_digest':'a'*64,'verified_at':stamp,'evidence_expires':stamp}
        path=Mock();journal=Mock();journal.exists.return_value=True
        with patch.object(host.operator,'locations',return_value=(path,journal)),\
             patch.object(host.operator,'journal',return_value={'phase':'prepared'}),self.assertRaises(ValueError):
            host.acknowledgement(connection,{'operation':'synthetic'},{})

    def test_expired_unsubmitted_packet_preserved_with_exact_optional_prepared_journal(self):
        from test_owner_recovery_reserve import PolicyTests, instant
        policy=PolicyTests();policy.setUp();context=policy.old;primary=policy.primary['receipt']
        packet={'version':2,'context':context,
            'holder':{'method':'independent_assisted_verification','completed_at':instant(110)},
            'target':{'method':'fresh_authentication_under_optional_mfa','completed_at':instant(120),
                'authentication_method':'google','email_verified':True,'applicable_factor_status':'none_enrolled'},
            'revocation':{'method':'all_source_and_target_provider_sessions','completed_at':instant(130)},
            'verified_at':instant(140),'expires_at':instant(410),
            'owner_approval':{**{key:context[key] for key in ('operation','account_id','epoch','target_binding','target_subject')},
                'configuration_verified':True,'authorized_at':instant(140)}}
        raw=host.ops.encoded(packet)
        connection=Mock();connection.scalar.return_value=True
        connection.execute.return_value.mappings.return_value.one.return_value={
            'evidence_digest':None,'verified_at':None,'evidence_expires':None}
        path=Mock();path.exists.return_value=True
        journal=Mock();journal.is_symlink.return_value=False
        import hashlib
        record={'phase':'prepared','digest':hashlib.sha256(raw).hexdigest(),
            'verified':packet['verified_at'],'expires':packet['expires_at']}
        with patch.object(host.operator,'locations',return_value=(path,journal)),\
             patch.object(host.operator,'fingerprint',return_value=('stable',)),\
             patch.object(host.ops,'read_protected',return_value=raw),\
             patch.object(host.operator,'journal',return_value=record):
            for present in (False,True):
                journal.exists.return_value=present
                self.assertEqual(host.acknowledgement(connection,context,primary),'expired_unsubmitted')
            path.unlink.assert_not_called();journal.unlink.assert_not_called()
            record['digest']='wrong'
            with self.assertRaises(ValueError):host.acknowledgement(connection,context,primary)
            record['digest']=hashlib.sha256(raw).hexdigest();connection.scalar.return_value=False
            with self.assertRaises(ValueError):host.acknowledgement(connection,context,primary)


if __name__=='__main__':unittest.main()
