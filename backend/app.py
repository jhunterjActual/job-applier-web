import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from typing import Literal, Optional
from datetime import date, datetime, timedelta

import config
from ai_providers import (
    AIProviderError,
    default_model,
    extract_text_from_images,
    normalize_provider,
    provider_key_configured,
    settings_from_profile,
    validate_provider_capability,
)
from maps_providers import (
    HeadquartersResult,
    MapsProviderError,
    maps_provider_ready,
    maps_settings_from_profile,
    normalize_maps_provider,
    resolve_headquarters,
    validate_maps_provider,
)
from application_insights import build_application_insights
from application_engagements import (
    EngagementLimitError,
    EngagementNotFound,
    create_engagement,
    delete_engagement,
    list_engagements,
    update_engagement,
)
from data_export import build_user_data_export
from database import get_db_connection, init_db
from backup_restore import (
    MAXIMUM_BACKUP_BYTES,
    BackupAuthenticationError,
    BackupCompatibilityError,
    BackupPasswordError,
    BackupRestoreError,
    create_encrypted_backup,
    restore_encrypted_backup,
)
from tailor import analyze_job_match, apply_resume_section_template, finalize_cover_letter, tailor_resume_and_cover_letter
from searcher import (
    MAX_JOB_DESCRIPTION_CHARS,
    canonicalize_job_url,
    inspect_job_posting,
    provider_for_url,
    run_job_search_and_matching,
    validate_public_http_url,
)
from utils import generate_resume_pdf, resume_pdf_metadata
from lifecycle import undo_latest_lifecycle_change, update_lifecycle
from job_cleanup import apply_cleanup, cleanup_preview
from job_suppressions import is_job_suppressed, record_job_suppression
from job_filters import derive_job_filter_facets
from interview_prep import (
    MAX_INTERVIEW_PREP_CHARS,
    generate_interview_prep,
    save_interview_prep,
    starter_interview_prep,
)
from source_diagnostics import (
    build_maintainer_report,
    format_maintainer_report,
    list_source_diagnostics,
    persist_source_diagnostics,
)
from materials import cover_letter_output_path, material_download_name, persist_cover_letter, resolve_output_file
from resume_documents import ResumeDocumentError, build_accessible_resume_docx, import_resume_document
from base_resumes import (
    BaseResumeNotFound,
    LastBaseResumeError,
    MAX_BASE_RESUME_CHARS,
    MAX_BASE_RESUME_NAME,
    MAX_EVIDENCE_FIELD_CHARS,
    activate_base_resume,
    delete_base_resume,
    get_base_resume,
    get_version,
    list_base_resumes,
    list_versions,
    professional_evidence_markdown,
    restore_version,
    save_base_resume,
)
from operations import (
    OperationCancelled,
    OperationToken,
    finish_operation,
    operation_scope,
    request_cancellation,
    start_operation,
)
from analytics import (
    capture_event,
    duration_bucket,
    initialize_analytics,
    shutdown_analytics,
    source_category,
)
from observability import initialize_sentry, sentry_debug_enabled

APP_BUILD = "20260808.23"
MAX_RESUME_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_RESUME_DOCUMENT_UPLOAD_BYTES = 10 * 1024 * 1024
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}
BACKUP_RESTORE_LOCK = Lock()

initialize_sentry(APP_BUILD)


def _headquarters_cache_key(
    provider: str,
    company: str,
    prefer_us: bool,
    job_location: str,
    job_url: str,
) -> str:
    """Fingerprint only bounded lookup context, never a complete posting URL."""
    hostname = (urlsplit(job_url).hostname or "").lower()
    context = "\n".join(
        (
            normalize_maps_provider(provider),
            " ".join(company.lower().split()),
            "us" if prefer_us else "global",
            " ".join((job_location or "").lower().split()),
            hostname,
        )
    )
    return hashlib.sha256(context.encode("utf-8")).hexdigest()


def _cached_headquarters(cache_key: str) -> HeadquartersResult | None:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT provider, address, country_code, attribution FROM headquarters_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    if not row:
        return None
    return HeadquartersResult(
        address=row["address"],
        source=row["provider"],
        attribution=row["attribution"],
        country_code=row["country_code"],
    )


