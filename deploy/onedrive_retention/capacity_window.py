"""Offline supplied schedule scenario; never activation-viability evidence.

The backward-compatible default assumes immediate native acceptance. Explicit
latencies, or None for never accepted, keep uploads pending and consuming space.
These remain assumptions, not measured native acceptance or latency bounds.
Even a fitting scenario is insufficient to establish viability.
Actual acceptance delay, concurrent writers and unexpected growth can worsen this
scenario. Existing free space includes all current active/recycled account bytes.
held_namespace_bytes counts additional held objects NOT listed in records.
"""
import datetime as dt
from . import policy, guard


def forecast(*, observed_at, records, uploads, held_namespace_bytes,
             account_total_bytes, account_free_bytes, source_peak_bytes,
             assumed_permanent_reclamation=False, acceptance_latency_seconds=0):
    now=policy.timestamp(observed_at)
    policy.need(type(assumed_permanent_reclamation) is bool)
    def latency(value):
        policy.need(value is None or type(value) is int and 0<=value<=365*86400)
        return value
    latency(acceptance_latency_seconds)
    for number in (held_namespace_bytes,account_total_bytes,account_free_bytes,source_peak_bytes):policy.integer(number)
    policy.need(account_total_bytes>0 and account_free_bytes<=account_total_bytes)
    policy.need(type(records) is list and type(uploads) is list and len(records)+len(uploads)<=10000)
    rows=[];seen=set()
    for row in records:
        policy._keys(row,('id','accepted_at','bytes','held'))
        policy.need(type(row['id']) is str and row['id'] and row['id'] not in seen and type(row['held']) is bool)
        # A supplied unresolved existing archive consumes space indefinitely;
        # it never becomes a verified successor merely because time passes.
        stamp=None if row['accepted_at'] is None else policy.timestamp(row['accepted_at'])
        policy.need(stamp is None or stamp<=now);policy.integer(row['bytes'],1)
        seen.add(row['id']);rows.append(dict(row,time=stamp))
    proposals=[];last=now
    for row in uploads:
        policy.need(type(row) is dict and set(row) in ({'id','at','max_candidate_bytes'},
            {'id','at','max_candidate_bytes','acceptance_latency_seconds'}))
        latency(row.get('acceptance_latency_seconds',acceptance_latency_seconds))
        stamp=policy.timestamp(row['at']);policy.integer(row['max_candidate_bytes'],1)
        policy.need(type(row['id']) is str and row['id'] and row['id'] not in seen and stamp>=last)
        seen.add(row['id']);last=stamp;proposals.append((row,stamp))
    free=account_free_bytes;recycled=0;events=[];pending=[]
    immediate=(acceptance_latency_seconds==0 and all(
        row.get('acceptance_latency_seconds',acceptance_latency_seconds)==0 for row in uploads)
        and all(row['time'] is not None for row in rows))
    for upload,stamp in proposals:
        for row in sorted((row for row in pending if row['time'] is not None and row['time']<=stamp),
                          key=lambda row:(row['time'],row['id'])):
            pending.remove(row);rows.append(row)
            events.append({'kind':'simulated_acceptance','at':row['time'].isoformat(),'id':row['id'],
                'native_acceptance_established':False})
        # Only already simulated accepted successors count. No future upload is
        # credited before its event. Ties order deterministically by ID.
        ordered=sorted((row for row in rows if row['time'] is not None),key=lambda row:(row['time'],row['id']))
        protected={row['id'] for row in ordered[-2:]}
        retire=[]
        for row in ordered:
            newer=[item for item in ordered if item['time']>row['time']]
            if row['held'] or row['id'] in protected or len(newer)<2:continue
            boundary=newer[1]['time']+dt.timedelta(seconds=policy.GRACE_SECONDS)
            if boundary<=stamp:retire.append((max(boundary,now),row))
        for boundary,row in sorted(retire,key=lambda pair:(pair[0],pair[1]['id'])):
            rows.remove(row)
            if assumed_permanent_reclamation:free+=row['bytes']
            else:recycled+=row['bytes']
            events.append({'kind':'simulated_retirement','at':boundary.isoformat(),'id':row['id'],
                'logical_bytes':row['bytes'],'simulated_account_credit_bytes':row['bytes'] if assumed_permanent_reclamation else 0})
        namespace=held_namespace_bytes+sum(row['bytes'] for row in rows+pending)
        check=guard.capacity(account_total_bytes,free,source_peak_bytes,upload['max_candidate_bytes'],namespace,upload['max_candidate_bytes'])
        events.append({'kind':'proposed_upload','at':stamp.isoformat(),'id':upload['id'],
            'simulated_free_before':free,'simulated_recycled_bytes':recycled,'capacity':check})
        if not check['allowed']:break
        free-=upload['max_candidate_bytes']
        delay=upload.get('acceptance_latency_seconds',acceptance_latency_seconds)
        accepted=None if delay is None else stamp+dt.timedelta(seconds=delay)
        row={'id':upload['id'],'time':accepted,'bytes':upload['max_candidate_bytes'],'held':False}
        if delay==0:rows.append(row)
        else:pending.append(row)
    fit=sum(event['kind']=='proposed_upload' and event['capacity']['allowed'] for event in events)==len(uploads)
    return {'schema':'careertrellis.capacity-window-simulation/v1','simulation_only':True,
        'native_actions_authorized':False,'future_uploads_assumed_immediately_native_accepted':immediate,
        'scenario':'optimistic_immediate_acceptance' if immediate else 'supplied_acceptance_latency',
        'activation_viability_established':False,
        'proven_acceptance_latency_bound_seconds':None,
        'assumed_permanent_reclamation':assumed_permanent_reclamation,'events':events,
        'assumed_acceptance_latency_seconds':acceptance_latency_seconds,
        'all_proposed_uploads_fit_optimistic_scenario':fit if immediate else None,
        'all_proposed_uploads_fit_supplied_scenario':fit,
        'pending_ids':sorted([row['id'] for row in pending]+[row['id'] for row in rows if row['time'] is None]),
        'retained_ids':sorted(row['id'] for row in rows+pending)}
