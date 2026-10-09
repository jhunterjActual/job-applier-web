"""Isolated exact-byte handoff faults; no host units, database, or HTTP calls."""
from contextlib import ExitStack, nullcontext, redirect_stdout
import io
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import admission_operations as ops
import admission_purge as purge
import admission_reporter as reporter
import monitoring_rebind as handoff
import totp_cleanup_operations as cleanup

OLD='a'*40
NEW='b'*40
NOW=360300


class RebindTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/' if os.name=='posix' else None)))
        for name in ('admission','cleanup','units','config'):
            (self.root/name).mkdir(mode=0o700)
        self.stack.enter_context(patch.object(purge,'STATE',self.root/'admission'))
        self.stack.enter_context(patch.object(cleanup,'STATE',self.root/'cleanup'))
        self.stack.enter_context(patch.object(reporter,'CONFIG',self.root/'config/reporter.json'))
        self.stack.enter_context(patch.object(handoff,'UNITS',self.root/'units'))
        if os.name!='posix':
            self.stack.enter_context(patch.object(ops,'protected_parents'))
            self.stack.enter_context(patch.object(handoff,'directory'))
            self.stack.enter_context(patch.object(handoff,'sync_directory'))
            self.stack.enter_context(patch.object(ops,'read_protected',side_effect=lambda p,**kw:p.read_bytes()))
            self.stack.enter_context(patch.object(ops,'write_protected',side_effect=self.portable_write))
        self.state={'schema':purge.SCHEMA_V2,'source':OLD,'initialized_at':NOW-600,
            'source_bound_at':NOW-600,'source_success':NOW-100,
            'last_success':NOW-100,'last_failure':None,
            'last_attempt':{'started_at':NOW-100,'finished_at':NOW-99,'result':'ok',
                'deleted':dict.fromkeys(purge.TABLES,7)}}
        self.original=ops.encoded(self.state)
        self.config=ops.encoded({'version':2,'source':OLD,
            'ping_url':'https://hc-ping.com/11111111-1111-4111-8111-111111111111'})
        ops.write_protected(purge.STATE/'state.json',self.original)
        ops.write_protected(purge.STATE/'health.json',ops.encoded(purge.health(self.state,NOW)))
        ops.write_protected(reporter.CONFIG,self.config)
        self.cleanup_state={'schema':cleanup.SCHEMA,'source':OLD,'initialized_at':NOW-600,
            'last_success':None,'last_attempt':{'started_at':NOW-50,'finished_at':NOW-49,
                'result':'failed','scrubbed':None}}
        self.cleanup_state['last_failure']=dict(self.cleanup_state['last_attempt'])
        self.cleanup_original=ops.encoded(self.cleanup_state)
        ops.write_protected(cleanup.STATE/'state.json',self.cleanup_original)
        ops.write_protected(cleanup.STATE/'health.json',ops.encoded(cleanup.health(self.cleanup_state,NOW)))
        self.old_files={'deploy/systemd/'+name:b'# old synthetic unit\n' for name in handoff.OLD_UNITS}
        self.new_files={'deploy/systemd/'+name:b'# new synthetic unit\n' for name in handoff.NEW_UNITS}
        for name in handoff.OLD_UNITS:
            ops.write_protected(handoff.UNITS/name,self.old_files['deploy/systemd/'+name],mode=0o644)
        self.terminal=self.stack.enter_context(patch.object(handoff,'terminal'))
        self.command=self.stack.enter_context(patch.object(purge,'command',return_value=b''))

    def portable_write(self,path,raw,*,previous=None,mode=0o600):
        if previous is None:
            with path.open('xb') as stream: stream.write(raw)
        else:
            self.assertEqual(previous,path.read_bytes()); path.write_bytes(raw)

    def stage(self):
        return handoff.stage(OLD,NEW,NOW,self.old_files,self.new_files)

    def publish(self,evidence):
        return handoff.publish(evidence,OLD,NEW,NOW,self.new_files)

    def test_history_exact_archive_and_no_inherited_success(self):
        evidence=self.stage()
        self.assertEqual('published_inactive_unverified',self.publish(evidence))
        self.assertEqual(self.original,(evidence/'admission-state.before').read_bytes())
        self.assertEqual(self.config,(evidence/'reporter-config.before').read_bytes())
        value=purge.read_state(NEW,NOW)
        for key in ('initialized_at','last_success','last_attempt','last_failure'):
            self.assertEqual(self.state[key],value[key])
        self.assertIn('source_unverified',purge.health(value,NOW)['issues'])
        self.assertIn('never_completed',cleanup.health(cleanup.read_state(NEW,NOW),NOW)['issues'])
        self.assertIn('source_unverified',cleanup.health(cleanup.read_state(NEW,NOW),NOW)['issues'])
        self.assertEqual(self.cleanup_original,(evidence/'cleanup-state.before').read_bytes())
        for key in ('initialized_at','last_success','last_attempt','last_failure'):
            self.assertEqual(self.cleanup_state[key],cleanup.read_state(NEW,NOW)[key])
        config=reporter.settings(reporter.CONFIG.read_bytes(),NEW)
        self.assertEqual(2,config['version'])
        self.assertEqual(ops.strict_json(self.config)['ping_url'],config['ping_url'])
        self.command.assert_called_once_with(['/usr/bin/systemctl','daemon-reload'])
        self.assertFalse(purge.rebind_pending())
        if os.name=='posix':
            self.assertEqual(0o700,evidence.stat().st_mode&0o777)
            for path in evidence.iterdir():
                self.assertEqual(0,path.stat().st_uid)
                self.assertEqual(0o600,path.stat().st_mode&0o777)

    def test_every_publication_write_can_reconcile_forward(self):
        evidence=self.stage()
        original_write=ops.write_protected
        # Each next write fails before publication, then the same sealed plan
        # advances one boundary. This includes marker and final completion.
        for count in range(len(handoff.target_paths())+2):
            with self.subTest(boundary=count):
                with patch.object(ops,'write_protected',side_effect=OSError('synthetic interruption')):
                    with self.assertRaises(OSError): self.publish(evidence)
                calls=[]
                def one(path,raw,**kw):
                    calls.append(path)
                    if len(calls)>1: raise OSError('synthetic interruption')
                    return original_write(path,raw,**kw)
                with patch.object(ops,'write_protected',side_effect=one):
                    try: self.publish(evidence)
                    except OSError: pass
        self.assertEqual('published_inactive_unverified',self.publish(evidence))
        self.assertEqual(self.original,(evidence/'admission-state.before').read_bytes())

    def test_target_drift_rejects_before_any_mutation(self):
        evidence=self.stage()
        reporter.CONFIG.write_bytes(self.config+b' ')
        with patch.object(ops,'write_protected') as write,self.assertRaises(ops.OperationError):
            self.publish(evidence)
        write.assert_not_called(); self.command.assert_not_called()
        self.assertEqual(self.original,(purge.STATE/'state.json').read_bytes())

    def test_archive_tampering_or_unsealed_attempt_rejects(self):
        evidence=self.stage()
        original=(evidence/'admission-state.after').read_bytes()
        (evidence/'admission-state.after').write_bytes(original+b' ')
        with self.assertRaises(ops.OperationError): self.publish(evidence)
        (evidence/'admission-state.after').write_bytes(original)
        (evidence/'ready.json').unlink()
        with self.assertRaises((ops.OperationError,FileNotFoundError)): self.publish(evidence)
        self.command.assert_not_called()

    def test_running_claim_and_repeated_apply_refuse(self):
        self.state['last_attempt'].update(result='running',finished_at=None)
        (purge.STATE/'state.json').write_bytes(ops.encoded(self.state))
        with self.assertRaises(ops.OperationError): self.stage()
        self.assertFalse((purge.STATE/'successors').exists())
        (purge.STATE/'state.json').write_bytes(self.original)
        self.stage()
        with self.assertRaises(FileExistsError): self.stage()

    def test_pending_barrier_blocks_runs_ack_monitors_and_send(self):
        evidence=self.stage()
        ops.write_protected(purge.STATE/'rebind-pending.json',(evidence/'ready.json').read_bytes())
        db=Mock()
        for module in (purge,cleanup):
            with self.assertRaises(ops.OperationError): module.run(NEW,db,clock=lambda:NOW)
            with self.assertRaises(ops.OperationError): module.acknowledge(NEW,NOW)
            with self.assertRaises(ops.OperationError): module.observe(NEW,NOW,persist=True)
            with redirect_stdout(io.StringIO()): self.assertEqual(1,module.observe(NEW,NOW,persist=False))
        db.query.assert_not_called()
        with patch.object(purge,'state_lock',side_effect=nullcontext),patch.object(reporter,'observe') as observe, \
                patch.object(reporter.healthchecks,'submit') as submit,self.assertRaises(ops.OperationError):
            reporter.report(Path('/synthetic')/OLD)
        observe.assert_not_called(); submit.assert_not_called()

    def test_success_requires_current_run_and_fresh_monitor(self):
        self.publish(self.stage())
        db=Mock(); db.query.return_value=True
        with patch.object(ops,'host_locks',side_effect=nullcontext), \
                patch.object(purge,'purge',return_value=(dict.fromkeys(purge.TABLES,0),'ok')):
            purge.run(NEW,db,clock=lambda:NOW)
        self.assertEqual(NOW,purge.read_state(NEW,NOW)['source_success'])
        with patch.object(purge,'timer_issues',return_value=[]),redirect_stdout(io.StringIO()):
            self.assertEqual(1,purge.observe(NEW,NOW,persist=False))
            self.assertEqual(0,purge.observe(NEW,NOW,persist=True))
            self.assertEqual(0,purge.observe(NEW,NOW,persist=False))

    def test_terminal_uses_unit_specific_properties_and_rejects_active(self):
        def result(argv):
            service=any(str(arg).endswith('.service') for arg in argv)
            return (b'LoadState=loaded\nActiveState=inactive\nSubState=dead\nUnitFileState=disabled\nJob=\n'+
                (b'MainPID=0\nControlPID=0\nControlGroup=\n' if service else b''))
        # Access the original function saved by the patcher.
        with patch.object(purge,'command',side_effect=result) as command:
            self.original_terminal(OLD,NEW)
            self.assertEqual(20,command.call_count)
        with patch.object(purge,'command',return_value=result(['x.timer']).replace(b'inactive',b'active')):
            with self.assertRaises(ops.OperationError): self.original_terminal(OLD,NEW)

    original_terminal=staticmethod(handoff.terminal)

    def install_fixtures(self):
        releases=self.root/'releases'; releases.mkdir()
        self.stack.enter_context(patch.object(handoff,'RELEASES',releases))
        for source,files,modules in ((OLD,self.old_files,handoff.NEW_FILES),(NEW,self.new_files,handoff.NEW_FILES)):
            release=releases/source; release.mkdir()
            manifest={}
            for name,raw in {**dict.fromkeys(modules,b'# synthetic source\n'),**files}.items():
                path=release/name; path.parent.mkdir(parents=True,exist_ok=True)
                ops.write_protected(path,raw,mode=0o644)
                manifest[name]=hashlib.sha256(raw).hexdigest()
            evidence=release/'handoff-installation'; evidence.mkdir(mode=0o700)
            ops.write_protected(evidence/'complete.json',ops.encoded({'commit':source,'phase':'installed_not_activated',
                'host_role':'db','system_python_only':True,'native_tests':'passed_no_skips'}))
            ops.write_protected(evidence/'source-manifest.json',ops.encoded(manifest))
        return releases

    def test_manifest_drift_prevents_publication(self):
        releases=self.install_fixtures()
        handoff.installed(OLD,successor=False); handoff.installed(NEW,successor=True)
        (releases/NEW/'backend/monitoring_rebind.py').write_bytes(b'# drift\n')
        with self.assertRaises(ops.OperationError): handoff.installed(NEW,successor=True)
        self.assertFalse((purge.STATE/'successors').exists())

    def test_complete_execute_and_exact_reconcile_hold_all_locks(self):
        self.install_fixtures()
        with patch.object(purge,'state_lock',side_effect=nullcontext) as lock, \
                patch.object(ops,'host_locks',side_effect=nullcontext):
            self.assertEqual('published_inactive_unverified',handoff.execute(OLD,NEW,clock=lambda:NOW))
            self.assertEqual(6,lock.call_count)
            lock.reset_mock()
            self.assertEqual('published_inactive_unverified',handoff.execute(OLD,NEW,reconcile=True,clock=lambda:NOW))
            self.assertEqual(6,lock.call_count)
        self.assertFalse(purge.rebind_pending())

    def test_v2_contract_refuses_v1_and_fabricated_success(self):
        import admission_alert_contract as contract
        raw=ops.encoded(purge.health(self.state,NOW))
        self.assertEqual(contract.UNAVAILABLE,contract.classify(0,raw,OLD,NOW))
        self.assertEqual((),contract.classify(0,raw,OLD,NOW,version=2))
        self.publish(self.stage())
        state=purge.read_state(NEW,NOW)
        for changes in ({'source_bound_at':0},{'source_success':NOW,'last_success':NOW,'last_attempt':None}):
            with self.assertRaises(ops.OperationError): purge.validate({**state,**changes},NEW,NOW)

    def test_totp_success_requires_new_run_and_fresh_monitor(self):
        import totp_cleanup_alert_contract as contract
        self.publish(self.stage())
        value=cleanup.read_state(NEW,NOW)
        raw=ops.encoded(cleanup.health(value,NOW))
        issues=contract.classify(1,raw,NEW,NOW)
        self.assertIn('source_unverified',issues)
        self.assertIn('totp:source_unverified',contract.combined((),issues))
        self.assertIn('source_unverified',contract.message(contract.combined((),issues)))
        db=Mock();db.query.return_value=True
        with patch.object(ops,'host_locks',side_effect=nullcontext),patch.object(cleanup.totp_cleanup,'scrub',return_value={'result':'ok','scrubbed':0}):
            cleanup.run(NEW,db,clock=lambda:NOW)
        self.assertEqual(NOW,cleanup.read_state(NEW,NOW)['source_success'])
        self.assertEqual(self.cleanup_state['last_failure'],cleanup.read_state(NEW,NOW)['last_failure'])
        with patch.object(cleanup,'timer_issues',return_value=[]),redirect_stdout(io.StringIO()):
            self.assertEqual(1,cleanup.observe(NEW,NOW,persist=False))
            self.assertEqual(0,cleanup.observe(NEW,NOW,persist=True))
            self.assertEqual(0,cleanup.observe(NEW,NOW,persist=False))

    def test_failed_terminal_services_are_not_reset_or_credited(self):
        def result(argv):
            service=any(str(arg).endswith('.service') for arg in argv)
            return (b'LoadState=loaded\nActiveState=failed\nSubState=failed\nUnitFileState=disabled\nJob=\nMainPID=0\nControlPID=0\nControlGroup=\n'
                if service else b'LoadState=loaded\nActiveState=inactive\nSubState=dead\nUnitFileState=disabled\nJob=\n')
        with patch.object(purge,'command',side_effect=result) as command:
            self.original_terminal(OLD,NEW)
            self.assertEqual(20,command.call_count)
            self.assertTrue(all(call.args[0][1]=='show' for call in command.call_args_list))
        for before,after in ((b'MainPID=0',b'MainPID=42'),(b'Job=',b'Job=42')):
            with patch.object(purge,'command',return_value=result(['x.service']).replace(before,after)):
                with self.assertRaises(ops.OperationError):self.original_terminal(OLD,NEW)

    def test_populated_cgroup_blocks_failed_unit(self):
        raw=b'LoadState=loaded\nActiveState=failed\nSubState=failed\nUnitFileState=disabled\nJob=\nMainPID=0\nControlPID=0\nControlGroup=/system.slice/synthetic.service\n'
        with patch.object(purge,'command',return_value=raw),patch.object(Path,'read_text',return_value='populated 1\nfrozen 0\n'):
            with self.assertRaises(ops.OperationError):self.original_terminal(OLD,NEW)

    def test_host_lock_conflict_never_dispatches_sql_or_manufactures_success(self):
        self.publish(self.stage())
        for module in (purge,cleanup):
            db=Mock()
            with patch.object(ops,'host_locks',side_effect=BlockingIOError('synthetic held lock')):
                with self.assertRaises(BlockingIOError):module.run(NEW,db,clock=lambda:NOW)
            db.query.assert_not_called()
            state=module.read_state(NEW,NOW)
            self.assertIsNone(state['source_success'])
            self.assertEqual('failed',state['last_attempt']['result'])
