"""Source-only runner preservation and instrumentation contract checks."""
import ast
import hashlib
from pathlib import Path
import unittest

class RunnerContractTests(unittest.TestCase):
    def test_all_existing_isolation_native_and_module_controls_unchanged(self):
        old=fixture_bytes('old-run-checks.py')
        self.assertEqual(hashlib.sha256(old).hexdigest(),'a812fea21f0908f36fdf9249f98b1a7a7c9b6af12881b63779325cfa17f2a97d')
        original=ast.parse(old);new=ast.parse(Path('run_checks_repair98.py').read_bytes())
        for name in ('verify_opened_readonly','opened_mount_id','readonly_mount_row','require_readonly_bind','modules_for','startup','isolated_env'):
            a=next(n for n in original.body if isinstance(n,ast.FunctionDef) and n.name==name)
            b=next(n for n in new.body if isinstance(n,ast.FunctionDef) and n.name==name)
            with self.subTest(name=name):self.assertEqual(ast.dump(a,include_attributes=False),ast.dump(b,include_attributes=False))
    def test_new_only_domain_qualification_and_fixed_instrumentation(self):
        source=Path('run_checks_repair98.py').read_text()
        self.assertIn("path=work/'repair-checks.json'",source)
        self.assertIn("'schema':'careertrellis.preparation98-repair-checks/v1'",source)
        self.assertIn("QUALIFIER['typed_qualification'](qualification,result,RecordedResult,RECORDED_SUCCESS_CODE)",source)
        self.assertIn('need(a.revision==CORRECTION',source)
        self.assertIn("'stage':REPAIR_STAGE",source);self.assertIn("'exception_class':kind if kind in allowed else 'OTHER'",source)
        self.assertNotIn('str(error)',source)
        self.assertNotIn("path=work/'checks.json'",source)

if __name__=='__main__':unittest.main()


def fixture_bytes(name):
    import hashlib,json
    root=Path(__file__).resolve().parent/'public-fixtures'
    register=json.loads((root/'HASH_REGISTER.json').read_text())
    raw=(root/name).read_bytes();entry=register[name]
    assert len(raw)==entry['bytes'] and hashlib.sha256(raw).hexdigest()==entry['sha256']
    return raw
