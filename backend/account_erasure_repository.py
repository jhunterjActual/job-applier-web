"""Durable PostgreSQL persistence for owner-requested account erasure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict
import uuid

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from data_store import SHARED_DATA_STORE, data_store_kind
from postgres_database import runtime_postgres_engine, tenant_transaction
from provider_secrets import current_owner_user_id


ACCOUNT_ERASURE_UNAVAILABLE_MESSAGE = "Account erasure is temporarily unavailable."
ACCOUNT_ERASURE_RECEIPT_RETENTION_DAYS = 30
DEFAULT_ACCOUNT_ERASURE_LEASE_SECONDS = 300
MAX_ACCOUNT_ERASURE_BATCH_SIZE = 500


class AccountErasureUnavailable(RuntimeError):
    """The fixed privacy boundary for erasure persistence failures."""

    def __init__(self) -> None:
        super().__init__(ACCOUNT_ERASURE_UNAVAILABLE_MESSAGE)


class AccountErasureLeaseLost(AccountErasureUnavailable):
    """The claimed request is no longer owned by this worker lease."""


class AccountErasureRequestResult(TypedDict):
    status: Literal["accepted"]
    accepted_at: str


@dataclass(frozen=True, repr=False)
class AccountErasureClaim:
    """Opaque lease identity; its representation intentionally contains no IDs."""

    request_id: str
    owner_user_id: int
    lease_token: str
    lease_expires_at: datetime

    def __repr__(self) -> str:
        return "AccountErasureClaim(<opaque>)"


@dataclass(frozen=True, repr=False)
class AccountErasureObject:
    """One internal delete target that must never be included in logs/receipts."""

    object_id: str
    object_key: str

    def __repr__(self) -> str:
        return "AccountErasureObject(<opaque>)"


def _positive_id(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive integer.")
    try:
        selected = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer.") from exc
    if selected <= 0 or str(selected) != str(value):
        raise ValueError(f"{label} must be a positive integer.")
    return selected


def _uuid(value: object, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{label} is invalid.") from exc


def _lease_seconds(value: object) -> int:
    try:
        selected = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("The account-erasure lease is invalid.") from exc
    if selected < 30 or selected > 900:
        raise ValueError("The account-erasure lease is invalid.")
    return selected


def _batch_size(value: object) -> int:
    try:
        selected = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("The account-erasure batch size is invalid.") from exc
    if selected < 1 or selected > MAX_ACCOUNT_ERASURE_BATCH_SIZE:
        raise ValueError("The account-erasure batch size is invalid.")
    return selected


def _claim_parameters(claim: AccountErasureClaim) -> dict[str, object]:
    if not isinstance(claim, AccountErasureClaim):
        raise ValueError("An account-erasure claim is required.")
    return {
        "request_id": _uuid(claim.request_id, "Request identifier"),
        "owner_user_id": _positive_id(claim.owner_user_id, "Owner identifier"),
        "lease_token": _uuid(claim.lease_token, "Lease token"),
    }


class PostgreSQLAccountErasureRepository:
    """Accept one authenticated owner's irreversible shared-host request."""

    def __init__(self, owner_user_id: int, *, engine: Engine | None = None) -> None:
        self._owner_user_id = _positive_id(owner_user_id, "Owner identifier")
        self._engine = engine or runtime_postgres_engine()

    def request_erasure(self) -> AccountErasureRequestResult:
        """Disable the local identity and durably enqueue in one transaction."""
        try:
            with tenant_transaction(self._engine, self._owner_user_id) as connection:
                row = connection.execute(
                    text("SELECT * FROM careertrellis.request_account_erasure()")
                ).mappings().one()
        except (SQLAlchemyError, LookupError) as exc:
            raise AccountErasureUnavailable() from exc
        accepted_at = row.get("accepted_at")
        if not isinstance(accepted_at, datetime):
            raise AccountErasureUnavailable()
        return {
            "status": "accepted",
            "accepted_at": accepted_at.isoformat(),
        }


