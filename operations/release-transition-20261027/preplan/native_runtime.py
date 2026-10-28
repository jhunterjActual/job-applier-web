"""Complete current scanner; current metadata security is not invented historical metadata."""
import ast,io,tarfile,re,os,stat,hashlib,json
from pathlib import Path,PurePosixPath
from native_io import need,sha,identity,parents,decode
BASE='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
ROOT=Path('/opt/careertrellis/staging');REPOSITORY='/srv/careertrellis/job-applier-web'
TARGET='bc68ea0dc253425a44e268452130becdf35d764e';CORRECTION='f70417d4bd70c3eb086ea63c90938dd2f43e0b99'
BROWSER=Path('/var/lib/careertrellis/shared/ms-playwright')
PROVENANCE=Path('/opt/careertrellis/staging/preparation98-repair-source-20261027/repair98-fixture-g1/baseline-provenance.json')
PROVENANCE_SHA='b4c21424441e63b45d484c2ff063afae394c745fd0a235c7210c11beecb05ee7'
DIFFS={TARGET:'db02c4ca5e044b3862fcef562a9eca75c0e4423fadca83db4eba5fdc9e6d4357',CORRECTION:'ad8f04dbf0afeeabb9ec1d3557cc5e709b4ebc0488490424950df83843beda12'}
PINS={'native_backend.py':'5c86f98fe6059330d5025a7d0041015a44fa406963cac8b137fe1e6c0a1b4c03','canonical_primitives.py':'778c9f1dc8e4c3afe38621adbeb7e819ecefed92bbde20cb1fdb671adc57968a'}

RAW_METHOD_PINS={'runtime_digest': '4eb39281876ab1bfd3411cf7753d9bf42dca9ad828c538f19a9703d856a6a4b5', 'source': 'e1a43ce50cec149ac10d99a8225521f512485dc1ccebc7189de007f1667a4254'}
AST_METHOD_PINS={'runtime_digest': 'ada7e86ac3a2a6022ea9d232209790871f82add2aa187b4d2108aa0d94aebfb4', 'source': 'ea1df427663b9b4bb9cba9d8fb9f87e9d2036a27a988af255a4c9505faee9f59'}

EXPECTED_SCANNER_LINEAGE={'runtime_digest': {'equivalence_kind': 'CRLF-to-LF-only/v1', 'original_whole_sha256': '5c86f98fe6059330d5025a7d0041015a44fa406963cac8b137fe1e6c0a1b4c03', 'current_whole_sha256': '92bc552a30ab992d4dd0d038d26e7315b3164ba332ecadbbed6a234c23e28ef7', 'original_method_raw_sha256': '4eb39281876ab1bfd3411cf7753d9bf42dca9ad828c538f19a9703d856a6a4b5', 'current_method_raw_sha256': 'ee449a8f96d564a3eaeb3f053c4182c441ce778fb8bb1f25c4e0e1b9c7850788', 'normalized_method_sha256': 'ee449a8f96d564a3eaeb3f053c4182c441ce778fb8bb1f25c4e0e1b9c7850788', 'ast_semantic_sha256': 'ada7e86ac3a2a6022ea9d232209790871f82add2aa187b4d2108aa0d94aebfb4'}, 'source': {'equivalence_kind': 'byte-identical/v1', 'original_whole_sha256': '778c9f1dc8e4c3afe38621adbeb7e819ecefed92bbde20cb1fdb671adc57968a', 'current_whole_sha256': '778c9f1dc8e4c3afe38621adbeb7e819ecefed92bbde20cb1fdb671adc57968a', 'original_method_raw_sha256': 'e1a43ce50cec149ac10d99a8225521f512485dc1ccebc7189de007f1667a4254', 'current_method_raw_sha256': 'e1a43ce50cec149ac10d99a8225521f512485dc1ccebc7189de007f1667a4254', 'normalized_method_sha256': 'e1a43ce50cec149ac10d99a8225521f512485dc1ccebc7189de007f1667a4254', 'ast_semantic_sha256': 'ea1df427663b9b4bb9cba9d8fb9f87e9d2036a27a988af255a4c9505faee9f59'}}

