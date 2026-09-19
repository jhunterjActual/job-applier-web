"""Strict operator plans; actual database coordination covered in PG tests."""
import unittest
from uuid import uuid4

import admission_operations as ops
import identity_configuration as configuration
import session_v2_transition as transition


def plan(binding='a'*64,owner='user_synthetic_owner'):
    op=str(uuid4())
    return {'version':1,'operation':op,'mode':'bind_current','source_application':'a'*40,
        'application':'b'*40,'operations':'c'*40,'baseline_binding':binding,'target_binding':binding,
        'expected_generation':0,'owner_proof':transition.owner_proof(op,binding,owner),
        'transitions':[],'quarantined_accounts':[],'authorized':True}


class ConfigurationPlanTests(unittest.TestCase):
    def test_initial_binding_cannot_be_a_different_provider(self):
        p=plan(); configuration.validate(ops.encoded(p))
        p['target_binding']='b'*64
        with self.assertRaises(ops.OperationError): configuration.validate(ops.encoded(p))

    def test_missing_authority_unknown_fields_duplicates_and_bad_types_rejected(self):
        for changes in ({'authorized':False},{'expected_generation':True},{'version':True},
                {'application':'main'},{'credentials':'synthetic'},{'quarantined_accounts':[True]},
                {'mode':'switch_environment'},{'transitions':[str(uuid4())]}):
            with self.assertRaises(Exception): configuration.validate(ops.encoded({**plan(),**changes}))
        with self.assertRaises(Exception): configuration.validate(b'{"version":1,"version":1}')
