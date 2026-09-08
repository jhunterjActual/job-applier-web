"""Asynchronous admission coordinator and committed PostgreSQL boundary.

Construction is explicit. The application's default-disabled factory does not
read candidate credentials, build transports or connect candidate databases.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from account_session_candidate import (AccountSessionCandidate, MAX_LOCAL_ID,
    SessionCapacity, SessionInvalid, StoreUnavailable)
from workos_transport_candidate import (API, ProviderRejected, ProviderUnavailable,
    bounded_string, strict_json)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@dataclass(frozen=True, repr=False)
class Flow:
    id: str
    purpose: str
    state: str
    verifier: str
    browser_digest: str
    deadline: int
    realm: str
    provider_binding: str

    def parameters(self):
        return {'rid': self.id, 'proof': digest(json.dumps(self.__dict__, sort_keys=True, separators=(',', ':')))}


@dataclass(frozen=True, repr=False)
class AdmissionOutcome:
    status: str
    cookie: str = ''
    authorization_url: str = ''


class AsyncAdmissionStore:
    def __init__(self, broker, runtime):
        if (not isinstance(broker, AsyncEngine) or not isinstance(runtime, AsyncEngine)
                or broker.url.username != 'careertrellis_admission_broker'
                or runtime.url.username != 'careertrellis_runtime'):
            raise ValueError('Explicit restricted candidate engines required')
        self.broker, self.runtime = broker, runtime

    async def _query(self, engine, sql, parameters=None, *, subject=None, mapping=False):
        try:
            async with asyncio.timeout(5):
                async with engine.begin() as connection:
                    await connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
                    await connection.exec_driver_sql("SET LOCAL statement_timeout = '3s'")
                    if subject is not None:
                        await connection.execute(text("SELECT set_config('careertrellis.workos_user_id', :subject, true)"),
                                                 {'subject': subject})
                    result = await connection.execute(text(sql), parameters or {})
                    value = result.mappings().one_or_none() if mapping else result.scalar_one()
                    value = dict(value) if mapping and value is not None else value
                # In particular, a transaction-exit/COMMIT error cannot return activation.
                return value
        except (SQLAlchemyError, TimeoutError, OSError):
            raise StoreUnavailable() from None

    async def availability(self):
        return await self._query(self.broker, 'SELECT careertrellis_admission.availability()')

    async def budget(self, lane, browser, network):
        return await self._query(self.broker,
            'SELECT careertrellis_admission.http_budget(:lane,:browser,:network)',
            {'lane': lane, 'browser': browser, 'network': network})

    async def session_guard(self, key, expires, operation, snapshot=None):
        return await self._query(self.broker,
            'SELECT careertrellis_admission.http_session_guard(:key,:expires,:operation,:snapshot)',
            {'key': key, 'expires': datetime.fromtimestamp(expires, timezone.utc),
             'operation': operation, 'snapshot': snapshot})

    async def context(self):
        return await self._query(self.runtime, 'SELECT careertrellis.admission_session_context()')

    async def reserve(self, flow):
        return await self._query(self.broker,
            'SELECT careertrellis_admission.reserve(:rid,:proof,:deadline,:purpose,:realm)',
            {**flow.parameters(), 'deadline': datetime.fromtimestamp(flow.deadline, timezone.utc),
             'purpose': flow.purpose, 'realm': flow.realm})

    async def claim(self, flow, code, attempt):
        return await self._query(self.broker, 'SELECT careertrellis_admission.claim(:rid,:proof,:digest,:attempt)',
                                 {**flow.parameters(), 'digest': digest(code), 'attempt': attempt})

    async def finish(self, flow, attempt, identity):
        return await self._query(self.broker, 'SELECT careertrellis_admission.finish(:rid,:proof,:attempt,:subject,:realm)',
            {**flow.parameters(), 'attempt': attempt, 'subject': identity.subject, 'realm': identity.generation})

    async def result(self, flow, cancel=False):
        return await self._query(self.broker, 'SELECT careertrellis_admission.result(:rid,:proof,:cancel)',
                                 {**flow.parameters(), 'cancel': cancel})

    async def mapping(self, subject, local_id):
        return await self._query(self.runtime,
            'SELECT id,role FROM careertrellis.app_users WHERE id=:uid AND workos_user_id=:subject AND disabled_at IS NULL',
            {'uid': local_id, 'subject': subject}, subject=subject, mapping=True)


def build_candidate(*, enabled=False, factory):
    if type(enabled) is not bool:
        raise ValueError('Explicit candidate enablement required')
    return factory() if enabled else None


class AdmissionAdapterCandidate(AccountSessionCandidate):
    def __init__(self, store, provider, codec, *, owner_subject, flow_key, redirect_uri):
        super().__init__(store, provider, codec, owner_subject=owner_subject)
        parsed = urlsplit(redirect_uri)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.path != '/auth/callback'
                or parsed.query or parsed.fragment):
            raise ValueError('Exact configured HTTPS callback required')
        self.flow_seal, self.redirect_uri = Fernet(flow_key), redirect_uri

    def _open_flow(self, cookie, browser):
        if not bounded_string(browser, 43) or len(browser) != 43 or not bounded_string(cookie, 2000):
            raise ValueError()
        data = strict_json(self.flow_seal.decrypt(cookie.encode('ascii'), ttl=900))
        if not isinstance(data, dict) or set(data) != set(Flow.__dataclass_fields__):
            raise ValueError()
        flow = Flow(**data)
        if not bounded_string(flow.id, 36):
            raise ValueError()
        UUID(flow.id)
        if (flow.purpose not in ('signup', 'login') or flow.realm != self.codec.generation
                or flow.provider_binding != self.codec.provider_binding
                or not bounded_string(flow.state, 43) or len(flow.state) != 43
                or not bounded_string(flow.verifier, 43) or len(flow.verifier) != 43
                or not isinstance(flow.browser_digest, str)
                or not hmac.compare_digest(flow.browser_digest, digest(browser))
                or type(flow.deadline) is not int or not time.time() < flow.deadline <= time.time() + 900):
            raise ValueError()
        return flow

    async def begin(self, browser, purpose, *, different_account=False):
        try:
            if not bounded_string(browser, 43) or len(browser) != 43 or purpose not in ('signup', 'login'):
                return AdmissionOutcome('invalid')
            await self.context()
            flow = Flow(str(uuid4()), purpose, secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                        digest(browser), int(time.time()) + 900, self.codec.generation, self.codec.provider_binding)
            result = await self.store.reserve(flow)
            if result['status'] != 'reserved':
                return AdmissionOutcome(result['status'])
            cookie = self.flow_seal.encrypt(json.dumps(flow.__dict__, separators=(',', ':')).encode()).decode('ascii')
            challenge = jwt_pkce(flow.verifier)
            parameters = {
                'client_id': self.provider.settings.client_id, 'redirect_uri': self.redirect_uri,
                'response_type': 'code', 'provider': 'authkit', 'state': flow.state,
                'code_challenge': challenge, 'code_challenge_method': 'S256', 'prompt': 'login',
                'screen_hint': 'sign-up' if purpose == 'signup' else 'sign-in',
            }
            if different_account:
                parameters['provider_query_params[prompt]'] = 'select_account'
            url = API + '/user_management/authorize?' + urlencode(parameters)
            return AdmissionOutcome('reserved', cookie, url)
        except StoreUnavailable:
            return AdmissionOutcome('unavailable')

    async def status(self, cookie, browser, *, cancel=False):
        try:
            flow = self._open_flow(cookie, browser)
            return AdmissionOutcome((await self.store.result(flow, cancel))['status'])
        except StoreUnavailable:
            return AdmissionOutcome('unavailable')
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError):
            return AdmissionOutcome('invalid')

    async def complete(self, cookie, browser, state, code='', *, cancelled=False):
        try:
            async with asyncio.timeout(20):
                flow = self._open_flow(cookie, browser)
                if not bounded_string(state, 43) or not hmac.compare_digest(flow.state, state):
                    return AdmissionOutcome('invalid')
                if cancelled:
                    return AdmissionOutcome((await self.store.result(flow, True))['status'])
                if not bounded_string(code, 2048):
                    return AdmissionOutcome('invalid')
                await self.context()
                attempt = str(uuid4())
                claimed = await self.store.claim(flow, code, attempt)
                if claimed['status'] != 'exchange':
                    # Durable replay is status only; it cannot recreate provider evidence/cookie.
                    return AdmissionOutcome(claimed['status'])
                if flow.deadline <= time.time():
                    return AdmissionOutcome('expired')
                try:
                    identity = await self.provider.exchange(code, flow.verifier)
                except ProviderUnavailable:
                    return AdmissionOutcome('incomplete')
                if identity.generation != flow.realm or (flow.purpose == 'signup' and identity.subject == self.owner_subject):
                    return AdmissionOutcome('ineligible')
                await self.context()
                # Known oversize cannot consume a local account seat.
                self.codec.issue(identity, MAX_LOCAL_ID)
                result = await self.store.finish(flow, attempt, identity)
                if result['status'] not in ('activated', 'login'):
                    return AdmissionOutcome(result['status'])
                await self.context()
                await self.mapping(identity.subject, result['local_id'])
                if flow.deadline <= time.time() or identity.expires <= time.time():
                    return AdmissionOutcome('incomplete')
                return AdmissionOutcome(result['status'], self.codec.issue(identity, result['local_id']))
        except (StoreUnavailable, TimeoutError):
            return AdmissionOutcome('unavailable')
        except SessionCapacity:
            return AdmissionOutcome('service_limited')
        except (ProviderRejected, SessionInvalid):
            return AdmissionOutcome('ineligible')
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return AdmissionOutcome('invalid')


def jwt_pkce(verifier):
    import base64
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
