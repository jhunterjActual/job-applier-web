"""Fixed package-local publicsource authentication and test aliases; no host IO."""
import hashlib,importlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
DONE=False
def install():
 global DONE
 if DONE:return
 record=json.loads((ROOT/'PUBLIC_RUNTIME_SOURCE_PINS.json').read_bytes())
 for relative,digest in record['files'].items():
  p=ROOT/relative
  if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise RuntimeError('PORTABLE_RUNTIME_SOURCE_DRIFT')
 sys.path.insert(0,str(ROOT))
 names=['prepared_types','historical98_controls','prepared98_history','prepared97_specs','prepared97_records','prepared97_checks','prepared_reader','prepared_sources','prepared97_reader','prepared97_sources','prepared97_history','prepared97_context','prepared_registries','prepared_evidence','policy_encoding','release_transaction']
 for name in names:importlib.import_module(name)
 aliases={'adapter':'prepared_types','historical':'prepared98_history','qualified_specs':'prepared97_specs','qualified97_records':'prepared97_records','checks97':'prepared97_checks','qualified97_history':'prepared97_history'}
 for old,new in aliases.items():sys.modules[old]=sys.modules[new]
 DONE=True
