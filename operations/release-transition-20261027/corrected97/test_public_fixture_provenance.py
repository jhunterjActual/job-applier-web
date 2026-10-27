import hashlib,json,unittest
from pathlib import Path
class PublicFixtureTests(unittest.TestCase):
 def test_exact_public_git_fixture_register(self):
  root=Path(__file__).parent;record=json.loads((root/'PUBLIC_FIXTURE_PROVENANCE.json').read_text());self.assertEqual(set(record['fixtures']),{'fixtures/test_p0_regressions.py.source','public-fixtures/target-deploy-release.sh'})
  for name,value in record['fixtures'].items():
   self.assertEqual(value['commit'],'bc68ea0dc253425a44e268452130becdf35d764e');self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),value['sha256']);self.assertFalse(name.startswith('/'))
if __name__=='__main__':unittest.main()
