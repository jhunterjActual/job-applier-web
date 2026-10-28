import portable_bootstrap as pb
"""Pure source-contract tests, not systemd/native observation."""
import ast,copy,json,stat,types,unittest
from pathlib import Path,PurePosixPath
from unittest.mock import patch
import collector as c
import pair_boundary as b
import role_fixture_reference as f
class Reader(f.Reader):
 def unit(self,name,remember=True):
  if name in c.DB_TEMPLATES:raise AssertionError('BARE_TEMPLATE_SHOW_FORBIDDEN')
  return super().unit(name,remember)
class Tests(unittest.TestCase):
 def setUp(self):
  self.fixture=f.Tests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
  self.registration={name:{'state':'static','preset':'-'} for name in c.DB_TEMPLATES};self.paths=['/etc/systemd/system','/usr/lib/systemd/system']
  self.stack=__import__('contextlib').ExitStack();self.addCleanup(self.stack.close)
  self.stack.enter_context(patch.object(c,'db_registered_templates',lambda:copy.deepcopy(self.registration)))
  self.stack.enter_context(patch.object(c,'db_unit_search_paths',lambda:list(self.paths)))
  self.stack.enter_context(patch.object(c,'template_dropin_snapshot',lambda *args:{'synthetic':1}))
 def coverage(self,role='db',phase='preflight',planless=False,reader=None):
  binding=self.fixture.binding(phase,planless);r=reader or Reader(role);return binding,r,c.local_scheduler_coverage(r,role,binding,{'mode':binding['mode'],'policy_sha256':'8'*64})
 def test_all_four_phases_and_planless_pair_no_bare_show(self):
  for phase,planless in [('preflight',False),('stopped',False),('verifying',False),('final',False),('preflight',True)]:
   binding,r,db=self.coverage(phase=phase,planless=planless);_,w,web=self.coverage('web',phase,planless)
   self.assertTrue(all(name not in r.calls for name in c.DB_TEMPLATES));self.assertEqual(b.check_scheduler_pair(web,db,binding,'a'*64,now=f.NOW)['result'],'scheduler_pair_bound');self.assertFalse(db['scheduler_verified'])
 def test_registration_before_after_drift_and_template_hash_refuse(self):
  binding,r,value=self.coverage();self.registration[c.DB_TEMPLATES[0]]['state']='disabled'
  with self.assertRaises(c.Refused):
   for callback in r.checks:callback()
  r=Reader('db');r.read=lambda *args:b'wrong'
  with self.assertRaises(c.Refused):self.coverage(reader=r)
 def test_pair_registered_state_path_shape_and_dropins_refuse(self):
  for kind in ('state','preset','hash','dropin','paths','extra'):
   binding,r,db=self.coverage();_,_,web=self.coverage('web');row=db['units'][c.DB_TEMPLATES[0]]
   if kind=='state':row['registration']['state']='enabled'
   elif kind=='preset':row['registration']['preset']='enabled'
   elif kind=='hash':row['sha256']='9'*64
   elif kind=='dropin':row['dropin_paths']=['unqualified']
   elif kind=='paths':row['unit_paths']=['/private']
   else:row['state']={'LoadState':'loaded'}
   with self.subTest(kind=kind),self.assertRaises(c.Refused):b.check_scheduler_pair(web,db,binding,'a'*64,now=f.NOW)
 def test_only_discovered_instances_get_real_properties(self):
  name='careertrellis-paired-db@'+'a'*32+'.service';r=Reader('db')
  with patch.object(c,'db_instance_names',lambda:[name]):
   binding,r,value=self.coverage(reader=r);self.assertIn(name,r.calls);self.assertEqual(len(value['instances']),1)
   for key,val in [('ActiveState','active'),('DropInPaths','bad'),('NeedDaemonReload','yes'),('FragmentPath','/bad')]:
    r=Reader('db');r.unit(name)[key]=val
    with self.subTest(key=key),self.assertRaises(c.Refused):self.coverage(reader=r)
 def test_web_scheduler_and_reader_source_bodies_unchanged(self):
  old=(Path(__file__).parent/'fixtures/template-baseline-collector.py.txt').read_bytes().decode();new=(pb.ROOT/'one_shot_readiness/collector.py').read_bytes().decode()
  for name in ('web_standing_scheduler','scheduler_unit_record','unit','read'):
   def part(text):return ast.get_source_segment(text,next(n for n in ast.walk(ast.parse(text)) if isinstance(n,ast.FunctionDef) and n.name==name))
   self.assertEqual(part(old),part(new))
 def test_binding_role_nonce_policy_source_stale_and_unitpath_mix_refuse(self):
  for kind in ('role','nonce','boot','policy','source','stale','paths'):
   binding,r,db=self.coverage();_,_,web=self.coverage('web')
   if kind=='role':db['role']='web'
   elif kind=='policy':db['policy_sha256']='9'*64
   elif kind=='source':db['collector_manifest_sha256']='9'*64
   elif kind=='stale':db['observed_at']=(f.NOW-f.timedelta(seconds=31)).isoformat()
   elif kind=='paths':db['units'][c.DB_TEMPLATES[0]]['unit_paths']=['/etc/systemd/system']
   else:
    db['binding']=copy.deepcopy(binding)
    if kind=='nonce':db['binding']['nonce']='9'*64
    else:db['binding']['boots']['db']='33333333-3333-3333-3333-333333333333'
   with self.subTest(kind=kind),self.assertRaises(c.Refused):b.check_scheduler_pair(web,db,binding,'a'*64,now=f.NOW)
 def test_unitpath_inventory_and_dropin_before_after_drift_refuse(self):
  for kind in ('paths','dropin'):
   binding,r,value=self.coverage()
   if kind=='paths':self.paths.append('/run/systemd/transient')
   else:self.stack.enter_context(patch.object(c,'template_dropin_snapshot',lambda *args:{'synthetic':2}))
   with self.subTest(kind=kind),self.assertRaises(c.Refused):
    for callback in r.checks:callback()
 def test_public493_source_has_no_install_section(self):
  import hashlib
  root=Path(__file__).parent;provenance=json.loads((root/'PUBLIC493_TEMPLATE_PROVENANCE.json').read_bytes())
  for name,row in provenance.items():
   raw=(root/'public493'/name).read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),row['sha256']);self.assertNotIn(b'[Install]',raw);self.assertEqual(row['commit'],'493bf14a3bae2cb2b36f3a57191602b72ea6e3d3')
