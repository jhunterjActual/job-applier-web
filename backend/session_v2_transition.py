"""Protected, signup-closed transition; invoked by admission_operations only.

No migration, credential generation, provider call, service start or rollback.
Receipts coordinate separately attended hosts; they are not remote attestation.
"""
import base64
import hashlib
from pathlib import Path
import re
from uuid import UUID

import admission_operations as ops
import totp_configuration

HEAD = '20260909_0015'
HEADS = (HEAD, '20260915_0016', '20260929_0021', '20261005_0023')


def schema(p):
    ops.require(p['schema'] in HEADS)
    return p['schema']


def owner_proof(operation, binding, subject):
    # Operation-scoped comparison, never a transferable raw WorkOS identity.
    return hashlib.sha256((operation+'\n'+binding+'\n'+subject).encode()).hexdigest()


def assert_identity_sql(p):
    """Run inside the maintenance/lifecycle transaction; no provider mutation."""
    return ("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-alembic-migrations-v1',0)); "
        "DO $$ BEGIN IF (SELECT version_num FROM careertrellis.alembic_version) IS DISTINCT FROM " + ops.literal(schema(p)) +
        " THEN RAISE EXCEPTION 'activation schema unavailable'; END IF; "
        "IF (SELECT binding FROM careertrellis_admission.identity_provider) IS DISTINCT FROM " + ops.literal(p['provider_binding']) +
        " OR NOT EXISTS (SELECT 1 FROM careertrellis_admission.identity_accounts a "
        "JOIN careertrellis_admission.identity_history h ON h.account_id=a.id AND h.binding=a.binding AND h.subject=a.subject AND h.state='active' "
        "JOIN careertrellis.app_users u ON u.id=a.id AND u.role=a.role AND u.workos_user_id=a.subject "
        "WHERE a.role='owner' AND NOT a.held AND NOT a.disabled AND u.disabled_at IS NULL "
        "AND NOT careertrellis_admission.identity_erasing(a.id) AND a.binding=" + ops.literal(p['provider_binding']) +
        " AND encode(sha256(convert_to(" + ops.literal(p['operation']) + "||E'\\n'||a.binding||E'\\n'||a.subject,'UTF8')),'hex')=" + ops.literal(p['owner_proof']) +
        ") OR (SELECT count(*) FROM careertrellis.app_users)>" + str(p['cap']) +
        " THEN RAISE EXCEPTION 'activation identity unavailable'; END IF; END $$; ")


def migration_receipt(p):
    ops.require(ops.strict_json(ops.read_protected(ops.STATE / ('web-' + p['operation'] + '.json'))) ==
        {'phase':'migrated', 'operation':p['operation'], 'target':p['application'], 'head':schema(p)})


def validate_plan(raw):
    # Internal parser; callers retain their fixed-error boundary (no raw input
    # or parsing diagnostics may be emitted by an operator-facing interface).
    ops.require(type(raw) is bytes and len(raw) <= 16384)
    value = ops.strict_json(raw)
    ops.require(isinstance(value, dict) and set(value) == {
        'operation', 'application', 'operations', 'realm', 'cap', 'ttl', 'session_seconds',
        'schema', 'provider_binding', 'owner_proof'})
    schema(value)
    for name in ('provider_binding', 'owner_proof'):
        ops.require(isinstance(value[name], str) and re.fullmatch('[0-9a-f]{64}', value[name]))
    ops.require(str(UUID(value['operation'])) == value['operation'])
    for name in ('application', 'operations'):
        ops.require(isinstance(value[name], str) and re.fullmatch('[0-9a-f]{40}', value[name]))
    ops.require(isinstance(value['realm'], str) and re.fullmatch('[A-Za-z0-9_-]{1,128}', value['realm']))
    ops.require(type(value['cap']) is int and 1 <= value['cap'] <= 1000000)
    ops.require(type(value['ttl']) is int and 60 <= value['ttl'] <= 900)
    ops.require(type(value['session_seconds']) is int and value['session_seconds'] == 3600)
    if value['schema'] in ('20260929_0021', '20261005_0023'):
        ops.require(value['cap'] == 25 and value['ttl'] == 600)
    return value


def plan():
    import identity_plan_store
    return identity_plan_store.load(activation=True)[1]['activation']


def path(p, side):
    return ops.STATE / ('v2-' + side + '-' + p['operation'] + '.json')


def receipt(p, phase):
    return {'plan': p, 'phase': phase}


