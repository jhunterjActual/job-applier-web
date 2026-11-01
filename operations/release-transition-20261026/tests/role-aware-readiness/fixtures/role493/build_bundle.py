"""Create a deterministic, reviewable ops packet from a fixed public allowlist.

No Git, network, host installation or private configuration is accessed. The
revision argument is an operator-supplied binding, not proof of source history.
"""
import argparse
import ast
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile


PACKAGE_FILES = frozenset(('__init__.py', 'common.py', 'runtime.py', 'provider.py',
    'client.py', 'ssh_gate.py', 'db_control.py', 'web_control.py', 'db_host.py', 'host.py', 'entry.py'))
UNITS = frozenset(('careertrellis-paired-maintenance.service', 'careertrellis-paired-maintenance.timer',
    'careertrellis-paired-recovery.service', 'careertrellis-paired-recovery.timer',
    'careertrellis-paired-db@.service', 'careertrellis-paired-db-recovery@.service',
    'careertrellis-paired-bootstrap.service'))
WRITERS = ('careertrellis-web.service', 'careertrellis-worker.service', 'careertrellis-erasure-worker.service')
PACKAGE = 'usr/local/libexec/careertrellis/paired_maintenance/'
CORE = 'usr/local/libexec/careertrellis/backup_health.py'
MAX_FILE = 2 * 1024**2
POLICY_EXAMPLE = 'deploy/paired_maintenance/policy-example.json'
SUPPORT_FILES = ('install_bundle.py', 'build_bundle.py', 'common.py', 'runtime.py', 'channel_setup.py', 'policy_transition.py', 'upgrade_bundle.py', 'upgrade_reconciled_bundle.py')


class BundleRefused(RuntimeError):
    pass


def need(value):
    if not value:
        raise BundleRefused('bundle_refused')


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def sha(raw): return hashlib.sha256(raw).hexdigest()


def support_bootstrap(expected, revision):
    """Only fixed reviewed support entry points can run after verification."""
    template = r'''"""Root-private hash-bound support bootstrap; use python3 -I -B."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys

EXPECTED = __EXPECTED__
REVISION = __REVISION__
SCHEMA = 'careertrellis.paired-maintenance-installer-support/v1'

def need(value):
    if not value: raise RuntimeError('support_refused')

def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result); result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs)

def verify_support():
    need(os.geteuid() == 0)
    root = Path(__file__).absolute().parent
    need(root.resolve(strict=True) == root)
    meta = root.lstat()
    need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0 and stat.S_IMODE(meta.st_mode) == 0o700)
    for parent in root.parents:
        meta = parent.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0)
        need(not meta.st_mode & 0o022 or (parent.name == 'tmp' and stat.S_IMODE(meta.st_mode) == 0o1777))
    allowed = set(EXPECTED) | {'bootstrap.py', 'support-manifest.json'}
    need({path.name for path in root.iterdir()} == allowed)
    values = {}
    for name in sorted(allowed):
        path = root / name; before = path.lstat()
        need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
             and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o644 and 0 < before.st_size <= 2097152)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_BINARY', 0))
        try:
            opened = os.fstat(descriptor)
            need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino))
            raw = os.read(descriptor, 2097153); after = os.fstat(descriptor)
            need(len(raw) == before.st_size and (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                 == (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
        finally: os.close(descriptor)
        values[name] = raw
    need(decode(values['support-manifest.json']) == {'schema': SCHEMA, 'revision': REVISION, 'files': EXPECTED})
    for name, expected in EXPECTED.items():
        need(hashlib.sha256(values[name]).hexdigest() == expected)
    return root

def main():
    try:
        root = verify_support()
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(root))
        module = 'install_bundle'
        if len(sys.argv) > 1 and sys.argv[1] in ('channel', 'policy', 'upgrade', 'upgrade-reconciled'):
            command = sys.argv.pop(1)
            module = {'channel': 'channel_setup', 'policy': 'policy_transition', 'upgrade': 'upgrade_bundle', 'upgrade-reconciled': 'upgrade_reconciled_bundle'}[command]
        return importlib.import_module(module).main()
    except Exception:
        print('{"result":"support_refused_no_retry"}')
        return 1

if __name__ == '__main__': raise SystemExit(main())
'''
    return template.replace('__EXPECTED__', repr(dict(sorted(expected.items())))).replace('__REVISION__', repr(revision)).encode('utf-8')


