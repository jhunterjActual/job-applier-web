"""Pure binding/expiry helper; cannot authenticate transport or authorize actions."""
from datetime import datetime,timedelta,timezone
from collector import need,SPECS,request
def stamp(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'));need(t.tzinfo is not None and t.utcoffset()==timedelta(0));return t
def check_pair(web,db,binding,manifest_sha256,*,now):
    request(binding);need(now.tzinfo is not None)
    observations=[]
    for role,value in [('web',web),('db',db)]:
        need(value['role']==role and value['binding']==binding and value['boot']==binding['boots'][role]
            and value['core_sha256']==SPECS[role][2] and value['collector_manifest_sha256']==manifest_sha256
            and value['result']=='ok' and value['failure_codes']==[])
        t=stamp(value['observed_at']);need(0<=(now-t).total_seconds()<=30);observations.append(t)
        mode=value['custody']['mode']
        need(mode==binding['mode'])
        if mode!='idle':need(value['custody']['owner']['boot']==binding['boots'][role])
    need(web['custody']['mode']==db['custody']['mode'] and web['custody']['policy_sha256']==db['custody']['policy_sha256'])
    if binding['phase']=='verifying':
        need(web['custody']['service_proof_sha256']==db['custody']['service_proof_sha256']
            and type(web['custody']['service_proof_sha256']) is str and len(web['custody']['service_proof_sha256'])==64)
    need((max(observations)-min(observations)).total_seconds()<=30)
    need(0<=(now-stamp(binding['issued_at'])).total_seconds()<=30)
    need(web['authority']=={'domain':'postgres-r2-authoritative','pending_phase':'activated','activation_lease_absent':True}
        and db['authority']['activation_lease_absent'] is True)
    return {'result':'pair_bound','expires_at':(min(min(observations),stamp(binding['issued_at']))+timedelta(seconds=30)).isoformat(),
        'nonce':binding['nonce'],'plan_sha256':binding['plan_sha256'],'phase':binding['phase']}
