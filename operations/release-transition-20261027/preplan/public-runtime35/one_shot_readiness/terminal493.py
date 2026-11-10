"""Readonly exact493 terminal functions; imports adapted only."""
import os,itertools,re
from datetime import datetime
from collector import Refused
def timestamp(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None:raise Refused('terminal_time_unknown')
    return t
STATE='/var/lib/careertrellis/paired-maintenance'
STATUS_KEYS={'schema','policy_sha256','boot_id','release','phase','result','observed_at','services_restored'}
def need(x):
    if not x:raise Refused('scheduler_terminal_unverified')

def terminal(target, old, status, now):
    need(set(status) in (STATUS_KEYS, STATUS_KEYS | {'transaction'})
         and status['schema'] == 'careertrellis.paired-maintenance-status/v1'
         and status['policy_sha256'] == old.digest and status['boot_id'] == target.boot
         and status['release'] == old.data['application_release']
         and status['services_restored'] is True
         and timestamp(status['observed_at']) <= now)
    phase = status['phase']
    tx = status.get('transaction')
    if tx is None:
        need((phase == 'idle' and status['result'] == 'ok') or
             (target.role == 'web' and phase == 'preflight' and status['result'] == 'failed'))
        return
    need(isinstance(tx, str) and re.fullmatch('[0-9a-f]{32}', tx))
    base = STATE + '/' + target.role + '/runs/' + tx
    if target.role == 'web':
        need((phase == 'complete' and status['result'] == 'ok') or
             (phase in ('failed','blocked','preflight') and status['result'] == 'failed'))
        if os.path.lexists(target.path(base+'/prestart-aborted.json')):
            need(phase in ('failed','blocked','preflight') and status['result']=='failed')
            target.directory(base,0o700)
            names={path.name for path in itertools.islice(target.path(base).iterdir(),4)}
            need(names=={'status.json','prestart-original-status.json','prestart-aborted.json'})
            record=target.json(base+'/prestart-aborted.json')
            keys={'schema','transaction','policy_sha256','release','boot_id','slot_claim_name',
                  'original_status_sha256','no_dispatch','services_unchanged','services_restored',
                  'restoration_performed','verified_at','verifier_invocation'}
            need(set(record)==keys and record['schema']=='careertrellis.paired-maintenance-prestart-abort/v1'
                 and record['boot_id']==target.boot and record['release']==old.data['application_release']
                 and record['no_dispatch'] is True and record['services_unchanged'] is True
                 and record['restoration_performed'] is False
                 and re.fullmatch('[0-9a-f]{32}',record['verifier_invocation'])
                 and re.fullmatch(r'[0-9]{8}(?:-'+old.digest+r')?\.json',record['slot_claim_name'])
                 and timestamp(record['verified_at'])<=now)
            snapshot_raw=target.read(base+'/prestart-original-status.json',mode=0o600)
            need(__import__('hashlib').sha256(snapshot_raw).hexdigest()==record['original_status_sha256'])
            snapshot=target.json(base+'/prestart-original-status.json')
            need(set(snapshot)=={'state','transaction','policy_sha256','release','boot_id','worker_invocation',
                                 'observed_at','result','services_restored'}
                 and snapshot['transaction']==tx and snapshot['policy_sha256']==old.digest
                 and snapshot['release']==old.data['application_release'] and snapshot['boot_id']==target.boot
                 and snapshot['services_restored'] is True and snapshot['state'] in ('preflight','blocked','operator_required','failed')
                 and snapshot['result'] in ('running','failed','blocked')
                 and re.fullmatch('[0-9a-f]{32}',snapshot['worker_invocation'])
                 and timestamp(snapshot['observed_at'])<=timestamp(record['verified_at']))
            claim=target.json(STATE+'/web/slots/'+record['slot_claim_name'])
            need(claim=={'transaction':tx,'policy_sha256':old.digest})
        else:
            record = target.json(base + ('/complete.json' if phase == 'complete' else '/aborted-restored.json'))
        need(record.get('services_restored') is True)
    else:
        # The DB worker's current global projection uses result=running even for
        # aborted; do not infer terminal safety from that projection. Only the
        # accepted complete/ok path or untouched first-install idle is admitted.
        need(phase == 'complete' and status['result'] == 'ok')
        worker = target.json(base + '/status.json')
        need(worker.get('state') == phase and worker.get('transaction') == tx
             and worker.get('policy_sha256') == old.digest and worker.get('boot_id') == target.boot
             and worker.get('release') == old.data['application_release'])
        need(isinstance(worker.get('worker_invocation'), str)
             and re.fullmatch('[0-9a-f]{32}', worker['worker_invocation']))
        started = target.json(base + '/worker-started.json')
        need(started.get('boot_id') == target.boot
             and started.get('worker_invocation') == worker['worker_invocation'])
        record = target.json(base + '/complete-result.json')
    need(record.get('transaction') == tx and record.get('policy_sha256') == old.digest)


def prior_intents(target):
    root = STATE + '/' + target.role + '/policy-transitions'
    if not os.path.lexists(target.path(root)):
        return root
    target.directory(root, 0o700)
    entries = list(itertools.islice(target.path(root).iterdir(), 129))
    need(len(entries) <= 128)
    for path in entries:
        need(re.fullmatch('[0-9a-f]{64}', path.name))
        base = root + '/' + path.name
        target.directory(base, 0o700)
        intent = target.json(base + '/intent.json')
        receipt = target.json(base + '/success.json')
        need(intent.get('new_policy_sha256') == path.name
             and receipt.get('new_policy_sha256') == path.name
             and receipt.get('old_policy_sha256') == intent.get('old_policy_sha256')
             and receipt.get('result') == 'applied' and receipt.get('role') == target.role)
    return root


