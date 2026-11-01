"""Exact app-release and standing-policy transaction engine.

No imports of installed maintenance code, network, provider or host operations.
An independently reviewed transport/native adapter must implement Backend.
There is deliberately no activation CLI or generic dynamic adapter loader.
"""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from policy_encoding import policy_canonical,policy_digest

BASE = '7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
PREVIOUS = '4095417909a8b13ad252be9420e422af3662bef5'
TARGET = 'bc68ea0dc253425a44e268452130becdf35d764e'
CORRECTION = 'f70417d4bd70c3eb086ea63c90938dd2f43e0b99'
ROLES = ('web', 'db')
BUILDS = {BASE: '20261017.96', TARGET: '20261026.97'}
POLICY_KEYS = frozenset(('application_release', 'application_build'))
STATUS_KEYS = frozenset(('schema','policy_sha256','boot_id','release','phase',
                        'result','observed_at','services_restored'))


class Refused(RuntimeError):
    pass

class GlobalTerminalUnknown(Refused):
    """A durable terminal-consensus attempt is ambiguous; never auto-resume."""
    pass


def need(value):
    if not value:
        raise Refused('release_transaction_refused_inspect_no_replay')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False, ensure_ascii=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(stamp.tzinfo is not None)
    return stamp.astimezone(timezone.utc)


def validate_plan(plan, now):
    need(type(plan) is dict and set(plan) == {
        'schema','base','previous','target','correction','builds','boots',
        'old_policy_digests','new_policy_digests','packet_sha256',
        'source_diffs','prepared_receipts','prepared_receipt_kinds','expires_at','readiness_manifest_sha256'})
    need(plan['schema'] == 'careertrellis.app-policy-release/v2')
    need(plan['base'] == BASE and plan['previous'] == PREVIOUS and plan['target'] == TARGET)
    correction = plan['correction']
    need(correction == CORRECTION)
    need(plan['builds'] == {**BUILDS, correction: '20261026.98'})
    need(set(plan['boots']) == set(ROLES)
         and all(re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', x)
                 for x in plan['boots'].values()))
    need(set(plan['old_policy_digests']) == set(ROLES))
    need(set(plan['new_policy_digests']) == {TARGET, correction})
    need(set(plan['source_diffs']) == {TARGET, correction})
    need(set(plan['prepared_receipts']) == {TARGET, correction})
    need(type(plan['prepared_receipt_kinds']) is dict and plan['prepared_receipt_kinds'] == {TARGET:'corrected-fixture97-new/v1',correction:'repair98-retained/v1'})
    hashes = [plan['packet_sha256'],plan['readiness_manifest_sha256'], *plan['old_policy_digests'].values(),
              *plan['source_diffs'].values(), *plan['prepared_receipts'].values()]
    for revision in (TARGET, correction):
        need(set(plan['new_policy_digests'][revision]) == set(ROLES))
        hashes.extend(plan['new_policy_digests'][revision].values())
    need(all(type(x) is str and re.fullmatch('[0-9a-f]{64}', x) and x != '0'*64 for x in hashes))
    need(now.tzinfo is not None)
    remaining=(timestamp(plan['expires_at'])-now).total_seconds()
    need(0<remaining<=3600)


def new_policy(old, revision, build, now):
    need(type(old) is dict and old.get('schema') == 'careertrellis.paired-maintenance-policy/v1')
    need(old.get('application_release') == BASE and old.get('application_build') == BUILDS[BASE])
    need(timestamp(old['valid_from']) <= now < timestamp(old['valid_until']))
    result = deepcopy(old)
    result.update(application_release=revision, application_build=build)
    need({k:v for k,v in old.items() if k not in POLICY_KEYS}
         == {k:v for k,v in result.items() if k not in POLICY_KEYS})
    return result


