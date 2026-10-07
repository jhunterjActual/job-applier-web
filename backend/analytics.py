"""Privacy-preserving, optional PostHog product analytics."""

import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import config


EVENT_SCHEMAS = {
    "job_search_started": frozenset({"result", "source_category"}),
    "resume_tailored": frozenset({"result", "source_category", "duration_bucket"}),
    "manual_application_opened": frozenset({"result", "source_category", "from_status"}),
    "material_downloaded": frozenset({"result", "source_category", "material_type"}),
    "job_lifecycle_updated": frozenset(
        {"result", "source_category", "from_status", "to_status"}
    ),
}
RESULT_VALUES = frozenset({"success", "failure"})
SOURCE_CATEGORY_VALUES = frozenset(
    {
        "manual",
        "saved_search",
        "automation",
        "greenhouse",
        "lever",
        "ashby",
        "smartrecruiters",
        "weworkremotely",
        "jobicy",
        "unknown",
        "runtime_verification",
    }
)
LIFECYCLE_VALUES = frozenset(
    {
        "matched",
        "tailored",
        "form_filled",
        "submitted",
        "applied",
        "interview",
        "offer",
        "rejected",
        "withdrawn",
        "closed",
    }
)
DURATION_BUCKET_VALUES = frozenset({"under_1s", "1_to_5s", "5_to_30s", "30s_plus"})
MATERIAL_TYPE_VALUES = frozenset({"resume", "cover_letter"})
SDK_PROPERTY_ALLOWLIST = frozenset(
    {"$lib", "$lib_version", "$geoip_disable", "$is_server", "$process_person_profile"}
)
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_STOP = object()


def duration_bucket(elapsed_seconds: float) -> str:
    """Return a controlled, low-cardinality duration bucket."""
    if elapsed_seconds < 1:
        return "under_1s"
    if elapsed_seconds < 5:
        return "1_to_5s"
    if elapsed_seconds < 30:
        return "5_to_30s"
    return "30s_plus"


def source_category(value: object) -> str:
    """Reduce a stored provider value to an approved analytics category."""
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in SOURCE_CATEGORY_VALUES else "unknown"


