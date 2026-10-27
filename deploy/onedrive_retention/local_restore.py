"""Injectable local-bundle recovery orchestration; native execution unsupported.

This is an offline state-machine implementation, not a native restore adapter.
The default refuses before IO. Injected adapters may exercise only offline tests;
their receipts always say native_restore_accepted=False. Never use a simulation
receipt as a catalog acceptance record, enable retirement, or clear health alerts.

An eventual reviewed native adapter must implement protected preflight, exact
ordered locks, exclusive durable journals, private command output and process
identity/cgroup fencing. Those capabilities are deliberately not inferred from
successful command exits or from the offline adapter's assertions.
"""
import copy
import re

try:
    from . import local_restore_plan as plans
except ImportError:
    # Direct script execution must not search cwd or an unreviewed import path.
    raise RuntimeError('local_bundle_restore_import_refused') from None


class ExecutionRefused(ValueError):
    """Fixed sanitized refusal; private adapter exceptions are never propagated."""


class NativeAdapter:
    """Explicit unsupported boundary: no locks, files or commands are created."""
    mode = 'native-unsupported'


def need(value, code='local_bundle_restore_refused'):
    if not value:
        raise ExecutionRefused(code)


def _identity(value, plan):
    need(type(value) is dict and set(value) == {
        'pid', 'start_ticks', 'boot_id', 'process_group', 'data_root', 'socket_root'})
    for field in ('pid', 'start_ticks', 'process_group'):
        need(type(value[field]) is int and value[field] > 0)
    need(type(value['boot_id']) is str and re.fullmatch(
        '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value['boot_id']))
    need(value['data_root'] == plan['data_root'] and value['socket_root'] == plan['socket_root'])
    return copy.deepcopy(value)


def execute(plan, adapter=None):
    """Execute an OFFLINE adapter simulation or refuse unsupported native work.

    Adapter methods are an explicit test contract. dispatch receives only commands
    reconstructed by local_restore_plan and returns {'exit_code': int}; all raw
    output remains private to the adapter. journal_event must exclusively append,
    fsync, and acknowledge the supplied canonical digest without ambiguous retry.
    retain_fences must model durable ownership transfer when a start may have
    created an unknown/running postmaster. No cleanup or signal operation exists.
    """
    try:
        checked = plans.validate_plan(plan)
        plan = copy.deepcopy(plan)
    except Exception:
        raise ExecutionRefused('local_bundle_restore_refused') from None
    if adapter is None:
        adapter = NativeAdapter()
    need(type(getattr(adapter, 'mode', None)) is str
         and adapter.mode == 'offline-test', 'native_local_bundle_restore_unsupported')

    plan_digest = checked['plan_sha256']
    locks = None
    journal = None
    journal_uncertain = False
    possible_postmaster = False
    sequence = 0
    identity = None

    def unchanged():
        need(plans.validate_plan(plan)['plan_sha256'] == plan_digest)

    def event(phase, state, extra=None):
        nonlocal sequence, journal_uncertain
        unchanged()
        value = {'schema': 'careertrellis.local-restore-simulation-event/v1',
                 'plan_sha256': plan_digest, 'transaction': plan['transaction'],
                 'sequence': sequence, 'phase': phase, 'state': state,
                 'native_restore_accepted': False}
        if extra is not None:
            value['identity'] = copy.deepcopy(extra)
        expected = plans.digest(value)
        journal_uncertain = True
        acknowledged = adapter.journal_event(journal, copy.deepcopy(value))
        need(acknowledged == expected, 'local_restore_journal_uncertain')
        journal_uncertain = False
        sequence += 1

    def command(phase):
        nonlocal possible_postmaster
        unchanged()
        event(phase, 'intent')
        # Persist dispatch BEFORE invoking anything. A crash after this event is
        # an uncertain operation to reconcile, never authority to dispatch again.
        event(phase, 'dispatch')
        if phase == 'start':
            possible_postmaster = True
        result = adapter.dispatch(phase, copy.deepcopy(plan[phase]),
                                  plan['timeouts_seconds'][phase])
        need(type(result) is dict and set(result) == {'exit_code'}
             and type(result['exit_code']) is int and result['exit_code'] == 0,
             'local_restore_command_failed')
        event(phase, 'terminal')

    try:
        # Both preflight calls must verify the plan's complete stated conditions;
        # the second occurs while every canonical lock is held, before writes.
        need(adapter.protected_preflight(copy.deepcopy(plan)) is True)
        locks = adapter.acquire_locks(list(plan['locks_in_order']))
        need(locks is not None)
        need(adapter.locked_preflight(copy.deepcopy(plan)) is True)
        journal = adapter.create_exclusive_journal(copy.deepcopy(plan), plan_digest)
        need(journal is not None)
        event('operation', 'intent')
        need(adapter.verify_hydrated_repository(copy.deepcopy(plan)) is True)
        command('info')
        need(adapter.validate_local_info(copy.deepcopy(plan)) is True)
        command('restore')
        need(adapter.prepare_isolated_cluster(copy.deepcopy(plan)) is True)
        need(adapter.verify_hydrated_repository(copy.deepcopy(plan)) is True)
        command('start')
        identity = _identity(adapter.owned_postmaster(copy.deepcopy(plan)), plan)
        event('postmaster', 'owned', identity)
        need(adapter.inspect_postmaster(copy.deepcopy(identity), copy.deepcopy(plan)) == 'owned_alive')
        need(adapter.verify_no_tcp_listener(plan['port']) is True)
        command('probe')
        need(adapter.validate_promotion_probe(copy.deepcopy(identity), copy.deepcopy(plan)) is True)
        # Recheck durable identity immediately before the sole normal stop. A
        # failed probe/identity check never signals an unidentified postmaster.
        need(adapter.inspect_postmaster(copy.deepcopy(identity), copy.deepcopy(plan)) == 'owned_alive')
        command('stop')
        need(adapter.verify_terminal(copy.deepcopy(identity), copy.deepcopy(plan)) is True)
        possible_postmaster = False
        need(adapter.verify_no_tcp_listener(plan['port']) is True)
        command('checksums')
        need(adapter.verify_hydrated_repository(copy.deepcopy(plan)) is True)
        event('operation', 'simulated_complete')
        result = {'schema': 'careertrellis.local-restore-simulation/v1',
                  'result': 'simulated_complete', 'plan_sha256': plan_digest,
                  'transaction': plan['transaction'], 'archive_sha256': plan['archive_sha256'],
                  'generation_sha256': plan['generation_sha256'],
                  'backup_label': plan['backup_label'], 'execution_authorized': False,
                  'native_restore_accepted': False, 'cleanup_performed': False}
    except Exception:
        # Journal failures are ambiguous: do not retry their append or issue any
        # further command. Failed roots are always retained, even before start.
        if journal is not None and not journal_uncertain:
            try:
                event('operation', 'failed_retained')
            except Exception:
                pass
        if locks is not None:
            try:
                if possible_postmaster:
                    adapter.retain_fences(locks, copy.deepcopy(identity))
                else:
                    adapter.release_locks(locks)
            except Exception:
                # No new operation can safely be inferred from failed fencing.
                pass
        raise ExecutionRefused('local_bundle_restore_failed_retained') from None
    else:
        try:
            adapter.release_locks(locks)
        except Exception:
            raise ExecutionRefused('local_bundle_restore_fence_release_unknown') from None
        return result
