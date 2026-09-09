"""WorkOS transport candidate. No environment reads or import-time clients.

Only fixed HTTPS authentication/client-scoped JWKS endpoints are used. Tests
inject an HTTPX transport, never a different production endpoint or SDK globals.
No automatic exchange/refresh retry; cancellation leaves consumption uncertain.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import hashlib
import re
import time

import httpx
import jwt


SUBJECT = re.compile(r'user_[A-Za-z0-9_]{6,120}')
SESSION = re.compile(r'session_[A-Za-z0-9_]{6,160}')
CLIENT = re.compile(r'client_[A-Za-z0-9_]{6,120}')
PKCE = re.compile(r'[A-Za-z0-9._~-]{43,128}')
API = 'https://api.workos.com'


class ProviderUnavailable(Exception):
    def __init__(self):
        super().__init__('Authentication service unavailable.')


class ProviderRejected(Exception):
    def __init__(self):
        super().__init__('Authentication evidence rejected.')


def bounded_string(value, maximum, pattern=None):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and value.isascii() and not any(ord(c) < 33 or ord(c) == 127 for c in value)
            and (pattern is None or pattern.fullmatch(value) is not None))


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate JSON member')
            value[key] = item
        return value
    def invalid_constant(_value):
        raise ValueError('Invalid JSON constant')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


@dataclass(frozen=True, repr=False)
class ProviderSettings:
    client_id: str
    client_secret: str
    issuer: str
    generation: str
    audience: str | None = None

    @property
    def binding(self):
        # Configuration continuity even if an operator accidentally reuses a realm.
        return hashlib.sha256(json.dumps([self.client_id,self.issuer,self.audience,self.generation],
                                         separators=(',', ':')).encode()).hexdigest()

    def __post_init__(self):
        # AuthKit session issuers are client-scoped, just like the fixed JWKS
        # endpoint. Never accept a bare origin, another client or URL aliases.
        if (not bounded_string(self.client_id, 128, CLIENT)
                or not bounded_string(self.client_secret, 512)
                or not bounded_string(self.generation, 128)
                or self.issuer != API + '/user_management/' + self.client_id
                or (self.audience is not None and not bounded_string(self.audience, 256))):
            raise ValueError('Invalid candidate provider configuration')


@dataclass(frozen=True, repr=False)
class VerifiedTokens:
    subject: str
    session_id: str
    generation: str
    access_token: str
    refresh_token: str
    expires: int
    provider_binding: str


class WorkOSTransportCandidate:
    def __init__(self, settings, *, transport=None, total_seconds=10, max_inflight=8,
                 cache_seconds=60, rotation_seconds=5):
        if (not isinstance(settings, ProviderSettings) or not 0 < total_seconds <= 15
                or type(max_inflight) is not int or not 1 <= max_inflight <= 32
                or not 0 <= rotation_seconds <= cache_seconds <= 300):
            raise ValueError('Invalid candidate transport bounds')
        self.settings = settings
        self.total_seconds = total_seconds
        self.max_inflight = max_inflight
        self.cache_seconds = cache_seconds
        self.rotation_seconds = rotation_seconds
        self._inflight = 0
        self._keys = {}
        self._fetched = None
        self._last_fetch_attempt = None
        self._key_lock = asyncio.Lock()
        limits = httpx.Limits(max_connections=max_inflight, max_keepalive_connections=max_inflight)
        self._client = httpx.AsyncClient(
            transport=transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False, limits=limits),
            follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(connect=2, read=3, write=3, pool=1),
            limits=limits,
            headers={'Accept': 'application/json', 'Accept-Encoding': 'identity'},
        )

    async def aclose(self):
        await self._client.aclose()

    @asynccontextmanager
    async def _operation(self):
        # Single event-loop ownership; reject excess work, do not queue unbounded tasks.
        if self._inflight >= self.max_inflight:
            raise ProviderUnavailable()
        self._inflight += 1
        try:
            async with asyncio.timeout(self.total_seconds):
                yield
        except (TimeoutError, httpx.HTTPError, OSError):
            raise ProviderUnavailable() from None
        finally:
            self._inflight -= 1

    async def _json(self, method, url, body=None):
        async with self._client.stream(method, url, json=body) as response:
            # Only bounded machine-readable invalid_grant establishes terminal
            # refresh/code rejection. Configuration/unknown errors are unavailable.
            error_response = method == 'POST' and response.status_code == 400
            if response.status_code != 200 and not error_response:
                raise ProviderUnavailable()
            if (response.headers.get('content-encoding', 'identity') != 'identity'
                    or response.headers.get('content-type', '').split(';')[0].strip() != 'application/json'):
                raise ProviderUnavailable()
            raw = bytearray()
            async for chunk in response.aiter_raw():
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise ProviderUnavailable()
            try:
                value = strict_json(raw)
                if not isinstance(value, dict):
                    raise ValueError()
            except (ValueError, UnicodeError, RecursionError):
                raise ProviderUnavailable() from None
            if error_response:
                if value.get('error') == 'invalid_grant':
                    raise ProviderRejected()
                raise ProviderUnavailable()
            return value

    async def _key(self, kid):
        async with self._key_lock:
            now = time.monotonic()
            if self._fetched is not None and now - self._fetched < self.cache_seconds and kid in self._keys:
                return self._keys[kid]
            if self._last_fetch_attempt is not None and now - self._last_fetch_attempt < self.rotation_seconds:
                # Includes failed fetches and unknown-kid floods; no stale-key fallback.
                raise ProviderUnavailable()
            self._last_fetch_attempt = now
            document = await self._json('GET', API + '/sso/jwks/' + self.settings.client_id)
            try:
                entries = document['keys']
                if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
                    raise ValueError()
                keys = {}
                for entry in entries:
                    if (not isinstance(entry, dict) or entry.get('kty') != 'RSA'
                            or entry.get('use', 'sig') != 'sig' or entry.get('alg', 'RS256') != 'RS256'
                            or entry.get('key_ops', ['verify']) != ['verify']
                            or not bounded_string(entry.get('kid'), 160)
                            or entry['kid'] in keys or any(name in entry for name in ('d','p','q','dp','dq','qi','oth'))
                            or not bounded_string(entry.get('n'), 700)
                            or not bounded_string(entry.get('e'), 8)):
                        raise ValueError()
                    key = jwt.PyJWK.from_dict(entry, algorithm='RS256').key
                    if not 2048 <= key.key_size <= 4096:
                        raise ValueError()
                    keys[entry['kid']] = key
            except (ValueError, TypeError, KeyError, jwt.PyJWTError):
                raise ProviderUnavailable() from None
            self._keys, self._fetched = keys, time.monotonic()
            if kid not in keys:
                raise ProviderRejected()
            return keys[kid]

    async def _verify(self, access, subject, *, allow_expired=False):
        if not bounded_string(access, 16384) or not bounded_string(subject, 128, SUBJECT):
            raise ProviderRejected()
        try:
            # Header selects only a bounded kid within our fixed client JWKS.
            # Reject duplicate JSON members before PyJWT's normal JSON parser.
            segments = access.split('.')
            if len(segments) != 3:
                raise ValueError()
            header = strict_json(jwt.utils.base64url_decode(segments[0]))
            claims_shape = strict_json(jwt.utils.base64url_decode(segments[1]))
            if (not isinstance(header, dict) or not isinstance(claims_shape, dict)
                    or header.get('alg') != 'RS256' or not bounded_string(header.get('kid'), 160)
                    or any(name in header for name in ('crit', 'jku', 'jwk', 'x5u'))):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ProviderRejected() from None
        key = await self._key(header['kid'])
        try:
            claims = jwt.decode(access, key, algorithms=['RS256'], issuer=self.settings.issuer,
                audience=self.settings.audience,
                options={'require': ['sub', 'sid', 'iss', 'exp', 'iat'], 'verify_exp': False})
            now = time.time()
            if (claims['sub'] != subject or not bounded_string(claims['sid'], 168, SESSION)
                    or (self.settings.audience is None and 'aud' in claims)
                    or type(claims['iat']) is not int or type(claims['exp']) is not int
                    or claims['iat'] > now or claims['exp'] <= claims['iat']
                    or (not allow_expired and claims['exp'] <= now)):
                raise ValueError()
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise ProviderRejected() from None

    async def verify(self, access, subject, *, allow_expired=False):
        async with self._operation():
            return await self._verify(access, subject, allow_expired=allow_expired)

    async def _authenticate(self, body):
        result = await self._json('POST', API + '/user_management/authenticate', {
            **body, 'client_id': self.settings.client_id, 'client_secret': self.settings.client_secret,
        })
        user = result.get('user')
        if (not isinstance(user, dict) or user.get('email_verified') is not True
                or result.get('impersonator') is not None
                or not bounded_string(user.get('id'), 128, SUBJECT)
                or not bounded_string(result.get('refresh_token'), 8192)):
            raise ProviderRejected()
        claims = await self._verify(result.get('access_token'), user['id'])
        # Drop email/profile, upstream OAuth tokens, organization roles and diagnostics.
        return VerifiedTokens(user['id'], claims['sid'], self.settings.generation,
                              result['access_token'], result['refresh_token'], claims['exp'], self.settings.binding)

    async def exchange(self, code, verifier):
        if not bounded_string(code, 2048) or not bounded_string(verifier, 128, PKCE):
            raise ProviderRejected()
        async with self._operation():
            return await self._authenticate({'grant_type': 'authorization_code', 'code': code,
                                             'code_verifier': verifier})

    async def refresh(self, token):
        if not bounded_string(token, 8192):
            raise ProviderRejected()
        async with self._operation():
            return await self._authenticate({'grant_type': 'refresh_token', 'refresh_token': token})
