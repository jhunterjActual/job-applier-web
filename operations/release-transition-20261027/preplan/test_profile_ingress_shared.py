"""Synthetic future profile ONLY; public8853 code is not a current deployed commit."""
import unittest,json,pathlib,hashlib,copy,types,io,contextlib,inspect
from unittest.mock import patch
import native_io as n
import native_runtime as r
import native_ingress as ingress
import native_idle as idle
import preplan_program as p
import package_bootstrap as b
D=pathlib.Path(__file__).parent
class Tests(unittest.TestCase):
 def profile_fixture(self):
  public={q.relative_to(D/'public-runtime35').as_posix():q.read_bytes() for q in (D/'public-runtime35').rglob('*') if q.is_file()};outer=n.decode((D/'packet_manifest.json').read_bytes());outer['source_commit']='c'*40
  for name in ('collector.py','pair_boundary.py','readiness_bridge.py','native_backend.py','peer_server.py'):
   path='one_shot_readiness/'+name if name in ('collector.py','pair_boundary.py') else name;public[path]=(D/'test-public-template-current'/name).read_bytes()
  for stem in outer['modules']:outer['modules'][stem]=n.sha(public[stem+'.py'])
  for name in outer['scripts']:outer['scripts'][name]=n.sha(public[name])
  tx={stem+'.py':pin for stem,pin in outer['modules'].items()};tx.update(outer['scripts']);public['one_shot_readiness/transaction04-pins.json']=n.canonical({'files':tx})
  for name in outer['readiness']:
   if name!='SOURCE_MANIFEST.json':outer['readiness'][name]=n.sha(public['one_shot_readiness/'+name])
  public['one_shot_readiness/SOURCE_MANIFEST.json']=n.canonical({'schema':'careertrellis.one-shot-readiness-source/v1','files':{name:pin for name,pin in outer['readiness'].items() if name!='SOURCE_MANIFEST.json'}});outer['readiness']['SOURCE_MANIFEST.json']=n.sha(public['one_shot_readiness/SOURCE_MANIFEST.json']);raw=n.canonical(outer);profile={'source_commit':'c'*40,'packet_sha256':n.sha(raw),'readiness_manifest_sha256':outer['readiness']['SOURCE_MANIFEST.json'],'scanner_methods':r.EXPECTED_SCANNER_LINEAGE};return public,outer,raw,profile
 def test_synthetic_profile_full_source_and_original_scanners(self):
  public,_,raw,profile=self.profile_fixture();self.assertEqual(len(p.public_sources(raw,capsule=public,runtime_profile=profile)),35);self.assertTrue(hasattr(r.methods(public,{name:(D/'public-runtime35'/name).read_bytes() for name in r.PINS},r.EXPECTED_SCANNER_LINEAGE),'runtime_digest'));self.assertNotEqual(n.sha(public['native_backend.py']),r.PINS['native_backend.py'])
 def test_nested_transaction_pins_not_merely_outer_hashes(self):
  public,outer,_,profile=self.profile_fixture();public['one_shot_readiness/transaction04-pins.json']=n.canonical({'files':{}});outer['readiness']['transaction04-pins.json']=n.sha(public['one_shot_readiness/transaction04-pins.json']);nested=n.decode(public['one_shot_readiness/SOURCE_MANIFEST.json']);nested['files']['transaction04-pins.json']=outer['readiness']['transaction04-pins.json'];public['one_shot_readiness/SOURCE_MANIFEST.json']=n.canonical(nested);outer['readiness']['SOURCE_MANIFEST.json']=n.sha(public['one_shot_readiness/SOURCE_MANIFEST.json']);raw=n.canonical(outer);profile['packet_sha256']=n.sha(raw);profile['readiness_manifest_sha256']=outer['readiness']['SOURCE_MANIFEST.json'];self.assertRaises(n.Refused,p.public_sources,raw,None,public,profile)
 def test_method_raw_body_change_refused_despite_new_profile(self):
  public,_,_,_=self.profile_fixture();public['native_backend.py']=public['native_backend.py'].replace(b'512*1024**2',b'513*1024**2');self.assertRaises(n.Refused,r.methods,public,{name:(D/'public-runtime35'/name).read_bytes() for name in r.PINS},r.EXPECTED_SCANNER_LINEAGE)
 def test_method_location_only_lineage_is_explicit(self):
  public,_,_,_=self.profile_fixture();public['native_backend.py']=b'# SYNTHETIC location-only\n'+public['native_backend.py'];self.assertRaises(n.Refused,r.methods,public,{name:(D/'public-runtime35'/name).read_bytes() for name in r.PINS},r.EXPECTED_SCANNER_LINEAGE)
 def manifest_fixture(self):
  public,_,packet,profile=self.profile_fixture();raws={name:(D/name).read_bytes() for name in b.PROGRAM_FILES};raws.update({'public-runtime35/'+name:raw for name,raw in public.items()});raws['packet_manifest.json']=packet;raws['historical_packet_manifest.json']=(D/'historical_packet_manifest.json').read_bytes();raws.update({'scanner-original-'+name:(D/('scanner-original-'+name)).read_bytes() for name in r.PINS});m={'schema':'careertrellis.preplan-source-package/v1','program_commit':'d'*40,'prefix':b.PREFIX,'runtime':profile,'files':{name:{'sha256':n.sha(raw),'bytes':len(raw)} for name,raw in raws.items()}};raw=n.canonical(m);return raws,m,raw
 def test_acyclic_package_checks_real_source_domains_not_git_packet_selfcycle(self):
  raws,m,raw=self.manifest_fixture();requests=[]
  def git(commit,path):
   requests.append((commit,path))
   if commit==m['program_commit']:return raws[path[len(b.PREFIX):]]
   if commit==p.OPS and path.rsplit('/',1)[-1] in r.PINS:return raws['scanner-original-'+path.rsplit('/',1)[-1]]
   self.assertEqual(commit,m['runtime']['source_commit']);return raws['public-runtime35/'+path[len(p.history.PREFIX):]]
  self.assertEqual(b.verify_sources(raw,n.sha(raw),raws,git),m);self.assertFalse(any(path.endswith('packet_manifest.json') for _,path in requests))
 def test_source_scalar_hooks_and_missing_program_refuse(self):
  raws,m,raw=self.manifest_fixture()
  class Evil(str):
   def __eq__(self,o):raise AssertionError('HOOK')
   __hash__=str.__hash__
  self.assertRaises(n.Refused,b.verify_sources,raw,n.sha(raw),{Evil('unknown'):b'x'});bad=dict(raws);bad.pop('session_server.py');self.assertRaises(n.Refused,b.verify_sources,raw,n.sha(raw),bad)
 def test_actual_shared_api_reader_shape_loaded_no_host(self):
  public,_,_,_=self.profile_fixture()
  with idle.readonly_modules(public) as collector:
   import sys
   bridge=sys.modules['readiness_bridge'];self.assertIn('reader',inspect.signature(collector.collect_preplan).parameters);self.assertIn('reader',inspect.signature(bridge.qualify_local_database).parameters);self.assertTrue(callable(sys.modules['pair_boundary'].check_scheduler_pair))
 def route(self,dial=ingress.DIAL):return {'srv0':{'routes':[{'match':[{'host':[ingress.CANONICAL]}],'handle':[{'handler':'reverse_proxy','upstreams':[{'dial':dial}]}]}]}}
 def test_ingress_effective_route_wrong_proxy_or_missing_host_refuses(self):
  ingress.canonical_route(self.route());self.assertRaises(n.Refused,ingress.canonical_route,self.route('10.0.0.1:8001'));self.assertRaises(n.Refused,ingress.canonical_route,{'srv0':{'routes':[]}})
 def test_ingress_only_fixed_known_hosts_and_bools_no_private_admin_body(self):
  private=self.route();private['srv0']['PRIVATE_SENTINEL']='never-export';replies=iter([(200,'',json.dumps(private).encode()),(302,'https://'+ingress.CANONICAL+'/',b''),(401,'',b''),(401,'',b'')]);calls=[];out=io.StringIO()
  with patch.object(ingress,'pointer_state',return_value={'build':'20261017.96'}),patch.object(ingress,'boot',return_value='synthetic'),patch.object(ingress,'unit',return_value={'MainPID':'123'}),patch.object(ingress,'caddy_actor',return_value={'pid':123,'birth':'456'}),patch.object(ingress,'listener_owned',side_effect=lambda pid,port:calls.append((pid,port)) or str(port)),patch.object(ingress,'http',side_effect=lambda *a:next(replies)),contextlib.redirect_stdout(out):proof=ingress.collect(789)
  self.assertNotIn('PRIVATE',json.dumps(proof));self.assertEqual(out.getvalue(),'');self.assertIn((789,8001),calls);self.assertFalse(proof['authenticated_session_tested'])
 def test_ingress_admin_unavailable_no_external_requests(self):
  with patch.object(ingress,'pointer_state',return_value={'build':'20261017.96'}),patch.object(ingress,'boot',return_value='synthetic'),patch.object(ingress,'unit',return_value={'MainPID':'123'}),patch.object(ingress,'caddy_actor',return_value={'pid':123,'birth':'456'}),patch.object(ingress,'listener_owned',return_value='socket'),patch.object(ingress,'http',return_value=(403,'',b'PRIVATE')) as request:self.assertRaises(n.Refused,ingress.collect,789);self.assertEqual(request.call_count,1)

 def test_explicit_lineage_rejects_generic_either_hash_or_other_whitespace(self):
  public,_,_,_=self.profile_fixture();original={name:(D/'public-runtime35'/name).read_bytes() for name in r.PINS}
  for change in ('loneCR','mixed','comment','indent','token'):
   bad=dict(public);raw=bad['native_backend.py']
   if change=='loneCR':raw=raw.replace(b'\n',b'\r',1)
   elif change=='mixed':raw=raw.replace(b'\n',b'\r\n',1)
   elif change=='comment':raw=raw.replace(b'# Installed dependency binaries',b'# CHANGED dependency binaries')
   elif change=='indent':raw=raw.replace(b'        path=Path(path)',b'        path = Path(path)')
   else:raw=raw.replace(b'512*1024**2',b'513*1024**2')
   bad['native_backend.py']=raw;self.assertRaises(n.Refused,r.methods,bad,original,r.EXPECTED_SCANNER_LINEAGE)
  changed=copy.deepcopy(r.EXPECTED_SCANNER_LINEAGE);changed['runtime_digest']['current_method_raw_sha256']=changed['runtime_digest']['original_method_raw_sha256'];self.assertRaises(n.Refused,r.methods,public,original,changed)

 def test_fixed_unauth_paths_reject_200_redirect_and_other_status(self):
  for slot in (2,3):
   for code in (200,302,303,403,500):
    replies=[(200,'',json.dumps(self.route()).encode()),(302,'https://'+ingress.CANONICAL+'/',b''),(401,'',b''),(401,'',b'')];replies[slot]=(code,'https://synthetic.authkit.app/',b'')
    with patch.object(ingress,'pointer_state',return_value={'build':'20261017.96'}),patch.object(ingress,'boot',return_value='synthetic'),patch.object(ingress,'unit',return_value={'MainPID':'123'}),patch.object(ingress,'caddy_actor',return_value={'pid':123,'birth':'456'}),patch.object(ingress,'listener_owned',return_value='socket'),patch.object(ingress,'http',side_effect=replies):self.assertRaises(n.Refused,ingress.collect,789)
 def test_protected_version_drift_refuses_even_both401(self):
  replies=[(200,'',json.dumps(self.route()).encode()),(302,'https://'+ingress.CANONICAL+'/',b''),(401,'',b''),(401,'',b'')]
  with patch.object(ingress,'pointer_state',side_effect=[{'build':'20261017.96'},{'build':'wrong'}]),patch.object(ingress,'boot',return_value='synthetic'),patch.object(ingress,'unit',return_value={'MainPID':'123'}),patch.object(ingress,'caddy_actor',return_value={'pid':123,'birth':'456'}),patch.object(ingress,'listener_owned',return_value='socket'),patch.object(ingress,'http',side_effect=replies):self.assertRaises(n.Refused,ingress.collect,789)

 def test_current_template_inputs_exact_hashbound_not_historical_relabel(self):
  lineage=json.loads((D/'CURRENT_TEMPLATE_SOURCE_LINEAGE.json').read_bytes());self.assertEqual(lineage['actual_profile'],'UNALLOCATED')
  for name,pin in lineage['files'].items():self.assertEqual(hashlib.sha256((D/'test-public-template-current'/name).read_bytes()).hexdigest(),pin)
  public,_,raw,profile=self.profile_fixture();self.assertEqual(n.sha(public['native_backend.py']),lineage['backend_sha256']);self.assertEqual(n.sha(public['one_shot_readiness/collector.py']),lineage['files']['collector.py']);self.assertEqual(len(p.public_sources(raw,capsule=public,runtime_profile=profile)),35)