def boot():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def armed_policy(record):
    p = record['plan']
    return {**record['policy'], 'phase': 'beta', 'signup_open': False,
        'adapter_ready': True, 'minimum_session_version': 2, 'maintenance': True,
        'cap': p['cap'], 'ttl': p['ttl'], 'realm': p['realm'],
        'generation': record['configured_generation'] + 1}


def arm_baseline(p):
    """Bind the maintenance generation to B in the same selected P archive."""
    import identity_plan_store as store
    import identity_plan_packet as packet
    snapshot, selected = store.load(activation=True)
    ops.require(selected['activation'] == p)
    pointer = ops.strict_json(snapshot)
    raw = ops.read_protected(store.archive(pointer['digest']), limit=packet.LIMIT)
    ops.require(hashlib.sha256(raw).hexdigest() == pointer['digest'])
    value = packet.validate(raw)
    ops.require(value['switch'] == selected)
    store.require_current(snapshot)
    return value['bind']['provider']['expected_generation']


def require_maintenance_baseline(old, generation):
    ops.require(type(generation) is int and 0 <= generation < 9223372036854775807)
    ops.require(type(old) is dict and type(old.get('generation')) is int
        and old == {**ops.INITIAL_POLICY, 'generation':generation})


def db_arm(db, p, *, baseline_generation=0):
    """Requires the separately completed ordinary maintenance fence."""
    marker = ops.STATE / ('maintenance-' + p['operation'] + '.json')
    maintenance = ops.strict_json(ops.read_protected(marker))
    schema(p)
    migration_receipt(p)
    ops.require(maintenance['operation'] == p['operation'] and maintenance['phase'] == 'quiesced' and maintenance['target'] == p['application']
        and maintenance['boot'] == boot() and maintenance['revision'] == ops.INITIAL_HEAD)
    ops.require(ops.revision(db) == schema(p))
    ops.assert_roles(db)
    require_maintenance_baseline(maintenance['policy'], baseline_generation)
    state = path(p, 'database')
    if state.exists():
        record = ops.strict_json(ops.read_protected(state))
        ops.require(record['plan'] == p and record['boot'] == boot()
            and record['phase'] in ('prepared', 'armed')
            and record['policy'] == maintenance['policy'] and record['roles'] == maintenance['roles'])
    else:
        old = maintenance['policy']
        ops.require(maintenance['roles'] == dict.fromkeys(ops.CALLERS, True))
        configured = ops.policy(db)
        generation = configured['generation']
        # Binding/recovery deliberately invalidates flows. Preserve those prior
        # increments; all other pre-arm policy fields must remain unchanged.
        ops.require(type(generation) is int and old['generation'] < generation < 9223372036854775807
            and configured == {**old, 'maintenance':True, 'generation':generation})
        record = {**receipt(p, 'prepared'), 'boot': boot(), 'policy': old, 'roles': maintenance['roles'],
            'configured_generation':generation}
        ops.state_write(state, record)
    current = ops.policy(db)
    target = armed_policy(record)
    fenced = dict.fromkeys(ops.CALLERS, False)
    ops.require(ops.roles(db) == fenced)
    if current == {**record['policy'], 'maintenance': True, 'generation':record['configured_generation']}:
        ops.require(record['phase'] == 'prepared')
        sql = 'UPDATE careertrellis_admission.policy SET ' + ','.join(
            name + '=' + (str(value).lower() if isinstance(value, (int, bool)) else ops.literal(value))
            for name, value in target.items() if name != 'singleton') + ';'
        ops.require(db.query('SELECT count(*) <= ' + str(p['cap']) + ' FROM careertrellis.app_users;') is True)
        db.query(ops.transaction(assert_identity_sql(p) + ops.assert_snapshot_sql(current, fenced) + ops.assert_quiescent_sql() + sql))
    else:
        ops.require(current == target)
        db.query(ops.transaction(assert_identity_sql(p) + ops.assert_snapshot_sql(target, fenced) + ops.assert_quiescent_sql()))
    ops.require(ops.policy(db) == target and ops.roles(db) == fenced)
    record['phase'] = 'armed'
    ops.state_write(state, record)
    ops.state_write(path(p, 'armed'), receipt(p, 'armed'))


WEB_INPUTS=('auth-v2.env','staging.env','object-storage.env','provider-secrets.env','admission-web.json','postgres-runtime.env')


