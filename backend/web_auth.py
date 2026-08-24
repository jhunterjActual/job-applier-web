import os
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from workos import WorkOSClient
from workos.session import seal_session_from_auth_response

import config


AUTH_MODE_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_AUTH_MODE"
SESSION_COOKIE_NAME = "__Host-careertrellis_session"
LOGIN_FLOW_COOKIE_NAME = "__Host-careertrellis_auth_flow"
LOGIN_FLOW_MAX_AGE_SECONDS = 10 * 60
SESSION_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
WORKOS_USER_ID_PATTERN = re.compile(r"^user_[A-Za-z0-9_]{6,120}$")


class AuthenticationConfigurationError(config.RuntimeConfigurationError):
    """The web authentication boundary is unsafe or incomplete."""


class LoginFlowError(ValueError):
    """The authorization callback did not match a recent browser login flow."""


class IdentityNotAuthorizedError(PermissionError):
    """The authenticated WorkOS identity cannot access this deployment."""


@dataclass(frozen=True)
class WorkOSAuthConfiguration:
    api_key: str
    client_id: str
    cookie_password: str
    redirect_uri: str
    logout_return_to: str
    owner_workos_user_id: str


@dataclass(frozen=True)
class PendingLogin:
    authorization_url: str
    sealed_flow: str


@dataclass(frozen=True)
class CompletedLogin:
    workos_user_id: str
    sealed_session: str


@dataclass(frozen=True)
class SessionAuthentication:
    status: Literal["authenticated", "unauthenticated", "unavailable"]
    workos_user_id: str | None = None
    session_id: str | None = None
    refreshed_session: str | None = None
    clear_cookie: bool = False


def _required_environment_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.lower().startswith("replace-"):
        raise AuthenticationConfigurationError(f"Shared-host authentication requires {name}.")
    return value


def _validate_public_url(name: str, value: str, expected_path: str) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port or 443
    except ValueError as exc:
        raise AuthenticationConfigurationError(f"{name} must be a valid HTTPS URL.") from exc
    allowed_hosts = config.get_allowed_public_hosts()
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in allowed_hosts
        or port != 443
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
    ):
        raise AuthenticationConfigurationError(
            f"{name} must be an exact HTTPS URL on a configured public host with path {expected_path}."
        )


