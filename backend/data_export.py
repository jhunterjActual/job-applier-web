import json
import sqlite3
from typing import Any


EXPORT_FORMAT = "career-trellis-user-data"
EXPORT_SCHEMA_VERSION = 3


EXPORT_FIELDS = {
    "profile": (
        "id", "name", "email", "phone", "github", "linkedin", "website",
        "base_resume_text", "suggested_keywords", "ai_provider", "ai_model",
        "maps_provider", "prefer_us_headquarters", "interface_language", "resume_mode",
        "active_base_resume_id",
    ),
    "base_resumes": (
        "id", "name", "resume_mode", "content", "evidence_json", "created_at", "updated_at",
    ),
    "base_resume_versions": (
        "id", "base_resume_id", "version_number", "name", "resume_mode", "content",
        "evidence_json", "created_at",
    ),
    "jobs": (
        "id", "title", "company", "description", "url", "match_score", "match_analysis",
        "date_found", "status", "archived_at", "archived_from_status", "last_checked_at",
        "is_expired", "expiration_reason", "location", "work_arrangement", "employment_type",
        "compensation", "source",
    ),
    "saved_searches": (
        "id", "name", "keywords", "location", "created_at", "last_run_at",
        "schedule_frequency", "next_alert_at", "enabled",
    ),
    "applications": (
        "id", "job_id", "company", "position", "date_applied", "us_hq",
        "tailored_resume_text", "cover_letter", "status", "created_at", "tailored_at",
        "form_filled_at", "submitted_at", "confirmed_at", "application_method",
        "submission_evidence", "notes", "follow_up_date", "headquarters_source",
        "headquarters_attribution", "base_resume_id", "base_resume_name",
        "base_resume_version", "interview_prep", "interview_prep_updated_at",
    ),
    "application_status_history": (
        "id", "job_id", "from_status", "to_status", "changed_at", "source", "notes", "undone_at",
    ),
    "application_engagements": (
        "id", "job_id", "engagement_type", "name", "organization", "contact_details",
        "status", "activity_on", "next_action_on", "notes", "created_at", "updated_at",
    ),
    "job_suppressions": (
        "id", "hostname", "company", "title", "deleted_at", "deletion_source",
    ),
    "source_diagnostics": (
        "id", "recorded_at", "provider", "diagnostic_code", "counters_json",
    ),
}


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _export_rows(
    connection: sqlite3.Connection,
    table: str,
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    available = _table_columns(connection, table)
    selected = [field for field in fields if field in available]
    if not selected:
        return []
    projection = ", ".join(f'"{field}"' for field in selected)
    rows = connection.execute(f'SELECT {projection} FROM "{table}" ORDER BY rowid').fetchall()
    return [dict(zip(selected, row)) for row in rows]


def _decode_json_field(record: dict[str, Any], stored_name: str, exported_name: str) -> None:
    raw_value = record.pop(stored_name, None)
    if raw_value in (None, ""):
        record[exported_name] = None
        return
    try:
        record[exported_name] = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        record[exported_name] = raw_value


def build_user_data_export(
    connection: sqlite3.Connection,
    *,
    exported_at: str,
    application_build: str,
) -> dict[str, Any]:
    """Build an explicit, portable export without credentials or host-only paths."""
    records = {
        table: _export_rows(connection, table, fields)
        for table, fields in EXPORT_FIELDS.items()
    }

    for resume in records["base_resumes"]:
        _decode_json_field(resume, "evidence_json", "professional_evidence")
    for version in records["base_resume_versions"]:
        _decode_json_field(version, "evidence_json", "professional_evidence")
    for application in records["applications"]:
        _decode_json_field(application, "submission_evidence", "submission_evidence")
    for diagnostic in records["source_diagnostics"]:
        _decode_json_field(diagnostic, "counters_json", "counters")

    for profile in records["profile"]:
        if "prefer_us_headquarters" in profile:
            profile["prefer_us_headquarters"] = bool(profile["prefer_us_headquarters"])
    for job in records["jobs"]:
        if "is_expired" in job:
            job["is_expired"] = bool(job["is_expired"])
    for search in records["saved_searches"]:
        if "enabled" in search:
            search["enabled"] = bool(search["enabled"])

    profile = records.pop("profile")
    counts = {name: len(items) for name, items in records.items()}
    counts["profiles"] = len(profile)

    return {
        "format": EXPORT_FORMAT,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": exported_at,
        "application_build": application_build,
        "scope": {
            "purpose": "Portable, human-readable copy of locally stored job-search data.",
            "excluded": [
                "Stored AI and maps API keys",
                "Generated-material filesystem paths",
                "Internal URL fingerprints and provider caches",
            ],
        },
        "counts": counts,
        "profile": profile[0] if profile else None,
        **records,
    }