def validate_web_inputs(p, files):
    """Validate supplied private bytes only; no host reads, writes or current switch."""
    from cryptography.fernet import Fernet
    from account_session_candidate import SessionCodec
    from workos_transport_candidate import ProviderSettings
    from postgres_database import postgres_url
    ops.require(type(files) is dict and set(files)==set(WEB_INPUTS) and all(type(v) is bytes for v in files.values()))
    raw = files['auth-v2.env']
    values = ops.assignments(raw)
    prefix = 'CAREERTRELLIS_AUTH_V2_'
    required = {prefix + name for name in ('ENABLED', 'ISSUER', 'GENERATION', 'KEY_ID',
        'SESSION_SECONDS', 'ENVELOPE_KEY', 'FLOW_KEY')} | {'CAREERTRELLIS_ADMISSION_DATABASE_URL'}
    ops.require(required <= set(values) <= required | {prefix + 'AUDIENCE'} | totp_configuration.FIELDS)
    staging = ops.assignments(files['staging.env'])
    objects = ops.assignments(files['object-storage.env'])
    provider_secrets = ops.assignments(files['provider-secrets.env'])
    for values_after_staging in (objects, provider_secrets):
        ops.require(not any(k.startswith('WORKOS_') or k.startswith(prefix) or k in (
            'CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID','CAREERTRELLIS_AUTH_MODE',
            'CAREERTRELLIS_IDENTITY_STORE','CAREERTRELLIS_ADMISSION_DATABASE_URL') for k in values_after_staging))
    ops.require(not any(k.startswith(prefix) or k == 'CAREERTRELLIS_ADMISSION_DATABASE_URL' for k in (*staging, *objects)))
    ops.require(values[prefix + 'GENERATION'] == p['realm'] and values[prefix + 'SESSION_SECONDS'] == '3600')
    metadata = ops.strict_json(files['admission-web.json'])
    expected = {'issuer': values[prefix + 'ISSUER'], 'generation': p['realm'],
        'key_id': values[prefix + 'KEY_ID'], 'session_seconds': 3600}
    if prefix + 'AUDIENCE' in values:
        expected['audience'] = values[prefix + 'AUDIENCE']
    ops.require(metadata == expected)
    provider = ProviderSettings(staging['WORKOS_CLIENT_ID'], staging['WORKOS_API_KEY'],
        expected['issuer'], p['realm'], expected.get('audience'))
    schema(p)
    ops.require(provider.binding == p['provider_binding'])
    subject = staging.get('CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID', '')
    ops.require(re.fullmatch('user_[A-Za-z0-9_]{1,120}', subject) and
        owner_proof(p['operation'], provider.binding, subject) == p['owner_proof'])
    keys = [values[prefix + 'ENVELOPE_KEY'], values[prefix + 'FLOW_KEY'], staging['WORKOS_COOKIE_PASSWORD']]
    for key in keys:
        Fernet(key.encode())
    ops.require(len({base64.urlsafe_b64decode(key) for key in keys}) == 3)
    totp_configuration.validate(values,keys,activation_schema=p['schema'])
    # Validate key/configuration construction only; this coordinator never issues
    # cookies. Epoch enforcement belongs to the separately prepared application.
    SessionCodec(keys[0], keys[2], key_id=expected['key_id'], generation=p['realm'],
        lifetime_seconds=3600, provider_binding=provider.binding)
    runtime_values = ops.assignments(files['postgres-runtime.env'])
    ops.require(set(runtime_values) == {'CAREERTRELLIS_DATABASE_URL'})
    urls = [postgres_url(values['CAREERTRELLIS_ADMISSION_DATABASE_URL']), postgres_url(runtime_values['CAREERTRELLIS_DATABASE_URL'])]
    for url, role in zip(urls, (ops.BROKER, 'careertrellis_runtime')):
        ops.require(url.username == role and url.password and url.host and ',' not in url.host
            and url.database == 'careertrellis' and set(url.query) <= {'sslmode', 'sslrootcert'}
            and all(isinstance(v, str) for v in url.query.values()))
    ops.require((urls[0].host, urls[0].port or 5432, urls[0].query) ==
        (urls[1].host, urls[1].port or 5432, urls[1].query))
    ops.require(urls[0].host == '10.70.0.2' or
        (urls[0].query.get('sslmode') == 'verify-full' and urls[0].query.get('sslrootcert')))
    return raw,values,prefix,staging,objects,provider_secrets,runtime_values,metadata,keys


