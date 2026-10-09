"""Backup-only adapter for the existing Healthchecks delivery transport.

Call with a dedicated approved check URL per surface and the installed status
root. Database and objects MUST use distinct checks: a shared URL would let a
healthy surface recover the other surface's failure.
Attended entry stores only the dedicated check configuration; it never pings.
No provider setup, scheduler or replay queue is created here.
Every call observes again; repeated failure pings target the same check, and
only fresh healthy observations send success. Provider deduplication, recovery
delivery and human acknowledgment require separate live acceptance.
"""
import os
from pathlib import Path
import re
import sys

import admission_healthchecks as delivery
import admission_operations as ops
import backup_health

CONFIG = Path('/etc/careertrellis/backup-healthchecks.json')
HOST_SURFACES = {
    'job-applier-db-staging-01': 'database',
    'careertrellis-staging-web-1': 'objects',
}
STATUS_ROOTS = {
    'database': Path('/var/lib/careertrellis/backup-health/database'),
    'objects': Path('/var/lib/careertrellis/backup-health/objects'),
}


def settings(raw, *, source, host):
    """Read one enrolled, host-specific check; never return it to CLI output.

    Enrollment must verify this check differs from admission and the other
    backup check while using the approved admission notification channel.
    Other hosts' capabilities are deliberately not copied here.
    """
    ops.require(type(raw) is bytes and len(raw) <= 2048
                and type(source) is str and re.fullmatch('[0-9a-f]{40}', source)
                and type(host) is str and host in HOST_SURFACES)
    value = ops.strict_json(raw)
    ops.require(type(value) is dict
                and set(value) == {'version', 'source', 'host', 'surface', 'ping_url'}
                and type(value['version']) is int and value['version'] == 1
                and value['source'] == source and value['host'] == host
                and value['surface'] == HOST_SURFACES[host]
                and type(value['ping_url']) is str and re.fullmatch(
                    r'https://hc-ping\.com/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
                    value['ping_url']))
    return value


def message(classification):
    """Render fixed codes only; never send status metadata or private paths."""
    allowed = {
        surface + ':' + code
        for surface in backup_health.SURFACES
        for code in backup_health.FAILURE_CODES_BY_SURFACE[surface] | {'observation_unavailable'}
    }
    if (type(classification) is not tuple
            or any(type(code) is not str or code not in allowed for code in classification)
            or tuple(sorted(set(classification))) != classification):
        raise ValueError('Invalid backup classification')
    if not classification:
        return 'CareerTrellis staging: fresh backup status reports healthy.'
    return 'CareerTrellis staging: backup attention required: ' + ', '.join(classification) + '.'


def observe(*, surface, status_root, now=None):
    if type(surface) is not str or surface not in backup_health.SURFACES:
        raise ValueError('Invalid backup surface')
    try:
        status = backup_health.checked_status(root=status_root, surface=surface, now=now)
        classification = tuple(surface + ':' + code for code in status['failure_codes'])
    except Exception:
        classification = (surface + ':observation_unavailable',)
    return classification


def report(ping_url, *, surface, status_root, now=None):
    """One fresh observation and one bounded delivery; no implicit retry."""
    classification = observe(surface=surface, status_root=status_root, now=now)
    acceptance = delivery.submit(ping_url, classification, version=3)
    return {'provider_acceptance': acceptance,
            'observation': 'alert' if classification else 'healthy'}


def report_configured(*, source, host):
    raw = ops.read_protected(CONFIG, limit=2048)
    value = settings(raw, source=source, host=host)
    classification = observe(surface=value['surface'], status_root=STATUS_ROOTS[value['surface']])
    # Never adopt changed configuration after the fresh observation.
    ops.require(ops.read_protected(CONFIG, limit=2048) == raw)
    acceptance = delivery.submit(value['ping_url'], classification, version=3)
    ops.require(acceptance in ('accepted', 'unconfirmed'))
    return {'provider_acceptance': acceptance,
            'observation': 'alert' if classification else 'healthy'}


def enter(*, source, host):
    """Operator-started hidden entry; create once, with no provider request."""
    from identity_entry import SecureTerminal
    ops.require(host in HOST_SURFACES)
    ops.protected_parents(CONFIG)
    ops.require(not CONFIG.exists() and not CONFIG.is_symlink())
    with SecureTerminal() as terminal:
        ops.require(terminal.ask('Confirm you started this in your visible secure terminal: type VISIBLE') == 'VISIBLE')
        ops.require(terminal.ask('Confirm the dedicated backup check surface: type ' + HOST_SURFACES[host])
                    == HOST_SURFACES[host])
        ops.require(terminal.ask('Verified this check differs from admission and the other backup check, and uses the existing admission email channel: type VERIFIED') == 'VERIFIED')
        url = terminal.ask('Dedicated backup check ping URL', limit=128)
        raw = ops.encoded({'version': 1, 'source': source, 'host': host,
                           'surface': HOST_SURFACES[host], 'ping_url': url})
        settings(raw, source=source, host=host)
        ops.require(terminal.ask('Save configuration only; no ping or timer activation: type SAVE') == 'SAVE')
    # write_protected uses a no-clobber link, including a concurrent creation.
    ops.write_protected(CONFIG, raw)
    ops.require(ops.read_protected(CONFIG, limit=2048) == raw)
    return {'outcome': 'configuration_saved_not_activated', 'surface': HOST_SURFACES[host]}


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        ops.require(args in (['report'], ['enter']) and os.geteuid() == 0)
        host = os.uname().nodename
        ops.require(host in HOST_SURFACES)
        module = Path(__file__).absolute()
        release = module.parent.parent
        ops.require(release.parent == Path('/opt/careertrellis/staging/releases')
                    and re.fullmatch('[0-9a-f]{40}', release.name))
        for name in ('backup_reporter', 'backup_health', 'admission_healthchecks',
                     'admission_operations', 'admission_alert_contract'):
            ops.read_protected(module.parent / (name + '.py'),
                               modes=(0o600, 0o640, 0o644), limit=131072)
        if args == ['enter']:
            for name in ('identity_entry', 'identity_evidence', 'identity_operator'):
                ops.read_protected(module.parent / (name + '.py'),
                                   modes=(0o600, 0o640, 0o644), limit=131072)
            result = enter(source=release.name, host=host)
            print(ops.encoded(result).decode().strip())
            return 0
        result = report_configured(source=release.name, host=host)
        print(ops.encoded(result).decode().strip())
        return 0 if result['provider_acceptance'] == 'accepted' else 1
    except BaseException:
        print('Backup reporting unavailable; inspect protected configuration.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
