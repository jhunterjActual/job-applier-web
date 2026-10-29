"""One-shot direct-SSH readonly original-evidence collection. No installation.

Never executes entry/CLI/wrappers, writes state, acquires locks, signals owners,
traverses providers, or edits a persisted snapshot. Unsupported custody REFUSES.
"""
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
import ast,hashlib,json,os,re,socket,stat,subprocess,sys,time,types
from pathlib import Path,PurePosixPath

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
    def __init__(self):self.observed={};self.start=time.monotonic();self.units={};self.directories={};self.timer_properties={};self.timer_cadences={};self.cadences={};self.timer_array_records={};self.artifact_pins={};self.artifacts={};self.checks=[]
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
    def read_artifact(self,path,expected,maximum=1048576):
        need(type(maximum) is int and 0<maximum<=1048576)
        need(type(path) is str and str(PurePosixPath(path))==path and path.startswith('/') and '..' not in PurePosixPath(path).parts)
        need(type(expected) is str and re.fullmatch('[0-9a-f]{64}',expected) and self.artifact_pins.get(path)==expected)
        p=Path(path)
        for parent in p.parents:
            s=parent.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022)
        fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
        identity=lambda s:tuple(getattr(s,k) for k in fields)
        before=p.lstat();need(stat.S_ISREG(before.st_mode) and before.st_uid==before.st_gid==0 and before.st_nlink==1
            and stat.S_IMODE(before.st_mode) in (0o600,0o644,0o755) and 0<before.st_size<=maximum)
        fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            need(identity(os.fstat(fd))==identity(before))
            with os.fdopen(fd,'rb',closefd=False) as f:raw=f.read(maximum+1)
            need(len(raw)==before.st_size and identity(os.fstat(fd))==identity(before) and identity(p.lstat())==identity(before) and sha(raw)==expected)
        finally:os.close(fd)
        record=(expected,maximum,identity(before),raw);previous=self.artifacts.get(path);need(previous is None or previous==record)
        self.artifacts[path]=record;return raw

    def timer_cadence(self,name,remember=True):
        need(type(name) is str and name in ('careertrellis-paired-maintenance.timer','careertrellis-paired-recovery.timer'))
        keys=('Persistent','RandomizedDelayUSec','AccuracyUSec')
        raw=run(['/usr/bin/systemctl','show','--all',name,'--property='+','.join(keys)],maximum=16384)
        value=unit_properties(raw,keys)
        if remember:
            previous=self.cadences.get(name);need(previous is None or previous==value);self.cadences[name]=value
        return value

    def stable(self):
        need(time.monotonic()-self.start<=30)
        for path,(mode,maximum,raw) in list(self.observed.items()):need(self.read(path,mode,maximum)==raw)
        for name,value in list(self.units.items()):need(self.unit(name,False)==value)
        for path,(mode,names) in list(self.directories.items()):self.directory(path,mode);need(self.directories[path][1]==names)
        for key,value in list(self.timer_properties.items()):need(timer_next(*key)==value)
        for name,value in list(self.timer_cadences.items()):need(timer_monotonic(name)==value)
        for name,value in list(self.cadences.items()):need(self.timer_cadence(name,False)==value)
        for path,(expected,maximum,identity,raw) in list(self.artifacts.items()):need(self.read_artifact(path,expected,maximum)==raw)
        for name,record in list(self.timer_array_records.items()):need(timer_array_record(name)==record)
        for check in self.checks:check()
        need(time.monotonic()-self.start<=30)
    def timer_arrays(self,name):
        record=timer_array_record(name);previous=self.timer_array_records.get(name)
        need(previous is None or previous==record);self.timer_array_records[name]=record
        return {'calendar_timers':record[2],'monotonic_timers':record[3]}
    def timer_next(self,name,property_name):
        value=timer_next(name,property_name);key=(name,property_name)
        previous=self.timer_properties.get(key);need(previous is None or previous==value);self.timer_properties[key]=value;return value
    def timer_monotonic(self,name):
        value=timer_monotonic(name);previous=self.timer_cadences.get(name)
        need(previous is None or previous==value);self.timer_cadences[name]=value;return value
    def json(self,path,mode=0o600):return decode(self.read(path,mode))
    def path(self,path):return Path(path)
    def directory(self,path,mode):
        p=Path(path);s=p.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0 and stat.S_IMODE(s.st_mode)==mode)
        names=sorted(x.name for x in p.iterdir());need(len(names)<=2048)
        previous=self.directories.get(str(p));need(previous is None or previous==(mode,names));self.directories[str(p)]=(mode,names)
    def unit(self,name,remember=True):
        need(type(name) is str and (re.fullmatch('careertrellis-[a-z-]+\\.(service|timer)',name) or re.fullmatch(r'careertrellis-paired-db(?:-recovery)?@(?:[0-9a-f]{32})?\.service',name)))
        fields=TIMER_UNIT_FIELDS if name.endswith('.timer') else SERVICE_UNIT_FIELDS+('InvocationID','NRestarts','UnitFileState')
        value=unit_properties(run(['/usr/bin/systemctl','show','--all',name,'--property='+','.join(fields)],maximum=16384),fields)
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
    source=INSTALL/'common.py' if name=='_one_shot_common493' else CORE
    value=types.ModuleType(name);value.__file__=str(source);sys.modules[name]=value
    exec(compile(raw,str(source),'exec'),value.__dict__);return value

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
    artifact_policy=common.Policy(decode(reader.read('/etc/careertrellis/paired-maintenance/policy.json')))
    verify_effective_artifacts(reader,artifact_policy,role)
    # A source-bound participant can already hold fences at PRECLAIM. This
    # mode validates actual kernel owners; it never fabricates an intent.
    held=False;rows=Path('/proc/locks').read_text().splitlines()
    for path in (ROOT/role/'control.lock',Path('/run/lock/careertrellis-maintenance.lock')):
        s=path.lstat();key=f'{os.major(s.st_dev):02x}:{os.minor(s.st_dev):02x}:{s.st_ino}'
        held|=any(len(x.split())>=6 and x.split()[5].lower()==key.lower() for x in rows)
    if binding.get('schema')=='careertrellis.preplan-scheduler-binding/v1':need(not held)
    if binding['phase']!='preflight' or held:
        from transaction04 import collect as collect_transaction
        result=collect_transaction(reader,role,binding,common)
        result['scheduler_verified']=False
        result['scheduler_coverage']=local_scheduler_coverage(reader,role,binding,result)
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
    result={'scheduler_verified':False,'mode':'idle','policy_sha256':policy.digest,'manifest_revision':REVISION,
        'schema_revision':d['schema_revision']}
    result['scheduler_coverage']=local_scheduler_coverage(reader,role,binding,result)
    return result

