"""Root-operated, privacy-safe two-user pilot acceptance core.

This module deliberately does not drive WorkOS, a browser, a paid provider, or
a worker claim.  It qualifies the live PostgreSQL authorization boundary with
small synthetic fixtures, performs a two-object encrypted R2 smoke, and checks
the exact-build worker heartbeats.  The installed shell boundary is responsible
for immutable-release, authority, process, and host-lock checks before invoking
this module.

No external identity is accepted through argv or the process environment.  The
configured owner is read from the protected staging environment and the pilot
identity is read from a non-echoed terminal prompt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from application_read_repository import PostgreSQLApplicationReadRepository
from base_resumes import BaseResumeNotFound
from data_export import EXPORT_FIELDS
from discovery_repository import PostgreSQLDiscoveryRepository
from postgres_database import (
    postgres_url,
    set_identity_context,
    set_tenant_context,
)
from profile_repository import PostgreSQLProfileRepository
from stored_objects import StoredObjectNotFound, download_stored_object
from user_data_export_repository import PostgreSQLUserDataExportRepository
from version import APP_BUILD

try:
    from pilot_acceptance_receipts import write_receipt
except ImportError:  # The receipt boundary may be added by the release installer.
    write_receipt = None


OPERATIONS = frozenset({
    "prepare", "verify-active", "verify-disabled", "verify-recovered",
    "record-browser", "inspect", "cleanup",
})
PHASES = frozenset({"preparing", "prepared", "automated_partial", "browser_recorded"})
STATE_SCHEMA = "careertrellis.pilot-acceptance-state/v1"
RECEIPT_SCHEMA = "careertrellis.pilot-auth-acceptance/v1"
FIXED_ERROR = "The pilot acceptance operation did not complete. Inspect before retrying."
FIXED_STATE_ROOT = Path("/var/lib/careertrellis/pilot-auth-acceptance")
FIXED_RECEIPT_ROOT = Path("/var/backups/careertrellis/pilot-auth-acceptance")
STAGING_ENVIRONMENT_FILE = Path("/etc/careertrellis/staging.env")
MIGRATION_ENVIRONMENT_FILE = Path("/etc/careertrellis/postgres-migration.env")
RUNTIME_ENVIRONMENT_FILE = Path("/etc/careertrellis/postgres-runtime.env")
PROVIDER_ENVIRONMENT_FILE = Path("/etc/careertrellis/provider-secrets.env")
OBJECT_ENVIRONMENT_FILE = Path("/etc/careertrellis/object-storage.env")
SEARCH_HEARTBEAT_FILE = Path("/var/lib/careertrellis/worker/worker-heartbeat.json")
ERASURE_HEARTBEAT_FILE = Path(
    "/var/lib/careertrellis/account-erasure/account-erasure-worker-heartbeat.json"
)
CURRENT_RELEASE_LINK = Path("/opt/careertrellis/staging/current")
RELEASE_ROOT = Path("/opt/careertrellis/staging/releases")
RUNTIME_ENVIRONMENT_KEY = "CAREERTRELLIS_ENVIRONMENT"
RELEASE_ENVIRONMENT_KEY = "CAREERTRELLIS_PILOT_ACCEPTANCE_RELEASE"
SEARCH_WORKER_PID_ENVIRONMENT_KEY = (
    "CAREERTRELLIS_PILOT_ACCEPTANCE_SEARCH_WORKER_PID"
)
ERASURE_WORKER_PID_ENVIRONMENT_KEY = (
    "CAREERTRELLIS_PILOT_ACCEPTANCE_ERASURE_WORKER_PID"
)
SEARCH_HEARTBEAT_UID_ENVIRONMENT_KEY = (
    "CAREERTRELLIS_PILOT_ACCEPTANCE_SEARCH_HEARTBEAT_UID"
)
SEARCH_HEARTBEAT_GID_ENVIRONMENT_KEY = (
    "CAREERTRELLIS_PILOT_ACCEPTANCE_SEARCH_HEARTBEAT_GID"
)
SEARCH_HEARTBEAT_MODE_ENVIRONMENT_KEY = (
    "CAREERTRELLIS_PILOT_ACCEPTANCE_SEARCH_HEARTBEAT_MODE"
)
ERASURE_HEARTBEAT_UID_ENVIRONMENT_KEY = (
    "CAREERTRELLIS_PILOT_ACCEPTANCE_ERASURE_HEARTBEAT_UID"
)
ERASURE_HEARTBEAT_GID_ENVIRONMENT_KEY = (
    "CAREERTRELLIS_PILOT_ACCEPTANCE_ERASURE_HEARTBEAT_GID"
)
ERASURE_HEARTBEAT_MODE_ENVIRONMENT_KEY = (
    "CAREERTRELLIS_PILOT_ACCEPTANCE_ERASURE_HEARTBEAT_MODE"
)
SEARCH_WORKER_USER = "careertrellis-worker"
ERASURE_WORKER_USER = "careertrellis-erasure-worker"
WORKER_HEARTBEAT_MODE = 0o600
OWNER_ID_KEY = "CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID"
MIGRATION_URL_KEY = "CAREERTRELLIS_MIGRATION_DATABASE_URL"
RUNTIME_URL_KEY = "CAREERTRELLIS_DATABASE_URL"
PROVIDER_KEYS = (
    "CAREERTRELLIS_PROVIDER_SECRET_STORE",
    "CAREERTRELLIS_PROVIDER_SECRET_ACTIVE_KEY_ID",
    "CAREERTRELLIS_PROVIDER_SECRET_KEYRING_B64",
)
PROVIDER_NAMES = ("gemini", "openai", "google_maps")
OBJECT_KEYS = (
    "CAREERTRELLIS_OBJECT_STORE",
    "CAREERTRELLIS_OBJECT_STORE_ENDPOINT",
    "CAREERTRELLIS_OBJECT_STORE_BUCKET",
    "CAREERTRELLIS_OBJECT_STORE_ACCESS_KEY_ID",
    "CAREERTRELLIS_OBJECT_STORE_SECRET_ACCESS_KEY",
    "CAREERTRELLIS_OBJECT_STORE_ACTIVE_KEY_ID",
    "CAREERTRELLIS_OBJECT_STORE_KEYRING_B64",
)
WORKOS_USER_ID_PATTERN = re.compile(r"^user_[A-Za-z0-9_]{6,120}$")
RELEASE_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ATTEMPT_PATTERN = re.compile(r"^[0-9a-f]{32}$")
MARKER_PATTERN = re.compile(r"^pilot-acceptance-[0-9a-f]{32}$")
PROTECTED_TABLES = (
    "profiles",
    "provider_secrets",
    "stored_objects",
    "base_resumes",
    "base_resume_versions",
    "jobs",
    "saved_searches",
    "applications",
    "application_status_history",
    "application_engagements",
    "job_suppressions",
    "source_diagnostics",
    "headquarters_cache",
    "search_runs",
)
BROKER_TABLES = (
    "search_run_claim_queue",
    "scheduled_search_dispatch_queue",
    "account_erasure_claim_queue",
)
BROWSER_CHECKS = (
    "owner_login_logout_recovery",
    "pilot_login_logout_recovery",
    "pilot_dashboard_isolated",
    "unlisted_denied",
    "disabled_denied",
    "pilot_recovered_after_reinvite",
    "privacy_logs_clean",
)
AUTOMATED_CHECKS = (
    "mapping_boundary",
    "pilot_initially_empty",
    "missing_context_empty",
    "pooled_context_cleared",
    "profile_resume_isolated",
    "job_isolated",
    "application_isolated",
    "export_isolated",
    "cross_write_blocked",
    "cross_relate_blocked",
    "rollback_only_erasure",
    "provider_secret_isolated",
    "provider_network_called",
    "object_storage_live",
    "worker_live",
)


class PilotAcceptanceError(RuntimeError):
    """One fixed-message acceptance-boundary failure."""


@dataclass(frozen=True)
class FixtureIds:
    owner_resume_id: int
    owner_resume_version_id: int
    owner_job_id: int
    owner_application_id: int
    pilot_profile_id: int
    pilot_resume_id: int
    pilot_resume_version_id: int
    pilot_job_id: int
    pilot_application_id: int
    owner_provider_secret_id: int
    pilot_provider_secret_id: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "FixtureIds":
        expected = tuple(cls.__dataclass_fields__)
        if set(value) != set(expected):
            raise PilotAcceptanceError(FIXED_ERROR)
        parsed: dict[str, int] = {}
        for name in expected:
            item = value[name]
            if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
                raise PilotAcceptanceError(FIXED_ERROR)
            parsed[name] = item
        return cls(**parsed)


@dataclass(frozen=True)
class MappingPhaseEvidence:
    disabled_verified: bool = False
    recovered_verified: bool = False

    @classmethod
    def from_mapping(cls, value: object) -> "MappingPhaseEvidence":
        if not isinstance(value, Mapping) or set(value) != {
            "disabled_verified", "recovered_verified"
        }:
            raise PilotAcceptanceError(FIXED_ERROR)
        disabled = value["disabled_verified"]
        recovered = value["recovered_verified"]
        if type(disabled) is not bool or type(recovered) is not bool:
            raise PilotAcceptanceError(FIXED_ERROR)
        if recovered and not disabled:
            raise PilotAcceptanceError(FIXED_ERROR)
        return cls(disabled_verified=disabled, recovered_verified=recovered)


@dataclass(frozen=True)
class AcceptanceState:
    schema: str
    attempt_id: str
    phase: str
    release: str
    build: str
    started_at: str
    marker: str
    provider: str
    owner_object_id: str
    pilot_object_id: str
    owner_user_id: int
    pilot_user_id: int
    fixture: FixtureIds | None = None
    automated_checks: dict[str, bool | str] = field(default_factory=dict)
    browser_checks: dict[str, bool] = field(default_factory=dict)
    mapping_evidence: MappingPhaseEvidence = field(
        default_factory=MappingPhaseEvidence
    )

    @classmethod
    def new(
        cls,
        *,
        release: str,
        build: str,
        owner_user_id: int,
        pilot_user_id: int,
        provider: str,
        owner_object_id: str | None = None,
        pilot_object_id: str | None = None,
    ) -> "AcceptanceState":
        attempt_id = uuid4().hex
        return cls(
            schema=STATE_SCHEMA,
            attempt_id=attempt_id,
            phase="preparing",
            release=_release(release),
            build=_build(build),
            started_at=_utc_now(),
            marker=f"pilot-acceptance-{attempt_id}",
            provider=_provider_name(provider),
            owner_object_id=_object_id(owner_object_id or str(uuid4())),
            pilot_object_id=_object_id(pilot_object_id or str(uuid4())),
            owner_user_id=_positive_id(owner_user_id),
            pilot_user_id=_positive_id(pilot_user_id),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "AcceptanceState":
        expected = {
            "schema", "attempt_id", "phase", "release", "build", "started_at",
            "marker", "provider", "owner_object_id", "pilot_object_id",
            "owner_user_id", "pilot_user_id", "fixture",
            "automated_checks", "browser_checks", "mapping_evidence",
        }
        if set(value) != expected or value.get("schema") != STATE_SCHEMA:
            raise PilotAcceptanceError(FIXED_ERROR)
        attempt_id = str(value["attempt_id"])
        marker = str(value["marker"])
        phase = str(value["phase"])
        if (
            not ATTEMPT_PATTERN.fullmatch(attempt_id)
            or marker != f"pilot-acceptance-{attempt_id}"
            or not MARKER_PATTERN.fullmatch(marker)
            or phase not in PHASES
        ):
            raise PilotAcceptanceError(FIXED_ERROR)
        automated = value["automated_checks"]
        browser = value["browser_checks"]
        if not isinstance(automated, dict) or not isinstance(browser, dict):
            raise PilotAcceptanceError(FIXED_ERROR)
        if not set(automated).issubset(AUTOMATED_CHECKS) or not set(browser).issubset(BROWSER_CHECKS):
            raise PilotAcceptanceError(FIXED_ERROR)
        if not all(item is True or item is False or item == "blocked" for item in automated.values()):
            raise PilotAcceptanceError(FIXED_ERROR)
        if not all(item is True or item is False for item in browser.values()):
            raise PilotAcceptanceError(FIXED_ERROR)
        fixture_value = value["fixture"]
        fixture = None
        if fixture_value is not None:
            if not isinstance(fixture_value, Mapping):
                raise PilotAcceptanceError(FIXED_ERROR)
            fixture = FixtureIds.from_mapping(fixture_value)
        return cls(
            schema=STATE_SCHEMA,
            attempt_id=attempt_id,
            phase=phase,
            release=_release(str(value["release"])),
            build=_build(str(value["build"])),
            started_at=_timestamp(str(value["started_at"])),
            marker=marker,
            provider=_provider_name(value["provider"]),
            owner_object_id=_object_id(str(value["owner_object_id"])),
            pilot_object_id=_object_id(str(value["pilot_object_id"])),
            owner_user_id=_positive_id(value["owner_user_id"]),
            pilot_user_id=_positive_id(value["pilot_user_id"]),
            fixture=fixture,
            automated_checks=dict(automated),
            browser_checks=dict(browser),
            mapping_evidence=MappingPhaseEvidence.from_mapping(
                value["mapping_evidence"]
            ),
        )

    def document(self) -> dict[str, object]:
        result = asdict(self)
        return result

    def transition(self, phase: str, **changes: object) -> "AcceptanceState":
        allowed = {
            "preparing": {"prepared"},
            "prepared": {"automated_partial"},
            "automated_partial": {"automated_partial", "browser_recorded"},
            "browser_recorded": {"browser_recorded"},
        }
        if phase not in allowed.get(self.phase, set()):
            raise PilotAcceptanceError(FIXED_ERROR)
        values = self.document()
        values.update(changes)
        values["phase"] = phase
        fixture = values.get("fixture")
        if isinstance(fixture, FixtureIds):
            values["fixture"] = asdict(fixture)
        return AcceptanceState.from_mapping(values)


@dataclass(frozen=True)
class OperationResult:
    operation: str
    result: str
    phase: str
    checks: dict[str, bool | str] = field(default_factory=dict)


class StateStore:
    """Atomic, no-symlink storage for transient cleanup authority."""

    def __init__(self, root: Path = FIXED_STATE_ROOT, *, strict_metadata: bool = True):
        self.root = Path(root)
        self.strict_metadata = strict_metadata
        self.path = self.root / "active.json"

    def _validate_root(self) -> None:
        if not self.root.exists():
            self.root.mkdir(mode=0o700, parents=False)
        metadata = self.root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or self.root.is_symlink():
            raise PilotAcceptanceError(FIXED_ERROR)
        if self.strict_metadata and (
            (metadata.st_uid, metadata.st_gid) != (0, 0)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise PilotAcceptanceError(FIXED_ERROR)

    def exists(self) -> bool:
        self._validate_root()
        return self.path.exists() or self.path.is_symlink()

    def read(self) -> AcceptanceState:
        self._validate_root()
        metadata = self.path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or self.path.is_symlink():
            raise PilotAcceptanceError(FIXED_ERROR)
        if self.strict_metadata and (
            (metadata.st_uid, metadata.st_gid) != (0, 0)
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise PilotAcceptanceError(FIXED_ERROR)
        try:
            raw = self.path.read_bytes()
            if not 1 <= len(raw) <= 16384:
                raise PilotAcceptanceError(FIXED_ERROR)
            document = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PilotAcceptanceError(FIXED_ERROR) from exc
        if not isinstance(document, Mapping):
            raise PilotAcceptanceError(FIXED_ERROR)
        return AcceptanceState.from_mapping(document)

    def write(self, state: AcceptanceState, *, create: bool = False) -> None:
        self._validate_root()
        if create and (self.path.exists() or self.path.is_symlink()):
            raise PilotAcceptanceError(FIXED_ERROR)
        if not create and not self.path.exists():
            raise PilotAcceptanceError(FIXED_ERROR)
        payload = (json.dumps(state.document(), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        temporary = self.root / f".active-{uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = None
        try:
            descriptor = os.open(temporary, flags, 0o600)
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
            _fsync_directory(self.root)
        except OSError as exc:
            raise PilotAcceptanceError(FIXED_ERROR) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def remove(self) -> None:
        self.read()
        self.path.unlink()
        _fsync_directory(self.root)


def _fsync_directory(path: Path) -> None:
    """Persist directory entries where the host exposes POSIX directory fsync."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PilotAcceptanceError(FIXED_ERROR) from exc
    if parsed.tzinfo is None:
        raise PilotAcceptanceError(FIXED_ERROR)
    return value


