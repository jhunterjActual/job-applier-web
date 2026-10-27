"""Policy, durable local records and the public writer-start interlock.

No service, provider, network or process-control operation is performed here.
Journal replacement is atomic, not compare-and-swap; its caller must own the
transaction lock. An interrupted exclusive write is evidence, never replayable.
"""
import copy
import datetime as dt
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Refused(RuntimeError):
    def __init__(self, code):
        if not isinstance(code, str) or not re.fullmatch('[a-z][a-z0-9_]{0,79}', code):
            code = 'invalid_refusal_code'
        self.code = code
        super().__init__(code)


def _require(value, code):
    if not value:
        raise Refused(code)


def _json_types(value):
    if isinstance(value, dict):
        _require(all(isinstance(key, str) for key in value), 'record_encoding')
        for item in value.values():
            _json_types(item)
    elif isinstance(value, list):
        for item in value:
            _json_types(item)
    elif value is None or type(value) in (bool, int, str):
        return
    elif type(value) is float:
        _require(math.isfinite(value), 'record_encoding')
    else:
        raise Refused('record_encoding')


def canonical(value):
    _require(isinstance(value, dict), 'record_encoding')
    try:
        _json_types(value)
        return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=True, allow_nan=False) + '\n').encode('utf-8')
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise Refused('record_encoding') from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _timestamp(value):
    _require(isinstance(value, str) and len(value) <= 48, 'policy_timestamp')
    try:
        result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError, OverflowError):
        raise Refused('policy_timestamp') from None
    _require(result.tzinfo is not None and result.utcoffset() == dt.timedelta(0),
             'policy_timestamp')
    return result.astimezone(dt.timezone.utc)


def _clock(value):
    _require(isinstance(value, dt.datetime) and value.tzinfo is not None,
             'invalid_clock')
    try:
        _require(value.utcoffset() == dt.timedelta(0), 'invalid_clock')
        return value.astimezone(dt.timezone.utc)
    except (ValueError, OverflowError):
        raise Refused('invalid_clock') from None


def _text(value, limit=160):
    return (isinstance(value, str) and 0 < len(value) <= limit
            and all(ord(character) >= 32 and ord(character) != 127 for character in value))


def _revision(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) is not None


