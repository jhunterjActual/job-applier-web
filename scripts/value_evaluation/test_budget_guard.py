import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from budget_guard import BudgetRefused, Ledger, RunConfig


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ct-value-budget-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "ledger.json"
        self.config = RunConfig("synthetic", "a" * 64, "25", "2",
                                "verified_upper_bound", "synthetic-approval")
        self.ledger = Ledger.create(self.path, self.config)

    def reserve(self, call="call", packet="packet", amount="1"):
        self.ledger.reserve(call, packet, amount, "synthetic-test-bound")

    def test_exact_packet_limit_and_multiple_calls(self):
        self.reserve()
        self.reserve("second")
        with self.assertRaises(BudgetRefused):
            self.reserve("third", amount="0.000001")

    def test_total_across_packets_and_retry_attempts(self):
        for i in range(12):
            self.reserve(f"call{i}", f"packet{i}", "2")
        self.reserve("last", "last", "1")
        with self.assertRaises(BudgetRefused):
            self.reserve("over", "new_attempt", "0.01")

    def test_unknown_outcome_retains_full_reservation(self):
        self.reserve(amount="2")
        with self.assertRaises(BudgetRefused):
            Ledger(self.path, self.config).reserve("retry", "packet", "0.01", "test")

    def test_duplicate_call_cannot_dispatch(self):
        self.reserve()
        with self.assertRaises(BudgetRefused):
            self.reserve()

    def test_actual_lower_than_reserved_releases_difference(self):
        self.reserve(amount="2")
        self.ledger.settle("call", "0.25")
        self.reserve("second", amount="1.75")
        with self.assertRaises(BudgetRefused):
            self.reserve("third", amount="0.01")

    def test_underestimated_bound_halts_and_retains_charge(self):
        self.reserve()
        with self.assertRaises(BudgetRefused):
            self.ledger.settle("call", "1.5")
        state = json.loads(self.path.read_text())
        self.assertTrue(state["halted"])
        self.assertEqual(state["calls"]["call"]["actual_usd"], "1.5")
        with self.assertRaises(BudgetRefused):
            self.reserve("second", "different")

    def test_invalid_numbers_and_missing_evidence(self):
        for value in ("NaN", "Infinity", "-1", 1.0, "0", "garbage", "", "1e-40"):
            with self.subTest(value=value), self.assertRaises(BudgetRefused):
                self.reserve(amount=value)
        with self.assertRaises(BudgetRefused):
            self.ledger.reserve("call", "packet", "1", "")

    def test_sub_microdollar_charge_cannot_round_under_ceiling(self):
        self.reserve(amount="2")
        for value in ("1e-40", "0.0000001", "1000000000"):
            with self.subTest(value=value), self.assertRaises(BudgetRefused):
                self.reserve("extra", amount=value)

    def test_existing_lock_is_preserved(self):
        lock = self.path.with_name(self.path.name + ".lock")
        lock.write_text("unresolved attempt")
        with self.assertRaises(BudgetRefused):
            self.reserve()
        self.assertEqual(lock.read_text(), "unresolved attempt")

    def test_unreserved_and_duplicate_settlement_refused(self):
        with self.assertRaises(BudgetRefused):
            self.ledger.settle("unknown", "0")
        self.reserve()
        self.ledger.settle("call", "0.1")
        with self.assertRaises(BudgetRefused):
            self.ledger.settle("call", "0")

    def test_missing_ledger_never_reinitializes(self):
        self.reserve()
        retained = self.path.with_name("retained.json")
        self.path.rename(retained)
        with self.assertRaises(BudgetRefused):
            Ledger(self.path, self.config).reserve("new", "packet", "1", "test")
        self.assertFalse(self.path.exists())
        self.assertIn("call", json.loads(retained.read_text())["calls"])

    def test_create_is_exclusive_and_wrong_run_refused(self):
        before = self.path.read_bytes()
        with self.assertRaises(BudgetRefused):
            Ledger.create(self.path, self.config)
        with self.assertRaises(BudgetRefused):
            Ledger(self.path, replace(self.config, run_id="another")).reserve("x", "p", "1", "test")
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_state_is_preserved(self):
        self.reserve()
        baseline = self.path.read_bytes()
        cases = [b"{", b'{"version":2,"version":2}', b"[]"]
        for field, value in (("halted", "false"), ("version", True), ("extra", "unrecognized")):
            state = json.loads(baseline)
            state[field] = value
            cases.append(json.dumps(state).encode())
        for field, value in (("reserved_usd", "-1"), ("reserved_usd", "NaN"),
                             ("reserved_usd", "0"), ("actual_usd", "0"),
                             ("status", "settled"), ("packet_attempt", None)):
            state = json.loads(baseline)
            state["calls"]["call"][field] = value
            cases.append(json.dumps(state).encode())
        for raw in cases:
            with self.subTest(raw=raw[:80]):
                self.path.write_bytes(raw)
                with self.assertRaises(BudgetRefused):
                    self.reserve("second")
                self.assertEqual(self.path.read_bytes(), raw)

    def test_exposure_over_limit_without_halt_refused(self):
        self.reserve()
        state = json.loads(self.path.read_text())
        state["calls"]["call"]["reserved_usd"] = "3"
        self.path.write_text(json.dumps(state))
        with self.assertRaises(BudgetRefused):
            self.reserve("second")

    def test_lock_owner_and_second_writer_refusal(self):
        with self.ledger.transaction():
            lock = self.path.with_name(self.path.name + ".lock")
            owner = json.loads(lock.read_text())
            self.assertEqual(owner["run_id"], self.config.run_id)
            self.assertGreater(owner["pid"], 0)
            with self.assertRaises(BudgetRefused):
                Ledger(self.path, self.config).reserve("other", "packet", "1", "test")
            self.assertEqual(json.loads(lock.read_text()), owner)
        self.reserve()

    def test_replace_failure_retains_previous_ledger(self):
        self.reserve()
        before = self.path.read_bytes()
        with patch("budget_guard.os.replace", side_effect=OSError("synthetic replace failure")):
            with self.assertRaises(OSError):
                self.ledger.settle("call", "0")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_fsync_failure_does_not_reserve_or_reset(self):
        before = self.path.read_bytes()
        with patch("budget_guard.os.fsync", side_effect=OSError("synthetic sync failure")):
            with self.assertRaises(OSError):
                self.reserve()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_explicit_estimate_mode_retained(self):
        config = replace(self.config, accounting_basis="approved_conservative_estimate")
        path = self.path.with_name("estimate.json")
        ledger = Ledger.create(path, config)
        ledger.reserve("estimate", "packet", "1.25", "reviewed-price-card")
        self.assertEqual(json.loads(path.read_text())["config"]["accounting_basis"],
                         "approved_conservative_estimate")


if __name__ == "__main__":
    unittest.main()