def scanner_lineage(value):
 need(type(value)is dict and all(type(k)is str and type(v)is dict for k,v in dict.items(value)) and set(value)==set(EXPECTED_SCANNER_LINEAGE))
 for name,row in value.items():need(all(type(k)is str and type(v)is str for k,v in dict.items(row)) and row==EXPECTED_SCANNER_LINEAGE[name])
 return value

def methods(public,original_public,lineage):
 scanner_lineage(lineage);need(type(original_public)is dict and set(original_public)==set(PINS));nodes=[]
 for filename,owner,name in [('native_backend.py','Backend','runtime_digest'),('canonical_primitives.py','LinuxHost','source')]:
  raw=public[filename];old=original_public[filename];rule=lineage[name];need(type(raw)is bytes and type(old)is bytes and sha(raw)==rule['current_whole_sha256'] and sha(old)==rule['original_whole_sha256'])
  def extract(body):
   cls=next(x for x in ast.parse(body).body if isinstance(x,ast.ClassDef) and x.name==owner);node=next(x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name==name);return node,ast.get_source_segment(body.decode('utf-8'),node).encode('utf-8')
  node,current=extract(raw);original_node,original=extract(old);need(sha(current)==rule['current_method_raw_sha256'] and sha(original)==rule['original_method_raw_sha256'])
  need(b'\r' not in current and b'\r' not in original.replace(b'\r\n',b'\n'))
  need(original.replace(b'\r\n',b'\n')==current and sha(current)==rule['normalized_method_sha256'])
  need(ast.dump(node,include_attributes=False)==ast.dump(original_node,include_attributes=False) and sha(ast.dump(node,include_attributes=False).encode())==rule['ast_semantic_sha256']);nodes.append(node)
 tree=ast.Module(body=[ast.ClassDef(name='VerifiedMethods',bases=[],keywords=[],body=nodes,decorator_list=[])],type_ignores=[]);ast.fix_missing_locations(tree)
 ns={'need':need,'require':need,'digest':sha,'ROOT':ROOT,'BASE':BASE,'REPOSITORY':REPOSITORY,'Path':Path,'PurePosixPath':PurePosixPath,'os':os,'stat':stat,'hashlib':hashlib,'tarfile':tarfile,'io':io,'re':re}
 exec(compile(tree,'exact-approved-current-methods-with-original-byte-lineage','exec'),ns);return ns['VerifiedMethods']