class PostgreSQLAccountErasureWorkerRepository:
    """Lease-bound worker operations exposed only through reviewed DB functions."""

    def __init__(self, *, engine: Engine | None = None) -> None:
        self._engine = engine or runtime_postgres_engine()

    def object_batch(
        self,
        claim: AccountErasureClaim,
        *,
        limit: int,
    ) -> tuple[AccountErasureObject, ...]:
        parameters = {**_claim_parameters(claim), "limit": _batch_size(limit)}
        try:
            with self._engine.begin() as connection:
                rows = connection.execute(
                    text(
                        "SELECT * FROM careertrellis.account_erasure_object_batch("
                        "CAST(:request_id AS uuid), :owner_user_id, "
                        "CAST(:lease_token AS uuid), :limit)"
                    ),
                    parameters,
                ).mappings().all()
        except SQLAlchemyError as exc:
            raise AccountErasureLeaseLost() from exc
        targets: list[AccountErasureObject] = []
        try:
            for row in rows:
                object_id = _uuid(row["object_id"], "Object identifier")
                object_key = str(row["object_key"])
                if object_key != f"v1/{uuid.UUID(object_id).hex}":
                    raise ValueError("The stored-object key is invalid.")
                targets.append(AccountErasureObject(object_id, object_key))
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountErasureUnavailable() from exc
        return tuple(targets)

    def confirm_object_deleted(
        self,
        claim: AccountErasureClaim,
        target: AccountErasureObject,
    ) -> None:
        if not isinstance(target, AccountErasureObject):
            raise ValueError("An account-erasure object is required.")
        parameters = {
            **_claim_parameters(claim),
            "object_id": _uuid(target.object_id, "Object identifier"),
        }
        try:
            with self._engine.begin() as connection:
                confirmed = connection.scalar(
                    text(
                        "SELECT careertrellis.confirm_account_erasure_object_deleted("
                        "CAST(:request_id AS uuid), :owner_user_id, "
                        "CAST(:lease_token AS uuid), CAST(:object_id AS uuid))"
                    ),
                    parameters,
                )
        except SQLAlchemyError as exc:
            raise AccountErasureLeaseLost() from exc
        if confirmed is not True:
            raise AccountErasureLeaseLost()

    def renew(
        self,
        claim: AccountErasureClaim,
        *,
        lease_seconds: int = DEFAULT_ACCOUNT_ERASURE_LEASE_SECONDS,
    ) -> None:
        parameters = {
            **_claim_parameters(claim),
            "lease_seconds": _lease_seconds(lease_seconds),
        }
        try:
            with self._engine.begin() as connection:
                renewed = connection.scalar(
                    text(
                        "SELECT careertrellis.renew_account_erasure_lease("
                        "CAST(:request_id AS uuid), :owner_user_id, "
                        "CAST(:lease_token AS uuid), :lease_seconds)"
                    ),
                    parameters,
                )
        except SQLAlchemyError as exc:
            raise AccountErasureLeaseLost() from exc
        if renewed is not True:
            raise AccountErasureLeaseLost()

    def finalize(self, claim: AccountErasureClaim) -> bool:
        try:
            with self._engine.begin() as connection:
                completed = connection.scalar(
                    text(
                        "SELECT careertrellis.finalize_account_erasure("
                        "CAST(:request_id AS uuid), :owner_user_id, "
                        "CAST(:lease_token AS uuid))"
                    ),
                    _claim_parameters(claim),
                )
        except SQLAlchemyError as exc:
            raise AccountErasureLeaseLost() from exc
        return completed is True

    def retry(self, claim: AccountErasureClaim, error_code: str) -> None:
        if error_code not in {"object_delete_failed", "persistence_unavailable"}:
            raise ValueError("The account-erasure error code is invalid.")
        try:
            with self._engine.begin() as connection:
                retried = connection.scalar(
                    text(
                        "SELECT careertrellis.retry_account_erasure("
                        "CAST(:request_id AS uuid), :owner_user_id, "
                        "CAST(:lease_token AS uuid), :error_code)"
                    ),
                    {**_claim_parameters(claim), "error_code": error_code},
                )
        except SQLAlchemyError as exc:
            raise AccountErasureLeaseLost() from exc
        if retried is not True:
            raise AccountErasureLeaseLost()

    def purge_expired(self, *, limit: int = 100) -> int:
        selected_limit = _batch_size(limit)
        try:
            with self._engine.begin() as connection:
                count = connection.scalar(
                    text(
                        "SELECT careertrellis.purge_expired_account_erasures(:limit)"
                    ),
                    {"limit": selected_limit},
                )
        except SQLAlchemyError as exc:
            raise AccountErasureUnavailable() from exc
        return int(count or 0)


def claim_next_account_erasure(
    worker_id: object,
    *,
    engine: Engine | None = None,
    lease_seconds: int = DEFAULT_ACCOUNT_ERASURE_LEASE_SECONDS,
) -> AccountErasureClaim | None:
    """Claim through the opaque cross-owner broker and close its transaction."""
    parameters = {
        "worker_id": _uuid(worker_id, "Worker identifier"),
        "lease_seconds": _lease_seconds(lease_seconds),
    }
    selected_engine = engine or runtime_postgres_engine()
    try:
        with selected_engine.begin() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM careertrellis.claim_next_account_erasure("
                    "CAST(:worker_id AS uuid), :lease_seconds)"
                ),
                parameters,
            ).mappings().first()
    except SQLAlchemyError as exc:
        raise AccountErasureUnavailable() from exc
    if row is None:
        return None
    try:
        request_id = _uuid(row["request_id"], "Request identifier")
        owner_user_id = _positive_id(row["owner_user_id"], "Owner identifier")
        lease_token = _uuid(row["lease_token"], "Lease token")
        lease_expires_at = row["lease_expires_at"]
        if not isinstance(lease_expires_at, datetime):
            raise ValueError("The account-erasure lease is invalid.")
        return AccountErasureClaim(
            request_id=request_id,
            owner_user_id=owner_user_id,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AccountErasureUnavailable() from exc


def runtime_account_erasure_repository(
    *, engine: Engine | None = None
) -> PostgreSQLAccountErasureRepository:
    """Return the shared-only owner repository; local mode never opens SQLite."""
    try:
        if data_store_kind() != SHARED_DATA_STORE:
            raise AccountErasureUnavailable()
        return PostgreSQLAccountErasureRepository(
            current_owner_user_id(), engine=engine
        )
    except AccountErasureUnavailable:
        raise
    except Exception as exc:
        raise AccountErasureUnavailable() from exc
