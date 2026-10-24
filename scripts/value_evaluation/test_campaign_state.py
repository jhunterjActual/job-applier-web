import copy
import json
import os
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from unittest.mock import patch

from campaign_state import Campaign, CampaignRefused


def policy():
    return {"schema": "study-campaign-policy-v1", "run_id": "synthetic-run",
        "frozen_content_sha256": "a" * 64, "mode": "offline", "approval_reference": None,
        "accounting_basis": "approved_conservative_estimate", "total_limit_usd": "100",
        "packet_limits_usd": {"CT": "10", "UP": "10"},
        "stage_limits_usd": {product: {"fit": "10", "materials": "10"} for product in ("CT", "UP")},
        "request_limits": {product: {"fit": 2, "materials": 2, "packet": 3} for product in ("CT", "UP")},
        "order": [{"product": product, "case": f"V{i:02d}", "stage": stage}
            for product in ("CT", "UP") for i in range(1, 13)
            for stage in (("fit", "materials") if i <= 6 else ("fit",))]}


class CampaignStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ct-campaign-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "campaign.jsonl"
        self.policy = policy()
        self.campaign = Campaign.create(self.path, self.policy)

    def begin(self):
        self.campaign.begin_stage("CT", "V01", "fit")

    def call(self, name="call1", amount="1", actual="0.1"):
        self.campaign.reserve(name, amount, "request/fixture")
        self.campaign.settle(name, actual, "usage/fixture")

    def test_create_and_open_are_exact_offline_binding_with_no_approval_inference(self):
        self.assertEqual("offline", self.campaign.snapshot()["mode"])
        self.assertEqual(self.campaign.snapshot(), Campaign.open(self.path, self.policy).snapshot())
        with self.assertRaises(CampaignRefused):
            Campaign.create(self.path, self.policy)
        changed = copy.deepcopy(self.policy)
        changed["frozen_content_sha256"] = "b" * 64
        with self.assertRaises(CampaignRefused):
            Campaign.open(self.path, changed)

    def test_atomic_admission_pending_blocks_both_products_and_completion(self):
        self.begin()
        self.campaign.reserve("call1", "2", "request/fixture")
        snapshot = Campaign.open(self.path, self.policy).snapshot()
        self.assertEqual(1, snapshot["admitted_requests"])
        self.assertEqual(2_000_000, snapshot["exposure_microdollars"])
        self.assertEqual(1, snapshot["pending_calls"])
        for action in (
            lambda: self.campaign.reserve("call2", "1", "request/fixture"),
            lambda: self.campaign.finish_stage("failed", "terminal/fixture"),
            lambda: self.campaign.begin_stage("UP", "V01", "fit"),
        ):
            with self.assertRaises(CampaignRefused):
                action()

    def test_settlement_never_resets_count_or_allows_duplicate_call(self):
        self.begin()
        self.call()
        self.assertEqual(1, self.campaign.snapshot()["admitted_requests"])
        self.assertEqual(100_000, self.campaign.snapshot()["exposure_microdollars"])
        with self.assertRaises(CampaignRefused):
            self.campaign.reserve("call1", "1", "request/fixture")
        with self.assertRaises(CampaignRefused):
            self.campaign.settle("call1", "0", "usage/fixture")

    def test_unknown_is_durable_and_cannot_be_silently_settled_or_replayed(self):
        self.begin()
        self.campaign.reserve("call1", "2", "request/fixture")
        self.campaign.mark_unknown("call1", "outcome/fixture")
        reopened = Campaign.open(self.path, self.policy)
        self.assertTrue(reopened.snapshot()["halted"])
        self.assertEqual(2_000_000, reopened.snapshot()["exposure_microdollars"])
        for action in (
            lambda: reopened.settle("call1", "0", "usage/fixture"),
            lambda: reopened.reserve("call2", "1", "request/fixture"),
            lambda: reopened.finish_stage("failed", "terminal/fixture"),
        ):
            with self.assertRaises(CampaignRefused):
                action()

    def test_response_before_settle_crash_keeps_original_reservation(self):
        self.begin()
        self.campaign.reserve("response-arrived", "3", "request/fixture")
        reopened = Campaign.open(self.path, self.policy)
        self.assertEqual(3_000_000, reopened.snapshot()["exposure_microdollars"])
        with self.assertRaises(CampaignRefused):
            reopened.reserve("retry", "1", "request/fixture")

    def test_packet_count_spans_fit_materials_and_child_reviewer_calls(self):
        self.begin()
        self.call("fit1", actual="0")
        self.call("fit2", actual="0")
        self.campaign.finish_stage("succeeded", "terminal/fixture")
        self.campaign.begin_stage("CT", "V01", "materials")
        self.call("native-reviewer", actual="0")
        with self.assertRaises(CampaignRefused):
            self.campaign.reserve("continuation", "1", "request/fixture")
        self.assertEqual(3, self.campaign.snapshot()["admitted_requests"])

    def test_stage_count_ceiling_and_failed_known_zero_preserve_count(self):
        self.begin()
        self.call("call1", actual="0")
        self.call("call2", actual="0")
        with self.assertRaises(CampaignRefused):
            self.campaign.reserve("call3", "1", "request/fixture")
        self.campaign.finish_stage("failed", "terminal/fixture")
        self.campaign.begin_stage("CT", "V01", "materials")
        with self.assertRaises(CampaignRefused):
            self.campaign.reserve("should-not-draft", "1", "request/fixture")
        self.campaign.finish_stage("skipped", "terminal/fixture")
        self.assertEqual(2, self.campaign.snapshot()["admitted_requests"])

    def test_packet_dollar_ceiling_spans_stages(self):
        self.begin()
        self.call("fit", amount="9", actual="9")
        self.campaign.finish_stage("succeeded", "terminal/fixture")
        self.campaign.begin_stage("CT", "V01", "materials")
        with self.assertRaises(CampaignRefused):
            self.campaign.reserve("draft", "1.000001", "request/fixture")
        self.campaign.reserve("draft", "1", "request/fixture")

    def test_overage_is_recorded_and_halts_instead_of_hiding_actual_charge(self):
        self.begin()
        self.campaign.reserve("call1", "1", "request/fixture")
        self.campaign.settle("call1", "1.000001", "usage/fixture")
        self.assertTrue(self.campaign.snapshot()["halted"])
        self.assertEqual(1_000_001, self.campaign.snapshot()["exposure_microdollars"])
        with self.assertRaises(CampaignRefused):
            self.campaign.finish_stage("failed", "terminal/fixture")

    def test_shared_total_ceiling_spans_both_products(self):
        bound = copy.deepcopy(self.policy)
        bound["total_limit_usd"] = "2"
        bound["packet_limits_usd"] = {"CT": "2", "UP": "2"}
        bound["stage_limits_usd"] = {product: {"fit": "2", "materials": "2"} for product in ("CT", "UP")}
        upstream_fit = {"product": "UP", "case": "V01", "stage": "fit"}
        bound["order"].remove(upstream_fit)
        bound["order"].insert(1, upstream_fit)
        campaign = Campaign.create(self.path.with_name("shared-total.jsonl"), bound)
        campaign.begin_stage("CT", "V01", "fit")
        campaign.reserve("ct-fit", "0.6", "request/fixture")
        campaign.settle("ct-fit", "0.6", "usage/fixture")
        campaign.finish_stage("succeeded", "terminal/fixture")
        campaign.begin_stage("UP", "V01", "fit")
        with self.assertRaises(CampaignRefused):
            campaign.reserve("up-fit", "1.400001", "request/fixture")
        campaign.reserve("up-fit", "1.4", "request/fixture")

    def test_stage_dollar_ceiling_does_not_borrow_another_stage_allocation(self):
        bound = copy.deepcopy(self.policy)
        bound["stage_limits_usd"]["CT"]["fit"] = "1"
        campaign = Campaign.create(self.path.with_name("stage-money.jsonl"), bound)
        campaign.begin_stage("CT", "V01", "fit")
        with self.assertRaises(CampaignRefused):
            campaign.reserve("fit", "1.000001", "request/fixture")

    def test_full_synthetic_order_and_fit_only_material_refusal(self):
        for index, stage in enumerate(self.policy["order"]):
            self.campaign.begin_stage(**stage)
            self.call(f"call{index}", amount="0.01", actual="0.01")
            self.campaign.finish_stage("succeeded", "terminal/fixture")
        result = self.campaign.snapshot()
        self.assertEqual(36, result["completed_stages"])
        self.assertEqual(36, result["admitted_requests"])
        for case in ("V07", "V12"):
            with self.assertRaises(CampaignRefused):
                self.campaign.begin_stage("CT", case, "materials")

    def test_wrong_order_and_unknown_fields_or_bool_limits_refused_without_change(self):
        before = self.path.read_bytes()
        with self.assertRaises(CampaignRefused):
            self.campaign.begin_stage("UP", "V01", "fit")
        self.assertEqual(before, self.path.read_bytes())
        invalid = copy.deepcopy(self.policy)
        invalid["request_limits"]["CT"]["fit"] = True
        with self.assertRaises(CampaignRefused):
            Campaign(self.path, invalid)
        invalid = copy.deepcopy(self.policy)
        invalid["approval"] = True
        with self.assertRaises(CampaignRefused):
            Campaign(self.path, invalid)

    def test_missing_legacy_corrupt_or_complete_line_truncation_refused(self):
        self.begin()
        original = self.path.read_bytes()
        variants = [b'', b'{"version":2}\n', original[:-1], b''.join(original.splitlines(keepends=True)[:-1]), original.replace(b'"begin"', b'"invalid"')]
        for raw in variants:
            self.path.write_bytes(raw)
            with self.assertRaises(CampaignRefused):
                Campaign.open(self.path, self.policy)
        self.path.write_bytes(original)
        self.assertTrue(self.campaign.snapshot()["active"])

    def test_interrupted_head_commit_refuses_resume(self):
        with patch.object(self.campaign, "_write_head", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                self.begin()
        with self.assertRaises(CampaignRefused):
            Campaign.open(self.path, self.policy)

    def test_old_lock_never_cleared_and_no_mutation(self):
        lock = self.path.with_name(self.path.name + ".lock")
        lock.write_text('{"pid":1,"owner":"fixture"}')
        original = self.path.read_bytes()
        with self.assertRaises(CampaignRefused):
            self.begin()
        self.assertEqual(original, self.path.read_bytes())
        self.assertTrue(lock.exists())

    def test_concurrent_admission_is_exclusive_and_never_oversubscribes(self):
        self.begin()
        barrier = Barrier(2)
        def reserve(index):
            barrier.wait()
            try:
                Campaign(self.path, self.policy).reserve(f"call{index}", "1", "request/fixture")
                return True
            except CampaignRefused:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, (1, 2)))
        self.assertEqual(1, sum(results))
        self.assertEqual(1, self.campaign.snapshot()["admitted_requests"])

    def test_paid_mode_requires_explicit_reference_but_does_not_dispatch(self):
        paid = copy.deepcopy(self.policy)
        paid["mode"] = "paid"
        with self.assertRaises(CampaignRefused):
            Campaign(self.path, paid)
        paid["approval_reference"] = "owner/frozen-approval"
        candidate = Campaign.create(self.path.with_name("declaration-only.jsonl"), paid)
        self.assertEqual(0, candidate.snapshot()["admitted_requests"])

    def test_proposed_full_envelope_admits_186_requests_and_preserves_headroom(self):
        bound = copy.deepcopy(self.policy)
        bound["total_limit_usd"] = "200"
        bound["packet_limits_usd"] = {"CT": "2", "UP": "24"}
        bound["stage_limits_usd"] = {"CT": {"fit": "1", "materials": "1"}, "UP": {"fit": "4", "materials": "20"}}
        bound["request_limits"] = {"CT": {"fit": 1, "materials": 1, "packet": 2}, "UP": {"fit": 4, "materials": 20, "packet": 24}}
        campaign = Campaign.create(self.path.with_name("proposed-not-approved.jsonl"), bound)
        number = 0
        for stage in bound["order"]:
            campaign.begin_stage(**stage)
            for _ in range(bound["request_limits"][stage["product"]][stage["stage"]]):
                number += 1
                campaign.reserve(f"call{number}", "1", "request/fixture")
                campaign.settle(f"call{number}", "1", "usage/fixture")
            with self.assertRaises(CampaignRefused):
                campaign.reserve("over-limit", "0.000001", "request/fixture")
            campaign.finish_stage("succeeded", "terminal/fixture")
        self.assertEqual(186, campaign.snapshot()["admitted_requests"])
        self.assertEqual(186_000_000, campaign.snapshot()["exposure_microdollars"])

    def test_hard_link_and_pending_head_refused(self):
        linked = self.path.with_name("linked.jsonl")
        os.link(self.path, linked)
        try:
            with self.assertRaises(CampaignRefused):
                Campaign.open(self.path, self.policy)
        finally:
            linked.unlink()
        self.campaign.pending_head.write_text("interrupted")
        with self.assertRaises(CampaignRefused):
            self.campaign.snapshot()

    def test_replaced_lock_owner_is_preserved(self):
        lock = self.path.with_name(self.path.name + ".lock")
        with self.assertRaises(CampaignRefused):
            with self.campaign._lock():
                lock.write_text("changed owner")
        self.assertEqual("changed owner", lock.read_text())


if __name__ == "__main__":
    unittest.main()
