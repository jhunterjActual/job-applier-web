"""Prepared UD/ZipRecruiter adapters; source-access review precedes activation.

No environment switch enables these providers. No persistent query/result cache,
credentials, implicit retries, or widget/application actions.
"""
from html.parser import HTMLParser
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from discovery_sources import Budget, NoRedirect, SourceError, bounded_terms
from candidate_filters import role_matches
from operations import OperationCancelled

ENABLED = ()  # Review source rights and detail path before enabling either source.
PROVIDERS = ('udayton', 'ziprecruiter')
UD_ORIGIN = 'https://employment.udayton.edu'
ZIP_ENDPOINT = 'https://api.ziprecruiter.com/mcp'
MAX_BYTES = 1024 * 1024
MAX_UD_PAGES = 5
MAX_ZIP_CALLS = 6


def canonical_source_url(url):
    if not isinstance(url, str) or len(url) > 2048 or re.search(r'[\s\\\x00-\x1f]', url):
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        if parsed.hostname == 'employment.udayton.edu' and re.fullmatch(r'/en-us/job/[0-9]{1,12}/[a-z0-9-]+/?', parsed.path):
            return urllib.parse.urlunsplit(('https', parsed.hostname, parsed.path.rstrip('/'), '', ''))
        if parsed.hostname in ('www.ziprecruiter.com', 'ziprecruiter.com'):
            ids = urllib.parse.parse_qs(parsed.query).get('jid', [])
            if len(ids) == 1 and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', ids[0]):
                # jid is identity; never drop it as generic tracking metadata.
                return urllib.parse.urlunsplit(('https', 'www.ziprecruiter.com', parsed.path or '/',
                                               urllib.parse.urlencode({'jid': ids[0]}), ''))
    except ValueError:
        pass
    return None


