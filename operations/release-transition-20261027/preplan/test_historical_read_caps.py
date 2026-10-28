"""Real temporary bytes, synthetic POSIX metadata; no host/private receipt IO."""
import ast,copy,hashlib,importlib.util,json,os,shutil,stat,sys,tempfile,types,unittest
from pathlib import Path,PurePosixPath
from unittest.mock import patch
P=Path(__file__).parent
PUBLIC=P
import native_io as c
import preplan_history as h
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
LARGE=sorted(c.HISTORICAL_LARGE_PATHS);SMALL=LARGE[0].rsplit('/',1)[0]+'/accepted.json'
class RealFiles:
 def __init__(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);assert shutil.disk_usage(self.root).free>=5*1024**3+96*1024**2
  self.files={};self.overrides={};self.fdpaths={};self.ctime={};self.parent_bad=False;self.fd_bad=False
  self.open=os.open;self.close=os.close;self.fstat=os.fstat
 def put(self,name,size):
  path=self.root/str(len(self.files));path.write_bytes(b'x'*size);self.files[name]=path;self.ctime[name]=path.stat().st_ctime_ns;return path
 def put_raw(self,name,raw):
  path=self.root/str(len(self.files));path.write_bytes(raw);self.files[name]=path;self.ctime[name]=path.stat().st_ctime_ns
 def metadata(self,s,name):
  value=types.SimpleNamespace(**{k:getattr(s,k) for k in c.FIELDS});value.st_uid=0;value.st_mode=stat.S_IFREG|0o600
  # Windows stat/fstat expose different ctime conventions. This is explicitly
  # synthetic POSIX metadata, not a native identity or creation-time assertion.
  value.st_ctime_ns=self.ctime[name]
  for k,v in self.overrides.get(name,{}).items():setattr(value,k,v)
  return value
 def context(self):
  fs=self
  class Mapped:
   def __init__(self,path):self.name=str(path)
   def __str__(self):return self.name
   def __fspath__(self):return str(fs.files[self.name])
   def is_absolute(self):return PurePosixPath(self.name).is_absolute()
   def lstat(self):return fs.metadata(fs.files[self.name].stat(),self.name)
   @property
   def parents(self):return [types.SimpleNamespace(lstat=lambda:types.SimpleNamespace(st_mode=stat.S_IFDIR|(0o777 if fs.parent_bad else 0o755),st_uid=0))]
  def opened(path,flags):
   fd=fs.open(os.fspath(path),flags);fs.fdpaths[fd]=str(path);return fd
  def fstat(fd):
   value=fs.metadata(fs.fstat(fd),fs.fdpaths[fd])
   if fs.fd_bad:value.st_ino+=1
   return value
  def closed(fd):fs.close(fd);fs.fdpaths.pop(fd,None)
  stack=__import__('contextlib').ExitStack();stack.enter_context(patch.object(c,'Path',Mapped));stack.enter_context(patch.object(c.os,'open',opened));stack.enter_context(patch.object(c.os,'fstat',fstat));stack.enter_context(patch.object(c.os,'close',closed))
  # Windows fixture-only flag constants; actual Linux source/flags untouched.
  stack.enter_context(patch.object(c.os,'O_NOFOLLOW',0,create=True));stack.enter_context(patch.object(c.os,'O_NONBLOCK',0,create=True));return stack
 def cleanup(self):self.tmp.cleanup()
def git_blob(commit,path):
 assert commit==h.OPS and path.startswith(h.PREFIX);return (PUBLIC/'public-runtime35'/path[len(h.PREFIX):]).read_bytes()
