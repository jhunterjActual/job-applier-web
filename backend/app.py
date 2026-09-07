import hashlib
import hmac
import json
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from typing import Literal, Optional
from datetime import date, datetime, timedelta, timezone

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
from application_read_repository import runtime_application_read_repository
from application_engagements import EngagementLimitError
from application_workspace_repository import (
    ApplicationWorkspaceChanged,
    ApplicationWorkspaceNotFound,
    runtime_application_workspace_repository,
)
from data_export import build_user_data_export
from user_data_export_repository import (
    EXPORT_UNAVAILABLE_MESSAGE,
    runtime_user_data_export_repository,
)
from account_erasure_repository import (
    ACCOUNT_ERASURE_RECEIPT_RETENTION_DAYS,
    ACCOUNT_ERASURE_UNAVAILABLE_MESSAGE,
    AccountErasureUnavailable,
    runtime_account_erasure_repository,
)
from database import get_db_connection, init_db
from data_store import (
    LOCAL_DATA_STORE,
    SHARED_DATA_STORE,
    check_data_store_ready,
    data_store_kind,
    validate_data_store_configuration,
)
from local_users import (
    LocalUserAuthorizationError,
    authorized_local_user,
    check_identity_store_ready,
    validate_identity_store_configuration,
)
from postgres_database import dispose_postgres_engines
from provider_secrets import (
    SHARED_SECRET_STORE,
    check_provider_secret_store_ready,
    current_provider_secret_configured,
    current_owner_user_id,
    provider_secret_store_kind,
    reset_current_owner_user_id,
    set_current_owner_user_id,
    update_provider_secrets,
    validate_provider_secret_store_configuration,
)
from object_store import (
    ObjectStoreBackendError,
    ObjectStoreConfigurationError,
    ObjectStoreIntegrityError,
    check_object_store_ready,
    validate_object_store_configuration,
)
from stored_objects import StoredObjectNotFound, download_stored_object
from generated_material_repository import (
    GeneratedMaterialChanged,
    GeneratedMaterialNotFound,
    GeneratedMaterialUnavailable,
    runtime_generated_material_repository,
)
from discovery_repository import runtime_discovery_repository
from headquarters_cache_repository import (
    HeadquartersCacheUnavailable,
    runtime_headquarters_cache_repository,
)
from search_run_execution import execute_search_claim
from search_run_repository import (
    SearchRunAlreadyActive,
    SearchRunUnavailable,
    runtime_search_run_repository,
)
from profile_repository import runtime_profile_repository
from web_auth import (
    LOGIN_FLOW_COOKIE_NAME,
    LOGIN_FLOW_MAX_AGE_SECONDS,
    SESSION_COOKIE_MAX_AGE_SECONDS,
    SESSION_COOKIE_NAME,
    AuthenticationConfigurationError,
    IdentityNotAuthorizedError,
    LoginFlowError,
    authenticate_session,
    begin_login,
    complete_login,
    get_auth_configuration,
    logout_url,
)
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
    validate_public_http_url,
)
from utils import generate_resume_pdf, resume_pdf_metadata
from lifecycle_repository import MAX_LIFECYCLE_NOTES_CHARS, runtime_lifecycle_repository
from verification_repository import (
    VerificationTargetChanged,
    runtime_verification_repository,
)
from job_retention_repository import (
    CleanupBatchTooLarge,
    CleanupPreviewChanged,
    DEPENDENT_DELETE_ACTION,
    DependentCleanupUnavailable,
    JobRetentionNotFound,
    JobRetentionProtected,
    runtime_job_retention_repository,
)
from job_filters import derive_job_filter_facets
from interview_prep import (
    MAX_INTERVIEW_PREP_CHARS,
    generate_interview_prep,
    starter_interview_prep,
)
from source_diagnostics import (
    build_maintainer_report,
    format_maintainer_report,
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
from version import APP_BUILD
from operations import (
    OperationCancelled,
    OperationToken,
    finish_operation,
    operation_progress,
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
import maintenance_notice

MAX_RESUME_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_RESUME_DOCUMENT_UPLOAD_BYTES = 10 * 1024 * 1024
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}
BACKUP_RESTORE_LOCK = Lock()
TRUSTED_PROXY_TOKEN_HEADER = "x-careertrellis-proxy-token"
PUBLIC_SHARED_HOST_PATHS = frozenset(
    {"/health/live", "/health/ready", "/auth/login", "/auth/callback",
     "/auth/sign-in", "/api/maintenance-notice",
     "/static/maintenance-notice.js", "/static/maintenance-notice.css"}
)
FULL_BACKUP_PATHS = frozenset({"/api/full-backup", "/api/full-backup/restore"})
SHARED_BACKUP_RESTORE_DETAIL = (
    "In-app full backup and restore are unavailable in shared mode."
)
LOCAL_ACCOUNT_ERASURE_DETAIL = (
    "Account erasure is available only in shared-host mode."
)

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
    try:
        return runtime_headquarters_cache_repository(
            sqlite_connection_factory=get_db_connection
        ).get(cache_key)
    except HeadquartersCacheUnavailable:
        return None


def _cache_headquarters(cache_key: str, result: HeadquartersResult, selected_provider: str) -> None:
    """Cache provider results; AI fallbacks remain retryable and explicitly provisional."""
    try:
        runtime_headquarters_cache_repository(
            sqlite_connection_factory=get_db_connection
        ).cache_openstreetmap_result(cache_key, result, selected_provider)
    except HeadquartersCacheUnavailable:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate the runtime, start optional analytics, and stop within a bounded budget."""
    config.validate_runtime_configuration()
    data_store = validate_data_store_configuration()
    if data_store == LOCAL_DATA_STORE:
        init_db()
    get_auth_configuration()
    validate_identity_store_configuration()
    validate_provider_secret_store_configuration()
    validate_object_store_configuration()
    initialize_analytics(APP_BUILD)
    try:
        yield
    finally:
        shutdown_analytics(timeout_seconds=0.5)
        dispose_postgres_engines()


app = FastAPI(title="CareerTrellis API", lifespan=lifespan)


def _allowed_authority(authority: str, scheme: str = "http") -> Optional[tuple[str, int]]:
    """Return an explicitly trusted loopback or configured public host and port."""
    if not authority or any(character in authority for character in "/\\@"):
        return None
    try:
        parsed = urlsplit(f"//{authority}")
        hostname = (parsed.hostname or "").lower()
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    allowed_hosts = config.LOCAL_HOSTS
    if config.get_runtime_environment() != "local":
        allowed_hosts |= config.get_allowed_public_hosts()
    if hostname not in allowed_hosts:
        return None
    return hostname, port


def _same_origin(value: str, request_authority: tuple[str, int], scheme: str) -> bool:
    """Validate an Origin or Referer value against the exact request origin."""
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


def _secured_response(request: Request, response: Response) -> Response:
    """Apply browser security headers consistently to normal and auth-boundary responses."""
    if (
        request.url.path == "/"
        or request.url.path.startswith("/static/")
        or request.url.path.startswith("/auth/")
        or request.url.path == "/api/session"
        or request.url.path == "/api/maintenance-notice"
        or request.url.path == "/api/account/erasure"
    ):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    if request.url.path.startswith("/auth/"):
        response.headers["Referrer-Policy"] = "no-referrer"
    else:
        response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if config.get_runtime_environment() != "local":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def _delete_auth_cookie(response: Response, cookie_name: str) -> None:
    response.delete_cookie(
        cookie_name,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _logout_incomplete_response(request: Request) -> Response:
    """End local browser state without claiming the provider session was revoked."""
    request.state.suppress_refreshed_session = True
    response = FileResponse(
        config.BASE_DIR / "static" / "sign-out-incomplete.html", status_code=503,
    )
    _delete_auth_cookie(response, SESSION_COOKIE_NAME)
    _delete_auth_cookie(response, LOGIN_FLOW_COOKIE_NAME)
    return _secured_response(request, response)


def _unauthenticated_response(request: Request, clear_cookie: bool = False) -> Response:
    accepts_html = "text/html" in request.headers.get("accept", "").lower()
    if request.method.upper() == "GET" and (request.url.path == "/" or accepts_html):
        response: Response = RedirectResponse("/auth/sign-in", status_code=303)
    else:
        response = JSONResponse({"detail": "Authentication required."}, status_code=401)
    if clear_cookie:
        _delete_auth_cookie(response, SESSION_COOKIE_NAME)
    return _secured_response(request, response)


def _authentication_unavailable_response(request: Request) -> Response:
    """Keep API failures fixed while giving browsers a safe fresh-login path."""
    accepts_html = "text/html" in request.headers.get("accept", "").lower()
    if request.method.upper() == "GET" and (request.url.path == "/" or accepts_html):
        response: Response = Response(
            """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Sign-in needs attention</title></head>
<body>
<main>
<h1>Sign-in needs attention</h1>
<p>Your current browser session could not be verified.</p>
<p><a href="/auth/login">Restart sign-in in this browser</a></p>
</main>
</body>
</html>
""",
            status_code=503,
            media_type="text/html",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    else:
        response = JSONResponse(
            {"detail": "Authentication is temporarily unavailable."},
            status_code=503,
        )
    return _secured_response(request, response)


def _require_local_backup_restore() -> None:
    """Keep SQLite workspace archives out of PostgreSQL-authoritative runtimes."""
    if data_store_kind() == SHARED_DATA_STORE:
        raise HTTPException(status_code=409, detail=SHARED_BACKUP_RESTORE_DETAIL)


def _operation_owner_id() -> int:
    """Bind in-memory work to the authenticated owner in shared runtimes."""
    if data_store_kind() == SHARED_DATA_STORE:
        return current_owner_user_id()
    return 0


@app.middleware("http")
async def protect_local_browser_boundary(request: Request, call_next):
    """Enforce the configured browser boundary and prevent direct origin access."""
    request_authority = _allowed_authority(request.headers.get("host", ""), request.url.scheme)
    if request_authority is None:
        return JSONResponse({"detail": "Invalid local Host header."}, status_code=400)

    if request_authority[0] not in config.LOCAL_HOSTS:
        expected_token = config.get_trusted_proxy_token()
        supplied_token = request.headers.get(TRUSTED_PROXY_TOKEN_HEADER, "")
        if not expected_token or not hmac.compare_digest(supplied_token, expected_token):
            return JSONResponse({"detail": "Trusted reverse proxy required."}, status_code=403)

    if request.method.upper() not in SAFE_HTTP_METHODS:
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if fetch_site in {"cross-site", "same-site"}:
            return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)

        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        source = origin or referer
        if source and not _same_origin(source, request_authority, request.url.scheme):
            return JSONResponse({"detail": "Request origin does not match this app."}, status_code=403)

    # Record the selected storage policy before any route/body handling, but
    # preserve the normal private-route authentication boundary below.
    shared_backup_restore_requested = (
        request.method.upper() == "POST"
        and request.url.path in FULL_BACKUP_PATHS
        and data_store_kind() == SHARED_DATA_STORE
    )

    auth_settings = None
    if request.url.path not in {"/health/live", "/health/ready"}:
        try:
            auth_settings = get_auth_configuration()
        except AuthenticationConfigurationError:
            if request.method == "POST" and request.url.path == "/auth/logout":
                return _logout_incomplete_response(request)
            return _secured_response(
                request,
                JSONResponse({"detail": "Authentication is not configured."}, status_code=503),
            )
    owner_context_token = None
    if auth_settings is not None and request.url.path not in PUBLIC_SHARED_HOST_PATHS:
        logout_requested = request.method == "POST" and request.url.path == "/auth/logout"
        sealed_session = request.cookies.get(SESSION_COOKIE_NAME, "")
        if len(sealed_session) > 8192:
            if logout_requested:
                return _logout_incomplete_response(request)
            return _unauthenticated_response(request, clear_cookie=True)
        try:
            authentication = await run_in_threadpool(
                authenticate_session,
                auth_settings,
                sealed_session,
            )
        except Exception:
            if logout_requested:
                return _logout_incomplete_response(request)
            raise
        if logout_requested:
            # Terminating this verified browser session requires no data-owner
            # access; disabled users must still be able to sign out. Return
            # before middleware can reissue a refreshed cookie.
            if authentication.status != "authenticated" or not authentication.session_id:
                return _logout_incomplete_response(request)
            request.state.workos_session_id = authentication.session_id
            return _secured_response(request, await run_in_threadpool(auth_logout, request))
        if authentication.status == "unavailable":
            return _authentication_unavailable_response(request)
        if authentication.status != "authenticated" or not authentication.workos_user_id:
            return _unauthenticated_response(request, authentication.clear_cookie)
        local_user = await run_in_threadpool(
            authorized_local_user,
            authentication.workos_user_id,
            auth_settings.owner_workos_user_id,
        )
        if local_user is None:
            return _unauthenticated_response(request, clear_cookie=True)
        local_user_id = local_user.id
        request.state.local_user_id = local_user_id
        request.state.local_user_role = local_user.role
        request.state.workos_session_id = authentication.session_id
        request.state.refreshed_session = authentication.refreshed_session
        owner_context_token = set_current_owner_user_id(local_user_id)

    try:
        if shared_backup_restore_requested:
            # An authenticated shared request is rejected before call_next, so
            # FastAPI never parses a restore upload or creates an UploadFile.
            response = JSONResponse(
                {"detail": SHARED_BACKUP_RESTORE_DETAIL},
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        else:
            response = await call_next(request)
    finally:
        if owner_context_token is not None:
            reset_current_owner_user_id(owner_context_token)
    refreshed_session = getattr(request.state, "refreshed_session", None)
    if refreshed_session and not getattr(request.state, "suppress_refreshed_session", False):
        response.set_cookie(
            SESSION_COOKIE_NAME,
            refreshed_session,
            max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
    return _secured_response(request, response)

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
    keywords: str = Field(max_length=500)
    location: Optional[str] = Field(default="", max_length=240)
    save_search: bool = False
    saved_search_name: Optional[str] = Field(default=None, max_length=80)
    saved_search_id: Optional[int] = Field(default=None, gt=0)
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
    action: Literal["archive", "delete", "restore", "delete_with_dependents"]
    preview_token: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class LifecycleUpdateRequest(BaseModel):
    status: Literal["matched", "tailored", "form_filled", "submitted", "applied", "interview", "offer", "rejected", "withdrawn", "closed"]
    applied_on: Optional[date] = None
    method: Optional[Literal["company_site", "job_board", "email", "recruiter", "referral", "other"]] = None
    notes: Optional[str] = Field(default=None, max_length=MAX_LIFECYCLE_NOTES_CHARS)
    follow_up_on: Optional[date] = None


class MaterialsUpdateRequest(BaseModel):
    tailored_resume: str = Field(max_length=MAX_BASE_RESUME_CHARS)
    cover_letter: str = Field(max_length=MAX_BASE_RESUME_CHARS)


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


class AccountErasureRequest(BaseModel):
    confirmation: Literal["DELETE MY CAREERTRELLIS DATA"]
    acknowledge_irreversible: Literal[True]


@app.get("/auth/sign-in", include_in_schema=False)
def sign_in_landing() -> Response:
    """App-owned public notice surface; authentication still starts at /auth/login."""
    return FileResponse(config.BASE_DIR / "static" / "sign-in.html")


@app.get("/api/maintenance-notice")
def get_maintenance_notice() -> Response:
    """Read only the explicitly public notice, never operator audit/configuration."""
    local = config.get_runtime_environment() == "local"
    path = config.DATA_DIR / "maintenance-notice.json" if local else maintenance_notice.NOTICE_FILE
    try:
        return JSONResponse(maintenance_notice.public_notice(path, strict=not local))
    except Exception:
        return JSONResponse({"detail": "Maintenance notice updates are temporarily unavailable."}, status_code=503)


@app.get("/auth/login", include_in_schema=False)
async def auth_login(request: Request) -> Response:
    """Redirect a shared-host browser to the hosted WorkOS AuthKit UI."""
    settings = get_auth_configuration()
    if settings is None:
        raise HTTPException(status_code=404, detail="Not found")
    account_choices = request.query_params.getlist("account")
    if account_choices not in ([], ["different"]):
        raise HTTPException(status_code=400, detail="Invalid sign-in choice.")
    try:
        pending_login = await run_in_threadpool(
            begin_login, settings, different_account=account_choices == ["different"],
        )
    except Exception:
        response = JSONResponse(
            {"detail": "Authentication is temporarily unavailable."}, status_code=503,
        )
        _delete_auth_cookie(response, SESSION_COOKIE_NAME)
        _delete_auth_cookie(response, LOGIN_FLOW_COOKIE_NAME)
        return response
    response = RedirectResponse(pending_login.authorization_url, status_code=303)
    _delete_auth_cookie(response, SESSION_COOKIE_NAME)
    response.set_cookie(
        LOGIN_FLOW_COOKIE_NAME,
        pending_login.sealed_flow,
        max_age=LOGIN_FLOW_MAX_AGE_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/auth/callback", include_in_schema=False)
async def auth_callback(request: Request) -> Response:
    """Complete a one-time PKCE login and establish the encrypted session cookie."""
    settings = get_auth_configuration()
    if settings is None:
        raise HTTPException(status_code=404, detail="Not found")
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    sealed_flow = request.cookies.get(LOGIN_FLOW_COOKIE_NAME, "")
    if not (
        1 <= len(code) <= 2048
        and 1 <= len(state) <= 512
        and 1 <= len(sealed_flow) <= 4096
    ):
        response = JSONResponse({"detail": "The login callback is invalid."}, status_code=400)
        _delete_auth_cookie(response, LOGIN_FLOW_COOKIE_NAME)
        return response
    try:
        completed_login = await run_in_threadpool(
            complete_login,
            settings,
            code=code,
            state=state,
            sealed_flow=sealed_flow,
        )
        local_user = await run_in_threadpool(
            authorized_local_user,
            completed_login.workos_user_id,
            settings.owner_workos_user_id,
            touch=True,
        )
        if local_user is None:
            raise LocalUserAuthorizationError("The local user is not active.")
    except LoginFlowError as exc:
        response = JSONResponse({"detail": "The login attempt expired or is invalid."}, status_code=400)
        _delete_auth_cookie(response, LOGIN_FLOW_COOKIE_NAME)
        return response
    except (IdentityNotAuthorizedError, LocalUserAuthorizationError) as exc:
        response = JSONResponse({"detail": "This identity is not authorized."}, status_code=403)
        _delete_auth_cookie(response, LOGIN_FLOW_COOKIE_NAME)
        return response
    except Exception as exc:
        response = JSONResponse(
            {"detail": "Authentication is temporarily unavailable."},
            status_code=503,
        )
        _delete_auth_cookie(response, LOGIN_FLOW_COOKIE_NAME)
        return response

    response = RedirectResponse("/", status_code=303)
    _delete_auth_cookie(response, LOGIN_FLOW_COOKIE_NAME)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        completed_login.sealed_session,
        max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/auth/logout", include_in_schema=False)
def auth_logout(request: Request) -> Response:
    """Clear the local session and terminate its WorkOS session."""
    settings = get_auth_configuration()
    if settings is None:
        raise HTTPException(status_code=404, detail="Not found")
    session_id = getattr(request.state, "workos_session_id", None)
    request.state.suppress_refreshed_session = True
    if not session_id:
        return _logout_incomplete_response(request)
    try:
        destination = logout_url(settings, session_id)
    except Exception:
        return _logout_incomplete_response(request)
    response = RedirectResponse(destination, status_code=303)
    _delete_auth_cookie(response, SESSION_COOKIE_NAME)
    _delete_auth_cookie(response, LOGIN_FLOW_COOKIE_NAME)
    return response


@app.get("/api/session")
def get_session_summary(request: Request) -> dict[str, object]:
    """Expose only the authentication mode needed to render session controls."""
    if get_auth_configuration() is None:
        return {
            "authentication": "local",
            "authenticated": True,
            "account_erasure_available": False,
        }
    try:
        account_erasure_available = (
            data_store_kind() == SHARED_DATA_STORE
            and config.account_erasure_worker_enabled()
        )
    except config.RuntimeConfigurationError:
        account_erasure_available = False
    return {
        "authentication": "workos",
        "authenticated": bool(getattr(request.state, "local_user_id", None)),
        "account_erasure_available": account_erasure_available,
    }


@app.get("/api/private-objects/{object_id}")
def download_private_object(object_id: str, request: Request) -> Response:
    """Stream one owner-authorized object without exposing an R2 URL or key."""
    owner_user_id = getattr(request.state, "local_user_id", None)
    if not owner_user_id:
        raise HTTPException(status_code=404, detail="The generated object is unavailable.")
    try:
        download = download_stored_object(owner_user_id, object_id)
    except StoredObjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ObjectStoreBackendError, ObjectStoreIntegrityError) as exc:
        raise HTTPException(
            status_code=503,
            detail="The generated object is temporarily unavailable.",
        ) from exc
    return Response(
        content=download.content,
        media_type=download.content_type,
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Content-Disposition": f'attachment; filename="{download.filename}"',
        },
    )


@app.get("/api/version")
def get_app_version() -> dict:
    """Identify the running backend so launchers never open a stale instance."""
    return {"build": APP_BUILD}


@app.get("/health/live", include_in_schema=False)
def get_liveness() -> dict:
    """Report process liveness without touching a dependency or returning secrets."""
    return {"status": "ok", "build": APP_BUILD}


@app.get("/health/ready", include_in_schema=False)
def get_readiness() -> dict:
    """Report readiness only when configuration, storage, and the database are usable."""
    try:
        config.validate_runtime_configuration()
        auth_settings = get_auth_configuration()
        if not all(
            directory.is_dir() and os.access(directory, os.R_OK | os.W_OK)
            for directory in (config.DATA_DIR, config.UPLOAD_DIR, config.OUTPUT_DIR)
        ):
            raise RuntimeError("Runtime storage is not accessible.")
        check_data_store_ready(get_db_connection)
        check_identity_store_ready(
            auth_settings.owner_workos_user_id if auth_settings is not None else None
        )
        check_provider_secret_store_ready()
        check_object_store_ready()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Service is not ready.") from exc
    return {"status": "ready", "build": APP_BUILD}


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
    row = runtime_profile_repository(
        sqlite_connection_factory=get_db_connection
    ).get_profile()
    if row:
        result = dict(row)
        result["gemini_api_key_configured"] = provider_key_configured(row, "gemini")
        result["openai_api_key_configured"] = provider_key_configured(row, "openai")
        result.pop("gemini_api_key", None)
        result.pop("openai_api_key", None)
        local_google_key = result.pop("google_maps_api_key", "")
        result["google_maps_api_key_configured"] = (
            current_provider_secret_configured("google_maps")
            if provider_secret_store_kind() == SHARED_SECRET_STORE
            else bool(local_google_key or config.get_google_maps_api_key())
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
    try:
        result = runtime_profile_repository(
            sqlite_connection_factory=get_db_connection
        ).update_profile(
            {
                **profile.model_dump(exclude={"professional_evidence"}),
                "professional_evidence": (
                    profile.professional_evidence.model_dump()
                    if profile.professional_evidence is not None
                    else None
                ),
                "base_resume_id_provided": "base_resume_id" in profile.model_fields_set,
            }
        )
    except BaseResumeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "success": True,
        "message": "Profile updated successfully.",
        **result,
    }


def _resume_api_action(method: str, *arguments):
    try:
        repository = runtime_profile_repository(sqlite_connection_factory=get_db_connection)
        return getattr(repository, method)(*arguments)
    except BaseResumeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LastBaseResumeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/base-resumes")
def get_base_resumes() -> list[dict]:
    return _resume_api_action("list_base_resumes")


@app.get("/api/base-resumes/{resume_id}")
def get_base_resume_details(resume_id: int) -> dict:
    return _resume_api_action("get_base_resume", resume_id)


@app.post("/api/base-resumes/{resume_id}/activate")
def activate_base_resume_endpoint(resume_id: int) -> dict:
    return _resume_api_action("activate_base_resume", resume_id)


@app.get("/api/base-resumes/{resume_id}/versions")
def get_base_resume_versions(resume_id: int) -> list[dict]:
    return _resume_api_action("list_versions", resume_id)


@app.get("/api/base-resumes/{resume_id}/versions/{version_number}")
def get_base_resume_version(resume_id: int, version_number: int) -> dict:
    return _resume_api_action("get_version", resume_id, version_number)


@app.post("/api/base-resumes/{resume_id}/versions/{version_number}/restore")
def restore_base_resume_version(resume_id: int, version_number: int) -> dict:
    return _resume_api_action("restore_version", resume_id, version_number)


@app.delete("/api/base-resumes/{resume_id}")
def delete_base_resume_endpoint(resume_id: int) -> dict:
    replacement = _resume_api_action("delete_base_resume", resume_id)
    return {"success": True, "active_resume": replacement}


@app.put("/api/profile/secrets")
def update_profile_secrets(secrets: ProfileSecretsUpdate, request: Request = None) -> dict:
    """Update explicitly supplied secrets without ever returning their values."""
    supplied = {
        "gemini": secrets.gemini_api_key,
        "openai": secrets.openai_api_key,
        "google_maps": secrets.google_maps_api_key,
    }
    if provider_secret_store_kind() == SHARED_SECRET_STORE:
        owner_user_id = getattr(request.state, "local_user_id", None) if request else None
        if not owner_user_id:
            raise HTTPException(status_code=401, detail="Authentication required.")
        update_provider_secrets(
            owner_user_id,
            {provider: value for provider, value in supplied.items() if value is not None},
        )
        return {
            "success": True,
            "gemini_api_key_configured": current_provider_secret_configured("gemini"),
            "openai_api_key_configured": current_provider_secret_configured("openai"),
            "google_maps_api_key_configured": current_provider_secret_configured("google_maps"),
        }

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
    with operation_scope(operation_id, owner_id=_operation_owner_id()) as operation:
        try:
            operation.checkpoint()
            profile = runtime_profile_repository(
                sqlite_connection_factory=get_db_connection
            ).get_profile()
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
    with operation_scope(operation_id, owner_id=_operation_owner_id()) as operation:
        try:
            operation.checkpoint()
            profile = runtime_profile_repository(
                sqlite_connection_factory=get_db_connection
            ).get_profile()
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

    operation = start_operation(operation_id, owner_id=_operation_owner_id())
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
            profile = runtime_profile_repository(
                sqlite_connection_factory=get_db_connection
            ).get_profile()
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

def run_local_search_run(run_id: str) -> None:
    """Claim one SQLite run after the request transaction has fully closed."""
    repository = runtime_search_run_repository(
        sqlite_connection_factory=get_db_connection
    )
    claim = repository.claim(run_id, str(uuid4()))
    if claim is not None:
        execute_search_claim(claim, repository)


@app.get("/api/operations/{operation_id}/status")
def get_operation_status(operation_id: str, response: Response) -> dict:
    """Return one owner-bound, privacy-safe active tailoring status."""
    response.headers["Cache-Control"] = "no-store, max-age=0"
    status = operation_progress(
        operation_id,
        owner_id=_operation_owner_id(),
        kind="tailor",
    )
    if status is None:
        raise HTTPException(
            status_code=404,
            detail="The operation is not active.",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    return status


@app.post("/api/operations/{operation_id}/cancel")
def cancel_operation(operation_id: str) -> dict:
    """Request cooperative cancellation at the operation's next safe checkpoint."""
    active = request_cancellation(operation_id, owner_id=_operation_owner_id())
    try:
        active = runtime_search_run_repository(
            sqlite_connection_factory=get_db_connection
        ).request_cancel(operation_id) or active
    except SearchRunUnavailable as exc:
        if not active:
            raise HTTPException(
                status_code=503,
                detail="Search cancellation is temporarily unavailable.",
            ) from exc
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
    jobs = runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).list_jobs(include_archived)
    for job in jobs:
        job["filter_facets"] = derive_job_filter_facets(job)
    return jobs


