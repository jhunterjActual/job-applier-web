import base64
import html
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
import time
import urllib.parse
import urllib.error
import urllib.request
from playwright.sync_api import sync_playwright
from pydantic import BaseModel
from ai_providers import (
    AIProviderError,
    AIProviderSettings,
    KEYWORD_PROVIDER_TIMEOUT_SECONDS,
    default_model,
    generate_structured,
    normalize_provider,
    claude_workflow_key,
    settings_from_profile,
)
import config
from operations import OperationCancelled
from tailor import analyze_job_match, analyze_job_matches_batch
from datetime import date, datetime, timezone
from typing import Any, Callable

MIN_MATCH_SCORE = 40
MAX_JOB_DESCRIPTION_CHARS = 50_000
MAX_LEVER_API_BYTES = 2 * 1024 * 1024
MAX_SEARCH_RESPONSE_BYTES = 1 * 1024 * 1024
MAX_PROVIDER_CANDIDATES = 25
SEARCH_HTTP_TIMEOUT_SECONDS = 12
MATCH_BATCH_SIZE = 5
MATCH_PROVIDER_MAX_ATTEMPTS = 3
MATCH_PROVIDER_RETRY_DELAYS_SECONDS = (5.0, 15.0)
MATCH_PROVIDER_RETRYABLE_ERRORS = frozenset({
    "empty_response",
    "invalid_response",
    "network",
    "overloaded",
    "oversized_response",
    "provider_error",
    "rate_limit",
    "timeout",
})
MATCH_PROVIDER_FAILURE_CODES = MATCH_PROVIDER_RETRYABLE_ERRORS | frozenset({
    "authentication",
    "missing_key",
    "model_unavailable",
    "permission",
    "unsupported_request",
    "refusal",
    "truncated",
    "invalid_input",
    "redirect_rejected",
    "unsupported_provider",
    "unsupported_operation",
    "unsupported_storage",
    "invalid_rate",
    "receipt_limit",
})
TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer", "source",
}
PROVIDER_DOMAINS = {
    "greenhouse": "greenhouse.io",
    "lever": "lever.co",
    "ashby": "ashbyhq.com",
    "smartrecruiters": "smartrecruiters.com",
}


class SearchKeywordResponse(BaseModel):
    titles: list[str]


def _wait_for_match_retry(seconds: float, check_cancelled: Callable[[], None]) -> None:
    """Keep bounded retry waits responsive to a durable stop request."""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(1.0, remaining))


INVALID_JOB_TEXT = (
    "internet explorer 11 is no longer supported",
    "consent to cookies",
    "job not found",
    "no longer active",
    "no longer available",
    "position closed",
    "job is closed",
    "page not found",
    "access denied",
)


def _cancellation_checkpoint(cancel_check: Callable[[], None] | None = None) -> None:
    """Run an optional cooperative-cancellation checkpoint."""
    if cancel_check:
        cancel_check()
ACCESS_CHALLENGE_TEXT = (
    "additional verification required", "are you a human", "captcha",
    "just a moment", "verify you are human", "unusual traffic",
)


def canonicalize_job_url(url: str) -> str:
    """Normalize a web posting URL while preserving unknown-site identifiers."""
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        if parsed.hostname in {'employment.udayton.edu', 'www.ziprecruiter.com', 'ziprecruiter.com'}:
            from additional_sources import canonical_source_url
            return canonical_source_url(url.strip()) or ''
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        port = parsed.port
        default_port = 443 if scheme == "https" else 80
        display_host = f"[{host}]" if ":" in host else host
        netloc = display_host if port in {None, default_port} else f"{display_host}:{port}"
        path_segments = [segment for segment in parsed.path.split("/") if segment]
        path = "/" + "/".join(path_segments)
        normalized_path = path.rstrip("/") or "/"

        provider = provider_for_url(urllib.parse.urlunsplit((scheme, netloc, normalized_path, "", "")))
        if provider == "ashby" and len(path_segments) >= 3 and path_segments[-1].lower() == "application":
            normalized_path = "/" + "/".join(path_segments[:-1])
        query = ""
        if provider == "unknown" and parsed.query:
            retained = [
                (key, value)
                for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
            ]
            query = urllib.parse.urlencode(sorted(retained))
        return urllib.parse.urlunsplit((scheme, netloc, normalized_path, query, ""))
    except Exception:
        return ""


def provider_for_url(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "greenhouse.io" or host.endswith(".greenhouse.io"):
        return "greenhouse"
    if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        return "lever"
    if host == "jobs.ashbyhq.com":
        return "ashby"
    if host == "jobs.smartrecruiters.com":
        return "smartrecruiters"
    if host == "weworkremotely.com":
        return "weworkremotely"
    if host == "jobicy.com":
        return "jobicy"
    if host == 'employment.udayton.edu':
        return 'udayton'
    if host in {'ziprecruiter.com', 'www.ziprecruiter.com'}:
        return 'ziprecruiter'
    return "unknown"


def validate_public_http_url(url: str, dns_cache: dict | None = None) -> tuple[bool, str]:
    """Require an HTTP(S) URL whose complete DNS result stays on the public Internet."""
    canonical_url = canonicalize_job_url(url)
    if not canonical_url:
        return False, "Enter a complete HTTP or HTTPS job-posting URL."
    parsed = urllib.parse.urlsplit(canonical_url)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False, "The URL contains an invalid port."
    if port not in {80, 443}:
        return False, "Job-posting URLs must use the standard HTTP or HTTPS port."

    hostname = parsed.hostname or ""
    cache_key = (hostname, port)
    if dns_cache is not None and cache_key in dns_cache:
        return dns_cache[cache_key]
    try:
        addresses = {ipaddress.ip_address(hostname)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError):
            result = (False, "The job-posting hostname could not be resolved.")
            if dns_cache is not None:
                dns_cache[cache_key] = result
            return result

    result = (
        (True, "")
        if addresses and all(address.is_global for address in addresses)
        else (False, "Local, private-network, and reserved addresses are not allowed.")
    )
    if dns_cache is not None:
        dns_cache[cache_key] = result
    return result


def is_public_http_url(url: str, dns_cache: dict | None = None) -> bool:
    """Boolean convenience wrapper for request-routing checks."""
    return validate_public_http_url(url, dns_cache)[0]


def _protect_browser_network(context) -> None:
    """Block redirects and subresources that leave the public HTTP(S) network."""
    dns_cache = {}

    def route_request(route, request) -> None:
        if is_public_http_url(request.url, dns_cache):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    context.route("**/*", route_request)


def _plain_text(value: object) -> str:
    """Convert JSON-LD or HTML text into a compact human-editable string."""
    if isinstance(value, list):
        value = "\n".join(_plain_text(item) for item in value)
    elif isinstance(value, dict):
        value = next((
            _plain_text(value.get(key))
            for key in ("name", "@value", "text", "value")
            if _plain_text(value.get(key))
        ), "")
    if not isinstance(value, str):
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(without_tags).split())


def _job_posting_json_ld(page) -> dict:
    """Return the first JobPosting object found in page JSON-LD."""
    def candidates(value):
        if isinstance(value, list):
            for item in value:
                yield from candidates(item)
        elif isinstance(value, dict):
            posting_types = value.get("@type") or []
            if isinstance(posting_types, str):
                posting_types = [posting_types]
            if "JobPosting" in posting_types:
                yield value
            if "@graph" in value:
                yield from candidates(value["@graph"])

    for element in page.query_selector_all('script[type="application/ld+json"]'):
        try:
            parsed = json.loads(element.text_content() or "")
            posting = next(candidates(parsed), None)
            if posting:
                return posting
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return {}


def _job_location_from_json_ld(posting: dict) -> str:
    def address_part(value: object) -> str:
        if isinstance(value, str):
            return _plain_text(value)
        if isinstance(value, dict):
            for key in ("name", "value", "code"):
                normalized = address_part(value.get(key))
                if normalized:
                    return normalized
        if isinstance(value, list):
            return ", ".join(part for item in value if (part := address_part(item)))
        return ""

    locations = posting.get("jobLocation") or []
    if isinstance(locations, dict):
        locations = [locations]
    values = []
    for location in locations:
        if isinstance(location, str):
            values.append(_plain_text(location))
            continue
        address = location.get("address", {}) if isinstance(location, dict) else {}
        if isinstance(address, str):
            values.append(address)
        elif isinstance(address, dict):
            parts = []
            seen_parts = set()
            for key in ("addressLocality", "addressRegion", "postalCode", "addressCountry"):
                component = address_part(address.get(key))
                for part in component.split(","):
                    part = part.strip()
                    normalized = part.casefold()
                    if part and normalized not in seen_parts:
                        parts.append(part)
                        seen_parts.add(normalized)
            values.append(", ".join(parts))
        if not values[-1] and isinstance(location, dict):
            values[-1] = _plain_text(location.get("name"))

    if not any(values):
        requirements = posting.get("applicantLocationRequirements") or []
        if not isinstance(requirements, list):
            requirements = [requirements]
        values = [_plain_text(requirement) for requirement in requirements]
    return "; ".join(value for value in values if value)


