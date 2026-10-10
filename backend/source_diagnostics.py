"""Privacy-safe persistence for job-source health notices."""

import json
import re
import sqlite3
from datetime import date


ALLOWED_PROVIDERS = frozenset({"greenhouse", "lever", "ashby", "smartrecruiters", "weworkremotely", "jobicy"})
ALLOWED_CODES = frozenset({
    "url_format_drift",
    "content_format_drift",
    "access_challenge",
    "provider_error",
    "stale_postings",
    "partial_results",
})
INFORMATIONAL_CODES = frozenset({"stale_postings", "partial_results"})
MAX_COUNTER_VALUE = 1_000_000
MAX_ALERTS_PER_RUN = 20
MAX_HISTORY_RECORDS = 500
DEFAULT_HISTORY_LIMIT = 100
MAX_HISTORY_LIMIT = 500
MAX_REPORT_GROUPS = 16

REPORTABLE_CODES = frozenset({
    "url_format_drift",
    "content_format_drift",
    "access_challenge",
    "provider_error",
})

DIRECT_COUNTERS = (
    "raw_candidates",
    "valid_discovered",
    "new_candidates",
    "accepted",
    "rejected",
    "skipped_active",
    "skipped_archived",
    "skipped_suppressed",
    "api_fallbacks",
    "source_attempts",
    "source_failures",
    "source_unusable",
    "source_empty",
    "source_fallbacks",
    "coverage_pages_read", "coverage_cached_pages", "coverage_http_attempts",
    "coverage_snapshots_read", "coverage_rows_read", "coverage_queries_with_total",
    "coverage_advertised_rows", "coverage_unlisted_rows", "coverage_role_excluded",
    "coverage_location_excluded", "coverage_location_unknown", "coverage_metadata_matches",
    "coverage_duplicate_candidates", "coverage_detail_cap_excluded",
    "coverage_complete_boards", "coverage_failed_boards",
)
REJECTION_COUNTERS = (
    "stale",
    "format_drift",
    "access_challenge",
    "embedded_content",
    "provider_error",
    "query_mismatch", "eligibility_unknown",
)
PARTIAL_COUNTERS = ("oversized_responses", "candidate_limit_hits", "timeouts", "planning_limits",
                    "catalog_page_limits", "catalog_request_limits", "catalog_term_limits",
                    "unknown_location_limit", "managed_page_limits")
STORED_COUNTERS = DIRECT_COUNTERS + REJECTION_COUNTERS + PARTIAL_COUNTERS + (
    "search_errors",
    "candidate_budget_exhausted",
)
REPORT_COUNTERS = (
    "raw_candidates",
    "valid_discovered",
    "new_candidates",
    "accepted",
    "rejected",
    "api_fallbacks",
    "format_drift",
    "access_challenge",
    "embedded_content",
    "provider_error",
    "timeouts",
    "search_errors",
)

_SAFE_BUILD_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_ISO_DAY_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _bounded_counter(value: object) -> int:
    """Convert an aggregate to a bounded, non-negative integer."""
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_COUNTER_VALUE, numeric))


def safe_provider_counters(health: dict | None) -> dict[str, int]:
    """Select only aggregate counters that cannot reveal search or job data."""
    health = health if isinstance(health, dict) else {}
    reasons = health.get("rejection_reasons")
    reasons = reasons if isinstance(reasons, dict) else {}
    partial = health.get("partial_results")
    partial = partial if isinstance(partial, dict) else {}

    counters = {name: _bounded_counter(health.get(name)) for name in DIRECT_COUNTERS}
    counters.update({name: _bounded_counter(reasons.get(name)) for name in REJECTION_COUNTERS})
    counters.update({name: _bounded_counter(partial.get(name)) for name in PARTIAL_COUNTERS})
    errors = health.get("errors")
    counters["search_errors"] = _bounded_counter(len(errors) if isinstance(errors, list) else 0)
    counters["candidate_budget_exhausted"] = int(bool(health.get("candidate_budget_exhausted")))
    return counters


