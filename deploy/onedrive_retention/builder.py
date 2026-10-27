"""Build immutable local-recovery plans from bound source database-size facts.

No database, provider or application operations occur here. Parsed claims alone
are not authentication. The fixed native writer verifies the installed namespace
and persistent-idle entry before any output; unknown runtime state fails closed.
Missing facts on historical candidates require a separately reviewed backfill,
never a guessed database-size ceiling.
"""
import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import socket

from . import bundle, entry, local_restore_plan as plans, native_restore, policy


SIZE_SCHEMA = 'careertrellis.pgbackrest-source-database-size/v1'
MAX_DATABASE_BYTES = 1024 ** 4
MAX_INFO_ROWS = 10000


class BuilderRefused(ValueError): pass


def need(condition):
    if not condition: raise BuilderRefused('native_plan_builder_refused')


def clock(now=None):
    result = dt.datetime.now(dt.timezone.utc) if now is None else now
    need(type(result) is dt.datetime and result.utcoffset() == dt.timedelta(0))
    return result


def digest(value): return hashlib.sha256(bundle.canonical(value)).hexdigest()


def database_sizes(info, label=None, *, observed_at, now=None):
    """Project only selected/ancestor logical sizes from private pgBackRest info.

    Omitted label selects the newest completed row. Duplicate labels, unhealthy
    selected/ancestor rows, unknown/noninteger sizes and future completion times
    refuse. No raw info, connection values or provider metadata are returned.
    """
    try:
        observed = policy.timestamp(observed_at); need(observed <= clock(now))
        need(type(info) is list and len(info) == 1)
        stanza = info[0]
        need(type(stanza) is dict and stanza.get('name') == 'careertrellis'
             and type(stanza.get('status')) is dict
             and type(stanza['status'].get('code')) is int and stanza['status']['code'] == 0)
        rows = stanza.get('backup'); need(type(rows) is list and 0 < len(rows) <= MAX_INFO_ROWS)
        by_label = {}
        for row in rows:
            need(type(row) is dict and type(row.get('label')) is str)
            plans._label(row['label'])
            need(row['label'] not in by_label and type(row.get('timestamp')) is dict)
            stopped = row['timestamp'].get('stop')
            need(type(stopped) is int and 0 < stopped <= observed.timestamp())
            by_label[row['label']] = row
        if label is None:
            newest = max(row['timestamp']['stop'] for row in rows)
            latest = [row for row in rows if row['timestamp']['stop'] == newest]
            need(len(latest) == 1)
            label = latest[0]['label']
        ancestor = plans._label(label)
        sizes = []
        for selected in (label, ancestor):
            need(selected in by_label)
            row = by_label[selected]
            need(row.get('error', False) is False and type(row.get('info')) is dict)
            size = row['info'].get('size')
            need(type(size) is int and 0 < size <= MAX_DATABASE_BYTES)
            sizes.append(size)
        need(by_label[ancestor]['timestamp']['stop'] <= by_label[label]['timestamp']['stop'])
        return {'backup_label': label, 'full_ancestor_label': ancestor,
                'selected_database_bytes': sizes[0], 'full_ancestor_database_bytes': sizes[1],
                'observed_at': observed_at}
    except Exception:
        raise BuilderRefused('source_database_size_unproved') from None


def validate_size_facts(value, *, now=None):
    try:
        policy._keys(value, ('schema', 'source', 'transaction', 'generation_sha256', 'backup_label',
                            'full_ancestor_label', 'selected_database_bytes',
                            'full_ancestor_database_bytes', 'observed_at'))
        need(value['schema'] == SIZE_SCHEMA and value['source'] == 'pgbackrest_info'
             and type(value['transaction']) is str and re.fullmatch('[0-9a-f]{32}', value['transaction']))
        policy._digest(value['generation_sha256'])
        need(value['full_ancestor_label'] == plans._label(value['backup_label']))
        for field in ('selected_database_bytes', 'full_ancestor_database_bytes'):
            need(type(value[field]) is int and 0 < value[field] <= MAX_DATABASE_BYTES)
        if value['backup_label'] == value['full_ancestor_label']:
            need(value['selected_database_bytes'] == value['full_ancestor_database_bytes'])
        need(policy.timestamp(value['observed_at']) <= clock(now))
        return value
    except Exception:
        raise BuilderRefused('source_database_size_unproved') from None


