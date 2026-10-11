"""Run isolated offline checks without touching the user's application data."""
import argparse
import compileall
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=ROOT / 'docs/search-quality/verification-20261007.json')
args = parser.parse_args()
output = args.output.resolve()
if output.exists():
    parser.error('Receipt already exists; choose a new --output path')
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts'))
with tempfile.TemporaryDirectory(prefix='ct-search002-') as temporary:
    os.environ.update(CAREERTRELLIS_DATA_DIR=temporary,
                      CAREERTRELLIS_ENVIRONMENT='local', CAREERTRELLIS_DATA_STORE='sqlite')
    os.environ.pop('CAREERTRELLIS_CONFIG_READ_ONLY', None)
    sys.pycache_prefix = str(Path(temporary) / 'bytecode')
    os.chdir(ROOT / 'backend')
    names = ['test_search_quality', 'test_search_role_scope', 'test_search_role_intent',
             'test_search_context_quality', 'test_search_role_sections',
             'test_p0_regressions', 'test_object_store',
             'test_object_import', 'test_stored_objects']
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    syntax = compileall.compile_dir(str(ROOT / 'backend'), quiet=1,
                                   rx=re.compile(r'[/\\]venv[/\\]'))
    syntax = compileall.compile_file(str(ROOT / 'scripts/search_quality.py'), quiet=1) and syntax
    syntax = compileall.compile_file(str(ROOT / 'scripts/search_role_intent.py'), quiet=1) and syntax
    syntax = compileall.compile_file(str(ROOT / 'scripts/search_context_quality.py'), quiet=1) and syntax
    syntax = compileall.compile_file(str(ROOT / 'scripts/search_role_sections.py'), quiet=1) and syntax
    receipt = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   skipped=len(result.skipped), syntax=syntax, passed=result.wasSuccessful() and syntax)
receipt['temporary_data_removed'] = not Path(temporary).exists()
with output.open('x', encoding='utf-8') as stream:
    json.dump(receipt, stream, indent=2)
    stream.write('\n')
print(json.dumps(receipt))
raise SystemExit(0 if receipt['passed'] and receipt['temporary_data_removed'] else 1)