@app.post("/api/jobs/import/preview")
def preview_manual_job(
    req: ManualJobPreviewRequest,
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """Validate a public posting URL and extract editable details without saving it."""
    with operation_scope(operation_id, owner_id=_operation_owner_id()) as operation:
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

    identity = runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).job_identity(canonical_url)
    if identity["suppressed"]:
        return {
            "success": True,
            "duplicate": False,
            "suppressed": True,
            "message": "This posting was previously deleted. Clear its suppression in Clean Up before importing it again.",
            "job": {"url": canonical_url},
        }
    if identity["existing"]:
        return {
            "success": True,
            "duplicate": True,
            "existing_job": identity["existing"],
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
        identity = runtime_discovery_repository(
            sqlite_connection_factory=get_db_connection
        ).job_identity(resolved_url)
        if identity["suppressed"]:
            return {
                "success": True,
                "duplicate": False,
                "suppressed": True,
                "message": "This posting was previously deleted. Clear its suppression in Clean Up before importing it again.",
                "job": {"url": resolved_url},
            }
        if identity["existing"]:
            return {
                "success": True,
                "duplicate": True,
                "existing_job": identity["existing"],
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
    with operation_scope(operation_id, owner_id=_operation_owner_id()) as operation:
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

    profile = runtime_profile_repository(
        sqlite_connection_factory=get_db_connection
    ).get_profile()
    operation.checkpoint()

    # Save the reviewed posting first so a later Stop request can preserve it
    # as explicitly unscored. The repository owns the atomic suppression,
    # duplicate, and insert transaction for the authenticated owner.
    match_score = None
    match_analysis = "Manual import saved; match analysis has not been completed."
    created = runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).create_job_if_available(
        title=title,
        company=company,
        description=description,
        url=canonical_url,
        match_score=match_score,
        match_analysis=match_analysis,
        date_found=date.today(),
        location=location,
        work_arrangement=req.work_arrangement,
        employment_type=req.employment_type,
        compensation=compensation,
        source=source,
        last_checked_at=datetime.now(timezone.utc),
    )
    if created["outcome"] == "suppressed":
        raise HTTPException(
            status_code=409,
            detail="This posting was previously deleted. Clear its suppression in Clean Up before importing it again.",
        )
    if created["outcome"] == "duplicate":
        existing = created.get("job") or {}
        duplicate_suffix = f" as job #{existing['id']}" if existing.get("id") else ""
        raise HTTPException(
            status_code=409,
            detail=f"This posting is already saved{duplicate_suffix}.",
        )
    if created["outcome"] != "created" or not created.get("job"):
        raise RuntimeError("Job repository returned an invalid create result.")
    job_id = created["job"]["id"]

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
                completed = runtime_discovery_repository(
                    sqlite_connection_factory=get_db_connection
                ).complete_initial_job_match(
                    job_id,
                    match_score=match_score,
                    match_analysis=match_analysis,
                )
                if completed is None:
                    raise RuntimeError("The saved job could not accept its initial match result.")
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
    try:
        result = runtime_lifecycle_repository(
            sqlite_connection_factory=get_db_connection
        ).update_lifecycle(
            job_id,
            req.status,
            applied_on=req.applied_on,
            method=req.method,
            notes=req.notes,
            follow_up_on=req.follow_up_on,
        )
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
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/lifecycle/undo")
def undo_job_lifecycle(job_id: int) -> dict:
    """Undo the latest recorded manual lifecycle correction."""
    try:
        result = runtime_lifecycle_repository(
            sqlite_connection_factory=get_db_connection
        ).undo_latest_lifecycle_change(job_id)
        return {"success": True, **result}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/jobs/cleanup-preview")
def preview_job_cleanup() -> dict:
    """Preview protected archive/delete/restore candidate sets without mutation."""
    try:
        return runtime_job_retention_repository(
            sqlite_connection_factory=get_db_connection
        ).cleanup_preview()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="CareerTrellis could not prepare the cleanup preview.",
        ) from exc


