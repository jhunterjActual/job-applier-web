"""Bounded, shared public listings. No personal queries or credentials leave this module."""
import contextlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from discovery_sources import SourceError, NoRedirect, bounded_terms, matches
from operations import OperationCancelled

PROVIDERS = ('weworkremotely', 'jobicy')
ENDPOINTS = {
    'weworkremotely': 'https://weworkremotely.com/remote-jobs.rss',
    'jobicy': 'https://jobicy.com/api/v2/remote-jobs?count=200',
}
MAX_BYTES = 4 * 1024 * 1024
MAX_CACHE_BYTES = 8 * MAX_BYTES
REFRESH_SECONDS = 6 * 60 * 60
FAILURE_RETRY_SECONDS = 60 * 60
MAX_ROWS = 2000
HOSTED_CACHE_DIRECTORY = Path('/var/lib/careertrellis/public-job-feeds')


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if tag in ('p', 'div', 'li'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    if not isinstance(value, str):
        return ''
    parser = _Text()
    parser.feed(value)
    parser.close()
    return '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())


def canonical_url(url):
    if not isinstance(url, str) or len(url) > 2048 or re.search(r'[\s\\\x00-\x1f]', url):
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port:
            return None
        host = parsed.hostname
        path = parsed.path.rstrip('/')
        patterns = {'weworkremotely.com': r'/remote-jobs/[a-zA-Z0-9][a-zA-Z0-9-]*',
                    'jobicy.com': r'/jobs/[0-9]+-[a-zA-Z0-9][a-zA-Z0-9-]*'}
        if host not in patterns or not re.fullmatch(patterns[host], path):
            return None
        return 'https://' + host + path
    except ValueError:
        return None


def provider_for_url(url):
    canonical = canonical_url(url)
    return ('jobicy' if canonical.startswith('https://jobicy.com/') else 'weworkremotely') if canonical else None


def _row(provider, title, url, company, location, description, employment_type=''):
    canonical = canonical_url(url)
    if not canonical or provider_for_url(canonical) != provider or not plain(title) or not plain(description):
        raise SourceError('invalid_response')
    title, company = plain(title), plain(company)
    if len(title) > 500 or len(company) > 500:
        raise SourceError('invalid_response')
    return {'title': title, 'url': canonical, 'company': company,
            'location': plain(location) or 'Remote — geographic eligibility not specified',
            'description': plain(description), 'employment_type': plain(employment_type),
            'work_arrangement': 'remote', 'remote': True, 'compensation': '',
            'source': provider}


def parse(provider, payload):
    if not isinstance(payload, str) or len(payload.encode('utf-8')) > MAX_BYTES:
        raise SourceError('oversized_response')
    rows = []
    try:
        if provider == 'jobicy':
            document = json.loads(payload)
            items = document.get('jobs') if isinstance(document, dict) else None
            if not isinstance(items, list) or len(items) > 200:
                raise SourceError('invalid_response')
            for item in items:
                if not isinstance(item, dict):
                    raise SourceError('invalid_response')
                types = item.get('jobType', [])
                if isinstance(types, list) and all(isinstance(t, str) for t in types):
                    types = ', '.join(types)
                rows.append(_row(provider, item.get('jobTitle'), item.get('url'), item.get('companyName'),
                                 item.get('jobGeo'), item.get('jobDescription'), types))
        elif provider == 'weworkremotely':
            # Reject DTD/entity declarations before ElementTree can expand entities.
            if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', payload, re.I):
                raise SourceError('invalid_response')
            document = ET.fromstring(payload)
            channel = document.find('channel')
            if document.tag != 'rss' or channel is None:
                raise SourceError('invalid_response')
            items = channel.findall('item')
            if len(items) > MAX_ROWS:
                raise SourceError('oversized_response')
            for item in items:
                title = item.findtext('title', '')
                company, separator, role = title.partition(': ')
                region = ' | '.join(dict.fromkeys(value for value in (
                    item.findtext('region', ''), item.findtext('country', ''),
                    item.findtext('state', '')) if value))
                rows.append(_row(provider, role if separator else title, item.findtext('link'),
                                 company if separator else '', region, item.findtext('description'),
                                 item.findtext('type', '')))
        else:
            raise SourceError('invalid_endpoint')
    except (ET.ParseError, ValueError, TypeError, AttributeError):
        raise SourceError('invalid_response') from None
    return list({row['url']: row for row in rows}.values())


def _directory():
    import config
    hosted = config.get_runtime_environment() != 'local'
    directory = HOSTED_CACHE_DIRECTORY if hosted else Path(config.DATA_DIR) / 'public-job-feeds'
    if directory.is_symlink():
        raise SourceError('cache_unavailable')
    if hosted:
        if not directory.is_dir():
            raise SourceError('cache_unavailable')
    else:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


@contextlib.contextmanager
def _lock(directory, provider):
    path = directory / (provider + '.lock')
    if path.is_symlink():
        raise SourceError('cache_unavailable')
    stream = open(path, 'a+b')
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield
    except OSError:
        raise SourceError('cache_unavailable') from None
    finally:
        if acquired:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


def _read(path, provider, now):
    if path.is_symlink():
        raise SourceError('cache_unavailable')
    if not path.exists():
        return None
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_CACHE_BYTES + 1)
        if len(raw) > MAX_CACHE_BYTES:
            raise ValueError()
        record = json.loads(raw)
        stamp = record['attempted_at']
        succeeded = record['succeeded_at']
        if (record.get('version') != 1 or record.get('provider') != provider or
                isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or
                not math.isfinite(stamp) or stamp < 0 or stamp > now or
                (succeeded is not None and (succeeded != stamp or not isinstance(record['payload'], str))) or
                (succeeded is None and record['payload'] is not None)):
            raise ValueError()
        return record
    except (OSError, ValueError, KeyError, TypeError):
        raise SourceError('cache_unavailable') from None