def persist_source_diagnostics(
    connection: sqlite3.Connection,
    search_result: dict | None,
    recorded_at: str,
) -> int:
    """Persist allowlisted notices and aggregates, then enforce local retention."""
    if not isinstance(search_result, dict):
        return 0
    alerts = search_result.get("provider_alerts")
    health_by_provider = search_result.get("provider_health")
    if not isinstance(alerts, list) or not isinstance(health_by_provider, dict):
        return 0

    inserted = 0
    for alert in alerts[:MAX_ALERTS_PER_RUN]:
        if not isinstance(alert, dict):
            continue
        provider = str(alert.get("provider") or "").lower()
        code = str(alert.get("code") or "").lower()
        if provider not in ALLOWED_PROVIDERS or code not in ALLOWED_CODES:
            continue
        counters = safe_provider_counters(health_by_provider.get(provider))
        connection.execute(
            """
            INSERT INTO source_diagnostics (recorded_at, provider, diagnostic_code, counters_json)
            VALUES (?, ?, ?, ?)
            """,
            (recorded_at, provider, code, json.dumps(counters, sort_keys=True, separators=(",", ":"))),
        )
        inserted += 1

    if inserted:
        connection.execute(
            """
            DELETE FROM source_diagnostics
            WHERE id NOT IN (
                SELECT id FROM source_diagnostics
                ORDER BY recorded_at DESC, id DESC
                LIMIT ?
            )
            """,
            (MAX_HISTORY_RECORDS,),
        )
    return inserted


