import os
from pathlib import Path


class RuntimeConfigurationError(RuntimeError):
    """The process environment is unsafe or incomplete for this runtime mode."""


RUNTIME_ENVIRONMENTS = frozenset({"local", "staging", "production"})
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MINIMUM_PROXY_TOKEN_CHARACTERS = 32


def is_web_admission_setting(name: str) -> bool:
    """Reserve the whole v2 namespace, including future/unknown settings."""
    return name == "CAREERTRELLIS_ADMISSION_DATABASE_URL" or name.startswith("CAREERTRELLIS_AUTH_V2_")


def _worker_has_web_admission_settings() -> bool:
    # Presence is forbidden, even blank or ENABLED=0: a worker must never be
    # configured as a web process. Keep the older guards' blank semantics.
    return any(is_web_admission_setting(name) for name in os.environ)
WORKER_FORBIDDEN_ENVIRONMENT_VARIABLES = frozenset({
    "CAREERTRELLIS_ALLOWED_HOSTS",
    "CAREERTRELLIS_TRUSTED_PROXY_TOKEN",
    "CAREERTRELLIS_AUTH_MODE",
    "CAREERTRELLIS_IDENTITY_STORE",
    "CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID",
    "CAREERTRELLIS_MIGRATION_DATABASE_URL",
    "CAREERTRELLIS_OBJECT_STORE",
    "CAREERTRELLIS_OBJECT_STORE_ACCESS_KEY_ID",
    "CAREERTRELLIS_OBJECT_STORE_ACTIVE_KEY_ID",
    "CAREERTRELLIS_OBJECT_STORE_BUCKET",
    "CAREERTRELLIS_OBJECT_STORE_ENDPOINT",
    "CAREERTRELLIS_OBJECT_STORE_KEYRING_B64",
    "CAREERTRELLIS_OBJECT_STORE_SECRET_ACCESS_KEY",
    "WORKOS_API_KEY",
    "WORKOS_CLIENT_ID",
    "WORKOS_COOKIE_PASSWORD",
    "WORKOS_REDIRECT_URI",
    "WORKOS_LOGOUT_RETURN_TO",
})
ACCOUNT_ERASURE_WORKER_FORBIDDEN_ENVIRONMENT_VARIABLES = frozenset({
    "CAREERTRELLIS_ALLOWED_HOSTS",
    "CAREERTRELLIS_TRUSTED_PROXY_TOKEN",
    "CAREERTRELLIS_AUTH_MODE",
    "CAREERTRELLIS_IDENTITY_STORE",
    "CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID",
    "CAREERTRELLIS_MIGRATION_DATABASE_URL",
    "CAREERTRELLIS_SEARCH_WORKER_ENABLED",
    "CAREERTRELLIS_WORKER_HEARTBEAT_SECONDS",
    "CAREERTRELLIS_OBJECT_STORE_ACTIVE_KEY_ID",
    "CAREERTRELLIS_OBJECT_STORE_KEYRING_B64",
    "CAREERTRELLIS_PROVIDER_SECRET_STORE",
    "CAREERTRELLIS_PROVIDER_SECRET_ACTIVE_KEY_ID",
    "CAREERTRELLIS_PROVIDER_SECRET_KEYRING_B64",
    "PLAYWRIGHT_BROWSERS_PATH",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_MAPS_API_KEY",
    "CAREERTRELLIS_SERPER_API_KEY",
    "WORKOS_API_KEY",
    "WORKOS_CLIENT_ID",
    "WORKOS_COOKIE_PASSWORD",
    "WORKOS_REDIRECT_URI",
    "WORKOS_LOGOUT_RETURN_TO",
})

# Paths
BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR.parent
DATA_DIR = Path(os.environ.get("CAREERTRELLIS_DATA_DIR", WORKSPACE_DIR / "data")).expanduser().resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
DB_PATH = DATA_DIR / "jobapplier.db"
ANALYTICS_INSTALLATION_ID_PATH = DATA_DIR / ".analytics-installation-id"
DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"

# The offline operator probe needs configuration validators, not runtime storage.
# Normal application/worker startup retains the existing directory preparation.
if os.environ.get("CAREERTRELLIS_CONFIG_READ_ONLY") != "1":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_runtime_environment() -> str:
    """Return the validated deployment environment name."""
    environment = os.environ.get("CAREERTRELLIS_ENVIRONMENT", "local").strip().lower()
    if environment not in RUNTIME_ENVIRONMENTS:
        raise RuntimeConfigurationError(
            "CAREERTRELLIS_ENVIRONMENT must be local, staging, or production."
        )
    return environment