def _valid_installation_id(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return parsed.version == 4 and str(parsed) == value.lower()


def load_or_create_installation_id(path: Path) -> str:
    """Load a persisted random installation UUID, creating it without overwriting files."""
    path = Path(path)
    try:
        existing = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        if not _valid_installation_id(existing):
            raise ValueError("The analytics installation identity file is invalid.")
        return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    installation_id = str(uuid.uuid4())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        persisted = path.read_text(encoding="ascii").strip()
        if not _valid_installation_id(persisted):
            raise ValueError("The analytics installation identity file is invalid.")
        return persisted
    with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as identity_file:
        identity_file.write(installation_id + "\n")
    return installation_id


def _properties_are_valid(event: str, properties: dict) -> bool:
    schema = EVENT_SCHEMAS.get(event)
    if schema is None or set(properties) != schema | {"application_version"}:
        return False
    if properties.get("result") not in RESULT_VALUES:
        return False
    if properties.get("source_category") not in SOURCE_CATEGORY_VALUES:
        return False
    if "from_status" in schema and properties.get("from_status") not in LIFECYCLE_VALUES:
        return False
    if "to_status" in schema and properties.get("to_status") not in LIFECYCLE_VALUES:
        return False
    if "duration_bucket" in schema and properties.get("duration_bucket") not in DURATION_BUCKET_VALUES:
        return False
    if "material_type" in schema and properties.get("material_type") not in MATERIAL_TYPE_VALUES:
        return False
    version = properties.get("application_version")
    return isinstance(version, str) and bool(_VERSION_PATTERN.fullmatch(version))


def _filter_final_posthog_message(message: dict) -> Optional[dict]:
    """Apply a final allowlist after SDK-added context and properties."""
    event = message.get("event")
    distinct_id = message.get("distinct_id")
    properties = message.get("properties")
    if not isinstance(properties, dict) or not _valid_installation_id(distinct_id):
        return None
    custom_properties = {key: value for key, value in properties.items() if not key.startswith("$")}
    if not _properties_are_valid(event, custom_properties):
        return None
    message["properties"] = {
        key: value
        for key, value in properties.items()
        if key in custom_properties or key in SDK_PROPERTY_ALLOWLIST
    }
    return message


def _default_client_factory(**kwargs):
    from posthog import Posthog

    return Posthog(**kwargs)


class AnalyticsService:
    """Enqueue allowlisted events so analytics never runs on a request thread."""

    def __init__(
        self,
        token: str,
        host: str,
        application_version: str,
        identity_path: Path,
        client_factory: Callable = _default_client_factory,
        queue_size: int = 128,
    ) -> None:
        self.client = None
        self.installation_id = None
        self._queue = queue.Queue(maxsize=queue_size)
        self._stopping = threading.Event()
        self._worker = None
        self._application_version = application_version

        if not (token or "").strip():
            return
        if not _VERSION_PATTERN.fullmatch(application_version):
            return
        try:
            installation_id = load_or_create_installation_id(identity_path)
            client = client_factory(
                project_api_key=token.strip(),
                host=(host or config.DEFAULT_POSTHOG_HOST).strip(),
                sync_mode=True,
                timeout=1.0,
                max_retries=0,
                disable_geoip=True,
                enable_exception_autocapture=False,
                capture_exception_code_variables=False,
                before_send=_filter_final_posthog_message,
            )
        except Exception:
            return

        self.installation_id = installation_id
        self.client = client
        self._worker = threading.Thread(
            target=self._send_events,
            name="jobapplier-analytics",
            daemon=True,
        )
        self._worker.start()

    @property
    def enabled(self) -> bool:
        return self.client is not None and self.installation_id is not None

    def capture(self, event: str, properties: dict) -> bool:
        """Queue a valid event immediately; silently drop invalid/full/stopping events."""
        if not self.enabled or self._stopping.is_set() or not isinstance(properties, dict):
            return False
        complete_properties = {**properties, "application_version": self._application_version}
        if not _properties_are_valid(event, complete_properties):
            return False
        complete_properties["$process_person_profile"] = False
        try:
            self._queue.put_nowait((event, complete_properties))
        except queue.Full:
            return False
        return True

    def _send_events(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.05)
            except queue.Empty:
                if self._stopping.is_set():
                    return
                continue
            try:
                if item is _STOP:
                    return
                event, properties = item
                try:
                    self.client.capture(
                        event,
                        distinct_id=self.installation_id,
                        properties=properties,
                    )
                except Exception:
                    pass
            finally:
                self._queue.task_done()

    def shutdown(self, timeout_seconds: float = 0.5) -> bool:
        """Stop and flush within a strict caller-provided time budget."""
        if not self.enabled:
            return True
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        self._stopping.set()
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            pass

        remaining = max(0.0, deadline - time.monotonic())
        self._worker.join(remaining)
        if self._worker.is_alive():
            return False

        def close_client() -> None:
            try:
                self.client.flush(timeout_seconds=max(0.0, deadline - time.monotonic()))
                self.client.shutdown()
            except Exception:
                pass

        closer = threading.Thread(target=close_client, name="jobapplier-analytics-close", daemon=True)
        closer.start()
        closer.join(max(0.0, deadline - time.monotonic()))
        return not closer.is_alive()


_analytics_service: Optional[AnalyticsService] = None


def initialize_analytics(application_version: str) -> Optional[AnalyticsService]:
    """Initialize optional analytics from environment variables without raising."""
    global _analytics_service
    token = os.environ.get("POSTHOG_PROJECT_TOKEN", "")
    if not token.strip():
        _analytics_service = None
        return None
    service = AnalyticsService(
        token=token,
        host=os.environ.get("POSTHOG_HOST", config.DEFAULT_POSTHOG_HOST),
        application_version=application_version,
        identity_path=config.ANALYTICS_INSTALLATION_ID_PATH,
    )
    _analytics_service = service if service.enabled else None
    return _analytics_service


def capture_event(event: str, properties: dict) -> bool:
    """Capture through the optional process service and never raise to application code."""
    try:
        return bool(_analytics_service and _analytics_service.capture(event, properties))
    except Exception:
        return False


def shutdown_analytics(timeout_seconds: float = 0.5) -> bool:
    """Bound analytics shutdown and clear the process service."""
    global _analytics_service
    service = _analytics_service
    _analytics_service = None
    try:
        return service is None or service.shutdown(timeout_seconds)
    except Exception:
        return False