def validate_baseline(state, role, plan):
    need(type(state) is dict and set(state) == {
        'boot','private_policy','public_policy','status','interlock',
        'installed_verified','terminal_verified','no_unresolved_intents'})
    need(state['boot'] == plan['boots'][role])
    need(state['installed_verified'] is True and state['terminal_verified'] is True
         and state['no_unresolved_intents'] is True)
    old = state['private_policy']
    need(policy_canonical(old) == policy_canonical(state['public_policy']))
    need(policy_digest(old) == plan['old_policy_digests'][role])
    status = state['status']
    need(STATUS_KEYS <= set(status) <= STATUS_KEYS | {'transaction'})
    need(status['policy_sha256'] == policy_digest(old) and status['boot_id'] == state['boot']
         and status['release'] == BASE and status['services_restored'] is True)
    # Full immutable transaction evidence must be validated by native adapter;
    # projection phase/result alone is not terminal-state proof.
    need(status['phase'] in ('idle','complete','failed','blocked','preflight'))
    if role == 'web':
        need(state['interlock'] == {
            'schema':'careertrellis.paired-maintenance-interlock/v1',
            'policy_sha256':policy_digest(old),'boot_id':state['boot'],
            'release':BASE,'phase':'idle'})
    else:
        need(state['interlock'] is None)


def assert_known_policies(host, states, policies):
    for role in ROLES:
        observed = host.policy_readback(role)
        allowed = [states[role]['private_policy'], *[p[role] for p in policies.values()]]
        need(any(policy_canonical(observed) == policy_canonical(value) for value in allowed))


def apply_selection(host, states, policies, plan, revision, predecessor):
    """All writers stopped and both host fences continuously held by caller."""
    host.assert_stopped()
    need(host.pointer('current') == predecessor)
    host.assert_owned_state(states, policies, plan)
    assert_known_policies(host,states,policies)
    for role in ROLES:
        # The adapter writes private/public atomically per leaf, fsyncs, then
        # reads back both. A partial pair is not a successful phase receipt.
        host.write_policies(role, policies[revision][role])
        need(host.policy_readback(role) == policies[revision][role])
        host.phase(role, 'policy_selected', revision)
    host.assert_stopped()
    need(host.pointer('current') == predecessor)
    need(host.pointer('previous') == PREVIOUS)
    host.point(revision, expected=predecessor)
    marker = {'schema':'careertrellis.paired-maintenance-interlock/v1',
              'policy_sha256':policy_digest(policies[revision]['web']),
              'boot_id':states['web']['boot'],'release':revision,'phase':'idle'}
    host.write_interlock(marker)
    need(host.interlock_readback() == marker)
    host.assert_preserved()
    # Invoke installed writer-gate byte identity with exact current policy;
    # do not replace it with this engine's own interpretation.
    host.assert_writer_gate(revision)
    host.start_and_stabilize(revision, plan['builds'][revision])
    host.project_verifying(revision, plan['builds'][revision])
    host.assert_preserved()
    host.assert_healthy(revision, plan['builds'][revision])
    host.verify_fresh_proofs(plan,'verifying',revision,predecessor)