class Policy:
    REQUIRED = frozenset((
        'schema', 'enabled', 'owner', 'valid_from', 'valid_until',
        'application_release', 'application_build', 'ops_release', 'schema_revision',
        'timezone', 'weekday', 'start_hour', 'start_minute', 'start_window_minutes',
        'minimum_capture_age_seconds', 'max_capture_objects', 'max_capture_bytes',
        'minimum_free_bytes', 'minimum_onedrive_reserve_bytes', 'max_database_bytes',
        'planned_stop_budget_seconds', 'backup_wait_seconds',
        'source_readonly_attested', 'no_external_writers_attested',
        'retention_policy', 'approved_egress_ipv4_cidr'))
    OPTIONAL = frozenset(('artifact_hashes', 'manifest_revision',
                          'source_custody_reviewed_at', 'policy_label'))

    def __init__(self, data):
        _require(isinstance(data, dict) and self.REQUIRED <= set(data)
                 and set(data) <= self.REQUIRED | self.OPTIONAL, 'policy_fields')
        canonical(data)
        self._data = copy.deepcopy(data)
        value = self._data
        _require(value['schema'] == 'careertrellis.paired-maintenance-policy/v1',
                 'policy_schema')
        for key in ('enabled', 'source_readonly_attested', 'no_external_writers_attested'):
            _require(type(value[key]) is bool, 'policy_boolean')
        _require(_text(value['owner']), 'policy_owner')
        for key in ('application_release', 'ops_release'):
            _require(_revision(value[key]), 'policy_revision')
        _require(isinstance(value['application_build'], str) and
                 re.fullmatch(r'[0-9]{8}\.[0-9]{1,8}', value['application_build']),
                 'policy_build')
        _require(isinstance(value['schema_revision'], str) and
                 re.fullmatch('[0-9]{8}_[0-9]{4}', value['schema_revision']),
                 'policy_database_schema')
        self.valid_from = _timestamp(value['valid_from'])
        self.valid_until = _timestamp(value['valid_until'])
        _require(dt.timedelta(0) < self.valid_until - self.valid_from <= dt.timedelta(days=90),
                 'policy_validity')
        _require(_text(value['timezone'], 64), 'policy_timezone')
        try:
            self.timezone = ZoneInfo(value['timezone'])
        except (ZoneInfoNotFoundError, ValueError):
            raise Refused('policy_timezone') from None
        bounds = {'weekday': (0, 6), 'start_hour': (0, 23), 'start_minute': (0, 59),
                  'start_window_minutes': (1, 120),
                  # Weekly execution must not skip a midweek attended refresh
                  # and then exceed the independent eight-day freshness limit.
                  'minimum_capture_age_seconds': (1, 12 * 3600),
                  'max_capture_objects': (1, 100000),
                  'max_capture_bytes': (1, 256 * 1024 ** 2),
                  'minimum_free_bytes': (5 * 1024 ** 3, 1024 ** 5),
                  'minimum_onedrive_reserve_bytes': (64 * 1024 ** 2, 1024 ** 5),
                  'max_database_bytes': (1, 1024 ** 4),
                  'planned_stop_budget_seconds': (60, 7200),
                  'backup_wait_seconds': (60, 24 * 3600)}
        for key, (minimum, maximum) in bounds.items():
            _require(type(value[key]) is int and minimum <= value[key] <= maximum,
                     'policy_limit')
        _require(value['start_hour'] * 60 + value['start_minute']
                 + value['start_window_minutes'] <= 1440, 'policy_crosses_midnight')
        _require(value['backup_wait_seconds'] >= value['planned_stop_budget_seconds'],
                 'policy_wait_budget')
        retention = value['retention_policy']
        _require(isinstance(retention, dict)
                 and set(retention) == {'full', 'differential', 'archive', 'expire_auto'},
                 'policy_retention')
        _require(all(type(retention[key]) is int and 1 <= retention[key] <= 10000
                     for key in ('full', 'differential', 'archive'))
                 and type(retention['expire_auto']) is bool, 'policy_retention')
        try:
            egress = ipaddress.ip_network(value['approved_egress_ipv4_cidr'], strict=True)
        except (TypeError, ValueError):
            raise Refused('policy_egress') from None
        _require(isinstance(egress, ipaddress.IPv4Network) and egress.prefixlen == 32
                 and str(egress) == value['approved_egress_ipv4_cidr'], 'policy_egress')
        if 'manifest_revision' in value:
            _require(_revision(value['manifest_revision']), 'policy_revision')
        if 'source_custody_reviewed_at' in value:
            _require(_timestamp(value['source_custody_reviewed_at']) <= self.valid_until,
                     'policy_custody_time')
        if 'policy_label' in value:
            _require(isinstance(value['policy_label'], str) and
                     re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', value['policy_label']),
                     'policy_label')
        if 'artifact_hashes' in value:
            roles = value['artifact_hashes']
            _require(isinstance(roles, dict) and set(roles) <= {'web', 'db'},
                     'policy_artifact_hashes')
            for paths in roles.values():
                _require(isinstance(paths, dict) and len(paths) <= 128,
                         'policy_artifact_hashes')
                for path, expected in paths.items():
                    _require(isinstance(path, str) and path.startswith('/') and
                             len(path) <= 512 and '\\' not in path and
                             all(ord(character) >= 32 and ord(character) != 127 for character in path)
                             and '..' not in Path(path).parts and
                             isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected),
                             'policy_artifact_hashes')
        self.digest = digest(self._data)

    @property
    def data(self):
        return copy.deepcopy(self._data)

    def _boundary(self, naive):
        candidates = []
        for fold in (0, 1):
            local = naive.replace(tzinfo=self.timezone, fold=fold)
            instant = local.astimezone(dt.timezone.utc)
            roundtrip = instant.astimezone(self.timezone)
            if roundtrip.replace(tzinfo=None) == naive and roundtrip.fold == fold:
                candidates.append((instant, local.utcoffset()))
        _require(len(candidates) == 1, 'ambiguous_or_missing_window')
        return candidates[0]

    def require_new_start(self, now):
        now = _clock(now)
        value = self.data
        _require(value['enabled'], 'policy_disabled')
        _require(value['source_readonly_attested'] and value['no_external_writers_attested'],
                 'policy_custody_unapproved')
        _require(self.valid_from <= now < self.valid_until, 'policy_not_current')
        if 'source_custody_reviewed_at' in value:
            _require(_timestamp(value['source_custody_reviewed_at']) <= now,
                     'policy_custody_in_future')
        local = now.astimezone(self.timezone)
        _require(local.weekday() == value['weekday'], 'outside_start_window')
        start = dt.datetime.combine(local.date(), dt.time(value['start_hour'], value['start_minute']))
        end = start + dt.timedelta(minutes=value['start_window_minutes'])
        first, first_offset = self._boundary(start)
        last, last_offset = self._boundary(end)
        _require(first_offset == last_offset, 'window_clock_transition')
        _require(first <= now < last, 'outside_start_window')
        return local.strftime('%Y%m%d')

    def due(self, capture_started_at, now):
        now = _clock(now)
        captured = _timestamp(capture_started_at) if isinstance(capture_started_at, str) else _clock(capture_started_at)
        _require(captured <= now, 'capture_in_future')
        return (now - captured).total_seconds() >= self.data['minimum_capture_age_seconds']