def _positive_id(value: object) -> int:
    if isinstance(value, bool):
        raise PilotAcceptanceError(FIXED_ERROR)
    try:
        selected = int(value)
    except (TypeError, ValueError) as exc:
        raise PilotAcceptanceError(FIXED_ERROR) from exc
    if selected <= 0 or str(selected) != str(value):
        raise PilotAcceptanceError(FIXED_ERROR)
    return selected


def _object_id(value: str) -> str:
    try:
        selected = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise PilotAcceptanceError(FIXED_ERROR) from exc
    if selected.version != 4 or str(selected) != value:
        raise PilotAcceptanceError(FIXED_ERROR)
    return value


def _object_key(object_id: str) -> str:
    return f"v1/{UUID(_object_id(object_id)).hex}"


def _release(value: str) -> str:
    if not RELEASE_PATTERN.fullmatch(value):
        raise PilotAcceptanceError(FIXED_ERROR)
    return value


def _build(value: str) -> str:
    if not re.fullmatch(r"[0-9]{8}\.[0-9]{1,6}", value):
        raise PilotAcceptanceError(FIXED_ERROR)
    return value


def _provider_name(value: object) -> str:
    selected = str(value or "")
    if selected not in PROVIDER_NAMES:
        raise PilotAcceptanceError(FIXED_ERROR)
    return selected


def _expected_pid_from_environment(name: str) -> int:
    value = os.environ.get(name, "")
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise PilotAcceptanceError(FIXED_ERROR)
    return _positive_id(value)