@app.post("/api/jobs/cleanup")
def cleanup_jobs(req: CleanupRequest) -> dict:
    """Apply a previously previewed bulk cleanup operation transactionally."""
    try:
        search_active = runtime_search_run_repository(
            sqlite_connection_factory=get_db_connection
        ).has_active()
    except SearchRunUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Search status is temporarily unavailable; cleanup was not started.",
        ) from exc
    if search_active:
        raise HTTPException(status_code=409, detail="Wait for the current job search to finish before cleanup.")

    try:
        affected = runtime_job_retention_repository(
            sqlite_connection_factory=get_db_connection
        ).apply_cleanup(req.action, req.preview_token)
    except (CleanupPreviewChanged, CleanupBatchTooLarge) as exc:
        raise HTTPException(
            status_code=409,
            detail="The cleanup selection is no longer safe to apply. Refresh the preview before continuing.",
        ) from exc
    except DependentCleanupUnavailable as exc:
        raise HTTPException(
            status_code=409,
            detail="Dependent bulk deletion is unavailable in standalone mode.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="CareerTrellis could not complete the cleanup request.",
        ) from exc

    if req.action == DEPENDENT_DELETE_ACTION:
        message = (
            f"{affected} job(s) and their saved application work permanently deleted."
        )
    else:
        verbs = {
            "archive": "archived",
            "delete": "permanently deleted",
            "restore": "restored",
        }
        message = f"{affected} untouched job(s) {verbs[req.action]}."
    return {
        "success": True,
        "action": req.action,
        "affected": affected,
        "message": message,
    }