class Journal:
    MAX_RECORD_BYTES = 131072

    def __init__(self, root, *, test_only=False):
        _require(type(test_only) is bool, 'journal_test_flag')
        self.root = Path(root).absolute()
        self.test_only = test_only
        _require('..' not in self.root.parts, 'journal_root')
        try:
            meta = self.root.lstat()
            self.identity = (meta.st_dev, meta.st_ino)
            self._check_root()
        except OSError:
            raise Refused('journal_root') from None

    def _check_root(self):
        try:
            for parent in (self.root, *self.root.parents):
                meta = parent.lstat()
                _require(stat.S_ISDIR(meta.st_mode) and not stat.S_ISLNK(meta.st_mode), 'journal_root')
                if not self.test_only:
                    _require(meta.st_uid == 0 and not meta.st_mode & 0o022, 'journal_root')
            meta = self.root.lstat()
            _require((meta.st_dev, meta.st_ino) == self.identity, 'journal_root_replaced')
            if not self.test_only:
                _require(stat.S_IMODE(meta.st_mode) == 0o700, 'journal_root')
        except OSError:
            raise Refused('journal_root') from None

    def _path(self, name):
        _require(isinstance(name, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,95}\.json', name),
                 'journal_name')
        self._check_root()
        return self.root / name

    def _metadata(self, meta):
        _require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1
                 and meta.st_size <= self.MAX_RECORD_BYTES, 'journal_record_metadata')
        if not self.test_only:
            _require(meta.st_uid == 0 and stat.S_IMODE(meta.st_mode) == 0o600,
                     'journal_record_metadata')

    def exists(self, name):
        path = self._path(name)
        try:
            self._metadata(path.lstat())
            return True
        except FileNotFoundError:
            return False
        except OSError:
            raise Refused('journal_io') from None

    def _raw(self, name):
        path = self._path(name)
        descriptor = None
        try:
            before = path.lstat()
            self._metadata(before)
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                                 | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
            opened = os.fstat(descriptor)
            self._metadata(opened)
            _require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino),
                     'journal_record_replaced')
            raw = os.read(descriptor, self.MAX_RECORD_BYTES + 1)
            _require(len(raw) == opened.st_size, 'journal_record_size')
            self._check_root()
            return raw
        except FileNotFoundError:
            raise Refused('record_missing') from None
        except OSError:
            raise Refused('journal_io') from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _decode(raw):
        def pairs(items):
            result = {}
            for key, value in items:
                _require(key not in result, 'record_invalid')
                result[key] = value
            return result
        def constant(_value):
            raise Refused('record_invalid')
        try:
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                               parse_constant=constant)
            _require(isinstance(value, dict), 'record_invalid')
            return value
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise Refused('record_invalid') from None

    def read(self, name):
        return self._decode(self._raw(name))

    def _fsync_root(self):
        self._check_root()
        if os.name == 'nt':
            return
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            _require((opened.st_dev, opened.st_ino) == self.identity, 'journal_root_replaced')
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write(descriptor, raw):
        offset = 0
        while offset < len(raw):
            amount = os.write(descriptor, raw[offset:])
            _require(amount > 0, 'journal_short_write')
            offset += amount
        os.fsync(descriptor)

    def write_once(self, name, value):
        path = self._path(name)
        raw = canonical(value)
        _require(len(raw) <= self.MAX_RECORD_BYTES, 'journal_record_size')
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
        except FileExistsError:
            existing = self._raw(name)
            _require(existing == raw, 'journal_collision')
            return self._decode(existing)
        except OSError:
            raise Refused('journal_io') from None
        try:
            self._metadata(os.fstat(descriptor))
            self._write(descriptor, raw)
        except OSError:
            raise Refused('journal_io') from None
        finally:
            os.close(descriptor)
        self._fsync_root()
        return self._decode(raw)

    def replace(self, name, value):
        _require(name == 'status.json', 'journal_replace_forbidden')
        path = self._path(name)
        raw = canonical(value)
        _require(len(raw) <= self.MAX_RECORD_BYTES, 'journal_record_size')
        self.exists(name)
        temporary = self.root / ('.status-' + uuid.uuid4().hex + '.tmp')
        descriptor = None
        created = None
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
            meta = os.fstat(descriptor)
            self._metadata(meta)
            created = (meta.st_dev, meta.st_ino)
            self._write(descriptor, raw)
            os.close(descriptor)
            descriptor = None
            self._check_root()
            self.exists(name)
            os.replace(temporary, path)
            self._fsync_root()
            return self._decode(raw)
        except OSError:
            raise Refused('journal_io') from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if created is not None:
                try:
                    self._check_root()
                    meta = temporary.lstat()
                    if (meta.st_dev, meta.st_ino) == created and stat.S_ISREG(meta.st_mode):
                        temporary.unlink()
                except (FileNotFoundError, OSError, Refused):
                    pass


