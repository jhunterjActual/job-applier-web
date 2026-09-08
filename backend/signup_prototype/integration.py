"""Isolated real-schema adapter experiment. No application or WorkOS imports.

Only the built-in synthetic provider and an exact loopback test database are
accepted. Browser input can never supply identity/verification assertions.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


DATABASE = 'ct_signup_integration'
REALM = 'synthetic-realm-v1'
TOKEN = re.compile(r'^[A-Za-z0-9_-]{43}$')
SUBJECT = re.compile(r'^user_[A-Za-z0-9_]{6,120}$')
MAX_COOKIE_VALUE = 3500  # Leave room for the cookie name and security attributes.


class MappingUnavailable(Exception):
    """A database outage is not invalid input or proof that a session ended."""


def isolated_engine(engine):
    url = engine.url
    if (url.drivername != 'postgresql+psycopg' or url.host != '127.0.0.1'
            or url.database != DATABASE or url.query):
        raise ValueError('An exact isolated loopback integration database is required')
    return engine


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


@dataclass(frozen=True, repr=False)
class Flow:
    id: str
    purpose: str
    state: str
    verifier: str
    browser_digest: str
    deadline: int
    realm: str = REALM

    def parameters(self):
        return {'rid': self.id, 'proof': digest(packed(self.__dict__))}


@dataclass(frozen=True, repr=False)
class Outcome:
    status: str
    cookie: str = ''
    state: str = ''


@dataclass(frozen=True, repr=False)
class Verified:
    subject: str
    realm: str
    provider_session: str
    email_verified: bool = True
    impersonated: bool = False


class SyntheticProvider:
    """In-memory fake codes/sessions. No transport, credentials or real SDK."""

    def __init__(self):
        self._seal = Fernet(Fernet.generate_key())
        self._lock = threading.Lock()
        self._flows = {}
        self._codes = {}
        self._revoked = set()
        self.exchanges = 0
        self.before_exchange = lambda: None

    def register(self, flow):
        with self._lock:
            self._flows[flow.state] = digest(flow.verifier)

    def issue(self, state, subject='user_synthetic_new', *, verified=True,
              impersonated=False, realm=REALM):
        with self._lock:
            challenge = self._flows[state]
            code = secrets.token_urlsafe(32)
            session = self._seal.encrypt(packed({
                'subject': subject, 'realm': realm, 'sid': str(uuid4()),
                'verified': verified, 'impersonated': impersonated,
            }).encode()).decode()
            self._codes[code] = (challenge, Verified(subject, realm, session, verified, impersonated))
            return code

    def exchange(self, code, verifier):
        with self._lock:
            self.exchanges += 1
            challenge, result = self._codes.pop(code)
        self.before_exchange()
        if not hmac.compare_digest(challenge, digest(verifier)):
            raise ValueError('synthetic exchange rejected')
        return result

    def authenticate(self, cookie):
        data = json.loads(self._seal.decrypt(cookie.encode()))
        if (data['sid'] in self._revoked or data['verified'] is not True
                or data['impersonated'] is not False or data['realm'] != REALM
                or not SUBJECT.fullmatch(data['subject'])):
            raise ValueError('synthetic session rejected')
        return data

    def refresh(self, cookie):
        data = self.authenticate(cookie)
        return self._seal.encrypt(packed(data).encode()).decode()

    def logout(self, cookie):
        data = self.authenticate(cookie)
        self._revoked.add(data['sid'])


class AdmissionStore:
    def __init__(self, broker_engine, runtime_engine):
        self.broker = isolated_engine(broker_engine)
        self.runtime = isolated_engine(runtime_engine)

    def call(self, sql, parameters=None):
        try:
            with self.broker.begin() as connection:
                connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
                result = connection.execute(text(sql), parameters or {}).scalar_one()
            # A failure of __exit__/COMMIT is not reported as successful activation.
            return result
        except SQLAlchemyError:
            return {'status': 'unavailable'}

    def availability(self):
        return self.call('SELECT ct_admission.availability()')

    def reserve(self, flow):
        return self.call('SELECT ct_admission.reserve(:rid, :proof, :deadline, :purpose, :realm)',
                         {**flow.parameters(), 'deadline': datetime.fromtimestamp(flow.deadline, timezone.utc),
                          'purpose': flow.purpose, 'realm': flow.realm})

    def claim(self, flow, code, attempt):
        return self.call('SELECT ct_admission.claim(:rid, :proof, :digest, :attempt)',
                         {**flow.parameters(), 'digest': digest(code), 'attempt': attempt})

    def finish(self, flow, attempt, identity):
        return self.call('SELECT ct_admission.finish(:rid, :proof, :attempt, :subject, :realm)',
                         {**flow.parameters(), 'attempt': attempt,
                          'subject': identity.subject, 'realm': identity.realm})

    def result(self, flow, cancel=False):
        return self.call('SELECT ct_admission.result(:rid, :proof, :cancel)',
                         {**flow.parameters(), 'cancel': cancel})

    def mapping(self, subject):
        try:
            with self.runtime.begin() as connection:
                connection.execute(text("SELECT set_config('careertrellis.workos_user_id', :subject, true)"),
                                   {'subject': subject})
                row = connection.execute(text('SELECT id, role FROM careertrellis.app_users '
                    'WHERE workos_user_id = :subject AND disabled_at IS NULL'),
                    {'subject': subject}).mappings().one_or_none()
                return dict(row) if row else None
        except SQLAlchemyError:
            raise MappingUnavailable() from None


class IntegrationAdapter:
    def __init__(self, store, provider, key, *, owner_subject='user_synthetic_owner', clock=time.time):
        if type(provider) is not SyntheticProvider or not SUBJECT.fullmatch(owner_subject):
            raise ValueError('Only the synthetic integration provider is permitted')
        self.store, self.provider = store, provider
        self.seal, self.clock = Fernet(key), clock
        self.owner_subject = owner_subject

    def _encrypt(self, kind, data):
        return self.seal.encrypt_at_time(packed({'v': 1, 'kind': kind, 'data': data}).encode(),
                                         int(self.clock())).decode()

    def _decrypt(self, token, kind, ttl):
        if not isinstance(token, str) or not 1 <= len(token) <= 4096:
            raise ValueError('invalid envelope')
        value = json.loads(self.seal.decrypt_at_time(token.encode(), ttl=ttl, current_time=int(self.clock())))
        if set(value) != {'v', 'kind', 'data'} or value['v'] != 1 or value['kind'] != kind:
            raise ValueError('invalid envelope')
        return value['data']

    def open_flow(self, cookie, browser):
        if not isinstance(browser, str) or not TOKEN.fullmatch(browser):
            raise ValueError('invalid browser')
        data = self._decrypt(cookie, 'flow', 900)
        if set(data) != set(Flow.__dataclass_fields__):
            raise ValueError('invalid flow')
        flow = Flow(**data)
        UUID(flow.id)
        if (flow.purpose not in ('signup', 'login') or flow.realm != REALM
                or not TOKEN.fullmatch(flow.state) or not TOKEN.fullmatch(flow.verifier)
                or not hmac.compare_digest(flow.browser_digest, digest(browser))
                or type(flow.deadline) is not int or flow.deadline <= self.clock()
                or flow.deadline > self.clock() + 900):
            raise ValueError('invalid or expired flow')
        return flow

    def begin(self, browser, purpose='signup'):
        if not isinstance(browser, str) or not TOKEN.fullmatch(browser) or purpose not in ('signup', 'login'):
            return Outcome('invalid')
        flow = Flow(str(uuid4()), purpose, secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                    digest(browser), int(self.clock()) + 900)
        status = self.store.reserve(flow)['status']
        if status != 'reserved':
            return Outcome(status)
        self.provider.register(flow)
        return Outcome('reserved', self._encrypt('flow', flow.__dict__), flow.state)

    def complete(self, cookie, browser, state, code='', *, cancelled=False):
        try:
            flow = self.open_flow(cookie, browser)
            if (not isinstance(state, str) or not TOKEN.fullmatch(state)
                    or not hmac.compare_digest(flow.state, state)):
                return Outcome('invalid')
            if cancelled:
                return Outcome(self.store.result(flow, cancel=True)['status'])
            if not isinstance(code, str) or not 1 <= len(code) <= 2048:
                return Outcome('invalid')
            attempt = str(uuid4())
            claim = self.store.claim(flow, code, attempt)
            if claim['status'] != 'exchange':
                # Terminal status may be recoverable; no fresh identity/session evidence.
                status = self.store.result(flow)['status'] if claim['status'] in ('activated', 'login') else claim['status']
                return Outcome(status)
            try:
                identity = self.provider.exchange(code, flow.verifier)
            except Exception:
                return Outcome('incomplete')
            if (identity.email_verified is not True or identity.impersonated is not False
                    or identity.realm != REALM or not SUBJECT.fullmatch(identity.subject)
                    or (flow.purpose == 'signup' and identity.subject == self.owner_subject)):
                self.store.result(flow, cancel=True)
                return Outcome('ineligible')
            result = self.store.finish(flow, attempt, identity)
            if result['status'] not in ('activated', 'login'):
                return Outcome(result['status'])
            current = self.store.mapping(identity.subject)
            if (not current or current['id'] != result['local_id']
                    or (identity.subject == self.owner_subject) != (current['role'] == 'owner')):
                return Outcome('ineligible')
            session = self._encrypt('session', {'local_id': current['id'], 'realm': REALM,
                                                'provider': identity.provider_session})
            if len(session) > MAX_COOKIE_VALUE:
                return Outcome('incomplete')
            return Outcome(result['status'], session)
        except MappingUnavailable:
            return Outcome('unavailable')
        except (InvalidToken, ValueError, TypeError, KeyError):
            return Outcome('invalid')

    def result(self, cookie, browser):
        try:
            return Outcome(self.store.result(self.open_flow(cookie, browser))['status'])
        except (InvalidToken, ValueError, TypeError, KeyError):
            return Outcome('invalid')

    def _session(self, cookie):
        data = self._decrypt(cookie, 'session', 86400)
        if (set(data) != {'local_id', 'realm', 'provider'} or data['realm'] != REALM
                or type(data['local_id']) is not int or data['local_id'] <= 0):
            raise ValueError('invalid session')
        identity = self.provider.authenticate(data['provider'])
        return data, identity

    def authenticate(self, cookie, *, refresh=False):
        try:
            data, identity = self._session(cookie)
            current = self.store.mapping(identity['subject'])
            if (not current or current['id'] != data['local_id']
                    or (identity['subject'] == self.owner_subject) != (current['role'] == 'owner')):
                return Outcome('unauthenticated')
            if refresh:
                refreshed = self.provider.refresh(data['provider'])
                checked = self.provider.authenticate(refreshed)
                if checked['subject'] != identity['subject'] or checked['sid'] != identity['sid']:
                    return Outcome('unauthenticated')
                wrapped = self._encrypt('session', {**data, 'provider': refreshed})
                if len(wrapped) > MAX_COOKIE_VALUE:
                    return Outcome('unavailable')
                return Outcome('authenticated', wrapped)
            return Outcome('authenticated')
        except MappingUnavailable:
            return Outcome('unavailable')
        except (InvalidToken, ValueError, TypeError, KeyError):
            return Outcome('unauthenticated')

    def logout(self, cookie):
        try:
            data, _ = self._session(cookie)
            self.provider.logout(data['provider'])
            return Outcome('signed_out')
        except (InvalidToken, ValueError, TypeError, KeyError):
            return Outcome('unauthenticated')