def get_auth_configuration() -> WorkOSAuthConfiguration | None:
    """Return WorkOS settings, requiring them for every shared-host web process."""
    environment = config.get_runtime_environment()
    mode = os.environ.get(AUTH_MODE_ENVIRONMENT_VARIABLE, "").strip().lower()
    if environment == "local":
        if mode not in {"", "local"}:
            raise AuthenticationConfigurationError(
                "CAREERTRELLIS_AUTH_MODE must be unset or local in the loopback-only runtime."
            )
        return None
    if environment == "production":
        raise AuthenticationConfigurationError(
            "The owner-only Stage 2 boundary is restricted to staging; production requires completed tenant isolation."
        )
    if mode != "workos":
        raise AuthenticationConfigurationError(
            "Shared-host web processes require CAREERTRELLIS_AUTH_MODE=workos."
        )

    api_key = _required_environment_value("WORKOS_API_KEY")
    client_id = _required_environment_value("WORKOS_CLIENT_ID")
    cookie_password = _required_environment_value("WORKOS_COOKIE_PASSWORD")
    redirect_uri = _required_environment_value("WORKOS_REDIRECT_URI")
    logout_return_to = _required_environment_value("WORKOS_LOGOUT_RETURN_TO")
    owner_workos_user_id = _required_environment_value(
        "CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID"
    )

    if len(api_key) < 20:
        raise AuthenticationConfigurationError("WORKOS_API_KEY is not a valid secret value.")
    if not client_id.startswith("client_") or len(client_id) > 160:
        raise AuthenticationConfigurationError("WORKOS_CLIENT_ID is not a valid WorkOS client ID.")
    try:
        Fernet(cookie_password.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise AuthenticationConfigurationError(
            "WORKOS_COOKIE_PASSWORD must be a URL-safe base64 Fernet key generated from 32 random bytes."
        ) from exc
    if not WORKOS_USER_ID_PATTERN.fullmatch(owner_workos_user_id):
        raise AuthenticationConfigurationError(
            "CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID is not a valid immutable WorkOS user ID."
        )
    _validate_public_url("WORKOS_REDIRECT_URI", redirect_uri, "/auth/callback")
    _validate_public_url("WORKOS_LOGOUT_RETURN_TO", logout_return_to, "/")

    return WorkOSAuthConfiguration(
        api_key=api_key,
        client_id=client_id,
        cookie_password=cookie_password,
        redirect_uri=redirect_uri,
        logout_return_to=logout_return_to,
        owner_workos_user_id=owner_workos_user_id,
    )


def _client(settings: WorkOSAuthConfiguration) -> WorkOSClient:
    return WorkOSClient(api_key=settings.api_key, client_id=settings.client_id)


def begin_login(
    settings: WorkOSAuthConfiguration,
    *,
    client: WorkOSClient | None = None,
) -> PendingLogin:
    """Start an AuthKit authorization-code flow with SDK-generated PKCE values."""
    workos_client = client or _client(settings)
    authorization = workos_client.user_management.get_authorization_url_with_pkce(
        redirect_uri=settings.redirect_uri,
        provider="authkit",
        prompt="login",
    )
    flow_data = (
        f"{authorization['state']}\n{authorization['code_verifier']}".encode("utf-8")
    )
    sealed_flow = Fernet(settings.cookie_password.encode("ascii")).encrypt(flow_data).decode("ascii")
    return PendingLogin(authorization_url=authorization["url"], sealed_flow=sealed_flow)


def _open_login_flow(settings: WorkOSAuthConfiguration, sealed_flow: str) -> tuple[str, str]:
    try:
        flow_data = Fernet(settings.cookie_password.encode("ascii")).decrypt(
            sealed_flow.encode("ascii"),
            ttl=LOGIN_FLOW_MAX_AGE_SECONDS,
        )
        expected_state, code_verifier = flow_data.decode("utf-8").split("\n", 1)
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise LoginFlowError("The login attempt expired or is invalid.") from exc
    if not expected_state or not code_verifier:
        raise LoginFlowError("The login attempt is invalid.")
    return expected_state, code_verifier


def _verified_user_id(
    user: Any,
    impersonator: Any = None,
) -> str:
    if impersonator is not None:
        raise IdentityNotAuthorizedError("Impersonated sessions are not permitted.")
    if isinstance(user, dict):
        workos_user_id = str(user.get("id", ""))
        email_verified = user.get("email_verified") is True
    else:
        workos_user_id = str(getattr(user, "id", ""))
        email_verified = getattr(user, "email_verified", False) is True
    if not email_verified or not WORKOS_USER_ID_PATTERN.fullmatch(workos_user_id):
        raise IdentityNotAuthorizedError("This identity is not authorized for staging.")
    return workos_user_id


def hmac_safe_equal(left: str, right: str) -> bool:
    """Compare bounded identity strings without early-exit behavior."""
    import hmac

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def complete_login(
    settings: WorkOSAuthConfiguration,
    *,
    code: str,
    state: str,
    sealed_flow: str,
    client: WorkOSClient | None = None,
) -> CompletedLogin:
    """Verify the browser flow, exchange its one-time code, and seal the server session."""
    expected_state, code_verifier = _open_login_flow(settings, sealed_flow)
    if not hmac_safe_equal(state, expected_state):
        raise LoginFlowError("The login state did not match.")
    workos_client = client or _client(settings)
    result = workos_client.user_management.authenticate_with_code_pkce(
        code=code,
        code_verifier=code_verifier,
    )
    workos_user_id = _verified_user_id(result.user, result.impersonator)
    sealed_session = seal_session_from_auth_response(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        user=result.user.to_dict(),
        impersonator=result.impersonator.to_dict() if result.impersonator is not None else None,
        cookie_password=settings.cookie_password,
    )
    return CompletedLogin(workos_user_id=workos_user_id, sealed_session=sealed_session)


def authenticate_session(
    settings: WorkOSAuthConfiguration,
    sealed_session: str,
    *,
    client: WorkOSClient | None = None,
) -> SessionAuthentication:
    """Validate a sealed cookie, refreshing only an expired/invalid access token."""
    if not sealed_session:
        return SessionAuthentication(status="unauthenticated")
    workos_client = client or _client(settings)
    session = workos_client.user_management.load_sealed_session(
        session_data=sealed_session,
        cookie_password=settings.cookie_password,
    )
    result = session.authenticate()
    if result.authenticated:
        try:
            workos_user_id = _verified_user_id(result.user, result.impersonator)
        except IdentityNotAuthorizedError:
            return SessionAuthentication(status="unauthenticated", clear_cookie=True)
        return SessionAuthentication(
            status="authenticated",
            workos_user_id=workos_user_id,
            session_id=result.session_id,
        )

    reason = getattr(result.reason, "value", result.reason)
    if reason != "invalid_jwt":
        return SessionAuthentication(status="unauthenticated", clear_cookie=True)

    refreshed = session.refresh()
    if not refreshed.authenticated:
        refresh_reason = getattr(refreshed.reason, "value", refreshed.reason)
        terminal_refresh_reasons = {
            "invalid_session_cookie",
            "no_session_cookie_provided",
            "refresh_denied",
            "mfa_challenge_required",
            "mfa_enrollment_required",
            "sso_required",
            "email_verification_required",
            "organization_selection_required",
            "organization_auth_methods_required",
            "authentication_method_not_allowed",
            "radar_challenge_required",
        }
        if refresh_reason not in terminal_refresh_reasons:
            return SessionAuthentication(status="unavailable")
        return SessionAuthentication(status="unauthenticated", clear_cookie=True)
    try:
        workos_user_id = _verified_user_id(refreshed.user, refreshed.impersonator)
    except IdentityNotAuthorizedError:
        return SessionAuthentication(status="unauthenticated", clear_cookie=True)
    return SessionAuthentication(
        status="authenticated",
        workos_user_id=workos_user_id,
        session_id=refreshed.session_id,
        refreshed_session=refreshed.sealed_session,
    )


def logout_url(settings: WorkOSAuthConfiguration, session_id: str) -> str:
    """Build the WorkOS logout endpoint for a previously authenticated session."""
    return _client(settings).user_management.get_logout_url(
        session_id=session_id,
        return_to=settings.logout_return_to,
    )