SOURCE_CATALOG493={'runtime.py': '0a62282a15f62b2103f88bdfe195f961cbbe4ae815fee02bac764398858f5604', 'host.py': 'b9b8d1869c8b5199f6c56889ce836e178911e45c2e4365b406d3544ba45c10b2', 'web_control.py': 'f2e0902257df78dcca0a9b17049bb76160cc39a997fcbfbe86d4f5b726f3c594', 'common.py': 'b1b14dc5ac8c824836d1342291de83ba747f902f7398dd9af244544face2cb44'}
ARCHIVE_KEYS={'release', 'old_policy_sha256', 'role', 'result', 'prepared_at', 'new_policy_sha256', 'boot_id', 'installed_revision', 'schema'}
ARCHIVE_FILES={'intent.json','success.json','old-policy.json','old-status.json'}
from itertools import islice
def stamp(value):
 need(type(value)is str);t=datetime.fromisoformat(value.replace('Z','+00:00'));need(t.tzinfo is not None);return t

class HistoricalView:
 def __init__(self,reader,boot):self.reader=reader;self.boot=boot
 def __getattr__(self,name):return getattr(self.reader,name)

def archive(reader,common,terminal,role,path,revision,now):
 reader.directory(path,0o700);need({leaf.name for leaf in islice(reader.path(path).iterdir(),5)}==ARCHIVE_FILES)
 intent=reader.json(path/'intent.json');success=reader.json(path/'success.json');old=common.Policy(reader.json(path/'old-policy.json'));status=reader.json(path/'old-status.json')
 need(type(intent)is dict and set(intent)==ARCHIVE_KEYS and intent['schema']=='careertrellis.paired-policy-transition/v1' and intent['role']==role and intent['new_policy_sha256']==path.name and intent['old_policy_sha256']==old.digest and intent['old_policy_sha256']!=intent['new_policy_sha256'] and type(intent['installed_revision'])is str and re.fullmatch('[0-9a-f]{40}',intent['installed_revision']) and old.data.get('manifest_revision')==intent['installed_revision'] and type(intent['release'])is str and re.fullmatch('[0-9a-f]{40}',intent['release']) and intent['result']=='prepared')
 need(success==dict(intent,result='applied') and type(intent['boot_id'])is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',intent['boot_id']) and stamp(status['observed_at'])<=stamp(intent['prepared_at'])<=now)
 need(type(status['boot_id'])is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',status['boot_id']))
 need(reader.read(path/'old-policy.json')==common.canonical(old.data))
 terminal(HistoricalView(reader,status['boot_id']),old,status,now)
 return old,status,intent

def web_prestart_record(reader,policy,prior,path,now):
    keys={'state','transaction','policy_sha256','release','boot_id','worker_invocation','observed_at','result','services_restored'}
    need(type(prior)is dict and set(prior)==keys)
    for key in keys-{'services_restored'}:need(type(prior[key])is str)
    need(prior['state'] in ('preflight','blocked','operator_required','failed') and prior['result'] in ('running','failed','blocked') and prior['services_restored']is True)
    need(prior['transaction']==path.name and re.fullmatch('[0-9a-f]{32}',prior['transaction']) and prior['policy_sha256']==policy.digest and prior['release']==policy.data['application_release'] and re.fullmatch('[0-9a-f]{32}',prior['worker_invocation']) and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',prior['boot_id']) and stamp(prior['observed_at'])<=now)
    raw=reader.read(path/'status.json');snapshot=reader.read(path/'prestart-original-status.json');record=reader.json(path/'prestart-aborted.json')
    need(raw==snapshot and sha(raw)==record.get('original_status_sha256'))

def database_complete_record(policy,prior,path):
    keys={'state','transaction','policy_sha256','release','boot_id','services_restored','worker_invocation'}
    need(type(prior)is dict and set(prior)==keys)
    for key in keys-{'services_restored'}:need(type(prior[key])is str)
    need(prior['state']=='complete' and prior['services_restored']is True and prior['transaction']==path.name and re.fullmatch('[0-9a-f]{32}',prior['transaction']) and re.fullmatch('[0-9a-f]{32}',prior['worker_invocation']))
    need(prior['policy_sha256']==policy.digest and prior['release']==policy.data['application_release'] and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',prior['boot_id']))

def private_complete_record(policy,prior,path,now):
    keys={'state','transaction','policy_sha256','release','boot_id','worker_invocation','observed_at','result','services_restored'}
    need(type(prior)is dict and set(prior)==keys)
    for key in ('state','transaction','policy_sha256','release','boot_id','worker_invocation','observed_at','result'):need(type(prior[key])is str)
    need(prior['state']=='complete' and prior['result']=='ok' and prior['services_restored']is True)
    need(prior['transaction']==path.name and re.fullmatch('[0-9a-f]{32}',prior['transaction']) and re.fullmatch('[0-9a-f]{32}',prior['worker_invocation']))
    need(prior['policy_sha256']==policy.digest and prior['release']==policy.data['application_release'] and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',prior['boot_id']))
    need(stamp(prior['observed_at'])<=now)

def web_complete_proof(reader,policy,path,ordered=True):
    complete=reader.json(path/'complete.json');need(type(complete)is dict and set(complete)=={'state','transaction','policy_sha256','services_restored','backup_label','manifest_sha256'})
    need(complete['state']=='complete' and complete['transaction']==path.name and complete['policy_sha256']==policy.digest and complete['services_restored']is True)
    need(type(complete['backup_label'])is str and re.fullmatch('[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?',complete['backup_label']) and type(complete['manifest_sha256'])is str and re.fullmatch('[0-9a-f]{64}',complete['manifest_sha256']))
    if ordered:
        a=reader.path(path/'complete.json').lstat();b=reader.path(path/'status.json').lstat();need(a.st_mtime_ns<=b.st_mtime_ns)
        reader.checks.append(lambda:need(reader.path(path/'complete.json').lstat().st_mtime_ns==a.st_mtime_ns and reader.path(path/'status.json').lstat().st_mtime_ns==b.st_mtime_ns))

def derived_complete_input(reader,terminal,policy,prior,path,now,revision):
    # Pure declared-format interpretation; not an original global snapshot or execution attestation.
    for name,expected in SOURCE_CATALOG493.items():need(sha(reader.read(INSTALL/name,0o644))==expected)
    need(policy.data.get('manifest_revision')==revision);private_complete_record(policy,prior,path,now)
    view={'schema':'careertrellis.paired-maintenance-status/v1','transaction':prior['transaction'],'policy_sha256':prior['policy_sha256'],'release':prior['release'],'boot_id':prior['boot_id'],'phase':prior['state'],'result':prior['result'],'services_restored':prior['services_restored'],'observed_at':prior['observed_at']}
    web_complete_proof(reader,policy,path)
    terminal(HistoricalView(reader,prior['boot_id']),policy,view,now)
    return {'interpretation':'DERIVED_INPUT','original_global_snapshot':False,'readiness_authority':False}

def historical_terminal(reader,policy,status,now):
    from terminal493 import terminal,prior_intents
    terminal(reader,policy,status,now);prior_intents(reader)
    pins=public_document('installed493-pins.json');common_raw=reader.read(INSTALL/'common.py',0o644);need(sha(common_raw)==pins['files']['common.py']);common=module('_one_shot_common493',common_raw)
    bodies={policy.digest:policy};statuses=[status]
    root=ROOT/reader.role/'policy-transitions'
    if os.path.lexists(reader.path(root)):
        reader.directory(root,0o700);entries=list(islice(reader.path(root).iterdir(),129));need(len(entries)<=128)
        for path in entries:
            need(re.fullmatch('[0-9a-f]{64}',path.name));old,original,intent=archive(reader,common,terminal,reader.role,path,REVISION,now)
            bodies[old.digest]=old;statuses.append(original)
    else:reader.checks.append(lambda:need(not os.path.lexists(reader.path(root))))
    runs=ROOT/reader.role/'runs';reader.directory(runs,0o700);entries=list(islice(reader.path(runs).iterdir(),2049));need(len(entries)<=2048)
    for path in entries:
        need(re.fullmatch('[0-9a-f]{32}',path.name));reader.directory(path,0o700)
        if path.name==status.get('transaction'):continue
        prior=reader.json(path/'status.json');need(type(prior)is dict and type(prior.get('policy_sha256'))is str and prior['policy_sha256'] in bodies);original_policy=bodies[prior['policy_sha256']]
        candidates=[x for x in statuses if x.get('transaction')==path.name and x.get('policy_sha256')==original_policy.digest]
        if candidates:
            original=candidates[0]
            need(stamp(original['observed_at'])<=now)
            if reader.role=='web' and original['phase'] in ('failed','blocked','preflight'):
                need(original['result']=='failed' and original['services_restored']is True);web_prestart_record(reader,original_policy,prior,path,now)
            else:
                need(original['phase']=='complete' and original['result']=='ok' and original['services_restored']is True and stamp(original['observed_at'])<=now)
                if reader.role=='web':
                    private_complete_record(original_policy,prior,path,now)
                    known493=original_policy.data.get('manifest_revision')==REVISION
                    if known493:
                        for name,expected in SOURCE_CATALOG493.items():need(sha(reader.read(INSTALL/name,0o644))==expected)
                        need(stamp(original['observed_at'])<=stamp(prior['observed_at']))
                    web_complete_proof(reader,original_policy,path,known493)
                else:
                    need(reader.role=='db');database_complete_record(original_policy,prior,path)
                    complete=reader.json(path/'complete-result.json');need(type(complete)is dict and set(complete)=={'transaction','policy_sha256','backup_label','manifest_sha256'} and complete['transaction']==path.name and complete['policy_sha256']==original_policy.digest)
                    need(type(complete['backup_label'])is str and re.fullmatch('[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?',complete['backup_label']) and type(complete['manifest_sha256'])is str and re.fullmatch('[0-9a-f]{64}',complete['manifest_sha256']))
            need(prior['boot_id']==original['boot_id'] and prior['release']==original['release']);terminal(HistoricalView(reader,original['boot_id']),original_policy,original,now)
        else:
            need(reader.role=='web');derived_complete_input(reader,terminal,original_policy,prior,path,now,REVISION)


def web_standing_scheduler(reader):
    """Actual unchanged493 scheduler units/next triggers, including transition."""
    pins=public_document('installed493-pins.json')
    now=utc()
    for stem in ('careertrellis-paired-maintenance','careertrellis-paired-recovery'):
        for suffix in ('.service','.timer'):
            name=stem+suffix;value=reader.unit(name);path='/etc/systemd/system/'+name
            need(value['FragmentPath']==path and value['DropInPaths']=='' and sha(reader.read(path,0o644))==pins['units'][path])
            if suffix=='.service':need(value['ActiveState']=='inactive' and value['MainPID']==value['ControlPID']=='0' and value['Job']=='')
            else:
                cadence=reader.timer_cadence(name);arrays=reader.timer_arrays(name);validate_unit_timer_arrays(name,arrays)
                need(value['ActiveState']=='active' and value['SubState']=='waiting' and value['UnitFileState']=='enabled'
                    and value['Job']=='' and cadence['RandomizedDelayUSec']=='0' and cadence['AccuracyUSec']=='1s')
                if stem.endswith('maintenance'):
                    need(cadence['Persistent']=='yes')
                    local=now.astimezone(ZoneInfo('America/New_York'));candidate=local.replace(hour=3,minute=0,second=0,microsecond=0)+timedelta(days=(6-local.weekday())%7)
                    if candidate<=local:candidate+=timedelta(days=7)
                    need(arrays['calendar_timers'][0]['next_elapse_usec']==reader.timer_next(name,'NextElapseUSecRealtime')==int(candidate.timestamp())*1000000)
                else:
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

def original_units(reader,role,health,inventory,custody):
    coverage=custody['scheduler_coverage'];validate_local_coverage(coverage,role,custody)
    need(custody['scheduler_verified'] is False) # Cross-host join is still required
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
            if operation=='full_backup':missed=True # Retired timer qualification remains provisional until shared join
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
        'units':original_units(reader,role,health,before,custody)}
    latest_receipts=verify_inventory_generation(health,before,SPECS[role][1],snapshot['observed_at'])
    if role=='db':snapshot.update(database_fields(reader))
    # Exact installed canonical evaluator scans original receipt inventory itself.
    # No persisted status/snapshot is copied or edited to construct this input.
    evaluated=health.evaluate_backup_health(snapshot,receipt_root=RECEIPTS)
    after=health.scan_receipts(root=RECEIPTS);after_sha=inventory_digest(health,after)
    need(after==before and after_sha==before_sha and evaluated['evaluated_at']==snapshot['observed_at'])
    return snapshot,evaluated,before_sha,after_sha,latest_receipts

def collect(role,binding,reader=None):
    global RUN_DEADLINE
    scheduler_binding(binding);need(role in SPECS and os.geteuid()==0 and socket.gethostname()==SPECS[role][0])
    issued=datetime.fromisoformat(binding['issued_at'].replace('Z','+00:00'));RUN_DEADLINE=time.monotonic()+30-(utc()-issued).total_seconds()
    if reader is None:reader=Reader()
    need(type(reader) is Reader);reader.role=role;observed=utc();boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip();reader.boot=boot
    need(boot==binding['boots'][role])
    clock=run(['/usr/bin/timedatectl','show','--property=NTPSynchronized','--property=Timezone']).decode()
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
    value={'binding':binding,'role':role,'observed_at':observed.isoformat(),'boot':boot,'core_sha256':SPECS[role][2],
        'collector_manifest_sha256':VERIFIED_SOURCE_MANIFEST,
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

    need(len(canonical(value))<=262144) # ASCII JSON including LF, before transport
    return value


WEB_SCHEDULER_UNITS=tuple('careertrellis-paired-'+stem+suffix for stem in ('maintenance','recovery') for suffix in ('.service','.timer'))
DB_TEMPLATES=('careertrellis-paired-db@.service','careertrellis-paired-db-recovery@.service')
SERVICE_UNIT_FIELDS=('LoadState','ActiveState','SubState','MainPID','ControlPID','Job','Result','FragmentPath','DropInPaths','NeedDaemonReload')
TIMER_UNIT_FIELDS=('LoadState','ActiveState','SubState','UnitFileState','Job','FragmentPath','DropInPaths','NeedDaemonReload')
UNIT_FIELDS=SERVICE_UNIT_FIELDS
FULL_SERVICE_PIN='63ee2e6c6430d80c50364fd1560c489ae3ea8d221f45e8ae92f11e0d2c29e9bd'
def scheduler_binding(value):
    need(type(value) is dict)
    if value.get('schema')=='careertrellis.preplan-scheduler-binding/v1':
        need(set(value)=={'schema','nonce','source_commit','boots','issued_at','phase','mode','release','build'} and value['phase']=='preflight' and value['mode']=='idle' and value['release']==BASE and value['build']=='20261017.96')
        need(type(value['nonce']) is str and re.fullmatch('[0-9a-f]{64}',value['nonce']) and type(value['source_commit']) is str and re.fullmatch('[0-9a-f]{40}',value['source_commit']))
        need(type(value['boots']) is dict and set(value['boots'])=={'web','db'} and all(type(v) is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',v) for v in value['boots'].values()))
        t=datetime.fromisoformat(value['issued_at'].replace('Z','+00:00'));need(t.utcoffset()==timedelta(0) and 0<=(utc()-t).total_seconds()<=30)
    else:request(value)
    return value

def db_instance_names():
    raw=run(['/usr/bin/systemctl','list-units','--all','--type=service','--no-legend','--no-pager','--plain','careertrellis-paired-db@*.service','careertrellis-paired-db-recovery@*.service'],65536)
    names=[]
    for line in raw.decode('utf-8').splitlines():
        need(line and len(line)<=4096);name=line.split()[0]
        need(re.fullmatch(r'careertrellis-paired-db(?:-recovery)?@[0-9a-f]{32}\.service',name) is not None);names.append(name)
    need(len(names)<=32 and len(set(names))==len(names));return sorted(names)

def db_registered_templates():
    # Bare templates have unit-file registration, not operative runtime state.
    raw=run(['/usr/bin/systemctl','list-unit-files','--no-legend','--no-pager','--full',*DB_TEMPLATES],16384)
    rows={}
    for line in raw.decode('ascii').splitlines():
        fields=line.split();need(len(fields)==3)
        name,state,preset=fields;need(name in DB_TEMPLATES and name not in rows and state=='static' and preset=='-')
        rows[name]={'state':state,'preset':preset}
    need(set(rows)==set(DB_TEMPLATES));return rows

def db_unit_search_paths():
    raw=run(['/usr/bin/systemctl','show','--property=UnitPath','--value'],16384)
    paths=raw.decode('ascii').split();need(0<len(paths)<=32 and len(set(paths))==len(paths))
    allowed=r'/(?:etc/systemd/system(?:\.(?:control|attached))?|run/systemd/(?:system(?:\.(?:control|attached))?|transient|generator(?:\.(?:early|late))?)|usr/(?:local/)?lib/systemd/system|lib/systemd/system)'
    need(all(re.fullmatch(allowed,name) for name in paths) and '/etc/systemd/system' in paths)
    return sorted(paths)

def template_dropin_snapshot(name,paths):
    # No following unknown links. Only the explicitly verified merged-/usr alias
    # /lib -> usr/lib (or /usr/lib) is accepted, with stable target identity.
    fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
    identity=lambda value:tuple(getattr(value,k) for k in fields)
    observed={}
    lib=Path('/lib');before=lib.lstat()
    if stat.S_ISLNK(before.st_mode):
        target=os.readlink(lib);need(before.st_uid==before.st_gid==0 and target in ('usr/lib','/usr/lib'))
        for parent in (Path('/'),Path('/usr')):
            ancestor=parent.lstat();need(stat.S_ISDIR(ancestor.st_mode) and ancestor.st_uid==ancestor.st_gid==0 and not ancestor.st_mode&0o022)
        canonical=Path('/usr/lib').lstat();need(stat.S_ISDIR(canonical.st_mode) and canonical.st_uid==canonical.st_gid==0 and not canonical.st_mode&0o022)
        opened=lib.stat();need((opened.st_dev,opened.st_ino)==(canonical.st_dev,canonical.st_ino))
        need(identity(lib.lstat())==identity(before) and os.readlink(lib)==target and identity(Path('/usr/lib').lstat())==identity(canonical))
        observed['/lib-alias']=(identity(before),target,identity(canonical))
    suffixes=['service.d','careertrellis-.service.d','careertrellis-paired-.service.d',name+'.d']
    if '-recovery@' in name:suffixes.append('careertrellis-paired-db-.service.d')
    for root in paths:
        base=Path(root)
        for parent in (base,*base.parents):
            if str(parent)=='/lib' and stat.S_ISLNK(before.st_mode):continue
            try:value=parent.lstat()
            except FileNotFoundError:continue
            need(stat.S_ISDIR(value.st_mode) and value.st_uid==value.st_gid==0 and not value.st_mode&0o022)
            observed[str(parent)]=identity(value)
        if root!='/etc/systemd/system':
            try:(base/name).lstat()
            except FileNotFoundError:pass
            else:need(False) # No alternate full template or mask can shadow it.
        for leaf in suffixes:
            path=base/leaf
            try:path.lstat()
            except FileNotFoundError:continue
            need(False) # Existing directory, file, link or mask is unqualified.
    return observed

def scheduler_template_record(reader,name,pins,registration,paths):
    path='/etc/systemd/system/'+name;digest=sha(reader.read(path,0o644));need(digest==pins['units'][path])
    snapshot=template_dropin_snapshot(name,paths)
    reader.checks.append(lambda:need(template_dropin_snapshot(name,paths)==snapshot))
    return {'name':name,'template':name,'sha256':digest,'registration':registration,'unit_paths':paths,'dropin_paths':[]}

def scheduler_unit_record(reader,name,template,pins):
    value=reader.unit(name);path='/etc/systemd/system/'+template
    need(value['FragmentPath']==path and value['DropInPaths']=='' and value['NeedDaemonReload']=='no' and value['LoadState']=='loaded')
    digest=sha(reader.read(path,0o644));need(digest==pins['units'][path])
    return {'name':name,'template':template,'sha256':digest,'state':{k:value[k] for k in (TIMER_UNIT_FIELDS if name.endswith('.timer') else SERVICE_UNIT_FIELDS)}}

def local_scheduler_coverage(reader,role,binding,custody):
    scheduler_binding(binding);need(role in ('web','db') and custody['mode']==binding['mode'])
    pins=public_document('installed493-pins.json');units={};instances=[];legacy=None;schedule=None
    if role=='web':
        web_standing_scheduler(reader)
        for name in WEB_SCHEDULER_UNITS:units[name]=scheduler_unit_record(reader,name,name,pins)
        schedule={}
        for name in WEB_SCHEDULER_UNITS:
            if not name.endswith('.timer'):continue
            value=reader.timer_cadence(name);prop='NextElapseUSecRealtime' if 'maintenance' in name else 'NextElapseUSecMonotonic'
            schedule[name]={k:value[k] for k in ('Persistent','RandomizedDelayUSec','AccuracyUSec')}
            schedule[name].update(property=prop,next_value=reader.timer_next(name,prop),monotonic_observed_us=time.monotonic_ns()//1000,**reader.timer_arrays(name))
    else:
        registered=db_registered_templates();paths=db_unit_search_paths()
        reader.checks.append(lambda:need(db_registered_templates()==registered and db_unit_search_paths()==paths))
        for name in DB_TEMPLATES:units[name]=scheduler_template_record(reader,name,pins,registered[name],paths)
        names=db_instance_names();reader.checks.append(lambda:need(db_instance_names()==names))
        for name in names:
            template='careertrellis-paired-db-recovery@.service' if name.startswith('careertrellis-paired-db-recovery@') else 'careertrellis-paired-db@.service'
            value=scheduler_unit_record(reader,name,template,pins)
            # A493 paired worker is not the source-bound app packet participant.
            need(value['state']['ActiveState']=='inactive' and value['state']['MainPID']==value['state']['ControlPID']=='0' and value['state']['Job']=='' and value['state']['Result'] in ('','success'));instances.append(value)
        legacy=retired_full_coverage(reader)
    value={'schema':'careertrellis.local-scheduler-coverage/v3','role':role,'binding':binding,'collector_manifest_sha256':VERIFIED_SOURCE_MANIFEST,'observed_at':utc().isoformat(),'policy_sha256':custody['policy_sha256'],'mode':custody['mode'],'units':units,'instances':instances,'legacy_full_timer':legacy,'web_schedule':schedule,'scheduler_verified':False}
    validate_local_coverage(value,role,custody);return value

def validate_local_coverage(value,role,custody):
    need(type(value) is dict and set(value)=={'schema','role','binding','collector_manifest_sha256','observed_at','policy_sha256','mode','units','instances','legacy_full_timer','web_schedule','scheduler_verified'})
    need(value['schema']=='careertrellis.local-scheduler-coverage/v3' and value['role']==role and value['policy_sha256']==custody['policy_sha256'] and value['mode']==custody['mode'] and value['scheduler_verified'] is False)
    need(type(value['units']) is dict and set(value['units'])==set(WEB_SCHEDULER_UNITS if role=='web' else DB_TEMPLATES))

def collect_preplan(role,binding,reader=None):
    need(type(binding) is dict and binding.get('schema')=='careertrellis.preplan-scheduler-binding/v1')
    return collect(role,binding,reader=reader)

def reevaluate_database(raw,binding,reader=None):
    """DB-only real-root reevaluation, no mutation/provider traversal or new nonce."""
    scheduler_binding(binding);need(os.geteuid()==0 and socket.gethostname()==SPECS['db'][0] and raw['binding']==binding and raw['boot']==binding['boots']['db'])
    if reader is None:reader=Reader()
    need(type(reader) is Reader);reader.role='db';reader.boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip();need(reader.boot==binding['boots']['db'])
    original_snapshot=canonical(raw['original_snapshot']);original_result=canonical(raw['canonical_evaluation'])
    sources=installed493(reader);health=core(reader,'db');need(authority(reader,'db')==raw['authority'])
    current=native_custody(reader,'db',binding,sources)
    for key in ('mode','policy_sha256','owner'):
        need(current.get(key)==raw['custody'].get(key))
    before=health.scan_receipts(root=RECEIPTS);before_sha=inventory_digest(health,before);need(before_sha==raw['receipt_inventory_sha256'])
    snapshot=decode(original_snapshot);need(snapshot['units']['full_backup']['missed'] is True)
    snapshot['units']['full_backup']['missed']=False
    result=health.evaluate_backup_health(snapshot,receipt_root=RECEIPTS)
    need(result['result']=='ok' and result['failure_codes']==[] and result['failure_count']==0)
    after=health.scan_receipts(root=RECEIPTS);after_sha=inventory_digest(health,after)
    need(after==before and after_sha==before_sha and original_snapshot==canonical(raw['original_snapshot']) and original_result==canonical(raw['canonical_evaluation']))
    need(authority(reader,'db')==raw['authority']);again=native_custody(reader,'db',binding,sources)
    for key in ('mode','policy_sha256','owner'):need(again.get(key)==current.get(key))
    reader.stable();need(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==reader.boot and time.monotonic()<RUN_DEADLINE)
    return {'derived_snapshot':snapshot,'derived_snapshot_sha256':sha(canonical(snapshot)),'derived_result':result,'derived_result_sha256':sha(canonical(result)),'core_sha256':SPECS['db'][2],'inventory_before_sha256':before_sha,'inventory_after_sha256':after_sha,'reevaluated_at':utc().isoformat()}

def effective_artifact_map(reader,policy,role):
    """Execute only exact493 pure constants/configure, never its module imports."""
    need(type(role) is str and role in ('web','db'))
    raw=reader.read(INSTALL/'runtime.py',0o644)
    need(sha(raw)=='0a62282a15f62b2103f88bdfe195f961cbbe4ae815fee02bac764398858f5604')
    tree=ast.parse(raw);required={'OPS','HELPER','CORE','PINS','STATE_ROOT','_BASE_PINS','_BASE_OPS'}
    selected=[];found=set();constructors={'for':0,'update':0};snapshot=False
    for node in tree.body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in required:
            if node.targets[0].id=='_BASE_PINS':snapshot=True
            need(node.targets[0].id not in found);found.add(node.targets[0].id);selected.append(node)
        elif not snapshot and isinstance(node,ast.For) and any(isinstance(n,ast.Name) and n.id=='PINS' for n in ast.walk(node)):
            need(len(node.body)==1 and isinstance(node.body[0],ast.Assign) and not node.orelse)
            constructors['for']+=1;selected.append(node)
        elif not snapshot and isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=='update' and any(isinstance(n,ast.Name) and n.id=='PINS' for n in ast.walk(node)):
            constructors['update']+=1;selected.append(node)
        elif not snapshot and isinstance(node,(ast.Assign,ast.AugAssign,ast.For,ast.While,ast.If,ast.Expr)) and any(isinstance(n,ast.Name) and n.id=='PINS' for n in ast.walk(node)):
            need(False)
        elif isinstance(node,ast.FunctionDef) and node.name=='configure':selected.append(node)
    need(found==required and constructors=={'for':1,'update':1} and sum(isinstance(n,ast.FunctionDef) for n in selected)==1)
    data=policy.data;need(type(data) is dict)
    for name in ('application_release','ops_release'):
        need(type(data[name]) is str and re.fullmatch('[0-9a-f]{40}',data[name]) is not None)
    overrides=data.get('artifact_hashes',{});need(type(overrides) is dict and set(overrides)<= {'web','db'})
    for values in overrides.values():
        need(type(values) is dict)
        for path,pin in values.items():
            need(type(path) is str and type(pin) is str and re.fullmatch('[0-9a-f]{64}',pin) is not None)
    scope={'__builtins__':{},'Path':PurePosixPath,'dict':dict,'require':lambda value,*unused:need(value)}
    exec(compile(ast.Module(body=selected,type_ignores=[]),'authenticated493-pure-configure','exec'),scope)
    # Unknown roles/paths are refused by the genuine source configure body.
    try:scope['configure'](policy,role)
    except (KeyError,TypeError):need(False)
    result=scope['PINS'];need(type(result) is dict and set(result)=={'web','db'})
    need(all(type(values) is dict for values in result.values()) and len(result['web'])==5 and len(result['db'])==10)
    return result

def verify_effective_artifacts(reader,policy,role):
    values=effective_artifact_map(reader,policy,role)[role]
    for path,expected in values.items():
        previous=reader.artifact_pins.get(path);need(previous is None or previous==expected);reader.artifact_pins[path]=expected
        need(sha(reader.read_artifact(path,expected))==expected)
    return values

def validate_monotonic_timers(value):
    """Closed JSON representation of systemd Timer.TimersMonotonic a(stt)."""
    need(type(value) is list and len(value)==2)
    expected={'OnBootUSec':30000000,'OnUnitActiveUSec':60000000};found=set()
    for row in value:
        need(type(row) is dict and set(row)=={'base','offset_usec','next_elapse_usec'})
        base=row['base'];need(type(base) is str and base in expected and base not in found);found.add(base)
        need(type(row['offset_usec']) is int and row['offset_usec']==expected[base])
        need(type(row['next_elapse_usec']) is int and 0<=row['next_elapse_usec']<2**64)
    need(found==set(expected));return value

def parse_monotonic_timers(raw):
    need(type(raw) is bytes and len(raw)<=1024)
    try:text=raw.decode('ascii').strip()
    except UnicodeDecodeError:need(False)
    matched=re.fullmatch(r'a\(stt\) 2 "([A-Za-z]+)" ([0-9]+) ([0-9]+) "([A-Za-z]+)" ([0-9]+) ([0-9]+)',text)
    need(matched is not None)
    rows=[{'base':matched[i],'offset_usec':int(matched[i+1]),'next_elapse_usec':int(matched[i+2])} for i in (1,4)]
    validate_monotonic_timers(rows);return rows

def timer_monotonic(name):
    need(type(name) is str and name=='careertrellis-paired-recovery.timer')
    encoded=''.join(c if c.isalnum() else '_'+format(ord(c),'02x') for c in name)
    raw=run(['/usr/bin/busctl','--system','get-property','org.freedesktop.systemd1',
        '/org/freedesktop/systemd1/unit/'+encoded,'org.freedesktop.systemd1.Timer','TimersMonotonic'],maximum=1024)
    return parse_monotonic_timers(raw)

def unit_properties(raw,fields):
    need(type(raw) is bytes and len(raw)<=16384)
    try:lines=raw.decode('utf-8').splitlines()
    except UnicodeDecodeError:need(False)
    value={}
    for line in lines:
        need('=' in line);key,item=line.split('=',1);need(key in fields and key not in value);value[key]=item
    need(set(value)==set(fields));return value

def full_service_pin(reader):
    raw=reader.read(INSTALL/'runtime.py',0o644);need(sha(raw)=='0a62282a15f62b2103f88bdfe195f961cbbe4ae815fee02bac764398858f5604')
    tree=ast.parse(raw);values=[ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='UNIT_PINS']
    need(len(values)==1 and values[0]['careertrellis-pgbackrest-full.service']==(FULL_SERVICE_PIN,None));return FULL_SERVICE_PIN

def retired_full_coverage(reader):
    timer=reader.unit('careertrellis-pgbackrest-full.timer')
    need(timer['ActiveState']=='inactive' and timer['UnitFileState']=='disabled' and timer['Job']=='')
    service=reader.unit('careertrellis-pgbackrest-full.service')
    need(service['ActiveState']=='inactive' and service['MainPID']==service['ControlPID']=='0' and service['Job']=='' and service['Result'] in ('','success'))
    path='/etc/systemd/system/careertrellis-pgbackrest-full.service';pin=full_service_pin(reader)
    need(service['FragmentPath']==path and service['DropInPaths']=='' and service['LoadState']=='loaded' and service['NeedDaemonReload']=='no')
    previous=reader.artifact_pins.get(path);need(previous is None or previous==pin);reader.artifact_pins[path]=pin
    need(sha(reader.read_artifact(path,pin))==pin)
    return {'timer':{'name':'careertrellis-pgbackrest-full.timer','state':{k:timer[k] for k in TIMER_UNIT_FIELDS}},
        'service':{'name':'careertrellis-pgbackrest-full.service','sha256':pin,'state':{k:service[k] for k in SERVICE_UNIT_FIELDS}}}

CALENDAR_SPEC='Sun *-*-* 03:00:00 America/New_York'

def parse_monotonic_array(raw):
    need(type(raw) is bytes and len(raw)<=1024)
    if raw.strip()==b'a(stt) 0':return []
    return parse_monotonic_timers(raw)

def parse_calendar_array(raw):
    need(type(raw) is bytes and len(raw)<=4096)
    if raw.strip()==b'a(sst) 0':return []
    try:text=raw.decode('ascii').strip()
    except UnicodeDecodeError:need(False)
    match=re.fullmatch(r'a\(sst\) 1 "([A-Za-z]+)" "([^"\\\x00-\x1f]+)" ([0-9]+)',text)
    need(match is not None)
    value=[{'base':match[1],'specification':match[2],'next_elapse_usec':int(match[3])}]
    validate_calendar_array(value);return value

def validate_calendar_array(value):
    need(type(value) is list and len(value)==1)
    row=value[0];need(type(row) is dict and set(row)=={'base','specification','next_elapse_usec'})
    need(type(row['base']) is str and row['base']=='OnCalendar' and type(row['specification']) is str and row['specification']==CALENDAR_SPEC)
    need(type(row['next_elapse_usec']) is int and 0<=row['next_elapse_usec']<2**64)
    return value

def validate_unit_timer_arrays(name,value):
    need(type(value) is dict and set(value)=={'calendar_timers','monotonic_timers'})
    if name=='careertrellis-paired-maintenance.timer':
        validate_calendar_array(value['calendar_timers']);need(type(value['monotonic_timers']) is list and value['monotonic_timers']==[])
    else:
        need(name=='careertrellis-paired-recovery.timer');validate_monotonic_timers(value['monotonic_timers'])
        need(type(value['calendar_timers']) is list and value['calendar_timers']==[])
    return value

def timer_array_record(name):
    need(type(name) is str and name in ('careertrellis-paired-maintenance.timer','careertrellis-paired-recovery.timer'))
    encoded=''.join(c if c.isalnum() else '_'+format(ord(c),'02x') for c in name)
    def query(property_name,maximum):
        return run(['/usr/bin/busctl','--system','get-property','org.freedesktop.systemd1',
            '/org/freedesktop/systemd1/unit/'+encoded,'org.freedesktop.systemd1.Timer',property_name],maximum=maximum)
    calendar_raw=query('TimersCalendar',4096);monotonic_raw=query('TimersMonotonic',1024)
    return (calendar_raw,monotonic_raw,parse_calendar_array(calendar_raw),parse_monotonic_array(monotonic_raw))