def get_discovery_mode() -> str:
    mode = os.environ.get("CAREERTRELLIS_DISCOVERY_MODE", "legacy").strip()
    if mode not in {"legacy", "hybrid_feeds"}:
        raise RuntimeConfigurationError("Discovery mode is invalid.")
    return mode


def get_serper_api_key() -> str:
    # Operator suspended Serper. Environment configuration cannot re-enable it.
    return ""


def get_allowed_public_hosts() -> frozenset[str]:
    """Return normalized DNS hostnames explicitly allowed outside loopback."""
    hosts: set[str] = set()
    for value in os.environ.get("CAREERTRELLIS_ALLOWED_HOSTS", "").split(","):
        hostname = value.strip().lower().rstrip(".")
        if not hostname:
            continue
        if (
            hostname in LOCAL_HOSTS
            or any(character in hostname for character in "/\\@:")
            or any(not (character.isalnum() or character in ".-") for character in hostname)
        ):
            raise RuntimeConfigurationError(
                "CAREERTRELLIS_ALLOWED_HOSTS must contain DNS hostnames without schemes, paths, or ports."
            )
        hosts.add(hostname)
    return frozenset(hosts)


def get_trusted_proxy_token() -> str:
    """Return the shared origin token injected by the loopback reverse proxy."""
    return os.environ.get("CAREERTRELLIS_TRUSTED_PROXY_TOKEN", "").strip()


def get_worker_heartbeat_seconds() -> int:
    """Return the bounded worker heartbeat interval."""
    raw_value = os.environ.get("CAREERTRELLIS_WORKER_HEARTBEAT_SECONDS", "15").strip()
    try:
        interval = int(raw_value)
    except ValueError as exc:
        raise RuntimeConfigurationError(
            "CAREERTRELLIS_WORKER_HEARTBEAT_SECONDS must be an integer from 5 through 300."
        ) from exc
    if interval < 5 or interval > 300:
        raise RuntimeConfigurationError(
            "CAREERTRELLIS_WORKER_HEARTBEAT_SECONDS must be an integer from 5 through 300."
        )
    return interval


def search_worker_enabled() -> bool:
    """Require an explicit reviewed switch before the durable worker consumes runs."""
    raw_value = os.environ.get("CAREERTRELLIS_SEARCH_WORKER_ENABLED", "false").strip().lower()
    if raw_value not in {"true", "false"}:
        raise RuntimeConfigurationError(
            "CAREERTRELLIS_SEARCH_WORKER_ENABLED must be true or false."
        )
    return raw_value == "true"


def account_erasure_worker_enabled() -> bool:
    """Require explicit activation before irreversible erasure work is claimed."""
    raw_value = os.environ.get(
        "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED", "false"
    ).strip().lower()
    if raw_value not in {"true", "false"}:
        raise RuntimeConfigurationError(
            "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED must be true or false."
        )
    return raw_value == "true"


def get_account_erasure_poll_seconds() -> int:
    """Return the bounded idle poll interval for the erasure worker."""
    raw_value = os.environ.get(
        "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_POLL_SECONDS", "15"
    ).strip()
    try:
        interval = int(raw_value)
    except ValueError as exc:
        raise RuntimeConfigurationError(
            "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_POLL_SECONDS must be an integer from 5 through 300."
        ) from exc
    if interval < 5 or interval > 300:
        raise RuntimeConfigurationError(
            "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_POLL_SECONDS must be an integer from 5 through 300."
        )
    return interval


def get_account_erasure_batch_size() -> int:
    """Return a bounded number of blob deletions for one durable claim."""
    raw_value = os.environ.get(
        "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_BATCH_SIZE", "10"
    ).strip()
    try:
        size = int(raw_value)
    except ValueError as exc:
        raise RuntimeConfigurationError(
            "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_BATCH_SIZE must be an integer from 1 through 500."
        ) from exc
    if size < 1 or size > 500:
        raise RuntimeConfigurationError(
            "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_BATCH_SIZE must be an integer from 1 through 500."
        )
    return size


