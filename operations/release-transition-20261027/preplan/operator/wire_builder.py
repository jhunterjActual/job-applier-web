"""Pure public-source capsule composer, original4MiB/1MiB compression limits."""
import ast,base64,hashlib,json,re,types
from pathlib import Path
from datetime import datetime,timezone
COMPRESS_SHA='55529cae4dc17514c0016351c55409288138488d0047f67e9a519acf531e6695'
def pure_api(raw,approved_sha):
 if not(type(raw)is bytes and 0<len(raw)<=131072 and type(approved_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_sha) and hashlib.sha256(raw).hexdigest()==approved_sha):raise ValueError('CARRIER_SOURCE_REFUSED')
 tree=ast.parse(raw);functions={'need','sha','pairs','validate_cache','selector','stage_binding','validate_operation','closed_plan'};constants={'ORDER','PUBLIC_PATHS','FILES','HOSTS','PLAN_KEYS'};nodes=[]
 for n in tree.body:
  if isinstance(n,ast.FunctionDef) and n.name in functions or isinstance(n,ast.ClassDef) and n.name=='Refused' or isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id in constants:nodes.append(n)
 ns={'hashlib':hashlib,'json':json,'re':re,'datetime':datetime,'timezone':timezone};exec(compile(ast.Module(body=nodes,type_ignores=[]),'verified-pure-public-carrier-api','exec'),ns);return ns

def build(carrier_raw,approved_carrier_sha,role,mode,expected_boot,manifest_raw,approved_manifest_sha,caps,binding_raw,approved_binding_sha,approved_intent_sha=None,operation=None):
 ns=pure_api(carrier_raw,approved_carrier_sha);tag=ns['selector'](role,mode,approved_manifest_sha)
 ns['need'](type(expected_boot)is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',expected_boot))
 ns['need'](type(approved_intent_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_intent_sha) if mode not in ('preflight','stage') else approved_intent_sha is None)
 manifest,cache=ns['validate_cache'](manifest_raw,approved_manifest_sha,caps)
 source_binding=ns['stage_binding'](binding_raw,approved_binding_sha,manifest['runtime'],role,expected_boot)
 operation=ns['validate_operation'](mode,operation)
 if mode in ('stage-packet','stage-plan'):
  fresh=ns['stage_binding'](operation['fresh_binding_raw'],operation['fresh_binding_sha256'],manifest['runtime'],role,expected_boot)
  ns['need'](operation['fresh_binding_sha256']!=approved_binding_sha and fresh['nonce']!=source_binding['nonce'])
 encoded_operation=None if operation is None else {key:{'kind':'bytes' if type(value)is bytes else 'str','value':base64.b64encode(value).decode('ascii') if type(value)is bytes else value} for key,value in operation.items()}
 capsule={'operation':encoded_operation,'binding':base64.b64encode(binding_raw).decode('ascii'),'manifest':base64.b64encode(manifest_raw).decode('ascii'),'caps':{name:base64.b64encode(raw).decode('ascii') for name,raw in cache.items()}}
 payload=json.dumps(capsule,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii');payload_sha=hashlib.sha256(payload).hexdigest()
 # All replacements are builtin validated enums/hex or freshly encoded ASCII.
 text="import base64,hashlib,json,os,socket,sys\ntry:\n if not(sys.platform=='linux' and os.geteuid()==0 and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode and socket.gethostname()=="+repr(ns['HOSTS'][role])+" and sys.argv==['-',"+repr(tag)+"]):raise RuntimeError()\n source=base64.b64decode("+repr(base64.b64encode(carrier_raw).decode('ascii'))+",validate=True)\n payload=base64.b64decode("+repr(base64.b64encode(payload).decode('ascii'))+",validate=True)\n if not(len(source)=="+str(len(carrier_raw))+" and hashlib.sha256(source).hexdigest()=="+repr(approved_carrier_sha)+" and len(payload)=="+str(len(payload))+" and hashlib.sha256(payload).hexdigest()=="+repr(payload_sha)+"):raise RuntimeError()\n namespace={'__name__':'verified_public_source_carrier'}\n exec(compile(source,'verified-public-source-carrier','exec'),namespace)\n capsule=json.loads(payload)\n caps={name:base64.b64decode(raw,validate=True) for name,raw in capsule['caps'].items()}\n operation=None if capsule['operation'] is None else {key:base64.b64decode(value['value'],validate=True) if value['kind']=='bytes' else value['value'] for key,value in capsule['operation'].items()}\n result=namespace['main']("+','.join(repr(value) for value in (role,mode,expected_boot,approved_manifest_sha))+",base64.b64decode(capsule['manifest'],validate=True),caps,base64.b64decode(capsule['binding'],validate=True),"+repr(approved_binding_sha)+","+repr(approved_intent_sha)+",operation=operation)\n print(json.dumps(result,sort_keys=True))\nexcept BaseException:\n print('{\"result\":\"public_source_carrier_refused_or_unknown\",\"no_replay\":true}');raise SystemExit(1)\n"
 raw=text.encode('ascii');reference=(Path(__file__).parent/'compress_wire.reference.py').read_bytes()
 if hashlib.sha256(reference).hexdigest()!=COMPRESS_SHA:raise ValueError('COMPRESSION_SOURCE_REFUSED')
 tree=ast.parse(reference);node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='compressed_wire');space={'base64':base64,'hashlib':hashlib,'re':re,'zlib':__import__('zlib')};exec(compile(ast.Module(body=[node],type_ignores=[]),'verified-compression-function','exec'),space)
 wire=space['compressed_wire'](raw,hashlib.sha256(raw).hexdigest());return {'wire':wire,'selector':tag,'wire_sha256':hashlib.sha256(wire).hexdigest(),'wire_bytes':len(wire),'expanded_source_bytes':len(raw),'manifest_sha256':approved_manifest_sha}