@app.get("/api/job-suppressions")
def get_job_suppressions() -> dict:
    """List privacy-minimized deleted-posting records for local review."""
    return runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).list_job_suppressions()


@app.delete("/api/job-suppressions/{suppression_id}")
def clear_job_suppression(suppression_id: int) -> dict:
    """Allow one intentionally deleted posting to be discovered again."""
    cleared = runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).clear_job_suppression(suppression_id)
    if not cleared:
        raise HTTPException(status_code=404, detail="Suppressed posting not found.")
    return {"success": True, "cleared": 1}


@app.delete("/api/job-suppressions")
def clear_all_job_suppressions() -> dict:
    """Allow all intentionally deleted postings to be discovered again."""
    cleared = runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).clear_all_job_suppressions()
    return {"success": True, "cleared": cleared}

@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int) -> dict:
    """
    Delete one owner-authorized job and its dependent records.
    
    Args:
        job_id (int): The unique ID of the job posting to delete.
        
    Returns:
        dict: Success status and feedback message.
    """
    try:
        runtime_job_retention_repository(
            sqlite_connection_factory=get_db_connection
        ).delete_job(job_id)
        return {"success": True, "message": "Job posting deleted successfully."}
    except JobRetentionNotFound as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc
    except JobRetentionProtected as exc:
        raise HTTPException(
            status_code=409,
            detail="This job cannot be deleted because it has related application activity or generated materials.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="CareerTrellis could not delete the job posting.",
        ) from exc