def validate_gate(policy, marker, current_boot, current_release):
    """Validate a root-controlled public marker, never grant new-run authority."""
    try:
        policy = policy if isinstance(policy, Policy) else Policy(policy)
        _require(isinstance(marker, dict), 'gate_marker')
        _require(set(marker) <= {'schema', 'policy_sha256', 'boot_id', 'release',
                                'phase', 'transaction', 'updated_at'}, 'gate_marker')
        _require(marker.get('schema') == 'careertrellis.paired-maintenance-interlock/v1',
                 'gate_marker')
        _require(isinstance(current_boot, str) and re.fullmatch(
            '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', current_boot),
            'gate_boot')
        _require(_revision(current_release) and current_release == policy.data['application_release'],
                 'gate_release')
        _require(marker.get('policy_sha256') == policy.digest
                 and marker.get('boot_id') == current_boot
                 and marker.get('release') == current_release, 'gate_binding')
        _require(marker.get('phase') in ('idle', 'restore_allowed'), 'gate_blocked')
        if marker['phase'] == 'restore_allowed' or marker.get('transaction') is not None:
            _require(isinstance(marker.get('transaction'), str) and
                     re.fullmatch('[0-9a-f]{32}', marker['transaction']), 'gate_transaction')
        if 'updated_at' in marker:
            _timestamp(marker['updated_at'])
        return True
    except (Refused, TypeError, ValueError, AttributeError, OverflowError):
        return False