class PureReaders(unittest.TestCase):
 def test_registered_unit_file_inventory_actual_parser(self):
  raw=''.join(name+' static -\n' for name in c.DB_TEMPLATES).encode();calls=[]
  with patch.object(c,'run',lambda args,maximum:(calls.append(args) or raw)):
   result=c.db_registered_templates();self.assertEqual(set(result),set(c.DB_TEMPLATES));self.assertIn('list-unit-files',calls[0]);self.assertNotIn('show',calls[0])
  for bad in (b'',raw+raw,raw.replace(b'static',b'enabled'),raw.replace(b' -',b' enabled'),b'unknown.service static -\n'):
   with patch.object(c,'run',lambda *args:bad),self.assertRaises(c.Refused):c.db_registered_templates()
 def test_unit_path_manager_parser(self):
  with patch.object(c,'run',lambda *args:b'/etc/systemd/system /run/systemd/transient /usr/lib/systemd/system\n'):
   self.assertEqual(len(c.db_unit_search_paths()),3)
  for raw in (b'/private',b'/etc/systemd/system /etc/systemd/system',b'/etc/systemd/system /usr/lib/systemd/../../private'):
   with patch.object(c,'run',lambda *args:raw),self.assertRaises(c.Refused):c.db_unit_search_paths()
 def snapshot(self,*,existing=(),alias='usr/lib',bad_owner=False,drift=False):
  entries=set(existing);calls={}
  def info(path,link=False):
   ident=7 if path in ('/lib','/usr/lib') and not link else abs(hash(path))%100000+10
   mode=stat.S_IFLNK|0o777 if link else stat.S_IFDIR|0o755
   return types.SimpleNamespace(st_dev=1,st_ino=ident,st_mode=mode,st_uid=1 if bad_owner else 0,st_gid=0,st_nlink=1,st_size=0,st_mtime_ns=0,st_ctime_ns=0)
  class FakePath(PurePosixPath):
   def lstat(self):
    path=str(self);calls[path]=calls.get(path,0)+1
    if path.endswith('.d') or path.endswith('.service'):
     if path not in entries:raise FileNotFoundError()
    out=info(path,link=path=='/lib')
    if drift and path=='/lib' and calls[path]>1:out.st_ino+=1
    return out
   def stat(self):return info('/usr/lib')
  with patch.object(c,'Path',FakePath),patch.object(c.os,'readlink',lambda _:alias):return c.template_dropin_snapshot(c.DB_TEMPLATES[0],['/etc/systemd/system','/lib/systemd/system','/run/systemd/system'])
 def test_exact_merged_usr_alias_and_unknown_alias_refusal(self):
  self.assertIn('/lib-alias',self.snapshot());self.assertIn('/lib-alias',self.snapshot(alias='/usr/lib'))
  for options in ({'alias':'/private'},{'bad_owner':True},{'drift':True}):
   with self.assertRaises(c.Refused):self.snapshot(**options)
 def test_prefix_type_template_dropins_and_full_shadow_refuse(self):
  for path in ('/etc/systemd/system/service.d','/run/systemd/system/careertrellis-.service.d','/lib/systemd/system/careertrellis-paired-.service.d','/etc/systemd/system/'+c.DB_TEMPLATES[0]+'.d','/run/systemd/system/'+c.DB_TEMPLATES[0]):
   with self.subTest(path=path),self.assertRaises(c.Refused):self.snapshot(existing=[path])
if __name__=='__main__':unittest.main()