def _format_salary_number(value: object) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _compensation_from_json_ld(posting: dict) -> str:
    """Normalize common Schema.org salary variants without exposing objects."""
    salary = posting.get("baseSalary") or posting.get("estimatedSalary")
    if isinstance(salary, (int, float)) and not isinstance(salary, bool):
        amount = _format_salary_number(salary)
        currency = _plain_text(posting.get("salaryCurrency"))
        return " ".join(value for value in (currency, amount) if value)
    if not isinstance(salary, dict):
        return ""

    currency = _plain_text(salary.get("currency")) or _plain_text(posting.get("salaryCurrency"))
    value = salary.get("value", salary)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = maximum = _format_salary_number(value)
        unit = ""
    elif isinstance(value, dict):
        minimum = _format_salary_number(value.get("minValue", value.get("minPrice")))
        maximum = _format_salary_number(value.get("maxValue", value.get("maxPrice")))
        exact = _format_salary_number(value.get("value", value.get("price")))
        minimum = minimum or exact
        maximum = maximum or exact
        unit = _plain_text(value.get("unitText"))
    else:
        return ""

    amount = f"{minimum}–{maximum}" if minimum and maximum and minimum != maximum else minimum or maximum
    if not amount:
        return ""
    unit_labels = {"HOUR": "per hour", "DAY": "per day", "WEEK": "per week", "MONTH": "per month", "YEAR": "per year"}
    unit = unit_labels.get(unit.upper(), unit.lower()) if unit else ""
    return " ".join(value for value in (currency, amount, unit) if value)[:160]


def _structured_job_metadata(posting: dict) -> dict:
    location_type = _plain_text(posting.get("jobLocationType")).lower()
    employment = _plain_text(posting.get("employmentType")).lower().replace("_", " ").replace("-", " ")
    employment_aliases = (
        ("full time", "full_time"), ("part time", "part_time"),
        ("contract", "contract"), ("temporary", "temporary"),
        ("intern", "internship"),
    )
    return {
        "work_arrangement": "remote" if "telecommute" in location_type or "remote" in location_type else "",
        "employment_type": next((normalized for marker, normalized in employment_aliases if marker in employment), ""),
        "compensation": _compensation_from_json_ld(posting),
    }


def _job_fetch_outcome(status: str, details: dict | None = None, reason: str = "") -> dict:
    return {"status": status, "details": details or {}, "reason": reason}


def _lever_company_name(site: str) -> str:
    decoded = urllib.parse.unquote(site).replace("-", " ").replace("_", " ")
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", decoded)
    return " ".join(spaced.split()).title()


def _lever_compensation(posting: dict) -> str:
    salary = posting.get("salaryRange")
    if isinstance(salary, dict):
        minimum = _format_salary_number(salary.get("min"))
        maximum = _format_salary_number(salary.get("max"))
        amount = f"{minimum}–{maximum}" if minimum and maximum and minimum != maximum else minimum or maximum
        currency = _plain_text(salary.get("currency"))
        interval = _plain_text(salary.get("interval")).lower()
        if amount:
            suffix = f"per {interval}" if interval else ""
            return " ".join(value for value in (currency, amount, suffix) if value)[:160]
    return _plain_text(posting.get("salaryDescriptionPlain"))[:160]


