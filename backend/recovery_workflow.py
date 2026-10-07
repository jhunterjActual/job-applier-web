"""Protected recovery effects and release, with durable claims before dispatch.

No routes, automatic startup, secret loading, or operator assertions. The caller
must obtain the hold through credential consumption and supply tokens from the
trusted, correlated fresh provider exchange. Unknown effects stay held.
"""
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import time
from uuid import UUID, uuid5

from recovery_factor_proof import RecoveryProofUncertain
from workos_transport_candidate import VerifiedTokens


def require(value):
    if not value:
        raise RecoveryProofUncertain()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None)
    return result.timestamp()


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


class RecoveryWorkflow:
    def __init__(self, store, sessions, factors, authentication, *, now=time.time):
        self.store, self.sessions, self.factors = store, sessions, factors
        self.authentication, self.now = authentication, now

    def _bound(self, context):
        require(context['state'] == 'held')
        require(context['binding'] == self.sessions.binding == self.factors.binding
                == self.authentication.settings.binding)
        require(instant(context['deadline']) > self.now())

    def _observation(self, value, context):
        require(value.provider_binding == context['binding']
                and value.subject == context['subject'] and value.complete is True
                and type(value.observed_at) is int
                and 0 <= self.now() - value.observed_at <= 30)

    async def contain_provider(self, operation, lost_factor):
        """Resume only never-claimed effects; an uncertain claim is never sent again."""
        try:
            context = await self.store.context(operation)
            self._bound(context)
            if context['plan_digest'] is None:
                factors = await self.sessions.observe_factors(context['subject'])
                self._observation(factors, context)
                require(lost_factor in factors.factor_ids)
                sessions = await self.sessions.observe(context['subject'])
                self._observation(sessions, context)
                require(len(sessions.session_ids) <= 100)
                targets = [('delete_factor', lost_factor)] + [
                    ('revoke_session', sid) for sid in sorted(sessions.session_ids)]
                effects = [{'effect_id': str(uuid5(UUID(operation), kind + ':' + target)),
                            'kind': kind, 'target': target} for kind, target in targets]
                await self.store.reserve(operation, context['epoch'], context['incarnation'],
                                         effects, digest(effects))
                context = await self.store.context(operation)
                self._bound(context)
            require([e['target'] for e in context['effects'] if e['kind'] == 'delete_factor']
                    == [lost_factor])
            # Do not dispatch ANY remaining effects when another one is uncertain.
            require(all(e['state'] in ('ready', 'succeeded') for e in context['effects']))
            for effect in context['effects']:
                if effect['state'] == 'succeeded':
                    continue
                require(await self.store.claim(operation, effect['effect_id']) is True)
                try:
                    if effect['kind'] == 'delete_factor':
                        owned = await self.factors.observe(context['subject'], effect['target'])
                        require(owned.provider_binding == context['binding']
                                and owned.subject == context['subject']
                                and owned.factor == effect['target'] and owned.complete is True
                                and owned.disposition == 'present' and owned.kind == 'totp')
                        result = await self.factors.delete(context['subject'], effect['target'], operation)
                        require(result.provider_binding == context['binding']
                                and result.subject == context['subject'] and result.factor == effect['target']
                                and result.operation == operation and result.disposition == 'deleted')
                    else:
                        require(effect['kind'] == 'revoke_session')
                        result = await self.sessions.revoke(context['subject'], effect['target'], operation)
                        require(result.provider_binding == context['binding']
                                and result.subject == context['subject'] and result.session_id == effect['target']
                                and result.operation == operation and result.status == 'revoked')
                    await self.store.record(operation, effect['effect_id'], 'succeeded', digest(asdict(result)))
                except asyncio.CancelledError:
                    # The committed claim remains unresolved, including cancellation.
                    raise
                except Exception:
                    # A successful result commit followed by response loss must not be
                    # overwritten; store.record rejects incompatible terminal updates.
                    try:
                        await self.store.record(operation, effect['effect_id'], 'uncertain',
                                                digest({'outcome': 'uncertain'}))
                    except Exception:
                        pass
                    raise RecoveryProofUncertain() from None
            sessions = await self.sessions.observe(context['subject'])
            factors = await self.sessions.observe_factors(context['subject'])
            self._observation(sessions, context)
            self._observation(factors, context)
            require(not sessions.session_ids and lost_factor not in factors.factor_ids)
            return {'outcome': 'provider_contained_account_held'}
        except (RecoveryProofUncertain, asyncio.CancelledError):
            raise
        except Exception:
            raise RecoveryProofUncertain() from None

    async def release(self, operation, tokens):
        """Verify signed fresh authentication and live provider state before release."""
        try:
            require(isinstance(tokens, VerifiedTokens))
            context = await self.store.context(operation)
            self._bound(context)
            require(context['plan_digest'] is not None and context['effects']
                    and all(e['state'] == 'succeeded' for e in context['effects']))
            require(tokens.provider_binding == context['binding'] and tokens.subject == context['subject'])
            claims = await self.authentication.verify_recent(tokens.access_token,
                context['subject'], tokens.session_id, maximum_age=300)
            last_effect = max(instant(e['finished_at']) for e in context['effects'])
            require(type(claims.get('auth_time')) is int and claims['auth_time'] > last_effect)
            sessions = await self.sessions.observe(context['subject'])
            factors = await self.sessions.observe_factors(context['subject'])
            self._observation(sessions, context)
            self._observation(factors, context)
            require(sessions.session_ids == (tokens.session_id,))
            # With optional MFA, absence after exact lost-factor deletion supports
            # fresh ordinary authentication. Remaining factors need their own proof;
            # membership alone must never be treated as enforced authentication.
            require(not factors.factor_ids)
            now = self.now()
            proof = {'binding': context['binding'], 'subject': context['subject'],
                     'authenticated_at': utc(claims['auth_time']), 'observed_at': utc(now),
                     'expires_at': utc(min(now + 60, claims['auth_time'] + 300,
                                           claims['exp'], instant(context['deadline']))),
                     'session_id': tokens.session_id, 'remaining_sessions': list(sessions.session_ids),
                     'remaining_factors': list(factors.factor_ids)}
            proof['proof_digest'] = digest({'operation': operation, 'epoch': context['epoch'],
                                            'incarnation': context['incarnation'], 'proof': proof})
            return await self.store.complete(operation, context['epoch'], context['incarnation'], proof)
        except (RecoveryProofUncertain, asyncio.CancelledError):
            raise
        except Exception:
            raise RecoveryProofUncertain() from None
