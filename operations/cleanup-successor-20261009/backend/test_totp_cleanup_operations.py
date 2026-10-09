"""Synthetic persisted-state/alert rehearsal; no provider, host DB or ping."""
from contextlib import ExitStack, nullcontext, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import admission_operations as ops
import admission_reporter as reporter
import totp_cleanup as scrub
import totp_cleanup_operations as cleanup
import totp_cleanup_alert_contract as contract

SOURCE = 'a'*40
START = 360300


class StateTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(cleanup, 'STATE', self.root))
        self.stack.enter_context(patch.object(ops, 'read_protected', side_effect=lambda p, **kw: p.read_bytes()))
        self.stack.enter_context(patch.object(ops, 'write_protected', side_effect=self.write))
        self.stack.enter_context(patch.object(ops, 'host_locks', side_effect=nullcontext))
        self.stack.enter_context(patch.object(cleanup, 'timer_issues', return_value=[]))
        cleanup.initialize(SOURCE, START)
        self.db = Mock(); self.db.query.return_value = True

    def write(self, path, raw, *, previous=None, **kw):
        if previous is None:
            with path.open('xb') as stream: stream.write(raw)
        else:
            self.assertEqual(previous, path.read_bytes()); path.write_bytes(raw)

    def run_ok(self, now=START):
        with patch.object(scrub, 'scrub', return_value={'result':'ok', 'scrubbed':3}):
            self.assertEqual('ok', cleanup.run(SOURCE, self.db, clock=lambda: now))

    def test_initialization_is_not_success_and_stale_success_alerts(self):
        value = cleanup.read_state(SOURCE, START)
        self.assertIn('never_completed', cleanup.health(value, START)['issues'])
        self.run_ok()
        value = cleanup.read_state(SOURCE, START)
        self.assertEqual([], cleanup.health(value, START+3719)['issues'])
        self.assertEqual(['hourly_run_missed'], cleanup.health(value, START+3720)['issues'])
        self.assertEqual(3, value['last_attempt']['scrubbed'])
        self.assertNotIn('deleted', json.dumps(value))

    def test_lost_final_receipt_blocks_retry_until_acknowledged(self):
        original = ops.state_write
        def lost(path, value):
            if value['last_attempt']['result'] != 'running': raise OSError('synthetic lost receipt')
            original(path, value)
        with patch.object(ops, 'state_write', side_effect=lost), self.assertRaises(OSError): self.run_ok()
        self.db.reset_mock()
        with self.assertRaises(ops.OperationError): cleanup.run(SOURCE, self.db, clock=lambda: START+60)
        self.db.query.assert_not_called()
        value = cleanup.read_state(SOURCE, START+60)
        self.assertIn('run_incomplete', cleanup.health(value, START+60)['issues'])
        cleanup.acknowledge(SOURCE, START+60)
        value = cleanup.read_state(SOURCE, START+60)
        self.assertIsNone(value['last_success']); self.assertIsNone(value['last_failure']['scrubbed'])
        self.run_ok(START+120)
        self.assertEqual([], cleanup.health(cleanup.read_state(SOURCE, START+120), START+120)['issues'])

    def test_failure_and_locked_backlog_never_clear_last_success(self):
        self.run_ok()
        with patch.object(scrub, 'scrub', return_value={'result':'backlog','scrubbed':0}):
            self.assertEqual('backlog', cleanup.run(SOURCE, self.db, clock=lambda: START+10))
        value = cleanup.read_state(SOURCE, START+10)
        self.assertEqual(START, value['last_success'])
        self.assertIn('expired_ciphertext_remaining', cleanup.health(value, START+10)['issues'])
        with patch.object(scrub, 'scrub', side_effect=RuntimeError('synthetic-private')), self.assertRaises(RuntimeError):
            cleanup.run(SOURCE, self.db, clock=lambda: START+20)
        value = cleanup.read_state(SOURCE, START+20)
        self.assertIsNone(value['last_failure']['scrubbed'])
        self.assertNotIn('synthetic-private', (self.root/'state.json').read_text())

    def test_claim_write_failure_prevents_database_call(self):
        with patch.object(ops, 'state_write', side_effect=OSError), self.assertRaises(OSError):
            cleanup.run(SOURCE, self.db, clock=lambda: START)
        self.db.query.assert_not_called()

    def test_status_requires_fresh_monitor_and_keeps_monitor_alert(self):
        self.run_ok()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(1, cleanup.observe(SOURCE, START, persist=False))
            self.assertEqual(0, cleanup.observe(SOURCE, START, persist=True))
            self.assertEqual(0, cleanup.observe(SOURCE, START+420, persist=False))
            self.assertEqual(1, cleanup.observe(SOURCE, START+421, persist=False))
            with patch.object(cleanup, 'timer_issues', return_value=['cleanup_timer_unavailable']):
                self.assertEqual(1, cleanup.observe(SOURCE, START+422, persist=True))
            self.assertEqual(1, cleanup.observe(SOURCE, START+423, persist=False))

    def test_malformed_state_alerts_without_rewriting_original(self):
        raw = b'{"private":"synthetic-private"}'
        (self.root/'state.json').write_bytes(raw)
        output = io.StringIO()
        with redirect_stdout(output): self.assertEqual(1, cleanup.observe(SOURCE, START, persist=True))
        self.assertEqual(raw, (self.root/'state.json').read_bytes())
        self.assertNotIn('synthetic-private', output.getvalue())

    def test_future_wrong_source_counts_and_unknown_fields_reject(self):
        self.run_ok()
        value = cleanup.read_state(SOURCE, START)
        for changes in ({'source':'b'*40}, {'last_success':START+1}, {'initialized_at':True}, {'extra':1}):
            with self.assertRaises(ops.OperationError): cleanup.validate({**value, **changes}, SOURCE, START)
        for count in (-1, True, 2001, None):
            changed = {**value, 'last_attempt':{**value['last_attempt'], 'scrubbed':count}}
            with self.assertRaises(ops.OperationError): cleanup.validate(changed, SOURCE, START)


