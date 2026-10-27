"""Concrete fixed synthetic Graph experiment; NOT staged or authorized to run.

No retries, refresh, redirects, proxies, backup namespace or cleanup. Native
factory requires a separately reviewed expiring source packet before any HTTP.
Only fixed classifications may leave this module; journals are root-private.

File creation uses PUT content with conflictBehavior in the URL, not POST file{}:
https://learn.microsoft.com/en-us/graph/api/driveitem-put-content?view=graph-rest-1.0
https://learn.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0#instance-attributes
"""
import configparser
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ORIGIN='https://graph.microsoft.com/v1.0'
CONFIG=Path('/etc/careertrellis/rclone.conf')
PACKETS=Path('/var/backups/careertrellis/onedrive-policy-activation-20261026/permanent-expiry-capability')
LIMIT=65536
NAME='conditional-delete-probe.txt'
BINARY=Path('/usr/local/bin/rclone')
BINARY_SHA256='f3f9aff817f9766029e50adf9a7963c169e475b8f10c7927823568a0d9443db7'


class Refused(ValueError):pass
class Unknown(Refused):pass


def need(value):
    if not value:raise Refused('synthetic_probe_refused')


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()+b'\n'
def quote(value):
    need(type(value) is str and 0<len(value)<=512 and all(32<=ord(c)<127 for c in value))
    return urllib.parse.quote(value,safe='')
def etag(value):
    need(type(value) is str and 0<len(value)<=1024 and value.strip()!='*' and all(32<=ord(c)<127 for c in value))
    return value
def identity(s):return (s.st_dev,s.st_ino,s.st_uid,s.st_gid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def parents(path):
    for parent in reversed(path.parents):
        s=parent.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022)


def protected(path,mode=0o600,limit=1024*1024):
    parents(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_uid==before.st_gid==0
            and stat.S_IMODE(before.st_mode)==mode and before.st_nlink==1 and before.st_size<=limit)
        chunks=[];remaining=before.st_size
        while remaining:
            part=os.read(fd,min(remaining,1024*1024));need(part);chunks.append(part);remaining-=len(part)
        raw=b''.join(chunks);need(len(raw)==before.st_size and identity(before)==identity(os.fstat(fd))==identity(path.lstat()))
        return raw,identity(before)
    finally:os.close(fd)


class Journal:
    def __init__(self,tx):
        packet=packet_paths(tx)['root'];self.root=packet/'journals';parents(self.root)
        s=packet.lstat();need(stat.S_IMODE(s.st_mode)==0o700 and s.st_uid==s.st_gid==0)
        self.root.mkdir(mode=0o700) # exclusive; no replay after interruption
        self.sync(packet)
    @staticmethod
    def sync(path):
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
    def once(self,name,value):
        need(re.fullmatch('[a-z-]+',name));path=self.root/(name+'.json')
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            os.fchmod(fd,0o600);raw=memoryview(canonical(value))
            while raw:
                size=os.write(fd,raw);need(size>0);raw=raw[size:]
            os.fsync(fd)
        finally:os.close(fd)
        self.sync(self.root)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class HTTP:
    def __init__(self,token):
        self.token=token
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    def call(self,method,path,body=None,headers=None):
        need(method in ('GET','POST','PUT') and path.startswith('/drives/') and '://' not in path)
        request=urllib.request.Request(ORIGIN+path,data=body,method=method,
            headers={'Authorization':'Bearer '+self.token,**(headers or {})})
        try:
            try:response=self.opener.open(request,timeout=15)
            except urllib.error.HTTPError as error:response=error
            with response:
                code=response.code;need(not 300<=code<400)
                raw=bytearray();deadline=time.monotonic()+30
                while True:
                    need(time.monotonic()<deadline)
                    part=response.read1(min(4096,LIMIT+1-len(raw)))
                    if not part:break
                    raw.extend(part);need(len(raw)<=LIMIT)
                return code,bytes(raw)
        except Exception:raise Unknown('synthetic_transport_unknown') from None


