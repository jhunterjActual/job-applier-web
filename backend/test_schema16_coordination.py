"""Exact schema and receipt boundaries; synthetic values only."""
import unittest
from unittest.mock import patch

import admission_operations as ops
import identity_plan_packet as packets
import identity_receipt_transfer as receipts
import session_v2_transition as transition
from test_identity_plan_packet import fixture


class SchemaCoordinationTests(unittest.TestCase):
    def test_migration_package_head_must_match_selected_plan(self):
        import identity_plan_store as store
        p=fixture()['switch']['activation']; p['schema']='20260915_0016'
        with patch.object(store,'load',return_value=(b'synthetic',{'activation':p})), \
                patch.object(store,'require_current') as current:
            ops.require_migration_plan(p['operation'],p['application'],p['schema'])
            current.assert_called_once_with(b'synthetic')
            for operation,target,head in ((p['operation'],p['application'],transition.HEAD),
                    (p['operation'],'f'*40,p['schema'])):
                with self.assertRaises(ops.OperationError): ops.require_migration_plan(operation,target,head)

    def test_packet_requires_same_schema_and_rejects_unknown(self):
        value=fixture()
        value['switch']['activation']['schema']='20260915_0016'
        with self.assertRaises(ops.OperationError): packets.validate(ops.encoded(value))
        value['bind']['activation']['schema']='20260915_0016'
        packets.validate(ops.encoded(value))
        value['switch']['activation']['schema']='20260916_0017'
        with self.assertRaises(ops.OperationError): packets.validate(ops.encoded(value))

    def test_migration_receipt_and_sql_bind_exact_selected_schema(self):
        p=fixture()['switch']['activation']; p['schema']='20260915_0016'
        expected=receipts.contract(p,'migrated')[2]
        self.assertEqual(p['schema'],expected['head'])
        with patch.object(ops,'read_protected',return_value=ops.encoded(expected)):
            transition.migration_receipt(p)
        with patch.object(ops,'read_protected',return_value=ops.encoded({**expected,'head':transition.HEAD})):
            with self.assertRaises(ops.OperationError): transition.migration_receipt(p)
        self.assertIn("'20260915_0016'",transition.assert_identity_sql(p))