class BoundsAndDeliveryTests(unittest.TestCase):
    def test_schedule_separation_and_reporter_budget(self):
        root = Path(__file__).resolve().parents[1]/'deploy/systemd'
        admission = (root/'careertrellis-admission-purge@.timer').read_text()
        enrollment = (root/'careertrellis-totp-cleanup@.timer').read_text()
        self.assertIn('*:00:00 UTC', admission)
        self.assertIn('*:05:00 UTC', enrollment)
        self.assertGreater(cleanup.SLOT_OFFSET, 100)
        self.assertIn('TimeoutStartSec=50s', (root/'careertrellis-admission-reporter@.service').read_text())
        self.assertLess(2*(reporter.STATUS_TIMEOUT+2)+8, 50)

    def test_scrub_limits_and_locked_remainder(self):
        db = Mock(); db.query.return_value = {'scrubbed':500, 'remaining':True}
        self.assertEqual({'result':'backlog','scrubbed':2000}, scrub.scrub(db))
        self.assertEqual(4, db.query.call_count)
        db.reset_mock(); db.query.return_value = {'scrubbed':0, 'remaining':True}
        self.assertEqual({'result':'backlog','scrubbed':0}, scrub.scrub(db))
        db.query.assert_called_once()
        db.reset_mock()
        self.assertEqual('backlog', scrub.scrub(db, monotonic=iter([0,24]).__next__)['result'])
        db.query.assert_not_called()

    def test_alert_contract_rejects_uncertainty_and_preserves_lanes(self):
        healthy = {'schema':cleanup.SCHEMA,'source':SOURCE,'checked_at':START,'result':'ok','issues':[]}
        self.assertEqual((), contract.classify(0, ops.encoded(healthy), SOURCE, START))
        for code, raw in ((None,b''), (1,ops.encoded(healthy)), (0,b'{}'),
                (0,ops.encoded({**healthy,'checked_at':START-421})),
                (0,ops.encoded({**healthy,'checked_at':START+1})),
                (1,ops.encoded({**healthy,'result':'alert','issues':['unknown']}))):
            self.assertEqual(contract.UNAVAILABLE, contract.classify(code, raw, SOURCE, START))
        value = contract.combined((), ('expired_ciphertext_remaining',))
        self.assertEqual(('totp:expired_ciphertext_remaining',), value)
        self.assertIn('authenticator', contract.message(value))

    def test_version2_reporter_requires_both_observations_before_send(self):
        raw = ops.encoded({'version':2,'source':SOURCE,'ping_url':'https://hc-ping.com/11111111-1111-4111-8111-111111111111'})
        healthy = ops.encoded({'schema':reporter.contract.SCHEMA_V2,'source':SOURCE,'checked_at':START,'result':'ok','issues':[]})
        with patch.object(ops,'read_protected',return_value=raw), patch.object(ops,'state_write') as write, \
                patch.object(reporter.purge,'state_lock',side_effect=nullcontext), \
                patch.object(reporter,'observe',side_effect=[(0,healthy),(None,b'')]) as observe, \
                patch.object(reporter.time,'time',return_value=START), \
                patch.object(reporter.healthchecks,'submit',return_value='accepted') as send:
            self.assertEqual('alert', reporter.report(Path('/synthetic')/SOURCE)['observation'])
        self.assertEqual(2, observe.call_count)
        self.assertEqual(('totp:observation_unavailable',), send.call_args.args[1])
        self.assertEqual({'version':2}, send.call_args.kwargs)
        self.assertEqual(2, write.call_args.args[1]['version'])

    def test_version2_healthy_failure_and_aging_matrix(self):
        raw = ops.encoded({'version':2,'source':SOURCE,'ping_url':'https://hc-ping.com/11111111-1111-4111-8111-111111111111'})
        def health(schema, issues=()):
            return ops.encoded({'schema':schema,'source':SOURCE,'checked_at':START,
                'result':'alert' if issues else 'ok','issues':list(issues)})
        for admission_issues, cleanup_issues, age in (((),(),0), (('last_run_failed',),(),0),
                ((),('expired_ciphertext_remaining',),0), ((),(),421)):
            with self.subTest(admission=admission_issues, cleanup=cleanup_issues, age=age), \
                    patch.object(ops,'read_protected',return_value=raw), patch.object(ops,'state_write'), \
                    patch.object(reporter.purge,'state_lock',side_effect=nullcontext), \
                    patch.object(reporter,'observe',side_effect=[
                        (int(bool(admission_issues)),health(reporter.contract.SCHEMA_V2,admission_issues)),
                        (int(bool(cleanup_issues)),health(cleanup.SCHEMA,cleanup_issues))]), \
                    patch.object(reporter.time,'time',side_effect=[START,START+age]), \
                    patch.object(reporter.healthchecks,'submit',return_value='accepted') as send:
                result = reporter.report(Path('/synthetic')/SOURCE)
                self.assertEqual('alert' if admission_issues or cleanup_issues or age else 'healthy',result['observation'])
                send.assert_called_once()

    def test_missing_disabled_or_unknown_timers_alert(self):
        for raw in (b'LoadState=not-found\nActiveState=inactive\nUnitFileState=\n',
                b'LoadState=loaded\nActiveState=active\nUnitFileState=disabled\n'):
            with patch.object(cleanup.purge,'command',return_value=raw):
                self.assertEqual(2,len(cleanup.timer_issues(SOURCE)))