@app.get("/api/jobs/status")
def get_search_status() -> dict:
    """
    Get the status of the background job search process.
    
    Returns:
        dict: Containing a boolean key 'searching' indicating search status.
    """
    try:
        return runtime_search_run_repository(
            sqlite_connection_factory=get_db_connection
        ).status()
    except SearchRunUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Search status is temporarily unavailable.",
        ) from exc


@app.post("/api/jobs/search/cancel")
def cancel_active_search() -> dict:
    """Stop the authenticated owner's current durable search after a reload."""
    try:
        active = runtime_search_run_repository(
            sqlite_connection_factory=get_db_connection
        ).request_cancel_active()
    except SearchRunUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Search cancellation is temporarily unavailable.",
        ) from exc
    return {
        "success": True,
        "active": active,
        "message": (
            "Stopping safely after the current search step."
            if active else
            "The search has already finished."
        ),
    }


@app.get("/api/source-diagnostics")
def get_source_diagnostic_history(limit: int = 100) -> JSONResponse:
    """Retrieve recent privacy-minimized source notices for troubleshooting."""
    history = runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).list_source_diagnostics(limit)
    return JSONResponse(history, headers={"Cache-Control": "no-store"})


@app.get("/api/data-export")
def export_user_data() -> StreamingResponse:
    """Download the authenticated owner's explicit portable-data export."""
    try:
        snapshot = runtime_user_data_export_repository(
            sqlite_connection_factory=get_db_connection
        ).snapshot()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail=EXPORT_UNAVAILABLE_MESSAGE,
        ) from None

    # The repository transaction and connection are closed before public-shape
    # serialization starts and before the response iterator can yield bytes.
    try:
        payload = build_user_data_export(
            snapshot,
            exported_at=datetime.now().isoformat(timespec="seconds"),
            application_build=APP_BUILD,
        )
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    except Exception:
        raise HTTPException(
            status_code=503,
            detail=EXPORT_UNAVAILABLE_MESSAGE,
        ) from None
    filename = f"career-trellis-user-data-{date.today().isoformat()}.json"
    return StreamingResponse(
        content=iter((content,)),
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/account/erasure", status_code=202)
def request_account_erasure(
    erasure: AccountErasureRequest,
    request: Request,
) -> JSONResponse:
    """Disable the shared workspace and durably queue irreversible owner-data erasure."""
    if data_store_kind() != SHARED_DATA_STORE:
        raise HTTPException(status_code=409, detail=LOCAL_ACCOUNT_ERASURE_DETAIL)
    if not getattr(request.state, "local_user_id", None):
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not config.account_erasure_worker_enabled():
        raise HTTPException(
            status_code=503,
            detail=ACCOUNT_ERASURE_UNAVAILABLE_MESSAGE,
        )
    try:
        receipt = runtime_account_erasure_repository().request_erasure()
    except AccountErasureUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=ACCOUNT_ERASURE_UNAVAILABLE_MESSAGE,
        ) from exc
    except Exception:
        raise HTTPException(
            status_code=503,
            detail=ACCOUNT_ERASURE_UNAVAILABLE_MESSAGE,
        ) from None

    try:
        accepted_at_text = receipt["accepted_at"]
        if not isinstance(accepted_at_text, str) or len(accepted_at_text) > 64:
            raise ValueError("The account erasure receipt is invalid.")
        accepted_at = datetime.fromisoformat(accepted_at_text)
        if receipt["status"] != "accepted" or accepted_at.tzinfo is None:
            raise ValueError("The account erasure receipt is invalid.")
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=503,
            detail=ACCOUNT_ERASURE_UNAVAILABLE_MESSAGE,
        ) from None

    response = JSONResponse(
        {
            "status": "accepted",
            "accepted_at": accepted_at.isoformat(),
            "receipt_retention_days": ACCOUNT_ERASURE_RECEIPT_RETENTION_DAYS,
        },
        status_code=202,
        headers={"Cache-Control": "no-store"},
    )
    # Do not let an authentication refresh restore the cookie after the local
    # account row has been disabled by the acceptance transaction.
    request.state.suppress_refreshed_session = True
    _delete_auth_cookie(response, SESSION_COOKIE_NAME)
    return response


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
    _require_local_backup_restore()
    if not BACKUP_RESTORE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another backup or restore is already in progress.")
    operation = start_operation(operation_id, owner_id=_operation_owner_id())
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
    try:
        _require_local_backup_restore()
    except HTTPException:
        # Normal HTTP traffic is rejected by middleware before an UploadFile is
        # created. Direct/internal callers that already own one still receive
        # the same fixed response without leaking its underlying file handle.
        await file.close()
        raise
    if not confirm_replace:
        await file.close()
        raise HTTPException(status_code=400, detail="Confirm that the current workspace will be replaced.")
    if Path(file.filename or "").suffix.lower() != ".ctbackup":
        await file.close()
        raise HTTPException(status_code=400, detail="Choose a CareerTrellis .ctbackup file.")
    if not BACKUP_RESTORE_LOCK.acquire(blocking=False):
        await file.close()
        raise HTTPException(status_code=409, detail="Another backup or restore is already in progress.")

    operation = start_operation(operation_id, owner_id=_operation_owner_id())
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
    history = runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).list_source_diagnostics(500)
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
    history = runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).list_source_diagnostics(500)
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
    cleared = runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).clear_source_diagnostics()
    return {"success": True, "cleared": cleared}


