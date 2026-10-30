"""Run on DB host only, by authorized operator; SELECT-only nonce-bound receipt.
No credentials or individual search data cross hosts. Never grants activation.
"""
import argparse
from datetime import datetime, timezone
import json
import os
import re
import subprocess

APPROVED_ENVELOPE = 'ct:title-list:v1:["Data Engineer","Software Engineer - Data"]'
SQL = """
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='10s';
SET LOCAL lock_timeout='2s';
SELECT json_build_object('database_expected',current_database()='careertrellis',
 'read_only',current_setting('transaction_read_only'),'isolation',current_setting('transaction_isolation'),
 'global_visibility',(SELECT rolsuper OR rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=current_user),
 'schema_revision',(SELECT version_num FROM careertrellis.alembic_version));
SELECT json_build_object('total_rows',count(*),
 'marker_rows',count(*) FILTER(WHERE strpos(keywords,'ct:title-list:')>0),
 'exact_prefix_rows',count(*) FILTER(WHERE left(keywords,17)='ct:title-list:v1:'),
 'oversized_rows',count(*) FILTER(WHERE length(keywords)>500 OR octet_length(keywords)>2000),
 'enabled_marker_rows',count(*) FILTER(WHERE enabled AND strpos(keywords,'ct:title-list:')>0),
 'scheduled_marker_rows',count(*) FILTER(WHERE enabled AND schedule_frequency IN ('daily','weekly')
 AND next_alert_at IS NOT NULL AND strpos(keywords,'ct:title-list:')>0)) FROM careertrellis.saved_searches;
SELECT json_build_object('total_rows',count(*),
 'marker_rows',count(*) FILTER(WHERE strpos(keywords,'ct:title-list:')>0),
 'exact_prefix_rows',count(*) FILTER(WHERE left(keywords,17)='ct:title-list:v1:'),
 'oversized_rows',count(*) FILTER(WHERE length(keywords)>500 OR octet_length(keywords)>2000),
 'active_marker_rows',count(*) FILTER(WHERE status IN ('preparing','queued','running','cancel_requested') AND strpos(keywords,'ct:title-list:')>0),
 'terminal_marker_rows',count(*) FILTER(WHERE status IN ('succeeded','failed','cancelled') AND strpos(keywords,'ct:title-list:')>0),
 'approved_terminal_envelope_rows',count(*) FILTER(WHERE status IN ('succeeded','failed','cancelled')
 AND keywords='ct:title-list:v1:["Data Engineer","Software Engineer - Data"]'),
 'unknown_status_rows',count(*) FILTER(WHERE status IS NULL OR status NOT IN ('preparing','queued','running','cancel_requested','succeeded','failed','cancelled')))
 FROM careertrellis.search_runs;
SELECT json_build_object('active_search_rows',(SELECT count(*) FROM careertrellis.search_runs
 WHERE status IN ('preparing','queued','running','cancel_requested')),
 'active_erasure_rows',(SELECT count(*) FROM careertrellis.account_erasure_requests WHERE status <> 'completed'),
 'due_saved_search_rows',(SELECT count(*) FROM careertrellis.saved_searches WHERE enabled
 AND schedule_frequency IN ('daily','weekly') AND next_alert_at <= CURRENT_TIMESTAMP),
 'near_due_saved_search_rows',(SELECT count(*) FROM careertrellis.saved_searches WHERE enabled
 AND schedule_frequency IN ('daily','weekly') AND next_alert_at > CURRENT_TIMESTAMP
 AND next_alert_at <= CURRENT_TIMESTAMP + INTERVAL '5 minutes'));
ROLLBACK;
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--recovery', required=True)
    args = parser.parse_args()
    try:
        assert re.fullmatch('[0-9a-f]{64}', args.nonce)
        assert all(re.fullmatch('[0-9a-f]{40}', x) for x in (args.target, args.recovery))
        assert os.geteuid() == 0 and os.uname().nodename == 'job-applier-db-staging-01'
        observed = datetime.now(timezone.utc).isoformat()
        result = subprocess.run(['sudo', '-n', '-u', 'postgres', 'psql', '-X', '-q', '-A', '-t',
            '-v', 'ON_ERROR_STOP=1', '-d', 'careertrellis'], input=SQL, text=True, capture_output=True,
            timeout=35, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                            'PGOPTIONS': '-c default_transaction_read_only=on'})
        assert result.returncode == 0
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert len(rows) == 4
        assert rows[0]['global_visibility'] is True and rows[0]['database_expected'] is True
        print(json.dumps({'audit_version': 2, 'request_nonce': args.nonce, 'target': args.target, 'recovery': args.recovery,
            'host': os.uname().nodename, 'observed_at_utc': observed, 'metadata': rows[0],
            'tables': dict(zip(('saved_searches', 'search_runs'), rows[1:3])), **rows[3],
            'release_authorized': False}, sort_keys=True))
        return 0
    except BaseException:
        print('{"status":"db_audit_refused","details_suppressed":true}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
