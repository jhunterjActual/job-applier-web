"""Offline schedule/failure/privacy checks; native descriptor tests are separate."""
from contextlib import ExitStack, nullcontext, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import admission_operations as ops
import admission_purge as purge

SOURCE = 'a'*40
START = 360000


class HealthTests(unittest.TestCase):
    def state(self):
        return {'schema': purge.SCHEMA, 'source': SOURCE, 'initialized_at': START,
            'last_success': None, 'last_attempt': None, 'last_failure': None}

    def test_two_actual_hourly_slots_not_monitor_invocations(self):
        state = self.state()
        for now in (START, START+3600, START+7199, START+7319):
            self.assertEqual([], purge.health(state, now)['issues'])
        self.assertEqual(['two_hourly_runs_missed'], purge.health(state, START+7320)['issues'])
        state['last_success'] = START+3600
        self.assertEqual([], purge.health(state, START+7320)['issues'])
        self.assertEqual(['two_hourly_runs_missed'], purge.health(state, START+10920)['issues'])

    def test_initialization_mid_hour_first_due_next_hour(self):
        state = self.state(); state['initialized_at'] += 3599
        self.assertEqual([], purge.health(state, START+7319)['issues'])
        self.assertIn('two_hourly_runs_missed', purge.health(state, START+7320)['issues'])

    def test_future_malformed_wrong_revision_and_unknown_fields_rejected(self):
        for changes in ({'last_success': START+1}, {'initialized_at': True}, {'source': 'b'*40},
                {'extra': 1}, {'last_attempt': {'result': 'ok'}}):
            with self.assertRaises(ops.OperationError):
                purge.validate({**self.state(), **changes}, SOURCE, START)

    def test_attempt_does_not_clear_failure_without_new_success(self):
        state = self.state()
        state['last_failure'] = {'started_at': START, 'finished_at': START+1, 'result': 'failed',
            'deleted': dict.fromkeys(purge.TABLES, None)}
        state['last_attempt'] = {'started_at': START+3600, 'finished_at': None, 'result': 'running',
            'deleted': dict.fromkeys(purge.TABLES, 0)}
        self.assertIn('last_run_failed', purge.health(state, START+3601)['issues'])
        self.assertIn('run_incomplete', purge.health(state, START+3800)['issues'])
        state['last_success'] = START+3600
        state['last_attempt'].update(result='ok', finished_at=START+3601)
        self.assertEqual([], purge.health(state, START+3602)['issues'])


class StateTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(purge, 'STATE', self.root))
        self.stack.enter_context(patch.object(ops, 'read_protected', side_effect=lambda p, **kw: p.read_bytes()))
        self.stack.enter_context(patch.object(ops, 'write_protected', side_effect=self.write))
        self.stack.enter_context(patch.object(ops, 'host_locks', side_effect=nullcontext))
        self.stack.enter_context(patch.object(purge, 'timer_issues', return_value=[]))
        purge.initialize(SOURCE, START)

    def write(self, path, raw, *, previous=None, **kw):
        if previous is None:
            with path.open('xb') as stream: stream.write(raw)
        else:
            self.assertEqual(previous, path.read_bytes()); path.write_bytes(raw)

    def test_success_then_failure_keeps_success_and_unknown_counts(self):
        db = Mock(); db.query.return_value = True
        with patch.object(purge, 'purge', return_value=(dict.fromkeys(purge.TABLES, 2), 'ok')):
            purge.run(SOURCE, db, clock=lambda: START+3600)
        with patch.object(purge, 'purge', side_effect=RuntimeError('synthetic-private')), self.assertRaises(RuntimeError):
            purge.run(SOURCE, db, clock=lambda: START+7200)
        state = purge.read_state(SOURCE, START+7200)
        self.assertEqual(START+3600, state['last_success'])
        self.assertEqual('failed', state['last_failure']['result'])
        self.assertEqual(dict.fromkeys(purge.TABLES, None), state['last_failure']['deleted'])
        self.assertNotIn('synthetic-private', (self.root/'state.json').read_text())

    def test_lost_final_receipt_refuses_retry_then_acknowledge_retains_failure(self):
        original = ops.state_write
        def lost(path, value):
            if value['last_attempt']['result'] != 'running': raise OSError('lost')
            original(path, value)
        db = Mock(); db.query.return_value = True
        with patch.object(ops, 'state_write', side_effect=lost), \
                patch.object(purge, 'purge', return_value=(dict.fromkeys(purge.TABLES, 0), 'ok')), self.assertRaises(OSError):
            purge.run(SOURCE, db, clock=lambda: START+3600)
        db.reset_mock()
        with self.assertRaises(ops.OperationError): purge.run(SOURCE, db, clock=lambda: START+7200)
        db.query.assert_not_called()
        purge.acknowledge(SOURCE, START+7200)
        state = purge.read_state(SOURCE, START+7200)
        self.assertIsNone(state['last_success'])
        self.assertEqual('failed', state['last_failure']['result'])

    def test_backlog_is_not_success_and_monitor_stays_independent(self):
        db = Mock(); db.query.return_value = True
        with patch.object(purge, 'purge', return_value=(dict.fromkeys(purge.TABLES, 2000), 'backlog')):
            purge.run(SOURCE, db, clock=lambda: START+3600)
        self.assertIsNone(purge.read_state(SOURCE, START+3600)['last_success'])
        with patch.object(purge, 'state_lock', side_effect=AssertionError('must not wait for purge')), redirect_stdout(io.StringIO()):
            self.assertEqual(1, purge.observe(SOURCE, START+3601, persist=True))

    def test_invalid_state_persists_alert_without_rewriting_evidence(self):
        path = self.root/'state.json'; path.write_bytes(b'{"private":"synthetic-private"}')
        output = io.StringIO()
        with redirect_stdout(output): self.assertEqual(1, purge.observe(SOURCE, START, persist=True))
        self.assertEqual(b'{"private":"synthetic-private"}', path.read_bytes())
        self.assertIn('state_unavailable', output.getvalue()); self.assertNotIn('synthetic-private', output.getvalue())

    def test_monitor_changes_only_and_timer_problems_remain_alerts(self):
        out = io.StringIO()
        with redirect_stdout(out), patch.object(purge, 'timer_issues', return_value=['purge_timer_unavailable']):
            purge.observe(SOURCE, START, persist=True)
            purge.observe(SOURCE, START+1, persist=True)
        self.assertEqual(1, len(out.getvalue().splitlines()))
        self.assertEqual(START+1, json.loads((self.root/'health.json').read_bytes())['checked_at'])

    def test_status_rejects_missing_stale_future_or_corrupt_monitor_evidence(self):
        path = self.root/'health.json'
        for raw in (None,b'{',ops.encoded({'schema':purge.SCHEMA,'source':SOURCE,
                'checked_at':START-421,'result':'ok','issues':[]}),
                ops.encoded({'schema':purge.SCHEMA,'source':SOURCE,'checked_at':START+1,'result':'ok','issues':[]})):
            if raw is not None: path.write_bytes(raw)
            out=io.StringIO()
            with redirect_stdout(out): self.assertEqual(1,purge.observe(SOURCE,START,persist=False))
            self.assertIn('monitor_evidence_unavailable',out.getvalue())
        path.unlink()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0,purge.observe(SOURCE,START,persist=True))
            self.assertEqual(0,purge.observe(SOURCE,START,persist=False))

    def test_monitor_does_not_overwrite_shape_invalid_previous_evidence(self):
        path=self.root/'health.json'
        for raw in (b'[]',b'{"unexpected":1}'):
            path.write_bytes(raw)
            with self.assertRaises(ops.OperationError): purge.observe(SOURCE,START,persist=True)
            self.assertEqual(raw,path.read_bytes())