def _worker_file_owner(user_name: str) -> tuple[int, int]:
    if os.name != "posix":
        raise PilotAcceptanceError(FIXED_ERROR)
    try:
        import pwd

        record = pwd.getpwnam(user_name)
    except (ImportError, KeyError) as exc:
        raise PilotAcceptanceError(FIXED_ERROR) from exc
    return record.pw_uid, record.pw_gid


def _expected_worker_file_owner(
    user_name: str,
    uid_environment_key: str,
    gid_environment_key: str,
    mode_environment_key: str,
) -> tuple[int, int]:
    uid = os.environ.get(uid_environment_key, "")
    gid = os.environ.get(gid_environment_key, "")
    mode = os.environ.get(mode_environment_key, "")
    if (
        not re.fullmatch(r"[1-9][0-9]*", uid)
        or not re.fullmatch(r"[1-9][0-9]*", gid)
        or mode != "600"
    ):
        raise PilotAcceptanceError(FIXED_ERROR)
    expected = (_positive_id(uid), _positive_id(gid))
    if expected != _worker_file_owner(user_name):
        raise PilotAcceptanceError(FIXED_ERROR)
    return expected


def _identity(value: object) -> str:
    selected = str(value or "")
    if not WORKOS_USER_ID_PATTERN.fullmatch(selected):
        raise PilotAcceptanceError(FIXED_ERROR)
    return selected


