from portable_bootstrap import install
install()
import unittest
from synthetic97 import Synthetic97
class ConsumerTests(unittest.TestCase):
 def test_expired_complete_synthetic_history_positive(self):
  f=Synthetic97();value=f.inspect();self.assertEqual(value['runtime_map'],f.runtime);self.assertFalse(value['historical']['authorizes_preparation'])
if __name__=='__main__':unittest.main()