def _lever_posting_from_api(url: str) -> dict:
    """Fetch one published Lever posting and classify failures before browser fallback."""
    parsed = urllib.parse.urlsplit(url)
    segments = [urllib.parse.unquote(segment) for segment in parsed.path.split("/") if segment]
    if len(segments) != 2:
        return _job_fetch_outcome("format_drift", reason="Lever posting URL did not contain a site and posting ID.")
    site, posting_id = segments
    api_host = "api.eu.lever.co" if parsed.hostname == "jobs.eu.lever.co" else "api.lever.co"
    api_url = f"https://{api_host}/v0/postings/{urllib.parse.quote(site, safe='')}/{urllib.parse.quote(posting_id, safe='')}"
    request = urllib.request.Request(api_url, headers={"Accept": "application/json", "User-Agent": "CareerTrellis/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = response.read(MAX_LEVER_API_BYTES + 1)
            if len(payload) > MAX_LEVER_API_BYTES:
                return _job_fetch_outcome("provider_error", reason="Lever API response exceeded the safe size limit.")
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 410}:
            return _job_fetch_outcome("stale", reason="Lever reports that this posting is no longer published.")
        return _job_fetch_outcome("api_unavailable", reason=f"Lever API returned HTTP {exc.code}.")
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return _job_fetch_outcome("api_unavailable", reason=f"Lever API was unavailable: {type(exc).__name__}.")

    try:
        posting = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _job_fetch_outcome("format_drift", reason="Lever API returned an invalid JSON document.")
    if not isinstance(posting, dict):
        return _job_fetch_outcome("format_drift", reason="Lever API returned an unexpected document type.")

    categories = posting.get("categories")
    if not isinstance(categories, dict):
        categories = {}
    title = _plain_text(posting.get("text"))
    description = _plain_text(posting.get("descriptionPlain")) or _plain_text(posting.get("description"))
    sections = []
    for section in posting.get("lists") or []:
        if not isinstance(section, dict):
            continue
        heading = _plain_text(section.get("text"))
        content = _plain_text(section.get("content"))
        if heading or content:
            sections.append("\n".join(value for value in (heading, content) if value))
    additional = _plain_text(posting.get("additionalPlain")) or _plain_text(posting.get("additional"))
    description = "\n\n".join(value for value in (description, *sections, additional) if value)

    locations = categories.get("allLocations") or categories.get("location") or []
    if not isinstance(locations, list):
        locations = [locations]
    normalized_locations = []
    for location in locations:
        location_text = _plain_text(location)
        if location_text and location_text.casefold() not in {item.casefold() for item in normalized_locations}:
            normalized_locations.append(location_text)

    workplace = _plain_text(posting.get("workplaceType")).lower()
    arrangement = {"remote": "remote", "hybrid": "hybrid", "on-site": "on_site"}.get(workplace, "")
    commitment = _metadata_from_text(_plain_text(categories.get("commitment"))).get("employment_type", "")
    details = {
        "title": title[:180],
        "company": _lever_company_name(site)[:180],
        "description": description[:MAX_JOB_DESCRIPTION_CHARS],
        "url": url,
        "location": "; ".join(normalized_locations)[:240],
        "work_arrangement": arrangement,
        "employment_type": commitment,
        "compensation": _lever_compensation(posting),
        "source": "lever",
    }
    if not is_useful_job_details(details):
        return _job_fetch_outcome("format_drift", reason="Lever API response omitted required posting fields.")
    return _job_fetch_outcome("ok", details=details)


def _largest_text(page, selectors: str) -> str:
    """Return the largest non-empty matching text block, avoiding UI shells."""
    candidates = page.query_selector_all(selectors)
    texts = []
    for candidate in candidates:
        try:
            text = candidate.inner_text().strip()
            if text:
                texts.append(text)
        except Exception:
            continue
    return max(texts, key=len, default="")


def _selector_text(page, selector: str) -> str:
    """Return trimmed text from the first matching element."""
    try:
        element = page.query_selector(selector)
        return element.inner_text().strip() if element else ""
    except Exception:
        return ""


def _adp_job_details(page) -> dict:
    """Extract the visible posting fields from ADP Workforce Now's job view."""
    try:
        page.wait_for_selector(
            ".job-description-data-item",
            timeout=6000,
        )
    except Exception:
        pass
    description = _largest_text(
        page,
        ".job-description-data-item, .job-description-data",
    )
    location = _selector_text(
        page,
        ".job-description-location-item, .job-description-location",
    )
    employment_label = _selector_text(page, ".job-description-worker-catergory")
    metadata = _metadata_from_text(
        f"{employment_label}\n{location}\n{description[:2500]}"
    )
    return {
        "title": _selector_text(page, "h2.job-description-title"),
        "company": _selector_text(
            page,
            ".job-description-data-item strong, .job-description-data strong",
        ),
        "description": description,
        "location": location,
        "work_arrangement": metadata["work_arrangement"],
        "employment_type": metadata["employment_type"],
        "compensation": (
            _selector_text(page, ".job-description-salary")
            or _selector_text(page, ".job-description-salary-details")
        ),
    }


def _select_job_content_scope(page) -> tuple[object, dict, bool]:
    """Prefer readable job data in a child frame while retaining the main page fallback."""
    scope = page
    posting = _job_posting_json_ld(page)
    embedded_frames = [frame for frame in page.frames if frame != page.main_frame]
    has_job_frame = any(
        re.search(r"(?:^|/)(?:jobs?|careers?)(?:/|$)", urllib.parse.urlsplit(frame.url).path, re.I)
        for frame in embedded_frames if frame.url
    )
    best_length = len(_largest_text(page, "[class*='job-description'], [id*='job-description'], main, article"))
    for frame in embedded_frames:
        try:
            frame_posting = _job_posting_json_ld(frame)
            frame_text = _largest_text(
                frame, "[class*='job-description'], [id*='job-description'], main, article, body"
            )
            if frame_posting or len(frame_text) > best_length:
                scope = frame
                posting = frame_posting
                best_length = len(frame_text)
            if frame_posting:
                break
        except Exception:
            continue
    return scope, posting, has_job_frame


def _metadata_from_text(text: str) -> dict:
    compact = " ".join((text or "").split())
    lowered = re.sub(r"[-_]", " ", compact.lower())
    work_arrangement = "remote" if re.search(r"\bremote\b", lowered) else "hybrid" if re.search(r"\bhybrid\b", lowered) else "on_site" if re.search(r"\b(on[- ]site|in[- ]office)\b", lowered) else ""
    employment_type = next((label for label in ("full time", "part time", "contract", "temporary", "internship") if label in lowered), "")
    compensation_match = re.search(r"(?:USD\s*)?\$[\d,]+(?:\.\d+)?(?:\s*[kK])?\s*(?:[-–—]|to)\s*(?:USD\s*)?\$?[\d,]+(?:\.\d+)?(?:\s*[kK])?(?:\s*(?:per|/)\s*(?:year|hour|yr|hr))?", compact)
    return {
        "work_arrangement": work_arrangement,
        "employment_type": employment_type.replace(" ", "_") if employment_type else "",
        "compensation": compensation_match.group(0)[:160] if compensation_match else "",
    }


def provider_alerts_from_health(provider_health: dict) -> list[dict]:
    """Convert abnormal provider outcomes into safe user-facing diagnostics."""
    alerts = []
    for provider, health in provider_health.items():
        if health.get("source_failures", 0):
            alerts.append({
                "provider": provider,
                "code": "provider_error",
                "message": f"{provider.title()} discovery could not return usable search results for some requests.",
            })
        elif health.get("source_fallbacks", 0):
            alerts.append({
                "provider": provider,
                "code": "partial_results",
                "message": f"{provider.title()} discovery used a fallback after its first search source was unavailable.",
            })
        attempted = health.get("new_candidates", 0)
        accepted = health.get("accepted", 0)
        reasons = health.get("rejection_reasons", {})
        if health.get("raw_candidates", 0) >= 3 and health.get("valid_discovered", 0) == 0:
            alerts.append({
                "provider": provider,
                "code": "url_format_drift",
                "message": f"{provider.title()} search results were found, but none matched the expected job URL format.",
            })
        elif reasons.get("format_drift", 0):
            alerts.append({
                "provider": provider,
                "code": "content_format_drift",
                "message": f"{provider.title()} returned {reasons['format_drift']} posting response(s) with an unexpected data format.",
            })
        elif reasons.get("access_challenge", 0) or reasons.get("embedded_content", 0):
            count = reasons.get("access_challenge", 0) + reasons.get("embedded_content", 0)
            alerts.append({
                "provider": provider,
                "code": "access_challenge",
                "message": f"{provider.title()} prevented automated reading of {count} posting(s); those results were skipped.",
            })
        elif (provider != "lever" and accepted == 0 and
              attempted - reasons.get('query_mismatch', 0) - reasons.get('eligibility_unknown', 0) >= 2):
            alerts.append({
                "provider": provider,
                "code": "content_format_drift",
                "message": f"{provider.title()} returned {attempted} new candidate postings, but none exposed complete job details.",
            })
        elif health.get("errors") or reasons.get("provider_error", 0):
            alerts.append({
                "provider": provider,
                "code": "provider_error",
                "message": f"{provider.title()} search encountered an error and may have incomplete results.",
            })
        if reasons.get("stale", 0):
            alerts.append({
                "provider": provider,
                "code": "stale_postings",
                "message": f"{provider.title()} skipped {reasons['stale']} posting(s) that are no longer published.",
            })
        partial = health.get("partial_results", {})
        if any(partial.values()) or health.get("candidate_budget_exhausted"):
            causes = []
            if partial.get("oversized_responses", 0):
                causes.append("a search response exceeded the 1 MiB download limit")
            if partial.get("candidate_limit_hits", 0) or health.get("candidate_budget_exhausted"):
                causes.append(f"the {MAX_PROVIDER_CANDIDATES}-posting processing limit was reached")
            if partial.get("managed_page_limits", 0):
                causes.append("the shared search returned its 10-result page limit; additional results may exist")
            if partial.get("timeouts", 0):
                causes.append(f"a search request exceeded the {SEARCH_HTTP_TIMEOUT_SECONDS}-second timeout")
            if partial.get("planning_limits", 0):
                causes.append("the search keyword or location limit was reached")
            if partial.get("catalog_page_limits", 0):
                causes.append("some employer feeds have more postings than this search could read")
            if partial.get("catalog_request_limits", 0):
                causes.append("the employer-feed request or time limit was reached")
            if partial.get("catalog_term_limits", 0):
                causes.append("the employer-feed search term limit was reached")
            if partial.get('unknown_location_limit', 0):
                causes.append('some postings need more location detail than this search could inspect')
            if not causes:
                causes.append("some sources could not be fully checked")
            alerts.append({
                "provider": provider,
                "code": "partial_results",
                "message": f"{provider.title()} returned partial results because {' and '.join(causes)}.",
            })
    return alerts


def is_useful_job_details(details: dict) -> bool:
    """Reject error pages and content too thin to support meaningful matching."""
    title = " ".join(str(details.get("title") or "").split())
    company = " ".join(str(details.get("company") or "").split())
    description = " ".join(str(details.get("description") or "").split())
    combined = f"{title}\n{company}\n{description}".lower()
    if not title or not company or company.lower() == "unknown company":
        return False
    if len(title) < 3 or len(title) > 180 or len(description) < 80:
        return False
    return not any(marker in combined for marker in INVALID_JOB_TEXT)


def _bounded_job_details(details: dict) -> dict:
    """Apply final retention limits before job details enter matching or storage."""
    bounded = dict(details)
    bounded.pop('_work_arrangement_from_text', None)
    bounded["description"] = str(bounded.get("description") or "")[:MAX_JOB_DESCRIPTION_CHARS]
    return bounded


def _append_candidate_with_budget(
    item: dict,
    results: list,
    seen_urls: set,
    provider_candidate_counts: dict,
    provider_health: dict,
) -> bool:
    """Append one canonical candidate unless its provider has reached the run-wide cap."""
    url = canonicalize_job_url(item.get("url", ""))
    if not url:
        return False
    provider = provider_for_url(url)
    if url in seen_urls:
        if provider in provider_health:
            health = provider_health[provider]
            health['coverage_duplicate_candidates'] = health.get('coverage_duplicate_candidates', 0) + 1
        return False
    if provider in provider_candidate_counts:
        if provider_candidate_counts[provider] >= MAX_PROVIDER_CANDIDATES:
            health = provider_health[provider]
            health['coverage_detail_cap_excluded'] = health.get('coverage_detail_cap_excluded', 0) + 1
            provider_health[provider]["candidate_budget_exhausted"] = True
            return False
        provider_candidate_counts[provider] += 1
        if provider_candidate_counts[provider] >= MAX_PROVIDER_CANDIDATES:
            provider_health[provider]["candidate_budget_exhausted"] = True
    seen_urls.add(url)
    results.append({**item, "url": url})
    return True


def _supplement_feed_metadata(details: dict, candidate: dict) -> dict:
    """Fill missing metadata only after detail validation for the same posting."""
    metadata = candidate.get("feed_metadata")
    candidate_url = candidate.get("url", "")
    detail_url = canonicalize_job_url(details.get("url", ""))
    same_posting = bool(detail_url) and detail_url == candidate_url
    if not same_posting:
        # SmartRecruiters adds a human-readable slug on legitimate redirects.
        # The employer and numeric posting ID must still agree exactly.
        pattern = r"https://jobs\.smartrecruiters\.com/([^/]+)/([0-9]+)(?:-[a-zA-Z0-9_-]+)?"
        origin = re.fullmatch(pattern, candidate_url)
        destination = re.fullmatch(pattern, detail_url or "")
        same_posting = bool(origin and destination and
                            origin[1].casefold() == destination[1].casefold() and
                            origin[2] == destination[2])
    if (not isinstance(metadata, dict) or
            metadata.get("url") != candidate_url or not same_posting):
        return details
    supplemented = dict(details)
    for key in ("location", "work_arrangement", "employment_type"):
        value = metadata.get(key)
        if not supplemented.get(key) and isinstance(value, str):
            supplemented[key] = " ".join(value.split())[:240]
    return supplemented


def is_specific_job_url(url: str) -> bool:
    """
    Validates if a Lever, Greenhouse, Ashby, or SmartRecruiters URL is a
    specific job posting rather than a generic boards or careers landing page.
    
    Args:
        url (str): The URL to validate.
        
    Returns:
        bool: True if it matches the specific job posting structure, False otherwise.
    """
    try:
        if provider_for_url(url) in {'udayton', 'ziprecruiter'}:
            from additional_sources import canonical_source_url
            return canonical_source_url(url) is not None
        if provider_for_url(url) in {"weworkremotely", "jobicy"}:
            from public_job_feeds import canonical_url
            return canonical_url(url) is not None
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.strip("/")
        segments = [s for s in path.split("/") if s]
        
        if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
            # Greenhouse specific postings must contain 'jobs' in segments and have at least 3 segments (e.g., /company/jobs/id)
            return "jobs" in segments and len(segments) >= 3
            
        elif host in {"jobs.lever.co", "jobs.eu.lever.co"}:
            # Lever specific postings must have exactly 2 segments (e.g., /company/job-uuid)
            if len(segments) == 2:
                # Avoid standard routing endpoints if any
                return segments[1] not in ["careers", "login", "logo", "about"]
            return False
            
        elif host == "jobs.ashbyhq.com":
            # Ashby postings look like jobs.ashbyhq.com/company/job-id or ashbyhq.com/company/jobs/job-id
            return len(segments) >= 2 and segments[-1] not in ["careers", "login", "about"]
            
        elif host == "jobs.smartrecruiters.com":
            # SmartRecruiters postings look like jobs.smartrecruiters.com/company/job-id
            return len(segments) == 2 and segments[1] not in ["careers", "login"]
            
        return False
    except Exception:
        return False


def _canonical_redirected_job_url(requested_url: str, final_url: str) -> str:
    """Reject known-provider redirects that no longer identify one posting."""
    canonical_url = canonicalize_job_url(final_url)
    if not canonical_url:
        return ""
    requested_provider = provider_for_url(requested_url)
    if requested_provider in PROVIDER_DOMAINS or requested_provider in {'udayton', 'ziprecruiter'}:
        if (
            provider_for_url(canonical_url) != requested_provider
            or not is_specific_job_url(canonical_url)
        ):
            return ""
        if requested_provider in {'udayton', 'ziprecruiter'}:
            before = urllib.parse.urlsplit(canonicalize_job_url(requested_url))
            after = urllib.parse.urlsplit(canonical_url)
            if requested_provider == 'udayton' and before.path.split('/')[3] != after.path.split('/')[3]:
                return ''
            if requested_provider == 'ziprecruiter' and before.query != after.query:
                return ''
    return canonical_url


def _decoded_search_result_url(link: str) -> str:
    """Unwrap only the known Yahoo and Bing result-link formats."""
    decoded = html.unescape(str(link or ""))
    try:
        parsed = urllib.parse.urlsplit(decoded)
    except ValueError:
        return ""
    if (parsed.hostname or "").lower() == "r.search.yahoo.com":
        match = re.search(r"/RU=([^/]+)", decoded)
        if match:
            return urllib.parse.unquote(match.group(1))
    try:
        if (
            (parsed.hostname or "").lower() in {"bing.com", "www.bing.com"}
            and parsed.path == "/ck/a"
        ):
            token = urllib.parse.parse_qs(parsed.query).get("u", [""])[0]
            if token.startswith("a1"):
                encoded = token[2:]
                encoded += "=" * (-len(encoded) % 4)
                return base64.urlsafe_b64decode(encoded).decode("utf-8")
            if token.startswith(("http://", "https://")):
                return token
    except (UnicodeDecodeError, ValueError):
        return ""
    return decoded


class _UnusableSearchResponse(ValueError):
    """An HTTP-success response did not establish candidates or an explicit zero."""


class _SearchResponseParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.empty_marker = False
        self.result_marker = False
        self.text = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"script", "style"}:
            self.ignored_depth += 1
        if tag == "a" and attributes.get("href") and not self.ignored_depth:
            self.links.append(attributes["href"])
        markers = set((attributes.get("class", "") or "").lower().split())
        markers.add((attributes.get("id", "") or "").lower())
        if markers & {"b_no", "compnoresults", "no-results", "noresults"}:
            self.empty_marker = True
        if markers & {"b_algo", "algo"}:
            self.result_marker = True

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored_depth = max(0, self.ignored_depth - 1)

    def handle_data(self, data):
        if not self.ignored_depth:
            self.text.append(data)


