"""Synthetic collector state-machine and native boundary tests; no host actions."""
import copy
import datetime as dt
from pathlib import Path
import unittest
from unittest.mock import patch

from deploy.onedrive_retention import collector, native_restore as native
from deploy.tests import test_onedrive_native_restore as fixtures


class Fake:
    def __init__(self, failure=None):
        proof, receipt, owner, stop = fixtures.TerminalCollectorTests().evidence()
        self.observation={'owner':owner,'receipt':receipt,'snapshot':stop['unit_state']}
        self.stop=stop;self.stopped=proof['stopped_unit_state'];self.events=[];self.failure=failure
        self.instant=0;self.polls=0
    def event(self,name):
        self.events.append(name)
        if name==self.failure:raise RuntimeError('private details')
    def preflight(self):self.event('preflight')
    def begin(self,tx,attempt):self.event('begin')
    def clock(self):return self.instant
    def now(self):return dt.datetime.now(dt.timezone.utc)
    def pause(self):self.events.append('pause');self.instant+=1
    def observe_owner(self):
        self.event('observe');self.polls+=1
        return None if self.polls==1 else self.observation
    def stop_intent(self,value):self.event('stop_intent');return self.stop
    def stop_empty_owner(self,value):self.event('stop');
    def wait_stopped(self,value,seconds):self.event('wait_stopped');return self.stopped
    def commit(self,proof,value):self.event('commit')
    def finish(self,proof):self.event('finish')
    def failed(self):self.events.append('failed')