def list_source_diagnostics(connection: sqlite3.Connection, limit: int = DEFAULT_HISTORY_LIMIT) -> dict:
    """Return recent minimized diagnostics without raw errors or source messages."""
    safe_limit = max(1, min(MAX_HISTORY_LIMIT, int(limit)))
    count = connection.execute("SELECT COUNT(*) FROM source_diagnostics").fetchone()[0]
    rows = connection.execute(
        """
        SELECT id, recorded_at, provider, diagnostic_code, counters_json
        FROM source_diagnostics
        ORDER BY recorded_at DESC, id DESC
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    items = []
    for row in rows:
        try:
            stored = json.loads(row["counters_json"])
        except (json.JSONDecodeError, TypeError):
            stored = {}
        stored = stored if isinstance(stored, dict) else {}
        counters = {name: _bounded_counter(stored.get(name)) for name in STORED_COUNTERS}
        counters["candidate_budget_exhausted"] = int(bool(counters["candidate_budget_exhausted"]))
        items.append({
            "id": row["id"],
            "recorded_at": row["recorded_at"],
            "provider": row["provider"],
            "code": row["diagnostic_code"],
            "level": "note" if row["diagnostic_code"] in INFORMATIONAL_CODES else "attention",
            "counters": counters,
        })
    return {"count": count, "items": items}


def _safe_day(value: object) -> str:
    match = _ISO_DAY_PATTERN.match(str(value or ""))
    if not match:
        return "unknown"
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return "unknown"


def build_maintainer_report(
    history: dict | None,
    application_build: str,
    generated_on: str,
) -> dict:
    """Aggregate only allowlisted local diagnostics for an explicit user-reviewed report."""
    items = history.get("items") if isinstance(history, dict) else []
    items = items if isinstance(items, list) else []
    groups: dict[tuple[str, str], dict] = {}

    for item in items:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").lower()
        code = str(item.get("code") or "").lower()
        if provider not in ALLOWED_PROVIDERS or code not in REPORTABLE_CODES:
            continue
        recorded_at = str(item.get("recorded_at") or "")
        observed_on = _safe_day(recorded_at)
        stored = item.get("counters")
        stored = stored if isinstance(stored, dict) else {}
        counters = {name: _bounded_counter(stored.get(name)) for name in REPORT_COUNTERS}
        key = (provider, code)
        group = groups.setdefault(key, {
            "provider": provider,
            "code": code,
            "occurrences": 0,
            "first_seen_on": observed_on,
            "last_seen_on": observed_on,
            "latest_counters": counters,
            "_latest_recorded_at": recorded_at,
        })
        group["occurrences"] = min(MAX_HISTORY_RECORDS, group["occurrences"] + 1)
        known_days = [day for day in (group["first_seen_on"], observed_on) if day != "unknown"]
        group["first_seen_on"] = min(known_days) if known_days else "unknown"
        known_days = [day for day in (group["last_seen_on"], observed_on) if day != "unknown"]
        group["last_seen_on"] = max(known_days) if known_days else "unknown"
        if recorded_at >= group["_latest_recorded_at"]:
            group["_latest_recorded_at"] = recorded_at
            group["latest_counters"] = counters

    report_groups = sorted(
        groups.values(),
        key=lambda group: (-group["occurrences"], group["provider"], group["code"]),
    )[:MAX_REPORT_GROUPS]
    for group in report_groups:
        group.pop("_latest_recorded_at", None)

    normalized_build = str(application_build or "")
    safe_build = normalized_build if _SAFE_BUILD_PATTERN.fullmatch(normalized_build) else "unknown"
    return {
        "report_version": 1,
        "application_build": safe_build,
        "generated_on": _safe_day(generated_on),
        "reportable_event_count": sum(group["occurrences"] for group in report_groups),
        "repeated_group_count": sum(1 for group in report_groups if group["occurrences"] > 1),
        "diagnostic_groups": report_groups,
        "privacy": {
            "included": [
                "application build",
                "calendar dates",
                "allowlisted provider and diagnostic codes",
                "occurrence counts",
                "bounded aggregate source counters",
            ],
            "excluded": [
                "profile and resume content",
                "API keys and credentials",
                "search terms and locations",
                "job URLs, titles, employers, and descriptions",
                "application history and generated materials",
                "raw errors and browsing details",
            ],
        },
    }


def format_maintainer_report(report: dict) -> str:
    """Render a compact report that users can review before sharing with maintainers."""
    lines = [
        "## CareerTrellis source diagnostic report",
        "",
        f"- Report format: {int(report.get('report_version') or 1)}",
        f"- Application build: {report.get('application_build') or 'unknown'}",
        f"- Generated on: {report.get('generated_on') or 'unknown'}",
        f"- Reportable events: {int(report.get('reportable_event_count') or 0)}",
        f"- Repeated provider/code groups: {int(report.get('repeated_group_count') or 0)}",
        "",
        "### Sanitized diagnostic groups",
        "",
    ]
    for group in report.get("diagnostic_groups") or []:
        repeated = " (repeated)" if int(group.get("occurrences") or 0) > 1 else ""
        lines.append(
            f"- `{group.get('provider')}` / `{group.get('code')}`: "
            f"{int(group.get('occurrences') or 0)} occurrence(s){repeated}; "
            f"observed {group.get('first_seen_on')} through {group.get('last_seen_on')}"
        )
        counters = group.get("latest_counters") if isinstance(group.get("latest_counters"), dict) else {}
        values = [
            f"{name}={_bounded_counter(counters.get(name))}"
            for name in REPORT_COUNTERS
            if _bounded_counter(counters.get(name)) > 0
        ]
        lines.append(f"  - Latest bounded counters: {', '.join(values) if values else 'none recorded'}")

    lines.extend([
        "",
        "### Privacy review",
        "",
        "This report was generated from CareerTrellis's minimized local source-diagnostic history.",
        "It excludes profile/resume content, credentials, searches, locations, job and application data, URLs, raw errors, and browsing details.",
        "Nothing is submitted automatically; the user reviewed this text before sharing it.",
    ])
    return "\n".join(lines)
