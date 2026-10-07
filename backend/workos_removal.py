"""Inert bounded WorkOS removal transport, constructed only by a trusted caller.

Membership is presence, never verification. The coordinator must prove ownership
before mutations and bind the constructor's configuration to its actor. WorkOS
challenge responses bind factor/challenge IDs, not our local operation UUID.
Deletion acknowledgment is local attribution of one response, not a signed
provider receipt, idempotency guarantee or permission to replay a lost request.

Paths: workos.com/docs/reference/{authkit/mfa,mfa/challenge,mfa/factor}.
Status contract follows workos/workos-python tests/test_multi_factor_auth.py:
JSON200 for challenge/verify/list; DELETE200 with empty/whitespace response.
Other success statuses remain uncertain until independently documented.
"""
import asyncio
from datetime import datetime
import re
from uuid import UUID

import httpx

from factor_removal import (FactorObservation, RemovalChallenge, RemovalDeletion,
                           RemovalUncertain, RemovalVerification)
from workos_transport_candidate import API, SUBJECT, ProviderSettings, bounded_string, strict_json


FACTOR=re.compile(r'auth_factor_[A-Za-z0-9_]{6,160}')
CHALLENGE=re.compile(r'auth_challenge_[A-Za-z0-9_]{6,160}')


def require(value):
    if not value: raise RemovalUncertain()


class WorkOSRemoval:
    def __init__(self, settings, *, transport=None):
        require(isinstance(settings,ProviderSettings))
        self.binding=settings.binding
        self._inflight=0
        limits=httpx.Limits(max_connections=8,max_keepalive_connections=8)
        self.client=httpx.AsyncClient(transport=transport or httpx.AsyncHTTPTransport(
            retries=0,trust_env=False,limits=limits),limits=limits,follow_redirects=False,
            trust_env=False,timeout=5,headers={'Authorization':'Bearer '+settings.client_secret,
                'Accept':'application/json','Accept-Encoding':'identity'})

    async def aclose(self):
        await self.client.aclose()

    @staticmethod
    def _inputs(subject,factor):
        require(bounded_string(subject,128,SUBJECT) and bounded_string(factor,172,FACTOR))

    async def _request(self,method,path,*,body=None,params=None,deletion=False):
        require(self._inflight<8)
        self._inflight+=1
        try:
            async with asyncio.timeout(10):
                async with self.client.stream(method,API+path,json=body,params=params) as response:
                    require(response.status_code==200)
                    require(response.headers.get('content-encoding','identity')=='identity')
                    if not deletion:
                        require(response.headers.get('content-type','').split(';')[0].strip()=='application/json')
                    raw=bytearray()
                    maximum=128 if deletion else 65536
                    async for chunk in response.aiter_raw():
                        require(len(raw)+len(chunk)<=maximum)
                        raw.extend(chunk)
                    if deletion:
                        require(not bytes(raw).strip(b' \t\r\n'))
                        return None
                    value=strict_json(raw)
                    require(type(value) is dict)
                    return value
        except Exception:
            raise RemovalUncertain() from None
        finally:
            self._inflight-=1

    @staticmethod
    def _subject_if_present(value,subject):
        for field in ('user_id','userId'):
            if field in value: require(value[field]==subject)

    async def observe(self,subject,factor):
        self._inputs(subject,factor)
        try:
            async with asyncio.timeout(15):
                after=None; cursors=set(); ids=set(); found=None
                for _ in range(10):
                    params={'limit':100}
                    if after is not None: params['after']=after
                    value=await self._request('GET','/user_management/users/'+subject+'/auth_factors',params=params)
                    require(value.get('object')=='list' and type(value.get('data')) is list and len(value['data'])<=100)
                    for item in value['data']:
                        require(type(item) is dict and item.get('object')=='authentication_factor'
                            and bounded_string(item.get('id'),172,FACTOR) and item['id'] not in ids
                            and type(item.get('type')) is str)
                        ids.add(item['id']); self._subject_if_present(item,subject)
                        if item['id']==factor: found=item['type']
                    require(type(value.get('list_metadata')) is dict and 'after' in value['list_metadata'])
                    after=value['list_metadata']['after']
                    if after is None:
                        return FactorObservation(self.binding,subject,factor,found,
                            'absent' if found is None else ('present' if found=='totp' else 'unknown'),True)
                    require(bounded_string(after,172,FACTOR) and after not in cursors)
                    cursors.add(after)
                raise RemovalUncertain()
        except Exception:
            raise RemovalUncertain() from None

    def _challenge(self,value,subject,factor,challenge=None):
        require(value.get('object')=='authentication_challenge'
            and bounded_string(value.get('id'),176,CHALLENGE)
            and value.get('authentication_factor_id')==factor
            and (challenge is None or value['id']==challenge))
        self._subject_if_present(value,subject)

    async def challenge(self,subject,factor):
        self._inputs(subject,factor)
        value=await self._request('POST','/auth/factors/'+factor+'/challenge',body={})
        try:
            self._challenge(value,subject,factor)
            require(type(value.get('expires_at')) is str and len(value['expires_at'])<=64)
            expiry=datetime.fromisoformat(value['expires_at'].replace('Z','+00:00'))
            require(expiry.tzinfo is not None)
            return RemovalChallenge(self.binding,subject,factor,value['id'],int(expiry.timestamp()))
        except Exception:
            raise RemovalUncertain() from None

    async def verify(self,subject,factor,challenge,code):
        self._inputs(subject,factor)
        require(bounded_string(challenge,176,CHALLENGE) and type(code) is str
            and re.fullmatch(r'[0-9]{6}',code) is not None)
        value=await self._request('POST','/auth/challenges/'+challenge+'/verify',body={'code':code})
        try:
            require(type(value.get('valid')) is bool and type(value.get('challenge')) is dict)
            self._challenge(value['challenge'],subject,factor,challenge)
            return RemovalVerification(self.binding,subject,factor,challenge,value['valid'])
        except Exception:
            raise RemovalUncertain() from None

    async def delete(self,subject,factor,operation):
        self._inputs(subject,factor)
        try:
            require(type(operation) is str and str(UUID(operation))==operation)
        except Exception:
            raise RemovalUncertain() from None
        await self._request('DELETE','/auth/factors/'+factor,deletion=True)
        return RemovalDeletion(self.binding,subject,factor,operation,'deleted')
