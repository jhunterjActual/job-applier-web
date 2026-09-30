"""Exact schema21 selected-plan/migration binding; no database or host effects."""
import unittest
from unittest.mock import patch
import admission_operations as ops
import identity_plan_packet as packet
import identity_plan_store as store
import identity_receipt_transfer as receipts
import session_v2_transition as transition
from test_identity_plan_packet import fixture


class Schema21CoordinationTests(unittest.TestCase):
    def test_initial_pair_policy_and_migration_require_exact_selected_schema(self):
        value=fixture()
        for name in ('bind','switch'): value[name]['activation']['schema']='20260929_0021'
        packet.validate(ops.encoded(value))
        p=value['switch']['activation']
        with patch.object(store,'load',return_value=(b'synthetic',value['switch'])), \
                patch.object(store,'require_current') as current:
            ops.require_migration_plan(p['operation'],p['application'],p['schema'])
            current.assert_called_once_with(b'synthetic')
            for operation,target,head in ((p['operation'],p['application'],'20260915_0016'),
                    (p['operation'],'f'*40,p['schema']),
                    (value['bind']['provider']['operation'],p['application'],p['schema'])):
                with self.assertRaises(ops.OperationError): ops.require_migration_plan(operation,target,head)

    def test_receipt_and_sql_require_exact_21_no_baseline_broadening(self):
        p=fixture()['switch']['activation']; p['schema']='20260929_0021'
        expected=receipts.contract(p,'migrated')[2]
        with patch.object(ops,'read_protected',return_value=ops.encoded(expected)):
            transition.migration_receipt(p)
        with patch.object(ops,'read_protected',return_value=ops.encoded({**expected,'head':'20260929_0020'})):
            with self.assertRaises(ops.OperationError): transition.migration_receipt(p)
        self.assertIn("'20260929_0021'",transition.assert_identity_sql(p))
        self.assertEqual('20260907_0014',ops.INITIAL_HEAD)
