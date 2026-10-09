"""V2 session boundary; explicit closed-by-default HTTP runtime, no token store."""

from dataclasses import dataclass, replace
import json
import time
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken
from session_authorization import SessionAuthorization
from workos.session import seal_session_from_auth_response

from workos_transport_candidate import (API, SUBJECT, SESSION, VerifiedTokens,
    ProviderRejected, ProviderUnavailable, bounded_string, strict_json)


COOKIE_NAME = '__Host-careertrellis_session_v2'
MAX_COOKIE_VALUE = 3500
MAX_LOCAL_ID = 9223372036854775807


class SessionInvalid(Exception):
    pass


class SessionCapacity(Exception):
    pass


class StoreUnavailable(Exception):
    def __init__(self):
        super().__init__('Account service unavailable.')


@dataclass(frozen=True, repr=False)
class SessionOutcome:
    status: str
    cookie: str = ''
    local_id: int | None = None
    role: str | None = None
    clear_cookie: bool = False
    logout_url: str = ''
    authorization: SessionAuthorization | None = None


@dataclass(frozen=True, repr=False)
class BoundSession:
    local_id: int
    generation: str
    subject: str
    session_id: str
    provider: str
    issued: int
    expires: int
    provider_binding: str
    epoch: int | None = None
    incarnation: str | None = None


class SessionCodec:
    def __init__(self, envelope_key, provider_key, *, key_id, generation, lifetime_seconds, provider_binding, require_epoch=False):
        envelope_key = envelope_key.encode('ascii') if isinstance(envelope_key, str) else envelope_key
        provider_key = provider_key.encode('ascii') if isinstance(provider_key, str) else provider_key
        if (not bounded_string(key_id, 32) or not bounded_string(generation, 128)
                or not bounded_string(provider_binding, 64) or len(provider_binding) != 64
                or type(lifetime_seconds) is not int or not 60 <= lifetime_seconds <= 86400
                or envelope_key == provider_key):
            raise ValueError('Invalid candidate session configuration')
        self._seal, self._provider_seal = Fernet(envelope_key), Fernet(provider_key)
        self._provider_key = provider_key.decode('ascii') if isinstance(provider_key, bytes) else provider_key
        self.key_id, self.generation, self.lifetime_seconds = key_id, generation, lifetime_seconds
        self.provider_binding = provider_binding
        self.require_epoch = require_epoch

    def provider_seal(self, identity):
        # Compatible pinned SDK format, minimal verified user; no email/profile/upstream tokens.
        return seal_session_from_auth_response(access_token=identity.access_token,
            refresh_token=identity.refresh_token,
            user={'id': identity.subject, 'email_verified': True},
            cookie_password=self._provider_key)

    def pack(self, session):
        value = self._seal.encrypt(json.dumps({'v': 2, 'kind': 'account_session',
            'kid': self.key_id, 'data': session.__dict__}, separators=(',', ':')).encode()).decode('ascii')
        header = f'{COOKIE_NAME}={value}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age={self.lifetime_seconds}'
        if len(value) > MAX_COOKIE_VALUE or len(header.encode('ascii')) > 4096:
            raise SessionCapacity()
        return value

    def issue(self, identity, local_id, *, epoch=None, incarnation=None):
        if (type(local_id) is not int or not 0 < local_id <= MAX_LOCAL_ID
                or identity.generation != self.generation or identity.provider_binding != self.provider_binding):
            raise SessionInvalid()
        now = int(time.time())
        if self.require_epoch:
            try:
                SessionAuthorization(local_id, identity.subject, identity.provider_binding, epoch, incarnation)
            except (ValueError, TypeError, AttributeError):
                raise SessionInvalid() from None
        return self.pack(BoundSession(local_id, identity.generation, identity.subject,
            identity.session_id, self.provider_seal(identity), now, now + self.lifetime_seconds, identity.provider_binding,
            epoch, incarnation))

    def open(self, cookie):
        try:
            if not bounded_string(cookie, MAX_COOKIE_VALUE):
                raise ValueError()
            raw = self._seal.decrypt(cookie.encode('ascii'), ttl=self.lifetime_seconds)
            envelope = strict_json(raw)
            if (not isinstance(envelope, dict) or set(envelope) != {'v', 'kind', 'kid', 'data'}
                    or type(envelope['v']) is not int or envelope['v'] != 2
                    or envelope['kind'] != 'account_session' or envelope['kid'] != self.key_id
                    or not isinstance(envelope['data'], dict)
                    or set(envelope['data']) != set(BoundSession.__dataclass_fields__)):
                raise ValueError()
            session = BoundSession(**envelope['data'])
            if self.require_epoch:
                SessionAuthorization(session.local_id, session.subject, session.provider_binding, session.epoch, session.incarnation)
            now = time.time()
            if (type(session.local_id) is not int or not 0 < session.local_id <= MAX_LOCAL_ID
                    or session.generation != self.generation
                    or session.provider_binding != self.provider_binding
                    or not bounded_string(session.subject, 128, SUBJECT)
                    or not bounded_string(session.session_id, 168, SESSION)
                    or not bounded_string(session.provider, MAX_COOKIE_VALUE)
                    or type(session.issued) is not int or type(session.expires) is not int
                    or not session.issued <= now < session.expires
                    or not 0 < session.expires - session.issued <= self.lifetime_seconds):
                raise ValueError()
            return session
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise SessionInvalid() from None

    def provider_open(self, value):
        try:
            if not bounded_string(value, 4096):
                raise ValueError()
            data = strict_json(self._provider_seal.decrypt(value.encode('ascii')))
            if (not isinstance(data, dict) or data.get('impersonator') is not None
                    or not isinstance(data.get('user'), dict)
                    or data['user'].get('email_verified') is not True
                    or not bounded_string(data['user'].get('id'), 128, SUBJECT)
                    or not bounded_string(data.get('access_token'), 16384)
                    or not bounded_string(data.get('refresh_token'), 8192)):
                raise ValueError()
            return data
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise SessionInvalid() from None