@app.get("/api/saved-searches")
def get_saved_searches() -> list[dict]:
    return runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).list_saved_searches()


@app.get("/api/saved-searches/due")
def get_due_saved_searches() -> list[dict]:
    return runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).list_due_saved_searches(datetime.now(timezone.utc))


@app.delete("/api/saved-searches/{search_id}")
def delete_saved_search(search_id: int) -> dict:
    deleted = runtime_discovery_repository(
        sqlite_connection_factory=get_db_connection
    ).delete_saved_search(search_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Saved search not found.")
    return {"success": True}


@app.post("/api/jobs/{job_id}/verify")
def verify_job_posting(
    job_id: int,
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """Re-scrape one listing and mark it expired without deleting history."""
    with operation_scope(operation_id, owner_id=_operation_owner_id()) as operation:
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
    repository = runtime_verification_repository(
        sqlite_connection_factory=get_db_connection
    )
    try:
        target = repository.get_verification_target(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc
    if not target:
        raise HTTPException(status_code=404, detail="Job not found.")

    checked_at = datetime.now(timezone.utc)
    outcome = inspect_job_posting(target["url"], cancel_check=operation.checkpoint)
    operation.checkpoint()
    details = outcome["details"]
    if not details and outcome.get("status") != "stale":
        raise HTTPException(
            status_code=503,
            detail="The posting could not be verified because its source was unavailable or unreadable. No job status was changed.",
        )
    try:
        result = repository.apply_verification(
            job_id,
            expected_url=target["url"],
            details=details or None,
            checked_at=checked_at,
        )
    except VerificationTargetChanged as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return result

@app.post("/api/jobs/search")
def search_jobs(
    req: SearchRequest,
    background_tasks: BackgroundTasks,
    request: Request,
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
    selected_store = data_store_kind()
    if selected_store == SHARED_DATA_STORE and not config.search_worker_enabled():
        raise HTTPException(
            status_code=503,
            detail="The job search could not be queued.",
        )
    try:
        search_run_repository = runtime_search_run_repository(
            sqlite_connection_factory=get_db_connection
        )
        run_id = search_run_repository.enqueue(operation_id, req.keywords, req.location)
    except SearchRunAlreadyActive:
        return {
            "success": False,
            "active": True,
            "message": "A job search is already in progress.",
        }
    except SearchRunUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="The job search could not be queued.",
        ) from exc
    search_keywords = req.keywords
    search_location = req.location
    try:
        if req.save_search or req.saved_search_id:
            saved_search_repository = runtime_discovery_repository(
                sqlite_connection_factory=get_db_connection
            )
            run_at = datetime.now(timezone.utc)
            if req.save_search:
                search_name = (
                    req.saved_search_name or req.keywords or "Resume-suggested search"
                ).strip()
                saved = saved_search_repository.upsert_saved_search(
                    name=search_name,
                    keywords=req.keywords,
                    location=req.location or "",
                    schedule_frequency=req.schedule_frequency,
                    run_at=run_at,
                )
            else:
                saved = saved_search_repository.advance_saved_search_run(
                    req.saved_search_id, run_at
                )
                if saved is None:
                    raise HTTPException(status_code=404, detail="Saved search not found.")
            if req.save_search:
                search_keywords = saved["keywords"]
                search_location = saved["location"]
        search_run_repository.publish_prepared(
            run_id, search_keywords, search_location
        )
    except Exception as exc:
        try:
            search_run_repository.abandon_prepared(run_id)
        except Exception:
            pass
        if isinstance(exc, SearchRunUnavailable):
            raise HTTPException(
                status_code=503,
                detail="The job search could not be queued.",
            ) from exc
        raise
    if selected_store == LOCAL_DATA_STORE:
        background_tasks.add_task(run_local_search_run, run_id)
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
    with operation_scope(
        operation_id,
        owner_id=_operation_owner_id(),
        kind="tailor",
    ) as operation:
        operation.update_progress("loading_inputs", completed=0, total=5)
        try:
            return _tailor_resume_endpoint(job_id, operation)
        except OperationCancelled:
            operation.update_progress("cancelled")
            return {
                "success": False,
                "cancelled": True,
                "outcome": "cancelled",
                "message": "Tailoring stopped. Previously saved materials and job status were preserved.",
            }
        except Exception:
            operation.update_progress("failed")
            raise


def _tailor_resume_endpoint(job_id: int, operation: OperationToken) -> dict:
    started_at = time.monotonic()
    operation.checkpoint()
    shared_materials = data_store_kind() == SHARED_DATA_STORE
    material_repository = None
    material_context_token = None
    if shared_materials:
        try:
            material_repository = runtime_generated_material_repository()
            material_context = material_repository.generation_context(job_id)
        except GeneratedMaterialNotFound as exc:
            detail = str(exc)
            status_code = 400 if "Base resume" in detail else 404
            raise HTTPException(status_code=status_code, detail=detail) from exc
        except GeneratedMaterialUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="Generated application materials are temporarily unavailable.",
            ) from exc
        job = material_context["job"]
        profile = material_context["profile"]
        source_resume_id = material_context["source_resume_id"]
        source_resume_name = material_context["source_resume_name"]
        source_resume_version = material_context["source_resume_version"]
        source_professional_evidence = professional_evidence_markdown(
            material_context["source_professional_evidence"]
        )
        material_context_token = material_context["context_token"]
    else:
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
    
    operation.update_progress("generating_materials", completed=1, total=5)
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

    operation.update_progress("rendering_resume", completed=2, total=5)
    # Generate HTML/PDF file paths
    output_root = Path(config.OUTPUT_DIR)
    if not shared_materials:
        output_root.mkdir(parents=True, exist_ok=True)
    resume_pdf_path = output_root / f"tailored_resume_{job_id}.pdf"
    cover_letter_path = cover_letter_output_path(job_id, output_root)
    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=None if shared_materials else output_root,
        suffix=".pdf",
    ) as temporary_pdf:
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
    temporary_cover_letter_path = None
    if not shared_materials:
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

    existing = None
    if not shared_materials:
        # Check if application record already exists for this job.
        conn = get_db_connection()
        existing = conn.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()

    operation.update_progress("resolving_headquarters", completed=3, total=5)
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

    operation.update_progress("saving_materials", completed=4, total=5)
    # Object upload and the following short metadata/reference transaction are
    # separated from all provider/PDF work. No old object is deleted first.
    if shared_materials:
        try:
            material_repository.replace_materials(
                job_id,
                material_context_token,
                temporary_resume_path.read_bytes(),
                cover_letter_text,
                {
                    "company": job["company"],
                    "position": job["title"],
                    "us_hq": headquarters.address,
                    "headquarters_source": headquarters.source,
                    "headquarters_attribution": headquarters.attribution,
                    "resume_text": res["tailored_resume"],
                    "cover_letter": cover_letter_text,
                    "base_resume_id": source_resume_id,
                    "base_resume_name": source_resume_name,
                    "base_resume_version": source_resume_version,
                },
            )
        except GeneratedMaterialChanged as exc:
            raise HTTPException(
                status_code=409,
                detail="The job or profile changed while materials were generated. Review it and generate again.",
            ) from exc
        except GeneratedMaterialNotFound as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc
        except (
            GeneratedMaterialUnavailable,
            ObjectStoreBackendError,
            ObjectStoreIntegrityError,
            ValueError,
        ) as exc:
            raise HTTPException(
                status_code=503,
                detail="Generated application materials are temporarily unavailable.",
            ) from exc
    else:
        # The two file replacements and database update below are the local
        # short commit boundary. There are no cancellation checkpoints inside it.
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
    outcome = "completed_with_review" if headquarters.warning else "completed"
    operation.update_progress(outcome, completed=5, total=5)

    return {
        "success": True,
        "outcome": outcome,
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
    if data_store_kind() == SHARED_DATA_STORE:
        try:
            app_row = runtime_generated_material_repository().materials(job_id)
        except GeneratedMaterialNotFound:
            return {"success": False, "message": "No tailored details found for this job."}
        except GeneratedMaterialUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="Generated application materials are temporarily unavailable.",
            ) from exc
        return {
            "success": True,
            "cover_letter": app_row["cover_letter"],
            "tailored_resume": app_row["tailored_resume_text"] or "",
            "tailored_resume_path": None,
            "cover_letter_path": None,
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
    with operation_scope(operation_id, owner_id=_operation_owner_id()) as operation:
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

    shared_materials = data_store_kind() == SHARED_DATA_STORE
    material_repository = None
    material_context_token = None
    if shared_materials:
        try:
            material_repository = runtime_generated_material_repository()
            edit_context = material_repository.edit_context(job_id)
        except GeneratedMaterialNotFound as exc:
            raise HTTPException(status_code=404, detail="No tailored materials found for this job.") from exc
        except GeneratedMaterialUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="Generated application materials are temporarily unavailable.",
            ) from exc
        application = edit_context["application"]
        profile = edit_context["profile"]
        material_context_token = edit_context["context_token"]
    else:
        conn = get_db_connection()
        application = conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        profile = conn.execute("SELECT name, resume_mode FROM profile LIMIT 1").fetchone()
        conn.close()
        if not application:
            raise HTTPException(status_code=404, detail="No tailored materials found for this job.")

    output_root = Path(config.OUTPUT_DIR)
    if not shared_materials:
        output_root.mkdir(parents=True, exist_ok=True)
    output_path = Path(
        application.get("tailored_resume_path")
        or os.path.join(config.OUTPUT_DIR, f"tailored_resume_{job_id}.pdf")
    ) if isinstance(application, dict) else Path(
        application["tailored_resume_path"]
        or os.path.join(config.OUTPUT_DIR, f"tailored_resume_{job_id}.pdf")
    )
    cover_letter_path = cover_letter_output_path(job_id, output_root)
    resume_mode = profile["resume_mode"] if profile and profile["resume_mode"] else "general_professional"
    try:
        resume_text = apply_resume_section_template(resume_text, resume_mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    page_limit = 6 if resume_mode == "academic_cv" else 2
    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=None if shared_materials else output_root,
        suffix=".pdf",
    ) as temporary_pdf:
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

    temporary_cover_letter_path = None
    if not shared_materials:
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

    if shared_materials:
        try:
            material_repository.replace_materials(
                job_id,
                material_context_token,
                temporary_resume_path.read_bytes(),
                cover_letter,
                {
                    "company": application["company"],
                    "position": application["position"],
                    "us_hq": application["us_hq"],
                    "headquarters_source": application["headquarters_source"],
                    "headquarters_attribution": application["headquarters_attribution"],
                    "resume_text": resume_text,
                    "cover_letter": cover_letter,
                    "base_resume_id": application["base_resume_id"],
                    "base_resume_name": application["base_resume_name"],
                    "base_resume_version": application["base_resume_version"],
                },
                require_application=True,
            )
        except GeneratedMaterialChanged as exc:
            raise HTTPException(
                status_code=409,
                detail="The application materials changed while the PDF was generated. Review them and try again.",
            ) from exc
        except GeneratedMaterialNotFound as exc:
            raise HTTPException(status_code=404, detail="No tailored materials found for this job.") from exc
        except (
            GeneratedMaterialUnavailable,
            ObjectStoreBackendError,
            ObjectStoreIntegrityError,
            ValueError,
        ) as exc:
            raise HTTPException(
                status_code=503,
                detail="Generated application materials are temporarily unavailable.",
            ) from exc
    else:
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
    if data_store_kind() == SHARED_DATA_STORE:
        try:
            return runtime_generated_material_repository().materials(job_id)
        except GeneratedMaterialNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail="Generated application materials were not found.",
            ) from exc
        except GeneratedMaterialUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="Generated application materials are temporarily unavailable.",
            ) from exc
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
def download_tailored_resume(job_id: int) -> Response:
    """Download the generated resume using a readable, filesystem-safe name."""
    material = _application_materials(job_id)
    if data_store_kind() == SHARED_DATA_STORE:
        object_id = material["tailored_resume_object_id"]
        if not object_id:
            raise HTTPException(status_code=404, detail="The generated resume PDF is unavailable.")
        try:
            download = download_stored_object(current_owner_user_id(), object_id)
        except StoredObjectNotFound as exc:
            raise HTTPException(status_code=404, detail="The generated resume PDF is unavailable.") from exc
        except (
            ObjectStoreBackendError,
            ObjectStoreConfigurationError,
            ObjectStoreIntegrityError,
            SQLAlchemyError,
        ) as exc:
            raise HTTPException(
                status_code=503,
                detail="The generated resume PDF is temporarily unavailable.",
            ) from exc
        capture_event(
            "material_downloaded",
            {
                "result": "success",
                "source_category": source_category(material["job_source"]),
                "material_type": "resume",
            },
        )
        return Response(
            content=download.content,
            media_type="application/pdf",
            headers={
                "Cache-Control": "private, no-store, max-age=0",
                "Content-Disposition": (
                    f'attachment; filename="{material_download_name(material["company"], material["position"], "resume", ".pdf", candidate_name=material["candidate_name"])}"'
                ),
            },
        )
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
    if data_store_kind() == SHARED_DATA_STORE:
        company = material["company"]
        position = material["position"]
    else:
        company = material["job_company"]
        position = material["job_title"]
    resume_text = (material["tailored_resume_text"] or "").strip()
    if not resume_text:
        raise HTTPException(status_code=404, detail="The editable tailored resume source is unavailable.")
    candidate_name = material["candidate_name"] or ""
    try:
        content = build_accessible_resume_docx(
            resume_text,
            candidate_name=candidate_name,
            title=f"{candidate_name or 'Candidate'} - {position} Resume",
        )
    except ResumeDocumentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = material_download_name(
        company, position, "resume-accessible", ".docx",
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
def download_cover_letter(job_id: int) -> Response:
    """Download the generated cover letter as a plain-text attachment."""
    material = _application_materials(job_id)
    if data_store_kind() == SHARED_DATA_STORE:
        object_id = material["cover_letter_object_id"]
        if not object_id:
            raise HTTPException(status_code=404, detail="The generated cover letter is unavailable.")
        try:
            download = download_stored_object(current_owner_user_id(), object_id)
        except StoredObjectNotFound as exc:
            raise HTTPException(status_code=404, detail="The generated cover letter is unavailable.") from exc
        except (
            ObjectStoreBackendError,
            ObjectStoreConfigurationError,
            ObjectStoreIntegrityError,
            SQLAlchemyError,
        ) as exc:
            raise HTTPException(
                status_code=503,
                detail="The generated cover letter is temporarily unavailable.",
            ) from exc
        capture_event(
            "material_downloaded",
            {
                "result": "success",
                "source_category": source_category(material["job_source"]),
                "material_type": "cover_letter",
            },
        )
        return Response(
            content=download.content,
            media_type="text/plain; charset=utf-8",
            headers={
                "Cache-Control": "private, no-store, max-age=0",
                "Content-Disposition": (
                    f'attachment; filename="{material_download_name(material["company"], material["position"], "cover-letter", ".txt", candidate_name=material["candidate_name"])}"'
                ),
            },
        )
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
    if data_store_kind() == SHARED_DATA_STORE:
        if not material["tailored_resume_object_id"] or not material["cover_letter_object_id"]:
            raise HTTPException(status_code=400, detail="Downloadable application materials are not ready.")
    else:
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


@app.get("/api/jobs/{job_id}/interview-prep")
def get_interview_prep(job_id: int) -> dict:
    """Load saved interview notes or a local starter without calling an AI provider."""
    try:
        record = runtime_application_workspace_repository(
            sqlite_connection_factory=get_db_connection
        ).get_interview_workspace(job_id)
    except ApplicationWorkspaceNotFound as exc:
        raise HTTPException(status_code=404, detail="Application record not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="CareerTrellis could not load the interview workspace.",
        ) from exc
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
    try:
        result = runtime_application_workspace_repository(
            sqlite_connection_factory=get_db_connection
        ).save_interview_prep(job_id, req.content)
    except ApplicationWorkspaceNotFound as exc:
        raise HTTPException(status_code=404, detail="Application record not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="CareerTrellis could not save the interview workspace.",
        ) from exc
    return {"success": True, **result}


@app.post("/api/jobs/{job_id}/interview-prep/generate")
def generate_job_interview_prep(
    job_id: int,
    operation_id: Optional[str] = Header(default=None, alias="X-CareerTrellis-Operation"),
) -> dict:
    """Generate and safely persist an AI-assisted interview plan on explicit request."""
    with operation_scope(operation_id, owner_id=_operation_owner_id()) as operation:
        try:
            operation.checkpoint()
            repository = runtime_application_workspace_repository(
                sqlite_connection_factory=get_db_connection
            )
            record = repository.get_interview_workspace(
                job_id, for_generation=True
            )
            context_token = record.pop("context_token")
            profile = runtime_profile_repository(
                sqlite_connection_factory=get_db_connection
            ).get_profile()
            if not profile:
                raise HTTPException(status_code=400, detail="Profile settings were not found.")
            content = generate_interview_prep(settings_from_profile(profile), record)
            operation.checkpoint()
            result = repository.save_interview_prep(
                job_id,
                content,
                expected_context_token=context_token,
            )
            return {"success": True, **result}
        except OperationCancelled:
            return {
                "success": False,
                "cancelled": True,
                "message": "Interview preparation stopped. Previously saved notes were preserved.",
            }
        except ApplicationWorkspaceNotFound as exc:
            raise HTTPException(status_code=404, detail="Application record not found.") from exc
        except ApplicationWorkspaceChanged as exc:
            raise HTTPException(
                status_code=409,
                detail="The interview workspace changed while the draft was generated. Review it and generate again.",
            ) from exc
        except AIProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="CareerTrellis could not generate the interview workspace.",
            ) from exc

