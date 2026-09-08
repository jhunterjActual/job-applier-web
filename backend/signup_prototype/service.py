"""Synthetic trusted-adapter seam, not an HTTP API or a WorkOS integration.

Only explicit, isolated engines are accepted. The eventual trusted adapter must
prove fresh registration intent, expiring browser/PKCE binding, provider evidence
and verified immutable subject; a browser must never supply that evidence itself.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


@dataclass(frozen=True, repr=False)
class SyntheticFlow:
    request_id: UUID
    browser_secret: str
    deadline: datetime


@dataclass(frozen=True, repr=False)
class SyntheticVerifiedIdentity:
    subject: str
    email_verified: bool = True
    impersonated: bool = False


def new_synthetic_flow() -> SyntheticFlow:
    return SyntheticFlow(uuid4(), secrets.token_urlsafe(32),
                         datetime.now(timezone.utc) + timedelta(minutes=15))


def _flow_parameters(flow: SyntheticFlow) -> dict:
    if (not isinstance(flow.request_id, UUID)
            or not isinstance(flow.browser_secret, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", flow.browser_secret)
            or not isinstance(flow.deadline, datetime)
            or flow.deadline.tzinfo is None):
        raise ValueError("Invalid synthetic flow")
    return {"request": flow.request_id,
            "proof": hashlib.sha256(flow.browser_secret.encode("ascii")).hexdigest(),
            "deadline": flow.deadline}


class AdmissionPrototype:
    def __init__(self, engine):
        url = engine.url
        if (url.get_backend_name() != "postgresql"
                or url.host not in {"127.0.0.1", "localhost", "::1"}
                or url.database != "ct_signup_prototype"):
            raise ValueError("An isolated local prototype database is required")
        self.engine = engine

    def _call(self, sql: str, parameters=None) -> dict:
        try:
            with self.engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                connection.execute(text("SET LOCAL statement_timeout = '5s'"))
                return connection.execute(text(sql), parameters or {}).scalar_one()
        except SQLAlchemyError:
            # Do not serialize SQL, parameters, credentials, or driver exceptions.
            # 'Unavailable' is NOT 'full'; a lost response may follow a commit.
            return {"status": "unavailable"}

    def availability(self) -> dict:
        return self._call("SELECT admission_prototype.availability()")

    def reserve(self, flow: SyntheticFlow) -> dict:
        return self._call("SELECT admission_prototype.reserve(:request, :proof, :deadline)",
                          _flow_parameters(flow))

    def activate(self, flow: SyntheticFlow, identity: SyntheticVerifiedIdentity) -> dict:
        if (identity.email_verified is not True or identity.impersonated is not False
                or not isinstance(identity.subject, str)
                or not re.fullmatch(r"user_[A-Za-z0-9_]{6,120}", identity.subject)):
            return {"status": "ineligible"}
        parameters = _flow_parameters(flow)
        parameters["subject"] = identity.subject
        return self._call("SELECT admission_prototype.activate(:request, :proof, :subject)",
                          parameters)

    def cancel(self, flow: SyntheticFlow) -> dict:
        return self._call("SELECT admission_prototype.cancel(:request, :proof)",
                          _flow_parameters(flow))