def _source_counter(health: dict | None, name: str) -> None:
    if health is not None:
        health[name] = min(100_000, max(0, health.get(name, 0)) + 1)


def _search_result_candidates(
    url: str,
    provider: str,
    health: dict | None,
) -> list[str]:
    request = urllib.request.Request(url, headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/130 Safari/537.36"
        ),
    })
    with urllib.request.urlopen(request, timeout=SEARCH_HTTP_TIMEOUT_SECONDS) as response:
        payload = response.read(MAX_SEARCH_RESPONSE_BYTES + 1)
    oversized = len(payload) > MAX_SEARCH_RESPONSE_BYTES
    if oversized:
        if health is not None:
            health["partial_results"]["oversized_responses"] += 1
        payload = payload[:MAX_SEARCH_RESPONSE_BYTES]
    document = payload.decode("utf-8", errors="replace")
    parser = _SearchResponseParser()
    parser.feed(document)
    candidates = []
    for link in parser.links:
        decoded = _decoded_search_result_url(link)
        if provider_for_url(decoded) != provider or "/embed/" in decoded:
            continue
        if health is not None:
            health["raw_candidates"] += 1
        if not is_specific_job_url(decoded):
            continue
        canonical_url = canonicalize_job_url(decoded)
        if not canonical_url or canonical_url in candidates:
            continue
        if len(candidates) >= MAX_PROVIDER_CANDIDATES:
            if health is not None:
                health["partial_results"]["candidate_limit_hits"] += 1
            break
        candidates.append(canonical_url)
    if not candidates:
        text = " ".join(" ".join(parser.text).lower().split())
        explicit_empty = parser.empty_marker and not parser.result_marker and re.search(
            r"\b(?:no results (?:found|for)|no results\b|there are no results|did not match any (?:documents|results))",
            text,
        )
        if oversized or not explicit_empty:
            _source_counter(health, "source_unusable")
            raise _UnusableSearchResponse("Search response did not establish a usable result.")
        _source_counter(health, "source_empty")
    return candidates


def _source_failure_code(exc: BaseException) -> str:
    if isinstance(exc, _UnusableSearchResponse):
        return "unusable_response"
    if isinstance(exc, urllib.error.HTTPError):
        return "http_5xx" if 500 <= exc.code <= 599 else "http_error"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        return "timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "network"
    return "unexpected"


def search_yahoo_jobs(
    keywords: str,
    location: str = "",
    diagnostics: dict | None = None,
    source_state: dict | None = None,
) -> list:
    """
    Query Yahoo per ATS domain, failing over to Bing after a source error.

    A run-scoped circuit breaker prevents a blanket Yahoo failure from being
    repeated for every keyword, location, and ATS domain.
    
    Args:
        keywords (str): The search phrase / job title keywords.
        location (str, optional): The target location constraint.
        
    Returns:
        list: A list of dictionaries representing the found job posting URLs.
    """
    domains = list(PROVIDER_DOMAINS.values())
    state = source_state if source_state is not None else {"yahoo_available": True}
    state.setdefault("yahoo_available", True)
    jobs = []
    seen_urls = set()
    
    for domain in domains:
        provider = next(name for name, value in PROVIDER_DOMAINS.items() if value == domain)
        health = diagnostics.get(provider) if diagnostics is not None else None
        if health is not None and health.get("candidate_budget_exhausted"):
            continue
        query = f"site:{domain} {keywords}"
        if location:
            query += f" {location}"
            
        encoded_query = urllib.parse.quote_plus(query)
        candidates = None
        yahoo_error = None
        if state["yahoo_available"]:
            try:
                _source_counter(health, "source_attempts")
                candidates = _search_result_candidates(
                    f"https://search.yahoo.com/search?p={encoded_query}",
                    provider,
                    health,
                )
            except OperationCancelled:
                raise
            except Exception as exc:
                yahoo_error = _source_failure_code(exc)
                state["yahoo_available"] = False
                print(json.dumps({
                    "event": "search_source_fallback_activated",
                    "from": "yahoo",
                    "to": "bing",
                    "error_code": yahoo_error,
                }, separators=(",", ":")), flush=True)

        if candidates is None:
            _source_counter(health, "source_fallbacks")
            try:
                _source_counter(health, "source_attempts")
                candidates = _search_result_candidates(
                    f"https://www.bing.com/search?q={encoded_query}",
                    provider,
                    health,
                )
            except OperationCancelled:
                raise
            except Exception as exc:
                fallback_error = _source_failure_code(exc)
                _source_counter(health, "source_failures")
                if health is not None:
                    if "timeout" in {yahoo_error, fallback_error}:
                        health["partial_results"]["timeouts"] += 1
                    elif len(health["errors"]) < 20:
                        health["errors"].append(fallback_error)
                print(json.dumps({
                    "event": "search_source_request_failed",
                    "provider": provider,
                    "source": "bing",
                    "error_code": fallback_error,
                }, separators=(",", ":")), flush=True)
                candidates = []

        for actual_url in candidates:
            if actual_url not in seen_urls:
                seen_urls.add(actual_url)
                jobs.append({
                    "title": "Job Posting",
                    "url": actual_url,
                    "snippet": "",
                })
                if health is not None:
                    health["valid_discovered"] += 1
            
    return jobs

