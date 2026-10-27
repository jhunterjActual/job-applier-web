from portable_bootstrap import install
install()
"""Projected source retrieval tests: public fixed fixtures and one local immutable Git read."""
import json, hashlib, subprocess, shutil, os, unittest
from pathlib import Path
import prepared_types as a
import prepared97_sources as sources
C=Path(__file__).parent

class SourceTests(unittest.TestCase):

    def setup_source(self):
        ops = 'a62e3f1269df9390d075a263385dc024b2623d6a'
        producer = (C / 'public-fixtures/producer-registry97.json').read_bytes()
        registry = json.loads(producer)
        values = {(ops, sources.PREFIX + name): (C / 'public-fixtures/qualified97-source' / name).read_bytes() for name in sources.NAMES}
        values[ops, sources.PREFIX + 'SOURCE_REGISTRY.json'] = producer
        values[ops, sources.SHARED_PATH] = (C / 'public-fixtures/repair98-source/repair_contract.py').read_bytes()
        values[a.TARGET, 'deploy/scripts/deploy-release.sh'] = (C / 'public-fixtures/target97-deploy-release.sh').read_bytes()
        values[a.TARGET, 'backend/test_p0_regressions.py'] = (C / 'public-fixtures/corrected-test_p0_regressions.py.source').read_bytes()
        values[a.TARGET, 'scripts/value_evaluation/test_campaign_transport.py'] = (C / 'public-fixtures/corrected-test_campaign_transport.py.source').read_bytes()
        values[sources.PREPARATION_OPS, sources.PREPARATION_PATH] = (C / 'public-fixtures/prepare_release291d.py').read_bytes()
        manifest = json.dumps({'schema': 'careertrellis.corrected97-source-manifest/v1', 'kind': 'corrected-fixture97-new/v1', 'ops_commit': ops, 'files': registry['files']}).encode()
        return (ops, values, manifest, (C / 'registry.snapshot.json').read_bytes())

    def test_exact_cached_corrected_sources_and_public_ast_inputs(self):
        ops, values, manifest, registry = self.setup_source()
        result = sources.snapshot(ops, manifest, registry, lambda c, p: values[c, p])
        self.assertEqual(len(result), 18)
        self.assertEqual(result['repair98/registry.snapshot.json'], registry)
        self.assertIn('target97/backend/test_p0_regressions.py', result)

    def test_git_changed_second_read_refuses(self):
        ops, values, manifest, registry = self.setup_source()
        count = {}

        def changing(c, p):
            k = (c, p)
            count[k] = count.get(k, 0) + 1
            return values[k] if count[k] == 1 else b'changed'
        with self.assertRaises(a.Refused):
            sources.snapshot(ops, manifest, registry, changing)