def plan_from_facts(download, manifest, sizefacts, attempt=1, *, now=None):
    """Validate supplied claims and derive max(selected, full ancestor), not IO.

    Size facts bind an immutable generation, so old observation time is allowed;
    future time and unknown provenance are not. Native authentication remains the
    installed writer/entry's responsibility and recovery is a separate gate.
    """
    try:
        current = clock(now); validate_size_facts(sizefacts, now=current)
        bundle.validate_manifest(manifest)
        need(type(download) is dict and download.get('transaction') == sizefacts['transaction']
             and download.get('generation_sha256') == sizefacts['generation_sha256'] == manifest['generation_sha256']
             and download.get('backup_label') == sizefacts['backup_label'] == manifest['backup_label'])
        maximum = max(sizefacts['selected_database_bytes'], sizefacts['full_ancestor_database_bytes'])
        prepared = plans.plan(sizefacts['backup_label'], download['archive_sha256'],
            sizefacts['generation_sha256'], sizefacts['transaction'], maximum, attempt=attempt)
        policy._keys(download, ('schema', 'result', 'transaction', 'backup_label', 'generation_sha256',
            'archive_sha256', 'archive_bytes', 'manifest_sha256', 'native_restore_accepted',
            'completed_at', 'destination', 'quota_after'))
        need(download['schema'] == 'careertrellis.onedrive-bundle-download/v1'
             and download['result'] == 'download_verified' and download['native_restore_accepted'] is False
             and type(download['archive_bytes']) is int and 0 < download['archive_bytes'] <= bundle.MAX_BYTES
             and download['manifest_sha256'] == digest(manifest)
             and download['destination'] == native_restore.PREFIX + download['archive_sha256'] + '/repository.tar')
        policy._keys(download['quota_after'], ('total', 'free'))
        quota = download['quota_after']
        need(type(quota['total']) is int and type(quota['free']) is int
             and quota['total'] > 0 and 0 <= quota['free'] <= quota['total'])
        need(policy.timestamp(download['completed_at']) <= current)
        return prepared
    except Exception:
        raise BuilderRefused('native_plan_facts_unproved') from None


def decode(raw, maximum):
    need(type(raw) is bytes and 0 < len(raw) <= maximum)
    value = native_restore.decode(raw)
    need(bundle.canonical(value) == raw)
    return value


class NativeFiles:
    """Fixed DB-only root-private IO; no arbitrary source/output paths."""
    def __init__(self, transaction, attempt):
        need(type(transaction) is str and re.fullmatch('[0-9a-f]{32}', transaction)
             and type(attempt) is int and 1 <= attempt <= 9)
        need(os.name == 'posix' and os.geteuid() == os.getegid() == 0
             and socket.gethostname() == 'job-applier-db-staging-01')
        self.transaction, self.attempt = transaction, attempt
        self.root = Path(plans.ROOT) / transaction
        suffix = '' if attempt == 1 else '-%02d' % attempt
        self.output = 'native-plan' + suffix + '.json'

    def guard(self):
        need(Path(__file__).absolute() == entry.ROOT / 'builder.py')
        entry.verify_install()
        need(entry.retention_idle(self.transaction, 'restore') is True)

    def read(self, name):
        limits = {'downloaded-receipt.json': 16384, 'downloaded-manifest.json': bundle.MAX_MANIFEST,
                  'source-database-size.json': 8192, self.output: 131072}
        need(name in limits)
        return native_restore.protected(self.root / name, maximum=limits[name],
                                        modes=(0o600,), uids=(0,), gids=(0,))

    def write_once(self, name, raw):
        need(name == self.output)
        value = decode(raw, 131072); plans.validate_plan(value)
        need(value['transaction'] == self.transaction and value['attempt'] == self.attempt)
        self.guard()
        native_restore.exclusive(self.root / name, raw)


def write_plan(transaction, attempt=1, *, io=None, now=None):
    """Write one guarded immutable plan; injected IO is explicitly offline-only."""
    try:
        need(type(transaction) is str and re.fullmatch('[0-9a-f]{32}', transaction)
             and type(attempt) is int and 1 <= attempt <= 9)
        native = io is None
        if native: io = NativeFiles(transaction, attempt)
        else: need(getattr(io, 'mode', None) == 'offline-test')
        io.guard()
        names = ('downloaded-receipt.json', 'downloaded-manifest.json', 'source-database-size.json')
        limits = (16384, bundle.MAX_MANIFEST, 8192)
        raws = [io.read(name) for name in names]
        values = [decode(raw, limit) for raw, limit in zip(raws, limits)]
        prepared = plan_from_facts(*values, attempt, now=now)
        need(prepared['transaction'] == transaction)
        if native: native_restore.validate_download(values[0], values[1], prepared)
        io.guard()
        need([io.read(name) for name in names] == raws)
        suffix = '' if attempt == 1 else '-%02d' % attempt
        output = 'native-plan' + suffix + '.json'
        encoded = plans.canonical(prepared)
        io.write_once(output, encoded)
        need(io.read(output) == encoded)
        return {'schema': 'careertrellis.onedrive-native-plan-build/v1', 'result': 'plan_written',
                'transaction': transaction, 'attempt': attempt, 'plan_sha256': plans.digest(prepared),
                'maximum_database_bytes': prepared['maximum_database_bytes'],
                'source_database_size_facts_sha256': digest(values[2]),
                'native_file_write': native, 'native_restore_accepted': False, 'execution_authorized': False}
    except Exception:
        raise BuilderRefused('native_plan_write_requires_reconciliation') from None
