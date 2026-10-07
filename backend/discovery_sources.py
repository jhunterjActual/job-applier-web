"""Bounded public-feed and Serper acquisition; no profile or credential logging."""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from collections import OrderedDict, deque

from operations import OperationCancelled


CATALOG = (
    ("lever", "tekton"), ("greenhouse", "clear"), ("ashby", "coder"),
    ("greenhouse", "springhealth66"), ("greenhouse", "sothebys"),
    ("greenhouse", "givedirectly"), ("lever", "rocketship"),
    ("lever", "weloglobal"), ("smartrecruiters", "BoschGroup"),
    ("smartrecruiters", "AccorHotel"), ("smartrecruiters", "Eurofins"),
    ("smartrecruiters", "HMGroup"), ("smartrecruiters", "AECOM2"),
    ("smartrecruiters", "NorthwesternMedicine"), ("greenhouse", "spacex"),
)
MAX_BYTES = 1024 * 1024
MAX_REQUESTS = 45
LARGE_BOARD_MAX_BYTES = 4 * MAX_BYTES
LEVER_PAGE_SIZE = 20
LEVER_MAX_PAGES = 3
SMARTRECRUITERS_PAGE_SIZE = 100
SMARTRECRUITERS_MAX_PAGES = 2
CACHE_TTL_SECONDS = 300
CACHE_MAX_BYTES = 16 * MAX_BYTES
CACHE_MAX_ENTRIES = 32
_PAGE_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
MAX_SERPER_REQUESTS = 6
MAX_SECONDS = 90
HTTP_TIMEOUT = 12
MAX_KEYWORDS = 3
MAX_LOCATIONS = 2
MAX_TERM_CHARS = 120
SERPER_ENABLED = False  # Requires a reviewed code change after renewed approval.


