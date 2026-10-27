import json
import unittest
import extract_v5_controls as e

def sample():
    lines=[json.dumps({'schema':'careertrellis.sandbox-stub-diagnostic/v1','stage':stage,'outcome':'entered'}) for stage in e.STAGES]
    lines.append(json.dumps(dict.fromkeys(e.CONTROLS,True)))
    return ('\n'.join(lines)+'\n').encode()

class ExtractionTests(unittest.TestCase):
    def test_exact_closed_controls(self):self.assertEqual(set(e.parse(sample())[0]),e.CONTROLS)
    def test_reject_duplicate_control_key(self):
        with self.assertRaises(ValueError):e.parse(sample().replace(b'"identity": true',b'"identity": true, "identity": true'))
    def test_reject_second_control_missing_stage_and_extra_data(self):
        for raw in [sample()+json.dumps(dict.fromkeys(e.CONTROLS,True)).encode(),
                    b'\n'.join(sample().splitlines()[1:]),sample()+b'{}',sample()+b'\xff',b'x'*32769]:
            with self.subTest(rawlen=len(raw)),self.assertRaises((ValueError,UnicodeError)):e.parse(raw)

if __name__=='__main__':unittest.main()