def validate_account_erasure_worker_configuration() -> dict[str, object]:
    """Validate only settings required by the delete-only erasure process."""
    if _worker_has_web_admission_settings() or any(
        os.environ.get(name, "").strip()
        for name in ACCOUNT_ERASURE_WORKER_FORBIDDEN_ENVIRONMENT_VARIABLES
    ):
        raise RuntimeConfigurationError(
            "The account-erasure worker environment contains a credential or setting outside its delete-only boundary."
        )
    environment = get_runtime_environment()
    if environment == "local":
        raise RuntimeConfigurationError(
            "The account-erasure worker requires a shared-host environment."
        )
    enabled = account_erasure_worker_enabled()
    get_account_erasure_poll_seconds()
    get_account_erasure_batch_size()
    if enabled and os.environ.get("CAREERTRELLIS_OBJECT_STORE", "").strip().lower() != "r2_encrypted":
        raise RuntimeConfigurationError(
            "The account-erasure worker requires CAREERTRELLIS_OBJECT_STORE=r2_encrypted."
        )
    if enabled:
        try:
            from postgres_database import database_url_from_environment

            database_url = database_url_from_environment()
        except Exception as exc:
            raise RuntimeConfigurationError(
                "The account-erasure worker requires its dedicated PostgreSQL role."
            ) from exc
        if database_url.username != "careertrellis_erasure_worker":
            raise RuntimeConfigurationError(
                "The account-erasure worker requires its dedicated PostgreSQL role."
            )
    return {"environment": environment, "enabled": enabled}


def validate_worker_runtime_configuration() -> dict[str, object]:
    """Validate only worker-owned settings, never web host or auth secrets."""
    if _worker_has_web_admission_settings() or any(os.environ.get(name, "").strip() for name in WORKER_FORBIDDEN_ENVIRONMENT_VARIABLES):
        raise RuntimeConfigurationError(
            "The search worker environment contains a credential or setting reserved for the web, migration, or object-storage runtime."
        )
    environment = get_runtime_environment()
    get_worker_heartbeat_seconds()
    search_worker_enabled()
    return {
        "environment": environment,
        "data_directory": str(DATA_DIR),
    }


def validate_runtime_configuration() -> dict[str, object]:
    """Fail closed when a shared-host deployment omits its origin protections."""
    environment = get_runtime_environment()
    public_hosts = get_allowed_public_hosts()
    proxy_token = get_trusted_proxy_token()
    get_worker_heartbeat_seconds()
    search_worker_enabled()

    if environment == "local":
        if public_hosts:
            raise RuntimeConfigurationError(
                "CAREERTRELLIS_ALLOWED_HOSTS cannot expose hosts while CAREERTRELLIS_ENVIRONMENT is local."
            )
    else:
        if not public_hosts:
            raise RuntimeConfigurationError(
                "Shared-host environments require at least one CAREERTRELLIS_ALLOWED_HOSTS entry."
            )
        if (
            len(proxy_token) < MINIMUM_PROXY_TOKEN_CHARACTERS
            or proxy_token.lower().startswith("replace-")
        ):
            raise RuntimeConfigurationError(
                f"Shared-host environments require a CAREERTRELLIS_TRUSTED_PROXY_TOKEN of at least {MINIMUM_PROXY_TOKEN_CHARACTERS} characters."
            )

    return {
        "environment": environment,
        "public_hosts": sorted(public_hosts),
        "data_directory": str(DATA_DIR),
    }

# API Keys
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
NOMINATIM_BASE_URL = os.environ.get("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org")

def get_gemini_api_key() -> str:
    """
    Retrieve the Gemini API key from environment variables or global configuration.
    
    Returns:
        str: The Gemini API key, or empty string if not configured.
    """
    return os.environ.get("GEMINI_API_KEY") or GEMINI_API_KEY


def get_openai_api_key() -> str:
    """Return the optional OpenAI API key without exposing it to the browser."""
    return os.environ.get("OPENAI_API_KEY") or OPENAI_API_KEY

def get_google_maps_api_key() -> str:
    """
    Retrieve the Google Maps/Places API key from environment variables or global configuration.
    
    Returns:
        str: The Google Maps API key, or empty string if not configured.
    """
    return os.environ.get("GOOGLE_MAPS_API_KEY") or GOOGLE_MAPS_API_KEY


def get_nominatim_base_url() -> str:
    """Return the configurable Nominatim endpoint for public or self-hosted use."""
    return os.environ.get("NOMINATIM_BASE_URL") or NOMINATIM_BASE_URL
