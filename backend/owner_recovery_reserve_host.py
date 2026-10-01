"""WEB reserve recovery live views. No credential issuance or provider calls.

The coordinator owns primary then reserve locks. These views take host locks
before short database transactions. Supplied attestation connections are reused
verbatim; they are never replaced by a second transaction. Private view data
must never be printed. Existing immutable bbbc libraries remain unchanged.
"""
from contextlib import contextmanager, nullcontext
from datetime import timezone
import hashlib
from pathlib import Path

from sqlalchemy import text
import admission_operations as ops
import identity_operator as operator
import identity_plan_packet as packet
import identity_plan_store as plans
import identity_plan_supersession as supersession
import identity_web_handoff as handoff
import owner_recovery as recovery
import owner_recovery_bridge as bridge
import session_v2_transition as transition

OPS = 'bbbc4298b2ca80df107e64d2673d6c1f243c5e41'
ENROLL_APPLICATION = 'cafad5b48a579adc80695519f0f8b7d39f621cd5'
ENROLL_SCHEMA = '20260907_0014'
TARGET_SCHEMA = '20260929_0021'
CURRENT = Path('/opt/careertrellis/staging/current')
RELEASES = CURRENT.parent / 'releases'

def context_from_row(row):
    value = {key: row[key] for key in recovery.CONTEXT_KEYS}
    for key in ('operation', 'incarnation', 'predecessor'):
        if value[key] is not None:
            value[key] = str(value[key])
    for key in ('started', 'deadline'):
        value[key] = value[key].astimezone(timezone.utc).isoformat()
    recovery.context_times(value)
    return value


def acknowledgement(connection, context, primary_receipt):
    """Classify retained intake after caller locked and proved old superseded.

    Authoritative null evidence on that terminal predecessor resolves a known
    expired unsubmitted packet; it is retained verbatim, never retried/removed.
    """
    operation = context['operation']
    path, record_path = operator.locations(operation)
    row = connection.execute(text('SELECT evidence_digest,verified_at,evidence_expires '
        'FROM careertrellis_admission.identity_transitions WHERE operation=CAST(:op AS uuid)'),
        {'op': operation}).mappings().one()
    present = record_path.exists() or record_path.is_symlink()
    if row['evidence_digest'] is None:
        recovery.require(row['verified_at'] is None and row['evidence_expires'] is None)
        if not path.exists() and not path.is_symlink():
            recovery.require(not present)
            return 'none'
        fingerprint = operator.fingerprint(path)
        raw = ops.read_protected(path, limit=operator.evidence.LIMIT)
        value = operator.evidence.validate(raw)
        operator.evidence._same_context(context, value['context'])
        expiry = operator.evidence.instant(value['expires_at']).timestamp()
        recovery.require(value['version'] == 2 and primary_receipt['context'] == context
            and operator.evidence.instant(value['holder']['completed_at']).timestamp() == primary_receipt['issued']
            and expiry <= primary_receipt['expires'])
        recovery.require(connection.scalar(text('SELECT CAST(:expiry AS double precision)<=extract(epoch FROM clock_timestamp())'),
            {'expiry': expiry}) is True)
        if present:
            record = operator.journal(record_path, operation, OPS)
            recovery.require(record['phase'] == 'prepared'
                and record['digest'] == hashlib.sha256(ops.encoded(value)).hexdigest()
                and operator.evidence.instant(record['verified']) == operator.evidence.instant(value['verified_at'])
                and operator.evidence.instant(record['expires']) == operator.evidence.instant(value['expires_at']))
        recovery.require(operator.fingerprint(path) == fingerprint and ops.read_protected(path, limit=operator.evidence.LIMIT) == raw)
        return 'expired_unsubmitted'
    recovery.require(present)
    record = operator.journal(record_path, operation, OPS)
    recovery.require(record['phase'] == 'recorded' and record['digest'] == row['evidence_digest']
        and operator.evidence.instant(record['verified']) == row['verified_at']
        and operator.evidence.instant(record['expires']) == row['evidence_expires'])
    recovery.require(not path.exists() and not path.is_symlink())
    return 'exact_recorded'