class BoundsTests(unittest.TestCase):
    def test_fixed_bounds_and_round_robin_fairness(self):
        db = Mock(); db.query.return_value = {'deleted': 500}
        counts, result = purge.purge(db)
        self.assertEqual('backlog', result); self.assertEqual(16, db.query.call_count)
        self.assertEqual(dict.fromkeys(purge.TABLES, 2000), counts)
        for table, call in zip(purge.TABLES, db.query.call_args_list[:4]):
            self.assertIn('DELETE FROM careertrellis_admission.' + table, call.args[0])
        db.query.return_value = {'deleted': 0}; db.reset_mock()
        self.assertEqual('ok', purge.purge(db)[1]); self.assertEqual(4, db.query.call_count)

    def test_time_budget_stops_without_an_extra_delete(self):
        db = Mock()
        self.assertEqual('backlog', purge.purge(db, monotonic=iter([0, 83]).__next__)[1])
        db.query.assert_not_called()

    def test_sql_names_and_both_flow_deadlines_are_fixed(self):
        with self.assertRaises(ops.OperationError): purge.batch_sql('app_users')
        sql = purge.batch_sql('flows')
        self.assertIn('deadline < statement_timestamp()', sql)
        self.assertIn('expires < statement_timestamp()', sql)
        self.assertIn("transaction_timeout='6s'", sql)
        self.assertIn('LIMIT 500 FOR UPDATE', sql)
        self.assertNotIn('careertrellis_admission.purge()', sql)

    def test_timer_inventory_requires_loaded_enabled_active(self):
        with patch.object(purge, 'command', return_value=b'LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n'):
            self.assertEqual([], purge.timer_issues(SOURCE))
        with patch.object(purge, 'command', return_value=b'LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\n'):
            self.assertEqual(2, len(purge.timer_issues(SOURCE)))

    def test_invalid_cli_has_fixed_diagnostic(self):
        out = io.StringIO()
        with redirect_stderr(out): self.assertEqual(2, purge.main(['synthetic-private']))
        self.assertNotIn('synthetic-private', out.getvalue())

    def test_native_command_bound_and_reset_environment(self):
        with patch.object(purge.subprocess, 'run', return_value=Mock(returncode=0,stdout=b't\n')) as run:
            self.assertTrue(purge.LocalPostgres().query('SELECT true;'))
        self.assertEqual(8,run.call_args.kwargs['timeout'])
        self.assertEqual({'PATH','LANG'},set(run.call_args.kwargs['env']))
        self.assertIn('/var/run/postgresql',run.call_args.args[0])
        self.assertNotIn('shell',run.call_args.kwargs)
