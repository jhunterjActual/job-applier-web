"""Memory-only, one-shot protected recovery authentication handoff.

The injected claim callback must durably commit an exclusive exchange claim
before returning True. It must never infer success from an existing claim.
This module opens no listener, loads no credentials and logs no response/token.
"""
import asyncio
import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from recovery_factor_proof import RecoveryProofUncertain
from recovery_workflow import instant
from workos_transport_candidate import ProviderSettings, bounded_string, strict_json


SCOPE = ('operation', 'account_id', 'incarnation', 'epoch', 'binding', 'subject')


def require(value):
    if not value:
        raise RecoveryProofUncertain()


class AttendedRecoveryAuthentication:
    def __init__(self, store, authentication, workflow, claim_exchange, *, now=time.time):
        self.store, self.authentication, self.workflow = store, authentication, workflow
        self.claim_exchange, self.now = claim_exchange, now
        self._verifier = self._state = None
        self._used = False
        self._scope = None

    def begin(self, context, redirect_uri, settings):
        try:
            require(not self._used and self._scope is None and isinstance(settings, ProviderSettings))
            require(self.authentication.settings == settings)
            require(type(context) is dict and context.get('state') == 'held')
            scope = {name: context[name] for name in SCOPE}
            require(type(scope['account_id']) is int and scope['account_id'] > 0
                    and type(scope['epoch']) is int and scope['epoch'] > 0
                    and str(UUID(scope['operation'])) == scope['operation']
                    and str(UUID(scope['incarnation'])) == scope['incarnation']
                    and scope['binding'] == settings.binding
                    and bounded_string(scope['subject'], 128))
            require(context.get('plan_digest') is not None and context.get('effects')
                    and all(e['state'] == 'succeeded' for e in context['effects']))
            require(type(redirect_uri) is str and redirect_uri.isascii())
            target = urlsplit(redirect_uri)
            require(target.scheme == 'https' and target.hostname and target.username is None
                    and target.password is None and target.path == '/auth/callback'
                    and not target.query and not target.fragment
                    and not any(ord(c) <= 32 or ord(c) == 127 for c in redirect_uri))
            self._held_deadline = context['deadline']
            # Browser handoff allowance is independent of signed authentication
            # freshness; RecoveryWorkflow still verifies auth_time within300s.
            self._deadline = min(self.now() + 600, instant(self._held_deadline))
            require(self._deadline > self.now())
            verifier = secrets.token_urlsafe(32)
            state = 'r1_' + secrets.token_urlsafe(30)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
            self._scope, self._verifier, self._state = scope, verifier, state
            return 'https://api.workos.com/user_management/authorize?' + urlencode({
                'client_id': settings.client_id, 'redirect_uri': redirect_uri,
                'response_type': 'code', 'provider': 'authkit', 'state': state,
                'code_challenge': challenge, 'code_challenge_method': 'S256',
                'prompt': 'login', 'max_age': '0'})
        except Exception:
            self.close()
            raise RecoveryProofUncertain() from None

    async def _current(self):
        require(self.now() < self._deadline
                and self.authentication.settings.binding == self._scope['binding'])
        current = await self.store.context(self._scope['operation'])
        require({name: current[name] for name in SCOPE} == self._scope
                and current['state'] == 'held' and current['deadline'] == self._held_deadline
                and instant(current['deadline']) > self.now()
                and current.get('effects') and all(e['state'] == 'succeeded' for e in current['effects']))

    async def finish(self, raw_response):
        # Poison before the first await so concurrent calls cannot both dispatch.
        if self._used or self._verifier is None or self._scope is None:
            raise RecoveryProofUncertain()
        self._used = True
        verifier = self._verifier
        try:
            require(type(raw_response) is str and len(raw_response.encode('utf-8')) <= 4096)
            response = strict_json(raw_response)
            require(type(response) is dict and set(response) == {'code', 'state'}
                    and bounded_string(response['code'], 2048)
                    and bounded_string(response['state'], 43)
                    and hmac.compare_digest(response['state'], self._state))
            await self._current()
            require(await self.claim_exchange(dict(self._scope),
                hashlib.sha256(self._state.encode('ascii')).hexdigest()) is True)
            # A cancellation or failure here leaves the durable claim unresolved.
            await self._current()
            tokens = await self.authentication.exchange(response['code'], verifier)
            await self._current()
            require(tokens.subject == self._scope['subject']
                    and tokens.provider_binding == self._scope['binding'])
            return await self.workflow.release(self._scope['operation'], tokens)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RecoveryProofUncertain() from None
        finally:
            self.close()
            verifier = None

    def close(self):
        self._used = True
        self._verifier = self._state = None
