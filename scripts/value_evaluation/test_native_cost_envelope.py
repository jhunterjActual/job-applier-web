import copy
from decimal import Decimal, ROUND_DOWN, localcontext
import unittest

import native_cost_envelope as subject


class NativeCostTests(unittest.TestCase):
    def setUp(self):
        self.inputs = {row[0]: 100000 for row in subject.OPERATIONS}
        self.pricing = {"model": subject.MODEL, "input_usd_per_million": "2",
            "output_usd_per_million": "10", "source": "https://platform.claude.com/docs/en/about-claude/pricing",
            "verified_utc": "2026-10-24T00:00:00Z"}

    def estimate(self, cap="100", contingency="0"):
        return subject.estimate(self.inputs, self.pricing, cap, contingency)

    def test_exact_native_limits_and_complete_denominators(self):
        result = self.estimate()
        self.assertEqual(192, result["max_generation_requests"])
        self.assertEqual(5922816, result["max_requested_output_tokens"])
        self.assertEqual("172.001280", result["batch_reservation_usd"])
        self.assertEqual("72.001280", result["reservation_gap_usd"])
        self.assertFalse(result["arithmetic_within_supplied_cap"])
        self.assertFalse(result["future_input_bounds_verified"])
        self.assertFalse(result["funding_or_dispatch_authorized"])
        rows = {(r["product_id"], r["stage"]): r for r in result["stage_rows"]}
        self.assertEqual((12, 1), (rows[("CT", "fit")]["count"], rows[("CT", "fit")]["max_requests_each"]))
        self.assertEqual((6, 2), (rows[("CT", "materials")]["count"], rows[("CT", "materials")]["max_requests_each"]))
        self.assertEqual((12, 4), (rows[("UP", "fit")]["count"], rows[("UP", "fit")]["max_requests_each"]))
        self.assertEqual((6, 20), (rows[("UP", "materials")]["count"], rows[("UP", "materials")]["max_requests_each"]))

    def test_zero_input_lower_floor_still_reserves_native_output_and_margin(self):
        self.inputs = dict.fromkeys(self.inputs, 0)
        result = self.estimate()
        self.assertEqual("76.001280", result["batch_reservation_usd"])
        self.assertTrue(result["arithmetic_within_supplied_cap"])
        self.assertFalse(result["future_input_bounds_verified"])

    def test_packet_stage_and_batch_reservations_agree(self):
        result = self.estimate()
        packet_total = sum((6 * Decimal(p["full_packet_reservation_usd"])
            + 6 * Decimal(p["fit_only_reservation_usd"]) for p in result["packets"].values()), Decimal(0))
        stage_total = sum((r["count"] * Decimal(r["reservation_each_usd"])
            for r in result["stage_rows"]), Decimal(0))
        self.assertEqual(Decimal(result["batch_reservation_usd"]), packet_total)
        self.assertEqual(packet_total, stage_total)

    def test_contingency_and_exact_boundary(self):
        self.assertTrue(self.estimate("173.001280", "1")["arithmetic_within_supplied_cap"])
        self.assertEqual("0.000001", self.estimate("173.001279", "1")["reservation_gap_usd"])

    def test_ceil_each_request_before_multiplication(self):
        amount = subject.request_reservation(1, 1, "0.000001", "0.000001")
        self.assertEqual(Decimal("0.000001"), amount)

    def test_caller_decimal_context_cannot_lower_reservations(self):
        pricing = {**self.pricing, "input_usd_per_million": "2.123456",
            "output_usd_per_million": "10.123456"}
        expected = subject.estimate(self.inputs, pricing, "100", "0")
        with localcontext() as context:
            context.prec = 7
            context.rounding = ROUND_DOWN
            original = (context.prec, context.rounding, dict(context.traps))
            self.assertEqual(Decimal("0.425567"),
                subject.request_reservation(7, 32768, "2.123456", "10.123456"))
            self.assertEqual(expected, subject.estimate(self.inputs, pricing, "100", "0"))
            self.assertEqual(original, (context.prec, context.rounding, dict(context.traps)))

    def test_no_mutation_or_private_balance_default(self):
        before = copy.deepcopy((self.inputs, self.pricing))
        self.estimate()
        self.assertEqual(before, (self.inputs, self.pricing))
        with self.assertRaises(TypeError):
            subject.estimate(self.inputs, self.pricing)

    def test_invalid_input_bounds_and_omitted_operations(self):
        for bad in (True, -1, 100001, 1.0, "100"):
            inputs = {**self.inputs, "up_materials": bad}
            with self.assertRaises(subject.EstimateRefused):
                subject.estimate(inputs, self.pricing, "100", "0")
        self.inputs.pop("ct_headquarters")
        with self.assertRaises(subject.EstimateRefused):
            self.estimate()

    def test_amounts_prices_and_unknown_categories_fail_closed(self):
        for bad in ("NaN", "Infinity", "-1", "0.0000001", "1e999999", "9999999999999", 100, True):
            with self.assertRaises(subject.EstimateRefused):
                self.estimate(bad)
        for modification in ({"model": "other-model"}, {"input_usd_per_million": "0"},
                             {"cache_price": "1"}, {"source": "https://unverified.invalid"}):
            with self.assertRaises(subject.EstimateRefused):
                subject.estimate(self.inputs, {**self.pricing, **modification}, "100", "0")


if __name__ == "__main__":
    unittest.main()