@unittest.skipUnless(os.name=='posix' and getattr(os,'geteuid',lambda:-1)()==0, 'Linux root fixture')
class NativeStateTests(unittest.TestCase):
    def test_killed_process_holds_claim_until_explicit_acknowledgement(self):
        with tempfile.TemporaryDirectory(dir='/') as folder, patch.object(cleanup,'STATE',Path(folder)):
            root = Path(folder)
            cleanup.initialize(SOURCE,START)
            code = ("import sys,time; from pathlib import Path; from contextlib import nullcontext; "
                "sys.path.insert(0,sys.argv[1]); import totp_cleanup_operations as c; "
                "c.STATE=Path(sys.argv[2]); c.ops.host_locks=nullcontext; "
                "db=type('DB',(),{'query':lambda self,sql:time.sleep(60)})(); "
                "c.run('a'*40,db,clock=lambda:360300)")
            child = subprocess.Popen([sys.executable,'-I','-B','-c',code,str(Path(__file__).parent),folder],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                deadline=time.monotonic()+5
                while cleanup.read_state(SOURCE,START)['last_attempt'] is None and time.monotonic()<deadline:
                    time.sleep(0.02)
                self.assertEqual('running',cleanup.read_state(SOURCE,START)['last_attempt']['result'])
                child.kill(); child.wait(timeout=3)
                db=Mock()
                with self.assertRaises(ops.OperationError): cleanup.run(SOURCE,db,clock=lambda:START+60)
                db.query.assert_not_called()
                cleanup.acknowledge(SOURCE,START+60)
                self.assertIsNone(cleanup.read_state(SOURCE,START+60)['last_success'])
            finally:
                if child.poll() is None: child.kill(); child.wait(timeout=3)

    def test_atomic_private_state_and_independent_monitor_lock(self):
        with tempfile.TemporaryDirectory(dir='/') as folder, patch.object(cleanup,'STATE',Path(folder)), \
                patch.object(cleanup,'timer_issues',return_value=[]):
            root = Path(folder)
            with cleanup.purge.state_lock(root/'cleanup.lock'):
                cleanup.initialize(SOURCE, START)
                with self.assertRaises(BlockingIOError):
                    with cleanup.purge.state_lock(root/'cleanup.lock'): pass
                with cleanup.purge.state_lock(root/'monitor.lock'), redirect_stdout(io.StringIO()):
                    self.assertEqual(1, cleanup.observe(SOURCE, START, persist=True))
            for path in root.iterdir():
                self.assertEqual(0o600,path.stat().st_mode&0o777); self.assertEqual(0,path.stat().st_uid)
            state = root/'state.json'; state.chmod(0o644)
            with self.assertRaises(ops.OperationError): cleanup.read_state(SOURCE, START)