def web_enable(p, *, preflight=False):
    from cryptography.fernet import Fernet
    if not preflight:
        ops.require(ops.strict_json(ops.read_protected(path(p, 'armed'))) == receipt(p, 'armed'))
        ops.stopped_services()
    ops.check_worker_delivery()
    ops.require(Path('/opt/careertrellis/staging/current').resolve() ==
        Path('/opt/careertrellis/staging/releases') / p['application'])
    files={name:ops.read_protected(ops.AUTH_FILE if name=='auth-v2.env' else ops.ROOT/name) for name in WEB_INPUTS}
    raw,values,prefix,staging,objects,provider_secrets,runtime_values,metadata,keys=validate_web_inputs(p,files)
    if preflight:
        ops.require(values[prefix + 'ENABLED'] == '0')
        return
    state = path(p, 'web')
    # Private encrypted continuity material, separate from transferable receipts.
    # It contains no printable fingerprint and cannot authorize a configuration
    # replacement. A changed envelope key cannot decrypt the original snapshot.
    continuity = {**values, prefix + 'ENABLED': '0'}
    baseline = ops.encoded({'boot': boot(), 'auth': continuity, 'provider': staging, 'objects': objects,
        'provider_secrets':provider_secrets, 'runtime': runtime_values, 'metadata': metadata})
    snapshot = ops.STATE / ('v2-config-' + p['operation'] + '.sealed')
    cipher = Fernet(keys[0].encode())
    if state.exists():
        old = ops.strict_json(ops.read_protected(state))
        ops.require(old in (receipt(p, 'prepared'), receipt(p, 'enabled')))
        ops.require(cipher.decrypt(ops.read_protected(snapshot, limit=524288)) == baseline)
        if old['phase'] == 'enabled':
            ops.require(values[prefix + 'ENABLED'] == '1')
    else:
        ops.require(values[prefix + 'ENABLED'] == '0')
        ops.write_protected(snapshot, cipher.encrypt(baseline))
        ops.state_write(state, receipt(p, 'prepared'))
    ops.require(values[prefix + 'ENABLED'] in ('0', '1'))
    if values[prefix + 'ENABLED'] == '0':
        # Preserve all installed bytes except the single explicit switch.
        changed, count = re.subn(rb'(?m)^CAREERTRELLIS_AUTH_V2_ENABLED=0(?=\r?$)',
            b'CAREERTRELLIS_AUTH_V2_ENABLED=1', raw)
        ops.require(count == 1)
        ops.write_protected(ops.AUTH_FILE, changed, previous=raw)
        ops.require(ops.read_protected(ops.AUTH_FILE) == changed)
    ops.check_worker_delivery()
    ops.state_write(state, receipt(p, 'enabled'))


def db_release(db, p):
    record = ops.strict_json(ops.read_protected(path(p, 'database')))
    ops.require(record['plan'] == p and record['boot'] == boot() and record['phase'] in ('armed', 'released'))
    ops.require(ops.strict_json(ops.read_protected(path(p, 'web'))) == receipt(p, 'enabled'))
    ops.require(ops.revision(db) == schema(p))
    migration_receipt(p)
    ops.assert_roles(db)
    marker = ops.STATE / ('maintenance-' + p['operation'] + '.json')
    maintenance = ops.strict_json(ops.read_protected(marker))
    ops.require(maintenance['operation'] == p['operation'] and maintenance['target'] == p['application']
        and maintenance['boot'] == boot() and maintenance['policy'] == record['policy']
        and maintenance['roles'] == record['roles'] and maintenance['revision'] == ops.INITIAL_HEAD
        and maintenance['phase'] in ('quiesced', 'released_services_still_stopped'))
    target = armed_policy(record)
    released = {**target, 'maintenance': False}
    current = ops.policy(db)
    if current == target:
        ops.require(record['phase'] == 'armed' and maintenance['phase'] == 'quiesced')
        db.query(ops.transaction(assert_identity_sql(p) + ops.assert_snapshot_sql(target, dict.fromkeys(ops.CALLERS, False)) +
            ops.assert_quiescent_sql() + ' '.join('ALTER ROLE ' + role + ' LOGIN;' for role in ops.CALLERS) +
            ' UPDATE careertrellis_admission.policy SET maintenance=false;'))
    else:
        ops.require(current == released)
        db.query(ops.transaction(assert_identity_sql(p) + ops.assert_snapshot_sql(released, record['roles']) + ops.assert_quiescent_sql()))
    ops.require(ops.policy(db) == released and ops.roles(db) == record['roles'])
    record['phase'] = 'released'
    ops.state_write(path(p, 'database'), record)
    maintenance['phase'] = 'released_services_still_stopped'
    ops.state_write(marker, maintenance)


def run(operation, source, db):
    p = plan()
    ops.require(source.parent.parent.name == p['operations'])
    {'db-v2-arm': lambda: db_arm(db, p, baseline_generation=arm_baseline(p)), 'web-v2-enable': lambda: web_enable(p),
        'web-v2-preflight': lambda: web_enable(p, preflight=True),
        'db-v2-release': lambda: db_release(db, p)}[operation]()