def _write(path, record):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Public snapshots are readable by the web group; only worker may write.
        # Never relax permissions on private DATA_DIR files or process umask.
        if os.name != 'nt' and path.parent == HOSTED_CACHE_DIRECTORY:
            os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _fetch(provider, check_cancelled):
    request = urllib.request.Request(ENDPOINTS[provider], headers={
        'Accept': 'application/json' if provider == 'jobicy' else 'application/rss+xml',
        'User-Agent': 'CareerTrellis/1.0 (public job feed reader)'})
    deadline = time.monotonic() + 30
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            if response.status != 200:
                raise SourceError('http_error')
            chunks, size = [], 0
            while True:
                check_cancelled()
                if time.monotonic() >= deadline:
                    raise SourceError('request_limit')
                chunk = getattr(response, 'read1', response.read)(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise SourceError('oversized_response')
        check_cancelled()
        return b''.join(chunks).decode('utf-8-sig')
    except (OperationCancelled, SourceError):
        raise
    except (UnicodeError, ValueError):
        raise SourceError('invalid_response') from None
    except urllib.error.HTTPError:
        raise SourceError('http_error') from None
    except OSError:
        raise SourceError('network') from None


def _snapshot(provider, check_cancelled, refresh=True):
    check_cancelled()
    try:
        directory = _directory()
        if not refresh:
            # Atomic replacement permits a reader without a writable lock file.
            # This is required for the hosted web service's read-only mount.
            now = time.time()
            record = _read(directory / (provider + '.json'), provider, now)
            if not record or record['succeeded_at'] is None or now - record['succeeded_at'] >= REFRESH_SECONDS:
                raise SourceError('snapshot_expired')
            return parse(provider, record['payload'])
        with _lock(directory, provider):
            now = time.time()
            path = directory / (provider + '.json')
            record = _read(path, provider, now)
            if record and now - record['attempted_at'] < (REFRESH_SECONDS if record['succeeded_at'] is not None else FAILURE_RETRY_SECONDS):
                if record['succeeded_at'] is None:
                    raise SourceError('refresh_cooldown')
                return parse(provider, record['payload'])
            if not refresh:
                raise SourceError('snapshot_expired')
            check_cancelled()
            # Persist reservation before transport, including cancellation/failure.
            record = {'version': 1, 'provider': provider, 'attempted_at': now,
                      'succeeded_at': None, 'payload': None}
            _write(path, record)
            payload = _fetch(provider, check_cancelled)
            rows = parse(provider, payload)
            check_cancelled()
            record.update(succeeded_at=now, payload=payload)
            _write(path, record)
            return rows
    except OSError:
        raise SourceError('cache_unavailable') from None


def collect(keywords, locations, health, check_cancelled):
    keywords, keyword_limit = bounded_terms(keywords, 3)
    locations, location_limit = bounded_terms(locations, 2)
    if not keywords:
        return []
    candidates = []
    for provider in PROVIDERS:
        check_cancelled()
        item = health.setdefault(provider, {})
        def count(key):
            item[key] = item.get(key, 0) + 1
        count('source_attempts')
        if keyword_limit or location_limit:
            partial = item.setdefault('partial_results', {})
            partial['catalog_term_limits'] = partial.get('catalog_term_limits', 0) + 1
        try:
            rows = _snapshot(provider, check_cancelled)
            selected = [row for row in rows if matches(row, keywords, locations or [''])]
            if not selected:
                count('source_empty')
            for row in selected:
                check_cancelled()
                candidates.append({'title': row['title'], 'url': row['url'],
                                   'snippet': row['location'] + ' | ' + row['source']})
                count('raw_candidates')
                count('valid_discovered')
        except SourceError as error:
            count('source_failures')
            if error.code in ('invalid_response', 'oversized_response'):
                count('source_unusable')
    return candidates


def details_for_url(url, check_cancelled=lambda: None):
    canonical = canonical_url(url)
    if not canonical:
        return None
    try:
        rows = _snapshot(provider_for_url(canonical), check_cancelled, refresh=False)
    except SourceError:
        return None
    return next((dict(row) for row in rows if row['url'] == canonical), None)
