"""Reviewed-input deterministic source generator proposal; actual invocation held."""
import ast,hashlib,json,re,base64,os,stat
from pathlib import Path
REVISION='493bf14a3bae2cb2b36f3a57191602b72ea6e3d3'
TARGET='bc68ea0dc253425a44e268452130becdf35d764e'
CACHE_SHA='bb45c3cfc9076b745a0e3f1467996467e12d59ced5c70f17c26e15aea510e815'
NESTED=('collector.py','terminal493.py','transaction04.py','pair_boundary.py')
MODULES=('policy_encoding', 'release_transaction', 'protected_state', 'terminal_validation', 'readiness_bridge', 'native_peer', 'canonical_primitives', 'accepted_pair_validator', 'current_health_validator', 'prepared_types', 'historical98_controls', 'prepared98_history', 'prepared97_specs', 'prepared97_records', 'prepared97_checks', 'prepared_reader', 'prepared_sources', 'prepared97_reader', 'prepared97_sources', 'prepared97_history', 'prepared97_context', 'prepared_registries', 'prepared_evidence', 'native_backend')
SCRIPTS=('peer_server.py', 'web_coordinator.py', 'db_audit.py', 'reviewed_probe.py')
NESTED_APPROVED={'collector.py': 'b73a840773ee31de63cef05136e69fcdcd6f3b40f3749cdd2bdffcff3bbaba0c', 'terminal493.py': '7eaf8d1c98a5049d8554291600bf6212ed9e5bc54bb4e62faafaaf09bcb9b919', 'transaction04.py': '7a4bfa56c0ebcea9683c3556a46c4a610363bc522787060b2766ab1323efcb82', 'pair_boundary.py': '035a8eb086005d15b3375be4337672d757870220157065a44215caec87ebc836'}
class Refused(RuntimeError):pass
def need(v):
 if not v:raise Refused('READINESS_JSON_GENERATOR_REFUSED')
def sha(b):return hashlib.sha256(b).hexdigest()
def canonical(v):return (json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def pairs(rows):
 value={}
 for k,v in rows:need(k not in value);value[k]=v
 return value
def decode(raw):return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
def exact_register(raw,source_bytes):
 register=decode(raw);need(type(register) is dict and set(register)=={'schema','files'} and register['schema']=='careertrellis.final-readiness-inputs/v1')
 expected={n+'.py' for n in MODULES}|set(SCRIPTS)|{'one_shot_readiness/'+n for n in NESTED}
 need(type(source_bytes) is dict and set(source_bytes)==set(register['files'])==expected)
 for name,b in source_bytes.items():need(type(b) is bytes and type(register['files'][name]) is str and re.fullmatch('[0-9a-f]{64}',register['files'][name]) and sha(b)==register['files'][name])
 for name in NESTED:need(sha(source_bytes['one_shot_readiness/'+name])==NESTED_APPROVED[name])
 peer=ast.parse(source_bytes['peer_server.py']);decl={n.targets[0].id:ast.literal_eval(n.value) for n in peer.body if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('MODULES','SCRIPTS')};need(decl=={'MODULES':MODULES,'SCRIPTS':SCRIPTS})
 tx=ast.parse(source_bytes['release_transaction.py']);decl=next(n for n in tx.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TARGET' for t in n.targets));need(ast.literal_eval(decl.value)==TARGET)
 # Source-level target contract is required in addition to approved raw hashes.
 transaction=ast.parse(source_bytes['one_shot_readiness/transaction04.py']);target=next(n for n in transaction.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TARGET' for t in n.targets));need(ast.literal_eval(target.value)==TARGET)
 collector=source_bytes['one_shot_readiness/collector.py'];need(b'56c8b137fd6c6e778a9f09e3b04b925edea6270e' not in collector and TARGET.encode() in collector)
 return register

def installed493(cache_raw):
 need(sha(cache_raw)==CACHE_SHA);cache=decode(cache_raw)
 need(type(cache) is dict and set(cache)=={'schema','revision','contract_git_path','blobs','io','host_observed'} and cache['schema']=='careertrellis.immutable493-public-source-cache/v1' and cache['revision']==REVISION and cache['host_observed'] is False)
 raw={}
 for path,row in cache['blobs'].items():
  need(type(row) is dict and set(row)=={'base64','sha256','bytes','git_blob_id'})
  value=base64.b64decode(row['base64'],validate=True);need(len(value)==row['bytes'] and sha(value)==row['sha256'] and hashlib.sha1(b'blob '+str(len(value)).encode()+b'\0'+value).hexdigest()==row['git_blob_id']);raw[path]=value
 contract=cache['contract_git_path'];need(contract=='deploy/paired_maintenance/build_bundle.py');tree=ast.parse(raw[contract]);constants={}
 for node in tree.body:
  if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('PACKAGE_FILES','UNITS','WRITERS'):
   value=node.value
   if isinstance(value,ast.Call):need(isinstance(value.func,ast.Name) and value.func.id=='frozenset' and len(value.args)==1);value=value.args[0]
   constants[node.targets[0].id]=ast.literal_eval(value)
 need(len(constants['PACKAGE_FILES'])==11 and len(constants['UNITS'])==7 and len(constants['WRITERS'])==3)
 package={name:'deploy/paired_maintenance/'+name for name in constants['PACKAGE_FILES']}
 units={'/etc/systemd/system/'+name:'deploy/systemd/'+name for name in constants['UNITS']}
 units.update({'/etc/systemd/system/'+name+'.d/80-paired-maintenance.conf':'deploy/systemd/'+name+'.d/80-paired-maintenance.conf' for name in constants['WRITERS']})
 need(set(raw)=={contract}|set(package.values())|set(units.values()))
 required={'/etc/systemd/system/careertrellis-paired-'+stem+suffix for stem in ('maintenance','recovery') for suffix in ('.service','.timer')};need(required<=set(units))
 return {'revision':REVISION,'files':{n:sha(raw[path]) for n,path in package.items()},'units':{n:sha(raw[path]) for n,path in units.items()}}

def documents(register_raw,source_bytes,cache_raw):
 exact_register(register_raw,source_bytes);installed=canonical(installed493(cache_raw))
 transaction=canonical({'files':{name:sha(source_bytes[name]) for name in sorted({n+'.py' for n in MODULES}|set(SCRIPTS))}})
 six={name:source_bytes['one_shot_readiness/'+name] for name in NESTED};six.update({'installed493-pins.json':installed,'transaction04-pins.json':transaction})
 manifest=canonical({'schema':'careertrellis.one-shot-readiness-source/v1','files':{name:sha(raw) for name,raw in sorted(six.items())}})
 return {'installed493-pins.json':installed,'transaction04-pins.json':transaction,'SOURCE_MANIFEST.json':manifest}

def safe_read(path,limit=4194304):
 path=Path(path);need(path.is_absolute() and '..' not in path.parts)
 for parent in (path.parent,*path.parents):
  info=parent.lstat();need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0))
 before=path.lstat();need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=limit and not getattr(before,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0))
 fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
 try:
  initial=os.fstat(fd)
  with os.fdopen(os.dup(fd),'rb') as f:raw=f.read(before.st_size+1)
  final=os.fstat(fd);after=path.lstat();fields=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
  need(len(raw)==before.st_size and all(getattr(before,k)==getattr(after,k) for k in fields) and all(getattr(initial,k)==getattr(final,k) for k in fields) and initial.st_size==before.st_size)
  return raw
 finally:os.close(fd)