def credentials(raw,now,valid_until=None):
    try:
        parser=configparser.ConfigParser(interpolation=None,strict=True);parser.read_string(raw.decode())
        need(not parser.defaults() and 'onedrive' in parser)
        remote=dict(parser['onedrive'])
        need(remote.get('type')=='onedrive' and remote.get('drive_type') in ('personal','business')
            and remote.get('region','global') in ('','global') and remote.get('root_folder_id','')==''
            and remote.get('access_scopes','')=='' and remote.get('encoding','')=='')
        # Reject endpoint overrides and additional root mappings instead of
        # assuming they mean the ordinary authenticated global Graph drive.
        allowed={'type','drive_id','drive_type','token','client_id','client_secret','region','root_folder_id'}
        need(set(remote)<=allowed);drive=remote['drive_id'];quote(drive)
        token=json.loads(remote['token']);expiry=dt.datetime.fromisoformat(token['expiry'].replace('Z','+00:00'))
        need(token.get('token_type','').lower()=='bearer' and expiry.utcoffset()==dt.timedelta(0)
            and (expiry-now).total_seconds()>=120)
        if valid_until is not None:
            need(valid_until.utcoffset()==dt.timedelta(0) and now<valid_until
                and expiry>=valid_until+dt.timedelta(seconds=60))
        bearer=token['access_token'];need(type(bearer) is str and 0<len(bearer)<=16384 and all(33<=ord(c)<=126 for c in bearer))
        return drive,bearer
    except Exception:raise Refused('existing_token_or_mapping_unavailable_no_refresh') from None


class Probe:
    """Injectable engine; native authority belongs solely to native()."""
    def __init__(self,tx,drive,http,journal,unchanged,readback=None):
        need(type(tx) is str and re.fullmatch('[0-9a-f]{32}',tx))
        self.tx=tx;self.drive=drive;self.http=http;self.journal=journal;self.unchanged=unchanged
        self.readback=readback
        self.read_count=0
        self.base='/drives/'+quote(drive);self.folder='careertrellis-permanent-expiry-probe-'+tx
    def read(self,path):
        # Public phase evidence contains neither paths nor response data. A
        # durable intent distinguishes preflight rejection from dispatched GET.
        self.read_count+=1;need(self.read_count<=16)
        phase='read-'+chr(ord('a')+self.read_count-1)
        self.journal.once(phase+'-intent',{'phase':phase,'operation':'read-only'})
        try:self.unchanged()
        except Exception:
            self.journal.once(phase+'-status',{'classification':'preflight_refused','http_dispatched':False})
            raise Refused('synthetic_read_preflight_refused') from None
        try:code,raw=self.http.call('GET',path)
        except Exception as error:
            category='transport_unknown' if isinstance(error,Unknown) else 'read_refused' if isinstance(error,Refused) else 'local_io' if isinstance(error,OSError) else 'unexpected_exception'
            self.journal.once(phase+'-status',{'classification':category,'http_dispatched':True})
            raise Refused('synthetic_read_unavailable') from None
        if not (type(code) is int and 100<=code<=599):
            self.journal.once(phase+'-status',{'classification':'invalid_response','http_dispatched':True})
            raise Refused('synthetic_read_unavailable')
        self.journal.once(phase+'-status',{'classification':'response_received','http_dispatched':True,'status':code})
        return code,raw
    def dispatch(self,name,method,path,body=None,headers=None):
        self.unchanged();self.journal.once(name+'-intent',{'phase':name,'sole_dispatch':True})
        try:code,raw=self.http.call(method,path,body,headers)
        except Exception:
            self.journal.once(name+'-unknown',{'classification':'unknown_outcome_no_retry'})
            # Bounded observation only; never replay a mutation. Retained private
            # creation acknowledgement identifies the exact task-owned parent.
            self.readback_unknown(name)
            raise Unknown('unknown_outcome_no_retry') from None
        self.journal.once(name+'-ack',{'status':code})
        return code,raw
    def readback_unknown(self,name):
        try:
            path=self.base+'/root:/'+quote(self.folder)
            if name=='create-file' and hasattr(self,'parent'):
                path=self.base+'/items/'+quote(self.parent)+':/'+quote(NAME)
            if hasattr(self,'item'):path=self.base+'/items/'+quote(self.item)
            code,_=self.read(path);result='present' if code==200 else 'absent' if code==404 else 'unknown'
        except Exception:result='unknown'
        self.journal.once(name+'-readback',{'classification':result,'retry_permitted':False})
    def document(self,raw):
        value=json.loads(raw);need(type(value) is dict);return value
    def check(self,row,name,parent,folder=False):
        need(row['name']==name and row['parentReference']['driveId']==self.drive
            and row['parentReference']['id']==parent and 'remoteItem' not in row and 'deleted' not in row
            and ('folder' in row and 'file' not in row if folder else 'file' in row and 'folder' not in row))
        quote(row['id']);return row['id']
    def spine(self,row,content):
        need(self.check(row,NAME,self.parent)==self.item and type(row['size']) is int
            and row['size']==len(content) and type(row['file']) is dict)
        return {'id':row['id'],'name':row['name'],'drive_id':row['parentReference']['driveId'],
            'parent_id':row['parentReference']['id'],'size':row['size'],'etag':etag(row['eTag']),'kind':'file'}
    def verify(self,content):
        code,raw=self.read(self.base+'/items/'+quote(self.item));need(code==200)
        before=self.spine(self.document(raw),content);tag=before['etag']
        if self.readback is None:
            # Offline-only/default refusal path; native supplies pinned rclone
            # for this exact fixture, never follows a Graph redirect itself.
            code,raw=self.read(self.base+'/items/'+quote(self.item)+'/content');need(code==200 and raw==content)
        else:need(self.readback()==content)
        code,raw=self.read(self.base+'/items/'+quote(self.item));need(code==200)
        need(self.spine(self.document(raw),content)==before);return tag
    def exact_absence(self):
        code,_=self.read(self.base+'/items/'+quote(self.item));need(code==404)
        code,raw=self.read(self.base+'/items/'+quote(self.parent));need(code==200)
        need(self.check(self.document(raw),self.folder,self.rootid,True)==self.parent)
        code,_=self.read(self.base+'/items/'+quote(self.parent)+':/'+quote(NAME));need(code==404)
        return True
    def run(self):
        self.journal.once('attempt',{'schema':'synthetic-permanent-expiry/v1','transaction':self.tx})
        code,raw=self.read(self.base+'/root');need(code==200)
        root=self.document(raw);need('root' in root and 'folder' in root);rootid=root['id'];quote(rootid)
        self.rootid=rootid
        code,_=self.read(self.base+'/root:/'+quote(self.folder));need(code==404)
        body=canonical({'name':self.folder,'folder':{},'@microsoft.graph.conflictBehavior':'fail'})
        code,raw=self.dispatch('create-folder','POST',self.base+'/items/'+quote(rootid)+'/children',body,{'Content-Type':'application/json'});need(code==201)
        self.parent=self.check(self.document(raw),self.folder,rootid,True)
        self.journal.once('owned-folder',{'drive_id':self.drive,'parent_id':self.parent,'root_id':rootid})
        content=('DISPOSABLE PUBLIC FIXTURE '+self.tx+' VERSION 1\n').encode();need(len(content)<=1024)
        code,raw=self.dispatch('create-file','PUT',self.base+'/items/'+quote(self.parent)+':/'+quote(NAME)+
            ':/content?@microsoft.graph.conflictBehavior=fail',content,
            {'Content-Type':'text/plain','If-None-Match':'*'});need(code==201)
        row=self.document(raw);self.item=self.check(row,NAME,self.parent);tag=etag(row['eTag'])
        self.journal.once('owned-file',{'item_id':self.item,'parent_id':self.parent})
        old=self.verify(content);need(old==tag)
        content=('DISPOSABLE PUBLIC FIXTURE '+self.tx+' VERSION 2\n').encode();need(len(content)<=1024)
        code,_=self.dispatch('write-two','PUT',self.base+'/items/'+quote(self.item)+'/content',content,
            {'If-Match':old,'Content-Type':'text/plain'});need(code==200)
        tag=self.verify(content)
        need(old!=tag)
        endpoint=self.base+'/items/'+quote(self.item)+'/permanentDelete'
        code,_=self.dispatch('wrong-etag','POST',endpoint,headers={'If-Match':old})
        if code!=412:
            if code==204:
                self.exact_absence()
                self.journal.once('conditional-ignored',{'classification':'stale_etag_ignored_exact_absence_observed'})
                raise Refused('conditional_permanent_delete_ignored_observed')
            raise Refused('conditional_capability_unavailable')
        need(self.verify(content)==tag)
        code,_=self.dispatch('right-etag','POST',endpoint,headers={'If-Match':tag});need(code==204)
        self.exact_absence()
        self.unchanged()
        result={'classification':'synthetic_conditional_behavior_observed','backup_expiry_authorized':False,
            'provider_guarantee_established':False,'folder_retained':True}
        self.journal.once('complete',result);return result


