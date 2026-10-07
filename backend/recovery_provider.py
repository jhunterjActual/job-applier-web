"""Bounded WorkOS recovery session effects, constructed by a protected caller.

Caller must hold the account and durably claim each effect before revoke. A
response is local attribution, not a signed receipt or permission to retry.
Final empty enumeration is required by the coordinator before release; it does
not prevent new provider logins. Local account hold/epoch fencing remains vital.
API: https://workos.com/docs/reference/authkit/session
POST200 {} contract: workos/workos-python tests/test_user_management.py.
"""
import asyncio
from dataclasses import dataclass
import time
from uuid import UUID

import httpx

from workos_transport_candidate import (
    API, SESSION, SUBJECT, ProviderSettings, bounded_string, strict_json,
)
from workos_removal import FACTOR


class SessionRevocationUncertain(Exception):
    def __init__(self):
        super().__init__('Provider session recovery is uncertain.')


def require(value):
    if not value:
        raise SessionRevocationUncertain()


@dataclass(frozen=True, repr=False)
class SessionObservation:
    provider_binding: str
    subject: str
    session_ids: tuple[str, ...]
    observed_at: int
    complete: bool = True


@dataclass(frozen=True, repr=False)
class SessionRevocation:
    provider_binding: str
    subject: str
    session_id: str
    operation: str
    completed_at: int
    status: str = 'revoked'


@dataclass(frozen=True, repr=False)
class FactorInventory:
    provider_binding: str
    subject: str
    factor_ids: tuple[str, ...]
    observed_at: int
    complete: bool = True


class WorkOSRecoverySessions:
    def __init__(self, settings, *, transport=None):
        require(isinstance(settings, ProviderSettings))
        self.binding = settings.binding
        self._inflight = 0
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
        self.client = httpx.AsyncClient(
            transport=transport or httpx.AsyncHTTPTransport(
                retries=0, trust_env=False, limits=limits),
            limits=limits, follow_redirects=False, trust_env=False, timeout=5,
            headers={'Authorization': 'Bearer ' + settings.client_secret,
                     'Accept': 'application/json', 'Accept-Encoding': 'identity'})

    async def aclose(self):
        await self.client.aclose()

    async def _request(self, method, path, *, body=None, params=None):
        require(self._inflight < 4)
        self._inflight += 1
        try:
            async with asyncio.timeout(10):
                async with self.client.stream(method, API + path, json=body, params=params) as response:
                    require(response.status_code == 200)
                    require(response.headers.get('content-encoding', 'identity') == 'identity')
                    require(response.headers.get('content-type', '').split(';')[0].strip() == 'application/json')
                    raw = bytearray()
                    async for chunk in response.aiter_raw():
                        require(len(raw) + len(chunk) <= 65536)
                        raw.extend(chunk)
                    value = strict_json(raw)
                    require(type(value) is dict)
                    return value
        except Exception:
            raise SessionRevocationUncertain() from None
        finally:
            self._inflight -= 1

    async def observe(self, subject):
        require(bounded_string(subject, 128, SUBJECT))
        try:
            async with asyncio.timeout(20):
                after = None
                cursors, ids = set(), set()
                ordered = []
                for _ in range(10):
                    params = {'limit': 100}
                    if after is not None:
                        params['after'] = after
                    value = await self._request('GET', '/user_management/users/' + subject + '/sessions', params=params)
                    require(value.get('object') == 'list' and type(value.get('data')) is list
                            and len(value['data']) <= 100)
                    for item in value['data']:
                        require(type(item) is dict and item.get('object') == 'session'
                                and item.get('user_id') == subject and item.get('status') == 'active'
                                and bounded_string(item.get('id'), 168, SESSION)
                                and item['id'] not in ids)
                        ids.add(item['id'])
                        ordered.append(item['id'])
                    require(type(value.get('list_metadata')) is dict and 'after' in value['list_metadata'])
                    after = value['list_metadata']['after']
                    if after is None:
                        return SessionObservation(self.binding, subject, tuple(ordered), int(time.time()))
                    require(bounded_string(after, 168, SESSION) and after not in cursors)
                    cursors.add(after)
                raise SessionRevocationUncertain()
        except Exception:
            raise SessionRevocationUncertain() from None

    async def revoke(self, subject, session_id, operation):
        require(bounded_string(subject, 128, SUBJECT) and bounded_string(session_id, 168, SESSION))
        try:
            require(type(operation) is str and str(UUID(operation)) == operation)
            async with asyncio.timeout(30):
                observation = await self.observe(subject)
                # Never accept a caller-supplied session ID without current ownership.
                require(session_id in observation.session_ids)
                value = await self._request('POST', '/user_management/sessions/revoke',
                                            body={'session_id': session_id})
                require(value == {})
                return SessionRevocation(self.binding, subject, session_id, operation, int(time.time()))
        except Exception:
            raise SessionRevocationUncertain() from None

    async def observe_factors(self, subject):
        """Membership only, never proof of successful enrollment/verification."""
        require(bounded_string(subject, 128, SUBJECT))
        try:
            async with asyncio.timeout(20):
                after = None
                cursors, ids = set(), set()
                ordered = []
                for _ in range(10):
                    params = {'limit': 100}
                    if after is not None:
                        params['after'] = after
                    value = await self._request('GET', '/user_management/users/' + subject + '/auth_factors', params=params)
                    require(value.get('object') == 'list' and type(value.get('data')) is list
                            and len(value['data']) <= 100)
                    for item in value['data']:
                        require(type(item) is dict and item.get('object') == 'authentication_factor'
                                and item.get('type') == 'totp'
                                and bounded_string(item.get('id'), 172, FACTOR)
                                and item['id'] not in ids)
                        for field in ('user_id', 'userId'):
                            if field in item:
                                require(item[field] == subject)
                        ids.add(item['id'])
                        ordered.append(item['id'])
                    require(type(value.get('list_metadata')) is dict and 'after' in value['list_metadata'])
                    after = value['list_metadata']['after']
                    if after is None:
                        return FactorInventory(self.binding, subject, tuple(ordered), int(time.time()))
                    require(bounded_string(after, 172, FACTOR) and after not in cursors)
                    cursors.add(after)
                raise SessionRevocationUncertain()
        except Exception:
            raise SessionRevocationUncertain() from None
