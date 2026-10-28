"""Source-only snapshot equality gate. Actual collection and admission are held."""
import ast,hashlib,io,json,re,tarfile
from pathlib import PurePosixPath
READY=False
TARGET='bc68ea0dc253425a44e268452130becdf35d764e';CORRECTION='f70417d4bd70c3eb086ea63c90938dd2f43e0b99'
REVISIONS=(TARGET,CORRECTION)
class Refused(ValueError):pass
def need(v):
 if not v:raise Refused('RUNTIME_EQUALITY_REFUSED')
def sha(b):return hashlib.sha256(b).hexdigest()
def text(v):need(type(v)is str);return v
def digest(v):need(type(v)is str and re.fullmatch('[0-9a-f]{64}',v)is not None);return v
def mapping(v):
 need(type(v)is dict and all(type(k)is str for k in v));return v
def relative(v):
 text(v);p=PurePosixPath(v);need(v and not p.is_absolute() and '..' not in p.parts and str(p)==v and '\\' not in v);return v
def rows(value):
 mapping(value);need(value)
 for path,row in value.items():
  relative(path);mapping(row);need(set(row)=={'kind','mode','uid','gid','nlink','size','sha256'})
  need(type(row['kind'])is str and row['kind']in ('file','directory'))
  need(all(type(row[k])is int and row[k]>=0 for k in ('mode','uid','gid','nlink','size')))
  need(row['uid']==0 and row['mode']<=0o777 and not row['mode']&0o022)
  if row['kind']=='file':need(row['nlink']==1 and row['size']<=512*1024**2);digest(row['sha256'])
  else:need(row['sha256']is None and row['nlink']>=1)
 return value
def archive_map(raw,pin,build):
 need(type(raw)is bytes and len(raw)<=128*1024**2 and sha(raw)==digest(pin));result={}
 with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
  for entry in archive:
   path=relative(entry.name.rstrip('/') if entry.isdir() else entry.name)
   if entry.isdir():continue
   need(entry.isfile() and path not in result and entry.size<=128*1024**2)
   body=archive.extractfile(entry).read();need(len(body)==entry.size);result[path]=sha(body)
   if path=='backend/version.py':need(re.search(r"(?m)^APP_BUILD\s*=\s*['\"]"+re.escape(build)+r"['\"]",body.decode('utf-8'))is not None)
 need('backend/version.py'in result);return result
def validate(context,historical_runtime_maps,approved_inventory,observed_before,observed_after,archives,source_deltas):
 # Each input inventory must come from an authenticated complete collector, not callers' booleans.
 for value in (context,historical_runtime_maps,approved_inventory,observed_before,observed_after,archives,source_deltas):mapping(value);need(set(value)==set(REVISIONS))
 for rev in REVISIONS:
  c=mapping(context[rev]);need(set(c)=={'build','archive_sha256','source_diff_sha256','historical_receipt_sha256','inventory_receipt_sha256'})
  text(c['build']);need(c['build']==('20261026.97'if rev==TARGET else '20261026.98'))
  for key in ('archive_sha256','source_diff_sha256','historical_receipt_sha256','inventory_receipt_sha256'):digest(c[key])
  need(type(source_deltas[rev])is bytes and sha(source_deltas[rev])==c['source_diff_sha256'])
  sections=[]
  for packet in (approved_inventory[rev],observed_before[rev],observed_after[rev]):
   mapping(packet);need(set(packet)=={'release','browser','interpreter'})
   sections.append({k:rows(packet[k]) for k in packet})
  expected,before,after=sections;need(expected==before==after)
  runtime=mapping(historical_runtime_maps[rev]);need(runtime)
  for path,pin in runtime.items():relative(path);digest(pin)
  release=before['release'];actual_runtime={path:row['sha256'] for path,row in release.items() if row['kind']=='file' and (path.startswith('backend/venv/') or '__pycache__'in PurePosixPath(path).parts)}
  need(actual_runtime==runtime and any(p.startswith('backend/venv/')for p in runtime))
  tracked=archive_map(archives[rev],c['archive_sha256'],c['build'])
  actual_source={path:row['sha256'] for path,row in release.items()if row['kind']=='file' and path not in actual_runtime}
  need(actual_source==tracked)
 return {'schema':'careertrellis.preplan-runtime-equality-result/v1','runtime_equality_verified':True,'ready_for_plan':False,'collector_enabled':False,'requires_authenticated_complete_inventory':True,'unresolved':('actual_native_protected_collector','archive_git_commit_and_migration_guard','bootstrap_authority_locks_and_custody','private_inventory_authentication')}
def reviewed_scanner_ast(native_raw,source_raw):
 # No privileged module import/exec. Return exact authenticated method ASTs for future collector binding.
 need(type(native_raw)is bytes and sha(native_raw)=='5c86f98fe6059330d5025a7d0041015a44fa406963cac8b137fe1e6c0a1b4c03')
 need(type(source_raw)is bytes and sha(source_raw)=='778c9f1dc8e4c3afe38621adbeb7e819ecefed92bbde20cb1fdb671adc57968a')
 result={}
 for raw,owner,name in ((native_raw,'Backend','runtime_digest'),(source_raw,'LinuxHost','source')):
  cls=next(n for n in ast.parse(raw).body if isinstance(n,ast.ClassDef) and n.name==owner)
  result[name]=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
 return result
def main():return 1
if __name__=='__main__':raise SystemExit(main())
