from portable_bootstrap import install,ROOT
install()
import ast,hashlib,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
proposal=Path(__file__).parent
consumer=proposal
sources={p.name:p.read_bytes() for p in ROOT.glob('*.py')}
a=sys.modules['prepared_types']
class OriginalGuardTests(unittest.TestCase):

    def test_only_prepared_method_changes_and_scanner_tail_is_exact(self):
        before = (proposal / 'BASE_BYTES/native_backend.py').read_bytes()
        after = sources['native_backend.py']

        def cls(raw):
            return next((n for n in ast.parse(raw).body if isinstance(n, ast.ClassDef) and n.name == 'Backend'))
        old = cls(before)
        new = cls(after)
        self.assertEqual(len(old.body), len(new.body))
        for lhs, rhs in zip(old.body, new.body):
            if isinstance(lhs, ast.FunctionDef) and lhs.name == 'verify_prepared':
                continue
            self.assertEqual(ast.dump(lhs), ast.dump(rhs))
        marker = b'            # Every venv/cache file is bound'
        self.assertEqual(before.split(marker, 1)[1], after.split(marker, 1)[1])

    def test_actual_full_runtime_guard_rejects_metadata_without_matching_files(self):
        tree = ast.parse(sources['native_backend.py'])
        cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Backend'))
        node = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'verify_prepared'))

        class FakePath:

            def __truediv__(self, name):
                return self

            def rglob(self, name):
                return []
        calls = []

        def proof(*args, **kwargs):
            return {'historical_metadata_verified': True, 'requires_original_current_source_authority_runtime_guards': True, 'runtime_map': {'missing.py': 'hash'}}
        scope = {'TARGET': a.TARGET, 'need': a.need, 'legacy': SimpleNamespace(ROOT=FakePath(), REPOSITORY='/synthetic/repo'), 'os': SimpleNamespace(walk=lambda *args, **kwargs: [])}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'projected original guard', 'exec'), scope)
        host = SimpleNamespace(packet=FakePath(), protected=lambda path: b'{}', source=lambda *args: calls.append('source'), authority=lambda *args: calls.append('authority'))
        plan = {'correction': a.CORRECTION, 'builds': {a.TARGET: '20261026.97'}, 'source_diffs': {a.TARGET: '1' * 64}}
        with patch.dict(sys.modules, {'prepared_evidence': SimpleNamespace(inspect_prepared=proof)}):
            with self.assertRaises(a.Refused):
                scope['verify_prepared'](host, plan)
        self.assertEqual(['source', 'authority'], calls)

    def test_missing_97_registry_refuses_before_protected_io(self):
        plan = {'prepared_receipt_kinds': dict(a.KINDS), 'prepared_receipts': {r: '1' * 64 for r in a.KINDS}}
        reads = []
        with self.assertRaises(a.Refused):
            sys.modules['prepared_evidence'].inspect_prepared(plan, a.TARGET, lambda p: reads.append(p), None, b'')
        self.assertEqual([], reads)

    def test_unallocated_actual_acceptance_plan_refuses_before_io(self):
        reads = []
        for receipt in (None, 'UNALLOCATED', '0' * 64):
            plan = {'prepared_receipt_kinds': dict(a.KINDS), 'prepared_receipts': {a.TARGET: receipt, a.CORRECTION: '1' * 64}}
            with self.assertRaises(a.Refused):
                sys.modules['prepared_evidence'].inspect_prepared(plan, a.TARGET, lambda p: reads.append(p), None, b'98', registry97_raw=b'97')
        self.assertEqual(reads, [])

    def test_registries_are_exact_held_consumer_bytes(self):
        registries = sys.modules['prepared_registries']
        self.assertEqual((consumer / 'registry.snapshot.json').read_bytes(), registries.REGISTRY98)
        self.assertEqual((consumer / 'registry97.snapshot.json').read_bytes(), registries.REGISTRY97)

    def test_dispatcher_passes_complete_historical_result_to_original_guards(self):
        from synthetic97 import Synthetic97
        fixture = Synthetic97()
        arguments = {'bundle': fixture.b, 'binding': fixture.binding, 'source_bytes': fixture.sources, 'accepted98_bundle': fixture.old, 'accepted98_source_bytes': fixture.sources98, 'registry_raw': fixture.registry_raw}
        plan = {'prepared_receipt_kinds': dict(a.KINDS), 'prepared_receipts': {a.TARGET: fixture.binding['receipt_sha256'], a.CORRECTION: fixture.binding['accepted98_receipt_sha256']}}
        with patch.object(sys.modules['prepared97_context'], 'collect', return_value=arguments):
            result = sys.modules['prepared_evidence'].inspect_prepared(plan, a.TARGET, None, None, b'98', registry97_raw=fixture.registry_raw)
        self.assertEqual(result['runtime_map'], fixture.runtime)
        self.assertTrue(result['requires_original_current_source_authority_runtime_guards'])