def extract_search_keywords_from_resume(
    resume_text: str,
    api_key: str = None,
    ai_provider: str = "gemini",
    ai_model: str | None = None,
) -> list:
    """
    Analyzes the candidate's base resume text and extracts the top 3 best
    matching job title search keywords using the selected AI provider.
    
    Args:
        resume_text (str): The candidate's raw base resume.
        api_key (str, optional): The selected provider's API key. Defaults to None.
        
    Returns:
        list: A list of extracted job title keyword strings.
    """
    provider = normalize_provider(ai_provider)
    key = (api_key or "").strip()
    if not key:
        if provider == 'anthropic':
            key = claude_workflow_key()
        else:
            key = config.get_openai_api_key() if provider == "openai" else config.get_gemini_api_key()
    if not key:
        return []

    settings = AIProviderSettings(provider, (ai_model or default_model(provider)).strip(), key)
    try:
        prompt = f"""
        Analyze this candidate's resume and determine the top 3 job title keywords (specific search phrases) they are highly qualified for.
        
        Guidelines:
        1. Keep each title keyword simple and generic for search engines, e.g. "Data Engineer", "Python Developer", "React Developer", "Machine Learning Engineer".
        2. Do not include locations.
        3. Do not include too specific tech stacks unless they are primary, e.g. use "Data Engineer" rather than "PostgreSQL Snowflake PySpark Data Engineer".
        
        Candidate's Resume:
        \"\"\"{resume_text}\"\"\"
        
        Return the titles using the supplied response schema.
        """
        titles = generate_structured(
            settings,
            prompt,
            SearchKeywordResponse,
            timeout_seconds=KEYWORD_PROVIDER_TIMEOUT_SECONDS,
            disable_thinking=True,
        ).get("titles", [])
        if isinstance(titles, list) and len(titles) > 0:
            return [str(title).strip() for title in titles[:3] if str(title).strip()]
        return []
    except AIProviderError as exc:
        print(f"AI keyword extraction did not complete: {exc}")
        return []
    except Exception:
        print("AI keyword extraction did not complete because the response was invalid.")
        return []

def _reject_job_fetch(diagnostic: dict | None, status: str, reason: str) -> dict:
    if diagnostic is not None:
        diagnostic.update({"status": status, "reason": reason})
    return {}


def _scrape_job_details_browser(url: str, allow_partial: bool = False, diagnostic: dict | None = None) -> dict:
    """
    Launches a headless browser via Playwright and scrapes the job details
    (title, company name, description, and link) from the target job board.
    
    Args:
        url (str): The specific job posting URL.
        
    Returns:
        dict: A dictionary containing title, company, description, and URL,
              or an empty dictionary if the posting is closed or invalid.
    """
    url = canonicalize_job_url(url)
    is_public, reason = validate_public_http_url(url)
    if not is_public:
        return _reject_job_fetch(diagnostic, "unsafe_url", reason)
    provider = provider_for_url(url)
    hostname = (urllib.parse.urlsplit(url).hostname or "").removeprefix("www.")
    details = {
        "title": "",
        "company": "",
        "description": "",
        "url": url,
        "location": "",
        "work_arrangement": "",
        "employment_type": "",
        "compensation": "",
        "source": provider if provider != "unknown" else hostname,
    }
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )
        _protect_browser_network(context)
        page = context.new_page()
        try:
            response = page.goto(url, timeout=20000, wait_until="domcontentloaded")
            if response and response.status >= 400:
                if response.status in {404, 410}:
                    return _reject_job_fetch(diagnostic, "stale", f"Posting returned HTTP {response.status}.")
                if response.status in {401, 403, 429}:
                    return _reject_job_fetch(diagnostic, "access_challenge", f"Posting returned HTTP {response.status}.")
                return _reject_job_fetch(diagnostic, "provider_error", f"Posting returned HTTP {response.status}.")
            if not is_public_http_url(page.url):
                return _reject_job_fetch(diagnostic, "unsafe_url", "Posting redirected to a non-public destination.")
            final_url = _canonical_redirected_job_url(url, page.url)
            if not final_url:
                return _reject_job_fetch(
                    diagnostic,
                    "stale",
                    "The posting redirected to a page that no longer identifies one job.",
                )
            details["url"] = final_url
            
            # Wait for content or title elements to load dynamically
            try:
                page.wait_for_selector("h1, h2, .app-title, .posting-header", timeout=4000)
            except Exception:
                pass
                
            page_title = page.title().lower()
            body_preview = ""
            try:
                body_preview = page.inner_text("body")[:4000].lower()
            except Exception:
                pass
            if any(marker in f"{page_title}\n{body_preview}" for marker in ACCESS_CHALLENGE_TEXT):
                return _reject_job_fetch(diagnostic, "access_challenge", "The site presented an automated-access challenge.")
            if any(marker in page_title for marker in ("couldn't find", "not found", "no longer available")):
                return _reject_job_fetch(diagnostic, "stale", "The posting page indicates that the job is no longer available.")
                
            if "lever.co" in url:
                title_elem = page.query_selector(".posting-header h2, h2")
                if not title_elem:
                    return _reject_job_fetch(diagnostic, "format_drift", "Lever browser fallback did not expose a job title.")
                    
                company_logo = page.query_selector(".main-header-logo img")
                company_name = ""
                if company_logo:
                    company_name = company_logo.get_attribute("alt") or ""
                    company_name = company_name.replace("logo", "").replace("Logo of", "").strip()
                
                if not company_name:
                    p_title = page.title()
                    if "-" in p_title:
                        company_name = p_title.split("-")[0].strip()
                        
                sections = page.query_selector_all(".section")
                desc_text = "\n\n".join([s.inner_text() for s in sections])
                
                details["title"] = title_elem.inner_text().strip()
                details["company"] = company_name if company_name else "Unknown Company"
                details["description"] = desc_text
                location_elem = page.query_selector(".posting-categories .location, .sort-by-location, [class*='location']")
                details["location"] = location_elem.inner_text().strip() if location_elem else ""
                
            elif "greenhouse.io" in url:
                title_elem = page.query_selector("h1.app-title, h1")
                if not title_elem:
                    return _reject_job_fetch(diagnostic, "format_drift", "Greenhouse page did not expose a job title.")
                    
                company_elem = page.query_selector("span.company-name")
                company_name = ""
                if company_elem:
                    company_name = company_elem.inner_text().replace("at ", "").strip()
                
                if not company_name:
                    p_title = page.title()
                    if " at " in p_title:
                        company_name = p_title.rsplit(" at ", 1)[-1].strip()
                    else:
                        segments = [urllib.parse.unquote(s) for s in urllib.parse.urlparse(url).path.split("/") if s]
                        company_name = segments[0].replace("-", " ").title() if segments else ""
                        
                desc_text = _largest_text(page, "#content, div[class*='description'], main")
                location_elem = page.query_selector("[class*='location'], [data-testid*='location']")
                
                details["title"] = title_elem.inner_text().strip()
                details["company"] = company_name if company_name else "Unknown Company"
                details["description"] = desc_text
                details["location"] = location_elem.inner_text().strip() if location_elem else ""
                
            elif "ashbyhq.com" in url:
                title_elem = page.query_selector("h1")
                if not title_elem:
                    return _reject_job_fetch(diagnostic, "format_drift", "Ashby page did not expose a job title.")
                    
                parsed_url = urllib.parse.urlparse(url)
                url_path = parsed_url.path.strip("/")
                url_segments = [s for s in url_path.split("/") if s]
                
                p_title = page.title()
                company_name = "Unknown Company"
                if " @ " in p_title:
                    company_name = p_title.rsplit(" @ ", 1)[-1].strip()
                elif " - " in p_title:
                    company_name = p_title.split(" - ")[0].strip()
                elif " | " in p_title:
                    company_name = p_title.split(" | ")[0].strip()
                elif url_segments:
                    company_name = url_segments[0].capitalize()
                    
                desc_text = _largest_text(page, "div[class*='description'], #job-description, main") or page.inner_text("body")
                body_text = page.inner_text("body")
                location_match = re.search(r"(?im)^Location\s*\n+([^\n]+)", body_text)
                
                details["title"] = title_elem.inner_text().strip()
                details["company"] = company_name
                details["description"] = desc_text
                details["location"] = location_match.group(1).strip() if location_match else ""

            elif "jobs.smartrecruiters.com" in url:
                title_candidates = page.query_selector_all("h1")
                title_text = next((element.inner_text().strip() for element in title_candidates if element.inner_text().strip() and not any(marker in element.inner_text().lower() for marker in INVALID_JOB_TEXT)), "")
                if not title_text:
                    return _reject_job_fetch(diagnostic, "format_drift", "SmartRecruiters page did not expose a job title.")

                parsed_url = urllib.parse.urlparse(url)
                url_segments = [urllib.parse.unquote(s) for s in parsed_url.path.strip("/").split("/") if s]
                if len(url_segments) < 2:
                    return _reject_job_fetch(diagnostic, "format_drift", "SmartRecruiters URL did not identify a posting.")

                # The company slug is stable; the page title can be an error or
                # search-engine title and must never become the company name.
                company_slug = url_segments[0].replace("-", " ").replace("_", " ")
                company_name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", company_slug).strip()
                desc_text = _largest_text(page, "div[class*='job'], div[class*='description'], #job-description, main")
                body_text = page.inner_text("body")

                details["title"] = title_text
                details["company"] = company_name
                details["description"] = desc_text
                location_match = re.search(r"(?im)^Location\s*\n+([^\n]+)", body_text)
                details["location"] = location_match.group(1).strip() if location_match else ""

            elif hostname == "workforcenow.adp.com":
                details.update(_adp_job_details(page))

            else:
                scope, posting, has_embedded_frames = _select_job_content_scope(page)
                organization = posting.get("hiringOrganization") or {}
                title_element = scope.query_selector("h1")
                description = _plain_text(posting.get("description"))
                if not description:
                    description = _largest_text(
                        scope,
                        "[class*='job-description'], [id*='job-description'], main, article",
                    )
                details["title"] = _plain_text(posting.get("title")) or (
                    title_element.inner_text().strip() if title_element else ""
                )
                details["company"] = _plain_text(organization)
                if not details["company"]:
                    site_name = scope.query_selector('meta[property="og:site_name"]') or page.query_selector('meta[property="og:site_name"]')
                    details["company"] = (site_name.get_attribute("content") or "").strip() if site_name else ""
                details["description"] = description
                details["location"] = _job_location_from_json_ld(posting)
                for key, value in _structured_job_metadata(posting).items():
                    details[key] = value or details.get(key, "")
                if not any(details.get(key) for key in ("title", "company", "description")) and has_embedded_frames:
                    return _reject_job_fetch(
                        diagnostic, "embedded_content",
                        "The posting is inside an embedded frame that did not expose readable job content.",
                    )
                
        except Exception as e:
            print(f"Error scraping details from {url}: {e}")
            return _reject_job_fetch(diagnostic, "provider_error", f"Browser extraction failed: {type(e).__name__}.")
        finally:
            browser.close()
            
    details["title"] = " ".join(details["title"].split())[:180]
    details["company"] = " ".join(details["company"].split())[:180]
    details["location"] = " ".join(details["location"].split())[:240]
    details["description"] = details["description"].strip()[:MAX_JOB_DESCRIPTION_CHARS]
    metadata = _metadata_from_text(f"{details['title']}\n{details['location']}\n{details['description'][:2500]}")
    for key, value in metadata.items():
        if key == 'work_arrangement' and not details.get(key) and value:
            details['_work_arrangement_from_text'] = True
        details[key] = details.get(key) or value

    # Reject generic/error pages and content that cannot support a real match.
    if allow_partial and any(details.get(key) for key in ("title", "company", "description")):
        if diagnostic is not None:
            diagnostic.update({
                "status": "ok" if all(details.get(key) for key in ("title", "company", "description")) else "partial",
                "reason": "",
            })
        return details
    if not is_useful_job_details(details):
        return _reject_job_fetch(diagnostic, "format_drift", "The page loaded but did not expose a complete job posting.")
    if diagnostic is not None:
        diagnostic.update({"status": "ok", "reason": ""})
        
    return details


