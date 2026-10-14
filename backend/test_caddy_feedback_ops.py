"""Focused ops-only contracts, no host access or provider requests."""
import ast
import copy
from pathlib import Path
import re
import unittest
from caddy_log_privacy import validate_caddy_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'deploy/caddy/Caddyfile.staging'
WRAPPER = ROOT / 'deploy/scripts/check-caddy-access-log-privacy-ops.sh'


class FeedbackIngressTests(unittest.TestCase):
    def test_exact_disjoint_paths_and_privacy_contract(self):
        source = CONFIG.read_text()
        validate_caddy_config(source)
        paths = '/feedback /static/feedback.css /static/feedback.js'
        self.assertIn('@feedbackReferrer path ' + paths, source)
        self.assertIn('not path ' + paths, source)
        self.assertEqual(source.count('>Referrer-Policy "no-referrer"'), 1)
        self.assertEqual(source.count('>Referrer-Policy "same-origin"'), 1)
        self.assertNotIn('\t\tReferrer-Policy "same-origin"', source)
        self.assertIn('log_skip /auth*', source)
        self.assertIn('careertrellis-staging-access-v7.log', source)

    def contract(self):
        source = WRAPPER.read_text().split("<<'PY' 2>/dev/null\n", 1)[1].split('\nPY\n', 1)[0]
        tree = ast.parse(source)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ('metadata', 'require')]
        namespace = {'re': re}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<ops-contract>', 'exec'), namespace)
        return namespace['metadata']

    def manifest(self):
        return {'schema': 'careertrellis.caddy-ops-binding/v1', 'source_commit': 'a'*40, 'runtime_commit': 'b'*40,
                'files': {'deploy/caddy/Caddyfile.staging': 'c'*64,
                          'deploy/scripts/check-caddy-access-log-privacy-ops.sh': 'd'*64,
                          'backend/caddy_log_privacy.py': 'e'*64},
                'runtime_files': {'backend/caddy_log_privacy.py': 'e'*64, 'python_executable': 'f'*64}}

    def test_manifest_exact_revision_and_hash_sets(self):
        validate = self.contract()
        manifest = self.manifest()
        validate(manifest, 'a'*40, 'b'*40)
        for key, value in [('schema', 'old'), ('source_commit', '0'*40), ('runtime_commit', '0'*40)]:
            bad = copy.deepcopy(manifest)
            bad[key] = value
            with self.assertRaises(ValueError): validate(bad, 'a'*40, 'b'*40)
        for field in ('files', 'runtime_files'):
            bad = copy.deepcopy(manifest)
            bad[field]['unexpected'] = 'f'*64
            with self.assertRaises(ValueError): validate(bad, 'a'*40, 'b'*40)
        bad = copy.deepcopy(manifest)
        bad['runtime_files']['backend/caddy_log_privacy.py'] = '0'*64
        with self.assertRaises(ValueError): validate(bad, 'a'*40, 'b'*40)

    def test_full_audit_and_rotation_guards_preserved(self):
        source = WRAPPER.read_text()
        self.assertIn('ops-manifest.json', source)
        self.assertIn('os.O_NOFOLLOW', source)
        self.assertIn("info.st_nlink==1", source)
        self.assertIn("stat.S_IMODE(info.st_mode)==mode", source)
        self.assertIn('arguments=(--config "${config_path}")', source)
        self.assertIn('snapshot_metadata', source)
        self.assertIn('${after_metadata} != "${before_metadata}"', source)
        self.assertIn("'caddy:caddy 600'", source)
        self.assertNotIn('--allow-no-representative', source)
        self.assertNotIn('--privacy-floor-only', source)


if __name__ == '__main__':
    unittest.main()
