"""Offline CT-BACKUP-LABEL-GAP policy candidate; not imported by production.

Inputs must come from the existing verified receipt evaluator. This classifier
does not validate receipts, change failure codes, or publish healthy evidence.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


MISMATCH = 'pgbackrest_mirror_label_mismatch'


@dataclass(frozen=True)
class Observation:
    now: datetime
    backup_completed: datetime
    mirror_completed: datetime
    previous_backup_label: str
    mirrored_label: str
    failure_codes: tuple[str, ...]
    evidence_verified: bool
    schedule_verified: bool
    alert_active: bool


@dataclass(frozen=True)
class Decision:
    state: str
    reason: str
    deadline: datetime | None
    original_failure_codes: tuple[str, ...]
    clear_existing_alert: bool = False


def classify(observation: Observation) -> Decision:
    """Propose pending only for a verified predecessor before a fixed deadline.

    Proposed schedule: daily 04:30 UTC, 15 min jitter, 30 min completion
    allowance. A three-hour cap from backup completion prevents sliding grace.
    The exact deadline is overdue. No wall clock is read internally.
    """
    def decision(state, reason, deadline=None):
        return Decision(state, reason, deadline, observation.failure_codes)

    if type(observation.failure_codes) is not tuple or any(
        not isinstance(code, str) or not code for code in observation.failure_codes
    ):
        raise ValueError('Invalid policy input')
    for value in (observation.now, observation.backup_completed, observation.mirror_completed):
        if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
            raise ValueError('UTC timestamps required')
    for value in (observation.evidence_verified, observation.schedule_verified, observation.alert_active):
        if type(value) is not bool:
            raise ValueError('Boolean evidence flags required')
    if not observation.evidence_verified or not observation.schedule_verified:
        return decision('alert', 'unverified_evidence_or_schedule')
    if observation.failure_codes != (MISMATCH,):
        return decision('unchanged', 'existing_evaluator_controls_result')
    if observation.alert_active:
        return decision('alert', 'existing_alert_requires_normal_recovery')
    if not isinstance(observation.previous_backup_label, str) or not observation.previous_backup_label:
        return decision('alert', 'predecessor_unknown')
    if observation.mirrored_label != observation.previous_backup_label:
        return decision('alert', 'mirror_is_not_verified_predecessor')
    if not observation.mirror_completed < observation.backup_completed <= observation.now:
        return decision('alert', 'inconsistent_clock_or_receipt_order')
    if observation.now - observation.mirror_completed >= timedelta(hours=48):
        return decision('alert', 'previous_mirror_stale')
    scheduled = observation.backup_completed.astimezone(timezone.utc).replace(
        hour=4, minute=30, second=0, microsecond=0)
    # An unscheduled late backup must not acquire tomorrow's grace window.
    if observation.backup_completed >= scheduled:
        return decision('alert', 'backup_outside_scheduled_window')
    deadline = min(scheduled + timedelta(minutes=45),
                   observation.backup_completed + timedelta(hours=3))
    if observation.now >= deadline:
        return decision('alert', 'mirror_overdue', deadline)
    return decision('pending', 'awaiting_scheduled_mirror', deadline)