# Applications logs endpoint
@app.get("/api/jobs/{job_id}/engagements")
def get_job_engagements(job_id: int) -> dict:
    """Load locally tracked people, relationship activity, and assessment steps."""
    try:
        return runtime_application_workspace_repository(
            sqlite_connection_factory=get_db_connection
        ).list_engagements(job_id)
    except ApplicationWorkspaceNotFound as exc:
        raise HTTPException(status_code=404, detail="Application workspace not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="CareerTrellis could not load the application workspace.",
        ) from exc


@app.post("/api/jobs/{job_id}/engagements", status_code=201)
def add_job_engagement(job_id: int, req: ApplicationEngagementRequest) -> dict:
    """Create one bounded, local relationship or assessment record."""
    try:
        return runtime_application_workspace_repository(
            sqlite_connection_factory=get_db_connection
        ).create_engagement(job_id, req.model_dump())
    except ApplicationWorkspaceNotFound as exc:
        raise HTTPException(status_code=404, detail="Application workspace not found.") from exc
    except (EngagementLimitError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="CareerTrellis could not save the application workspace.",
        ) from exc


@app.put("/api/jobs/{job_id}/engagements/{engagement_id}")
def edit_job_engagement(
    job_id: int,
    engagement_id: int,
    req: ApplicationEngagementRequest,
) -> dict:
    """Edit a selected local relationship or assessment record."""
    try:
        return runtime_application_workspace_repository(
            sqlite_connection_factory=get_db_connection
        ).update_engagement(job_id, engagement_id, req.model_dump())
    except ApplicationWorkspaceNotFound as exc:
        raise HTTPException(status_code=404, detail="Application workspace not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="CareerTrellis could not save the application workspace.",
        ) from exc


@app.delete("/api/jobs/{job_id}/engagements/{engagement_id}")
def remove_job_engagement(job_id: int, engagement_id: int) -> dict:
    """Delete one explicitly selected local tracking record."""
    try:
        runtime_application_workspace_repository(
            sqlite_connection_factory=get_db_connection
        ).delete_engagement(job_id, engagement_id)
        return {"success": True, "deleted": engagement_id}
    except ApplicationWorkspaceNotFound as exc:
        raise HTTPException(status_code=404, detail="Application workspace not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="CareerTrellis could not update the application workspace.",
        ) from exc


@app.get("/api/applications")
def get_applications() -> list[dict]:
    """Return bounded application records through the configured data store."""
    try:
        return runtime_application_read_repository(
            sqlite_connection_factory=get_db_connection
        ).list_applications()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="CareerTrellis could not load application records.",
        ) from exc


@app.get("/api/application-insights")
def get_application_insights() -> dict:
    """Return owner-scoped application funnel and response-rate aggregates."""
    try:
        return runtime_application_read_repository(
            sqlite_connection_factory=get_db_connection
        ).application_insights()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="CareerTrellis could not load application insights.",
        ) from exc

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