def _request(url, budget, payload=None):
    # Transport targets are fixed and redirect refusal prevents arbitrary egress.
    if url != ZIP_ENDPOINT and not re.fullmatch(
            re.escape(UD_ORIGIN) + r'/en-us/listing/\?page=[1-5]&page-items=20', url):
        raise SourceError('invalid_endpoint')
    timeout = budget.take()
    headers = {'User-Agent': 'CareerTrellis/1.0', 'Accept': 'application/json, text/event-stream, text/html'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    try:
        request = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            chunks, size = [], 0
            while size <= MAX_BYTES:
                budget.check_cancelled()
                if time.monotonic() >= budget.deadline:
                    raise SourceError('request_limit')
                chunk = getattr(response, 'read1', response.read)(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
        budget.check_cancelled()
        if size > MAX_BYTES:
            raise SourceError('oversized_response')
        if time.monotonic() >= budget.deadline:
            raise SourceError('request_limit')
        return b''.join(chunks).decode('utf-8')
    except (SourceError, OperationCancelled):
        raise
    except urllib.error.HTTPError as exc:
        raise SourceError('rate_limit' if exc.code == 429 else 'http_error') from None
    except (ValueError, UnicodeError):
        raise SourceError('invalid_response') from None
    except (OSError, urllib.error.URLError):
        raise SourceError('network') from None


class UDListing(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.seen = [], set()
        self.active, self.title = None, []
        self.complete = True

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get('class', '').split()
        if tag == 'a' and 'more-link' in classes:
            self.complete = False
        if tag == 'a' and 'job-link' in classes:
            self.active = canonical_source_url(urllib.parse.urljoin(UD_ORIGIN, attributes.get('href', '')))
            if not self.active or not self.active.startswith(UD_ORIGIN + '/'):
                raise SourceError('invalid_response')
            self.title = []

    def handle_data(self, data):
        if self.active:
            self.title.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.active:
            title = ' '.join(''.join(self.title).split())
            identity = self.active.split('/')[5]
            if not title or len(title) > 180:
                raise SourceError('invalid_response')
            if identity not in self.seen:
                self.seen.add(identity)
                self.rows.append({'title': title, 'url': self.active, 'snippet': '', 'verify_query': True})
            self.active = None


def parse_ud_listing(raw):
    parser = UDListing()
    parser.feed(raw)
    parser.close()
    if not parser.rows or len(parser.rows) > 20:
        # A changed template/challenge cannot be mistaken for valid empty results.
        raise SourceError('invalid_response')
    return parser.rows, parser.complete


def zip_arguments(role, location):
    args = {'job_role': role, 'offset': 0}
    if location.casefold() == 'remote':
        args['location_types'] = ['REMOTE']
    elif location:
        args['location'] = location
    return args


def parse_zip_response(raw, request_id):
    try:
        # Streamable HTTP may return a single JSON-RPC message as JSON or SSE.
        if raw.lstrip().startswith('data:') or raw.lstrip().startswith('event:'):
            messages = [json.loads(line[5:].strip()) for line in raw.splitlines() if line.startswith('data:')]
            matches = [item for item in messages if isinstance(item, dict) and item.get('id') == request_id]
            if len(matches) != 1:
                raise ValueError()
            envelope = matches[0]
        else:
            envelope = json.loads(raw)
        if (not isinstance(envelope, dict) or envelope.get('id') != request_id
                or envelope.get('jsonrpc') != '2.0' or 'error' in envelope):
            raise ValueError()
        result = envelope['result']
        if not isinstance(result, dict) or result.get('isError'):
            raise ValueError()
        data = result.get('structuredContent')
        if not isinstance(data, dict):
            raise ValueError()
        status, meta = data.get('status'), data.get('meta')
        rows = data.get('results', [])
        if (status not in ('OK', 'NO_RESULTS') or not isinstance(meta, dict)
                or not isinstance(rows, list) or len(rows) > 5
                or type(meta.get('count')) is not int or meta['count'] != len(rows)
                or type(meta.get('total')) is not int or meta['total'] < len(rows)
                or meta.get('limit') != 5 or (status == 'NO_RESULTS' and rows)):
            raise ValueError()
        candidates = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError()
            url = canonical_source_url(row.get('job_redirect_url'))
            if (not url or not url.startswith('https://www.ziprecruiter.com/')
                    or any(not isinstance(row.get(key), str) or not row[key].strip() for key in ('title', 'company', 'location'))
                    or type(row.get('is_remote')) is not bool):
                raise ValueError()
            candidates.append({'title': row['title'][:180], 'url': url, 'snippet': '',
                               'verify_query': True, 'feed_metadata': {
                                   'url': url, 'location': row['location'][:240],
                                   'work_arrangement': 'remote' if row['is_remote'] else '',
                                   'employment_type': ''}})
        # Full details remain independently inspected, never manufactured from cards.
        return candidates, meta['total'] > len(rows) or bool(data.get('warnings'))
    except (ValueError, TypeError, KeyError):
        raise SourceError('invalid_response') from None


def collect(keywords, locations, health, check_cancelled):
    if not ENABLED:
        return []
    terms, term_limited = bounded_terms(keywords, 3)
    places, place_limited = bounded_terms(locations, 2)
    places = places or ['']
    budget, found, seen = Budget(check_cancelled), [], set()
    for provider in ENABLED:
        if provider not in PROVIDERS or provider not in health:
            raise SourceError('invalid_catalog')
        info = health[provider]
        info['source_attempts'] = info.get('source_attempts', 0) + 1
        partial = info.setdefault('partial_results', {})
        if term_limited or place_limited:
            partial['planning_limits'] = 1
        selected, failed = [], False
        try:
            if provider == 'udayton':
                for page in range(1, MAX_UD_PAGES + 1):
                    rows, complete = parse_ud_listing(_request(
                        f'{UD_ORIGIN}/en-us/listing/?page={page}&page-items=20', budget))
                    selected.extend(row for row in rows if role_matches(row['title'], terms))
                    if complete:
                        break
                if not complete:
                    partial['catalog_page_limits'] = 1
            else:
                calls = 0
                for role in terms:
                    for place in places:
                        calls += 1
                        if calls > MAX_ZIP_CALLS:
                            raise SourceError('request_limit')
                        body = {'jsonrpc': '2.0', 'id': calls, 'method': 'tools/call',
                                'params': {'name': 'search_jobs', 'arguments': zip_arguments(role, place)}}
                        rows, limited = parse_zip_response(_request(ZIP_ENDPOINT, budget, body), calls)
                        selected.extend(row for row in rows if role_matches(row['title'], terms))
                        if limited:
                            partial['catalog_page_limits'] = 1
        except SourceError:
            failed = True
            info['source_failures'] = info.get('source_failures', 0) + 1
        if not selected and not failed:
            info['source_empty'] = info.get('source_empty', 0) + 1
        for row in selected:
            check_cancelled()
            info['raw_candidates'] = info.get('raw_candidates', 0) + 1
            if row['url'] not in seen:
                seen.add(row['url'])
                found.append(row)
                info['valid_discovered'] = info.get('valid_discovered', 0) + 1
    return found
