import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import value_evidence as evidence


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="value-evidence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inventory = {"schema": "synthetic-evidence-inventory-v1", "operations": []}

    def add(self, case="V01", outcome="success", actual="0.01", dispatches=1, output=True):
        call = f"CT-{case}-fit-a1"
        folder = self.root / call
        folder.mkdir()
        receipt = dict(call_id=call, generation_dispatches=dispatches, outcome=outcome,
                       actual_usd=actual, reservation_usd="0.2" if dispatches else None)
        if output:
            raw = b'{"synthetic":"not included in audit"}'
            (folder / "product-output.json").write_bytes(raw)
            receipt["product_output_sha256"] = hashlib.sha256(raw).hexdigest()
        raw = json.dumps(receipt).encode()
        (folder / "receipt.json").write_bytes(raw)
        self.inventory["operations"].append(dict(call_id=call, receipt=f"{call}/receipt.json",
                                                sha256=hashlib.sha256(raw).hexdigest()))
        return folder

    def audit(self): return evidence.audit(self.root, self.inventory)

    def test_success_hashes_and_no_text_or_mutation(self):
        self.add()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.audit()
        self.assertEqual(result["counts"]["product_success"], 1)
        self.assertEqual(result["complete_actual_cost_usd"], "0.01")
        self.assertNotIn("not included in audit", json.dumps(result))
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_unknown_cost_success_distinguished_from_reservation(self):
        self.add(outcome="paused_pending_reconciliation", actual=None)
        result = self.audit()
        self.assertEqual(result["counts"]["product_success"], 1)
        self.assertEqual(result["counts"]["unresolved"], 1)
        self.assertEqual(result["unresolved_reservation_usd"], "0.2")
        self.assertIsNone(result["complete_actual_cost_usd"])

    def test_blocked_and_attempted_failure_denominators(self):
        self.add(outcome="blocked_before_dispatch", actual=None, dispatches=0, output=False)
        self.add(case="V02", outcome="attempted_failure", output=False)
        result = self.audit()["counts"]
        self.assertEqual((result["declared"], result["blocked"], result["attempted"], result["attempted_failure"]), (2, 1, 1, 1))

    def test_missing_is_not_attempted(self):
        folder = self.add()
        (folder / "receipt.json").unlink()
        result = self.audit()
        self.assertEqual(result["counts"]["missing"], 1)
        self.assertEqual(result["counts"]["attempted"], 0)
        self.assertIsNone(result["complete_actual_cost_usd"])

    def test_tampered_output_not_counted_success(self):
        folder = self.add()
        (folder / "product-output.json").write_text("changed")
        result = self.audit()["counts"]
        self.assertEqual(result["invalid"], 1)
        self.assertEqual(result["product_success"], 0)

    def test_tampered_receipt(self):
        folder = self.add()
        (folder / "receipt.json").write_text("{}")
        self.assertEqual(self.audit()["operations"][0]["reason"], "receipt_hash_mismatch")

    def test_duplicate_declaration(self):
        self.add()
        self.inventory["operations"] *= 2
        with self.assertRaises(evidence.EvidenceError): self.audit()

    def test_paths_refuse_escape_absolute_stream_and_backslash(self):
        self.add()
        for value in ("../receipt.json", "/tmp/file", "C:/file", "safe\\file", "file:stream", ["bad"]):
            self.inventory["operations"][0]["receipt"] = value
            with self.subTest(value=value), self.assertRaises(evidence.EvidenceError): self.audit()

    def test_corrupt_types_and_nonfinite_json(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{', b'[' * 20000 + b']' * 20000):
            with self.assertRaises(evidence.EvidenceError): evidence.decode(raw)
        for value in (True, 1, "NaN", "-1", "0.0000001", "1e2"):
            with self.assertRaises(evidence.EvidenceError): evidence.amount(value)

    def test_inconsistent_reservation_cannot_disappear_from_totals(self):
        folder = self.add(outcome="blocked_before_dispatch", actual=None, dispatches=0, output=False)
        path = folder / "receipt.json"
        receipt = json.loads(path.read_bytes())
        for dispatches, outcome, reservation in ((0, "blocked_before_dispatch", "0.2"), (1, "attempted_failure", "0")):
            receipt.update(generation_dispatches=dispatches, outcome=outcome, reservation_usd=reservation)
            raw = json.dumps(receipt).encode()
            path.write_bytes(raw)
            self.inventory["operations"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
            result = self.audit()
            self.assertEqual(result["counts"]["invalid"], 1)
            self.assertIsNone(result["complete_actual_cost_usd"])

    def test_oversized_and_directory_receipts_are_invalid(self):
        folder = self.add()
        path = folder / "receipt.json"
        path.write_bytes(b"x" * (evidence.MAX_BYTES + 1))
        self.assertEqual(self.audit()["counts"]["invalid"], 1)
        path.unlink()
        path.mkdir()
        self.assertEqual(self.audit()["counts"]["invalid"], 1)


if __name__ == "__main__": unittest.main()

