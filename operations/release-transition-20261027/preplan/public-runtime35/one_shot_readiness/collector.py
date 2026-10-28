"""One-shot direct-SSH readonly original-evidence collection. No installation.

Never executes entry/CLI/wrappers, writes state, acquires locks, signals owners,
traverses providers, or edits a persisted snapshot. Unsupported custody REFUSES.
"""
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
import ast,hashlib,json,os,re,socket,stat,subprocess,sys,time,types
from pathlib import Path

ROOT=Path('/var/lib/careertrellis/paired-maintenance')
INSTALL=Path('/usr/local/libexec/careertrellis/paired_maintenance')
CORE=Path('/usr/local/libexec/careertrellis/backup_health.py')
RECEIPTS=Path('/var/backups/careertrellis/backup-health/operations')
SPECS={'web':('careertrellis-staging-web-1','objects','bb2434763298845c0b44fcc5793fdc7f3d4521174e3691e60cf283f5d8fc8f6c'),
 'db':('job-applier-db-staging-01','database','8d2facf6d71bf985c1c0ffe8b6a661fc9a9699548b6bb81163cd787f53ba97fa')}
REVISION='493bf14a3bae2cb2b36f3a57191602b72ea6e3d3'
BASE='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
PUBLIC_FILES={}
RUN_DEADLINE=None
VERIFIED_SOURCE_MANIFEST=None
ENV={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','PYTHONDONTWRITEBYTECODE':'1'}

class Refused(RuntimeError):pass
def need(value):
    if not value:raise Refused('one_shot_native_unverified')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()+b'\n'
def decode(raw):
    def pairs(items):
        result={}
        for k,v in items:need(k not in result);result[k]=v
        return result
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False));need(type(value) is dict);return value
def utc():return datetime.now(timezone.utc)
def public_document(name):
    need(name in ('installed493-pins.json','transaction04-pins.json'))
    return decode(PUBLIC_FILES[name] if name in PUBLIC_FILES else (Path(__file__).parent/name).read_bytes())

class Reader:
    def __init__(self):self.observed={};self.start=time.monotonic();self.units={};self.directories={};self.timer_properties={};self.checks=[]
    def read(self,path,mode=0o600,maximum=1048576):
        path=Path(path)
        for parent in path.parents:
            s=parent.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022)
        fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
        identity=lambda s:tuple(getattr(s,k) for k in fields)
        before=path.lstat();need(stat.S_ISREG(before.st_mode) and before.st_uid==before.st_gid==0
            and before.st_nlink==1 and stat.S_IMODE(before.st_mode)==mode and before.st_size<=maximum)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            need(identity(os.fstat(fd))==identity(before))
            with os.fdopen(fd,'rb',closefd=False) as f:raw=f.read(maximum+1)
            need(len(raw)==before.st_size and identity(os.fstat(fd))==identity(before) and identity(path.lstat())==identity(before))
        finally:os.close(fd)
        previous=self.observed.get(str(path));need(previous is None or previous==(mode,maximum,raw))
        self.observed[str(path)]=(mode,maximum,raw);return raw
    def stable(self):
        need(time.monotonic()-self.start<=30)
        for path,(mode,maximum,raw) in list(self.observed.items()):need(self.read(path,mode,maximum)==raw)
        for name,value in list(self.units.items()):need(self.unit(name,False)==value)
        for path,(mode,names) in list(self.directories.items()):self.directory(path,mode);need(self.directories[path][1]==names)
        for key,value in list(self.timer_properties.items()):need(timer_next(*key)==value)
        for check in self.checks:check()
    def timer_next(self,name,property_name):
        value=timer_next(name,property_name);key=(name,property_name)
        previous=self.timer_properties.get(key);need(previous is None or previous==value);self.timer_properties[key]=value;return value
    def json(self,path,mode=0o600):return decode(self.read(path,mode))
    def path(self,path):return Path(path)
    def directory(self,path,mode):
        p=Path(path);s=p.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0 and stat.S_IMODE(s.st_mode)==mode)
        names=sorted(x.name for x in p.iterdir());need(len(names)<=2048)
        previous=self.directories.get(str(p));need(previous is None or previous==(mode,names));self.directories[str(p)]=(mode,names)
    def unit(self,name,remember=True):
        need(re.fullmatch('careertrellis-[a-z-]+\\.(service|timer)',name))
        keys='LoadState,ActiveState,SubState,UnitFileState,MainPID,ControlPID,Job,Result,InvocationID,NRestarts,FragmentPath,DropInPaths,NeedDaemonReload,TimersCalendar,TimersMonotonic,Persistent,RandomizedDelayUSec,AccuracyUSec'
        value=dict(line.split('=',1) for line in run(['/usr/bin/systemctl','show',name,'--property='+keys]).decode().splitlines())
        need(value['LoadState']=='loaded' and value['NeedDaemonReload']=='no')
        if remember:
            old=self.units.get(name);need(old is None or old==value);self.units[name]=value
        return value