def _cache_headquarters(cache_key: str, result: HeadquartersResult, selected_provider: str) -> None:
    """Cache provider results; AI fallbacks remain retryable and explicitly provisional."""
    provider = normalize_maps_provider(selected_provider)
    # The public Nominatim policy asks applications to cache results. Google
    # Places content has separate storage restrictions, so it is never added to
    # this lookup cache (application history remains a distinct user record).
    if provider != "openstreetmap" or result.address == "Unknown" or result.source != provider:
        return
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO headquarters_cache (
                cache_key, provider, address, country_code, attribution, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                provider,
                result.address,
                result.country_code,
                result.attribution,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start optional analytics and stop it within a bounded shutdown budget."""
    initialize_analytics(APP_BUILD)
    try:
        yield
    finally:
        shutdown_analytics(timeout_seconds=0.5)


app = FastAPI(title="CareerTrellis API", lifespan=lifespan)


def _local_authority(authority: str, scheme: str = "http") -> Optional[tuple[str, int]]:
    """Return a normalized loopback host and port, or None for an unsafe authority."""
    if not authority or any(character in authority for character in "/\\@"):
        return None
    try:
        parsed = urlsplit(f"//{authority}")
        hostname = (parsed.hostname or "").lower()
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    if hostname not in LOCAL_HOSTS:
        return None
    return hostname, port


def _same_local_origin(value: str, request_authority: tuple[str, int], scheme: str) -> bool:
    """Validate an Origin or Referer value against the exact local request origin."""
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    return (
        parsed.scheme == scheme
        and hostname == request_authority[0]
        and port == request_authority[1]
        and parsed.username is None
        and parsed.password is None
    )


@app.middleware("http")
async def protect_local_browser_boundary(request: Request, call_next):
    """Enforce the loopback-only browser trust boundary and prevent cross-site writes."""
    request_authority = _local_authority(request.headers.get("host", ""), request.url.scheme)
    if request_authority is None:
        return JSONResponse({"detail": "Invalid local Host header."}, status_code=400)

    if request.method.upper() not in SAFE_HTTP_METHODS:
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if fetch_site in {"cross-site", "same-site"}:
            return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)

        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        source = origin or referer
        if source and not _same_local_origin(source, request_authority, request.url.scheme):
            return JSONResponse({"detail": "Request origin does not match this local app."}, status_code=403)

    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

class ProfessionalEvidenceUpdate(BaseModel):
    skills: str = Field(default="", max_length=MAX_EVIDENCE_FIELD_CHARS)
    projects: str = Field(default="", max_length=MAX_EVIDENCE_FIELD_CHARS)
    portfolio: str = Field(default="", max_length=MAX_EVIDENCE_FIELD_CHARS)
    licenses: str = Field(default="", max_length=MAX_EVIDENCE_FIELD_CHARS)
    certifications: str = Field(default="", max_length=MAX_EVIDENCE_FIELD_CHARS)
    work_samples: str = Field(default="", max_length=MAX_EVIDENCE_FIELD_CHARS)


class ProfileUpdate(BaseModel):
    name: str
    email: str
    phone: str
    github: str
    linkedin: str
    website: str
    base_resume_id: Optional[int] = Field(default=None, gt=0)
    base_resume_name: str = Field(default="Primary Resume", min_length=1, max_length=MAX_BASE_RESUME_NAME)
    base_resume_text: str = Field(max_length=MAX_BASE_RESUME_CHARS)
    professional_evidence: Optional[ProfessionalEvidenceUpdate] = None
    resume_mode: Literal["it", "technical_executive", "general_professional", "federal", "healthcare", "education", "sales", "trades_operations", "academic_cv", "cover_letter"] = "general_professional"
    ai_provider: Literal["gemini", "openai"] = "gemini"
    ai_model: str = Field(default="gemini-2.5-flash", min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:/-]+$")
    maps_provider: Literal["google", "openstreetmap"] = "openstreetmap"
    prefer_us_headquarters: bool = True
    interface_language: Literal["en", "es"] = "en"


class ProfileSecretsUpdate(BaseModel):
    gemini_api_key: Optional[str] = Field(default=None, max_length=512)
    openai_api_key: Optional[str] = Field(default=None, max_length=512)
    google_maps_api_key: Optional[str] = Field(default=None, max_length=512)

class SearchRequest(BaseModel):
    keywords: str
    location: Optional[str] = ""
    save_search: bool = False
    saved_search_name: Optional[str] = None
    saved_search_id: Optional[int] = None
    schedule_frequency: Literal["none", "daily", "weekly"] = "none"


class ManualJobPreviewRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class ManualJobSaveRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    title: str = Field(min_length=2, max_length=180)
    company: str = Field(min_length=2, max_length=180)
    description: str = Field(min_length=20, max_length=MAX_JOB_DESCRIPTION_CHARS)
    location: str = Field(default="", max_length=240)
    work_arrangement: Literal["", "remote", "hybrid", "on_site"] = ""
    employment_type: Literal["", "full_time", "part_time", "contract", "temporary", "internship"] = ""
    compensation: str = Field(default="", max_length=160)

class CleanupRequest(BaseModel):
    action: Literal["archive", "delete", "restore"]
    preview_token: str


class LifecycleUpdateRequest(BaseModel):
    status: Literal["matched", "tailored", "form_filled", "submitted", "applied", "interview", "offer", "rejected", "withdrawn", "closed"]
    applied_on: Optional[date] = None
    method: Optional[Literal["company_site", "job_board", "email", "recruiter", "referral", "other"]] = None
    notes: Optional[str] = None
    follow_up_on: Optional[date] = None


class MaterialsUpdateRequest(BaseModel):
    tailored_resume: str
    cover_letter: str


class InterviewPrepUpdateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_INTERVIEW_PREP_CHARS)


class ApplicationEngagementRequest(BaseModel):
    engagement_type: Literal["recruiter", "hiring_manager", "referral", "networking", "assessment"]
    name: str = Field(min_length=1, max_length=160)
    organization: str = Field(default="", max_length=160)
    contact_details: str = Field(default="", max_length=320)
    status: Literal["planned", "contacted", "waiting", "scheduled", "completed", "closed"] = "planned"
    activity_on: Optional[date] = None
    next_action_on: Optional[date] = None
    notes: str = Field(default="", max_length=4000)


class FullBackupRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


@app.get("/api/version")
def get_app_version() -> dict:
    """Identify the running backend so launchers never open a stale instance."""
    return {"build": APP_BUILD}


@app.get("/sentry-debug", include_in_schema=False)
def trigger_sentry_debug_error() -> None:
    """Raise a test error only while explicit local Sentry verification is enabled."""
    if not sentry_debug_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    raise RuntimeError("Intentional Sentry verification error")

# Profile endpoints
@app.get("/api/profile")
def get_profile() -> dict:
    """
    Retrieve the current candidate profile settings from the database.
    
    Returns:
        dict: Profile details plus secret-presence flags; never plaintext keys.
    """
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM profile LIMIT 1").fetchone()
    conn.close()
    if row:
        result = dict(row)
        result["gemini_api_key_configured"] = provider_key_configured(row, "gemini")
        result["openai_api_key_configured"] = provider_key_configured(row, "openai")
        result.pop("gemini_api_key", None)
        result.pop("openai_api_key", None)
        result["google_maps_api_key_configured"] = bool(
            result.pop("google_maps_api_key", "") or config.get_google_maps_api_key()
        )
        result["ai_provider"] = normalize_provider(result.get("ai_provider"))
        result["ai_model"] = str(result.get("ai_model") or default_model(result["ai_provider"]))
        result["maps_provider"] = normalize_maps_provider(result.get("maps_provider"))
        result["interface_language"] = (
            result.get("interface_language") if result.get("interface_language") in {"en", "es"} else "en"
        )
        result["maps_provider_ready"] = maps_provider_ready(row, result["maps_provider"])
        return result
    return {}

@app.post("/api/profile")
def update_profile(profile: ProfileUpdate) -> dict:
    """
    Update non-secret candidate profile settings.
    
    Args:
        profile (ProfileUpdate): The validated profile details.
        
    Returns:
        dict: Success status and feedback message.
    """
    conn = get_db_connection()
    try:
        resume = None
        resume_id = profile.base_resume_id
        if resume_id is None and "base_resume_id" not in profile.model_fields_set:
            active = conn.execute(
                "SELECT active_base_resume_id FROM profile WHERE id = 1"
            ).fetchone()
            resume_id = active["active_base_resume_id"] if active else None
        if resume_id is not None or profile.base_resume_text.strip():
            resume = save_base_resume(
                conn,
                resume_id,
                profile.base_resume_name,
                profile.resume_mode,
                profile.base_resume_text,
                profile.professional_evidence.model_dump() if profile.professional_evidence else None,
            )
            activate_base_resume(conn, resume["id"])
        conn.execute("""
        UPDATE profile
        SET name = ?, email = ?, phone = ?, github = ?, linkedin = ?, website = ?,
            base_resume_text = ?, resume_mode = ?, ai_provider = ?, ai_model = ?,
            maps_provider = ?, prefer_us_headquarters = ?, interface_language = ?,
            suggested_keywords = ''
        WHERE id = 1
        """, (
            profile.name, profile.email, profile.phone, profile.github, profile.linkedin,
            profile.website, profile.base_resume_text.strip(), profile.resume_mode,
            profile.ai_provider, profile.ai_model, profile.maps_provider,
            int(profile.prefer_us_headquarters), profile.interface_language,
        ))
        conn.commit()
    except BaseResumeNotFound as exc:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()

    return {
        "success": True,
        "message": "Profile updated successfully.",
        "base_resume_id": resume["id"] if resume else None,
        "base_resume_version": resume["version_number"] if resume else None,
        "base_resume_version_created": resume["version_created"] if resume else False,
    }


def _resume_api_action(action):
    conn = get_db_connection()
    try:
        result = action(conn)
        conn.commit()
        return result
    except BaseResumeNotFound as exc:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LastBaseResumeError as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/base-resumes")
def get_base_resumes() -> list[dict]:
    return _resume_api_action(list_base_resumes)


@app.get("/api/base-resumes/{resume_id}")
def get_base_resume_details(resume_id: int) -> dict:
    return _resume_api_action(lambda conn: get_base_resume(conn, resume_id))


@app.post("/api/base-resumes/{resume_id}/activate")
def activate_base_resume_endpoint(resume_id: int) -> dict:
    return _resume_api_action(lambda conn: activate_base_resume(conn, resume_id))


@app.get("/api/base-resumes/{resume_id}/versions")
def get_base_resume_versions(resume_id: int) -> list[dict]:
    return _resume_api_action(lambda conn: list_versions(conn, resume_id))


@app.get("/api/base-resumes/{resume_id}/versions/{version_number}")
def get_base_resume_version(resume_id: int, version_number: int) -> dict:
    return _resume_api_action(lambda conn: get_version(conn, resume_id, version_number))


@app.post("/api/base-resumes/{resume_id}/versions/{version_number}/restore")
def restore_base_resume_version(resume_id: int, version_number: int) -> dict:
    return _resume_api_action(lambda conn: restore_version(conn, resume_id, version_number))


@app.delete("/api/base-resumes/{resume_id}")
def delete_base_resume_endpoint(resume_id: int) -> dict:
    replacement = _resume_api_action(lambda conn: delete_base_resume(conn, resume_id))
    return {"success": True, "active_resume": replacement}


@app.put("/api/profile/secrets")
def update_profile_secrets(secrets: ProfileSecretsUpdate) -> dict:
    """Update only explicitly supplied local API secrets without returning them."""
    updates = []
    values = []
    if secrets.gemini_api_key is not None:
        updates.append("gemini_api_key = ?")
        values.append(secrets.gemini_api_key.strip())
    if secrets.openai_api_key is not None:
        updates.append("openai_api_key = ?")
        values.append(secrets.openai_api_key.strip())
    if secrets.google_maps_api_key is not None:
        updates.append("google_maps_api_key = ?")
        values.append(secrets.google_maps_api_key.strip())
    if updates:
        conn = get_db_connection()
        conn.execute(f"UPDATE profile SET {', '.join(updates)} WHERE id = 1", values)
        conn.commit()
        conn.close()

    if secrets.gemini_api_key is not None:
        config.GEMINI_API_KEY = secrets.gemini_api_key.strip()
        os.environ["GEMINI_API_KEY"] = config.GEMINI_API_KEY
    if secrets.openai_api_key is not None:
        config.OPENAI_API_KEY = secrets.openai_api_key.strip()
        os.environ["OPENAI_API_KEY"] = config.OPENAI_API_KEY
    if secrets.google_maps_api_key is not None:
        config.GOOGLE_MAPS_API_KEY = secrets.google_maps_api_key.strip()
        os.environ["GOOGLE_MAPS_API_KEY"] = config.GOOGLE_MAPS_API_KEY
    conn = get_db_connection()
    configured = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    conn.close()
    return {
        "success": True,
        "gemini_api_key_configured": provider_key_configured(configured, "gemini"),
        "openai_api_key_configured": provider_key_configured(configured, "openai"),
        "google_maps_api_key_configured": bool(configured["google_maps_api_key"] or config.get_google_maps_api_key()),
    }


@app.post("/api/profile/ai-provider/validate")
def validate_ai_provider(
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """Validate the selected provider's stored key, model, and structured output."""
    with operation_scope(operation_id) as operation:
        try:
            operation.checkpoint()
            conn = get_db_connection()
            try:
                profile = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
            finally:
                conn.close()
            settings = settings_from_profile(profile)
            result = validate_provider_capability(settings)
            operation.checkpoint()
            return result
        except OperationCancelled:
            return {"success": False, "cancelled": True, "message": "AI provider test stopped."}
        except AIProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/profile/maps-provider/validate")
def validate_selected_maps_provider(
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """Validate the selected maps provider without exposing its credential."""
    with operation_scope(operation_id) as operation:
        try:
            operation.checkpoint()
            conn = get_db_connection()
            try:
                profile = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
            finally:
                conn.close()
            result = validate_maps_provider(maps_settings_from_profile(profile), operation.checkpoint)
            operation.checkpoint()
            return result
        except OperationCancelled:
            return {"success": False, "cancelled": True, "message": "Maps provider test stopped."}
        except MapsProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.post("/api/profile/upload-resume")
async def upload_resume(
    file: UploadFile = File(...),
    allow_ocr: bool = Form(False),
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """
    Import a text, Markdown, DOCX, or PDF base resume and return editable text.
    Scanned PDF pages use the selected AI provider only with explicit consent.
    The user explicitly saves the imported text as a version afterward.
    
    Args:
        file (UploadFile): The uploaded file resource.
        
    Returns:
        dict: Success status and the parsed resume text.
    """
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in {".txt", ".md", ".docx", ".pdf"}:
        raise HTTPException(status_code=400, detail="Resume imports support .txt, .md, .docx, and .pdf files.")
    maximum_bytes = MAX_RESUME_UPLOAD_BYTES if suffix in {".txt", ".md"} else MAX_RESUME_DOCUMENT_UPLOAD_BYTES
    maximum_label = "2 MB" if maximum_bytes == MAX_RESUME_UPLOAD_BYTES else "10 MB"

    operation = start_operation(operation_id)
    try:
        content = bytearray()
        while chunk := await file.read(64 * 1024):
            operation.checkpoint()
            content.extend(chunk)
            if len(content) > maximum_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"{suffix[1:].upper()} resume files must be {maximum_label} or smaller.",
                )

        def ocr_pages(images: list[bytes], checkpoint) -> list[str]:
            conn = get_db_connection()
            try:
                profile = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
            finally:
                conn.close()
            return extract_text_from_images(settings_from_profile(profile), images, checkpoint)

        imported = import_resume_document(
            filename,
            bytes(content),
            allow_ocr=allow_ocr,
            ocr_images=ocr_pages if allow_ocr else None,
            checkpoint=operation.checkpoint,
        )

        operation.checkpoint()
        return {
            "success": True,
            "resume_text": imported.text,
            "source_format": imported.source_format,
            "ocr_used": imported.ocr_used,
            "page_count": imported.page_count,
        }
    except OperationCancelled:
        return {"success": False, "cancelled": True, "message": "Resume import stopped before the editor changed."}
    except HTTPException:
        raise
    except ResumeDocumentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except AIProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to read the resume file.") from exc
    finally:
        finish_operation(operation)
        await file.close()

IS_SEARCHING = False
LAST_SEARCH_RESULT = None


def _save_source_diagnostic_history(search_result: dict) -> None:
    """Best-effort local history must never change the outcome of a search."""
    conn = None
    try:
        conn = get_db_connection()
        persist_source_diagnostics(
            conn,
            search_result,
            datetime.now().isoformat(timespec="seconds"),
        )
        conn.commit()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        print("Source diagnostic history could not be saved; search results remain available.")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def run_search_wrapper(
    keywords: str,
    location: str,
    operation: Optional[OperationToken] = None,
) -> None:
    """
    A background helper task that triggers the crawling and matching of jobs,
    ensuring that the searching status flag is reset once completed.
    
    Args:
        keywords (str): Semi-colon or space-separated job keywords.
        location (str): Semicolon-separated target locations.
    """
    operation = operation or start_operation()
    global IS_SEARCHING, LAST_SEARCH_RESULT
    try:
        LAST_SEARCH_RESULT = run_job_search_and_matching(keywords, location, operation.checkpoint)
        if isinstance(LAST_SEARCH_RESULT, dict) and LAST_SEARCH_RESULT.get("provider_alerts"):
            _save_source_diagnostic_history(LAST_SEARCH_RESULT)
    except OperationCancelled:
        LAST_SEARCH_RESULT = {
            "success": True,
            "cancelled": True,
            "partial": True,
            "message": "Search stopped. Any jobs committed before the stop request were kept.",
            "provider_alerts": [],
        }
    except Exception as exc:
        LAST_SEARCH_RESULT = {"success": False, "error": str(exc), "provider_alerts": []}
    finally:
        finish_operation(operation)
        IS_SEARCHING = False


@app.post("/api/operations/{operation_id}/cancel")
def cancel_operation(operation_id: str) -> dict:
    """Request cooperative cancellation at the operation's next safe checkpoint."""
    active = request_cancellation(operation_id)
    return {
        "success": True,
        "active": active,
        "message": (
            "Stopping safely after the current step."
            if active else
            "The operation has already finished."
        ),
    }

# Job listings endpoints
@app.get("/api/jobs")
def get_jobs(include_archived: bool = False) -> list[dict]:
    """
    Retrieve all job postings stored in the database, ordered by match score descending.
    
    Returns:
        list[dict]: A list of job listing records.
    """
    conn = get_db_connection()
    where_clause = "" if include_archived else "WHERE j.status != 'archived'"
    rows = conn.execute(f"""
        SELECT j.*, a.date_applied, a.application_method, a.notes, a.follow_up_date,
               CASE WHEN a.tailored_resume_path IS NOT NULL OR a.cover_letter IS NOT NULL
                    THEN 1 ELSE 0 END AS has_materials
        FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        {where_clause}
        ORDER BY j.match_score DESC
    """).fetchall()
    conn.close()
    jobs = [dict(row) for row in rows]
    for job in jobs:
        job["filter_facets"] = derive_job_filter_facets(job)
    return jobs


@app.post("/api/jobs/import/preview")
def preview_manual_job(
    req: ManualJobPreviewRequest,
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """Validate a public posting URL and extract editable details without saving it."""
    with operation_scope(operation_id) as operation:
        try:
            return _preview_manual_job(req, operation)
        except OperationCancelled:
            return {
                "success": False,
                "cancelled": True,
                "message": "Preview stopped before any job data changed.",
            }


def _preview_manual_job(req: ManualJobPreviewRequest, operation: OperationToken) -> dict:
    operation.checkpoint()
    canonical_url = canonicalize_job_url(req.url)
    is_public, reason = validate_public_http_url(canonical_url)
    if not is_public:
        raise HTTPException(status_code=422, detail=reason)

    conn = get_db_connection()
    suppressed = is_job_suppressed(conn, canonical_url)
    existing = conn.execute(
        "SELECT id, title, company, status FROM jobs WHERE url = ?",
        (canonical_url,),
    ).fetchone()
    conn.close()
    if suppressed:
        return {
            "success": True,
            "duplicate": False,
            "suppressed": True,
            "message": "This posting was previously deleted. Clear its suppression in Clean Up before importing it again.",
            "job": {"url": canonical_url},
        }
    if existing:
        return {
            "success": True,
            "duplicate": True,
            "existing_job": dict(existing),
            "job": {"url": canonical_url},
        }

    outcome = inspect_job_posting(
        canonical_url,
        allow_partial=True,
        cancel_check=operation.checkpoint,
    )
    operation.checkpoint()
    details = outcome["details"]
    resolved_url = canonicalize_job_url(details.get("url") or canonical_url)
    if resolved_url != canonical_url:
        conn = get_db_connection()
        suppressed = is_job_suppressed(conn, resolved_url)
        existing = conn.execute(
            "SELECT id, title, company, status FROM jobs WHERE url = ?",
            (resolved_url,),
        ).fetchone()
        conn.close()
        if suppressed:
            return {
                "success": True,
                "duplicate": False,
                "suppressed": True,
                "message": "This posting was previously deleted. Clear its suppression in Clean Up before importing it again.",
                "job": {"url": resolved_url},
            }
        if existing:
            return {
                "success": True,
                "duplicate": True,
                "existing_job": dict(existing),
                "job": {"url": resolved_url},
            }

    provider = provider_for_url(resolved_url)
    source = provider if provider != "unknown" else (urlsplit(resolved_url).hostname or "manual")
    job = {
        "url": resolved_url,
        "title": str(details.get("title") or "")[:180],
        "company": str(details.get("company") or "")[:180],
        "description": str(details.get("description") or "")[:MAX_JOB_DESCRIPTION_CHARS],
        "location": str(details.get("location") or "")[:240],
        "work_arrangement": details.get("work_arrangement") or "",
        "employment_type": details.get("employment_type") or "",
        "compensation": str(details.get("compensation") or "")[:160],
        "source": source,
    }
    extraction_succeeded = all(job.get(key) for key in ("title", "company", "description"))
    extraction_status = outcome.get("status", "provider_error")
    messages = {
        "ok": "Review the extracted details before saving.",
        "partial": "Some details were extracted. Complete the missing required fields before saving.",
        "stale": "The source reports that this posting is no longer published. Save only if you have verified the details elsewhere.",
        "access_challenge": "The site blocked automated reading. Enter the posting details manually before saving.",
        "embedded_content": "The posting is inside a protected embedded frame. Enter the posting details manually before saving.",
        "format_drift": "The page loaded but its job data was not in a recognized format. Enter the required fields manually.",
        "provider_error": "The posting could not be retrieved. You can still enter the required fields manually.",
    }
    return {
        "success": True,
        "duplicate": False,
        "extraction_succeeded": extraction_succeeded,
        "extraction_status": extraction_status,
        "message": messages.get(extraction_status, "Complete the required fields before saving."),
        "job": job,
    }


@app.post("/api/jobs/import")
def save_manual_job(
    req: ManualJobSaveRequest,
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """Save one reviewed posting and score it when the configured AI is available."""
    with operation_scope(operation_id) as operation:
        try:
            return _save_manual_job(req, operation)
        except OperationCancelled:
            return {
                "success": False,
                "cancelled": True,
                "saved": False,
                "message": "Job import stopped before the posting was saved.",
            }


def _save_manual_job(req: ManualJobSaveRequest, operation: OperationToken) -> dict:
    operation.checkpoint()
    canonical_url = canonicalize_job_url(req.url)
    is_public, reason = validate_public_http_url(canonical_url)
    if not is_public:
        raise HTTPException(status_code=422, detail=reason)

    title = " ".join(req.title.split())
    company = " ".join(req.company.split())
    description = req.description.strip()
    location = " ".join(req.location.split())
    compensation = " ".join(req.compensation.split())
    provider = provider_for_url(canonical_url)
    source = provider if provider != "unknown" else (urlsplit(canonical_url).hostname or "manual")
    if len(title) < 2 or len(company) < 2 or len(description) < 20:
        raise HTTPException(
            status_code=422,
            detail="Company and title are required, and the description must contain at least 20 characters.",
        )

    conn = get_db_connection()
    try:
        profile = conn.execute("SELECT * FROM profile LIMIT 1").fetchone()
        operation.checkpoint()

        # Save the reviewed posting first so a later Stop request can preserve
        # it as explicitly unscored. The duplicate check and insert remain one
        # short atomic transaction.
        conn.execute("BEGIN IMMEDIATE")
        if is_job_suppressed(conn, canonical_url):
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail="This posting was previously deleted. Clear its suppression in Clean Up before importing it again.",
            )
        existing = conn.execute(
            "SELECT id, title, company, status FROM jobs WHERE url = ?",
            (canonical_url,),
        ).fetchone()
        if existing:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"This posting is already saved as job #{existing['id']}.",
            )

        now = datetime.now().isoformat(timespec="seconds")
        match_score = None
        match_analysis = "Manual import saved; match analysis has not been completed."
        cursor = conn.execute("""
            INSERT INTO jobs (
                title, company, description, url, match_score, match_analysis,
                date_found, status, location, work_arrangement, employment_type,
                compensation, source, last_checked_at, is_expired
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'matched', ?, ?, ?, ?, ?, ?, 0)
        """, (
            title, company, description, canonical_url, match_score, match_analysis,
            date.today().isoformat(), location, req.work_arrangement,
            req.employment_type, compensation, source, now,
        ))
        job_id = cursor.lastrowid
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    match_error = None
    try:
        operation.checkpoint()
        ai_settings = settings_from_profile(profile)
        if profile and profile["base_resume_text"] and ai_settings.api_key:
            match = analyze_job_match(
                profile["base_resume_text"],
                title,
                company,
                description,
                ai_settings.api_key,
                cancel_check=operation.checkpoint,
                ai_provider=ai_settings.provider,
                ai_model=ai_settings.model,
            )
            operation.checkpoint()
            if match.get("success"):
                match_score = max(0, min(100, int(match["match_score"])))
                match_analysis = str(match.get("match_analysis") or "Matched successfully.")
                conn = get_db_connection()
                try:
                    conn.execute(
                        "UPDATE jobs SET match_score = ?, match_analysis = ? WHERE id = ?",
                        (match_score, match_analysis, job_id),
                    )
                    conn.commit()
                finally:
                    conn.close()
            else:
                match_error = match.get("error") or "Match analysis was unavailable."
    except OperationCancelled:
        return {
            "success": True,
            "cancelled": True,
            "saved": True,
            "job_id": job_id,
            "match_score": None,
            "message": "Job saved. Match analysis was stopped, so the posting is shown as unscored.",
        }
    except (AttributeError, KeyError, TypeError, ValueError):
        match_score = None

    return {
        "success": True,
        "job_id": job_id,
        "match_score": match_score,
        "match_error": match_error,
        "message": (
            "Job imported and match analysis completed."
            if match_score is not None else
            (
                f"Job imported. {match_error} It is shown as unscored."
                if match_error else
                "Job imported. Match analysis was unavailable, so it is shown as unscored."
            )
        ),
    }


@app.patch("/api/jobs/{job_id}/lifecycle")
def change_job_lifecycle(job_id: int, req: LifecycleUpdateRequest) -> dict:
    """Record a manual application or pipeline correction with audit history."""
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = update_lifecycle(
            conn,
            job_id,
            req.status,
            applied_on=req.applied_on,
            method=req.method,
            notes=req.notes,
            follow_up_on=req.follow_up_on,
            source="manual",
        )
        conn.commit()
        capture_event(
            "job_lifecycle_updated",
            {
                "result": "success",
                "source_category": "manual",
                "from_status": result["previous_status"],
                "to_status": result["status"],
            },
        )
        return {"success": True, **result}
    except LookupError as exc:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/lifecycle/undo")
def undo_job_lifecycle(job_id: int) -> dict:
    """Undo the latest recorded manual lifecycle correction."""
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = undo_latest_lifecycle_change(conn, job_id)
        conn.commit()
        return {"success": True, **result}
    except LookupError as exc:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/jobs/cleanup-preview")
def preview_job_cleanup() -> dict:
    """Preview protected archive/delete/restore candidate sets without mutation."""
    conn = get_db_connection()
    try:
        return cleanup_preview(conn)
    finally:
        conn.close()


@app.post("/api/jobs/cleanup")
def cleanup_jobs(req: CleanupRequest) -> dict:
    """Apply a previously previewed bulk cleanup operation transactionally."""
    global IS_SEARCHING
    if IS_SEARCHING:
        raise HTTPException(status_code=409, detail="Wait for the current job search to finish before cleanup.")

    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        affected = apply_cleanup(
            conn,
            req.action,
            req.preview_token,
            datetime.now().isoformat(timespec="seconds"),
        )
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    verbs = {"archive": "archived", "delete": "permanently deleted", "restore": "restored"}
    return {
        "success": True,
        "action": req.action,
        "affected": affected,
        "message": f"{affected} untouched job(s) {verbs[req.action]}.",
    }


@app.get("/api/job-suppressions")
def get_job_suppressions() -> dict:
    """List privacy-minimized deleted-posting records for local review."""
    conn = get_db_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM job_suppressions").fetchone()[0]
        rows = conn.execute(
            """
            SELECT id, hostname, company, title, deleted_at, deletion_source
            FROM job_suppressions
            ORDER BY deleted_at DESC, id DESC
            LIMIT 100
            """
        ).fetchall()
        return {"count": count, "items": [dict(row) for row in rows]}
    finally:
        conn.close()


@app.delete("/api/job-suppressions/{suppression_id}")
def clear_job_suppression(suppression_id: int) -> dict:
    """Allow one intentionally deleted posting to be discovered again."""
    conn = get_db_connection()
    try:
        cursor = conn.execute("DELETE FROM job_suppressions WHERE id = ?", (suppression_id,))
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Suppressed posting not found.")
        return {"success": True, "cleared": 1}
    finally:
        conn.close()


@app.delete("/api/job-suppressions")
def clear_all_job_suppressions() -> dict:
    """Allow all intentionally deleted postings to be discovered again."""
    conn = get_db_connection()
    try:
        cursor = conn.execute("DELETE FROM job_suppressions")
        conn.commit()
        return {"success": True, "cleared": cursor.rowcount}
    finally:
        conn.close()

@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int) -> dict:
    """
    Delete a specific job posting from the database, along with its associated application record.
    
    Args:
        job_id (int): The unique ID of the job posting to delete.
        
    Returns:
        dict: Success status and feedback message.
    """
    conn = get_db_connection()
    try:
        # Check if exists
        job = conn.execute(
            "SELECT id, url, company, title FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")

        record_job_suppression(
            conn,
            url=job["url"],
            company=job["company"],
            title=job["title"],
            deleted_at=datetime.now().isoformat(timespec="seconds"),
            deletion_source="manual",
        )
        # Delete corresponding application first to prevent foreign key issues
        conn.execute("DELETE FROM applications WHERE job_id = ?", (job_id,))
        # Delete job
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return {"success": True, "message": "Job posting deleted successfully."}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete job: {str(e)}")
    finally:
        conn.close()

@app.get("/api/jobs/status")
def get_search_status() -> dict:
    """
    Get the status of the background job search process.
    
    Returns:
        dict: Containing a boolean key 'searching' indicating search status.
    """
    global IS_SEARCHING, LAST_SEARCH_RESULT
    return {"searching": IS_SEARCHING, "last_result": LAST_SEARCH_RESULT}


@app.get("/api/source-diagnostics")
def get_source_diagnostic_history(limit: int = 100) -> JSONResponse:
    """Retrieve recent privacy-minimized source notices for troubleshooting."""
    conn = get_db_connection()
    try:
        history = list_source_diagnostics(conn, limit)
        return JSONResponse(history, headers={"Cache-Control": "no-store"})
    finally:
        conn.close()


@app.get("/api/data-export")
def export_user_data() -> StreamingResponse:
    """Download an explicit local-data export without credentials or host paths."""
    conn = get_db_connection()
    try:
        payload = build_user_data_export(
            conn,
            exported_at=datetime.now().isoformat(timespec="seconds"),
            application_build=APP_BUILD,
        )
    finally:
        conn.close()
    filename = f"career-trellis-user-data-{date.today().isoformat()}.json"
    content = json.JSONEncoder(ensure_ascii=False, indent=2).iterencode(payload)
    return StreamingResponse(
        content=content,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _remove_temporary_download(path: Path) -> None:
    """Best-effort cleanup after Starlette finishes streaming a generated download."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


@app.post("/api/full-backup")
def download_full_backup(
    req: FullBackupRequest,
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
):
    """Create an authenticated encrypted download of the database and generated materials."""
    if not BACKUP_RESTORE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another backup or restore is already in progress.")
    operation = start_operation(operation_id)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".career-trellis-backup-",
            suffix=".ctbackup",
            dir=config.DATA_DIR,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        summary = create_encrypted_backup(
            temporary_path,
            req.password,
            database_path=config.DB_PATH,
            output_dir=config.OUTPUT_DIR,
            application_build=APP_BUILD,
            checkpoint=operation.checkpoint,
        )
        filename = f"career-trellis-full-backup-{date.today().isoformat()}.ctbackup"
        response = FileResponse(
            temporary_path,
            media_type="application/vnd.careertrellis.backup",
            filename=filename,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-CareerTrellis-Backup-Materials": str(summary.material_file_count),
                "X-CareerTrellis-Backup-Warnings": str(summary.warning_count),
            },
            background=BackgroundTask(_remove_temporary_download, temporary_path),
        )
        temporary_path = None
        return response
    except OperationCancelled:
        return JSONResponse({
            "success": False,
            "cancelled": True,
            "message": "Backup stopped before a download was created.",
        })
    except BackupPasswordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BackupRestoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="CareerTrellis could not create the encrypted backup.") from exc
    finally:
        if temporary_path:
            _remove_temporary_download(temporary_path)
        finish_operation(operation)
        BACKUP_RESTORE_LOCK.release()


@app.post("/api/full-backup/restore")
async def restore_full_backup(
    file: UploadFile = File(...),
    password: str = Form(..., min_length=1, max_length=1024),
    confirm_replace: bool = Form(False),
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """Restore a validated full backup and retain the replaced workspace for recovery."""
    if not confirm_replace:
        await file.close()
        raise HTTPException(status_code=400, detail="Confirm that the current workspace will be replaced.")
    if Path(file.filename or "").suffix.lower() != ".ctbackup":
        await file.close()
        raise HTTPException(status_code=400, detail="Choose a CareerTrellis .ctbackup file.")
    if not BACKUP_RESTORE_LOCK.acquire(blocking=False):
        await file.close()
        raise HTTPException(status_code=409, detail="Another backup or restore is already in progress.")

    operation = start_operation(operation_id)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".career-trellis-restore-",
            suffix=".ctbackup",
            dir=config.DATA_DIR,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            copied = 0
            while chunk := await file.read(1024 * 1024):
                operation.checkpoint()
                copied += len(chunk)
                if copied > MAXIMUM_BACKUP_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="The encrypted backup exceeds the supported 2 GB size.",
                    )
                temporary.write(chunk)
        operation.checkpoint()
        summary = await run_in_threadpool(
            restore_encrypted_backup,
            temporary_path,
            password,
            database_path=config.DB_PATH,
            output_dir=config.OUTPUT_DIR,
            recovery_root=config.DATA_DIR / "restore-recovery",
            application_build=APP_BUILD,
            checkpoint=operation.checkpoint,
            post_restore_check=init_db,
        )
        return {
            "success": True,
            "message": "Backup restored. The previous workspace was retained for local recovery.",
            "backup_created_at": summary.backup_created_at,
            "backup_application_build": summary.backup_application_build,
            "material_file_count": summary.material_file_count,
            "recovery_folder": Path(summary.recovery_directory).name,
        }
    except OperationCancelled:
        return {
            "success": False,
            "cancelled": True,
            "message": "Restore stopped before the current workspace was replaced.",
        }
    except HTTPException:
        raise
    except (BackupPasswordError, BackupAuthenticationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BackupCompatibilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BackupRestoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="CareerTrellis could not safely restore this backup.") from exc
    finally:
        await file.close()
        if temporary_path:
            _remove_temporary_download(temporary_path)
        finish_operation(operation)
        BACKUP_RESTORE_LOCK.release()


@app.get("/api/source-diagnostics/export")
def export_source_diagnostic_history() -> JSONResponse:
    """Download the bounded local history without searches, URLs, or job data."""
    conn = get_db_connection()
    try:
        history = list_source_diagnostics(conn, 500)
    finally:
        conn.close()
    payload = {
        "schema_version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "record_count": history["count"],
        "diagnostics": history["items"],
    }
    filename = f"career-trellis-source-diagnostics-{date.today().isoformat()}.json"
    return JSONResponse(
        payload,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.get("/api/source-diagnostics/maintainer-report")
def preview_source_diagnostic_maintainer_report() -> JSONResponse:
    """Prepare a minimized report for explicit review; never transmit it."""
    conn = get_db_connection()
    try:
        history = list_source_diagnostics(conn, 500)
    finally:
        conn.close()
    report = build_maintainer_report(
        history,
        application_build=APP_BUILD,
        generated_on=date.today().isoformat(),
    )
    groups = report["diagnostic_groups"]
    issue_title = "[Source diagnostics] CareerTrellis provider report"
    if groups:
        primary = groups[0]
        issue_title = (
            f"[Source diagnostics] {primary['provider'].title()} "
            f"{primary['code'].replace('_', ' ')}"
        )
    return JSONResponse(
        {
            "can_report": bool(groups),
            "issue_title": issue_title,
            "report": report,
            "markdown": format_maintainer_report(report) if groups else "",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.delete("/api/source-diagnostics")
def clear_source_diagnostic_history() -> dict:
    """Clear local troubleshooting history without affecting jobs or searches."""
    conn = get_db_connection()
    try:
        cursor = conn.execute("DELETE FROM source_diagnostics")
        conn.commit()
        return {"success": True, "cleared": cursor.rowcount}
    finally:
        conn.close()


@app.get("/api/saved-searches")
def get_saved_searches() -> list[dict]:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM saved_searches WHERE enabled = 1 ORDER BY name COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@app.get("/api/saved-searches/due")
def get_due_saved_searches() -> list[dict]:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT id, name, keywords, location, schedule_frequency, next_alert_at
        FROM saved_searches
        WHERE enabled = 1 AND schedule_frequency != 'none'
          AND next_alert_at IS NOT NULL AND next_alert_at <= ?
        ORDER BY next_alert_at
    """, (now,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@app.delete("/api/saved-searches/{search_id}")
def delete_saved_search(search_id: int) -> dict:
    conn = get_db_connection()
    cursor = conn.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,))
    conn.commit()
    conn.close()
    if not cursor.rowcount:
        raise HTTPException(status_code=404, detail="Saved search not found.")
    return {"success": True}


@app.post("/api/jobs/{job_id}/verify")
def verify_job_posting(
    job_id: int,
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """Re-scrape one listing and mark it expired without deleting history."""
    with operation_scope(operation_id) as operation:
        try:
            return _verify_job_posting(job_id, operation)
        except OperationCancelled:
            return {
                "success": False,
                "cancelled": True,
                "message": "Listing verification stopped. The saved job was not changed.",
            }


def _verify_job_posting(job_id: int, operation: OperationToken) -> dict:
    operation.checkpoint()
    conn = get_db_connection()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    checked_at = datetime.now().isoformat(timespec="seconds")
    outcome = inspect_job_posting(job["url"], cancel_check=operation.checkpoint)
    operation.checkpoint()
    details = outcome["details"]
    if not details and outcome.get("status") != "stale":
        raise HTTPException(
            status_code=503,
            detail="The posting could not be verified because its source was unavailable or unreadable. No job status was changed.",
        )
    conn = get_db_connection()
    if details:
        conn.execute("""
            UPDATE jobs SET title = ?, company = ?, description = ?, location = ?,
                work_arrangement = ?, employment_type = ?, compensation = ?, source = ?,
                last_checked_at = ?, is_expired = 0, expiration_reason = NULL
            WHERE id = ?
        """, (
            details["title"], details["company"], details["description"], details.get("location", ""),
            details.get("work_arrangement", ""), details.get("employment_type", ""),
            details.get("compensation", ""), details.get("source", ""), checked_at, job_id,
        ))
        result = {"success": True, "expired": False, "message": "Listing is still active."}
    else:
        next_status = "closed" if job["status"] in {"matched", "tailored", "form_filled", "submitted"} else job["status"]
        conn.execute("""
            UPDATE jobs SET status = ?, last_checked_at = ?, is_expired = 1,
                expiration_reason = 'Source reports posting is no longer published'
            WHERE id = ?
        """, (next_status, checked_at, job_id))
        if next_status == "closed":
            conn.execute("UPDATE applications SET status = 'closed' WHERE job_id = ?", (job_id,))
        result = {"success": True, "expired": True, "message": "Listing appears closed or unavailable."}
    conn.commit()
    conn.close()
    return result

@app.post("/api/jobs/search")
def search_jobs(
    req: SearchRequest,
    background_tasks: BackgroundTasks,
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """
    Start a background job crawl and match analysis task using the user keywords and locations.
    
    Args:
        req (SearchRequest): The search keywords and locations.
        background_tasks (BackgroundTasks): FastAPI background task manager.
        
    Returns:
        dict: Success status and status message.
    """
    global IS_SEARCHING, LAST_SEARCH_RESULT
    if IS_SEARCHING:
        return {"success": False, "message": "A job search is already in progress."}
    operation = start_operation(operation_id)
    try:
        operation.checkpoint()
    except OperationCancelled:
        finish_operation(operation)
        return {"success": False, "cancelled": True, "message": "Search stopped before it started."}
    LAST_SEARCH_RESULT = None
    if req.save_search:
        search_name = (req.saved_search_name or req.keywords or "Resume-suggested search").strip()
        now = datetime.now().isoformat(timespec="seconds")
        conn = get_db_connection()
        interval = timedelta(days=1 if req.schedule_frequency == "daily" else 7)
        next_alert = (datetime.now() + interval).isoformat(timespec="seconds") if req.schedule_frequency != "none" else None
        conn.execute("""
            INSERT INTO saved_searches (
                name, keywords, location, created_at, last_run_at,
                schedule_frequency, next_alert_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(keywords, location) DO UPDATE SET
                name = excluded.name, last_run_at = excluded.last_run_at,
                schedule_frequency = excluded.schedule_frequency,
                next_alert_at = excluded.next_alert_at, enabled = 1
        """, (
            search_name, req.keywords.strip(), (req.location or "").strip(), now, now,
            req.schedule_frequency, next_alert,
        ))
        conn.commit()
        conn.close()
    elif req.saved_search_id:
        conn = get_db_connection()
        saved = conn.execute("SELECT schedule_frequency FROM saved_searches WHERE id = ?", (req.saved_search_id,)).fetchone()
        if saved:
            frequency = saved["schedule_frequency"] or "none"
            interval = timedelta(days=1 if frequency == "daily" else 7)
            next_alert = (datetime.now() + interval).isoformat(timespec="seconds") if frequency != "none" else None
            conn.execute(
                "UPDATE saved_searches SET last_run_at = ?, next_alert_at = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), next_alert, req.saved_search_id),
            )
            conn.commit()
        conn.close()
    IS_SEARCHING = True
    background_tasks.add_task(run_search_wrapper, req.keywords, req.location, operation)
    capture_event(
        "job_search_started",
        {
            "result": "success",
            "source_category": "saved_search" if (req.save_search or req.saved_search_id) else "manual",
        },
    )
    return {"success": True, "message": "Job search started in background."}

# Resume tailoring endpoints
@app.post("/api/jobs/{job_id}/tailor")
def tailor_resume_endpoint(
    job_id: int,
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """
    Generate a tailored resume and cover letter for a specific job matching the candidate's profile.
    
    Args:
        job_id (int): The ID of the job listing.
        
    Returns:
        dict: Containing the tailored resume, cover letter, and U.S. HQ location.
    """
    with operation_scope(operation_id) as operation:
        try:
            return _tailor_resume_endpoint(job_id, operation)
        except OperationCancelled:
            return {
                "success": False,
                "cancelled": True,
                "message": "Tailoring stopped. Previously saved materials and job status were preserved.",
            }


def _tailor_resume_endpoint(job_id: int, operation: OperationToken) -> dict:
    started_at = time.monotonic()
    operation.checkpoint()
    conn = get_db_connection()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    profile = conn.execute("SELECT * FROM profile LIMIT 1").fetchone()
    
    if not job:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found.")
    if not profile or not profile["base_resume_text"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Base resume text is missing. Please setup your profile.")

    source_resume_id = profile["active_base_resume_id"] if "active_base_resume_id" in profile.keys() else None
    source_resume_name = None
    source_resume_version = None
    source_professional_evidence = ""
    if source_resume_id:
        source_resume = conn.execute(
            """
            SELECT r.name, COALESCE(MAX(v.version_number), 0) AS version_number
            FROM base_resumes r
            LEFT JOIN base_resume_versions v ON v.base_resume_id = r.id
            WHERE r.id = ?
            GROUP BY r.id
            """,
            (source_resume_id,),
        ).fetchone()
        if source_resume:
            source_resume_name = source_resume["name"]
            source_resume_version = source_resume["version_number"]
            source_professional_evidence = professional_evidence_markdown(
                get_base_resume(conn, source_resume_id)["professional_evidence"]
            )

    conn.close()
    
    ai_settings = settings_from_profile(profile)
    maps_settings = maps_settings_from_profile(profile)
    res = tailor_resume_and_cover_letter(
        base_resume_text=profile["base_resume_text"],
        job_title=job["title"],
        company_name=job["company"],
        job_description=job["description"],
        api_key=ai_settings.api_key,
        resume_mode=profile["resume_mode"] or "general_professional",
        cancel_check=operation.checkpoint,
        ai_provider=ai_settings.provider,
        ai_model=ai_settings.model,
        professional_evidence=source_professional_evidence,
    )
    operation.checkpoint()
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error", "Tailoring failed."))
        
    # Generate HTML/PDF file paths
    output_root = Path(config.OUTPUT_DIR)
    output_root.mkdir(parents=True, exist_ok=True)
    resume_pdf_path = output_root / f"tailored_resume_{job_id}.pdf"
    cover_letter_path = cover_letter_output_path(job_id, output_root)
    with tempfile.NamedTemporaryFile(delete=False, dir=output_root, suffix=".pdf") as temporary_pdf:
        temporary_resume_path = Path(temporary_pdf.name)
    operation.track_temporary_file(temporary_resume_path)
    
    # Save the tailored resume PDF only when it satisfies the two-page contract.
    try:
        page_limit = 6 if (profile["resume_mode"] or "") == "academic_cv" else 2
        pdf_result = generate_resume_pdf(
            res["tailored_resume"],
            str(temporary_resume_path),
            max_pages=page_limit,
            metadata=resume_pdf_metadata(profile["name"], job["title"], job["company"]),
        )
        operation.checkpoint()
    except ValueError as exc:
        temporary_resume_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    
    # Persist both artifacts before exposing this job as ready for manual application.
    cover_letter_text = res["cover_letter"]
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=output_root,
        suffix=".txt",
    ) as temporary_letter:
        temporary_letter.write(cover_letter_text.strip() + "\n")
        temporary_cover_letter_path = Path(temporary_letter.name)
    operation.track_temporary_file(temporary_cover_letter_path)
    operation.checkpoint()
    
    # Update application log or store inside job metadata
    # (For convenience we will store tailored details in the applications table with a tailored/pending status)
    conn = get_db_connection()
    # Check if application record already exists for this job
    existing = conn.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()

    # Headquarters lookups are cached by provider and bounded identity context.
    prefer_us_headquarters = bool(profile["prefer_us_headquarters"])
    headquarters_cache_key = _headquarters_cache_key(
        maps_settings.provider,
        job["company"],
        prefer_us_headquarters,
        job["location"] or "",
        job["url"] or "",
    )
    headquarters = _cached_headquarters(headquarters_cache_key)
    if headquarters is None:
        headquarters = resolve_headquarters(
            maps_settings,
            ai_settings,
            job["company"],
            prefer_us=prefer_us_headquarters,
            job_location=job["location"] or "",
            job_url=job["url"] or "",
            cancel_check=operation.checkpoint,
        )
        _cache_headquarters(headquarters_cache_key, headquarters, maps_settings.provider)
    operation.checkpoint()

    # The two file replacements and database update below are the short commit
    # boundary. There are intentionally no cancellation checkpoints inside it.
    os.replace(temporary_resume_path, resume_pdf_path)
    operation.commit_temporary_file(temporary_resume_path)
    os.replace(temporary_cover_letter_path, cover_letter_path)
    operation.commit_temporary_file(temporary_cover_letter_path)
    conn = get_db_connection()
    now = datetime.now().isoformat(timespec="seconds")
    if existing:
        conn.execute("""
        UPDATE applications
        SET company = ?, position = ?, us_hq = ?, headquarters_source = ?, headquarters_attribution = ?,
            tailored_resume_path = ?, cover_letter_path = ?, tailored_resume_text = ?, cover_letter = ?,
            created_at = COALESCE(created_at, ?), tailored_at = ?, base_resume_id = ?,
            base_resume_name = ?, base_resume_version = ?,
            status = CASE WHEN status IN ('applied', 'interview', 'offer') THEN status ELSE 'tailored' END
        WHERE job_id = ?
        """, (
            job["company"], job["title"], headquarters.address, headquarters.source,
            headquarters.attribution, str(resume_pdf_path.resolve()), str(cover_letter_path.resolve()),
            res["tailored_resume"], cover_letter_text, now, now, source_resume_id,
            source_resume_name, source_resume_version, job_id,
        ))
    else:
        conn.execute("""
        INSERT INTO applications (
            job_id, company, position, date_applied, us_hq, headquarters_source,
            headquarters_attribution, tailored_resume_path, cover_letter_path,
            tailored_resume_text, cover_letter, status, created_at, tailored_at,
            base_resume_id, base_resume_name, base_resume_version
        ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 'tailored', ?, ?, ?, ?, ?)
        """, (
            job_id, job["company"], job["title"], headquarters.address, headquarters.source,
            headquarters.attribution, str(resume_pdf_path.resolve()), str(cover_letter_path.resolve()),
            res["tailored_resume"], cover_letter_text, now, now, source_resume_id,
            source_resume_name, source_resume_version,
        ))
        
    # Update job status to tailored
    conn.execute("""
        UPDATE jobs
        SET status = CASE WHEN status IN ('applied', 'interview', 'offer') THEN status ELSE 'tailored' END
        WHERE id = ?
    """, (job_id,))
    conn.commit()
    conn.close()
    capture_event(
        "resume_tailored",
        {
            "result": "success",
            "source_category": source_category(job["source"]),
            "duration_bucket": duration_bucket(time.monotonic() - started_at),
        },
    )
    
    return {
        "success": True,
        "tailored_resume": res["tailored_resume"],
        "cover_letter": cover_letter_text,
        "us_hq": headquarters.address,
        "headquarters_source": headquarters.source,
        "headquarters_attribution": headquarters.attribution,
        "headquarters_warning": headquarters.warning,
        "resume_download_url": f"/api/jobs/{job_id}/materials/resume",
        "resume_docx_download_url": f"/api/jobs/{job_id}/materials/resume.docx",
        "cover_letter_download_url": f"/api/jobs/{job_id}/materials/cover-letter",
        "manual_application_url": f"/api/jobs/{job_id}/apply-manually",
        "pdf_page_count": pdf_result["page_count"],
        "pdf_compact": pdf_result["compact"],
        "base_resume_id": source_resume_id,
        "base_resume_name": source_resume_name,
        "base_resume_version": source_resume_version,
    }

@app.get("/api/jobs/{job_id}/tailored")
def get_tailored_details(job_id: int) -> dict:
    """
    Retrieve previously generated tailored materials (cover letter, PDF path, HQ address) for a job.
    
    Args:
        job_id (int): The ID of the job listing.
        
    Returns:
        dict: Tailored details or error message.
    """
    conn = get_db_connection()
    app_row = conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    if app_row:
        cover_letter_path = app_row["cover_letter_path"]
        try:
            resolve_output_file(cover_letter_path, ".txt")
        except (ValueError, FileNotFoundError):
            if app_row["cover_letter"]:
                cover_letter_path = persist_cover_letter(job_id, app_row["cover_letter"])
                conn = get_db_connection()
                conn.execute(
                    "UPDATE applications SET cover_letter_path = ? WHERE job_id = ?",
                    (cover_letter_path, job_id),
                )
                conn.commit()
                conn.close()
        # Load tailored resume markdown text (we can rebuild it or return letter + path)
        return {
            "success": True,
            "cover_letter": app_row["cover_letter"],
            "tailored_resume": app_row["tailored_resume_text"] or "",
            "tailored_resume_path": app_row["tailored_resume_path"],
            "cover_letter_path": cover_letter_path,
            "us_hq": app_row["us_hq"],
            "headquarters_source": app_row["headquarters_source"],
            "headquarters_attribution": app_row["headquarters_attribution"],
            "status": app_row["status"],
            "base_resume_id": app_row["base_resume_id"],
            "base_resume_name": app_row["base_resume_name"],
            "base_resume_version": app_row["base_resume_version"],
            "resume_download_url": f"/api/jobs/{job_id}/materials/resume",
            "resume_docx_download_url": f"/api/jobs/{job_id}/materials/resume.docx",
            "cover_letter_download_url": f"/api/jobs/{job_id}/materials/cover-letter",
            "manual_application_url": f"/api/jobs/{job_id}/apply-manually",
        }
    return {"success": False, "message": "No tailored details found for this job."}


@app.patch("/api/jobs/{job_id}/tailored")
def update_tailored_details(
    job_id: int,
    req: MaterialsUpdateRequest,
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """Save reviewed material edits and regenerate the attached resume PDF."""
    with operation_scope(operation_id) as operation:
        try:
            return _update_tailored_details(job_id, req, operation)
        except OperationCancelled:
            return {
                "success": False,
                "cancelled": True,
                "message": "PDF regeneration stopped. Previously saved materials were preserved.",
            }


def _update_tailored_details(job_id: int, req: MaterialsUpdateRequest, operation: OperationToken) -> dict:
    operation.checkpoint()
    resume_text = req.tailored_resume.strip()
    if not resume_text:
        raise HTTPException(status_code=422, detail="Tailored resume text cannot be empty.")
    try:
        cover_letter = finalize_cover_letter(req.cover_letter)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    conn = get_db_connection()
    application = conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    profile = conn.execute("SELECT name, resume_mode FROM profile LIMIT 1").fetchone()
    conn.close()
    if not application:
        raise HTTPException(status_code=404, detail="No tailored materials found for this job.")

    output_path = Path(application["tailored_resume_path"] or os.path.join(config.OUTPUT_DIR, f"tailored_resume_{job_id}.pdf"))
    output_root = Path(config.OUTPUT_DIR)
    output_root.mkdir(parents=True, exist_ok=True)
    cover_letter_path = cover_letter_output_path(job_id, output_root)
    resume_mode = profile["resume_mode"] if profile and profile["resume_mode"] else "general_professional"
    try:
        resume_text = apply_resume_section_template(resume_text, resume_mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    page_limit = 6 if resume_mode == "academic_cv" else 2
    with tempfile.NamedTemporaryFile(delete=False, dir=output_root, suffix=".pdf") as temporary_pdf:
        temporary_resume_path = Path(temporary_pdf.name)
    operation.track_temporary_file(temporary_resume_path)
    try:
        pdf_result = generate_resume_pdf(
            resume_text,
            str(temporary_resume_path),
            max_pages=page_limit,
            metadata=resume_pdf_metadata(
                profile["name"] if profile else "",
                application["position"],
                application["company"],
            ),
        )
        operation.checkpoint()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=output_root,
        suffix=".txt",
    ) as temporary_letter:
        temporary_letter.write(cover_letter.strip() + "\n")
        temporary_cover_letter_path = Path(temporary_letter.name)
    operation.track_temporary_file(temporary_cover_letter_path)
    operation.checkpoint()

    os.replace(temporary_resume_path, output_path)
    operation.commit_temporary_file(temporary_resume_path)
    os.replace(temporary_cover_letter_path, cover_letter_path)
    operation.commit_temporary_file(temporary_cover_letter_path)
    conn = get_db_connection()
    conn.execute("""
        UPDATE applications
        SET tailored_resume_text = ?, tailored_resume_path = ?, cover_letter_path = ?, cover_letter = ?
        WHERE job_id = ?
    """, (resume_text, str(output_path.resolve()), str(cover_letter_path.resolve()), cover_letter, job_id))
    conn.commit()
    conn.close()
    return {
        "success": True,
        "pdf_page_count": pdf_result["page_count"],
        "pdf_compact": pdf_result["compact"],
        "resume_download_url": f"/api/jobs/{job_id}/materials/resume",
        "resume_docx_download_url": f"/api/jobs/{job_id}/materials/resume.docx",
        "cover_letter_download_url": f"/api/jobs/{job_id}/materials/cover-letter",
    }

def _application_materials(job_id: int):
    """Load a job and its generated materials for a guided manual application."""
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT a.*, j.company AS job_company, j.title AS job_title,
               j.url AS job_url, j.source AS job_source, j.status AS job_status,
               (SELECT name FROM profile ORDER BY id LIMIT 1) AS candidate_name
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        WHERE a.job_id = ?
        """,
        (job_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Generated application materials were not found.")
    return row


@app.get("/api/jobs/{job_id}/materials/resume")
def download_tailored_resume(job_id: int) -> FileResponse:
    """Download the generated resume using a readable, filesystem-safe name."""
    material = _application_materials(job_id)
    try:
        path = resolve_output_file(material["tailored_resume_path"], ".pdf")
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="The generated resume PDF is unavailable.") from exc
    capture_event(
        "material_downloaded",
        {
            "result": "success",
            "source_category": source_category(material["job_source"]),
            "material_type": "resume",
        },
    )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=material_download_name(
            material["job_company"], material["job_title"], "resume", ".pdf",
            candidate_name=material["candidate_name"],
        ),
    )


@app.get("/api/jobs/{job_id}/materials/resume.docx")
def download_tailored_resume_docx(job_id: int) -> Response:
    """Build an accessible, editable DOCX from the reviewed tailored source."""
    material = _application_materials(job_id)
    resume_text = (material["tailored_resume_text"] or "").strip()
    if not resume_text:
        raise HTTPException(status_code=404, detail="The editable tailored resume source is unavailable.")
    candidate_name = material["candidate_name"] or ""
    try:
        content = build_accessible_resume_docx(
            resume_text,
            candidate_name=candidate_name,
            title=f"{candidate_name or 'Candidate'} - {material['job_title']} Resume",
        )
    except ResumeDocumentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = material_download_name(
        material["job_company"], material["job_title"], "resume-accessible", ".docx",
        candidate_name=candidate_name,
    )
    capture_event(
        "material_downloaded",
        {
            "result": "success",
            "source_category": source_category(material["job_source"]),
            "material_type": "resume",
        },
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/jobs/{job_id}/materials/cover-letter")
def download_cover_letter(job_id: int) -> FileResponse:
    """Download the generated cover letter as a plain-text attachment."""
    material = _application_materials(job_id)
    cover_letter_path = material["cover_letter_path"]
    try:
        path = resolve_output_file(cover_letter_path, ".txt")
    except (ValueError, FileNotFoundError):
        if not material["cover_letter"]:
            raise HTTPException(status_code=404, detail="The generated cover letter is unavailable.")
        cover_letter_path = persist_cover_letter(job_id, material["cover_letter"])
        conn = get_db_connection()
        conn.execute(
            "UPDATE applications SET cover_letter_path = ? WHERE job_id = ?",
            (cover_letter_path, job_id),
        )
        conn.commit()
        conn.close()
        path = resolve_output_file(cover_letter_path, ".txt")
    capture_event(
        "material_downloaded",
        {
            "result": "success",
            "source_category": source_category(material["job_source"]),
            "material_type": "cover_letter",
        },
    )
    return FileResponse(
        path,
        media_type="text/plain; charset=utf-8",
        filename=material_download_name(
            material["job_company"], material["job_title"], "cover-letter", ".txt",
            candidate_name=material["candidate_name"],
        ),
    )


@app.get("/api/jobs/{job_id}/apply-manually")
def open_manual_application(job_id: int) -> RedirectResponse:
    """Open a verified employer URL after confirming required materials exist."""
    material = _application_materials(job_id)
    try:
        resolve_output_file(material["tailored_resume_path"], ".pdf")
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail="Downloadable application materials are not ready.") from exc
    if not material["cover_letter"]:
        raise HTTPException(status_code=400, detail="Downloadable application materials are not ready.")

    parsed_url = urlsplit(material["job_url"] or "")
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise HTTPException(status_code=400, detail="The employer application URL is invalid.")
    from_status = material["status"] or material["job_status"] or "matched"
    if from_status not in {"matched", "tailored", "form_filled", "submitted", "applied", "interview", "offer", "rejected", "withdrawn", "closed"}:
        from_status = "matched"
    capture_event(
        "manual_application_opened",
        {
            "result": "success",
            "source_category": source_category(material["job_source"]),
            "from_status": from_status,
        },
    )
    return RedirectResponse(material["job_url"], status_code=302)


def _interview_prep_record(connection: sqlite3.Connection, job_id: int):
    return connection.execute("""
        SELECT a.job_id, a.company, a.position, a.status, a.notes,
               a.tailored_resume_text, a.interview_prep, a.interview_prep_updated_at,
               j.title, j.description, j.match_analysis
        FROM applications a
        LEFT JOIN jobs j ON j.id = a.job_id
        WHERE a.job_id = ?
    """, (job_id,)).fetchone()


@app.get("/api/jobs/{job_id}/interview-prep")
def get_interview_prep(job_id: int) -> dict:
    """Load saved interview notes or a local starter without calling an AI provider."""
    conn = get_db_connection()
    try:
        record = _interview_prep_record(conn, job_id)
    finally:
        conn.close()
    if not record:
        raise HTTPException(status_code=404, detail="Application record not found.")
    saved_content = (record["interview_prep"] or "").strip()
    return {
        "job_id": job_id,
        "company": record["company"],
        "position": record["position"] or record["title"],
        "status": record["status"],
        "content": saved_content or starter_interview_prep(record),
        "has_saved_content": bool(saved_content),
        "updated_at": record["interview_prep_updated_at"],
    }


@app.put("/api/jobs/{job_id}/interview-prep")
def update_interview_prep(job_id: int, req: InterviewPrepUpdateRequest) -> dict:
    """Persist user-reviewed interview preparation as an atomic local edit."""
    conn = get_db_connection()
    try:
        result = save_interview_prep(conn, job_id, req.content)
        conn.commit()
    except LookupError as exc:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()
    return {"success": True, **result}


@app.post("/api/jobs/{job_id}/interview-prep/generate")
def generate_job_interview_prep(
    job_id: int,
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """Generate and safely persist an AI-assisted interview plan on explicit request."""
    with operation_scope(operation_id) as operation:
        try:
            operation.checkpoint()
            conn = get_db_connection()
            try:
                record = _interview_prep_record(conn, job_id)
                profile = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
            finally:
                conn.close()
            if not record:
                raise HTTPException(status_code=404, detail="Application record not found.")
            if not profile:
                raise HTTPException(status_code=400, detail="Profile settings were not found.")
            content = generate_interview_prep(settings_from_profile(profile), record)
            operation.checkpoint()
            conn = get_db_connection()
            try:
                result = save_interview_prep(conn, job_id, content)
                conn.commit()
            finally:
                conn.close()
            return {"success": True, **result}
        except OperationCancelled:
            return {
                "success": False,
                "cancelled": True,
                "message": "Interview preparation stopped. Previously saved notes were preserved.",
            }
        except AIProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

# Applications logs endpoint
@app.get("/api/jobs/{job_id}/engagements")
def get_job_engagements(job_id: int) -> dict:
    """Load locally tracked people, relationship activity, and assessment steps."""
    conn = get_db_connection()
    try:
        return list_engagements(conn, job_id)
    except EngagementNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/engagements", status_code=201)
def add_job_engagement(job_id: int, req: ApplicationEngagementRequest) -> dict:
    """Create one bounded, local relationship or assessment record."""
    conn = get_db_connection()
    try:
        return create_engagement(conn, job_id, req.model_dump())
    except EngagementNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (EngagementLimitError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()


@app.put("/api/jobs/{job_id}/engagements/{engagement_id}")
def edit_job_engagement(
    job_id: int,
    engagement_id: int,
    req: ApplicationEngagementRequest,
) -> dict:
    """Edit a selected local relationship or assessment record."""
    conn = get_db_connection()
    try:
        return update_engagement(conn, job_id, engagement_id, req.model_dump())
    except EngagementNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()


@app.delete("/api/jobs/{job_id}/engagements/{engagement_id}")
def remove_job_engagement(job_id: int, engagement_id: int) -> dict:
    """Delete one explicitly selected local tracking record."""
    conn = get_db_connection()
    try:
        delete_engagement(conn, job_id, engagement_id)
        return {"success": True, "deleted": engagement_id}
    except EngagementNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/applications")
def get_applications() -> list[dict]:
    """
    Retrieve logs of all job applications that have been processed.
    
    Returns:
        list[dict]: A list of application records.
    """
    conn = get_db_connection()
    rows = conn.execute("""
    SELECT a.*, j.url,
           (SELECT COUNT(*) FROM application_engagements e WHERE e.job_id = a.job_id) AS engagement_count
    FROM applications a
    LEFT JOIN jobs j ON a.job_id = j.id
    ORDER BY a.date_applied DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/application-insights")
def get_application_insights() -> dict:
    """Return local-only application funnel and response-rate aggregates."""
    conn = get_db_connection()
    try:
        return build_application_insights(conn)
    finally:
        conn.close()

# Mount frontend static folder
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
def read_index() -> FileResponse:
    """
    Serve the main SPA dashboard index page.
    
    Returns:
        FileResponse: The HTML page response.
    """
    return FileResponse(
        os.path.join(static_dir, "index.html"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )
