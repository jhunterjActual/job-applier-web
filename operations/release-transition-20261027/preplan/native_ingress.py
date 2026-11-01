"""Fixed read-only ingress proof; effective Caddy route stays private in memory."""
import os,stat,re,json,urllib.request,urllib.error,urllib.parse
from pathlib import Path
from native_io import need,retained_run,identity,boot
from public_version import pointer_state
STAGING='staging-app.jhunterj.com';CANONICAL='careertrellis.jhunterj.com';DIAL='127.0.0.1:8001'
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*_):return None

def http(url,method,maximum):
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 request=urllib.request.Request(url,method=method,headers={'User-Agent':'CareerTrellis-readonly-preplan'})
 try:response=opener.open(request,timeout=3)
 except urllib.error.HTTPError as error:response=error
 with response:
  raw=response.read(maximum+1);need(len(raw)<=maximum);return response.status,response.headers.get('Location',''),raw

def unit():
 fields={'User','MainPID','ControlPID','ActiveState','SubState','LoadState','Job','NeedDaemonReload','FragmentPath','DropInPaths'}
 raw=retained_run(['/usr/bin/systemctl','show','caddy.service','--property='+','.join(sorted(fields))],16384);rows=raw.decode().splitlines();need(len(rows)==len(fields));value=dict(row.split('=',1) for row in rows);need(set(value)==fields)
 need(value['User']=='caddy' and value['MainPID'].isdigit() and int(value['MainPID'])>1 and value['ControlPID']=='0' and value['LoadState']=='loaded' and value['ActiveState']=='active' and value['SubState']=='running' and value['Job']=='' and value['NeedDaemonReload']=='no');return value

def kernel_raw(path,maximum):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  raw=os.read(fd,maximum+1);need(len(raw)<=maximum);return raw
 finally:os.close(fd)

def caddy_actor(pid):
 import pwd
 root=Path('/proc')/str(pid);raw=kernel_raw(root/'stat',16384).decode();parts=raw[raw.rfind(')')+2:].split();status=kernel_raw(root/'status',16384).decode();uids=next(row.split()[1:] for row in status.splitlines() if row.startswith('Uid:'));expected=str(pwd.getpwnam('caddy').pw_uid)
 need(uids==[expected]*4 and os.readlink(root/'exe')=='/usr/bin/caddy' and kernel_raw(root/'stat',16384).decode()==raw);return {'pid':pid,'birth':parts[19]}

def listener_owned(pid,port):
 need(type(pid)is int and pid>1 and type(port)is int and port in (2019,8001))
 rows=kernel_raw('/proc/net/tcp',1048576).decode('ascii').splitlines()[1:];inodes=[]
 for row in rows:
  cells=row.split()
  if len(cells)>=10 and cells[1]=='0100007F:'+format(port,'04X') and cells[3]=='0A':inodes.append(cells[9])
 need(len(inodes)==1 and inodes[0].isdigit());fds=list((Path('/proc')/str(pid)/'fd').iterdir());need(len(fds)<=512)
 need(any(p.name.isdigit() and os.readlink(p)=='socket:['+inodes[0]+']' for p in fds));return inodes[0]

def proxy_dials(value):
 result=set()
 if type(value)is dict:
  if value.get('handler')=='reverse_proxy':
   need(type(value.get('upstreams'))is list and value['upstreams'])
   for row in value['upstreams']:need(type(row)is dict and type(row.get('dial'))is str);result.add(row['dial'])
  for key in ('routes','handle'):
   if key in value:result.update(proxy_dials(value[key]))
 elif type(value)is list:
  for row in value:result.update(proxy_dials(row))
 return result

def canonical_route(servers):
 need(type(servers)is dict and 0<len(servers)<=16);found=False
 for server in servers.values():
  need(type(server)is dict)
  for route in server.get('routes',[]):
   matches=route.get('match',[])
   if any(type(m)is dict and set(m)=={'host'} and type(m['host'])is list and CANONICAL in m['host'] for m in matches):
    need(proxy_dials(route)=={DIAL});found=True
 need(found)

def collect(expected_web_pid):
 version_before=pointer_state();original_boot=boot();before=unit();actor=caddy_actor(int(before['MainPID']));inode=listener_owned(actor['pid'],2019);web_inode=listener_owned(expected_web_pid,8001)
 code,_,raw=http('http://127.0.0.1:2019/config/apps/http/servers/','GET',2097152);need(code==200);canonical_route(json.loads(raw))
 code,location,_=http('https://'+STAGING+'/','HEAD',0);target=urllib.parse.urlsplit(location);need(code==302 and target.scheme=='https' and target.hostname==CANONICAL and target.port in (None,443))
 code,_,_=http('https://'+CANONICAL+'/','HEAD',0);need(code==401)
 code,_,_=http('http://127.0.0.1:8001/api/version','GET',16384);need(code==401)
 need(pointer_state()==version_before)
 need(unit()==before and caddy_actor(actor['pid'])==actor and listener_owned(actor['pid'],2019)==inode and listener_owned(expected_web_pid,8001)==web_inode and boot()==original_boot)
 return {'schema':'careertrellis.preplan-readonly-ingress/v1','staging_hostname':STAGING,'canonical_hostname':CANONICAL,'staging_redirects_to_canonical':True,'effective_canonical_proxy_is_loopback':True,'current_build_96_verified':True,'canonical_unauth_status':401,'loopback_unauth_status':401,'protected_version':version_before,'caddy_unit_and_listener_stable':True,'authenticated_session_tested':False}
