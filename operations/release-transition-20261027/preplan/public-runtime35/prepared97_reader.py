"""Draft fixed-domain protected97 snapshot; caller supplies reviewed protected IO."""
import prepared_types as a

DOMAIN = '/var/backups/careertrellis/preparation97-corrected-20261027/corrected97-fixture-g1/'
NAMES = frozenset({
    'accepted.json', 'postterminal-proof.json', 'owner-terminal.json',
    'owner.json', 'coordinator.json', 'child-spawn.json',
    'coordinator-spawn-intent.json', 'intent.json',
    'materialization-intent.json', 'materialization-child.json',
    'materialization-proof.json', 'runtime-before.json',
    'runtime-manifest.json', 'unit-intent.json', 'unit-client.json',
    'unit-terminal.json', 'checks.json', 'candidate.json', 'plan.json',
    'decision.json', 'source-manifest.json', 'preflight.json'})


def snapshot(protected_read):
    """Read every fixed leaf twice; return only the first consistent bytes.

    Ownership, regular-file, bounded-FD and ancestor checks remain the caller's
    original protected reader contract. This function neither replaces those
    checks nor adds a path supplied by a receipt or registry.
    """
    first = {}
    for name in sorted(NAMES):
        raw = protected_read(DOMAIN + name)
        a.need(type(raw) is bytes and 0 < len(raw) <= 16 * 1024 * 1024)
        first[name] = raw
    for name in sorted(NAMES):
        a.need(protected_read(DOMAIN + name) == first[name])
    return first
