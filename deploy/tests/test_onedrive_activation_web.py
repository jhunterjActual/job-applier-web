"""Synthetic lifecycle ordering only; no host, provider or native acceptance."""
from contextlib import contextmanager,nullcontext,ExitStack
import copy
import datetime as dt
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from deploy.onedrive_retention import activation_web as w

TX='a'*32
OTHER='b'*32
CONTEXT=SimpleNamespace(revision='c'*40,document={'application_release':'d'*40},bindings={'standalone_policy_sha256':'e'*64})
OWNER={'owner':{'pid':123,'start_ticks':456,'boot_id':'01234567-89ab-cdef-0123-456789abcdef'},'invocation':'f'*32,'unit':w.actor._unit(TX)}
REVIEW={'intent_sha256':'1'*64,'review_sha256':{},'prepare_deadline':(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=10)).isoformat()}


class Tests(unittest.TestCase):
    def closed_history(self):
        now=dt.datetime.now(dt.timezone.utc)
        dispatch={'schema':'careertrellis.retention-activation-web-dispatch/v1','transaction':TX,
            'baseline_sha256':w.t.digest(CONTEXT.document),'baseline_bindings':CONTEXT.bindings,'source_sha256':'1'*64,
            'requester_owner':{'pid':12,'start_ticks':111,'boot_id':OWNER['owner']['boot_id']},'review':REVIEW,
            'unit':w.actor._unit(TX),'command':w._command(TX),'issued_at':(now-dt.timedelta(minutes=3)).isoformat()}
        owner={**copy.deepcopy(OWNER),'schema':'careertrellis.retention-activation-web-owner/v1','transaction':TX,
            'dispatch_sha256':w.t.digest(dispatch),'baseline_sha256':dispatch['baseline_sha256'],'source_sha256':'1'*64}
        terminal={'schema':'careertrellis.retention-activation-web-terminal/v1','transaction':TX,
            'owner_sha256':w.t.digest(owner),'dispatch_sha256':w.t.digest(dispatch),'result':'finalized',
            'finalized_sha256':'2'*64,'db_ack_sha256':'3'*64,'completed_at':(now-dt.timedelta(minutes=2)).isoformat()}
        state={'ActiveState':'active','SubState':'exited','Type':'exec','User':'root','Group':'root',
            'MainPID':'0','ControlPID':'0','InvocationID':owner['invocation'],'ExecMainPID':str(owner['owner']['pid']),
            'Restart':'no','RemainAfterExit':'yes','RuntimeMaxUSec':'30min','TimeoutStartUSec':'30s','TimeoutStopUSec':'30s',
            'KillMode':'control-group','SendSIGKILL':'yes','PrivateNetwork':'no','UMask':'0077','Result':'success',
            'ExecMainCode':'1','ExecMainStatus':'0','Slice':'system.slice','LoadState':'loaded','Job':'','ControlGroup':''}
        closure={'schema':'careertrellis.retention-activation-web-unit-closure/v1','transaction':TX,
            'owner_sha256':w.t.digest(owner),'terminal_sha256':w.t.digest(terminal),'unit':w.actor._unit(TX),
            'invocation':owner['invocation'],'actual_terminal':{'unit_state':state,
                'cgroup':{'path':'/system.slice/'+w.actor._unit(TX),'present':False,'directories':0,'empty':True}},
            'observed_at':(now-dt.timedelta(minutes=1)).isoformat(),
            'observer_owner':{'pid':222,'start_ticks':999,'boot_id':owner['owner']['boot_id']}}
        return {'dispatch.json':dispatch,'owner.json':owner,'terminal.json':terminal,'unit-closure.json':closure}

    def test_previous_boot_requires_saved_success_and_exact_absent_empty_unit(self):
        original=self.closed_history()
        absent=b'LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\nJob=\nControlGroup=\nInvocationID=\n'
        empty={'path':'/system.slice/'+w.actor._unit(TX),'present':False,'directories':0,'empty':True}
        records=copy.deepcopy(original)
        def read(path):
            if path.name not in records:raise FileNotFoundError(2,'missing immutable proof')
            return records[path.name]
        with patch.object(w,'_context',return_value=CONTEXT),patch.object(w,'_same'),patch.object(w,'_root'),\
             patch.object(w.os,'listdir',side_effect=lambda path:list(records)),patch.object(w,'_private',side_effect=read),\
             patch.object(w,'_source',return_value='1'*64),\
             patch.object(Path,'read_text',return_value='fedcba98-7654-3210-fedc-ba9876543210'),\
             patch.object(w.preservation,'_terminal_birth',return_value='previous_boot_terminal') as birth,\
             patch.object(w.hydrate,'command',return_value=absent) as query,\
             patch.object(w.actor,'_empty_cgroup',return_value=empty) as group,\
             patch.object(w.actor,'_terminal_state') as same_boot,patch.object(w.t,'_exclusive_marker') as write:
            self.assertTrue(w.history_complete(TX));same_boot.assert_not_called();write.assert_not_called()
            for mutate in (lambda d:d.pop('unit-closure.json'),
                           lambda d:d['unit-closure.json'].update(terminal_sha256='9'*64),
                           lambda d:d['unit-closure.json'].update(observed_at=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1)).isoformat()),
                           lambda d:d['unit-closure.json']['actual_terminal']['cgroup'].update(empty=False),
                           lambda d:d['unit-closure.json']['actual_terminal']['unit_state'].update(ExecMainStatus='1'),
                           lambda d:d['unit-closure.json']['actual_terminal']['unit_state'].update(RuntimeMaxUSec='infinity'),
                           lambda d:d['unit-closure.json']['observer_owner'].update(boot_id='fedcba98-7654-3210-fedc-ba9876543210')):
                records=copy.deepcopy(original);mutate(records)
                with self.subTest(mutation=mutate):self.assertFalse(w.history_complete(TX))
            records=copy.deepcopy(original)
            query.return_value=absent.replace(b'LoadState=not-found',b'LoadState=loaded')
            self.assertFalse(w.history_complete(TX))
            query.return_value=absent;group.return_value={**empty,'empty':False}
            self.assertFalse(w.history_complete(TX))
            group.return_value=empty;birth.side_effect=ValueError('original birth not terminal')
            self.assertFalse(w.history_complete(TX));write.assert_not_called()

    def test_same_boot_still_requires_actual_original_terminal_state(self):
        records=self.closed_history();actual=records['unit-closure.json']['actual_terminal']
        with patch.object(w,'_context',return_value=CONTEXT),patch.object(w,'_same'),patch.object(w,'_root'),\
             patch.object(w.os,'listdir',return_value=list(records)),\
             patch.object(w,'_private',side_effect=lambda path:records[path.name]),patch.object(w,'_source',return_value='1'*64),\
             patch.object(Path,'read_text',return_value=OWNER['owner']['boot_id']),\
             patch.object(w.actor,'_terminal_state',return_value=actual) as terminal,\
             patch.object(w.hydrate,'command') as query:
            self.assertTrue(w.history_complete(TX));query.assert_not_called()
            terminal.side_effect=ValueError('original process remains alive')
            self.assertFalse(w.history_complete(TX));query.assert_not_called()

    def test_fixed_review_packet_requires_all_five_approvals_without_issuing_any(self):
        from deploy.tests.test_onedrive_activation_transition import fixture,NOW
        context,evidence,installed,packet=fixture()
        intent=w.t.proposal(context,evidence,installed,packet,NOW)['intent']
        root=w.t.REVIEW_ROOT/TX;intent['transaction']=TX
        deadline=(NOW+dt.timedelta(minutes=10)).isoformat();peer='9'*64
        documents={}
        for name in w.a.REVIEWS:
            documents[root/(name+'.json')]={'schema':w.t.REVIEW_DOCUMENT,
                'subject_sha256':w.t.digest({key:value for key,value in intent.items() if key!='reviews'}),
                'roles':['db','web'],'baseline_sha256':{'web':w.t.digest(context.document),'db':peer},
                'result':'approved','reviewed_at':NOW.isoformat(),'prepare_deadline':deadline,
                'active_valid_until':intent['valid_until']}
        intent['reviews']={name:w.t.digest(documents[root/(name+'.json')]) for name in w.a.REVIEWS}
        local={'schema':w.t.LOCAL_REVIEW,'role':'web','transaction':TX,'baseline_sha256':w.t.digest(context.document),
            'baseline_bindings':context.bindings,'installation_receipts':installed,'intent_sha256':w.t.digest(intent),
            'prepare_deadline':deadline,'reviewed_at':NOW.isoformat(),'documents':intent['reviews'],
            'peer_baseline_sha256':peer}
        documents.update({root/'review.json':local,root/'intent.json':intent})
        def read(path):
            if path not in documents:raise FileNotFoundError(2,'missing protected approval')
            return documents[path]
        with patch.object(w,'_private',side_effect=read),patch.object(w,'_now',return_value=NOW+dt.timedelta(minutes=1)),\
             patch.object(w.t,'_exclusive_marker') as write,patch.object(w.hydrate,'command') as command:
            self.assertEqual(w._reviews(context,TX)['intent_sha256'],w.t.digest(intent))
            documents[root/'source_review.json']['result']='claimed'
            with self.assertRaises(ValueError):w._reviews(context,TX)
            documents.pop(root/'source_review.json')
            with self.assertRaises(FileNotFoundError):w._reviews(context,TX)
            write.assert_not_called();command.assert_not_called()

    def test_source_capability_does_not_authenticate_checkout_before_any_call(self):
        self.assertTrue(w.native_routes_available())
        with patch.object(w.hydrate,'command') as command,patch.object(w.t,'_exclusive_marker') as write:
            for function in (w.dispatch,w.run,w.status,w.verify_terminal):
                with self.assertRaises(ValueError):function(TX)
            command.assert_not_called();write.assert_not_called()

    def test_wrong_finite_owner_command_cgroup_or_invocation_refuses(self):
        state={'ActiveState':'active','SubState':'running','MainPID':'123','ControlPID':'0','InvocationID':'f'*32}
        with patch.object(w.actor,'_unit_state',return_value=state),patch.object(w.actor,'_birth',return_value=OWNER['owner']),\
             patch.object(w.os,'getpid',return_value=123),\
             patch.object(Path,'read_bytes',return_value=b'\0'.join(part.encode() for part in w._command(TX))+b'\0') as command,\
             patch.object(Path,'read_text',return_value='0::/system.slice/'+w.actor._unit(TX)) as cgroup:
            self.assertEqual(w._finite(TX),OWNER)
            with self.assertRaises(ValueError):w._finite(TX,{**OWNER,'invocation':'9'*32})
            state['MainPID']='321'
            with self.assertRaises(ValueError):w._finite(TX)
            state['MainPID']='123';command.return_value=b'other\0'
            with self.assertRaises(ValueError):w._finite(TX)
            command.return_value=b'\0'.join(part.encode() for part in w._command(TX))+b'\0';cgroup.return_value='0::/other'
            with self.assertRaises(ValueError):w._finite(TX)

    def test_prior_partial_history_or_same_transaction_prevents_new_launch(self):
        with patch.object(w,'_context',return_value=CONTEXT),patch.object(w,'_unselected'),\
             patch.object(w,'_holder',return_value=SimpleNamespace(locked=lambda:nullcontext())),patch.object(w,'_held'),patch.object(w,'_same'),\
             patch.object(w,'_reviews',return_value=REVIEW),patch.object(w,'_root',return_value=[OTHER]) as roots,\
             patch.object(w,'history_complete',return_value=False),patch.object(w.hydrate,'command') as launch,\
             patch.object(w.t,'_exclusive_marker') as write:
            with self.assertRaises(ValueError):w.dispatch(TX)
            roots.return_value=[TX]
            with self.assertRaises(ValueError):w.dispatch(TX)
            launch.assert_not_called();write.assert_not_called()

    def test_ambiguous_launch_records_claim_and_unknown_never_replays(self):
        names=[];writes={}
        @contextmanager
        def folder(path):yield 7
        def mkdir(name,*args,**kwargs):names.append(name)
        def exclusive(path,value,**kwargs):
            if path in writes:raise FileExistsError()
            writes[path]=value
        def command(args,**kwargs):
            if args[0]=='/usr/bin/systemctl':return b'LoadState=not-found\nActiveState=inactive\nMainPID=0\nControlPID=0\n'
            raise TimeoutError()
        with patch.object(w,'_context',return_value=CONTEXT),patch.object(w,'_unselected'),patch.object(w,'_same'),\
             patch.object(w,'_holder',return_value=SimpleNamespace(locked=lambda:nullcontext())),patch.object(w,'_held'),\
             patch.object(w,'_reviews',return_value=REVIEW),patch.object(w,'_root',side_effect=lambda:list(names)),\
             patch.object(w,'_source',return_value='1'*64),patch.object(w.actor,'_birth',return_value=OWNER['owner']),\
             patch.object(w.bundle,'_directory',folder),patch.object(w.os,'mkdir',side_effect=mkdir),patch.object(w.os,'fsync'),\
             patch.object(w.t,'_exclusive_marker',side_effect=exclusive),patch.object(w.hydrate,'command',side_effect=command) as launch:
            with self.assertRaises(ValueError):w.dispatch(TX)
            with self.assertRaises(ValueError):w.dispatch(TX)
        self.assertEqual({path.name for path in writes},{'dispatch.json','unknown.json'})
        self.assertEqual(sum(call.args[0][0]=='/usr/bin/systemd-run' for call in launch.call_args_list),1)

    def test_two_transactions_cannot_interleave_census_and_claim_or_launch_gap(self):
        held=[False];names=[];writes={};events=[]
        @contextmanager
        def locked():
            if held[0]:raise ValueError('canonical fence busy')
            held[0]=True;events.append('lock')
            try:yield
            finally:held[0]=False;events.append('release')
        @contextmanager
        def folder(path):yield 7
        def mkdir(name,*args,**kwargs):
            self.assertTrue(held[0]);names.append(name);events.append('mkdir-'+name)
        def claim(path,value,**kwargs):
            self.assertTrue(held[0]);self.assertNotIn(path,writes);writes[path]=value
            events.append('claim-'+value['transaction'])
            # Caller B reaches admission while A still owns both canonical locks.
            with self.assertRaises(ValueError):w.dispatch(OTHER)
            events.append('busy-second-caller')
        def history(name):
            self.assertTrue(held[0]);events.append('unresolved-'+name);return False
        def command(args,**kwargs):
            if args[0]=='/usr/bin/systemctl':
                self.assertTrue(held[0]);return b'LoadState=not-found\nActiveState=inactive\nMainPID=0\nControlPID=0\n'
            self.assertFalse(held[0]);events.append('launch-'+TX)
            # After release, but before A's unit starts, B must see A's durable
            # unresolved claim instead of treating the namespace as idle.
            with self.assertRaises(ValueError):w.dispatch(OTHER)
            return b''
        with patch.object(w,'_context',return_value=CONTEXT),patch.object(w,'_unselected'),patch.object(w,'_same'),\
             patch.object(w,'_holder',return_value=SimpleNamespace(locked=locked)),\
             patch.object(w,'_held',side_effect=lambda:self.assertTrue(held[0])),\
             patch.object(w,'_reviews',return_value=REVIEW),patch.object(w,'_root',side_effect=lambda:list(names)),\
             patch.object(w,'history_complete',side_effect=history),patch.object(w,'_source',return_value='1'*64),\
             patch.object(w.actor,'_birth',return_value=OWNER['owner']),patch.object(w.bundle,'_directory',folder),\
             patch.object(w.os,'mkdir',side_effect=mkdir),patch.object(w.os,'fsync'),\
             patch.object(w.t,'_exclusive_marker',side_effect=claim),patch.object(w.hydrate,'command',side_effect=command):
            self.assertEqual(w.dispatch(TX)['state'],'dispatched')
        self.assertEqual(names,[TX]);self.assertEqual(len(writes),1)
        self.assertLess(events.index('claim-'+TX),events.index('release'))
        self.assertLess(events.index('release'),events.index('launch-'+TX))
        self.assertIn('busy-second-caller',events);self.assertIn('unresolved-'+TX,events)

    def test_same_process_orders_actual_controller_phases_and_one_finalization(self):
        events=[];writes={};fail=[False];finalized={'actual':'local protected finalization fixture'}
        dispatch={'schema':'careertrellis.retention-activation-web-dispatch/v1','transaction':TX,
            'baseline_sha256':w.t.digest(CONTEXT.document),'baseline_bindings':CONTEXT.bindings,'source_sha256':'1'*64,
            'review':REVIEW,'unit':w.actor._unit(TX),'command':w._command(TX)}
        class Controller:
            def __getattr__(self,name):return lambda *args:events.append((name,*args))
        @contextmanager
        def locked():
            events.append(('locked',))
            try:yield
            finally:events.append(('unlocked',))
        holder=SimpleNamespace(locked=locked)
        def finish(tx):
            events.append(('finalize',tx))
            if fail[0]:raise TimeoutError('acknowledgement unavailable')
            return {'finalized_sha256':w.t.digest(finalized),'db_ack_sha256':'2'*64}
        def exclusive(path,value,**kwargs):
            if path in writes:raise FileExistsError()
            writes[path]=value
        def private(path):return finalized if path==w.a.FINALIZED else dispatch
        with ExitStack() as stack:
            stack.enter_context(patch.object(w,'_context',return_value=CONTEXT))
            roots=stack.enter_context(patch.object(w,'_root',return_value=[TX]))
            stack.enter_context(patch.object(w,'_unselected'))
            stack.enter_context(patch.object(w,'_reviews',return_value=REVIEW))
            stack.enter_context(patch.object(w,'_source',return_value='1'*64))
            stack.enter_context(patch.object(w,'_private',side_effect=private))
            stack.enter_context(patch.object(w.os,'listdir',return_value=['dispatch.json']))
            stack.enter_context(patch.object(w,'_finite',side_effect=lambda *args:dict(OWNER)))
            stack.enter_context(patch.object(w,'_held'))
            stack.enter_context(patch.object(w,'_same'))
            stack.enter_context(patch.object(w.t,'_exclusive_marker',side_effect=exclusive))
            stack.enter_context(patch.object(w.coordinator,'Policy'))
            stack.enter_context(patch.object(w.coordinator,'read_private'))
            stack.enter_context(patch.object(w.coordinator,'native_policy_binding'))
            stack.enter_context(patch.object(w.coordinator,'NativeWEB',return_value=holder))
            stack.enter_context(patch.object(w.hydrate,'protected',return_value=(b'',None)))
            stack.enter_context(patch.object(w.controller,'native',return_value=Controller()))
            stack.enter_context(patch.object(w,'_wait_db',side_effect=lambda *args:events.append(('wait_terminal',))))
            send=stack.enter_context(patch.object(w.finalizer,'send',side_effect=finish))
            result=w.run(TX)
            successful_events=list(events);successful_writes=set(writes)
            with self.assertRaises(FileExistsError):w.run(TX)
            send.assert_called_once_with(TX)
            # Separate disposable I/O fixture for an uncertain finalizer ACK.
            writes.clear();events.clear();fail[0]=True
            with self.assertRaises(ValueError):w.run(TX)
            self.assertEqual({path.name for path in writes},{'owner.json','unknown.json'})
            self.assertEqual(send.call_count,2)  # One call in each isolated fixture.
            with self.assertRaises(FileExistsError):w.run(TX)
            self.assertEqual(send.call_count,2)
            # Another retained unresolved claim must be checked again under
            # the runner's fences, before creating any protocol phase.
            writes.clear();events.clear();fail[0]=False;roots.return_value=[TX,OTHER]
            with patch.object(w,'history_complete',return_value=False) as history:
                with self.assertRaises(ValueError):w.run(TX)
                history.assert_called_once_with(OTHER)
            self.assertEqual(events,[('locked',),('unlocked',)])
            self.assertEqual(send.call_count,2)
            self.assertEqual({path.name for path in writes},{'owner.json','unknown.json'})
        self.assertEqual(successful_events,[('locked',),('publish_pending',),('prepare_once','db'),('prepare_once','web'),
            ('produce_commit_authorities',),('transfer_commit_material',),('commit_once','db'),('commit_once','web'),
            ('reconcile','db'),('reconcile','web'),('terminal_evidence',),('wait_terminal',),('finalize',TX),('unlocked',)])
        self.assertEqual(result['result'],'finalized')
        self.assertEqual({path.name for path in successful_writes},{'owner.json','terminal.json'})

    def test_status_and_failed_history_are_read_only(self):
        with patch.object(w,'_context',return_value=CONTEXT),patch.object(w,'_root'),\
             patch.object(w.os,'listdir',return_value=['dispatch.json','unknown.json']),\
             patch.object(w.t,'_exclusive_marker') as write,patch.object(w.hydrate,'command') as command,patch.object(w,'_held') as held:
            self.assertEqual(w.status(TX)['state'],'operator_required')
            self.assertFalse(w.history_complete(TX))
            write.assert_not_called();command.assert_not_called();held.assert_not_called()

    def test_current_exception_requires_finite_owner_and_fences_not_an_old_stage(self):
        dispatch={'transaction':TX,'source_sha256':'1'*64,'baseline_sha256':w.t.digest(CONTEXT.document),
            'baseline_bindings':CONTEXT.bindings,'command':w._command(TX)}
        owner={**OWNER,'transaction':TX,'source_sha256':'1'*64,'baseline_sha256':w.t.digest(CONTEXT.document),
            'dispatch_sha256':w.t.digest(dispatch)}
        with patch.object(w,'_context',return_value=CONTEXT),patch.object(w,'_root'),patch.object(w,'_source',return_value='1'*64),\
             patch.object(w.os,'listdir',return_value=['dispatch.json','owner.json']),\
             patch.object(w,'_private',side_effect=lambda path:dispatch if path.name=='dispatch.json' else owner),\
             patch.object(w,'_finite') as finite,patch.object(w,'_held') as held,patch.object(w.t,'_exclusive_marker') as write:
            self.assertFalse(w.history_complete(TX,current_transaction=OTHER))
            finite.assert_not_called();held.assert_not_called()
            self.assertTrue(w.history_complete(TX,current_transaction=TX))
            finite.assert_called_once_with(TX,owner);held.assert_called_once_with()
            finite.side_effect=ValueError()
            self.assertFalse(w.history_complete(TX,current_transaction=TX))
            write.assert_not_called()


if __name__=='__main__':unittest.main()
