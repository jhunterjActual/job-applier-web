"""Read-only aggregate title-format audit; never a deployment authorization."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from role_queries import TITLE_LIST_PREFIX


ACTIVE = "('preparing','queued','running','cancel_requested')"
TERMINAL = "('succeeded','failed','cancelled')"


def aggregate_sql(dialect, table):
    """Fixed tables/predicates only. Search text never leaves the database."""
    if dialect not in ('sqlite', 'postgres') or table not in ('saved_searches', 'search_runs'):
        raise ValueError('Unsupported audit scope.')
    marker = ("instr(keywords, ?) > 0" if dialect == 'sqlite'
              else "strpos(keywords, %s) > 0")
    exact = ("substr(keywords, 1, ?) = ?" if dialect == 'sqlite'
             else "left(keywords, %s) = %s")
    bytes_length = 'length(CAST(keywords AS BLOB))' if dialect == 'sqlite' else 'octet_length(keywords)'
    predicates = [('total_rows', '1=1'), ('marker_rows', marker), ('exact_prefix_rows', exact),
                  ('oversized_rows', f'length(keywords) > 500 OR {bytes_length} > 2000')]
    if table == 'saved_searches':
        predicates += [('enabled_marker_rows', f'enabled AND {marker}'),
                       ('scheduled_marker_rows', "enabled AND schedule_frequency IN ('daily','weekly') "
                        f'AND next_alert_at IS NOT NULL AND {marker}')]
    else:
        predicates += [('active_marker_rows', f'status IN {ACTIVE} AND {marker}'),
                       ('terminal_marker_rows', f'status IN {TERMINAL} AND {marker}'),
                       ('unknown_status_rows', f'status IS NULL OR status NOT IN {ACTIVE[:-1]},{TERMINAL[1:]}')]
    columns, parameters = [], []
    for name, predicate in predicates:
        columns.append(f'COALESCE(SUM(CASE WHEN {predicate} THEN 1 ELSE 0 END), 0) AS {name}')
        if exact in predicate:
            parameters.extend((len(TITLE_LIST_PREFIX), TITLE_LIST_PREFIX))
        elif marker in predicate:
            parameters.append(TITLE_LIST_PREFIX)
    qualified = f'careertrellis.{table}' if dialect == 'postgres' else table
    return 'SELECT ' + ', '.join(columns) + ' FROM ' + qualified, parameters, [name for name, _ in predicates]


def collect(connection, dialect):
    tables = {}
    for table in ('saved_searches', 'search_runs'):
        sql, parameters, names = aggregate_sql(dialect, table)
        with_cursor = connection.cursor()
        try:
            with_cursor.execute(sql, parameters)
            counts = with_cursor.fetchone()
            if counts is None or len(counts) != len(names):
                raise ValueError('Incomplete audit result.')
            if any(type(value) is not int or value < 0 for value in counts):
                raise ValueError('Invalid audit counts.')
            tables[table] = dict(zip(names, counts))
        finally:
            with_cursor.close()
    markers = sum(row['marker_rows'] for row in tables.values())
    invalid = sum(row['oversized_rows'] for row in tables.values()) + tables['search_runs']['unknown_status_rows']
    return {
        'audit_version': 1,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'title_parser_sha256': hashlib.sha256(Path(__file__).with_name('role_queries.py').read_bytes()).hexdigest(),
        'coverage': 'database_wide_bypass_principal' if dialect == 'postgres' else 'selected_sqlite_file',
        'read_only': True,
        'tables': tables,
        'finding': 'review_required' if markers or invalid else 'no_marker_or_storage_blockers_observed',
        'legacy_worker_compatibility': 'requires_review' if markers or invalid else 'no_stored_blockers_observed',
        'marker_detection': 'substring_anywhere_including_leading_whitespace; conservative_overcount',
        'envelope_validity': 'not_inspected; a marker is not evidence of intentional encoding',
        'release_authorized': False,
        'snapshot_only': True,
    }


def audit_sqlite(path):
    # No application imports, schema creation, immutable=1, or guessed database path.
    uri = Path(path).resolve(strict=True).as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=2)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        deadline = time.monotonic() + 10
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        return collect(connection, 'sqlite')
    finally:
        connection.rollback()
        connection.close()


def audit_postgres(service):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}', service):
        raise ValueError('Use an existing libpq service name.')
    import psycopg
    # The operator supplies an existing authorized service; no credentials are read
    # from application configuration or accepted as command-line parameters.
    with psycopg.connect(service=service, connect_timeout=5,
                         options='-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=2000') as connection:
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT rolsuper OR rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = current_user')
            principal = cursor.fetchone()
            if principal != (True,):
                raise ValueError('Database-wide audit visibility is required.')
        return collect(connection, 'postgres')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--sqlite', metavar='EXISTING_FILE')
    source.add_argument('--postgres-service', metavar='EXISTING_LIBPQ_SERVICE')
    args = parser.parse_args(argv)
    try:
        result = audit_sqlite(args.sqlite) if args.sqlite else audit_postgres(args.postgres_service)
    except Exception:
        # Driver diagnostics may include private values or connection details.
        print(json.dumps({'audit_version': 1, 'finding': 'audit_unavailable', 'release_authorized': False}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 2 if result['finding'] == 'review_required' else 0


if __name__ == '__main__':
    raise SystemExit(main())
