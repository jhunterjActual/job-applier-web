"""Original accepted pair evidence; remote raw bytes stay private in memory."""
import os,re,types
from pathlib import Path
from datetime import datetime,timezone
from native_io import need,sha,decode,retained_run
TX='45a42a288e304da8a25e99dbbbd62258'
LABEL='20261026-155056F'
FULL='0bf45e020bbac0bbdc05f473549a3feea4091f466ee7da906e8ef7cf94fdb0c3'
PINS={'web':'19743124faf4864a8e5efdde5b9f416cc77e0070967dc6f2c487807a07262f09','db':'42fec6f6ff56c4c2c4021d717458dc620542cd68e88b06ac32cd4b7762d84f98','metadata':'9f01ad709f033e9eec5cd765a2276076a982e594a155824173d3acc746cbe78b','manifest':'d346b4727d7443098ce0d4f1efe8e8fa96165b0191b8c1681ff20bdb9794aa20'}
BASE='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
TARGET='bc68ea0dc253425a44e268452130becdf35d764e'
CORRECTION='f70417d4bd70c3eb086ea63c90938dd2f43e0b99'

def cleanup_absent():
 need(not os.path.lexists('/var/lib/careertrellis-paired-db-acceptance/'+TX))
 for prefix in ('careertrellis-paired-acceptance-cleanup-','careertrellis-retained-start-recovery-'):
  fields={'ActiveState','MainPID','ControlPID','Job','ControlGroup'}
  raw=retained_run(['/usr/bin/systemctl','show',prefix+TX+'.service','--property='+','.join(sorted(fields))],16384)
  rows=raw.decode('ascii').splitlines();need(len(rows)==len(fields));state=dict(row.split('=',1) for row in rows)
  need(set(state)==fields and state['ActiveState'] in ('inactive','failed') and state['MainPID']==state['ControlPID']=='0' and state['Job']=='')
  group=state['ControlGroup']
  if group:
   need(group.startswith('/') and '..' not in group.split('/'))
   p=Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.procs'
   try:fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
   except FileNotFoundError:continue
   try:body=os.read(fd,65537);need(len(body)<=65536 and body.strip()==b'')
   finally:os.close(fd)

def local(role,reader):
 need(type(role)is str and role in ('web','db'))
 if role=='web':
  raw=reader.read('/var/lib/careertrellis/paired-acceptance-support/'+TX+'/plaintext-acceptance.json')
  for key,path in (('metadata','/var/lib/careertrellis/paired-acceptance-support/'+TX+'/object-metadata.json'),('manifest','/var/lib/careertrellis/paired-object-acceptance/'+TX+'/snapshot-manifest.json')):
   body=reader.read(path,4*1024**2);need(sha(body)==PINS[key] and type(decode(body))is dict)
 else:
  cleanup_absent();raw=reader.read('/var/backups/careertrellis/paired-restore-receipts/retained-'+TX+'.json')
 need(sha(raw)==PINS[role]);return raw

def validate(public,webraw,dbraw,checked_at):
 module=types.ModuleType('preplan_authenticated_pair');exec(compile(public['accepted_pair_validator.py'],'authenticated-current-pair-validator','exec'),module.__dict__)
 # Whole source is already exact packet/Git verified; immutable historical hashes remain unchanged.
 need(module.BASE==BASE and module.TARGET==TARGET and module.CORRECTION==CORRECTION and module.WEB_DIGEST==PINS['web'] and module.DB_DIGEST==PINS['db'])
 return module.validate_pair(webraw,dbraw,{'baseline':BASE,'target':TARGET,'correction':CORRECTION,'accepted_backup_label':LABEL,'checked_at':checked_at},now=datetime.now(timezone.utc))