def execute(host, plan, *, approved_digest=None, now=None):
    """One-shot coordinated activation; no implicit replay/resume.

    Backend methods are a reviewed contract, not evidence by themselves. Native
    adapters must bind observations and writes to protected bytes/owned PIDs.
    Only approve_digest == exact canonical plan digest authorizes activation.
    """
    now = now or datetime.now(timezone.utc)
    validate_plan(plan, now)
    need(approved_digest == digest(plan))
    states, policies = {}, {TARGET:{}, plan['correction']:{}}
    with ExitStack() as stack:
        # One global order; DB participant remains connected/owns descriptors
        # until the web coordinator records terminal state and releases it.
        stack.enter_context(host.web_locks())
        stack.enter_context(host.db_locks())
        host.verify_packet(plan['packet_sha256'])
        need(host.pointer('current') == BASE and host.pointer('previous') == PREVIOUS)
        for role in ROLES:
            states[role] = host.baseline(role)
            validate_baseline(states[role], role, plan)
            for revision in policies:
                policy = new_policy(states[role]['private_policy'],revision,plan['builds'][revision],now)
                need(policy_digest(policy) == plan['new_policy_digests'][revision][role])
                policies[revision][role] = policy
        # These validate native prepared source/runtime/capability/full-diff and
        # fresh matched DB/object restore proof. No prep/build/provider dispatch.
        host.verify_prepared(plan)
        host.verify_fresh_proofs(plan,'preflight',BASE,BASE)
        host.assert_preserved()
        host.audit('preflight', plan)
        host.assert_readiness_current('preflight',BASE,BASE)
        # Both journals must be exclusively claimed before ANY policy/pointer
        # mutation. Claim failure may leave only evidence, never stop services.
        for role in ROLES:
            host.claim(role, digest(plan), states[role])
        try:
            # Durable installed-gate deny marker before stopping any producer.
            # DB participant retains fences on transport loss until reconciled;
            # an ExitStack exit is not permission to release an unknown owner.
            host.block_starts()
            host.assert_starts_blocked()
            host.stop()
            host.assert_stopped()
            host.verify_fresh_proofs(plan,'stopped',TARGET,BASE)
            host.audit('stopped',plan)
            host.assert_preserved()
            host.assert_readiness_current('stopped',TARGET,BASE)
            apply_selection(host,states,policies,plan,TARGET,BASE)
            for role in ROLES:
                host.finish(role, TARGET, policies[TARGET][role], 'target_healthy')
            host.authorize_terminal('target_healthy',TARGET)
            return {'result':'target_healthy','release':TARGET,'build':plan['builds'][TARGET]}
        except BaseException as failure:
            host.revoke_terminal()
            # No secrets/provider diagnostics escape this boundary. All original
            # journal/phase bytes are retained for manual inspection.
            with host.recovery_signals():
                try:
                    if isinstance(failure,GlobalTerminalUnknown):
                        host.contain();host.record_containment()
                        raise GlobalTerminalUnknown('terminal_consensus_unknown_fenced')
                    host.begin_recovery()
                    host.block_starts()
                    host.assert_starts_blocked()
                    host.stop()
                    host.assert_stopped()
                    host.assert_preserved()
                    current = host.pointer('current')
                    need(host.pointer('previous') == PREVIOUS)
                    selected=BASE if current==BASE else plan['correction']
                    host.verify_fresh_proofs(plan,'stopped',selected,current)
                    host.audit('recovery_stopped',plan)
                    host.assert_readiness_current('stopped',selected,current)
                    host.assert_owned_state(states,policies,plan)
                    assert_known_policies(host,states,policies)
                    if current == BASE:
                        for role in ROLES:
                            host.restore_original(role,states[role])
                            need(host.policy_readback(role) == states[role]['private_policy'])
                        need(host.interlock_readback() == states['web']['interlock'])
                        host.assert_preserved()
                        host.assert_writer_gate(BASE)
                        host.start_and_stabilize(BASE,BUILDS[BASE])
                        host.project_verifying(BASE,BUILDS[BASE])
                        host.assert_healthy(BASE,BUILDS[BASE])
                        host.verify_fresh_proofs(plan,'verifying',BASE,BASE)
                        host.assert_preserved()
                        for role in ROLES: host.finish_original(role, states[role])
                        host.authorize_terminal('aborted_before_switch',BASE)
                    else:
                        need(current == TARGET)
                        host.verify_prepared(plan)
                        apply_selection(host,states,policies,plan,plan['correction'],TARGET)
                        for role in ROLES:
                            host.finish(role,plan['correction'],policies[plan['correction']][role],'corrected')
                        host.authorize_terminal('corrected',plan['correction'])
                except GlobalTerminalUnknown:
                    host.revoke_terminal()
                except BaseException:
                    host.revoke_terminal()
                    host.contain()
                    # Containment must not fabricate successful restored status.
                    host.record_containment()
            raise Refused('activation_failed_inspect_original_journals_no_replay') from None