def run(args,maximum=1048576):
    need(type(args) is list and args[0].startswith('/'))
    remaining=3 if RUN_DEADLINE is None else min(3,RUN_DEADLINE-time.monotonic());need(remaining>0)
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=ENV,timeout=remaining)
    need(p.returncode==0 and len(p.stdout)<=maximum);return p.stdout

def module(name,raw):
    value=types.ModuleType(name);value.__file__=str(CORE);sys.modules[name]=value
    exec(compile(raw,str(CORE),'exec'),value.__dict__);return value

def runtime_identity_function(raw):
    # Extract only original readonly identity-probe declarations; never load the
    # runtime's fence/service/dispatch/Journal callers into the collector.
    names={'PROCESS_USERS','PROCESS_IDENTITY_PROBE','Refused','require','process_identity_args'}
    tree=ast.parse(raw);selected=[]
    for node in tree.body:
        name=getattr(node,'name',None)
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):name=node.targets[0].id
        if name in names:selected.append(node)
    need(len(selected)==len(names));scope={'re':re};tree=ast.Module(body=selected,type_ignores=[]);ast.fix_missing_locations(tree)
    exec(compile(tree,'installed493-readonly-process-identity','exec'),scope)
    return scope['process_identity_args']

def timer_next(name,property_name):
    need(name in ('careertrellis-paired-maintenance.timer','careertrellis-paired-recovery.timer'))
    need(property_name in ('NextElapseUSecRealtime','NextElapseUSecMonotonic'))
    encoded=''.join(c if c.isalnum() else '_'+format(ord(c),'02x') for c in name)
    raw=run(['/usr/bin/busctl','--system','get-property','org.freedesktop.systemd1',
        '/org/freedesktop/systemd1/unit/'+encoded,'org.freedesktop.systemd1.Timer',property_name]).decode().strip()
    matched=re.fullmatch('t ([0-9]+)',raw);need(matched is not None);return int(matched[1])

def core(reader,role):
    raw=reader.read(CORE,0o644);need(sha(raw)==SPECS[role][2])
    return module('_one_shot_health_'+role,raw)

def installed493(reader):
    pins=public_document('installed493-pins.json')
    need(pins['revision']==REVISION)
    manifest=decode(reader.read(INSTALL/'manifest.json',0o644))
    need(manifest['revision']==REVISION and set(manifest['files'])==set(pins['files']))
    sources={}
    for name,expected in pins['files'].items():
        need(re.fullmatch(r'[a-z_]+\.py',name) and manifest['files'][name]==expected)
        raw=reader.read(INSTALL/name,0o644);need(sha(raw)==expected);sources[name]=raw
    return sources