def support_payload(root, revision):
    values = {name: read_source(root, 'deploy/paired_maintenance/' + name) for name in SUPPORT_FILES}
    expected = {name: sha(raw) for name, raw in values.items()}
    values['support-manifest.json'] = encoded({'schema': 'careertrellis.paired-maintenance-installer-support/v1',
        'revision': revision, 'files': expected})
    values['bootstrap.py'] = support_bootstrap(expected, revision)
    return values


def write_archive(path, values):
    with path.open('xb') as destination:
        with gzip.GzipFile(filename='', mode='wb', fileobj=destination, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for name, raw in sorted(values.items()):
                    member = tarfile.TarInfo(name); member.size = len(raw); member.mode = 0o644
                    member.uid = member.gid = member.mtime = 0; member.uname = member.gname = ''
                    archive.addfile(member, io.BytesIO(raw))


def sources():
    result = {PACKAGE + name: ('deploy/paired_maintenance/' + name, ['web', 'db']) for name in PACKAGE_FILES}
    for name in UNITS:
        roles = ['db'] if name in ('careertrellis-paired-db@.service', 'careertrellis-paired-db-recovery@.service') else ['web']
        result['etc/systemd/system/' + name] = ('deploy/systemd/' + name, roles)
    for name in WRITERS:
        suffix = name + '.d/80-paired-maintenance.conf'
        result['etc/systemd/system/' + suffix] = ('deploy/systemd/' + suffix, ['web'])
    result[CORE] = ('backend/backup_health.py', ['web'])
    return result


def plain(meta):
    return not stat.S_ISLNK(meta.st_mode) and not getattr(meta, 'st_file_attributes', 0) & 0x400


def read_source(root, relative):
    path = root.joinpath(*PurePosixPath(relative).parts)
    need(path.is_relative_to(root))
    for parent in (root, *path.relative_to(root).parents):
        # Relative ancestor paths are resolved beneath the explicitly chosen root.
        candidate = parent if parent == root else root / parent
        meta = candidate.lstat()
        need(stat.S_ISDIR(meta.st_mode) and plain(meta))
    meta = path.lstat()
    need(stat.S_ISREG(meta.st_mode) and plain(meta) and meta.st_nlink == 1 and meta.st_size <= MAX_FILE)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        need((before.st_dev, before.st_ino) == (meta.st_dev, meta.st_ino))
        raw = stream.read(MAX_FILE + 1)
        after = os.fstat(stream.fileno())
    need(len(raw) == meta.st_size and (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns))
    return raw


def verify_archive(path, inventory):
    allowed = set(sources()) | {PACKAGE + 'manifest.json'}
    need(set(inventory) == allowed)
    with tarfile.open(path, 'r:gz') as archive:
        members = archive.getmembers()
        need(len(members) == len(allowed) and {member.name for member in members} == allowed)
        for member in members:
            parts = PurePosixPath(member.name)
            need(not parts.is_absolute() and '..' not in parts.parts and '\\' not in member.name)
            need(member.isfile() and not member.issym() and not member.islnk())
            need(member.uid == member.gid == member.mtime == 0 and member.mode == 0o644)
            expected = inventory[member.name]
            need(member.size == expected['size_bytes'] <= MAX_FILE)
            raw = archive.extractfile(member).read(MAX_FILE + 1)
            need(sha(raw) == expected['sha256'])


def build_bundle(root, output, revision, include_policy_example=False, include_installer_support=False):
    need(isinstance(revision, str) and re.fullmatch('[0-9a-f]{40}', revision) is not None)
    root = Path(root).absolute()
    output = Path(output).absolute()
    need('..' not in root.parts and '..' not in output.parts and output.is_relative_to(root) and output != root)
    need(not os.path.lexists(output))
    for parent in (output.parent, *output.parent.parents):
        meta = parent.lstat()
        need(stat.S_ISDIR(meta.st_mode) and plain(meta))
    data = {}; inventory = {}
    for target, (source, roles) in sorted(sources().items()):
        raw = read_source(root, source)
        data[target] = raw
        inventory[target] = {'source': source, 'roles': roles, 'mode': '0644', 'size_bytes': len(raw), 'sha256': sha(raw)}
    tree = ast.parse(data[PACKAGE + 'entry.py'].decode('utf-8-sig'))
    declarations = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(name, ast.Name) and name.id == 'FILES' for name in node.targets)]
    need(len(declarations) == 1 and declarations[0] == set(PACKAGE_FILES))
    manifest = {'schema': 'careertrellis.paired-maintenance-install/v1', 'revision': revision,
        'files': {name: sha(data[PACKAGE + name]) for name in sorted(PACKAGE_FILES)}}
    data[PACKAGE + 'manifest.json'] = encoded(manifest)
    inventory[PACKAGE + 'manifest.json'] = {'source': 'generated', 'roles': ['web', 'db'], 'mode': '0644',
        'size_bytes': len(data[PACKAGE + 'manifest.json']), 'sha256': sha(data[PACKAGE + 'manifest.json'])}
    example = read_source(root, POLICY_EXAMPLE) if include_policy_example else None
    support = support_payload(root, revision) if include_installer_support else None
    if support is not None:
        for name in SUPPORT_FILES:
            if PACKAGE + name in data:
                need(support[name] == data[PACKAGE + name])
    output.mkdir(mode=0o700)
    archive_path = output / 'paired-maintenance.tar.gz'
    write_archive(archive_path, data)
    verify_archive(archive_path, inventory)
    archive_bytes = archive_path.read_bytes()
    receipt = {'schema': 'careertrellis.paired-maintenance-bundle/v1', 'revision': revision,
        'revision_verification': 'operator_supplied_not_verified_by_builder',
        'archive': {'name': archive_path.name, 'sha256': sha(archive_bytes), 'size_bytes': len(archive_bytes)},
        'install_manifest_sha256': sha(encoded(manifest)),
        'install_file_count': len(inventory), 'install_bytes': sum(len(raw) for raw in data.values()),
        'files': inventory, 'policy_in_archive': False, 'application_release_changed': False}
    if support is not None:
        support_root = output / 'installer-support'
        support_root.mkdir(mode=0o700)
        for name, raw in sorted(support.items()):
            (support_root / name).write_bytes(raw)
            os.chmod(support_root / name, 0o644)
        support_archive = output / 'installer-support.tar.gz'
        write_archive(support_archive, support)
        archive_raw = support_archive.read_bytes()
        receipt['installer_support'] = {'revision': revision,
            'archive': {'name': support_archive.name, 'sha256': sha(archive_raw), 'size_bytes': len(archive_raw)},
            'files': {name: {'sha256': sha(raw), 'size_bytes': len(raw), 'mode': '0644'} for name, raw in sorted(support.items())},
            'stage_mode': '0700', 'requires_root_owned_stage': True,
            'bootstrap_authentication': 'Verify approved archive/bootstrap hashes before staging or execution'}
    (output / 'manifest.json').write_bytes(encoded(manifest))
    (output / 'bundle.json').write_bytes(encoded(receipt))
    (output / 'bundle.sha256').write_text(sha(encoded(receipt)) + '  bundle.json\n', encoding='ascii')
    if example is not None:
        (output / 'policy.example.json').write_bytes(example)
    need(json.loads((output / 'manifest.json').read_bytes()) == manifest)
    need(json.loads((output / 'bundle.json').read_bytes()) == receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--include-policy-example', action='store_true')
    parser.add_argument('--include-installer-support', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = build_bundle(Path(__file__).absolute().parents[2], args.output, args.revision,
                              args.include_policy_example, args.include_installer_support)
        print(json.dumps({'result': 'bundle_created', 'archive': result['archive'], 'install_file_count': result['install_file_count']}, sort_keys=True))
        return 0
    except Exception:
        print('{"result":"bundle_unconfirmed_inspect_output_no_overwrite"}')
        return 1


if __name__ == '__main__': raise SystemExit(main())
