"""Exact reviewed changed-method source evidence, not an equivalence waiver."""
import ast,hashlib,json
from pathlib import Path
import portable_bootstrap as pb
scope=json.loads((Path(__file__).parent/'PAIRED_CONTRACT03_SCOPE.json').read_bytes())
def actual_method_hash(name):
 raw=(pb.ROOT/'one_shot_readiness/collector.py').read_bytes();lines=raw.splitlines(True)
 cls,sep,method=name.partition('.')
 tree=ast.parse(raw);nodes=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls).body if sep else tree.body
 node=next(n for n in nodes if isinstance(n,ast.FunctionDef) and n.name==(method if sep else name))
 return hashlib.sha256(b''.join(lines[node.lineno-1:node.end_lineno])).hexdigest()

# The original03 scope JSON is immutable history. Only these two current
# query methods take their expected hash from the separately reviewed delta.
query_scope=json.loads((Path(__file__).parent/'SYSTEMCTL_EMPTY_QUERY_SCOPE.json').read_bytes())
def expected_method_hash(name):
 if name in ('Reader.unit','Reader.timer_cadence'):return query_scope['raw_method_after'][name]
 return scope['collector']['raw_methods_after'][name]