class SourceError(Exception):
    """Only fixed classifications cross the acquisition boundary."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SourceError("redirect")


def bounded_terms(values, maximum):
    result = []
    limited = False
    for value in values:
        if not isinstance(value, str):
            limited = True
            continue
        term = " ".join(value.split())
        if not term or not re.search(r"\w", term):
            continue
        if len(term) > MAX_TERM_CHARS:
            limited = True
            continue
        if term.casefold() in {item.casefold() for item in result}:
            continue
        if len(result) >= maximum:
            limited = True
            continue
        result.append(term)
    return result, limited


@dataclass
class Budget:
    check_cancelled: object
    requests: int = 0
    serper_requests: int = 0
    deadline: float = 0

    def __post_init__(self):
        self.deadline = time.monotonic() + MAX_SECONDS

    def take(self, paid=False):
        self.check_cancelled()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.requests >= MAX_REQUESTS:
            raise SourceError("request_limit")
        if paid and self.serper_requests >= MAX_SERPER_REQUESTS:
            raise SourceError("request_limit")
        self.requests += 1
        self.serper_requests += int(paid)
        return min(HTTP_TIMEOUT, remaining)


def fetch_json(url, budget, *, api_key=None, payload=None, reserve=None, max_bytes=MAX_BYTES):
    if max_bytes not in (MAX_BYTES, LARGE_BOARD_MAX_BYTES) or (
            max_bytes > MAX_BYTES and url != "https://boards-api.greenhouse.io/v1/boards/spacex/jobs"):
        raise SourceError("invalid_endpoint")
    paid = api_key is not None
    if (paid or urllib.parse.urlsplit(url).hostname == "google.serper.dev") and not SERPER_ENABLED:
        raise SourceError("serper_disabled")
    timeout = budget.take(paid)
    if paid:
        if url != "https://google.serper.dev/search":
            raise SourceError("invalid_endpoint")
        # Reservation is durable and happens before transport, including failures.
        if not api_key or reserve is None or not reserve():
            raise SourceError("quota_unavailable")
        budget.check_cancelled()
        remaining = budget.deadline - time.monotonic()
        if remaining <= 0:
            raise SourceError("request_limit")
        timeout = min(HTTP_TIMEOUT, remaining)
    headers = {"Accept": "application/json", "User-Agent": "CareerTrellis/1.0"}
    data = None
    if paid:
        headers["X-API-KEY"] = api_key
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            chunks = []
            size = 0
            while size <= max_bytes:
                budget.check_cancelled()
                if time.monotonic() >= budget.deadline:
                    raise SourceError("request_limit")
                chunk = getattr(response, "read1", response.read)(min(65536, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            raw = b"".join(chunks)
        budget.check_cancelled()
        if time.monotonic() > budget.deadline:
            raise SourceError("request_limit")
        if len(raw) > max_bytes:
            raise SourceError("oversized_response")
        return json.loads(raw)
    except (OperationCancelled, SourceError):
        raise
    except urllib.error.HTTPError as exc:
        # No in-run retry, including 429: never retry before Retry-After.
        code = "rate_limit" if exc.code == 429 else "authentication" if exc.code in (401, 403) else "http_error"
        raise SourceError(code) from None
    except (ValueError, UnicodeError):
        raise SourceError("invalid_response") from None
    except (OSError, urllib.error.URLError):
        raise SourceError("network") from None


class FeedRows(list):
    """Bounded page; complete means this board/query has no remaining rows."""

    def __init__(self, rows=(), *, complete=True, query_index=None, advertised_total=None, from_cache=False):
        super().__init__(rows)
        self.complete = complete
        self.query_index = query_index
        self.advertised_total = advertised_total
        self.from_cache = from_cache


def clear_feed_cache():
    with _CACHE_LOCK:
        _PAGE_CACHE.clear()


def _cached_document(key, budget):
    budget.check_cancelled()
    now = time.monotonic()
    if now >= budget.deadline:
        raise SourceError("request_limit")
    if key is None:
        return None
    with _CACHE_LOCK:
        for item in [item for item, (expires, _) in _PAGE_CACHE.items() if expires <= now]:
            del _PAGE_CACHE[item]
        cached = _PAGE_CACHE.get(key)
        if cached is None:
            return None
        _PAGE_CACHE.move_to_end(key)
        raw = cached[1]
    return json.loads(raw)  # Return a fresh copy, never shared mutable rows.


def _cache_document(key, document):
    if key is None:
        return
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    if len(raw) > CACHE_MAX_BYTES:
        return
    with _CACHE_LOCK:
        _PAGE_CACHE.pop(key, None)
        while _PAGE_CACHE and (len(_PAGE_CACHE) >= CACHE_MAX_ENTRIES or
                sum(len(item[1]) for item in _PAGE_CACHE.values()) + len(raw) > CACHE_MAX_BYTES):
            _PAGE_CACHE.popitem(last=False)
        _PAGE_CACHE[key] = (time.monotonic() + CACHE_TTL_SECONDS, raw)


def feed_rows(provider, board, budget, *, page_index=None, keywords=()):
    if (provider, board) not in CATALOG:
        raise SourceError("invalid_catalog")
    terms, _ = bounded_terms(keywords, MAX_KEYWORDS)
    if provider == "smartrecruiters" and not terms:
        raise SourceError("missing_keyword")
    max_pages = (LEVER_MAX_PAGES if provider == "lever" else
                 SMARTRECRUITERS_MAX_PAGES * len(terms) if provider == "smartrecruiters" else 1)
    if page_index is None:
        result, completed = [], set()
        for index in range(max_pages):
            query_index = index % len(terms) if provider == "smartrecruiters" else 0
            if query_index in completed:
                continue
            page = feed_rows(provider, board, budget, page_index=index, keywords=terms)
            result.extend(page)
            if page.complete:
                completed.add(query_index)
        expected = len(terms) if provider == "smartrecruiters" else 1
        return FeedRows(result, complete=len(completed) == expected)
    if type(page_index) is not int or not 0 <= page_index < max_pages:
        raise SourceError("page_limit")
    # Only fixed unfiltered public pages enter the cross-user process cache.
    # Search terms and SmartRecruiters query responses are never cached.
    key = (provider, board, page_index) if provider != "smartrecruiters" else None
    document = _cached_document(key, budget)
    from_cache = document is not None
    if provider == "lever":
        url = f"https://api.lever.co/v0/postings/{board}?mode=json&limit={LEVER_PAGE_SIZE}&skip={page_index * LEVER_PAGE_SIZE}"
    elif provider == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
    elif provider == "ashby":
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
    else:
        query_index = page_index % len(terms)
        offset = page_index // len(terms) * SMARTRECRUITERS_PAGE_SIZE
        query = urllib.parse.urlencode({"q": terms[query_index], "limit": SMARTRECRUITERS_PAGE_SIZE,
                                       "offset": offset, "destination": "PUBLIC"})
        url = f"https://api.smartrecruiters.com/v1/companies/{board}/postings?{query}"
    if not from_cache:
        if provider == "greenhouse" and board == "spacex":
            document = fetch_json(url, budget, max_bytes=LARGE_BOARD_MAX_BYTES)
        else:
            document = fetch_json(url, budget)
    if provider == "lever":
        if not isinstance(document, list) or len(document) > LEVER_PAGE_SIZE:
            raise SourceError("invalid_response")
        result = FeedRows(document, complete=len(document) < LEVER_PAGE_SIZE)
    elif provider == "smartrecruiters":
        if not isinstance(document, dict) or not isinstance(document.get("content"), list):
            raise SourceError("invalid_response")
        rows, total = document["content"], document.get("totalFound")
        if (type(total) is not int or total < 0 or len(rows) > SMARTRECRUITERS_PAGE_SIZE or
                type(document.get("offset")) is not int or document["offset"] != offset or
                type(document.get("limit")) is not int or document["limit"] != SMARTRECRUITERS_PAGE_SIZE or
                (rows and offset + len(rows) > total) or (not rows and offset < total)):
            raise SourceError("invalid_response")
        result = FeedRows(rows, complete=offset + len(rows) >= total, query_index=query_index,
                          advertised_total=total)
    else:
        if not isinstance(document, dict) or not isinstance(document.get("jobs"), list):
            raise SourceError("invalid_response")
        rows = document["jobs"]
        if len(rows) > (5000 if board == "spacex" else 1000):
            raise SourceError("page_limit")
        if provider == "greenhouse":
            meta = document.get("meta")
            if not isinstance(meta, dict) or type(meta.get("total")) is not int or meta["total"] != len(rows):
                raise SourceError("invalid_response")
        result = FeedRows(rows)
        if provider == "greenhouse":
            result.advertised_total = meta["total"]
    result.from_cache = from_cache
    # Cache only validated successes. Never serve stale data after a failed refresh.
    for row in result:
        budget.check_cancelled()
        feed_candidate(provider, board, row)
    if not from_cache:
        _cache_document(key, document)
    return result


def feed_candidate(provider, board, row):
    if (provider, board) not in CATALOG or not isinstance(row, dict):
        raise SourceError("invalid_response")
    arrangement, employment = "", ""
    if provider == "lever":
        title, url = row.get("text"), row.get("hostedUrl")
        categories = row.get("categories")
        if not isinstance(categories, dict):
            raise SourceError("invalid_response")
        location = categories.get("location", "")
        other_locations = categories.get("allLocations", [])
        if not isinstance(location, str) or not isinstance(other_locations, list) or any(
                not isinstance(value, str) for value in other_locations):
            raise SourceError("invalid_response")
        location = "; ".join(dict.fromkeys([location, *other_locations]))
        remote = row.get("workplaceType") == "remote"
    elif provider == "greenhouse":
        title, url = row.get("title"), row.get("absolute_url")
        place = row.get("location")
        if not isinstance(place, dict):
            raise SourceError("invalid_response")
        location, remote = place.get("name", ""), False
    elif provider == "smartrecruiters":
        title, posting_id = row.get("name"), row.get("id")
        company, place = row.get("company"), row.get("location")
        if (not isinstance(posting_id, str) or not re.fullmatch(r"[0-9]{1,30}", posting_id) or
                not isinstance(company, dict) or not isinstance(company.get("identifier"), str) or
                company["identifier"].casefold() != board.casefold() or not isinstance(place, dict)):
            raise SourceError("invalid_response")
        values = [place.get(field, "") for field in ("city", "region", "country")]
        if any(not isinstance(value, str) for value in values):
            raise SourceError("invalid_response")
        location, remote = ", ".join(value for value in values if value), place.get("remote") is True
        arrangement = "remote" if remote else "hybrid" if place.get("hybrid") is True else ""
        employment_info = row.get("typeOfEmployment")
        if isinstance(employment_info, dict):
            label = employment_info.get("label")
            if isinstance(label, str):
                employment = {"full-time": "full_time", "part-time": "part_time",
                              "contract": "contract", "temporary": "temporary",
                              "internship": "internship"}.get(label.strip().casefold(), "")
        url = row.get("postingUrl", f"https://jobs.smartrecruiters.com/{board}/{posting_id}")
        ref = row.get("ref")
        if ref is not None:
            if not isinstance(ref, str):
                raise SourceError("invalid_response")
            try:
                ref_parts = urllib.parse.urlsplit(ref)
                expected_paths = {f"/{version}/companies/{board}/postings/{posting_id}".casefold()
                                  for version in ("v1", "api-v1")}
                if (ref_parts.scheme != "https" or ref_parts.netloc != "api.smartrecruiters.com" or
                        ref_parts.path.casefold() not in expected_paths or ref_parts.query or ref_parts.fragment):
                    raise SourceError("invalid_response")
            except (TypeError, ValueError):
                raise SourceError("invalid_response") from None
    else:
        if type(row.get("isListed")) is not bool:
            raise SourceError("invalid_response")
        if not row["isListed"]:
            return None
        title, url = row.get("title"), row.get("jobUrl")
        location, remote = row.get("location", ""), row.get("isRemote") is True
    if not all(isinstance(value, str) for value in (title, url, location)) or not title.strip():
        raise SourceError("invalid_response")
    # Never admit another employer's URL from a board response.
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        raise SourceError("invalid_response") from None
    expected = {"lever": {"jobs.lever.co"}, "greenhouse": {"boards.greenhouse.io", "job-boards.greenhouse.io"},
                "ashby": {"jobs.ashbyhq.com"}, "smartrecruiters": {"jobs.smartrecruiters.com"}}
    parts = parsed.path.split("/")
    board_matches = len(parts) >= 3 and (parts[1].casefold() == board.casefold() if provider == "smartrecruiters" else parts[1] == board)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.netloc not in expected[provider] or not board_matches:
        raise SourceError("invalid_response")
    if provider == "smartrecruiters" and (len(parts) != 3 or parts[2].split("-", 1)[0] != posting_id):
        raise SourceError("invalid_response")
    # These are expressions of interest rather than an advertised current opening.
    if re.search(r"\b(talent (?:pool|community|network)|future opportunities|expression(?:s)? of interest)(?:\s*[-–:|(]|\s*$)", title, re.I):
        return None
    return {"title": title, "url": url, "location": location, "remote": remote,
            "work_arrangement": arrangement or ("remote" if remote else ""),
            "employment_type": employment}


def matches(row, keywords, locations):
    from candidate_filters import role_matches, location_matches
    return role_matches(row['title'], keywords) and location_matches(row, locations)


def collect(keywords, locations, health, check_cancelled, *, api_key=None, reserve=None, feeds_only=False):
    # Imported at call time to preserve the existing URL-validation boundary.
    from searcher import canonicalize_job_url, is_specific_job_url, provider_for_url

    budget = Budget(check_cancelled)
    candidates, seen = [], set()

    def record(provider, field, amount=1):
        item = health[provider]
        item[field] = item.get(field, 0) + amount

    def admit(url, expected=None, feed=None):
        canonical = canonicalize_job_url(url)
        if not canonical:
            return False
        provider = provider_for_url(canonical)
        if provider not in health or (expected and provider != expected) or not is_specific_job_url(canonical):
            return False
        if canonical not in seen:
            seen.add(canonical)
            candidate = {"title": "Job Posting", "url": canonical, "snippet": ""}
            if feed is not None:
                candidate['verify_query'] = True
                # Bound public metadata to the independently inspected posting.
                candidate["feed_metadata"] = {"url": canonical, **{
                    key: " ".join(feed[key].split())[:240]
                    for key in ("location", "work_arrangement", "employment_type")}}
            candidates.append(candidate)
            record(provider, "valid_discovered")
        else:
            record(provider, "coverage_duplicate_candidates")
        return True

    keywords, term_limited = bounded_terms(keywords, MAX_KEYWORDS)
    locations, location_limited = bounded_terms(locations, MAX_LOCATIONS)
    locations = locations or [""]

    def partial(provider, field):
        item = health[provider].setdefault("partial_results", {})
        item[field] = item.get(field, 0) + 1

    states = [{"provider": provider, "board": board, "completed": set(),
               "selected": deque(), "unknown": deque(), "attempted": False, "failed": False, "limited": False}
              for provider, board in CATALOG]
    if term_limited or location_limited:
        for provider in {item["provider"] for item in states}:
            partial(provider, "catalog_term_limits")
    if not keywords:
        return candidates
    # Each employer gets its first page before any employer's second page.
    # Slow/failed providers still share one wall-clock bound, not 15 independent
    # deadlines. Unattempted employers are explicitly incomplete when it expires.
    for page_index in range(max(LEVER_MAX_PAGES, SMARTRECRUITERS_MAX_PAGES * len(keywords))):
        for state in states:
            check_cancelled()
            provider, board = state["provider"], state["board"]
            query_index = page_index % len(keywords) if provider == "smartrecruiters" else 0
            max_pages = (LEVER_MAX_PAGES if provider == "lever" else
                         SMARTRECRUITERS_MAX_PAGES * len(keywords) if provider == "smartrecruiters" else 1)
            if state["failed"] or page_index >= max_pages or query_index in state["completed"]:
                continue
            if time.monotonic() >= budget.deadline or budget.requests >= MAX_REQUESTS:
                state["failed"] = state["limited"] = True
                partial(provider, "catalog_request_limits")
                continue
            if not state["attempted"]:
                state["attempted"] = True
                record(provider, "source_attempts")
            try:
                before_requests = budget.requests
                try:
                    page = feed_rows(provider, board, budget, page_index=page_index, keywords=keywords)
                finally:
                    record(provider, 'coverage_http_attempts', budget.requests - before_requests)
                record(provider, 'coverage_pages_read')
                record(provider, 'coverage_cached_pages', int(getattr(page, 'from_cache', False)))
                record(provider, 'coverage_rows_read', len(page))
                # First page per query only; overlapping query totals are NOT unique inventory.
                if page_index < (len(keywords) if provider == 'smartrecruiters' else 1):
                    total = getattr(page, 'advertised_total', None)
                    if type(total) is int and total >= 0:
                        record(provider, 'coverage_queries_with_total')
                        record(provider, 'coverage_advertised_rows', total)
                rows = [feed_candidate(provider, board, row) for row in page]
                from candidate_filters import role_matches, needs_location_details
                for row in rows:
                    if not row:
                        record(provider, 'coverage_unlisted_rows')
                    elif not role_matches(row['title'], keywords):
                        record(provider, 'coverage_role_excluded')
                    elif matches(row, keywords, locations):
                        record(provider, 'coverage_metadata_matches')
                        state['selected'].append(row)
                    elif needs_location_details(row, locations):
                        record(provider, 'coverage_location_unknown')
                        # Unknown metadata cannot consume every detail slot.
                        if len(state['unknown']) < 2:
                            state['unknown'].append(row)
                        else:
                            partial(provider, 'unknown_location_limit')
                    else:
                        record(provider, 'coverage_location_excluded')
                if getattr(page, "complete", True):
                    state["completed"].add(query_index)
            except SourceError as error:
                state["failed"] = True
                record(provider, 'coverage_failed_boards')
                record(provider, "source_failures")
                if error.code == "request_limit":
                    state["limited"] = True
                    partial(provider, "catalog_request_limits")
    for state in states:
        provider = state["provider"]
        expected = len(keywords) if provider == "smartrecruiters" else 1
        if not state["failed"] and len(state["completed"]) != expected:
            partial(provider, "catalog_page_limits")
        elif not state['failed']:
            record(provider, 'coverage_complete_boards')
            if not state["selected"] and not state['unknown']:
                record(provider, "source_empty")
    # Interleave employers before the existing per-provider downstream cap, so a
    # large early board cannot take every detail-fetch slot for its ATS provider.
    while any(state["selected"] for state in states):
        for state in states:
            check_cancelled()
            if not state["selected"]:
                continue
            row = state["selected"].popleft()
            provider = state["provider"]
            record(provider, "raw_candidates")
            if not admit(row["url"], provider, feed=row):
                record(provider, "source_failures")

    # Known matches take priority; at most five uncertain candidates per provider
    # enter the existing 25-posting downstream budget.
    unknown_counts = {}
    for state in states:
        provider = state['provider']
        for row in state['unknown']:
            check_cancelled()
            if unknown_counts.get(provider, 0) >= 5:
                partial(provider, 'unknown_location_limit')
                break
            unknown_counts[provider] = unknown_counts.get(provider, 0) + 1
            record(provider, 'raw_candidates')
            admit(row['url'], provider, feed=row)

    if feeds_only:
        return candidates

    # One multi-domain query per keyword/location, rather than one per ATS.
    # Coverage equivalence must be established by the live acceptance matrix.
    domains = "(site:greenhouse.io OR site:lever.co OR site:ashbyhq.com OR site:smartrecruiters.com)"
    for keyword in keywords:
        for location in locations:
            check_cancelled()
            for provider in health:
                record(provider, "source_attempts")
            try:
                document = fetch_json("https://google.serper.dev/search", budget, api_key=api_key,
                                      payload={"q": f"{domains} {keyword} {location}".strip(), "num": 10}, reserve=reserve)
                if not isinstance(document, dict) or not isinstance(document.get("organic"), list):
                    raise SourceError("invalid_response")
                rows = document["organic"]
                if len(rows) > 10:
                    raise SourceError("invalid_response")
                valid = 0
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("link"), str):
                        raise SourceError("invalid_response")
                    valid += int(admit(row["link"]))
                if rows and not valid:
                    raise SourceError("invalid_response")
                if not rows:
                    for provider in health:
                        record(provider, "source_empty")
                if len(rows) == 10:
                    for item in health.values():
                        item["partial_results"]["candidate_limit_hits"] += 1
            except SourceError:
                for provider in health:
                    record(provider, "source_failures")
                # Run-wide circuit breaker: no blind retry on outage or quota.
                return candidates
    return candidates
