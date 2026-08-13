"""Local application-funnel and response-rate reporting."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime
import sqlite3


RESPONSE_STATUSES = frozenset({"interview", "offer", "rejected"})
POSITIVE_RESPONSE_STATUSES = frozenset({"interview", "offer"})
DIMENSIONS = ("source", "role", "location", "resume", "method")
MAX_GROUPS_PER_DIMENSION = 50


def _clean_label(value: object, fallback: str) -> str:
    label = " ".join(str(value or "").split())
    return label[:160] or fallback


def _source_label(value: object) -> str:
    label = _clean_label(value, "Unknown source")
    normalized = label.lower().removeprefix("www.").rstrip("/")
    aliases = {
        "ashby": "Ashby",
        "ashbyhq.com": "Ashby",
        "eightfold": "Eightfold",
        "eightfold.ai": "Eightfold",
        "greenhouse": "Greenhouse",
        "greenhouse.io": "Greenhouse",
        "icims": "iCIMS",
        "icims.com": "iCIMS",
        "indeed.com": "Indeed",
        "lever": "Lever",
        "lever.co": "Lever",
        "linkedin.com": "LinkedIn",
        "smartrecruiters": "SmartRecruiters",
        "smartrecruiters.com": "SmartRecruiters",
        "workday": "Workday",
        "workday.com": "Workday",
    }
    return aliases.get(normalized, label.replace("_", " ").replace("-", " ").title())


def _method_label(value: object) -> str:
    method = _clean_label(value, "Unknown method")
    if method.startswith("manual:"):
        method = method.split(":", 1)[1]
    labels = {
        "company_site": "Company site",
        "job_board": "Job board",
        "email": "Email",
        "recruiter": "Recruiter",
        "referral": "Referral",
        "other": "Other",
        "legacy": "Legacy record",
    }
    return labels.get(method.lower(), method.replace("_", " ").title())


def _resume_label(name: object, version: object) -> str:
    resume_name = _clean_label(name, "")
    if not resume_name:
        return "Unattributed resume"
    try:
        version_number = int(version)
    except (TypeError, ValueError):
        return resume_name
    return f"{resume_name} · v{version_number}"


def _rate(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 1) if denominator else 0.0


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _milestone_date(
    rows: list[Mapping[str, object]], statuses: frozenset[str]
) -> date | None:
    dates = [
        _parse_date(row["changed_at"])
        for row in rows
        if row["to_status"] in statuses and not row["undone_at"]
    ]
    return min((value for value in dates if value is not None), default=None)


def _new_bucket() -> dict[str, object]:
    return {
        "applications": 0,
        "responses": 0,
        "positive_responses": 0,
        "interviews": 0,
        "offers": 0,
        "rejections": 0,
        "response_days": [],
    }


def _add_outcome(bucket: dict[str, object], outcome: dict[str, object]) -> None:
    bucket["applications"] += 1
    for key in ("responses", "positive_responses", "interviews", "offers", "rejections"):
        bucket[key] += int(bool(outcome[key]))
    if outcome["response_days"] is not None:
        bucket["response_days"].append(outcome["response_days"])


def _present_bucket(bucket: dict[str, object]) -> dict[str, object]:
    applications = int(bucket["applications"])
    responses = int(bucket["responses"])
    positive_responses = int(bucket["positive_responses"])
    response_days = bucket["response_days"]
    return {
        "applications": applications,
        "responses": responses,
        "positive_responses": positive_responses,
        "interviews": int(bucket["interviews"]),
        "offers": int(bucket["offers"]),
        "rejections": int(bucket["rejections"]),
        "pending": max(applications - responses, 0),
        "response_rate": _rate(responses, applications),
        "positive_response_rate": _rate(positive_responses, applications),
        "average_response_days": (
            round(sum(response_days) / len(response_days), 1)
            if response_days else None
        ),
    }


def build_application_insights_from_rows(
    application_rows: Iterable[Mapping[str, object]],
    history_rows: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Summarize already owner-scoped application rows without external calls."""
    applications = list(application_rows)
    histories = list(history_rows)
    history_by_job: dict[int, list[Mapping[str, object]]] = defaultdict(list)
    for history in histories:
        history_by_job[int(history["job_id"])].append(history)

    summary = _new_bucket()
    groups: dict[str, dict[str, dict[str, object]]] = {
        dimension: defaultdict(_new_bucket) for dimension in DIMENSIONS
    }
    for application in applications:
        history = history_by_job.get(int(application["job_id"] or 0), [])
        reached = {
            row["to_status"] for row in history if not row["undone_at"]
        }
        reached.add(application["status"])
        response = bool(reached & RESPONSE_STATUSES)
        positive_response = bool(reached & POSITIVE_RESPONSE_STATUSES)
        applied_on = _parse_date(application["date_applied"] or application["confirmed_at"])
        responded_on = _milestone_date(history, RESPONSE_STATUSES)
        response_days = None
        if applied_on and responded_on and responded_on >= applied_on:
            response_days = (responded_on - applied_on).days
        outcome = {
            "responses": response,
            "positive_responses": positive_response,
            "interviews": "interview" in reached,
            "offers": "offer" in reached,
            "rejections": "rejected" in reached,
            "response_days": response_days,
        }
        _add_outcome(summary, outcome)
        labels = {
            "source": _source_label(application["source"]),
            "role": _clean_label(application["position"], "Untitled role"),
            "location": _clean_label(application["location"], "Unspecified location"),
            "resume": _resume_label(application["base_resume_name"], application["base_resume_version"]),
            "method": _method_label(application["application_method"]),
        }
        for dimension, label in labels.items():
            _add_outcome(groups[dimension][label], outcome)

    presented_groups = {}
    for dimension, buckets in groups.items():
        values = [
            {"label": label, **_present_bucket(bucket)}
            for label, bucket in buckets.items()
        ]
        values.sort(key=lambda item: (-item["applications"], -item["responses"], item["label"].lower()))
        presented_groups[dimension] = values[:MAX_GROUPS_PER_DIMENSION]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "definitions": {
            "application": "A user-confirmed application in an applied or later lifecycle state.",
            "response": "An interview, offer, or rejection recorded in the current lifecycle or its active history.",
            "positive_response": "An interview or offer recorded in the current lifecycle or its active history.",
        },
        "summary": _present_bucket(summary),
        "groups": presented_groups,
    }


def build_application_insights(connection: sqlite3.Connection) -> dict[str, object]:
    """Summarize confirmed applications from the standalone SQLite store."""
    applications = connection.execute("""
        SELECT a.job_id, a.position, a.date_applied, a.confirmed_at,
               a.application_method, a.base_resume_name, a.base_resume_version,
               a.status, j.source, j.location
        FROM applications a
        LEFT JOIN jobs j ON j.id = a.job_id
        WHERE a.status IN ('applied','interview','offer','rejected','withdrawn','closed')
        ORDER BY COALESCE(a.date_applied, a.confirmed_at, a.created_at), a.id
    """).fetchall()
    histories = connection.execute("""
        SELECT h.job_id, h.to_status, h.changed_at, h.undone_at
        FROM application_status_history h
        INNER JOIN applications a ON a.job_id = h.job_id
        WHERE a.status IN ('applied','interview','offer','rejected','withdrawn','closed')
        ORDER BY h.changed_at, h.id
    """).fetchall()
    return build_application_insights_from_rows(applications, histories)