def read_protected_environment(
    path: Path,
    required_keys: tuple[str, ...],
    *,
    strict_metadata: bool = True,
) -> dict[str, str]:
    """Parse exact KEY=value records without evaluating shell syntax."""
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise PilotAcceptanceError(FIXED_ERROR)
        if strict_metadata and (
            metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise PilotAcceptanceError(FIXED_ERROR)
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PilotAcceptanceError(FIXED_ERROR) from exc
    values: dict[str, str] = {}
    required = set(required_keys)
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PilotAcceptanceError(FIXED_ERROR)
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or not value or value != value.strip():
            raise PilotAcceptanceError(FIXED_ERROR)
        if any(character.isspace() for character in value) or key in values:
            raise PilotAcceptanceError(FIXED_ERROR)
        if key in required:
            values[key] = value
    if set(values) != required:
        raise PilotAcceptanceError(FIXED_ERROR)
    return values


def _create_engine(url_value: str, expected_role: str, *, pooled: bool) -> Engine:
    url = postgres_url(url_value)
    if url.username != expected_role or url.database != "careertrellis":
        raise PilotAcceptanceError(FIXED_ERROR)
    options: dict[str, object] = {
        "hide_parameters": True,
        "pool_pre_ping": True,
        "echo": False,
    }
    if pooled:
        options.update(pool_size=1, max_overflow=0, pool_reset_on_return="rollback", pool_timeout=10)
    else:
        options["poolclass"] = NullPool
    return create_engine(url, **options)


def _protected_migration_transaction(engine: Engine):
    """Return a transaction context with server parameter logging disabled."""
    context = engine.begin()
    connection = context.__enter__()
    try:
        connection.exec_driver_sql("SET LOCAL log_parameter_max_length = 0")
        connection.exec_driver_sql("SET LOCAL log_parameter_max_length_on_error = 0")
        connection.exec_driver_sql("SET ROLE careertrellis_schema_owner")
    except Exception:
        context.__exit__(*sys.exc_info())
        raise
    return context, connection


def _mapping(connection: Any, workos_user_id: str) -> Mapping[str, object] | None:
    set_identity_context(connection, workos_user_id)
    return connection.execute(
        text(
            "SELECT id, role, disabled_at FROM careertrellis.app_users "
            "WHERE workos_user_id = :identity"
        ),
        {"identity": workos_user_id},
    ).mappings().one_or_none()


def mapping_preflight(
    migration_engine: Engine,
    runtime_engine: Engine,
    owner_identity: str,
    pilot_identity: str,
) -> tuple[int, int]:
    """Resolve exactly one active owner and active pilot without disclosing either."""
    owner_identity = _identity(owner_identity)
    pilot_identity = _identity(pilot_identity)
    if owner_identity == pilot_identity:
        raise PilotAcceptanceError(FIXED_ERROR)
    context, connection = _protected_migration_transaction(migration_engine)
    try:
        owner = _mapping(connection, owner_identity)
        pilot = _mapping(connection, pilot_identity)
        if (
            owner is None or owner["role"] != "owner" or owner["disabled_at"] is not None
            or pilot is None or pilot["role"] != "user" or pilot["disabled_at"] is not None
        ):
            raise PilotAcceptanceError(FIXED_ERROR)
        owner_id = _positive_id(owner["id"])
        pilot_id = _positive_id(pilot["id"])
        if owner_id == pilot_id:
            raise PilotAcceptanceError(FIXED_ERROR)
        connection.exec_driver_sql("RESET ROLE")
    except Exception:
        context.__exit__(*sys.exc_info())
        raise
    else:
        context.__exit__(None, None, None)

    with runtime_engine.begin() as runtime:
        set_tenant_context(runtime, pilot_id, pilot_identity)
        for table in PROTECTED_TABLES:
            if int(runtime.scalar(text(f"SELECT count(*) FROM careertrellis.{table}")) or 0) != 0:
                raise PilotAcceptanceError(FIXED_ERROR)

    context, connection = _protected_migration_transaction(migration_engine)
    try:
        if int(connection.scalar(text(
            "SELECT count(*) FROM careertrellis.account_erasure_requests "
            "WHERE owner_user_id = :owner"
        ), {"owner": pilot_id}) or 0) != 0:
            raise PilotAcceptanceError(FIXED_ERROR)
        for table in BROKER_TABLES:
            if int(connection.scalar(text(f"SELECT count(*) FROM careertrellis.{table}")) or 0) != 0:
                raise PilotAcceptanceError(FIXED_ERROR)
        connection.exec_driver_sql("RESET ROLE")
    except Exception:
        context.__exit__(*sys.exc_info())
        raise
    else:
        context.__exit__(None, None, None)
    return owner_id, pilot_id


def _choose_provider_namespace(
    rows: list[Mapping[str, object]], owner_user_id: int, pilot_user_id: int
) -> str:
    occupied = {
        (_positive_id(row["owner_user_id"]), _provider_name(row["provider"]))
        for row in rows
    }
    for provider in PROVIDER_NAMES:
        if (
            (owner_user_id, provider) not in occupied
            and (pilot_user_id, provider) not in occupied
        ):
            return provider
    raise PilotAcceptanceError(FIXED_ERROR)


def select_provider_namespace(
    migration_engine: Engine, owner_user_id: int, pilot_user_id: int
) -> str:
    """Choose one allowed namespace absent for both tenants under a table lock."""

    context, connection = _protected_migration_transaction(migration_engine)
    try:
        connection.exec_driver_sql(
            "LOCK TABLE careertrellis.provider_secrets "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
        rows = connection.execute(text(
            "SELECT owner_user_id, provider "
            "FROM careertrellis.provider_secrets "
            "WHERE owner_user_id IN (:owner, :pilot) "
            "AND provider IN ('gemini', 'openai', 'google_maps') "
            "FOR UPDATE"
        ), {
            "owner": owner_user_id,
            "pilot": pilot_user_id,
        }).mappings().all()
        provider = _choose_provider_namespace(
            rows, owner_user_id, pilot_user_id
        )
        connection.exec_driver_sql("RESET ROLE")
    except Exception:
        context.__exit__(*sys.exc_info())
        raise
    else:
        context.__exit__(None, None, None)
    return provider


def _assert_provider_namespace_free(
    connection: Any, state: AcceptanceState
) -> None:
    connection.exec_driver_sql(
        "LOCK TABLE careertrellis.provider_secrets "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    occupied = int(connection.scalar(text(
        "SELECT count(*) FROM careertrellis.provider_secrets "
        "WHERE owner_user_id IN (:owner, :pilot) AND provider=:provider"
    ), {
        "owner": state.owner_user_id,
        "pilot": state.pilot_user_id,
        "provider": state.provider,
    }) or 0)
    if occupied != 0:
        raise PilotAcceptanceError(FIXED_ERROR)


def _fixture_for_owner(
    connection: Any,
    owner_user_id: int,
    marker: str,
    label: str,
    *,
    create_profile: bool,
    provider_secret: tuple[str, bytes] | None,
    provider: str,
    staged_object: Any,
) -> dict[str, int]:
    set_tenant_context(connection, owner_user_id)
    metadata = staged_object.metadata
    if (
        metadata.owner_user_id != owner_user_id
        or metadata.object_key != _object_key(str(metadata.id))
    ):
        raise PilotAcceptanceError(FIXED_ERROR)
    connection.execute(text(
        "INSERT INTO careertrellis.stored_objects "
        "(id, owner_user_id, object_key, content_type, plaintext_size, sha256, wrapped_data_key) "
        "VALUES (:id, :owner, :key, :content_type, :size, :sha256, :wrapped)"
    ), {
        "id": metadata.id,
        "owner": owner_user_id,
        "key": metadata.object_key,
        "content_type": metadata.content_type,
        "size": metadata.size_bytes,
        "sha256": bytes.fromhex(metadata.sha256),
        "wrapped": metadata.wrapped_data_key,
    })
    resume_id = int(connection.scalar(text(
        "INSERT INTO careertrellis.base_resumes "
        "(owner_user_id, name, resume_mode, content, evidence) "
        "VALUES (:owner, :marker, 'general_professional', :content, '{}'::jsonb) RETURNING id"
    ), {"owner": owner_user_id, "marker": marker, "content": f"{marker}-{label}-resume"}))
    version_id = int(connection.scalar(text(
        "INSERT INTO careertrellis.base_resume_versions "
        "(owner_user_id, base_resume_id, version_number, name, resume_mode, content, evidence) "
        "VALUES (:owner, :resume, 1, :marker, 'general_professional', :content, '{}'::jsonb) RETURNING id"
    ), {"owner": owner_user_id, "resume": resume_id, "marker": marker,
        "content": f"{marker}-{label}-resume"}))
    profile_id = 0
    if create_profile:
        profile_id = int(connection.scalar(text(
            "INSERT INTO careertrellis.profiles "
            "(owner_user_id, name, ai_provider, ai_model, maps_provider, "
            "prefer_us_headquarters, interface_language, active_base_resume_id) "
            "VALUES (:owner, :marker, 'gemini', 'gemini-2.5-flash', "
            "'openstreetmap', true, 'en', :resume) RETURNING id"
        ), {"owner": owner_user_id, "marker": marker, "resume": resume_id}))
    job_id = int(connection.scalar(text(
        "INSERT INTO careertrellis.jobs "
        "(owner_user_id, title, company, description, url, match_score, date_found, "
        "status, location, work_arrangement, employment_type, source, is_expired) "
        "VALUES (:owner, :marker, :company, :description, :url, 100, CURRENT_DATE, "
        "'matched', 'Synthetic', 'remote', 'full_time', 'pilot_acceptance', false) RETURNING id"
    ), {
        "owner": owner_user_id,
        "marker": marker,
        "company": f"{marker}-{label}",
        "description": f"{marker}-{label}-job",
        "url": f"https://example.test/{marker}/{label}",
    }))
    application_id = int(connection.scalar(text(
        "INSERT INTO careertrellis.applications "
        "(owner_user_id, job_id, company, position, status, tailored_resume_text, "
        "cover_letter, cover_letter_object_id, base_resume_id, base_resume_name, "
        "base_resume_version, interview_prep) "
        "VALUES (:owner, :job, :company, :marker, 'tailored', :resume_text, :letter, "
        ":object_id, :resume, :marker, 1, :prep) RETURNING id"
    ), {
        "owner": owner_user_id,
        "job": job_id,
        "company": f"{marker}-{label}",
        "marker": marker,
        "resume_text": f"{marker}-{label}-tailored",
        "letter": f"{marker}-{label}-letter",
        "object_id": metadata.id,
        "resume": resume_id,
        "prep": f"{marker}-{label}-prep",
    }))
    provider_id = 0
    if provider_secret is not None:
        key_id, ciphertext = provider_secret
        provider_id = int(connection.scalar(text(
            "INSERT INTO careertrellis.provider_secrets "
            "(owner_user_id, provider, key_id, ciphertext) "
            "VALUES (:owner, :provider, :key_id, :ciphertext) RETURNING id"
        ), {
            "owner": owner_user_id,
            "provider": _provider_name(provider),
            "key_id": key_id,
            "ciphertext": ciphertext,
        }))
    return {
        "profile_id": profile_id,
        "resume_id": resume_id,
        "resume_version_id": version_id,
        "job_id": job_id,
        "application_id": application_id,
        "provider_secret_id": provider_id,
    }


def create_fixtures(
    migration_engine: Engine,
    state: AcceptanceState,
    owner_provider_secret: tuple[str, bytes],
    pilot_provider_secret: tuple[str, bytes],
    owner_staged_object: Any,
    pilot_staged_object: Any,
) -> FixtureIds:
    if state.phase != "preparing" or state.fixture is not None:
        raise PilotAcceptanceError(FIXED_ERROR)
    if (
        str(owner_staged_object.metadata.id) != state.owner_object_id
        or str(pilot_staged_object.metadata.id) != state.pilot_object_id
    ):
        raise PilotAcceptanceError(FIXED_ERROR)
    context, connection = _protected_migration_transaction(migration_engine)
    try:
        _assert_provider_namespace_free(connection, state)
        owner = _fixture_for_owner(
            connection, state.owner_user_id, state.marker, "owner",
            create_profile=False, provider_secret=owner_provider_secret,
            provider=state.provider,
            staged_object=owner_staged_object,
        )
        pilot = _fixture_for_owner(
            connection, state.pilot_user_id, state.marker, "pilot",
            create_profile=True, provider_secret=pilot_provider_secret,
            provider=state.provider,
            staged_object=pilot_staged_object,
        )
        connection.exec_driver_sql("RESET ROLE")
    except Exception:
        context.__exit__(*sys.exc_info())
        raise
    else:
        context.__exit__(None, None, None)
    return FixtureIds(
        owner_resume_id=owner["resume_id"],
        owner_resume_version_id=owner["resume_version_id"],
        owner_job_id=owner["job_id"],
        owner_application_id=owner["application_id"],
        pilot_profile_id=pilot["profile_id"],
        pilot_resume_id=pilot["resume_id"],
        pilot_resume_version_id=pilot["resume_version_id"],
        pilot_job_id=pilot["job_id"],
        pilot_application_id=pilot["application_id"],
        owner_provider_secret_id=owner["provider_secret_id"],
        pilot_provider_secret_id=pilot["provider_secret_id"],
    )


def upload_staged_objects(
    object_backend: Any,
    state: AcceptanceState,
    owner_staged_object: Any,
    pilot_staged_object: Any,
    *,
    object_keyring: Any,
) -> None:
    from object_store import decrypt_object

    expected = (
        (
            state.owner_object_id,
            state.owner_user_id,
            f"{state.marker}-owner-object".encode("ascii"),
            owner_staged_object,
        ),
        (
            state.pilot_object_id,
            state.pilot_user_id,
            f"{state.marker}-pilot-object".encode("ascii"),
            pilot_staged_object,
        ),
    )
    for object_id, owner_id, plaintext, staged in expected:
        if (
            str(staged.metadata.id) != object_id
            or staged.metadata.owner_user_id != owner_id
            or staged.metadata.object_key != _object_key(object_id)
        ):
            raise PilotAcceptanceError(FIXED_ERROR)
        object_backend.put(staged.metadata.object_key, staged.encrypted_payload)
        if object_backend.head(staged.metadata.object_key) != len(staged.encrypted_payload):
            raise PilotAcceptanceError(FIXED_ERROR)
        payload = object_backend.get(staged.metadata.object_key)
        if decrypt_object(
            staged.metadata, payload, owner_id, keyring=object_keyring
        ) != plaintext:
            raise PilotAcceptanceError(FIXED_ERROR)


def _contains_marker(value: object, marker: str) -> bool:
    if isinstance(value, str):
        return marker in value
    if isinstance(value, Mapping):
        return any(_contains_marker(item, marker) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_marker(item, marker) for item in value)
    return False


def _cross_mutation_is_blocked(
    engine: Engine,
    actor_user_id: int,
    other_job_id: int,
    marker: str,
) -> tuple[bool, bool]:
    connection = engine.connect()
    transaction = connection.begin()
    try:
        set_tenant_context(connection, actor_user_id)
        updated = connection.execute(text(
            "UPDATE careertrellis.jobs SET title = :marker "
            "WHERE id = :other_job"
        ), {"marker": f"{marker}-forbidden", "other_job": other_job_id}).rowcount
        deleted = connection.execute(text(
            "DELETE FROM careertrellis.jobs WHERE id = :other_job"
        ), {"other_job": other_job_id}).rowcount
        related = connection.execute(text(
            "INSERT INTO careertrellis.applications "
            "(owner_user_id, job_id, company, position, status) "
            "SELECT :actor, id, :marker, :marker, 'tailored' "
            "FROM careertrellis.jobs WHERE id = :other_job"
        ), {"actor": actor_user_id, "marker": marker, "other_job": other_job_id}).rowcount
        return updated == 0 and deleted == 0, related == 0
    finally:
        transaction.rollback()
        connection.close()


def _rollback_only_erasure(engine: Engine, owner_user_id: int) -> bool:
    connection = engine.connect()
    transaction = connection.begin()
    try:
        set_tenant_context(connection, owner_user_id)
        row = connection.execute(
            text("SELECT * FROM careertrellis.request_account_erasure()")
        ).mappings().one()
        disabled = connection.scalar(text(
            "SELECT disabled_at IS NOT NULL FROM careertrellis.app_users WHERE id = :owner"
        ), {"owner": owner_user_id})
        qualified = row.get("status") == "accepted" and disabled is True
    finally:
        transaction.rollback()
        connection.close()
    with engine.begin() as connection:
        set_tenant_context(connection, owner_user_id)
        active = connection.scalar(text(
            "SELECT disabled_at IS NULL FROM careertrellis.app_users WHERE id = :owner"
        ), {"owner": owner_user_id})
    return qualified and active is True


class _CountingBackend:
    def __init__(self, backend: Any):
        self.backend = backend
        self.get_count = 0

    def get(self, object_key: str) -> bytes:
        self.get_count += 1
        return self.backend.get(object_key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backend, name)


def validate_worker_heartbeat(
    path: Path,
    build: str,
    expected_pid: int,
    expected_owner: tuple[int, int],
    *,
    now: datetime | None = None,
    maximum_age_seconds: int = 120,
) -> bool:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or stat.S_IMODE(metadata.st_mode) != WORKER_HEARTBEAT_MODE
            or (metadata.st_uid, metadata.st_gid) != expected_owner
        ):
            return False
        raw = path.read_bytes()
        if not 1 <= len(raw) <= 1024:
            return False
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(document, dict) or set(document) != {
        "build", "pid", "status", "updated_at"
    }:
        return False
    pid = document["pid"]
    status = document["status"]
    if (
        document["build"] != build
        or isinstance(pid, bool) or not isinstance(pid, int)
        or pid != expected_pid
        or status != "idle"
    ):
        return False
    try:
        updated = datetime.fromisoformat(str(document["updated_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if updated.tzinfo is None:
        return False
    selected_now = now or datetime.now(timezone.utc)
    age = (selected_now - updated.astimezone(timezone.utc)).total_seconds()
    return -5 <= age <= maximum_age_seconds


def _ids(snapshot: Mapping[str, object], collection: str) -> set[int]:
    rows = snapshot.get(collection)
    if not isinstance(rows, list):
        return set()
    result: set[int] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            return set()
        value = row.get("id")
        if isinstance(value, bool) or not isinstance(value, int):
            return set()
        result.add(value)
    return result


def exports_reciprocally_isolated(
    owner_export: Mapping[str, object],
    pilot_export: Mapping[str, object],
    fixture: FixtureIds,
) -> bool:
    expected_collections = set(EXPORT_FIELDS)
    if (
        set(owner_export) != expected_collections
        or set(pilot_export) != expected_collections
    ):
        return False
    for collection in EXPORT_FIELDS:
        if not _ids(owner_export, collection).isdisjoint(
            _ids(pilot_export, collection)
        ):
            return False
    fixture_collections = (
        (
            "base_resumes",
            fixture.owner_resume_id,
            fixture.pilot_resume_id,
        ),
        (
            "base_resume_versions",
            fixture.owner_resume_version_id,
            fixture.pilot_resume_version_id,
        ),
        ("jobs", fixture.owner_job_id, fixture.pilot_job_id),
        (
            "applications",
            fixture.owner_application_id,
            fixture.pilot_application_id,
        ),
    )
    for collection, owner_id, pilot_id in fixture_collections:
        owner_ids = _ids(owner_export, collection)
        pilot_ids = _ids(pilot_export, collection)
        if (
            owner_id not in owner_ids
            or pilot_id in owner_ids
            or pilot_id not in pilot_ids
            or owner_id in pilot_ids
        ):
            return False
    owner_profile_ids = _ids(owner_export, "profile")
    pilot_profile_ids = _ids(pilot_export, "profile")
    return fixture.pilot_profile_id in pilot_profile_ids


def _provider_secret_evidence(
    runtime_engine: Engine,
    state: AcceptanceState,
    provider_keyring: Any,
) -> bool:
    from provider_secrets import decrypt_provider_secret

    fixture = state.fixture
    if fixture is None:
        return False
    expected = (
        (
            state.owner_user_id,
            fixture.owner_provider_secret_id,
            fixture.pilot_provider_secret_id,
            f"{state.marker}-owner-secret",
        ),
        (
            state.pilot_user_id,
            fixture.pilot_provider_secret_id,
            fixture.owner_provider_secret_id,
            f"{state.marker}-pilot-secret",
        ),
    )
    for actor_id, own_id, other_id, marker in expected:
        with runtime_engine.begin() as connection:
            set_tenant_context(connection, actor_id)
            own = connection.execute(text(
                "SELECT key_id, ciphertext "
                "FROM careertrellis.provider_secrets "
                "WHERE id=:secret AND owner_user_id=:owner "
                "AND provider=:provider AND revoked_at IS NULL"
            ), {
                "secret": own_id,
                "owner": actor_id,
                "provider": state.provider,
            }).mappings().one_or_none()
            cross_count = int(connection.scalar(text(
                "SELECT count(*) FROM careertrellis.provider_secrets "
                "WHERE id=:secret AND provider=:provider"
            ), {
                "secret": other_id,
                "provider": state.provider,
            }) or 0)
        if own is None or cross_count != 0:
            return False
        try:
            plaintext = decrypt_provider_secret(
                actor_id,
                state.provider,
                own["key_id"],
                own["ciphertext"],
                keyring=provider_keyring,
            )
        except Exception:
            return False
        if plaintext != marker:
            return False
    return True


def verify_live_boundary(
    runtime_engine: Engine,
    state: AcceptanceState,
    *,
    object_backend: Any,
    object_keyring: Any,
    provider_keyring: Any,
    search_worker_pid: int,
    erasure_worker_pid: int,
    search_worker_owner: tuple[int, int],
    erasure_worker_owner: tuple[int, int],
    search_heartbeat: Path = SEARCH_HEARTBEAT_FILE,
    erasure_heartbeat: Path = ERASURE_HEARTBEAT_FILE,
) -> dict[str, bool | str]:
    fixture = state.fixture
    if state.phase != "prepared" or fixture is None:
        raise PilotAcceptanceError(FIXED_ERROR)

    with runtime_engine.begin() as connection:
        if connection.scalar(text("SELECT current_setting('careertrellis.user_id', true)")) not in (None, ""):
            raise PilotAcceptanceError(FIXED_ERROR)
        for table in PROTECTED_TABLES:
            if int(connection.scalar(text(f"SELECT count(*) FROM careertrellis.{table}")) or 0) != 0:
                raise PilotAcceptanceError(FIXED_ERROR)

    owner_jobs = PostgreSQLDiscoveryRepository(state.owner_user_id, engine=runtime_engine).list_jobs(True)
    pilot_jobs = PostgreSQLDiscoveryRepository(state.pilot_user_id, engine=runtime_engine).list_jobs(True)
    job_isolated = (
        _contains_marker(owner_jobs, state.marker)
        and _contains_marker(pilot_jobs, state.marker)
        and not _contains_marker(
            [row for row in owner_jobs if row.get("id") == fixture.pilot_job_id], state.marker
        )
        and not _contains_marker(
            [row for row in pilot_jobs if row.get("id") == fixture.owner_job_id], state.marker
        )
    )

    owner_profile = PostgreSQLProfileRepository(state.owner_user_id, engine=runtime_engine)
    pilot_profile = PostgreSQLProfileRepository(state.pilot_user_id, engine=runtime_engine)
    try:
        owner_profile.get_base_resume(fixture.pilot_resume_id)
        profile_cross_one = False
    except BaseResumeNotFound:
        profile_cross_one = True
    try:
        pilot_profile.get_base_resume(fixture.owner_resume_id)
        profile_cross_two = False
    except BaseResumeNotFound:
        profile_cross_two = True
    profile_resume_isolated = (
        profile_cross_one and profile_cross_two
        and _contains_marker(owner_profile.get_base_resume(fixture.owner_resume_id), state.marker)
        and _contains_marker(pilot_profile.get_base_resume(fixture.pilot_resume_id), state.marker)
    )

    owner_applications = PostgreSQLApplicationReadRepository(
        state.owner_user_id, engine=runtime_engine
    ).list_applications()
    pilot_applications = PostgreSQLApplicationReadRepository(
        state.pilot_user_id, engine=runtime_engine
    ).list_applications()
    application_isolated = (
        _contains_marker(owner_applications, state.marker)
        and _contains_marker(pilot_applications, state.marker)
        and not any(row.get("id") == fixture.pilot_application_id for row in owner_applications)
        and not any(row.get("id") == fixture.owner_application_id for row in pilot_applications)
    )

    owner_export = PostgreSQLUserDataExportRepository(
        state.owner_user_id, engine=runtime_engine
    ).snapshot()
    pilot_export = PostgreSQLUserDataExportRepository(
        state.pilot_user_id, engine=runtime_engine
    ).snapshot()
    export_isolated = exports_reciprocally_isolated(
        owner_export, pilot_export, fixture
    )

    write_one, relate_one = _cross_mutation_is_blocked(
        runtime_engine, state.owner_user_id, fixture.pilot_job_id, state.marker
    )
    write_two, relate_two = _cross_mutation_is_blocked(
        runtime_engine, state.pilot_user_id, fixture.owner_job_id, state.marker
    )

    provider_secret_isolated = _provider_secret_evidence(
        runtime_engine, state, provider_keyring
    )

    with runtime_engine.begin() as connection:
        set_tenant_context(connection, state.owner_user_id)
        if int(connection.scalar(text(
            "SELECT count(*) FROM careertrellis.jobs WHERE id = :job"
        ), {"job": fixture.owner_job_id}) or 0) != 1:
            raise PilotAcceptanceError(FIXED_ERROR)
    with runtime_engine.begin() as connection:
        pooled_context_cleared = (
            connection.scalar(text("SELECT current_setting('careertrellis.user_id', true)")) in (None, "")
            and int(connection.scalar(text("SELECT count(*) FROM careertrellis.jobs")) or 0) == 0
        )

    erasure_qualified = _rollback_only_erasure(runtime_engine, state.owner_user_id)
    erasure_qualified = erasure_qualified and _rollback_only_erasure(
        runtime_engine, state.pilot_user_id
    )

    counted_backend = _CountingBackend(object_backend)
    owner_payload = f"{state.marker}-owner-object".encode("ascii")
    pilot_payload = f"{state.marker}-pilot-object".encode("ascii")
    owner_download = download_stored_object(
        state.owner_user_id, state.owner_object_id, engine=runtime_engine,
        backend=counted_backend, keyring=object_keyring,
    )
    pilot_download = download_stored_object(
        state.pilot_user_id, state.pilot_object_id, engine=runtime_engine,
        backend=counted_backend, keyring=object_keyring,
    )
    own_downloads = (
        owner_download.content == owner_payload
        and pilot_download.content == pilot_payload
    )
    before_cross = counted_backend.get_count
    cross_denied = True
    for actor, other_object in (
        (state.owner_user_id, state.pilot_object_id),
        (state.pilot_user_id, state.owner_object_id),
    ):
        try:
            download_stored_object(
                actor, other_object, engine=runtime_engine,
                backend=counted_backend, keyring=object_keyring,
            )
            cross_denied = False
        except StoredObjectNotFound:
            pass
    object_isolated = (
        own_downloads and cross_denied and counted_backend.get_count == before_cross
    )
    worker_live = (
        validate_worker_heartbeat(
            search_heartbeat,
            state.build,
            search_worker_pid,
            search_worker_owner,
        )
        and validate_worker_heartbeat(
            erasure_heartbeat,
            state.build,
            erasure_worker_pid,
            erasure_worker_owner,
        )
    )

    checks: dict[str, bool | str] = {
        "mapping_boundary": True,
        "pilot_initially_empty": True,
        "missing_context_empty": True,
        "pooled_context_cleared": pooled_context_cleared,
        "profile_resume_isolated": profile_resume_isolated,
        "job_isolated": job_isolated,
        "application_isolated": application_isolated,
        "export_isolated": export_isolated,
        "cross_write_blocked": write_one and write_two,
        "cross_relate_blocked": relate_one and relate_two,
        "rollback_only_erasure": erasure_qualified,
        "provider_secret_isolated": provider_secret_isolated,
        "provider_network_called": False,
        "object_storage_live": object_isolated,
        "worker_live": worker_live,
    }
    if any(value is False for key, value in checks.items() if key != "provider_network_called"):
        raise PilotAcceptanceError(FIXED_ERROR)
    return checks


def _delete_exact(
    connection: Any,
    table: str,
    row_id: int,
    owner_user_id: int,
    marker_column: str,
    marker: str,
) -> None:
    visible = connection.execute(text(
        f"SELECT {marker_column} FROM careertrellis.{table} "
        "WHERE id = :id AND owner_user_id = :owner FOR UPDATE"
    ), {"id": row_id, "owner": owner_user_id}).scalar_one_or_none()
    if visible is None:
        return
    if marker not in str(visible):
        raise PilotAcceptanceError(FIXED_ERROR)
    deleted = connection.execute(text(
        f"DELETE FROM careertrellis.{table} "
        "WHERE id = :id AND owner_user_id = :owner "
        f"AND {marker_column} = :selected_value"
    ), {
        "id": row_id,
        "owner": owner_user_id,
        "selected_value": visible,
    }).rowcount
    if deleted != 1:
        raise PilotAcceptanceError(FIXED_ERROR)


def _delete_stored_object_metadata(
    connection: Any,
    owner_user_id: int,
    object_id: str,
) -> None:
    expected_key = _object_key(object_id)
    row = connection.execute(text(
        "SELECT object_key FROM careertrellis.stored_objects "
        "WHERE id=:id AND owner_user_id=:owner FOR UPDATE"
    ), {"id": UUID(object_id), "owner": owner_user_id}).scalar_one_or_none()
    if row is None:
        return
    if row != expected_key:
        raise PilotAcceptanceError(FIXED_ERROR)
    if connection.execute(text(
        "DELETE FROM careertrellis.stored_objects "
        "WHERE id=:id AND owner_user_id=:owner AND object_key=:key"
    ), {
        "id": UUID(object_id), "owner": owner_user_id, "key": expected_key,
    }).rowcount != 1:
        raise PilotAcceptanceError(FIXED_ERROR)


def _delete_provider_secret(
    connection: Any,
    state: AcceptanceState,
    provider_keyring: Any,
    expected_id: int | None,
    owner_user_id: int,
    expected_marker: str,
) -> None:
    from provider_secrets import decrypt_provider_secret
    set_tenant_context(connection, owner_user_id)
    parameters: dict[str, object] = {
        "owner": owner_user_id,
        "provider": state.provider,
    }
    id_clause = ""
    if expected_id is not None:
        parameters["id"] = expected_id
        id_clause = " AND id=:id"
    rows = connection.execute(text(
        "SELECT id, key_id, ciphertext, revoked_at "
        "FROM careertrellis.provider_secrets "
        "WHERE owner_user_id=:owner AND provider=:provider" + id_clause
        + " FOR UPDATE"
    ), parameters).mappings().all()
    if not rows:
        return
    if len(rows) != 1:
        raise PilotAcceptanceError(FIXED_ERROR)
    row = rows[0]
    if row["revoked_at"] is not None:
        raise PilotAcceptanceError(FIXED_ERROR)
    try:
        plaintext = decrypt_provider_secret(
            owner_user_id, state.provider, row["key_id"], row["ciphertext"],
            keyring=provider_keyring,
        )
    except Exception as exc:
        raise PilotAcceptanceError(FIXED_ERROR) from exc
    if plaintext != expected_marker:
        raise PilotAcceptanceError(FIXED_ERROR)
    if connection.execute(text(
        "DELETE FROM careertrellis.provider_secrets "
        "WHERE id=:id AND owner_user_id=:owner AND provider=:provider "
        "AND key_id=:key_id AND ciphertext=:ciphertext AND revoked_at IS NULL"
    ), {
        "id": row["id"],
        "owner": owner_user_id,
        "provider": state.provider,
        "key_id": row["key_id"],
        "ciphertext": row["ciphertext"],
    }).rowcount != 1:
        raise PilotAcceptanceError(FIXED_ERROR)


def _delete_crash_row(
    connection: Any,
    table: str,
    owner_user_id: int,
    criteria: Mapping[str, object],
) -> None:
    columns = tuple(criteria)
    predicates = " AND ".join(f"{column}=:{column}" for column in columns)
    rows = connection.execute(text(
        f"SELECT id, {', '.join(columns)} FROM careertrellis.{table} "
        f"WHERE owner_user_id=:owner AND {predicates} FOR UPDATE"
    ), {"owner": owner_user_id, **criteria}).mappings().all()
    if len(rows) > 1:
        raise PilotAcceptanceError(FIXED_ERROR)
    for row in rows:
        delete_parameters = {
            "id": row["id"],
            "owner": owner_user_id,
            **{column: row[column] for column in columns},
        }
        if connection.execute(text(
            f"DELETE FROM careertrellis.{table} "
            f"WHERE id=:id AND owner_user_id=:owner AND {predicates}"
        ), delete_parameters).rowcount != 1:
            raise PilotAcceptanceError(FIXED_ERROR)


def cleanup_fixtures(
    migration_engine: Engine,
    state: AcceptanceState,
    *,
    provider_keyring: Any,
) -> None:
    context, connection = _protected_migration_transaction(migration_engine)
    try:
        fixture = state.fixture
        if fixture is not None:
            set_tenant_context(connection, state.pilot_user_id)
            _delete_exact(
                connection, "profiles", fixture.pilot_profile_id,
                state.pilot_user_id, "name", state.marker,
            )
            for owner_id, application_id, job_id, version_id, resume_id in (
                (
                    state.owner_user_id, fixture.owner_application_id,
                    fixture.owner_job_id, fixture.owner_resume_version_id,
                    fixture.owner_resume_id,
                ),
                (
                    state.pilot_user_id, fixture.pilot_application_id,
                    fixture.pilot_job_id, fixture.pilot_resume_version_id,
                    fixture.pilot_resume_id,
                ),
            ):
                set_tenant_context(connection, owner_id)
                _delete_exact(connection, "applications", application_id, owner_id, "company", state.marker)
                object_id = (
                    state.owner_object_id
                    if owner_id == state.owner_user_id
                    else state.pilot_object_id
                )
                _delete_stored_object_metadata(
                    connection, owner_id, object_id
                )
                _delete_exact(connection, "jobs", job_id, owner_id, "title", state.marker)
                _delete_exact(connection, "base_resume_versions", version_id, owner_id, "name", state.marker)
                _delete_exact(connection, "base_resumes", resume_id, owner_id, "name", state.marker)
            for owner_id, secret_id, label in (
                (
                    state.owner_user_id,
                    fixture.owner_provider_secret_id,
                    "owner",
                ),
                (
                    state.pilot_user_id,
                    fixture.pilot_provider_secret_id,
                    "pilot",
                ),
            ):
                _delete_provider_secret(
                    connection,
                    state,
                    provider_keyring,
                    secret_id,
                    owner_id,
                    f"{state.marker}-{label}-secret",
                )
        else:
            # Recover a crash between fixture commit and the prepared-state write.
            for owner_id in (state.owner_user_id, state.pilot_user_id):
                label = "owner" if owner_id == state.owner_user_id else "pilot"
                set_tenant_context(connection, owner_id)
                _delete_crash_row(
                    connection,
                    "applications",
                    owner_id,
                    {
                        "company": f"{state.marker}-{label}",
                        "position": state.marker,
                    },
                )
                _delete_stored_object_metadata(
                    connection,
                    owner_id,
                    state.owner_object_id
                    if owner_id == state.owner_user_id
                    else state.pilot_object_id,
                )
                _delete_crash_row(
                    connection,
                    "jobs",
                    owner_id,
                    {
                        "title": state.marker,
                        "company": f"{state.marker}-{label}",
                        "source": "pilot_acceptance",
                    },
                )
                if owner_id == state.pilot_user_id:
                    _delete_crash_row(
                        connection,
                        "profiles",
                        owner_id,
                        {"name": state.marker},
                    )
                for table in ("base_resume_versions", "base_resumes"):
                    _delete_crash_row(
                        connection,
                        table,
                        owner_id,
                        {
                            "name": state.marker,
                            "content": f"{state.marker}-{label}-resume",
                        },
                    )
                _delete_provider_secret(
                    connection,
                    state,
                    provider_keyring,
                    None,
                    owner_id,
                    f"{state.marker}-{label}-secret",
                )
        connection.exec_driver_sql("RESET ROLE")
    except Exception:
        context.__exit__(*sys.exc_info())
        raise
    else:
        context.__exit__(None, None, None)


def cleanup_objects(object_backend: Any, state: AcceptanceState) -> bool:
    for object_id in (state.owner_object_id, state.pilot_object_id):
        key = _object_key(object_id)
        if object_backend.head(key) is not None:
            object_backend.delete(key)
        if object_backend.head(key) is not None:
            raise PilotAcceptanceError(FIXED_ERROR)
    return True


def _receipt_document(
    state: AcceptanceState, *, cleanup_complete: bool = False
) -> dict[str, object] | None:
    automated_complete = (
        set(state.automated_checks) == set(AUTOMATED_CHECKS)
        and all(value is True for key, value in state.automated_checks.items() if key != "provider_network_called")
        and state.automated_checks.get("provider_network_called") is False
    )
    browser_complete = (
        set(state.browser_checks) == set(BROWSER_CHECKS)
        and all(state.browser_checks.values())
    )
    mapping_complete = (
        state.mapping_evidence.disabled_verified
        and state.mapping_evidence.recovered_verified
    )
    if (
        not automated_complete
        or not browser_complete
        or not mapping_complete
        or not cleanup_complete
    ):
        return None
    return {
        "schema": RECEIPT_SCHEMA,
        "operation": "two_user_acceptance",
        "result": "ok",
        "release": state.release,
        "build": state.build,
        "started_at": state.started_at,
        "completed_at": _utc_now(),
        "subject_kind": "pilot",
        "checks": {
            "invitation_only": True,
            "mapping_present": True,
            "role_expected": True,
            "active": True,
            "login_succeeded": True,
            "logout_succeeded": True,
            "recovery_succeeded": True,
            "cross_tenant_exposed": False,
            "data_isolated": True,
            "provider_secret_isolated": True,
            "export_isolated": True,
            "download_isolated": True,
            "missing_context_exposed": False,
            "pooled_context_cleared": True,
            "forbidden_field_present": False,
            "synthetic_data_removed": True,
            "object_isolated": True,
        },
    }


def record_browser_checks(
    state: AcceptanceState,
    answers: Mapping[str, bool],
    *,
    receipt_root: Path = FIXED_RECEIPT_ROOT,
) -> tuple[AcceptanceState, bool]:
    if (
        state.phase != "automated_partial"
        or set(answers) != set(BROWSER_CHECKS)
        or not state.mapping_evidence.disabled_verified
        or not state.mapping_evidence.recovered_verified
    ):
        raise PilotAcceptanceError(FIXED_ERROR)
    if not all(value is True or value is False for value in answers.values()):
        raise PilotAcceptanceError(FIXED_ERROR)
    updated = state.transition("browser_recorded", browser_checks=dict(answers))
    del receipt_root
    return updated, False


def _interactive_identity() -> str:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise PilotAcceptanceError(FIXED_ERROR)
    terminal_attributes = None
    value = ""
    try:
        import termios

        terminal_fd = sys.stdin.fileno()
        terminal_attributes = termios.tcgetattr(terminal_fd)
        hidden = terminal_attributes.copy()
        hidden[3] &= ~termios.ECHO
        print("Immutable WorkOS user ID: ", end="", file=sys.stderr, flush=True)
        termios.tcsetattr(terminal_fd, termios.TCSAFLUSH, hidden)
        value = sys.stdin.readline(257)
    except (EOFError, KeyboardInterrupt, OSError, ValueError) as exc:
        raise PilotAcceptanceError(FIXED_ERROR) from exc
    finally:
        if terminal_attributes is not None:
            termios.tcsetattr(terminal_fd, termios.TCSADRAIN, terminal_attributes)
            print(file=sys.stderr, flush=True)
    if not value.endswith("\n") or len(value) > 256:
        raise PilotAcceptanceError(FIXED_ERROR)
    return _identity(value[:-1])


def _interactive_browser_checks() -> dict[str, bool]:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise PilotAcceptanceError(FIXED_ERROR)
    answers: dict[str, bool] = {}
    for check in BROWSER_CHECKS:
        print(f"{check.replace('_', ' ')} [yes/no]: ", end="", file=sys.stderr, flush=True)
        value = sys.stdin.readline(8).strip().lower()
        if value not in {"yes", "no"}:
            raise PilotAcceptanceError(FIXED_ERROR)
        answers[check] = value == "yes"
    return answers


def _release_binding() -> tuple[str, str]:
    supplied = _release(os.environ.get(RELEASE_ENVIRONMENT_KEY, ""))
    try:
        current = CURRENT_RELEASE_LINK.resolve(strict=True)
    except OSError as exc:
        raise PilotAcceptanceError(FIXED_ERROR) from exc
    if current.parent != RELEASE_ROOT or not current.is_dir():
        raise PilotAcceptanceError(FIXED_ERROR)
    current_release = _release(current.name)
    if supplied != current_release:
        raise PilotAcceptanceError(FIXED_ERROR)
    return current_release, _build(APP_BUILD)


def _mapping_state(runtime_engine: Engine, user_id: int, *, active: bool) -> bool:
    with runtime_engine.begin() as connection:
        set_tenant_context(connection, user_id)
        row = connection.execute(text(
            "SELECT role, disabled_at FROM careertrellis.app_users WHERE id=:owner"
        ), {"owner": user_id}).mappings().one_or_none()
    return bool(
        row is not None and row["role"] == "user"
        and (row["disabled_at"] is None) is active
    )


def _load_operator_boundary() -> tuple[
    str, str, str, dict[str, str], dict[str, str]
]:
    staging = read_protected_environment(
        STAGING_ENVIRONMENT_FILE, (OWNER_ID_KEY, RUNTIME_ENVIRONMENT_KEY)
    )
    if staging[RUNTIME_ENVIRONMENT_KEY] != "staging":
        raise PilotAcceptanceError(FIXED_ERROR)
    migration = read_protected_environment(
        MIGRATION_ENVIRONMENT_FILE, (MIGRATION_URL_KEY,)
    )[MIGRATION_URL_KEY]
    runtime = read_protected_environment(
        RUNTIME_ENVIRONMENT_FILE, (RUNTIME_URL_KEY,)
    )[RUNTIME_URL_KEY]
    provider = read_protected_environment(
        PROVIDER_ENVIRONMENT_FILE, PROVIDER_KEYS
    )
    object_storage = read_protected_environment(
        OBJECT_ENVIRONMENT_FILE, OBJECT_KEYS
    )
    object_storage[RUNTIME_ENVIRONMENT_KEY] = staging[RUNTIME_ENVIRONMENT_KEY]
    return (
        _identity(staging[OWNER_ID_KEY]), migration, runtime,
        provider, object_storage,
    )


@contextmanager
def _temporary_environment(values: Mapping[str, str]):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _validate_operator_runtime_boundary(
    provider_environment: Mapping[str, str],
    object_environment: Mapping[str, str],
) -> None:
    """Validate protected crypto and backend configuration without network I/O."""
    if object_environment.get(RUNTIME_ENVIRONMENT_KEY) != "staging":
        raise PilotAcceptanceError(FIXED_ERROR)
    try:
        from object_store import (
            R2ObjectBackend,
            parse_object_keyring,
            runtime_object_backend,
        )
        from provider_secrets import _parse_keyring as parse_provider_keyring

        with _temporary_environment(
            {**provider_environment, **object_environment}
        ):
            parse_provider_keyring()
            parse_object_keyring()
            if not isinstance(runtime_object_backend(), R2ObjectBackend):
                raise PilotAcceptanceError(FIXED_ERROR)
    except PilotAcceptanceError:
        raise
    except Exception as exc:
        raise PilotAcceptanceError(FIXED_ERROR) from exc


def _fixed_result(result: OperationResult) -> None:
    print(f"operation={result.operation}")
    print(f"result={result.result}")
    print(f"phase={result.phase}")
    for key in sorted(result.checks):
        value = result.checks[key]
        rendered = value if value == "blocked" else ("yes" if value else "no")
        print(f"{key}={rendered}")
    print("identity_disclosed=no")
    print("personal_data_disclosed=no")


def _run_operation(operation: str, store: StateStore) -> OperationResult:
    release, build = _release_binding()
    (
        owner_identity, migration_url, runtime_url,
        provider_environment, object_environment,
    ) = _load_operator_boundary()
    migration_engine = _create_engine(migration_url, "careertrellis_migration", pooled=False)
    runtime_engine = _create_engine(runtime_url, "careertrellis_runtime", pooled=True)
    try:
        if operation == "inspect":
            _validate_operator_runtime_boundary(
                provider_environment, object_environment
            )
            if not store.exists():
                return OperationResult(operation, "absent", "none")
            state = store.read()
            if state.release != release or state.build != build:
                raise PilotAcceptanceError(FIXED_ERROR)
            return OperationResult(operation, "ok", state.phase, state.automated_checks)

        if operation == "prepare":
            if store.exists():
                raise PilotAcceptanceError(FIXED_ERROR)
            _validate_operator_runtime_boundary(
                provider_environment, object_environment
            )
            pilot_identity = _interactive_identity()
            owner_id, pilot_id = mapping_preflight(
                migration_engine, runtime_engine, owner_identity, pilot_identity
            )
            provider = select_provider_namespace(
                migration_engine, owner_id, pilot_id
            )
            from provider_secrets import (
                _parse_keyring as parse_provider_keyring,
                encrypt_provider_secret,
            )
            from object_store import (
                encrypt_object, parse_object_keyring, runtime_object_backend,
            )

            with _temporary_environment(
                {**provider_environment, **object_environment}
            ):
                object_keyring = parse_object_keyring()
                provider_keyring = parse_provider_keyring()
                draft = AcceptanceState.new(
                    release=release, build=build,
                    owner_user_id=owner_id, pilot_user_id=pilot_id,
                    provider=provider,
                )
                owner_staged = encrypt_object(
                    owner_id,
                    f"{draft.marker}-owner-object".encode("ascii"),
                    "text/plain; charset=utf-8",
                    keyring=object_keyring,
                )
                pilot_staged = encrypt_object(
                    pilot_id,
                    f"{draft.marker}-pilot-object".encode("ascii"),
                    "text/plain; charset=utf-8",
                    keyring=object_keyring,
                )
                document = draft.document()
                document["owner_object_id"] = str(owner_staged.metadata.id)
                document["pilot_object_id"] = str(pilot_staged.metadata.id)
                state = AcceptanceState.from_mapping(document)
                store.write(state, create=True)
                owner_provider_secret = encrypt_provider_secret(
                    owner_id,
                    state.provider,
                    f"{state.marker}-owner-secret",
                    keyring=provider_keyring,
                )
                pilot_provider_secret = encrypt_provider_secret(
                    pilot_id,
                    state.provider,
                    f"{state.marker}-pilot-secret",
                    keyring=provider_keyring,
                )
                object_backend = runtime_object_backend()
                upload_staged_objects(
                    object_backend, state, owner_staged, pilot_staged,
                    object_keyring=object_keyring,
                )
                fixture = create_fixtures(
                    migration_engine, state,
                    owner_provider_secret, pilot_provider_secret,
                    owner_staged, pilot_staged,
                )
            state = state.transition("prepared", fixture=asdict(fixture))
            store.write(state)
            return OperationResult(operation, "ok", state.phase)

        state = store.read()
        if state.release != release or state.build != build:
            raise PilotAcceptanceError(FIXED_ERROR)

        if operation == "verify-active":
            from object_store import parse_object_keyring, runtime_object_backend
            from provider_secrets import _parse_keyring as parse_provider_keyring

            search_worker_pid = _expected_pid_from_environment(
                SEARCH_WORKER_PID_ENVIRONMENT_KEY
            )
            erasure_worker_pid = _expected_pid_from_environment(
                ERASURE_WORKER_PID_ENVIRONMENT_KEY
            )
            with _temporary_environment(
                {**provider_environment, **object_environment}
            ):
                checks = verify_live_boundary(
                    runtime_engine, state,
                    object_backend=runtime_object_backend(),
                    object_keyring=parse_object_keyring(),
                    provider_keyring=parse_provider_keyring(),
                    search_worker_pid=search_worker_pid,
                    erasure_worker_pid=erasure_worker_pid,
                    search_worker_owner=_expected_worker_file_owner(
                        SEARCH_WORKER_USER,
                        SEARCH_HEARTBEAT_UID_ENVIRONMENT_KEY,
                        SEARCH_HEARTBEAT_GID_ENVIRONMENT_KEY,
                        SEARCH_HEARTBEAT_MODE_ENVIRONMENT_KEY,
                    ),
                    erasure_worker_owner=_expected_worker_file_owner(
                        ERASURE_WORKER_USER,
                        ERASURE_HEARTBEAT_UID_ENVIRONMENT_KEY,
                        ERASURE_HEARTBEAT_GID_ENVIRONMENT_KEY,
                        ERASURE_HEARTBEAT_MODE_ENVIRONMENT_KEY,
                    ),
                )
            state = state.transition(
                "automated_partial", automated_checks=checks
            )
            store.write(state)
            return OperationResult(operation, "ok", state.phase, checks)

        if operation in {"verify-disabled", "verify-recovered"}:
            if state.phase != "automated_partial":
                raise PilotAcceptanceError(FIXED_ERROR)
            expected_active = operation == "verify-recovered"
            if (
                expected_active
                and not state.mapping_evidence.disabled_verified
            ) or (
                not expected_active
                and state.mapping_evidence.recovered_verified
            ):
                raise PilotAcceptanceError(FIXED_ERROR)
            if not _mapping_state(
                runtime_engine, state.pilot_user_id, active=expected_active
            ):
                raise PilotAcceptanceError(FIXED_ERROR)
            evidence = MappingPhaseEvidence(
                disabled_verified=True,
                recovered_verified=expected_active,
            )
            state = state.transition(
                state.phase,
                mapping_evidence=asdict(evidence),
            )
            store.write(state)
            return OperationResult(
                operation,
                "ok",
                state.phase,
                {
                    "mapping_boundary": True,
                    "disabled_verified": evidence.disabled_verified,
                    "recovered_verified": evidence.recovered_verified,
                },
            )

        if operation == "record-browser":
            answers = _interactive_browser_checks()
            state, receipt_written = record_browser_checks(state, answers)
            store.write(state)
            checks: dict[str, bool | str] = dict(answers)
            checks["receipt_written"] = receipt_written
            return OperationResult(
                operation,
                "ok",
                state.phase,
                checks,
            )

        if operation == "cleanup":
            receipt_expected = state.phase == "browser_recorded"
            if (
                receipt_expected
                and _receipt_document(state, cleanup_complete=True) is None
            ):
                raise PilotAcceptanceError(FIXED_ERROR)
            from object_store import parse_object_keyring, runtime_object_backend
            from provider_secrets import _parse_keyring as parse_provider_keyring

            with _temporary_environment(
                {**provider_environment, **object_environment}
            ):
                cleanup_fixtures(
                    migration_engine, state,
                    provider_keyring=parse_provider_keyring(),
                )
                cleanup_objects(runtime_object_backend(), state)
            receipt_document = (
                _receipt_document(state, cleanup_complete=True)
                if receipt_expected
                else None
            )
            receipt_written = False
            if receipt_document is not None:
                if write_receipt is None:
                    raise PilotAcceptanceError(FIXED_ERROR)
                write_receipt(receipt_document, root=FIXED_RECEIPT_ROOT)
                receipt_written = True
            store.remove()
            return OperationResult(
                operation, "cleaned", "none",
                {
                    "synthetic_data_removed": True,
                    "object_storage_live": True,
                    "receipt_written": receipt_written,
                },
            )

        raise PilotAcceptanceError(FIXED_ERROR)
    finally:
        migration_engine.dispose()
        runtime_engine.dispose()


def _effective_uid() -> int:
    getter = getattr(os, "geteuid", None)
    return int(getter()) if getter is not None else -1


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (
        _effective_uid() != 0
        or len(arguments) != 1
        or arguments[0] not in OPERATIONS
        or not sys.stdin.isatty()
        or not sys.stdout.isatty()
        or not sys.stderr.isatty()
    ):
        print(FIXED_ERROR, file=sys.stderr)
        return 1
    try:
        result = _run_operation(arguments[0], StateStore())
    except (Exception, KeyboardInterrupt):
        print(FIXED_ERROR, file=sys.stderr)
        return 1
    _fixed_result(result)
    return 0 if result.result in {"ok", "absent", "cleaned"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
