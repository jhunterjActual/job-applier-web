"""Portable public-source fixture provenance; never real runtime inventories."""
from pathlib import Path
import hashlib,json,unittest
class PublicFixtureProvenanceTests(unittest.TestCase):
 def test_eight_fixtures_match_exact_public_git_blobs(self):
  root=Path(__file__).resolve().parent/'public-fixtures'
  provenance=json.loads((root/'PUBLIC_GIT_PROVENANCE.json').read_text());register=json.loads((root/'HASH_REGISTER.json').read_text())
  self.assertEqual(set(provenance),set(register));self.assertEqual(len(provenance),8)
  self.assertFalse((root/'baseline-provenance.json').exists())
  for name,item in provenance.items():
   with self.subTest(fixture=name):
    raw=(root/item['fixture_relative_path']).read_bytes();blob=b'blob '+str(len(raw)).encode()+b'\0'+raw
    self.assertEqual(hashlib.sha1(blob).hexdigest(),item['git_blob_sha1']);self.assertEqual(hashlib.sha256(raw).hexdigest(),item['sha256'])
    self.assertEqual(register[name],{'bytes':len(raw),'sha256':item['sha256']});self.assertEqual(item['repository'],'web/job-applier-web')
    if name=='prepare_release291d.py':
     self.assertEqual(item['commit'],'291d1b4b58bb17448395012408556090bea5ede1');self.assertEqual(item['path'],'operations/release-transition-20261026/preparation/prepare_release.py')
    else:
     self.assertEqual(item['commit'],'ae4e5171b9f0c874721cce8db5170a5815d59e8e');self.assertEqual(item['path'],'operations/release-transition-20261027/repair98/'+name)
if __name__=='__main__':unittest.main()