def request(value):
    need(type(value) is dict and set(value)=={'nonce','plan_sha256','release','build','phase','mode','predecessor','boots','source_commit','issued_at'})
    for key,n in [('nonce',64),('plan_sha256',64),('release',40),('source_commit',40)]:
        need(type(value[key]) is str and re.fullmatch('[0-9a-f]{%d}'%n,value[key]))
    need(value['phase'] in ('preflight','stopped','verifying','final') and type(value['boots']) is dict and set(value['boots'])=={'web','db'})
    need(value['mode']=={'preflight':'participant-preclaim','stopped':'owned-stopped','verifying':'owned-verifying','final':'owned-final'}[value['phase']])
    need(value['predecessor'] in (BASE,'bc68ea0dc253425a44e268452130becdf35d764e'))
    issued=datetime.fromisoformat(value['issued_at'].replace('Z','+00:00'));need(issued.tzinfo is not None and 0<=(utc()-issued).total_seconds()<=30)
    for boot in value['boots'].values():need(type(boot) is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',boot))
    need(type(value['build']) is str and re.fullmatch(r'[0-9]{8}\.[0-9]+',value['build']))
    return value

def native_custody(reader,role,binding,sources):
    # Integration-incomplete branches REFUSE rather than treating caller flags
    # or an old health snapshot as actual scheduler/transaction evidence.
    common=module('_one_shot_common493',sources['common.py'])
    # A source-bound participant can already hold fences at PRECLAIM. This
    # mode validates actual kernel owners; it never fabricates an intent.
    held=False;rows=Path('/proc/locks').read_text().splitlines()
    for path in (ROOT/role/'control.lock',Path('/run/lock/careertrellis-maintenance.lock')):
        s=path.lstat();key=f'{os.major(s.st_dev):02x}:{os.minor(s.st_dev):02x}:{s.st_ino}'
        held|=any(len(x.split())>=6 and x.split()[5].lower()==key.lower() for x in rows)
    if binding['phase']!='preflight' or held:
        from transaction04 import collect as collect_transaction
        standing_scheduler(reader)
        result=collect_transaction(reader,role,binding,common)
        if role=='web' and binding['phase'] in ('preflight','verifying','final'):
            for name in ('careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service'):
                unit=reader.unit(name);need(unit['ActiveState']=='active' and unit['SubState']=='running' and unit['Result']=='success' and unit['Job']=='')
                run(runtime_identity_function(sources['runtime.py'])(name,int(unit['MainPID']),binding['release']))
        return result
    need(binding['release']==BASE and binding['build']=='20261017.96')
    policy=common.Policy(decode(reader.read('/etc/careertrellis/paired-maintenance/policy.json')))
    public=common.Policy(decode(reader.read(ROOT/'public-policy.json',0o644)))
    need(policy.digest==public.digest and policy.data['manifest_revision']==REVISION
         and policy.data['application_release']==BASE and policy.data['application_build']==binding['build'])
    now=utc();d=policy.data
    need(datetime.fromisoformat(d['valid_from'].replace('Z','+00:00'))<=now<datetime.fromisoformat(d['valid_until'].replace('Z','+00:00')))
    need(d['enabled'] is True and d['timezone']=='America/New_York' and (d['weekday'],d['start_hour'],d['start_minute'])==(6,3,0))
    status=decode(reader.read(ROOT/role/'status.json'))
    need(status['boot_id']==binding['boots'][role] and status['policy_sha256']==policy.digest
        and status['release']==BASE and status['phase'] in ('idle','complete') and status['result']=='ok' and status['services_restored'] is True)
    reader.role=role;reader.boot=binding['boots'][role]
    historical_terminal(reader,policy,status,now)
    for path,expected in d['artifact_hashes'][role].items():
        mode=0o755 if path.startswith('/usr/local/sbin/') else 0o644
        need(sha(reader.read(path,mode))==expected)
    pins=public_document('installed493-pins.json')
    for name in ('careertrellis-paired-maintenance','careertrellis-paired-recovery'):
        service=reader.unit(name+'.service');timer=reader.unit(name+'.timer')
        need(service['ActiveState']=='inactive' and service['MainPID']==service['ControlPID']=='0' and service['Job']=='')
        need(timer['ActiveState']=='active' and timer['SubState']=='waiting' and timer['UnitFileState']=='enabled' and timer['Job']=='')
        for unit,value in ((name+'.service',service),(name+'.timer',timer)):
            path='/etc/systemd/system/'+unit
            need(value['FragmentPath']==path and value['DropInPaths']=='' and sha(reader.read(path,0o644))==pins['units'][path])
        need(timer['RandomizedDelayUSec']=='0' and timer['AccuracyUSec']=='1s')
        if name.endswith('maintenance'):
            need('OnCalendar=Sun *-*-* 03:00:00 America/New_York' in timer['TimersCalendar'] and timer['Persistent']=='yes')
            local=now.astimezone(ZoneInfo('America/New_York'))
            candidate=local.replace(hour=3,minute=0,second=0,microsecond=0)+timedelta(days=(6-local.weekday())%7)
            if candidate<=local:candidate+=timedelta(days=7)
            need(reader.timer_next(name+'.timer','NextElapseUSecRealtime')==int(candidate.timestamp())*1000000)
        else:
            need('OnBootUSec=30s' in timer['TimersMonotonic'] and 'OnUnitActiveUSec=1min' in timer['TimersMonotonic'])
            next_tick=reader.timer_next(name+'.timer','NextElapseUSecMonotonic')
            need(time.monotonic_ns()//1000<=next_tick<=time.monotonic_ns()//1000+61000000)
    # Kernel-owned lock metadata is observed, never flocked. No owner may be
    # guessed stale or removed; any matching holder refuses idle admission.
    locks=Path('/proc/locks').read_text().splitlines()
    paths=[ROOT/role/'control.lock',Path('/run/lock/careertrellis-maintenance.lock'),
        Path('/run/lock/careertrellis-generated-object-recovery.lock' if role=='web' else '/run/lock/careertrellis-pgbackrest-repository.lock')]
    for path in paths:
        s=path.lstat();need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022)
        key=f'{os.major(s.st_dev):02x}:{os.minor(s.st_dev):02x}:{s.st_ino}'
        need(not any(len(row.split())>=6 and row.split()[5].lower()==key.lower() for row in locks))
    need(not os.path.lexists('/run/careertrellis-hosted-activation.lease'))
    if role=='web':
        need(os.readlink('/opt/careertrellis/staging/current').rstrip('/').endswith('/'+BASE))
        marker=reader.json(ROOT/'writer-interlock.json',0o644)
        need(marker=={'schema':'careertrellis.paired-maintenance-interlock/v1','policy_sha256':policy.digest,
            'boot_id':reader.boot,'release':BASE,'phase':'idle'})
        for name in ('careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service'):
            unit=reader.unit(name);need(unit['ActiveState']=='active' and unit['SubState']=='running' and unit['Result']=='success' and int(unit['MainPID'])>0)
            run(runtime_identity_function(sources['runtime.py'])(name,int(unit['MainPID']),BASE))
    else:
        timer=reader.unit('careertrellis-pgbackrest-full.timer')
        need(timer['ActiveState']=='inactive' and timer['UnitFileState']=='disabled' and timer['Job']=='')
    return {'scheduler_verified':True,'mode':'idle','policy_sha256':policy.digest,'manifest_revision':REVISION,
        'schema_revision':d['schema_revision']}

def historical_terminal(reader,policy,status,now):
    """Exact493 closure rules, same approved original binding in EVERY mode."""
    from terminal493 import terminal,prior_intents
    terminal(reader,policy,status,now);prior_intents(reader)
    reader.directory(ROOT/reader.role/'runs',0o700)
    for path in (ROOT/reader.role/'runs').iterdir():
        need(re.fullmatch('[0-9a-f]{32}',path.name));reader.directory(path,0o700)
        if path.name==status.get('transaction'):continue
        prior=reader.json(path/'status.json')
        historical_run(reader,policy,prior,path.name,now)

def historical_run(reader,original_policy,prior,transaction,now):
    """Historical closure uses ORIGINAL policy and ORIGINAL boot, never97.

    A policy different from the authenticated saved baseline needs its own
    protected original policy snapshot; a digest/healthy flag is insufficient.
    """
    from terminal493 import terminal
    if prior.get('policy_sha256')!=original_policy.digest:
        raise Refused('historical_original_policy_snapshot_required')
    need(prior.get('state',prior.get('phase'))=='complete' and prior['release']==original_policy.data['application_release']
        and type(prior['boot_id']) is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',prior['boot_id']))
    class HistoricalView:
        boot=prior['boot_id']
        def __getattr__(self,key):return getattr(reader,key)
    projection={'schema':'careertrellis.paired-maintenance-status/v1','transaction':transaction,
        'policy_sha256':prior['policy_sha256'],'boot_id':prior['boot_id'],'release':prior['release'],
        'phase':'complete','result':'ok','services_restored':True,'observed_at':prior['observed_at']}
    # Original worker/started/complete records must prove this projection. The
    # temporary historical view does not rewrite a file/current boot/generation.
    terminal(HistoricalView(),original_policy,projection,now)

def standing_scheduler(reader):
    """Actual unchanged493 scheduler units/next triggers, including transition."""
    pins=public_document('installed493-pins.json')
    now=utc()
    for stem in ('careertrellis-paired-maintenance','careertrellis-paired-recovery'):
        for suffix in ('.service','.timer'):
            name=stem+suffix;value=reader.unit(name);path='/etc/systemd/system/'+name
            need(value['FragmentPath']==path and value['DropInPaths']=='' and sha(reader.read(path,0o644))==pins['units'][path])
            if suffix=='.service':need(value['ActiveState']=='inactive' and value['MainPID']==value['ControlPID']=='0' and value['Job']=='')
            else:
                need(value['ActiveState']=='active' and value['SubState']=='waiting' and value['UnitFileState']=='enabled'
                    and value['Job']=='' and value['RandomizedDelayUSec']=='0' and value['AccuracyUSec']=='1s')
                if stem.endswith('maintenance'):
                    need('OnCalendar=Sun *-*-* 03:00:00 America/New_York' in value['TimersCalendar'] and value['Persistent']=='yes')
                    local=now.astimezone(ZoneInfo('America/New_York'));candidate=local.replace(hour=3,minute=0,second=0,microsecond=0)+timedelta(days=(6-local.weekday())%7)
                    if candidate<=local:candidate+=timedelta(days=7)
                    need(reader.timer_next(name,'NextElapseUSecRealtime')==int(candidate.timestamp())*1000000)
                else:
                    need('OnBootUSec=30s' in value['TimersMonotonic'] and 'OnUnitActiveUSec=1min' in value['TimersMonotonic'])
                    tick=reader.timer_next(name,'NextElapseUSecMonotonic');need(time.monotonic_ns()//1000<=tick<=time.monotonic_ns()//1000+61000000)
    if reader.role=='db':
        legacy=reader.unit('careertrellis-pgbackrest-full.timer')
        need(legacy['ActiveState']=='inactive' and legacy['UnitFileState']=='disabled' and legacy['Job']=='')

def authority(reader,role):
    need(not os.path.lexists('/run/careertrellis-hosted-activation.lease'))
    if role!='web':return {'domain':'web-authority-verified-by-peer','activation_lease_absent':True}
    value=reader.read('/etc/careertrellis/authority/data-authority',0o644).decode().strip()
    need(value=='postgres-r2-authoritative')
    lines=reader.read('/etc/careertrellis/authority/pending-cutover').decode().splitlines()
    need(len(lines)==8 and lines[0]=='version=1' and lines[1]=='phase=activated')
    # Remaining old-cutover source/config digest fields remain local only.
    expected=[r'attempt_id=[0-9a-f]{32}',r'release=[0-9a-f]{40}',r'migration_environment_sha256=[0-9a-f]{64}',
        r'object_environment_sha256=[0-9a-f]{64}',r'provider_environment_sha256=[0-9a-f]{64}',r'source_snapshot_sha256=[0-9a-f]{64}']
    need(all(re.fullmatch(pattern,line) for pattern,line in zip(expected,lines[2:])))
    return {'domain':'postgres-r2-authoritative','pending_phase':'activated','activation_lease_absent':True}

DB_UNITS={'full_backup':'careertrellis-pgbackrest-full','differential_backup':'careertrellis-pgbackrest-differential',
 'pgbackrest_mirror':'careertrellis-pgbackrest-onedrive','pgbackrest_inventory':'careertrellis-pgbackrest-inventory',
 'pgbackrest_restore':'careertrellis-pgbackrest-restore'}

def inventory_digest(health,inventory):
    value={'invalid_operations':sorted(inventory.invalid_operations),'receipts':{}}
    for operation,receipts in sorted(inventory.receipts.items()):
        value['receipts'][operation]=sorted(sha(health.serialize_operation_receipt(x)) for x in receipts)
    return sha(canonical(value))

def original_units(reader,role,health,inventory,scheduler_verified):
    need(scheduler_verified is True) # produced only by local native custody
    roots={'web':'objects','db':'database'}
    alerts=Path('/var/lib/careertrellis/backup-health')/roots[role]/'unit-alerts'
    reader.directory(alerts,0o700)
    info=alerts.lstat();need(stat.S_ISDIR(info.st_mode) and info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==0o700)
    entries=list(alerts.iterdir());need(len(entries)<=32)
    for path in entries:reader.read(path)
    # Exact wrapper audit treats unresolved health-service and malformed/unknown
    # alert leaves as failures too. Strict refusal is stronger than accepting an
    # inventory merely because a backupstem-specific filename is absent.
    need(not entries)
    result={}
    for operation in health.SURFACE_OPERATIONS[SPECS[role][1]]:
        receipt=health._latest(inventory.receipts[operation],successful=True)
        last='success' if receipt is not None else 'never'
        missed=False
        if role=='db':
            stem=DB_UNITS[operation];service=reader.unit(stem+'.service');timer=reader.unit(stem+'.timer')
            if service['Result'] not in ('','success'):last='failed'
            missed=not(timer['ActiveState']=='active' and timer['UnitFileState']=='enabled')
            if operation=='full_backup':missed=not scheduler_verified
            if any(p.name==stem+'.service.alert.json' for p in entries):last='failed'
        elif entries:last='failed' # actual health-service alert wins all object inputs
        if operation in inventory.invalid_operations:last='failed'
        result[operation]={'last_result':last,'missed':missed}
    return result

def database_fields(reader):
    # Metadata-only info is single-attempt and must be separately authorized for
    # actual collection; no list/check/backup/mirror/restore or retry is present.
    for stem in DB_UNITS.values():
        unit=reader.unit(stem+'.service');need(unit['MainPID']==unit['ControlPID']=='0' and unit['Job']=='')
    entries=list(Path('/proc').iterdir());need(len(entries)<=8192)
    for path in entries:
        if not path.name.isdecimal():continue
        try:name=(path/'comm').read_text().strip()
        except FileNotFoundError:continue
        need(name not in ('pgbackrest','rclone'))
    need(sha(reader.read('/usr/local/sbin/careertrellis-run-pgbackrest-backup',0o755))=='70d95eafce97e6bae0ba6cf28b61c9d764a7b82ce9bd4fb67843bedf642ec25e')
    raw=run(['/usr/bin/setpriv','--reuid=postgres','--regid=postgres','--init-groups','--reset-env','--',
        '/usr/bin/pgbackrest','--stanza=careertrellis','--output=json','info'])
    info=json.loads(raw);need(type(info) is list and len(info)==1 and info[0]['name']=='careertrellis' and info[0]['status']['code']==0)
    latest=max(info[0]['backup'],key=lambda b:b['timestamp']['stop'])['label']
    need(re.fullmatch(r'[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?',latest))
    full=[b for b in info[0]['backup'] if b['type']=='full'];need(bool(full))
    latest_full=max(full,key=lambda b:b['timestamp']['stop'])['label'];need(re.fullmatch('[0-9]{8}-[0-9]{6}F',latest_full))
    reader.repository={'latest_backup_label':latest,'latest_full_backup_label':latest_full,'checked_at':utc().isoformat()}
    archive=max((x['max'] for x in info[0]['archive'] if type(x.get('max')) is str),default='')
    wal=run(['/usr/sbin/runuser','-u','postgres','--','/usr/bin/psql','-X','-qAt','-w','-v','ON_ERROR_STOP=1',
        '-h','/var/run/postgresql','-d','careertrellis','-c',
        "BEGIN READ ONLY; SET LOCAL statement_timeout='1s'; SELECT failed_count, COALESCE(last_archived_wal, ''), pg_size_bytes(current_setting('wal_segment_size')), (SELECT version_num FROM careertrellis.alembic_version) FROM pg_stat_archiver; ROLLBACK;"]).decode().strip().split('|')
    need(len(wal)==4 and wal[0].isdigit() and wal[2].isdigit() and wal[3]=='20261005_0023')
    failed=int(wal[0]);segment=int(wal[2]);need(segment>0 and 4294967296%segment==0)
    baseline=reader.read('/var/lib/careertrellis/backup-health/database/wal-failed-count').strip();need(baseline.isdigit())
    lag=2;behind=True
    if re.fullmatch('[0-9A-F]{24}',archive) and re.fullmatch('[0-9A-F]{24}',wal[1]) and archive[:8]==wal[1][:8]:
        per=4294967296//segment
        index=lambda s:int(s[8:16],16)*per+int(s[16:],16)
        lag=max(0,index(wal[1])-index(archive));behind=index(archive)<index(wal[1])
    return {'latest_backup_label':latest,'wal':{'failed_count':failed,'previous_failed_count':int(baseline),
        'segment_lag':lag,'repository_archive_max_behind_database':behind}}

def verify_inventory_generation(health,inventory,surface,evaluated_at):
    evaluation=datetime.fromisoformat(evaluated_at.replace('Z','+00:00'));need(evaluation.tzinfo is not None)
    operations=health.SURFACE_OPERATIONS[surface];need(not set(inventory.invalid_operations).intersection(operations))
    summaries={}
    for operation in operations:
        rows=inventory.receipts[operation];need(bool(rows))
        parsed=[(datetime.fromisoformat(row['completed_at'].replace('Z','+00:00')),row) for row in rows]
        need(all(t.tzinfo is not None and t<=evaluation for t,_ in parsed))
        latest_time=max(t for t,_ in parsed);ties=[row for t,row in parsed if t==latest_time]
        need(all(row['result']=='ok' for row in ties))
        latest=health._latest(rows)
        summaries[operation]={k:latest[k] for k in ('result','completed_at','backup_label') if k in latest}
        summaries[operation]['receipt_sha256']=sha(health.serialize_operation_receipt(latest))
    return summaries

def persisted_status_qualifies(role,status,now):
    evaluated=datetime.fromisoformat(status['evaluated_at'].replace('Z','+00:00'))
    need(evaluated.tzinfo is not None and 0<=(now-evaluated).total_seconds()<=10800)
    if status['result']=='ok':need(status['failure_count']==0 and status['failure_codes']==[])
    else:need(role=='db' and status['result']=='alert' and status['failure_count']==1 and status['failure_codes']==['full_backup_unit_missed'])

def fresh_snapshot(reader,role,health,custody):
    reader.json(ROOT/role/'status.json')
    if role=='web':
        reader.directory(ROOT/'web',0o700)
        for path in (ROOT/'web').iterdir():
            if path.name.startswith(('lock-timeout-','verified-generation')):reader.read(path)
    before=health.scan_receipts(root=RECEIPTS);before_sha=inventory_digest(health,before)
    snapshot={'schema':health.SNAPSHOT_SCHEMA,'surface':SPECS[role][1],'observed_at':utc().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'units':original_units(reader,role,health,before,custody['scheduler_verified'])}
    latest_receipts=verify_inventory_generation(health,before,SPECS[role][1],snapshot['observed_at'])
    if role=='db':snapshot.update(database_fields(reader))
    # Exact installed canonical evaluator scans original receipt inventory itself.
    # No persisted status/snapshot is copied or edited to construct this input.
    evaluated=health.evaluate_backup_health(snapshot,receipt_root=RECEIPTS)
    after=health.scan_receipts(root=RECEIPTS);after_sha=inventory_digest(health,after)
    need(after==before and after_sha==before_sha and evaluated['evaluated_at']==snapshot['observed_at'])
    return snapshot,evaluated,before_sha,after_sha,latest_receipts

def collect(role,binding):
    global RUN_DEADLINE
    request(binding);need(role in SPECS and os.geteuid()==0 and socket.gethostname()==SPECS[role][0])
    issued=datetime.fromisoformat(binding['issued_at'].replace('Z','+00:00'));RUN_DEADLINE=time.monotonic()+30-(utc()-issued).total_seconds()
    reader=Reader();reader.role=role;observed=utc();boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip();reader.boot=boot
    need(boot==binding['boots'][role])
    clock=run(['/usr/bin/timedatectl','show','--property=NTPSynchronized,Timezone']).decode()
    need(set(clock.splitlines())=={'NTPSynchronized=yes','Timezone=Etc/UTC'})
    sources=installed493(reader);health=core(reader,role)
    authority_result=authority(reader,role)
    custody=native_custody(reader,role,binding,sources)
    status_root=Path('/var/lib/careertrellis/backup-health')/SPECS[role][1]
    persisted=health.checked_status(surface=SPECS[role][1],root=status_root,now=utc())
    persisted_status_qualifies(role,persisted,utc())
    snapshot,evaluated,before_sha,after_sha,latest_receipts=fresh_snapshot(reader,role,health,custody)
    again=health.checked_status(surface=SPECS[role][1],root=status_root,now=utc())
    need(again==persisted);persisted_status_qualifies(role,again,utc())
    reader.stable();need(boot==Path('/proc/sys/kernel/random/boot_id').read_text().strip() and time.monotonic()<RUN_DEADLINE)
    generation={'binding':binding,'core_sha256':SPECS[role][2],'collector_manifest_sha256':VERIFIED_SOURCE_MANIFEST,
        'snapshot_sha256':sha(canonical(snapshot)),'result_sha256':sha(canonical(evaluated)),
        'inventory_before_sha256':before_sha,'inventory_after_sha256':after_sha,
        'evaluated_at':evaluated['evaluated_at'],'failure_count':evaluated['failure_count']}
    return {'binding':binding,'role':role,'observed_at':observed.isoformat(),'boot':boot,'core_sha256':SPECS[role][2],
        'custody':custody,
        'authority':authority_result,
        'snapshot_sha256':sha(canonical(snapshot)),'result_sha256':sha(canonical(evaluated)),
        'original_snapshot':snapshot,
        'latest_receipts':latest_receipts,'repository':getattr(reader,'repository',None),
        'latest_accepted_full_label':latest_receipts['full_backup']['backup_label'] if role=='db' else None,
        'receipt_inventory_sha256':before_sha,'result':evaluated['result'],'failure_codes':evaluated['failure_codes'],
        'failure_count':evaluated['failure_count'],'canonical_evaluation':evaluated,
        'inventory_verified':True,'evaluation_generation_current':True,'evaluation_generation':generation,
        'evaluation_generation_sha256':sha(canonical(generation)),
        'persisted_status':persisted,'persisted_status_unchanged':True}
