"""Synthetic read-only audit tests; no credentials, providers, or hosted DB."""
from contextlib import closing, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import title_compatibility_audit as audit


class TitleCompatibilityAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ct-title-audit-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'synthetic.sqlite'
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('CREATE TABLE saved_searches (keywords TEXT NOT NULL, enabled INTEGER, schedule_frequency TEXT, next_alert_at TEXT)')
            connection.execute('CREATE TABLE search_runs (keywords TEXT NOT NULL, status TEXT)')

    def add_rows(self, saved=(), runs=()):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executemany('INSERT INTO saved_searches VALUES (?, ?, ?, ?)', saved)
            connection.executemany('INSERT INTO search_runs VALUES (?, ?)', runs)

    def test_empty_and_legacy_semicolons_are_clear_without_release_authority(self):
        for rows in ([], [('A;B;C;D', 1, 'daily', '2099-01-01')]):
            self.add_rows(saved=rows)
            result = audit.audit_sqlite(self.path)
            self.assertEqual('no_marker_or_storage_blockers_observed', result['finding'])
            self.assertEqual('selected_sqlite_file', result['coverage'])
            self.assertFalse(result['release_authorized'])
            self.assertTrue(result['snapshot_only'])

    def test_all_saved_rows_and_whitespace_markers_block_even_disabled_unscheduled(self):
        marker = audit.TITLE_LIST_PREFIX + '["PRIVATE_TITLE"]'
        self.add_rows(saved=[(marker, 0, 'none', None), ('\t\u2003' + marker, 1, 'none', None),
                             ('ordinary interior ' + marker, 1, 'daily', '2099-01-01'),
                             (audit.TITLE_LIST_PREFIX + 'malformed', 1, 'weekly', '2000-01-01')])
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = audit.audit_sqlite(self.path)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual({'total_rows': 4, 'marker_rows': 4, 'exact_prefix_rows': 2,
                          'oversized_rows': 0, 'enabled_marker_rows': 3, 'scheduled_marker_rows': 2},
                         result['tables']['saved_searches'])
        self.assertEqual('review_required', result['finding'])
        self.assertNotIn('PRIVATE_TITLE', json.dumps(result))
        self.assertEqual([self.path], list(self.path.parent.iterdir()))

    def test_active_cancel_requested_and_terminal_runs_are_distinguished(self):
        marker = audit.TITLE_LIST_PREFIX + 'synthetic'
        statuses = ['preparing', 'queued', 'running', 'cancel_requested', 'succeeded', 'failed', 'cancelled']
        self.add_rows(runs=[(marker, state) for state in statuses])
        counts = audit.audit_sqlite(self.path)['tables']['search_runs']
        self.assertEqual(4, counts['active_marker_rows'])
        self.assertEqual(3, counts['terminal_marker_rows'])
        self.assertEqual(0, counts['unknown_status_rows'])

    def test_storage_or_unknown_status_cannot_report_clear(self):
        self.add_rows(saved=[('a' * 501, 1, 'none', None)], runs=[('legacy', 'unknown')])
        result = audit.audit_sqlite(self.path)
        self.assertEqual(1, result['tables']['saved_searches']['oversized_rows'])
        self.assertEqual(1, result['tables']['search_runs']['unknown_status_rows'])
        self.assertEqual('review_required', result['finding'])

    def test_sqlite_connection_rejects_writes(self):
        def attempt_write(connection, dialect):
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute('DELETE FROM saved_searches')
            return {'read_only': True}
        with patch.object(audit, 'collect', side_effect=attempt_write):
            self.assertTrue(audit.audit_sqlite(self.path)['read_only'])

    def test_missing_file_or_schema_does_not_create_database_or_emit_error_details(self):
        missing = self.path.parent / 'PRIVATE_missing.sqlite'
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(1, audit.main(['--sqlite', str(missing)]))
        self.assertFalse(missing.exists())
        self.assertNotIn('PRIVATE', output.getvalue())
        with patch.object(audit, 'audit_sqlite', side_effect=RuntimeError('PRIVATE secret diagnostic')):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(1, audit.main(['--sqlite', str(self.path)]))
        self.assertEqual({'audit_version': 1, 'finding': 'audit_unavailable', 'release_authorized': False},
                         json.loads(output.getvalue()))

    def test_cli_exit_codes_report_findings_without_rows(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, audit.main(['--sqlite', str(self.path)]))
        self.add_rows(runs=[(audit.TITLE_LIST_PREFIX + 'PRIVATE', 'failed')])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(2, audit.main(['--sqlite', str(self.path)]))
        self.assertNotIn('PRIVATE', output.getvalue())

    def test_postgres_requires_global_visibility_before_payload_queries(self):
        for principal in ((False,), None, (True,)):
            connection = MagicMock()
            connection.__enter__.return_value = connection
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = principal
            with patch('psycopg.connect', return_value=connection) as connect, \
                 patch.object(audit, 'collect', return_value={'synthetic': True}) as collect:
                if principal == (True,):
                    self.assertEqual({'synthetic': True}, audit.audit_postgres('synthetic-audit'))
                    collect.assert_called_once_with(connection, 'postgres')
                else:
                    with self.assertRaises(ValueError):
                        audit.audit_postgres('synthetic-audit')
                    collect.assert_not_called()
                self.assertIn('default_transaction_read_only=on', connect.call_args.kwargs['options'])
                self.assertIn('statement_timeout=10000', connect.call_args.kwargs['options'])
                self.assertEqual('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY',
                                 cursor.execute.call_args_list[0].args[0])

    def test_untrusted_service_and_scope_are_rejected(self):
        with patch('psycopg.connect') as connect:
            with self.assertRaises(ValueError):
                audit.audit_postgres('postgresql://PRIVATE')
            connect.assert_not_called()
        for dialect, table in (('other', 'search_runs'), ('sqlite', 'search_runs; DELETE')):
            with self.assertRaises(ValueError):
                audit.aggregate_sql(dialect, table)


if __name__ == '__main__':
    unittest.main()
