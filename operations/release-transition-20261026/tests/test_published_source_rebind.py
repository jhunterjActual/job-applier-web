from portable_bootstrap import install
install()
import copy,hashlib,json,unittest
from pathlib import Path
import qualified97_history as q
from synthetic97 import Synthetic97,encoded,sha
class PublishedSourceRebindTests(unittest.TestCase):
 def test_actual_published_ops_registry_and13_rawsources(self):
  root=Path(__file__).parent;raw=(root/'public-fixtures/producer-registry97.json').read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),q.PUBLISHED_PRODUCER_REGISTRY_SHA);self.assertEqual(q.APPROVED_OPS,'a62e3f1269df9390d075a263385dc024b2623d6a')
  producer=json.loads(raw);consumer=json.loads((root/'registry97.snapshot.json').read_bytes());self.assertEqual(consumer['source_files'],producer['files']);self.assertTrue(producer['entrypoints_enabled'])
  provenance=json.loads((root/'PUBLISHED_PUBLIC_SOURCE_PROVENANCE.json').read_bytes());self.assertEqual(len(provenance['files']),14)
  for path,info in provenance['files'].items():
   self.assertEqual(info['commit'],q.APPROVED_OPS);self.assertTrue(info['git_path'].startswith(q.PREFIX));self.assertEqual(hashlib.sha256((root/path).read_bytes()).hexdigest(),info['sha256'])
 def test_expired_synthetic_graph_uses_real_public_ops_not_real_receipts(self):
  f=Synthetic97();value=f.inspect();self.assertEqual(f.binding['ops_commit'],q.APPROVED_OPS);self.assertFalse(value['historical']['authorizes_preparation'])
 def test_wrong_approved_registry_consistently_rehashed_refused(self):
  f=Synthetic97();r=q.decode(f.registry_raw);r['source_files']['qualified_route.py']['sha256']='9'*64;raw=encoded(r);binding=copy.deepcopy(f.binding);binding['registry_sha256']=sha(raw)
  with self.assertRaises(q.Refused):q.inspect_history(f.b,binding,f.sources,f.old,f.sources98,raw)
if __name__=='__main__':unittest.main()