class CompleteScanner:
 def __init__(self,public,reader,git,lineage,original_public):
  self.native=methods(public,original_public,lineage)();self.source_attestation={'original_whole_files':dict(PINS),'current_whole_files':{name:sha(public[name]) for name in PINS},'raw_methods':dict(RAW_METHOD_PINS),'ast_methods':dict(AST_METHOD_PINS),'approved_profile_lineage':lineage};self.reader=reader;self.git=git;self.identities={};self.memberships={}
  self.native.run=git.run;self.native.protected=lambda p:reader.read(p,2097152,(0o600,0o640,0o644,0o750,0o755))
 def file(self,path):
  path=Path(path);before=path.lstat();pin=self.native.runtime_digest(path);s=path.lstat();need(identity(before)==identity(s));self.identities[str(path)]=identity(s)
  return {'kind':'file','mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid,'gid':s.st_gid,'nlink':s.st_nlink,'size':s.st_size,'sha256':pin}
 def snapshot(self,root):
  root=Path(root);parents(root);result={};count=0
  for folder,dirs,files in os.walk(root,followlinks=False,onerror=lambda _:need(False)):
   p=Path(folder);s=p.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022);self.identities[str(p)]=identity(s)
   names=tuple(sorted(dirs+files));self.memberships[str(p)]=names;count+=len(names);need(count<=100000)
   relative=p.relative_to(root).as_posix();result[relative]={'kind':'directory','mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid,'gid':s.st_gid,'nlink':s.st_nlink,'size':s.st_size,'sha256':None}
   for name in dirs:need(not (p/name).is_symlink())
   for name in files:result[(p/name).relative_to(root).as_posix()]=self.file(p/name)
  need(result and '.' in result);return result
 def stable(self):
  for path,value in self.identities.items():need(identity(Path(path).lstat())==value)
  for path,names in self.memberships.items():need(tuple(sorted(q.name for q in Path(path).iterdir()))==names)
 def source(self,revision,build):self.native.source(revision,None,build,DIFFS[revision])

def validate_provenance_record(raw,reader,history_cache):
 need(sha(raw)==PROVENANCE_SHA);p=decode(raw)
 need(type(p) is dict and set(p)=={'schema','baseline','requirements','interpreter','site_packages','browser','platform','reviewed_at','accepted_venv_provenance'} and p['schema']=='careertrellis.existing-runtime-provenance/v1' and p['baseline']==BASE)
 need(set(p['requirements'])=={'backend/requirements.in','backend/requirements.txt'} and p['platform']['system']=='Linux' and p['platform']['machine']=='x86_64')
 interp=p['interpreter'];need(set(interp)=={'path','sha256','python_version'} and interp['path']=='/usr/bin/python3.12' and re.fullmatch(r'3\.12\.\d+',interp['python_version']))
 receipt=p['accepted_venv_provenance'];need(receipt=={'path':'/opt/careertrellis/staging/title-offweb-import-7a4d3f077c4d/venv-provenance.json','sha256':'82ab88e5d3f76a21ae3ee8d4fc51cd64a6d24c09faae33edbda4067d9526af63'})
 need(sha(reader.read(receipt['path'],2097152))==receipt['sha256'])
 # Original accepted preflight/plan authentication already happened in4c9.
 from datetime import datetime,timedelta
 reviewed=datetime.fromisoformat(p['reviewed_at'].replace('Z','+00:00'));need(reviewed.tzinfo is not None);found=0
 for path,entry in history_cache.items():
  if path.endswith('/preflight.json'):
   pre=decode(entry[3])
   if pre.get('provenance_sha256')==PROVENANCE_SHA:
    original=pre.get('observed_at',pre.get('checked_at'));need(type(original) is str);when=datetime.fromisoformat(original.replace('Z','+00:00'));need(reviewed<=when<=reviewed+timedelta(days=1));found+=1
 need(found>=1)
 for key in ('site_packages','browser'):
  need(type(p[key]) is dict and p[key])
  for name,row in p[key].items():
   q=PurePosixPath(name);need(str(q)==name and not q.is_absolute() and '..' not in q.parts and type(row) is dict and set(row)=={'sha256','bytes','executable'} and type(row['bytes']) is int and 0<=row['bytes']<=512*1024**2 and type(row['executable']) is bool and re.fullmatch('[0-9a-f]{64}',row['sha256']))
 return p

def recorded_software(snapshot):
 return {path:{'sha256':row['sha256'],'bytes':row['size'],'executable':bool(row['mode']&0o111)} for path,row in snapshot.items() if row['kind']=='file'}

def collect_current(public,reader,git,historical,lineage,original_public):
 scanner=CompleteScanner(public,reader,git,lineage,original_public);provenance=validate_provenance_record(reader.read(PROVENANCE,2097152),reader,reader.observed)
 browser_before=scanner.snapshot(BROWSER);need(recorded_software(browser_before)==provenance['browser']);interpreter=scanner.file(provenance['interpreter']['path']);need(interpreter['sha256']==provenance['interpreter']['sha256'])
 result={}
 for revision,build in ((TARGET,'20261026.97'),(CORRECTION,'20261026.98')):
  scanner.source(revision,build);root=ROOT/'releases'/revision;before=scanner.snapshot(root);after=scanner.snapshot(root);need(before==after)
  runtime={p:r['sha256'] for p,r in before.items() if r['kind']=='file' and (p.startswith('backend/venv/') or '__pycache__' in PurePosixPath(p).parts)};need(runtime==historical['runtime_maps'][revision])
  for prefix in ('backend/venv/lib/python3.12/site-packages/','backend/venv/lib64/python3.12/site-packages/'):
   package={path[len(prefix):]:{'sha256':r['sha256'],'bytes':r['size'],'executable':bool(r['mode']&0o111)} for path,r in before.items() if path.startswith(prefix) and r['kind']=='file'};need(package==provenance['site_packages'])
  for name,pin in provenance['requirements'].items():need(sha(reader.read(root/name,2097152,(0o640,0o600,0o644)))==pin)
  result[revision]={'snapshot':before,'runtime_count':len(runtime)}
 need(scanner.snapshot(BROWSER)==browser_before);scanner.stable();return {'releases':result,'browser':browser_before,'interpreter':interpreter,'scanner':scanner,'provenance_sha256':PROVENANCE_SHA,'scanner_source_lineage':scanner.source_attestation}