def inspect_job_posting(
    url: str,
    allow_partial: bool = False,
    cancel_check: Callable[[], None] | None = None,
    require_live: bool = False,
) -> dict:
    """Return extracted details together with a privacy-safe outcome classification."""
    _cancellation_checkpoint(cancel_check)
    canonical_url = canonicalize_job_url(url)
    is_public, reason = validate_public_http_url(canonical_url)
    if not is_public:
        return _job_fetch_outcome("unsafe_url", reason=reason)

    if provider_for_url(canonical_url) in {"weworkremotely", "jobicy"}:
        from public_job_feeds import details_for_url, canonical_url as feed_url
        if not feed_url(url):
            return _job_fetch_outcome("unsafe_url", reason="Unsupported public-feed listing URL.")
        if require_live:
            # Explicit Verify must not certify a cached listing as currently live.
            from discovery_sources import NoRedirect
            try:
                request = urllib.request.Request(canonical_url, method="HEAD",
                    headers={"User-Agent": "CareerTrellis-listing-verification/1.0"})
                with urllib.request.build_opener(NoRedirect()).open(request, timeout=12) as response:
                    if response.status != 200:
                        return _job_fetch_outcome("provider_error", reason="The listing could not be verified.")
            except urllib.error.HTTPError as exc:
                if exc.code in {404, 410}:
                    return _job_fetch_outcome("stale", reason="The source reports that this listing is no longer available.")
                return _job_fetch_outcome("provider_error", reason="The listing could not be verified.")
            except OperationCancelled:
                raise
            except Exception:
                return _job_fetch_outcome("provider_error", reason="The listing could not be verified.")
        details = details_for_url(canonical_url, cancel_check or (lambda: None))
        _cancellation_checkpoint(cancel_check)
        if details is None:
            return _job_fetch_outcome("provider_error", reason="This posting is not available in a fresh public feed snapshot. Try discovery again after the source refresh.")
        if not is_useful_job_details(details):
            return _job_fetch_outcome("format_drift", reason="The public feed did not supply complete posting details.")
        return _job_fetch_outcome("ok", details)

    if provider_for_url(canonical_url) == "lever":
        api_outcome = _lever_posting_from_api(canonical_url)
        _cancellation_checkpoint(cancel_check)
        if api_outcome["status"] != "api_unavailable":
            return api_outcome
        browser_diagnostic = {}
        details = _scrape_job_details_browser(canonical_url, allow_partial, browser_diagnostic)
        _cancellation_checkpoint(cancel_check)
        status = browser_diagnostic.get("status", "provider_error")
        if status == "format_drift":
            status = "provider_error"
        reason = browser_diagnostic.get("reason") or api_outcome["reason"]
        outcome = _job_fetch_outcome(status, details, reason)
        outcome["used_browser_fallback"] = True
        return outcome

    browser_diagnostic = {}
    details = _scrape_job_details_browser(canonical_url, allow_partial, browser_diagnostic)
    _cancellation_checkpoint(cancel_check)
    return _job_fetch_outcome(
        browser_diagnostic.get("status", "provider_error"),
        details,
        browser_diagnostic.get("reason", "Browser extraction did not complete."),
    )


def scrape_job_details(url: str, allow_partial: bool = False) -> dict:
    """Compatibility wrapper returning details only for callers that do not need diagnostics."""
    return inspect_job_posting(url, allow_partial)["details"]

