import unittest,pathlib,io,ast
from unittest.mock import patch
import native_io as n
import native_runtime as r
D=pathlib.Path(__file__).parent
class Tests(unittest.TestCase):
 def test_exact_reviewed_scanners_compile_without_live_backend(self):
  original={name:(D/'public-runtime35'/name).read_bytes() for name in r.PINS};public=dict(original);public['native_backend.py']=(D/'test-public-rpc92bc/native_backend.py').read_bytes();cls=r.methods(public,original,r.EXPECTED_SCANNER_LINEAGE);self.assertEqual(set(k for k in cls.__dict__ if not k.startswith('__')),{'source','runtime_digest'})
  public['native_backend.py']=public['native_backend.py'].replace(b'512*1024**2',b'513*1024**2');self.assertRaises(n.Refused,r.methods,public,original,r.EXPECTED_SCANNER_LINEAGE)
 def test_bootstrap_scalar_hooks_not_called(self):
  class Evil(str):
   def __hash__(self):raise AssertionError('HOOK')
  self.assertRaises(n.Refused,n.bootstrap,Evil('web'))
 def test_git_unsafe_command_never_spawned(self):
  with patch.object(n,'retained_run') as spawned:self.assertRaises(n.Refused,n.GitObjects().run,'git','-C',n.REPOSITORY,'checkout','main');spawned.assert_not_called()
 def test_recorded_software_keeps_historical_fields_only(self):
  rows={'x':{'kind':'file','mode':0o750,'sha256':'a'*64,'size':3},'.':{'kind':'directory'}};self.assertEqual(r.recorded_software(rows),{'x':{'sha256':'a'*64,'bytes':3,'executable':True}})
 def test_signals_deferred_before_child(self):
  events=[]
  class Child:
   stdout=io.BytesIO(b'public')
   def wait(self):events.append('terminal');return 0
   def poll(self):return 0
  def spawn(*a,**k):events.append('spawn');self.assertGreaterEqual(events.count('signal'),3);return Child()
  with patch.object(n.signal,'SIGHUP',1,create=True),patch.object(n.signal,'getsignal',return_value=0),patch.object(n.signal,'signal',side_effect=lambda *_:events.append('signal')),patch.object(n.subprocess,'Popen',side_effect=spawn):self.assertEqual(n.retained_run(['/usr/bin/git']),b'public')
  self.assertLess(events.index('terminal'),len(events)-1)
 def test_signal_setup_failure_never_spawns(self):
  count=[0]
  def fail(*_):
   count[0]+=1
   if count[0]==2:raise RuntimeError('synthetic')
  with patch.object(n.signal,'SIGHUP',1,create=True),patch.object(n.signal,'getsignal',return_value=0),patch.object(n.signal,'signal',side_effect=fail),patch.object(n.subprocess,'Popen') as spawned:self.assertRaises(RuntimeError,n.retained_run,['/usr/bin/git']);spawned.assert_not_called()
