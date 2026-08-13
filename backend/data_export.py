"""Pure, deterministic serialization for the portable user-data export."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
import json
from typing import Any


EXPORT_FORMAT = "career-trellis-user-data"
EXPORT_SCHEMA_VERSION = 3


# This allowlist is the public export contract. Persistence repositories may
# return no additional fields, and the serializer applies it again as a
# defense-in-depth boundary before bytes are produced for a response.
EXPORT_FIELDS = {
    "profile": (
        "id", "name", "email", "phone", "github", "linkedin", "website",
        "base_resume_text", "suggested_keywords", "ai_provider", "ai_model",
        "maps_provider", "prefer_us_headquarters", "interface_language", "resume_mode",
        "active_base_resume_id",
    ),
    "base_resumes": (
        "id", "name", "resume_mode", "content", "professional_evidence",
        "created_at", "updated_at",
    ),
    "base_resume_versions": (
        "id", "base_resume_id", "version_number", "name", "resume_mode", "content",
        "professional_evidence", "created_at",
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
        "id", "recorded_at", "provider", "diagnostic_code", "counters",
    ),
}


_JSON_FIELDS = {
    ("base_resumes", "professional_evidence"),
    ("base_resume_versions", "professional_evidence"),
    ("applications", "submission_evidence"),
    ("source_diagnostics", "counters"),
}
_BOOLEAN_FIELDS = {
    ("profile", "prefer_us_headquarters"),
    ("jobs", "is_expired"),
    ("saved_searches", "enabled"),
}


def _portable_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _portable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_portable_value(item) for item in value]
    return value


def _json_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            pass
    return _portable_value(value)


def _public_record(table: str, raw_record: Mapping[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field in EXPORT_FIELDS[table]:
        if field not in raw_record:
            continue
        value = raw_record[field]
        if (table, field) in _JSON_FIELDS:
            value = _json_value(value)
        elif (table, field) in _BOOLEAN_FIELDS:
            value = bool(value)
        else:
            value = _portable_value(value)
        record[field] = value
    return record


def build_user_data_export(
    snapshot: Mapping[str, list[Mapping[str, Any]]],
    *,
    exported_at: str,
    application_build: str,
) -> dict[str, Any]:
    """Serialize one already-closed repository snapshot into the public format."""
    records = {
        table: [_public_record(table, row) for row in snapshot.get(table, [])]
        for table in EXPORT_FIELDS
    }
    profiles = records.pop("profile")
    counts = {name: len(items) for name, items in records.items()}
    counts["profiles"] = len(profiles)

    return {
        "format": EXPORT_FORMAT,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": exported_at,
        "application_build": application_build,
        "scope": {
            "purpose": "Portable, human-readable copy of stored job-search data.",
            "excluded": [
                "Stored provider credentials and encryption material",
                "Generated-object identifiers, keys, storage locations, and local paths",
                "Owner identifiers, internal URL fingerprints, and provider caches",
            ],
        },
        "counts": counts,
        "profile": profiles[0] if profiles else None,
        **records,
    }