def _search_completion(
    provider_health: dict,
    discovered: int,
    inspected: int,
    analyzed: int,
    saved: int,
    ai_error: str | None = None,
    ai_error_code: str | None = None,
    save_failures: int = 0,
    scored: int | None = None,
    meeting_minimum: int | None = None,
) -> dict:
    """Classify useful work separately from discovery and posting failures."""
    totals = lambda key: sum(item.get(key, 0) for item in provider_health.values())
    known = totals("skipped_active") + totals("skipped_archived")
    suppressed = totals("skipped_suppressed")
    stale = sum(item.get("rejection_reasons", {}).get("stale", 0) for item in provider_health.values())
    detail_errors = sum(
        sum(value for key, value in item.get("rejection_reasons", {}).items() if key not in {"stale", "query_mismatch", "eligibility_unknown"})
        for item in provider_health.values()
    )
    limited = any(
        any(item.get("partial_results", {}).values()) or item.get("candidate_budget_exhausted")
        for item in provider_health.values()
    )
    source_failed = totals("source_failures") > 0
    issues = source_failed or detail_errors > 0 or limited
    query_filtered = sum(item.get('rejection_reasons', {}).get('query_mismatch', 0) +
                         item.get('rejection_reasons', {}).get('eligibility_unknown', 0)
                         for item in provider_health.values())
    useful = totals("accepted") + known + suppressed + stale + totals("source_empty") + query_filtered
    if save_failures and ai_error is None:
        ai_error = "Some matched postings could not be saved. Please try again later."
        ai_error_code = "storage_error"
    if ai_error is not None:
        outcome = "partial_results" if saved > 0 else None
        success = saved > 0
    elif issues and not useful and not saved:
        outcome = "source_unavailable"
        success = False
        ai_error = "Job sources did not return usable postings. Please try again later."
        ai_error_code = "source_unavailable"
    elif issues:
        outcome = "partial_results"
        success = True
    else:
        outcome = "matches_saved" if saved else "no_new_matches" if discovered else "no_results"
        success = True
    counts = {
        "discovered": discovered, "inspected": inspected,
        "accepted": totals("accepted"), "known": known,
        "filtered": suppressed + stale + query_filtered + max(0, analyzed - saved - save_failures), "saved": saved,
    }
    if scored is not None and meeting_minimum is not None:
        counts.update(scored=scored, meeting_minimum=meeting_minimum)
        # A match can qualify but already exist by insertion time, or fail to
        # save. Neither case is a rejection by the minimum match threshold.
        counts["filtered"] = suppressed + stale + query_filtered + max(0, scored - meeting_minimum)
    result = {
        "success": success, "partial": outcome == "partial_results",
        "jobs_added": saved, "error": ai_error, "failure_code": ai_error_code,
        "counts": {key: max(0, min(100_000, value)) for key, value in counts.items()},
        "provider_health": provider_health,
        "provider_alerts": provider_alerts_from_health(provider_health),
        # These are separate diagnostic measures, not additive failure totals:
        # unusable counts responses, failed counts unrecovered source searches.
        "source_counts": {
            "confirmed_empty": totals("source_empty"),
            "failed": totals("source_failures"),
            "unusable": totals("source_unusable"),
            "limited": sum(bool(any(item.get("partial_results", {}).values()) or
                                item.get("candidate_budget_exhausted"))
                           for item in provider_health.values()),
        },
    }
    result["source_counts"] = {
        key: max(0, min(100_000, value)) for key, value in result["source_counts"].items()
    }
    if scored is not None and meeting_minimum is not None:
        result["minimum_match_score"] = MIN_MATCH_SCORE
    if outcome is not None:
        result["outcome"] = outcome
    return result