class Host:
    def __init__(self, engine, *, verify_sources, metadata_reader, approval_reader, primary_plan_reader,
                 library_pins, action=None, closure_sha256=None, convergence=None):
        self.engine = engine
        self.verify_sources = verify_sources
        self.metadata_reader = metadata_reader
        self.approval_reader = approval_reader
        self.primary_plan_reader = primary_plan_reader
        self.library_pins = dict(library_pins)
        self.action = action
        self.closure_sha256 = closure_sha256
        if convergence is None:
            from owner_recovery_reserve_relay import request_observation
            convergence = request_observation
        self.convergence = convergence

    def verify(self):
        recovery.require(self.verify_sources() is True)

    def locks(self):
        return handoff.locks()

    def primary_selection(self):
        return self.primary_plan_reader()

    def observe(self, operation, selected, pair, metadata, stage):
        """Fresh workstation-relayed observation, never server-to-server SSH.

        The root-owned relay response is operational evidence supplied by the
        independently pinned operator relay. Arbitrary JSON is not authentication.
        Source checks, exact request binding and expiry remain mandatory.
        """
        self.verify()
        recovery.require(self.action in ('consume', 'receipt', 'enter', 'attest'))
        context = {'action': self.action, 'stage': stage, 'operation': operation,
            'enrollment_sha256': hashlib.sha256(recovery.encoded(metadata)).hexdigest(),
            'selection': selected,
            'activation_sha256': hashlib.sha256(ops.encoded(pair['activation'])).hexdigest(),
            'closure_sha256': self.closure_sha256}
        recovery.require(self.convergence(context) == selected)
        self.verify()

    @contextmanager
    def primary_view(self):
        """Freeze actual original P selection after hold, before primary use."""
        self.verify()
        with handoff.locks(), operator.secure_connection(self.engine) as connection:
            metadata = self.metadata_reader()
            original_raw = bytes.fromhex(metadata['original_plan_hex'])
            original = packet.validate(original_raw)
            enrolled = ops.strict_json(bytes.fromhex(metadata['original_selection_hex']))
            selected, pair = plans.load(activation=True)
            value = ops.strict_json(selected)
            recovery.require(value['digest'] == enrolled['digest'] and value['operation'] == enrolled['operation']
                and value['sequence'] >= enrolled['sequence'] and pair == original['switch']
                and original['version'] == 1 and pair['provider']['operations'] == OPS
                and hashlib.sha256(original_raw).hexdigest() == metadata['original_plan']
                and ops.read_protected(plans.archive(value['digest']), limit=packet.LIMIT) == original_raw)
            recovery.require(CURRENT.resolve(strict=True) == RELEASES / pair['provider']['application'])
            ops.stopped_services()
            operator.setup(connection, expected_schema=TARGET_SCHEMA)
            supersession.replacement(connection, original)  # Uncommitted/fenced/baseline/owner checks.
            staging_raw = ops.read_protected(ops.ROOT/'staging.env')
            auth_raw = ops.read_protected(ops.ROOT/'auth-v2.env')
            database = [self.engine.url.host, self.engine.url.port or 5432, self.engine.url.database,
                str(connection.scalar(text('SELECT oid FROM pg_database WHERE datname=current_database()')))]
            current = bridge.snapshot(connection, metadata['original_operation'],
                ops.assignments(staging_raw), ops.assignments(auth_raw), selected, pair, database)
            recovery.require(current['context']['predecessor'] is None and current['binding'] == metadata['binding'])
            transition.migration_receipt(pair['activation'])
            yield {'selection': selected, 'bridge': current}
            self.verify(); plans.require_current(selected)
            recovery.require(ops.read_protected(ops.ROOT/'staging.env') == staging_raw
                and ops.read_protected(ops.ROOT/'auth-v2.env') == auth_raw)

    @contextmanager
    def enrollment_view(self):
        self.verify()
        with handoff.locks():
            self.verify()
            recovery.require(CURRENT.resolve(strict=True) == RELEASES / ENROLL_APPLICATION)
            selected, pair = plans.load(activation=True)
            selection = ops.strict_json(selected)
            raw = ops.read_protected(plans.archive(selection['digest']), limit=packet.LIMIT)
            original = packet.validate(raw)
            recovery.require(original['version'] == 1 and raw == packet.canonical(raw)
                and pair == original['switch'] and pair['provider']['operations'] == OPS
                and pair['provider']['source_application'] == ENROLL_APPLICATION
                and len(pair['provider']['transitions']) == 1)
            primary = recovery.Store().read()
            recovery.require(primary is not None and primary['version'] == 1 and primary['state'] == 'active')
            binding = recovery.owner_snapshot()
            recovery.require(primary['binding'] == binding)
            # owner_snapshot verifies schema0014, maintenance off, signup closed,
            # original owner mapping and erasure absence against protected config.
            # Confirm no partially introduced identity hold table is present.
            with operator.secure_connection(self.engine) as connection:
                connection.exec_driver_sql('SET TRANSACTION READ ONLY')
                connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
                connection.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
                recovery.require(connection.scalar(text('SELECT version_num FROM careertrellis.alembic_version')) == ENROLL_SCHEMA)
                recovery.require(connection.scalar(text("SELECT to_regclass('careertrellis_admission.identity_transitions') IS NULL")) is True)
                plans.require_current(selected)
                view = {'binding': binding, 'original_plan': raw, 'original_selection': selected,
                    'original_operation': pair['provider']['transitions'][0], 'prehold_verified': True}
                yield view
                self.verify()
                recovery.require(CURRENT.resolve(strict=True) == RELEASES / ENROLL_APPLICATION)
                plans.require_current(selected)
                recovery.require(recovery.owner_snapshot() == binding and recovery.Store().read() == primary)

    def _candidate(self, pair, selected):
        """Same checks as handoff prepared_preflight without nested host locks."""
        from cryptography.fernet import Fernet
        p, activation = pair['provider'], pair['activation']
        recovery.require(CURRENT.resolve(strict=True) == RELEASES / p['source_application'])
        ops.stopped_services()
        files = handoff.read_files()
        _, _, auth = handoff.validate_files(files)
        sealed, receipt = handoff.paths(p)
        recovery.require(not receipt.exists() and not receipt.is_symlink())
        value = ops.strict_json(Fernet(auth[handoff.PREFIX+'ENVELOPE_KEY'].encode()).decrypt(
            ops.read_protected(sealed, limit=524288)))
        recovery.require(set(value) == {'plan', 'activation', 'boot', 'before', 'after', 'units'}
            and value['plan'] == p and value['activation'] == activation
            and value['boot'] == transition.boot() and value['before'] == files
            and value['units'] == handoff.inventory())
        provider, _, settings = handoff.validate_files(value['after'])
        recovery.require(provider.binding == p['target_binding'] and settings[handoff.PREFIX+'ENABLED'] == '0')
        transition.validate_web_inputs(activation,
            {name: value['after'][name].encode() for name in transition.WEB_INPUTS})
        transition.migration_receipt(activation)
        plans.require_current(selected)
        return files

    def _successor(self, connection, operation):
        self.verify()
        metadata = self.metadata_reader()
        original_raw = bytes.fromhex(metadata['original_plan_hex'])
        original_selected = bytes.fromhex(metadata['original_selection_hex'])
        recovery.require(hashlib.sha256(original_raw).hexdigest() == metadata['original_plan']
            and hashlib.sha256(original_selected).hexdigest() == metadata['original_selection'])
        original = packet.validate(original_raw)
        before = ops.strict_json(original_selected)
        primary_selected = self.primary_plan_reader()
        armed = ops.strict_json(primary_selected)
        recovery.require(set(armed) == set(before) and armed['digest'] == before['digest']
            and armed['operation'] == before['operation'] and armed['version'] == before['version'] == 1
            and type(armed['sequence']) is int and armed['sequence'] >= before['sequence'])
        recovery.require(original['version'] == 1 and original_raw == packet.canonical(original_raw)
            and hashlib.sha256(original_raw).hexdigest() == before['digest'])
        recovery.require(ops.read_protected(plans.archive(before['digest']), limit=packet.LIMIT) == original_raw)
        selected, pair = plans.load(activation=True)
        recovery.require(selected == self.approval_reader())
        self.observe(operation, selected, pair, metadata, 'pre')
        after = ops.strict_json(selected)
        successor_raw = ops.read_protected(plans.archive(after['digest']), limit=packet.LIMIT)
        successor = packet.validate(successor_raw)
        recovery.require(successor_raw == packet.canonical(successor_raw)
            and successor['predecessor'] == before['digest']
            and after['sequence'] > armed['sequence'] and after['operation'] == before['operation']
            and pair == successor['switch'] and pair['provider']['operations'] == OPS)
        packet.successor(original, successor)
        recovery.require(len(original['switch']['provider']['transitions']) == 1
            and pair['provider']['transitions'] == [operation])
        old_operation = original['switch']['provider']['transitions'][0]
        operator.setup(connection, expected_schema=TARGET_SCHEMA)
        # Derive complete candidate from authoritative recorded DB lineage; its
        # multi-hop capability is narrowed below to exactly one owner successor.
        derived = supersession.replacement(connection, original)
        derived['predecessor'] = before['digest']
        recovery.require(packet.canonical(ops.encoded(derived)) == successor_raw)
        old = connection.execute(text('SELECT * FROM careertrellis_admission.identity_transitions '
            'WHERE operation=CAST(:op AS uuid)'), {'op': old_operation}).mappings().one()
        new = connection.execute(text('SELECT * FROM careertrellis_admission.identity_transitions '
            'WHERE predecessor=CAST(:op AS uuid)'), {'op': old_operation}).mappings().one()
        old_context, new_context = context_from_row(old), context_from_row(new)
        recovery.require(old['state'] == 'superseded' and old_context['predecessor'] is None
            and str(new['operation']) == operation and new['state'] == 'held'
            and new_context['predecessor'] == old_operation and new['epoch'] == old['epoch'] + 1)
        recovery.require(connection.scalar(text('SELECT deadline<=clock_timestamp() '
            'FROM careertrellis_admission.identity_transitions WHERE operation=CAST(:op AS uuid)'),
            {'op': old_operation}) is True)
        recovery.require(connection.scalar(text('SELECT NOT EXISTS(SELECT 1 FROM '
            'careertrellis_admission.identity_transitions WHERE predecessor=CAST(:op AS uuid))'),
            {'op': operation}) is True)
        for key in ('account_id', 'incarnation', 'old_binding', 'old_subject',
                    'target_binding', 'target_subject', 'expected_role', 'owner_authorized'):
            recovery.require(old_context[key] == new_context[key])
        primary = recovery.Store().read()
        recovery.require(primary is not None and primary['version'] == 2 and primary['state'] == 'consumed'
            and primary['id'] == metadata['primary_id'])
        receipt = recovery.transition_receipt(primary['receipt'], primary)
        recovery.require(receipt['context'] == old_context
            and receipt['selection'] == hashlib.sha256(primary_selected).hexdigest())
        recovery.require(connection.scalar(text('SELECT CAST(:expiry AS double precision)<=extract(epoch FROM clock_timestamp())'),
            {'expiry': receipt['expires']}) is True)
        ack = acknowledgement(connection, old_context, receipt)
        files = self._candidate(pair, selected)
        database = [self.engine.url.host, self.engine.url.port or 5432, self.engine.url.database,
            str(connection.scalar(text('SELECT oid FROM pg_database WHERE datname=current_database()')))]
        current = bridge.snapshot(connection, operation, ops.assignments(files['staging.env'].encode()),
            ops.assignments(files['auth-v2.env'].encode()), selected, pair, database)
        recovery.require(current['binding'] == primary['binding'] == metadata['binding'])
        view = {'bridge': current, 'lineage': {'predecessor_context': old_context,
            'successor_context': new_context, 'original_plan': original_raw,
            'original_selection': original_selected, 'primary_selection': primary_selected,
            'successor_selection': selected,
            'predecessor_state': 'superseded', 'uncommitted_verified': True,
            'acknowledgement_resolved': ack in ('none', 'exact_recorded', 'expired_unsubmitted'),
            'successor_ready_verified': True}}
        return view, selected, files

    @contextmanager
    def successor_view(self, operation, connection=None):
        self.verify()
        # Coordinator holds host locks when passing the submit transaction.
        # Otherwise this adapter supplies host/rotation locks before opening DB.
        host = handoff.locks() if connection is None else nullcontext()
        with host:
            transaction = operator.secure_connection(self.engine) if connection is None else nullcontext(connection)
            with transaction as current_connection:
                view, selected, files = self._successor(current_connection, operation)
                yield view
                self.verify()
                plans.require_current(selected)
                recovery.require(self.approval_reader() == selected and handoff.read_files() == files)
                current_selected, pair = plans.load(activation=True)
                recovery.require(current_selected == selected)
                self.observe(operation, selected, pair, self.metadata_reader(), 'post')

    def transaction_view(self, connection, operation):
        return self.successor_view(operation, connection=connection)
