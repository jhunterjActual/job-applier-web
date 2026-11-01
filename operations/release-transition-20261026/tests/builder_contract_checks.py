import unittest,tempfile,types,sys,json,hashlib
from pathlib import Path
from unittest.mock import patch
class Tests(unittest.TestCase):
 def fixture(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
  m=types.ModuleType('peer_server');m.MODULES=('alpha',);m.SCRIPTS=('peer_server.py',);m.READINESS_FILES=('collector.py','transaction04-pins.json','SOURCE_MANIFEST.json')
  self.env={'__name__':'review_build'}
  with patch.dict(sys.modules,{'peer_server':m}):exec(compile((Path(__file__).parent.parent/'build_manifest.py').read_bytes(),'UNAPPLIED','exec'),self.env)
  for n,b in {'alpha.py':b'alpha','peer_server.py':b'peer','one_shot_readiness/collector.py':b'collector','one_shot_readiness/transaction04-pins.json':json.dumps({'files':{'alpha.py':hashlib.sha256(b'alpha').hexdigest(),'peer_server.py':hashlib.sha256(b'peer').hexdigest()}}).encode()}.items():
   p=self.p/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
  nested={'schema':'careertrellis.one-shot-readiness-source/v1','files':{n:hashlib.sha256((self.p/'one_shot_readiness'/n).read_bytes()).hexdigest() for n in m.READINESS_FILES if n!='SOURCE_MANIFEST.json'}}
  (self.p/'one_shot_readiness/SOURCE_MANIFEST.json').write_text(json.dumps(nested))
  self.blobs={p.relative_to(self.p).as_posix():p.read_bytes() for p in self.p.rglob('*') if p.is_file()}
  def git(root,*args):
   if args[:2]==('remote','get-url'):return b'https://github.com/jhunterjActual/job-applier-web.git'
   if args[0]=='rev-parse':return ('a'*40).encode()
   if args[0]=='merge-base':return b''
   return self.blobs[args[-1].split('operations/release-transition-20261026/',1)[1]]
  self.env['git']=git
 def runbuild(self):return self.env['build'](self.p,'a'*40,self.p,self.p/'output.json')
 def test_readiness_explicit_domain(self):
  self.fixture();self.runbuild();v=json.loads((self.p/'output.json').read_bytes());self.assertEqual(len(v['readiness']),3)
 def test_missing_nested_file_refuses(self):
  self.fixture();(self.p/'one_shot_readiness/transaction04-pins.json').unlink()
  with self.assertRaises(FileNotFoundError):self.runbuild()
 def test_untracked_nested_byte_refuses(self):
  self.fixture();(self.p/'one_shot_readiness/collector.py').write_bytes(b'drift')
  with self.assertRaises(RuntimeError):self.runbuild()
 def test_wrong_nested_pin_refuses_even_if_git_matches(self):
  self.fixture();p=self.p/'one_shot_readiness/SOURCE_MANIFEST.json';v=json.loads(p.read_bytes());v['files']['collector.py']='0'*64;p.write_text(json.dumps(v));self.blobs['one_shot_readiness/SOURCE_MANIFEST.json']=p.read_bytes()
  with self.assertRaises(RuntimeError):self.runbuild()

 def bad_transaction(self,raw):
  p=self.p/'one_shot_readiness/transaction04-pins.json';p.write_bytes(raw);self.blobs['one_shot_readiness/transaction04-pins.json']=raw
  q=self.p/'one_shot_readiness/SOURCE_MANIFEST.json';v=json.loads(q.read_bytes());v['files']['transaction04-pins.json']=hashlib.sha256(raw).hexdigest();q.write_text(json.dumps(v));self.blobs['one_shot_readiness/SOURCE_MANIFEST.json']=q.read_bytes()
 def test_empty_missing_extra_wrong_and_stale_transaction_refuse(self):
  for kind in ('empty','missing','extra','wrong','stale'):
   self.fixture();v=json.loads((self.p/'one_shot_readiness/transaction04-pins.json').read_bytes())
   if kind=='empty':v={}
   elif kind=='missing':del v['files']['alpha.py']
   elif kind=='extra':v['files']['unknown.py']='0'*64
   elif kind=='wrong':v['files']['alpha.py']='0'*64
   else:v['files']['peer_server.py']='1'*64
   self.bad_transaction(json.dumps(v).encode())
   with self.assertRaises(RuntimeError):self.runbuild()
   self.assertFalse((self.p/'output.json').exists())
 def test_duplicate_transaction_key_refuses(self):
  self.fixture();self.bad_transaction(b'{"files":{},"files":{}}')
  with self.assertRaises(RuntimeError):self.runbuild()

 def test_verified_transaction_swapped_after_git_check_refuses(self):
  self.fixture();original=self.env['git'];changed=False
  def swap(root,*args):
   nonlocal changed
   raw=original(root,*args)
   if args[0]=='cat-file' and args[-1].endswith('SOURCE_MANIFEST.json') and not changed:
    changed=True;(self.p/'one_shot_readiness/transaction04-pins.json').write_bytes(b'{"files":{}}')
   return raw
  self.env['git']=swap
  with self.assertRaises(RuntimeError):self.runbuild()
  self.assertFalse((self.p/'output.json').exists())
