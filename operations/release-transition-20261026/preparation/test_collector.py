"""Pure collector controls, no host observation or file inventory invocation."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import collect_existing_provenance as c

def fixture():
    objects={'manifest.json':{'revision':c.BASE},
       'venv-provenance.json':{str(i):'a'*64 for i in range(7947)}}
    manifest=json.dumps(objects['manifest.json']).encode()
    objects['success.json']={'revision':c.BASE,'manifest_sha256':hashlib.sha256(manifest).hexdigest(),
        'result':'inactive_imported','activation_performed':False,
        'destination':str(c.ROOT/'releases'/c.BASE)}
    raw={name:json.dumps(value).encode() for name,value in objects.items()}
    pins={name:hashlib.sha256(value).hexdigest() for name,value in raw.items()}
    return raw,pins

class Tests(unittest.TestCase):
    def test_exact_accepted_fixture(self):
        raw,pins=fixture()
        with patch.object(c,'PINS',pins):self.assertEqual(len(c.verify_accepted(raw)),7947)
    def test_changed_byte_or_missing_receipt_refused(self):
        raw,pins=fixture()
        with patch.object(c,'PINS',pins):
            changed=dict(raw);changed['success.json']+=b' '
            with self.assertRaises(c.Refused):c.verify_accepted(changed)
            changed.pop('success.json')
            with self.assertRaises(c.Refused):c.verify_accepted(changed)
    def test_wrong_baseline_refused_even_with_matching_test_pins(self):
        raw,pins=fixture();value=json.loads(raw['success.json']);value['revision']='f'*40
        raw['success.json']=json.dumps(value).encode();pins['success.json']=hashlib.sha256(raw['success.json']).hexdigest()
        with patch.object(c,'PINS',pins):
            with self.assertRaises(c.Refused):c.verify_accepted(raw)
    def test_duplicate_json_fields_refused(self):
        with self.assertRaises(c.Refused):c.decode(b'{"revision":1,"revision":2}')
    def test_no_mutation_or_subprocess_primitives(self):
        tree=ast.parse(Path(c.__file__).read_text())
        forbidden={'write','write_text','write_bytes','mkdir','unlink','rename','replace','chmod','chown',
                   'system','popen','fork','spawn','execv','socket','connect','bind'}
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                if not (node.func.attr=='system' and isinstance(node.func.value,ast.Name) and node.func.value.id=='platform'):
                    self.assertNotIn(node.func.attr,forbidden)
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module]
                self.assertNotIn('subprocess',names)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='open':
                self.assertTrue(any(isinstance(n,ast.Attribute) and n.attr=='O_RDONLY' for n in ast.walk(node)))

if __name__=='__main__':unittest.main()