class CollectorTests(unittest.TestCase):
    def test_durable_intent_then_one_stop_then_validated_proof(self):
        fake=Fake();proof=collector.collect('c'*32,backend=fake)
        self.assertEqual(fake.events,['preflight','begin','observe','pause','observe','stop_intent',
            'stop','wait_stopped','finish','commit'])
        native.validate_terminal_proof(proof,fake.observation['receipt'],fake.observation['owner'],stop_intent=fake.stop)
    def test_lost_stop_ack_or_failed_unit_never_retries_or_commits(self):
        for phase in ('observe','stop_intent','stop','wait_stopped'):
            fake=Fake(phase)
            with self.subTest(phase=phase),self.assertRaisesRegex(collector.CollectorRefused,
                    '^native_collector_requires_reconciliation$'):
                collector.collect('c'*32,backend=fake)
            self.assertEqual(fake.events.count(phase),1);self.assertNotIn('commit',fake.events)
            self.assertEqual(fake.events[-1],'failed')
    def test_existing_collector_attempt_is_not_resumed_or_marked_by_other_owner(self):
        fake=Fake('begin')
        with self.assertRaises(collector.CollectorRefused):collector.collect('c'*32,backend=fake)
        self.assertEqual(fake.events,['preflight','begin'])
    def test_missing_installed_guard_sanitizes_failure_without_claiming_journal(self):
        fake=Fake('preflight')
        with self.assertRaisesRegex(collector.CollectorRefused,'^native_collector_requires_reconciliation$'):
            collector.collect('c'*32,backend=fake)
        self.assertEqual(fake.events,['preflight'])
    def test_wait_has_finite_budget_and_preserves_unfinished_journal(self):
        fake=Fake();fake.observe_owner=lambda:None
        fake.pause=lambda:setattr(fake,'instant',fake.instant+collector.WAIT_SECONDS)
        with self.assertRaises(collector.CollectorRefused):collector.collect('c'*32,backend=fake)
        self.assertNotIn('stop',fake.events);self.assertEqual(fake.events[-1],'failed')
    def test_attempts_have_separate_owner_collector_journal_and_proof_roots(self):
        first=collector.coordinates('c'*32,1);second=collector.coordinates('c'*32,2)
        self.assertTrue(str(second['attempt_root']).endswith('c'*32+'-02'))
        self.assertTrue(str(second['journal_root']).endswith('collector-02'))
        self.assertTrue(second['collector_unit'].endswith('-02.service'))
        for key in first:self.assertNotEqual(first[key],second[key])
        for attempt in (True,0,10,'2'):
            with self.assertRaises(collector.CollectorRefused):collector.coordinates('c'*32,attempt)
    def test_default_non_native_host_refuses_before_installed_entry_or_io(self):
        with patch.object(collector.sys,'platform','win32'),patch.object(collector.Path,'lstat') as read:
            with self.assertRaises(collector.CollectorRefused):collector.collect('c'*32)
            read.assert_not_called()
    def test_retained_success_requires_actual_loaded_metadata_and_no_live_owner(self):
        fake=Fake();state=dict(fake.observation['snapshot'],Type='exec',Restart='no',
            KillMode='control-group',SendSIGKILL='yes',TimeoutStartUSec='30s',
            TimeoutStopUSec='90s',RuntimeMaxUSec='2h',PrivateNetwork='yes',ExecStop='',ExecStopPost='')
        state.update(dict.fromkeys(native.OWNER_HOOK_FIELDS,''))
        unit=fake.stop['unit'];owner=fake.observation['owner']
        collector.retained_success(state,owner,unit)
        for change in ({'LoadState':'not-found'},{'MainPID':'123'},{'Result':'timeout'},
                       {'RemainAfterExit':'no'},{'KillMode':'process'},
                       *({field:'unknown hook'} for field in native.OWNER_HOOK_FIELDS)):
            with self.subTest(change=change),self.assertRaises((collector.CollectorRefused,native.NativeRefused)):
                collector.retained_success(dict(state,**change),owner,unit)
    def test_invocation_changed_before_stop_never_issues_stop(self):
        from contextlib import nullcontext
        fake=Fake();backend=collector.Native('c'*32)
        backend.installed=lambda:None
        changed=copy.deepcopy(fake.observation);changed['snapshot']['InvocationID']='f'*32
        backend.observe_owner=lambda:changed
        with patch.object(collector.c,'control',return_value=nullcontext()), \
                patch.object(collector.hydrate,'command') as command:
            with self.assertRaises(collector.CollectorRefused):backend.stop_empty_owner(fake.observation)
            command.assert_not_called()
    def test_live_pid_cannot_be_called_terminal_merely_because_session_changed(self):
        backend=collector.Native('c'*32)
        original=Fake().observation['owner'];backend.owner=original
        with patch.object(native,'process_identity',return_value=dict(original,process_group=999)):
            self.assertFalse(backend.terminal_identity(original))
        with patch.object(native,'process_identity',return_value=dict(original,start_ticks=999)):
            self.assertTrue(backend.terminal_identity(original))
    def test_wrong_transaction_or_attempt_evidence_refuses_before_stop(self):
        for change in ({'transaction':'f'*32},{'attempt':2}):
            fake=Fake();fake.observation['receipt'].update(change)
            with self.assertRaises(collector.CollectorRefused):collector.collect('c'*32,backend=fake)
            self.assertNotIn('stop',fake.events)
    def test_native_show_proves_empty_stop_hooks_through_typed_calls(self):
        backend=collector.Native('c'*32)
        values=dict(Fake().observation['snapshot'],Type='exec',Restart='no',KillMode='control-group',
            SendSIGKILL='yes',TimeoutStartUSec='30s',TimeoutStopUSec='90s',RuntimeMaxUSec='2h',
            PrivateNetwork='yes',ExecStop='',ExecStopPost='')
        values.update(dict.fromkeys(native.OWNER_HOOK_FIELDS,''))
        scalars={key:value for key,value in values.items() if key not in collector.common_io.HOOK_PROPERTIES}
        calls=[]
        def capture(args,**kwargs):
            calls.append(args)
            return (''.join(key+'='+value+'\n' for key,value in scalars.items()).encode()
                if args[0]=='/usr/bin/systemctl' else collector.common_io.EMPTY_EXEC_ARRAY)
        with patch.object(collector.hydrate,'command',side_effect=capture):
            self.assertEqual(backend.show(backend.owner_unit),values)
        self.assertEqual(len(calls),6)
        requested=calls[0][-1].removeprefix('--property=').split(',')
        self.assertFalse(set(requested)&set(native.OWNER_HOOK_FIELDS))
    def test_post_stop_gc_uses_only_scalars_not_missing_typed_hook_defaults(self):
        fake=Fake();backend=collector.Native('c'*32)
        backend.clock=lambda:0;backend.group_empty=lambda unit:True
        backend.evidence=lambda:(fake.observation['owner'],fake.observation['receipt'])
        values=dict(fake.stopped,Result='success',ExecMainStatus='0',InvocationID='')
        raw=''.join(key+'='+value+'\n' for key,value in values.items()).encode()
        with patch.object(collector.hydrate,'command',return_value=raw) as capture:
            self.assertEqual(backend.wait_stopped(fake.observation,120),fake.stopped)
        self.assertEqual(capture.call_count,1)
        self.assertEqual(capture.call_args.args[0][0],'/usr/bin/systemctl')
        requested=capture.call_args.args[0][-1].removeprefix('--property=').split(',')
        self.assertFalse(set(requested)&collector.common_io.HOOK_PROPERTIES)