def prepare(snapshot,register_path,approved_register_sha,cache_path):
 snapshot=Path(snapshot).absolute();need('..' not in snapshot.parts);need(re.fullmatch('[0-9a-f]{64}',approved_register_sha) is not None)
 register_raw=safe_read(Path(register_path).absolute());need(sha(register_raw)==approved_register_sha);register=decode(register_raw)
 names={n+'.py' for n in MODULES}|set(SCRIPTS)|{'one_shot_readiness/'+n for n in NESTED};need(set(register.get('files',{}))==names)
 # Cache and authenticate exact bytes ONCE; parse only these verified snapshots.
 raws={name:safe_read(snapshot/name) for name in sorted(names)};cache_raw=safe_read(Path(cache_path).absolute())
 outputs=documents(register_raw,raws,cache_raw)
 # Complete independent double read before the first output mutation.
 need(safe_read(Path(register_path).absolute())==register_raw and safe_read(Path(cache_path).absolute())==cache_raw)
 need(all(safe_read(snapshot/name)==raw for name,raw in raws.items()));return outputs

def write_outputs(output,outputs,snapshot):
 output=Path(output).absolute();snapshot=Path(snapshot).absolute();need('..' not in output.parts and '..' not in snapshot.parts)
 need(type(outputs) is dict and set(outputs)=={'installed493-pins.json','transaction04-pins.json','SOURCE_MANIFEST.json'} and all(type(v) is bytes for v in outputs.values()))
 need(not output.exists() and output.parent.is_dir() and not output.is_relative_to(snapshot) and not snapshot.is_relative_to(output))
 for parent in (output.parent,*output.parents):
  info=parent.lstat();need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0))
 output.mkdir(mode=0o700)
 # Partial new output stays retained and cannot be replayed/adopted.
 for name,raw in outputs.items():
  fd=os.open(output/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
  try:
   with os.fdopen(os.dup(fd),'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
  finally:os.close(fd)
  need(safe_read(output/name)==raw)
 if os.name=='posix':
  fd=os.open(output,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  try:os.fsync(fd)
  finally:os.close(fd)
 return {'result':'new_readiness_json_source_artifacts_only','files':{n:sha(b) for n,b in outputs.items()},'directory_durability':'fsynced' if os.name=='posix' else 'not_claimed_windows'}

def main(argv=None):
 import argparse
 try:
  ap=argparse.ArgumentParser();ap.add_argument('--snapshot',required=True);ap.add_argument('--input-register',required=True);ap.add_argument('--approved-input-register-sha256',required=True);ap.add_argument('--installed493-cache',required=True);ap.add_argument('--output',required=True);args=ap.parse_args(argv)
  outputs=prepare(args.snapshot,args.input_register,args.approved_input_register_sha256,args.installed493_cache)
  print(json.dumps(write_outputs(args.output,outputs,args.snapshot),sort_keys=True));return 0
 except BaseException:print('{"result":"readiness_json_source_refused_or_partial_retained","no_replay":true}');return 1
if __name__=='__main__':raise SystemExit(main())
