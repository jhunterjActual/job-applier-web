"""Bounded public-feed and Serper acquisition; no profile or credential logging."""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from operations import OperationCancelled


CATALOG = (("lever", "tekton"), ("greenhouse", "clear"), ("ashby", "coder"))
MAX_BYTES = 1024 * 1024
MAX_REQUESTS = 12
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


def fetch_json(url, budget, *, api_key=None, payload=None, reserve=None):
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
            while size <= MAX_BYTES:
                budget.check_cancelled()
                if time.monotonic() >= budget.deadline:
                    raise SourceError("request_limit")
                chunk = getattr(response, "read1", response.read)(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            raw = b"".join(chunks)
        budget.check_cancelled()
        if time.monotonic() > budget.deadline:
            raise SourceError("request_limit")
        if len(raw) > MAX_BYTES:
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


def feed_rows(provider, board, budget):
    if (provider, board) not in CATALOG:
        raise SourceError("invalid_catalog")
    if provider == "lever":
        rows = []
        for offset in (0, 100, 200):
            page = fetch_json(f"https://api.lever.co/v0/postings/{board}?mode=json&limit=100&skip={offset}", budget)
            if not isinstance(page, list) or len(page) > 100:
                raise SourceError("invalid_response")
            rows.extend(page)
            if len(page) < 100:
                return rows
        raise SourceError("page_limit")
    if provider == "greenhouse":
        document = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", budget)
    else:
        document = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}", budget)
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), list):
        raise SourceError("invalid_response")
    rows = document["jobs"]
    if len(rows) > 1000:
        raise SourceError("page_limit")
    if provider == "greenhouse":
        meta = document.get("meta")
        if not isinstance(meta, dict):
            raise SourceError("invalid_response")
        total = meta.get("total")
        if type(total) is not int or total != len(rows):
            raise SourceError("invalid_response")
    return rows


def feed_candidate(provider, board, row):
    if not isinstance(row, dict):
        raise SourceError("invalid_response")
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
    expected = {"lever": {"jobs.lever.co"}, "greenhouse": {"boards.greenhouse.io", "job-boards.greenhouse.io"}, "ashby": {"jobs.ashbyhq.com"}}
    parts = parsed.path.split("/")
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname not in expected[provider] or len(parts) < 3 or parts[1] != board:
        raise SourceError("invalid_response")
    return {"title": title, "url": url, "location": location, "remote": remote}


def matches(row, keywords, locations):
    title = row["title"].casefold()
    role = any(all(word in title for word in re.findall(r"\w+", phrase.casefold())) for phrase in keywords)
    place = row["location"].casefold()
    location = any(not value or value.casefold() in place or (value.casefold() == "remote" and row["remote"]) for value in locations)
    return role and location


def collect(keywords, locations, health, check_cancelled, *, api_key=None, reserve=None, feeds_only=False):
    # Imported at call time to preserve the existing URL-validation boundary.
    from searcher import canonicalize_job_url, is_specific_job_url, provider_for_url

    budget = Budget(check_cancelled)
    candidates, seen = [], set()

    def record(provider, field):
        item = health[provider]
        item[field] = item.get(field, 0) + 1

    def admit(url, expected=None):
        canonical = canonicalize_job_url(url)
        if not canonical:
            return False
        provider = provider_for_url(canonical)
        if provider not in health or (expected and provider != expected) or not is_specific_job_url(canonical):
            return False
        if canonical not in seen:
            seen.add(canonical)
            candidates.append({"title": "Job Posting", "url": canonical, "snippet": ""})
            record(provider, "valid_discovered")
        return True

    for provider, board in CATALOG:
        check_cancelled()
        record(provider, "source_attempts")
        try:
            rows = [feed_candidate(provider, board, row) for row in feed_rows(provider, board, budget)]
            selected = [row for row in rows if row and matches(row, keywords, locations)]
            for row in selected:
                record(provider, "raw_candidates")
                if not admit(row["url"], provider):
                    raise SourceError("invalid_response")
            if not selected:
                record(provider, "source_empty")
        except SourceError:
            record(provider, "source_failures")

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
