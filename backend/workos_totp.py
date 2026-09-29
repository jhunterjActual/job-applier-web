"""Bounded AuthKit REST TOTP transport; constructed explicitly, never on import.

No delete/import endpoint and no network retry. Raw provider payloads, codes and
setup material must not enter logs. Callers persist uncertainty before mutations.
"""
import asyncio
from datetime import datetime
import re

import httpx

from totp_enrollment import EnrollmentSetup, EnrollmentUncertain
from workos_transport_candidate import API, SUBJECT, bounded_string, strict_json


FACTOR = re.compile(r'auth_factor_[A-Za-z0-9_]{6,160}')
CHALLENGE = re.compile(r'auth_challenge_[A-Za-z0-9_]{6,160}')


def require(value):
    if not value:
        raise EnrollmentUncertain()


class WorkOSTOTP:
    def __init__(self, api_key, *, transport=None):
        require(bounded_string(api_key, 512))
        self._inflight = 0
        limits = httpx.Limits(max_connections=8, max_keepalive_connections=8)
        self.client = httpx.AsyncClient(
            transport=transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False, limits=limits),
            limits=limits,
            follow_redirects=False, trust_env=False, timeout=5,
            headers={'Authorization': 'Bearer ' + api_key, 'Accept': 'application/json',
                     'Accept-Encoding': 'identity'})

    async def aclose(self):
        await self.client.aclose()

    async def _request(self, method, path, *, body=None, params=None):
        require(self._inflight < 8)
        self._inflight += 1
        try:
            async with asyncio.timeout(10):
                async with self.client.stream(method, API + path, json=body, params=params) as response:
                    require(response.status_code in (200, 201))
                    require(response.headers.get('content-encoding', 'identity') == 'identity')
                    require(response.headers.get('content-type', '').split(';')[0].strip() == 'application/json')
                    raw = bytearray()
                    async for chunk in response.aiter_raw():
                        require(len(raw) + len(chunk) <= 65536)
                        raw.extend(chunk)
                    value = strict_json(raw)
                    require(isinstance(value, dict))
                    return value
        except (httpx.HTTPError, TimeoutError, OSError, ValueError, UnicodeError, RecursionError):
            raise EnrollmentUncertain() from None
        finally:
            self._inflight -= 1

    async def enroll(self, subject):
        require(bounded_string(subject, 128, SUBJECT))
        value = await self._request('POST', '/user_management/users/' + subject + '/auth_factors',
                                   body={'type': 'totp', 'totp_issuer': 'CareerTrellis',
                                         'totp_user': subject})
        try:
            factor, challenge = value['authentication_factor'], value['authentication_challenge']
            require(factor['type'] == 'totp' and bounded_string(factor['id'], 172, FACTOR))
            for field in ('user_id', 'userId'):
                if field in factor:
                    require(factor[field] == subject)
            require(bounded_string(challenge['id'], 176, CHALLENGE))
            require(challenge['authentication_factor_id'] == factor['id'])
            secret = factor['totp']['secret']
            require(isinstance(secret, str) and re.fullmatch(r'[A-Z2-7]{16,128}', secret))
            expiry = datetime.fromisoformat(challenge['expires_at'].replace('Z', '+00:00'))
            require(expiry.tzinfo is not None)
            # Ignore provider HTML, URI and QR image. UI renders only validated secret.
            return EnrollmentSetup(factor['id'], challenge['id'], secret, int(expiry.timestamp()))
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            raise EnrollmentUncertain() from None

    async def verify(self, challenge, factor, code):
        require(bounded_string(challenge, 176, CHALLENGE) and bounded_string(factor, 172, FACTOR))
        require(isinstance(code, str) and re.fullmatch(r'[0-9]{6}', code))
        value = await self._request('POST', '/auth/challenges/' + challenge + '/verify', body={'code': code})
        try:
            require(type(value['valid']) is bool and value['challenge']['id'] == challenge
                    and value['challenge']['authentication_factor_id'] == factor)
            return value['valid']
        except (KeyError, TypeError):
            raise EnrollmentUncertain() from None

    async def is_verified(self, subject, factor):
        try:
            async with asyncio.timeout(15):
                return await self._verified_pages(subject, factor)
        except TimeoutError:
            raise EnrollmentUncertain() from None

    async def _verified_pages(self, subject, factor):
        require(bounded_string(subject, 128, SUBJECT) and bounded_string(factor, 172, FACTOR))
        after, seen, found = None, set(), False
        # Bound total readback as well as every individual request. Never interpret
        # a repeated cursor, malformed page or truncated traversal as absence.
        for _ in range(10):
            params = {'limit': 100}
            if after is not None:
                params['after'] = after
            value = await self._request('GET', '/user_management/users/' + subject + '/auth_factors', params=params)
            try:
                require(isinstance(value['data'], list) and len(value['data']) <= 100)
                for item in value['data']:
                    require(isinstance(item, dict) and bounded_string(item.get('id'), 172, FACTOR))
                    if item['id'] == factor:
                        require(item.get('type') == 'totp')
                        found = True
                after = value['list_metadata']['after']
                if after is None:
                    return found
                require(bounded_string(after, 172, FACTOR) and after not in seen)
                seen.add(after)
            except (KeyError, TypeError):
                raise EnrollmentUncertain() from None
        raise EnrollmentUncertain()