class Readback:
    """Existing pinned rclone reads only this attempt's fixed public fixture."""
    def __init__(self,tx,config,drive,journal,unchanged):
        need(type(tx) is str and re.fullmatch('[0-9a-f]{32}',tx))
        self.tx,self.config,self.drive,self.journal,self.unchanged=tx,config,drive,journal,unchanged
        self.count=0
        raw,self.binary_id=protected(BINARY,0o755,256*1024**2)
        need(hashlib.sha256(raw).hexdigest()==BINARY_SHA256)
    def isolated_config(self):
        drive,_=credentials(self.config,dt.datetime.now(dt.timezone.utc));need(drive==self.drive)
        parser=configparser.ConfigParser(interpolation=None,strict=True);parser.read_string(self.config.decode())
        isolated=configparser.ConfigParser(interpolation=None);isolated['onedrive']=dict(parser['onedrive'])
        stream=io.StringIO();isolated.write(stream);return stream.getvalue().encode()
    def args(self,descriptor):
        need(type(descriptor) is int and descriptor>=0)
        return [str(BINARY),'cat','onedrive:careertrellis-permanent-expiry-probe-'+self.tx+'/'+NAME,
            '--config','/proc/self/fd/'+str(descriptor),'--retries','1','--low-level-retries','1',
            '--contimeout','10s','--timeout','15s','--log-level','ERROR','--stats','0']
    def __call__(self):
        import fcntl,resource
        self.unchanged();now=dt.datetime.now(dt.timezone.utc)
        drive,_=credentials(self.config,now);need(drive==self.drive)
        need(identity(BINARY.lstat())==self.binary_id)
        data=self.isolated_config()
        self.count+=1;need(self.count<=3);phase='readback-'+('one','two','three')[self.count-1]
        self.journal.once(phase+'-intent',{'phase':phase,'source':'fixed_synthetic_fixture','sole_dispatch':True})
        fd=os.memfd_create('onedrive-synthetic-readonly',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
        output=None;child=None;terminal=False
        path=self.journal.root/(phase+'.bin')
        try:
            view=memoryview(data)
            while view:
                size=os.write(fd,view);need(size>0);view=view[size:]
            os.lseek(fd,0,os.SEEK_SET)
            fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_SEAL|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_GROW|fcntl.F_SEAL_WRITE)
            output=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            os.fchmod(output,0o600);owned=identity(os.fstat(output));self.journal.sync(self.journal.root)
            def limits():resource.setrlimit(resource.RLIMIT_FSIZE,(65536,65536))
            child=subprocess.Popen(self.args(fd),stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.DEVNULL,
                env={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'},pass_fds=(fd,),preexec_fn=limits)
            ticks=int(Path('/proc/%d/stat'%child.pid).read_text().rsplit(') ',1)[1].split()[19])
            self.journal.once(phase+'-child',{'pid':child.pid,'start_ticks':ticks,
                'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()})
            try:code=child.wait(timeout=20);terminal=True
            except subprocess.TimeoutExpired:
                child.kill()
                try:child.wait(timeout=5);terminal=True
                except subprocess.TimeoutExpired:pass
                raise Unknown('synthetic_readback_timeout_no_retry') from None
            self.journal.once(phase+'-terminal',{'pid':child.pid,'exit_code':code,'terminal':True})
            need(code==0);os.fsync(output);meta=os.fstat(output)
            need(meta.st_size<=1024 and (meta.st_dev,meta.st_ino)==owned[:2]
                and identity(path.lstat())==identity(meta))
            os.lseek(output,0,os.SEEK_SET);result=os.read(output,1025);need(len(result)==meta.st_size)
            self.unchanged();need(identity(BINARY.lstat())==self.binary_id)
            # Exact owned output only, after proven terminal success. Failures
            # retain private files and intent; never retry or clean unknown data.
            os.unlink(path);self.journal.sync(self.journal.root)
            return result
        except Exception:
            if child is not None and not terminal:
                # A failure to capture child identity is still an owned child;
                # bound its lifetime and retain output/journal for reconciliation.
                if child.poll() is None:
                    child.kill()
                    try:child.wait(timeout=5);terminal=True
                    except subprocess.TimeoutExpired:pass
                else:terminal=True
            self.journal.once(phase+'-failed',{'classification':'readback_failed_no_retry','child_terminal':terminal})
            raise Refused('synthetic_readback_failed_no_retry') from None
        finally:
            if output is not None:os.close(output)
            os.close(fd)


def packet_paths(transaction):
    need(type(transaction) is str and re.fullmatch('[0-9a-f]{32}',transaction))
    root=PACKETS/transaction
    return {'root':root,'source':root/'native_probe.py','review':root/'review.json'}


def native(transaction):
    """Separate reviewed source packet/native window required; never auto-run."""
    paths=packet_paths(transaction);sourcepath,reviewpath=paths['source'],paths['review']
    need(os.name=='posix' and os.geteuid()==os.getegid()==0
        and socket.gethostname()=='job-applier-db-staging-01' and Path(__file__).absolute()==sourcepath)
    meta=paths['root'].lstat();need(stat.S_ISDIR(meta.st_mode) and stat.S_IMODE(meta.st_mode)==0o700 and meta.st_uid==meta.st_gid==0)
    source,sourceid=protected(sourcepath,0o644);reviewraw,_=protected(reviewpath);review=json.loads(reviewraw)
    need(set(review)=={'schema','transaction','source_sha256','valid_until','native_window_approved'}
        and review['schema']=='synthetic-expiry-reviewed-source/v1' and review['transaction']==transaction
        and review['source_sha256']==hashlib.sha256(source).hexdigest() and review['native_window_approved'] is True)
    now=dt.datetime.now(dt.timezone.utc);until=dt.datetime.fromisoformat(review['valid_until'].replace('Z','+00:00'))
    need(until.utcoffset()==dt.timedelta(0) and 0<(until-now).total_seconds()<=900)
    raw,configid=protected(CONFIG);drive,token=credentials(raw,now,until)
    def unchanged():
        credentials(raw,dt.datetime.now(dt.timezone.utc),until)
        need(protected(CONFIG)==(raw,configid) and protected(sourcepath,0o644)==(source,sourceid)
            and protected(reviewpath)[0]==reviewraw and dt.datetime.now(dt.timezone.utc)<until)
    journal=Journal(transaction)
    journal.once('owner',{'pid':os.getpid(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'start_ticks':int(Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()[19])})
    try:
        readback=Readback(transaction,raw,drive,journal,unchanged)
        return Probe(transaction,drive,HTTP(token),journal,unchanged,readback).run()
    except Exception:
        journal.once('failed',{'classification':'synthetic_probe_failed_no_retry','resources_retained':True})
        raise Refused('synthetic_probe_failed_no_retry') from None
    finally:
        try:unchanged()
        except Exception:raise Refused('synthetic_source_or_configuration_changed') from None


def main(argv):
    """Fixed public classification only; intended for separately reviewed packet."""
    try:
        need(type(argv) is list and len(argv)==1)
        result=native(argv[0]);return result['classification']
    except Exception:return 'synthetic_probe_refused_or_requires_reconciliation'


if __name__=='__main__':
    import sys
    print(main(sys.argv[1:]))