class Tests(unittest.TestCase):
 def fs(self):
  fs=RealFiles();self.addCleanup(fs.cleanup);return fs
 def test_actual_authenticated_catalogue_and_original_double_snapshots(self):
  packet=(PUBLIC/'historical_packet_manifest.json').read_bytes();self.assertEqual(c.sha(packet),h.PACKET_SHA)
  fs=self.fs()
  with fs.context(),h.authenticated_modules(packet,git_blob) as m:
   reader=c.ProtectedReader();adapter=h.historical_read_adapter(reader,m)
   self.assertIs(type(adapter),c.HistoricalReadAdapter)
   for path in LARGE:fs.put(path,2*1024**2+1)
   # Original source readers traverse their complete fixed source catalogues.
   for name in m['prepared97_reader'].NAMES:
    path=m['prepared97_reader'].DOMAIN+name
    if path not in fs.files:fs.put(path,1)
   reg=h.decode(m['prepared_registries'].REGISTRY98)
   for name in set(reg['records'])|set(reg['plain_full_maps']):
    path=m['prepared_types'].REPAIR_ROOT+name
    if path not in fs.files:fs.put(path,1)
   a=m['prepared97_reader'].snapshot(adapter);b=m['prepared_reader'].snapshot_protected_history(adapter,m['prepared_registries'].REGISTRY98)
   for name in ('runtime-before.json','runtime-manifest.json'):self.assertGreater(len(a[name]),2097152);self.assertGreater(len(b[name]),2097152)
   self.assertEqual(len(reader.observed),len(fs.files));reader.stable()
 def test_authenticated_adapter_original_snapshots_full_synthetic_expired_dag(self):
  # Existing PUBLIC synthetic DAG, never actual199a/d91/private failure bytes.
  ops=PUBLIC.parent.parent/'release-transition-20261026';tests=ops/'tests';saved={n:sys.modules[n] for n in h.PURE if n in sys.modules};oldpath=list(sys.path)
  try:
   sys.path.insert(0,str(tests));bootstrap=load('cap_public_bootstrap',tests/'portable_bootstrap.py');bootstrap.install()
   synthetic=load('cap_synthetic97',tests/'synthetic97.py');f=synthetic.Synthetic97()
   f.runtime.update({'backend/venv/lib/synthetic-%05d.py'%i:'3'*64 for i in range(25000)})
   f.v['runtime-before.json']=dict(f.runtime);f.v['runtime-manifest.json']=dict(f.runtime)
   f.v['checks.json']['retained_runtime_manifest_sha256']=synthetic.sha(synthetic.encoded(f.runtime));f.refresh()
   for name in h.PURE:sys.modules.pop(name,None)
   fs=self.fs()
   with fs.context(),h.authenticated_modules((PUBLIC/'historical_packet_manifest.json').read_bytes(),git_blob) as m:
    reader=c.ProtectedReader();adapter=h.historical_read_adapter(reader,m)
    for name,raw in f.b.items():fs.put_raw(m['prepared97_reader'].DOMAIN+name,raw)
    for name,raw in f.old.items():fs.put_raw(m['prepared_types'].REPAIR_ROOT+name,raw)
    current=m['prepared97_reader'].snapshot(adapter);old=m['prepared_reader'].snapshot_protected_history(adapter,m['prepared_registries'].REGISTRY98)
    self.assertGreater(len(current['runtime-manifest.json']),2097152)
    result=m['prepared97_history'].inspect_history(current,f.binding,f.sources,old,f.sources98,f.registry_raw)
    self.assertEqual(result['runtime_map'],f.runtime);self.assertFalse(result['historical']['authorizes_preparation']);reader.stable()
   # Original private failure validator remains raw unchanged and separate;
   # synthetic full byte-DAG test does not claim real original-failure proof.
  finally:
   for name in h.PURE:sys.modules.pop(name,None)
   sys.modules.update(saved);sys.path[:]=oldpath
 def test_real_large_boundary_four_only_generic_default_unchanged(self):
  fs=self.fs();fs.put(LARGE[0],16777216);fs.put(SMALL,2097153)
  with fs.context():
   reader=c.ProtectedReader();adapter=c.HistoricalReadAdapter(reader);self.assertEqual(len(adapter(LARGE[0])),16777216)
   with self.assertRaises(c.Refused):reader.read(LARGE[0])
   with self.assertRaises(c.Refused):adapter(SMALL)
  fs2=self.fs();fs2.put(LARGE[1],16777217)
  with fs2.context(),self.assertRaises(c.Refused):c.HistoricalReadAdapter(c.ProtectedReader())(LARGE[1])
 def test_actual_aggregate64_not_increased_and_repeated_reads_not_recounted(self):
  fs=self.fs()
  for path in LARGE:fs.put(path,16777216)
  fs.put(SMALL,1)
  with fs.context():
   reader=c.ProtectedReader();adapter=c.HistoricalReadAdapter(reader)
   for path in LARGE:self.assertEqual(len(adapter(path)),16777216)
   self.assertEqual(reader.total,64*1024**2);adapter(LARGE[0]);self.assertEqual(reader.total,64*1024**2)
   with self.assertRaises(c.Refused):adapter(SMALL)
 def test_constructor_path_variants_and_wrong_reader_no_public_limits(self):
  reader=c.ProtectedReader()
  with self.assertRaises(TypeError):c.HistoricalReadAdapter(reader,LARGE)
  with self.assertRaises(c.Refused):c.HistoricalReadAdapter(types.SimpleNamespace(read=lambda *a:b''))
  adapter=c.HistoricalReadAdapter(reader)
  for path in (LARGE[0]+'/',LARGE[0].replace('/runtime','/./runtime'),LARGE[0].replace('/runtime','/../runtime'),'/'+LARGE[0],LARGE[0].replace('/runtime','\\runtime'),LARGE[0].encode()):
   with self.subTest(path=path),self.assertRaises(c.Refused):adapter(path)
 def test_mode_owner_links_symlink_parent_and_fd_stability_unchanged(self):
  for key,value in [('st_uid',1),('st_nlink',2),('st_mode',stat.S_IFREG|0o644),('st_mode',stat.S_IFLNK|0o600)]:
   fs=self.fs();fs.put(LARGE[0],2097153);fs.overrides[LARGE[0]]={key:value}
   with self.subTest(key=key,value=value),fs.context(),self.assertRaises(c.Refused):c.HistoricalReadAdapter(c.ProtectedReader())(LARGE[0])
  fs=self.fs();fs.put(LARGE[0],2097153);fs.parent_bad=True
  with fs.context(),self.assertRaises(c.Refused):c.HistoricalReadAdapter(c.ProtectedReader())(LARGE[0])
  fs=self.fs();fs.put(LARGE[0],2097153)
  with fs.context():
   reader=c.ProtectedReader();adapter=c.HistoricalReadAdapter(reader);adapter(LARGE[0]);fs.overrides[LARGE[0]]={'st_ino':123456789}
   with self.assertRaises(c.Refused):reader.stable()
  fs=self.fs();fs.put(LARGE[0],2097153);fs.fd_bad=True
  with fs.context(),self.assertRaises(c.Refused):c.HistoricalReadAdapter(c.ProtectedReader())(LARGE[0])
  for field in c.FIELDS:
   fs=self.fs();fs.put(LARGE[0],2097153)
   with fs.context():
    reader=c.ProtectedReader();c.HistoricalReadAdapter(reader)(LARGE[0]);original=reader.observed[LARGE[0]][2][c.FIELDS.index(field)];fs.overrides[LARGE[0]]={field:original+1}
    with self.subTest(stable_field=field),self.assertRaises(c.Refused):reader.stable()
 def test_wrong_registry_names_domain_refuse_before_any_larger_read(self):
  packet=(PUBLIC/'historical_packet_manifest.json').read_bytes()
  with h.authenticated_modules(packet,git_blob) as m:
   reader=c.ProtectedReader()
   for owner,key,bad in [(m['prepared_registries'],'REGISTRY98',b'{}'),(m['prepared_registries'],'REGISTRY97',b'{}'),(m['prepared97_reader'],'DOMAIN','/foreign/'),(m['prepared97_reader'],'NAMES',frozenset({'runtime-before.json','runtime-manifest.json'}))]:
    with patch.object(owner,key,bad),patch.object(reader,'read',side_effect=AssertionError('NO_READ_BEFORE_AUTH')) as read:
     with self.assertRaises(h.Refused):h.historical_read_adapter(reader,m)
     self.assertEqual(read.call_count,0)
 def test_generic_reader_entire_raw_class_and_next_scanner_limits_unchanged(self):
  def cls(raw,name):
   n=next(n for n in ast.parse(raw).body if isinstance(n,ast.ClassDef) and n.name==name);return b''.join(raw.splitlines(True)[n.lineno-1:n.end_lineno])
  self.assertEqual(cls((P/'fixtures/native_io-precap.reference.py').read_bytes(),'ProtectedReader'),cls((P/'native_io.py').read_bytes(),'ProtectedReader'))
  source=(PUBLIC/'native_runtime.py').read_text();self.assertIn('reader.read(p,2097152,(0o600,0o640,0o644,0o750,0o755))',source);self.assertIn('0<=row[\'bytes\']<=512*1024**2',source)
if __name__=='__main__':unittest.main()
