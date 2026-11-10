"""Pure binding/expiry helper; cannot authenticate transport or authorize actions."""
from datetime import datetime,timedelta,timezone
import re
from collector import need,SPECS,request,scheduler_binding,validate_local_coverage,WEB_SCHEDULER_UNITS,DB_TEMPLATES,UNIT_FIELDS,public_document,sha,canonical
def stamp(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'));need(t.tzinfo is not None and t.utcoffset()==timedelta(0));return t
def check_pair(web,db,binding,manifest_sha256,*,now,qualified_database=None):
    scheduler_binding(binding);need(now.tzinfo is not None)
    check_scheduler_pair(web['custody']['scheduler_coverage'],db['custody']['scheduler_coverage'],binding,manifest_sha256,now=now)
    observations=[]
    for role,value in [('web',web),('db',db)]:
        need(value['role']==role and value['binding']==binding and value['boot']==binding['boots'][role]
            and value['core_sha256']==SPECS[role][2] and value['collector_manifest_sha256']==manifest_sha256
            and (value['result']=='ok' and value['failure_codes']==[] and value['failure_count']==0 or role=='db' and qualified_database is not None and value['result']=='alert' and value['failure_codes']==['full_backup_unit_missed'] and value['failure_count']==1))
        t=stamp(value['observed_at']);need(0<=(now-t).total_seconds()<=30);observations.append(t)
        mode=value['custody']['mode']
        need(mode==binding['mode'])
        if mode!='idle':need(value['custody']['owner']['boot']==binding['boots'][role])
    if qualified_database is not None:
        import collector as source_collector
        from readiness_bridge import verify_qualified_context
        verify_qualified_context(qualified_database,binding,source_collector,manifest_sha256,now=now)
        need(qualified_database['envelope']['raw_db']==db and qualified_database['envelope']['raw_web']==web and qualified_database['envelope']['derived_result']['result']=='ok' and qualified_database['envelope']['derived_result']['failure_codes']==[])
    need(web['custody']['mode']==db['custody']['mode'] and web['custody']['policy_sha256']==db['custody']['policy_sha256'])
    if binding['phase']=='verifying':
        need(web['custody']['service_proof_sha256']==db['custody']['service_proof_sha256']
            and type(web['custody']['service_proof_sha256']) is str and len(web['custody']['service_proof_sha256'])==64)
    need((max(observations)-min(observations)).total_seconds()<=30)
    need(0<=(now-stamp(binding['issued_at'])).total_seconds()<=30)
    need(web['authority']=={'domain':'postgres-r2-authoritative','pending_phase':'activated','activation_lease_absent':True}
        and db['authority']['activation_lease_absent'] is True)
    return {'result':'pair_bound','expires_at':(min(min(observations),stamp(binding['issued_at']))+timedelta(seconds=30)).isoformat(),
        'nonce':binding['nonce'],'phase':binding['phase'],**({'plan_sha256':binding['plan_sha256']} if 'plan_sha256' in binding else {'binding_sha256':sha(canonical(binding))})}


def check_scheduler_pair(web,db,binding,manifest_sha256,*,now):
    # Scheduler-only coverage join; NEVER canonical health or action admission.
    scheduler_binding(binding);need(now.tzinfo is not None)
    need(type(manifest_sha256) is str and re.fullmatch('[0-9a-f]{64}',manifest_sha256) is not None)
    pins=public_document('installed493-pins.json');times=[]
    for role,value in (('web',web),('db',db)):
        need(type(value) is dict)
        validate_local_coverage(value,role,{'policy_sha256':value['policy_sha256'],'mode':binding['mode']})
        need(value['binding']==binding and value['collector_manifest_sha256']==manifest_sha256)
        t=stamp(value['observed_at']);need(0<=(now-t).total_seconds()<=30);times.append(t)
        for name,row in value['units'].items():
            need(type(row) is dict and set(row)=={'name','template','sha256','state'} and row['name']==name and row['template']==name)
            _scheduler_unit(row,pins)
            state=row['state']
            if role=='web' and name.endswith('.timer'):need(state['ActiveState']=='active' and state['SubState']=='waiting' and state['Job']=='')
            else:need(state['ActiveState']=='inactive' and state['MainPID']==state['ControlPID']=='0' and state['Job']=='')
        if role=='web':
            need(value['instances']==[] and value['legacy_full_timer'] is None);_web_schedule(value['web_schedule'],t)
        else:need(value['web_schedule'] is None)
        if role=='db':
            need(type(value['instances']) is list and len(value['instances'])<=32)
            names=[]
            for row in value['instances']:
                need(type(row) is dict and set(row)=={'name','template','sha256','state'})
                name=row['name'];need(type(name) is str)
                need(re.fullmatch(r'careertrellis-paired-db(?:-recovery)?@[0-9a-f]{32}\.service',name) is not None)
                template='careertrellis-paired-db-recovery@.service' if name.startswith('careertrellis-paired-db-recovery@') else 'careertrellis-paired-db@.service'
                need(row['template']==template);_scheduler_unit(row,pins);state=row['state']
                need(state['ActiveState']=='inactive' and state['MainPID']==state['ControlPID']=='0' and state['Job']=='' and state['Result'] in ('','success'));names.append(name)
            need(len(set(names))==len(names))
            need(value['legacy_full_timer']=={'ActiveState':'inactive','UnitFileState':'disabled','MainPID':'0','ControlPID':'0','Job':''})
    need(web['policy_sha256']==db['policy_sha256'] and type(web['policy_sha256']) is str and re.fullmatch('[0-9a-f]{64}',web['policy_sha256']) is not None)
    need(web['mode']==db['mode']==binding['mode'] and (max(times)-min(times)).total_seconds()<=30 and 0<=(now-stamp(binding['issued_at'])).total_seconds()<=30)
    return {'schema':'careertrellis.scheduler-pair-coverage/v1','result':'scheduler_pair_bound','binding_sha256':sha(canonical(binding)),'collector_manifest_sha256':manifest_sha256,'web_coverage_sha256':sha(canonical(web)),'db_coverage_sha256':sha(canonical(db)),'policy_sha256':web['policy_sha256'],'expires_at':(min(min(times),stamp(binding['issued_at']))+timedelta(seconds=30)).isoformat(),'canonical_health_qualified':False}

def _scheduler_unit(row,pins):
    state=row['state'];need(type(state) is dict and all(type(k) is str and type(v) is str for k,v in state.items()) and set(state)==set(UNIT_FIELDS))
    path='/etc/systemd/system/'+row['template']
    need(state['LoadState']=='loaded' and state['NeedDaemonReload']=='no' and state['FragmentPath']==path and state['DropInPaths']=='' and row['sha256']==pins['units'][path])

def _web_schedule(value,observed):
    from zoneinfo import ZoneInfo
    names={name for name in WEB_SCHEDULER_UNITS if name.endswith('.timer')}
    need(type(value) is dict and set(value)==names)
    for name,row in value.items():
        need(type(row) is dict and set(row)=={'TimersCalendar','TimersMonotonic','Persistent','RandomizedDelayUSec','AccuracyUSec','property','next_value','monotonic_observed_us'})
        need(all(type(row[k]) is str for k in ('TimersCalendar','TimersMonotonic','Persistent','RandomizedDelayUSec','AccuracyUSec','property')))
        need(row['RandomizedDelayUSec']=='0' and row['AccuracyUSec']=='1s' and type(row['next_value']) is int and type(row['monotonic_observed_us']) is int)
        if 'maintenance' in name:
            need(row['property']=='NextElapseUSecRealtime' and 'OnCalendar=Sun *-*-* 03:00:00 America/New_York' in row['TimersCalendar'] and row['Persistent']=='yes')
            local=observed.astimezone(ZoneInfo('America/New_York'));candidate=local.replace(hour=3,minute=0,second=0,microsecond=0)+timedelta(days=(6-local.weekday())%7)
            if candidate<=local:candidate+=timedelta(days=7)
            need(row['next_value']==int(candidate.timestamp())*1000000)
        else:need(row['property']=='NextElapseUSecMonotonic' and 'OnBootUSec=30s' in row['TimersMonotonic'] and 'OnUnitActiveUSec=1min' in row['TimersMonotonic'] and 0<=row['next_value']-row['monotonic_observed_us']<=61000000)