def run_job_search_and_matching(
    keywords: str,
    location: str = "",
    cancel_check: Callable[[], None] | None = None,
    job_repository: Any | None = None,
    profile_repository: Any | None = None,
    progress_update: Callable[[str, int, int | None], None] | None = None,
    serper_api_key: str | None = None,
    reserve_search_request: Callable[[], bool] | None = None,
) -> dict:
    """
    Coordinates the entire job search pipeline: retrieves the resume,
    calculates/suggests keywords if undefined, searches job boards in parallel,
    scrapes detailed job profiles, performs batch AI matching, and stores
    results through the configured owner-scoped repositories.
    
    Args:
        keywords (str): The keywords to search for. If empty, uses cached resume-suggested keywords.
        location (str, optional): Semicolon-separated list of target locations. Defaults to "".
        
    Returns:
        dict: A dictionary containing success status and number of new jobs matched.
    """
    # 1. Fetch the user's base resume through one short repository operation.
    if profile_repository is None:
        from profile_repository import runtime_profile_repository

        profile_repository = runtime_profile_repository()
    if job_repository is None:
        # Import lazily because the repository's URL canonicalization helpers
        # depend on this module during application startup.
        from discovery_repository import runtime_discovery_repository

        job_repository = runtime_discovery_repository()

    def check_cancelled() -> None:
        _cancellation_checkpoint(cancel_check)

    def report_progress(phase: str, completed: int = 0, total: int | None = None) -> None:
        if progress_update:
            progress_update(phase, completed, total)

    check_cancelled()
    profile = profile_repository.get_profile()
    if not profile or not profile["base_resume_text"]:
        return {"error": "Profile or base resume text is missing. Please setup your profile first."}
        
    resume_text = profile["base_resume_text"]
    ai_settings = settings_from_profile(profile)
    api_key = ai_settings.api_key
    
    # 2. Determine search terms
    report_progress("preparing_keywords", 0, 1)
    search_keywords = []
    from role_queries import execution_titles
    try:
        manual_titles = execution_titles(keywords or "")
    except ValueError as exc:
        return {"success": False, "failure_code": "keywords_unavailable",
                "error": str(exc), "jobs_added": 0, "provider_alerts": []}
    stripped_kw = keywords.strip() if keywords else ""
    if not stripped_kw or stripped_kw.lower() == "undefined":
        # Check database cache first
        cached_kw = profile["suggested_keywords"]
        if cached_kw:
            search_keywords = [k.strip() for k in cached_kw.split(",") if k.strip()]
            print(json.dumps({"event": "search_keyword_cache_used"}), flush=True)
        else:
            check_cancelled()
            search_keywords = extract_search_keywords_from_resume(
                resume_text,
                api_key,
                ai_provider=ai_settings.provider,
                ai_model=ai_settings.model,
            )
            check_cancelled()
            # Cache only when another request has not already populated it.
            if search_keywords:
                profile_repository.cache_suggested_keywords_if_empty(
                    ",".join(search_keywords)
                )
            print(json.dumps({"event": "search_keywords_prepared"}), flush=True)
    else:
        search_keywords = manual_titles
    from discovery_sources import bounded_terms, MAX_KEYWORDS, MAX_LOCATIONS
    search_keywords, keywords_limited = bounded_terms(search_keywords, MAX_KEYWORDS)
    if not search_keywords:
        return {"success": False, "failure_code": "keywords_unavailable",
                "error": "Search keywords could not be prepared. Enter a job title and try again.",
                "jobs_added": 0, "provider_alerts": []}
    report_progress("preparing_keywords", 1, 1)
        
    # 3. Parse locations (supports multiple semicolon or comma separated locations)
    locs = []
    if location:
        if ";" in location:
            locs = [l.strip() for l in location.split(";") if l.strip()]
        else:
            raw_parts = [p.strip() for p in location.split(",") if p.strip()]
            merged_parts = []
            for part in raw_parts:
                if len(part) == 2 and part.isupper() and merged_parts:
                    merged_parts[-1] = f"{merged_parts[-1]}, {part}"
                else:
                    merged_parts.append(part)
            locs = merged_parts
    if not locs:
        locs = [""]
    locs, locations_limited = bounded_terms(locs, MAX_LOCATIONS) if location else ([""], False)
    if not locs:
        return {"success": False, "failure_code": "keywords_unavailable",
                "error": "Enter a shorter search location and try again.", "jobs_added": 0}

    search_providers = list(PROVIDER_DOMAINS)
    if config.get_discovery_mode() == "hybrid_feeds":
        from public_job_feeds import PROVIDERS
        search_providers.extend(PROVIDERS)
        from additional_sources import ENABLED
        search_providers.extend(ENABLED)
    provider_health = {
        provider: {
            "raw_candidates": 0, "valid_discovered": 0, "new_candidates": 0,
            "accepted": 0, "rejected": 0, "skipped_active": 0,
            "skipped_archived": 0, "skipped_suppressed": 0,
            "errors": [], "api_fallbacks": 0,
            "candidate_budget_exhausted": False,
            "partial_results": {
                "oversized_responses": 0, "candidate_limit_hits": 0, "timeouts": 0,
            },
            "rejection_reasons": {
                "stale": 0, "format_drift": 0, "access_challenge": 0,
                "embedded_content": 0, "provider_error": 0,
            },
        }
        for provider in search_providers
    }

    # 4. Search Yahoo for matching Greenhouse/Lever postings
    results = []
    seen_urls = set()
    provider_candidate_counts = {provider: 0 for provider in search_providers}
    source_total = len(search_keywords) * len(locs)
    source_completed = 0
    source_state = {"yahoo_available": True}
    managed_successes = set()
    report_progress("searching_sources", source_completed, source_total)
    if keywords_limited or locations_limited:
        for item in provider_health.values():
            item["partial_results"]["planning_limits"] = 1
    if config.get_discovery_mode() == "hybrid_feeds":
        from additional_sources import collect as collect_additional_sources
        for item in collect_additional_sources(search_keywords, locs, provider_health, check_cancelled):
            check_cancelled()
            _append_candidate_with_budget(item, results, seen_urls, provider_candidate_counts, provider_health)
        from public_job_feeds import collect as collect_public_feeds
        for item in collect_public_feeds(search_keywords, locs, provider_health, check_cancelled):
            check_cancelled()
            _append_candidate_with_budget(item, results, seen_urls, provider_candidate_counts, provider_health)
        from discovery_sources import collect
        for item in collect(search_keywords, locs, provider_health, check_cancelled,
                            api_key=serper_api_key, reserve=reserve_search_request,
                            feeds_only=not bool(serper_api_key and reserve_search_request),
                            managed_successes=managed_successes):
            check_cancelled()
            _append_candidate_with_budget(item, results, seen_urls, provider_candidate_counts, provider_health)
    for kw, loc in ((kw, loc) for kw in search_keywords for loc in locs):
        check_cancelled()
        # A validated managed response (including empty or a full page) is the
        # primary discovery result. Legacy search remains the fallback for each
        # failed or unattempted pair after the managed circuit breaker opens.
        kw_results = [] if (kw, loc) in managed_successes else search_yahoo_jobs(
            kw,
            loc,
            provider_health,
            source_state=source_state,
        )
        check_cancelled()
        for item in kw_results:
            check_cancelled()
            _append_candidate_with_budget(
                item, results, seen_urls, provider_candidate_counts, provider_health
            )
        source_completed += 1
        report_progress("searching_sources", source_completed, source_total)
    
    # 4. Inspect, analyze, and commit bounded chunks. Completed chunks remain
    # useful if a later provider call fails or the user stops the search.
    pending_jobs = []
    catalog_context = job_repository.search_catalog_context()
    known_urls = catalog_context["known_status_by_url"]
    suppressed_fingerprints = catalog_context["suppressed_fingerprints"]
    from job_suppressions import job_url_fingerprint
    inspected_count = 0
    analyzed_count = 0
    scored_count = 0
    meeting_minimum_count = 0
    saved_count = 0
    matches_added = 0
    save_failures = 0
    ai_error = None
    ai_error_code = None
    report_progress("inspecting_postings", inspected_count, len(results))

    def process_pending_jobs() -> bool:
        nonlocal analyzed_count, saved_count, matches_added, ai_error, ai_error_code, save_failures
        nonlocal scored_count, meeting_minimum_count
        if not pending_jobs:
            return True
        batch = list(pending_jobs)
        pending_jobs.clear()
        report_progress("analyzing_matches", analyzed_count, len(results))
        batch_res = None
        for provider_attempt in range(1, MATCH_PROVIDER_MAX_ATTEMPTS + 1):
            try:
                check_cancelled()
                batch_res = analyze_job_matches_batch(
                    resume_text,
                    batch,
                    api_key,
                    ai_provider=ai_settings.provider,
                    ai_model=ai_settings.model,
                )
                check_cancelled()
            except OperationCancelled:
                raise
            except Exception:
                batch_res = {
                    "success": False,
                    "error": "The selected AI provider could not score the discovered jobs.",
                    "error_code": "provider_error",
                }

            if batch_res.get("success"):
                break

            attempt_error = (
                batch_res.get("error")
                or "The selected AI provider could not score the discovered jobs."
            )
            candidate_code = str(
                batch_res.get("error_code") or "provider_error"
            ).strip().lower()
            attempt_error_code = (
                candidate_code
                if candidate_code in MATCH_PROVIDER_FAILURE_CODES
                else "provider_error"
            )
            retryable = attempt_error_code in MATCH_PROVIDER_RETRYABLE_ERRORS
            if not retryable or provider_attempt >= MATCH_PROVIDER_MAX_ATTEMPTS:
                ai_error = attempt_error
                ai_error_code = attempt_error_code
                print(json.dumps({
                    "event": "search_match_provider_failed",
                    "provider": normalize_provider(ai_settings.provider),
                    "error_code": ai_error_code,
                    "attempt": provider_attempt,
                }, separators=(",", ":")), flush=True)
                return False

            print(json.dumps({
                "event": "search_match_provider_retry_scheduled",
                "provider": normalize_provider(ai_settings.provider),
                "error_code": attempt_error_code,
                "attempt": provider_attempt,
                "max_attempts": MATCH_PROVIDER_MAX_ATTEMPTS,
            }, separators=(",", ":")), flush=True)
            report_progress(
                "retrying_match_analysis",
                provider_attempt,
                MATCH_PROVIDER_MAX_ATTEMPTS,
            )
            _wait_for_match_retry(
                MATCH_PROVIDER_RETRY_DELAYS_SECONDS[provider_attempt - 1],
                check_cancelled,
            )
            report_progress("analyzing_matches", analyzed_count, len(results))

        if not batch_res or not batch_res.get("success"):
            return False

        batch_results = {
            match.get("index"): {
                "score": match.get("match_score"),
                "analysis": match.get("match_analysis", "Matched successfully."),
            }
            for match in batch_res.get("matches", [])
            if match.get("index") is not None
        }
        analyzed_count += len(batch)
        report_progress("analyzing_matches", analyzed_count, len(results))
        report_progress("saving_matches", saved_count, len(results))
        for index, job in enumerate(batch):
            check_cancelled()
            match_info = batch_results.get(index)
            if match_info:
                try:
                    score = max(0, min(100, int(match_info["score"])))
                except (KeyError, TypeError, ValueError):
                    score = -1
                if score >= 0:
                    scored_count += 1
                if score >= MIN_MATCH_SCORE:
                    meeting_minimum_count += 1
                    try:
                        created = job_repository.create_job_if_available(
                            title=job["title"],
                            company=job["company"],
                            description=job["description"],
                            url=job["url"],
                            match_score=score,
                            match_analysis=match_info["analysis"],
                            date_found=date.today(),
                            location=job.get("location", ""),
                            work_arrangement=job.get("work_arrangement", ""),
                            employment_type=job.get("employment_type", ""),
                            compensation=job.get("compensation", ""),
                            source=job.get("source", provider_for_url(job["url"])),
                            last_checked_at=datetime.now(timezone.utc),
                        )
                        if created["outcome"] == "created":
                            matches_added += 1
                    except OperationCancelled:
                        raise
                    except Exception:
                        save_failures += 1
                        print(json.dumps({"event": "search_job_save_failed"}), flush=True)
            saved_count += 1
            report_progress("saving_matches", saved_count, len(results))
        return True

    for item in results:
        check_cancelled()
        url = item["url"]
        provider = provider_for_url(url)
        accepted = False
        if job_url_fingerprint(url) in suppressed_fingerprints:
            if provider in provider_health:
                provider_health[provider]["skipped_suppressed"] += 1
        elif url in known_urls:
            key = "skipped_archived" if known_urls[url] == "archived" else "skipped_active"
            if provider in provider_health:
                provider_health[provider][key] += 1
        else:
            if provider in provider_health:
                provider_health[provider]["new_candidates"] += 1
            outcome = inspect_job_posting(url, cancel_check=check_cancelled)
            check_cancelled()
            job_details = outcome["details"]
            if provider in provider_health and outcome.get("used_browser_fallback"):
                provider_health[provider]["api_fallbacks"] += 1
            if not job_details or not job_details.get("title"):
                if provider in provider_health:
                    provider_health[provider]["rejected"] += 1
                    reason = outcome.get("status", "provider_error")
                    reason = reason if reason in provider_health[provider]["rejection_reasons"] else "provider_error"
                    provider_health[provider]["rejection_reasons"][reason] += 1
            else:
                job_details = _supplement_feed_metadata(job_details, item)
                from candidate_filters import role_matches, detail_location_matches
                role_ok = not item.get('verify_query') or role_matches(job_details['title'], search_keywords)
                place_ok = not item.get('verify_query') or detail_location_matches(job_details, locs)
                if role_ok and place_ok:
                    pending_jobs.append(_bounded_job_details(job_details))
                    accepted = True
                    if provider in provider_health:
                        provider_health[provider]["accepted"] += 1
                elif provider in provider_health:
                    provider_health[provider]['rejected'] += 1
                    reasons = provider_health[provider]['rejection_reasons']
                    code = 'query_mismatch' if not role_ok else 'eligibility_unknown'
                    reasons[code] = reasons.get(code, 0) + 1
        inspected_count += 1
        report_progress("inspecting_postings", inspected_count, len(results))
        if accepted and len(pending_jobs) >= MATCH_BATCH_SIZE and not process_pending_jobs():
            break

    if ai_error is None:
        process_pending_jobs()
            
    check_cancelled()
    return _search_completion(
        provider_health, len(results), inspected_count, analyzed_count,
        matches_added, ai_error, ai_error_code, save_failures,
        scored=scored_count, meeting_minimum=meeting_minimum_count,
    )
