import os
from pathlib import Path


class RuntimeConfigurationError(RuntimeError):
    """The process environment is unsafe or incomplete for this runtime mode."""


RUNTIME_ENVIRONMENTS = frozenset({"local", "staging", "production"})
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MINIMUM_PROXY_TOKEN_CHARACTERS = 32

# Paths
BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR.parent
DATA_DIR = Path(os.environ.get("CAREERTRELLIS_DATA_DIR", WORKSPACE_DIR / "data")).expanduser().resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
DB_PATH = DATA_DIR / "jobapplier.db"
ANALYTICS_INSTALLATION_ID_PATH = DATA_DIR / ".analytics-installation-id"
DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"

# Ensure folders exist
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


def validate_runtime_configuration() -> dict[str, object]:
    """Fail closed when a shared-host deployment omits its origin protections."""
    environment = get_runtime_environment()
    public_hosts = get_allowed_public_hosts()
    proxy_token = get_trusted_proxy_token()
    get_worker_heartbeat_seconds()

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
