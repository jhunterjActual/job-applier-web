"""Entirely synthetic protocol/IO boundaries. No native state, plans or SSH."""
import unittest,io,json,ast,pathlib,hashlib,copy
from unittest.mock import patch
import native_io as n
import preplan_program as p
import package_bootstrap as b
import private_channel as c
import session_server as server
import assemble_plan as a
D=pathlib.Path(__file__).parent
class Tests(unittest.TestCase):
 def test_public35_capsule_real_bytes_authenticate(self):
  raw=(D/'packet_manifest.json').read_bytes();caps={q.relative_to(D/'public-runtime35').as_posix():q.read_bytes() for q in (D/'public-runtime35').rglob('*') if q.is_file()};self.assertEqual(len(p.public_sources(raw,capsule=caps)),35)
  caps['release_transaction.py']+=b' ';self.assertRaises(n.Refused,p.public_sources,raw,None,caps)
 def test_manifest_full_membership_not_caller_list(self):
  self.assertEqual(len(b.public_paths()),35);self.assertEqual(set(b.PROGRAM_FILES),{name+'.py' for name in c.MODULE_ORDER});self.assertRaises(n.Refused,b.source_manifest,b'{}','a'*64)
 def test_bootstrap_compiles_and_no_clock_spoof(self):
  ast.parse(c.BOOTSTRAP);self.assertIn("cache[name+'.py']",c.BOOTSTRAP);self.assertNotIn('sys.flags=',c.BOOTSTRAP);self.assertNotIn('__file__=',c.BOOTSTRAP)
 def test_command_exact_private_binary_source_route(self):
  cmd=c.command('db','a'*64,'inspect');self.assertIn('StrictHostKeyChecking=yes',cmd);self.assertIn('ClearAllForwardings=yes',cmd);self.assertEqual(cmd[-3:],['a'*64,'db','inspect']);self.assertNotIn('PRIVATE',str(cmd))
 def test_frame_oversize_refuses_before_private_write(self):
  sink=io.BytesIO();self.assertRaises(n.Refused,c.send,sink,{'PRIVATE_SENTINEL':'x'*c.MAXIMUM});self.assertEqual(sink.getvalue(),b'')
 def test_database_eof_no_callback(self):
  class Session:
   def hello(self):raise AssertionError('unexpected')
  self.assertEqual(server.database(Session(),io.BytesIO(),io.BytesIO(),'a'*64),0)
 def test_database_wrong_sequence_no_callback(self):
  stream=io.BytesIO(n.canonical({'seq':1,'program_sha256':'a'*64,'action':'hello','args':{}})+b'\n')
  class Session:
   def hello(self):raise AssertionError('unexpected')
  self.assertRaises(n.Refused,server.database,Session(),io.BytesIO(),stream,'a'*64)
 def test_database_partial_or_unknown_action_refuses(self):
  for raw in [b'{"PRIVATE_SENTINEL":',n.canonical({'seq':0,'program_sha256':'a'*64,'action':'write','args':{}})+b'\n']:
   self.assertRaises((RuntimeError,ValueError),server.database,object(),io.BytesIO(),io.BytesIO(raw),'a'*64)
 def test_native_allocator_not_bound_no_clock(self):
  with patch.object(a,'datetime') as clock:self.assertRaises(a.Refused,a.allocate_actual,{}, {},'/not-used');clock.now.assert_not_called()
 def test_native_context_no_selfreported_fact_bypass(self):
  self.assertRaises(a.Refused,a.authenticate_external_facts,{'ready':True},{})
 def test_standard_application_fact_canonical_domain(self):
  value={'unicode':'ÃƒÂ©'};self.assertEqual(n.canonical(value),a.canonical(value));self.assertNotEqual(n.canonical(value),n.canonical(value)+b'\n')