class AccountSessionCandidate:
    def __init__(self, store, provider, codec, *, owner_subject):
        if (not bounded_string(owner_subject, 128, SUBJECT)
                or codec.generation != provider.settings.generation or codec.provider_binding != provider.settings.binding):
            raise ValueError('Invalid candidate account configuration')
        self.store, self.provider, self.codec = store, provider, codec
        self.owner_subject = owner_subject
        # Installed by the closed-by-default HTTP runtime; isolated unit slices
        # may omit it, but the application factory never does.
        self.coordination = None

    async def context(self):
        if self.provider.settings.binding != self.codec.provider_binding:
            raise StoreUnavailable()
        context = await self.store.context()
        if (not isinstance(context, dict) or context.get('adapter_ready') is not True
                or type(context.get('minimum_session_version')) is not int
                or context['minimum_session_version'] != 2
                or context.get('maintenance') is not False
                or context.get('realm') != self.codec.generation):
            raise StoreUnavailable()

    async def mapping(self, subject, local_id):
        # Implementations must query exact local ID + subject under identity-scoped RLS.
        row = await self.store.mapping(subject, local_id)
        if (not row or type(row.get('id')) is not int or row['id'] != local_id
                or row.get('role') not in ('owner', 'user')
                or (subject == self.owner_subject) != (row['role'] == 'owner')):
            raise SessionInvalid()
        return row

    async def session_mapping(self, session):
        if not self.codec.require_epoch:
            return await self.mapping(session.subject, session.local_id)
        row = await self.store.session_mapping(session)
        if (not row or row.get('id') != session.local_id or row.get('role') not in ('user','owner')
                or (session.subject == self.owner_subject) != (row['role'] == 'owner')):
            raise SessionInvalid()
        return row

    def authorization(self, session):
        if not self.codec.require_epoch:
            return None
        return SessionAuthorization(session.local_id, session.subject, session.provider_binding,
            session.epoch, session.incarnation)

    async def authenticate(self, cookie, *, refresh=False):
        try:
            session = self.codec.open(cookie)  # Legacy never reaches mapping or refresh.
            await self.context()
            if self.coordination and not await self.coordination.check(session):
                raise SessionInvalid()
            data = self.codec.provider_open(session.provider)
            claims = await self.provider.verify(data['access_token'], data['user']['id'], allow_expired=True)
            if claims['sub'] != session.subject or claims['sid'] != session.session_id:
                raise SessionInvalid()
            row = await self.session_mapping(session)
            if session.expires <= time.time():
                raise SessionInvalid()
            if claims['exp'] <= time.time():
                if not refresh:
                    return SessionOutcome('refresh_required')
                if self.coordination and not await self.coordination.claim(session, data):
                    # A competing response may carry the winner's new cookie.
                    # Never clear/overwrite it or retry this token snapshot.
                    return SessionOutcome('restart_required')
                # One explicit refresh; no retry loop and no automatic fresh login.
                identity = await self.provider.refresh(data['refresh_token'])
                if (identity.subject != session.subject or identity.session_id != session.session_id
                        or identity.generation != session.generation or identity.provider_binding != session.provider_binding):
                    raise SessionInvalid()
                await self.context()
                if self.coordination and not await self.coordination.check(session):
                    return SessionOutcome('restart_required')
                row = await self.session_mapping(session)
                if session.expires <= time.time():
                    raise SessionInvalid()
                if identity.expires <= time.time():
                    return SessionOutcome('unavailable')
                cookie = self.codec.pack(replace(session, provider=self.codec.provider_seal(identity)))
                return SessionOutcome('authenticated', cookie, row['id'], row['role'], authorization=self.authorization(session))
            await self.context()
            if self.coordination and not await self.coordination.check(session):
                raise SessionInvalid()
            row = await self.session_mapping(session)
            if session.expires <= time.time():
                raise SessionInvalid()
            if claims['exp'] <= time.time():
                return SessionOutcome('refresh_required')
            return SessionOutcome('authenticated', local_id=row['id'], role=row['role'], authorization=self.authorization(session))
        except (ProviderUnavailable, StoreUnavailable):
            return SessionOutcome('unavailable')
        except SessionCapacity:
            return SessionOutcome('service_limited')
        except (SessionInvalid, ProviderRejected):
            return SessionOutcome('unauthenticated', clear_cookie=True)

    async def logout_evidence(self, cookie, *, legacy=False):
        """Return a verified provider logout URL, not a claim that logout occurred.

        An explicitly selected legacy cookie can support logout only. No local
        mapping is consulted and no account/session is created or refreshed.
        The later HTTP boundary owns CSRF, cookie clearing and redirect delivery.
        """
        try:
            if legacy:
                data = self.codec.provider_open(cookie)
                subject, sid = data['user']['id'], None
            else:
                session = self.codec.open(cookie)
                data = self.codec.provider_open(session.provider)
                subject, sid = session.subject, session.session_id
            claims = await self.provider.verify(data['access_token'], data['user']['id'], allow_expired=True)
            if claims['sub'] != subject or (sid is not None and claims['sid'] != sid):
                raise SessionInvalid()
            return SessionOutcome('logout_ready', clear_cookie=True,
                logout_url=API + '/user_management/sessions/logout?' + urlencode({'session_id': claims['sid']}))
        except ProviderUnavailable:
            return SessionOutcome('unavailable')
        except (ProviderRejected, SessionInvalid):
            return SessionOutcome('unauthenticated', clear_cookie=True)
