import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import study_readiness as readiness


class StudyReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = dict(schema="application-study-freeze-v2", study_id="synthetic-v2")
        for name in ("protocol", "inputs", "expected_key", "codebook", "order", "runtime"):
            self.manifest[name] = self.save(name, {"synthetic": name})
        self.manifest["controls"] = dict(
            evaluation_date="2026-10-20", material_cases=[f"V{i:02d}" for i in range(1, 7)],
            no_material_cases=[f"V{i:02d}" for i in range(7, 13)], scored_stage="first_native_completed_pre_human",
            resume_pages=[1, 2], native_page_compliance_separate=True, automatic_retries=False,
            unknown_outcome_pause=True, retain_first_drafts=True)
        self.manifest["products"] = []
        for name in ("CT", "UP"):
            settings = self.save(name + "-settings", {"synthetic": name})
            proof = self.save(name + "-proof", dict(schema="native-mechanics-proof-v2", product_id=name,
                product_commit="a" * 40, settings_sha256=settings["sha256"], runtime_sha256=self.manifest["runtime"]["sha256"],
                provider_calls=0, terminal=True, completed_steps=["review", "resume_pdf_delivery", "cover_pdf_delivery",
                "extraction", "layout", "every_page_visual", "native_recordkeeping", "unknown_outcome"]))
            self.manifest["products"].append(dict(id=name, commit="a" * 40, settings=settings, native_proof=proof, full_packet_cap_usd="2"))
        calibration = self.save("calibration", dict(schema="claim-calibration-v2", codebook_sha256=self.manifest["codebook"]["sha256"],
                                                   reviewer_ids=["reviewer-one", "reviewer-two"], passed=True))
        self.manifest["reviewers"] = dict(independent_ids=["reviewer-one", "reviewer-two"], adjudicator_id="adjudicator",
                                          identity_withheld=True, calibration=calibration)
        pricing = self.save("pricing", dict(schema="verified-study-pricing-v2", model="synthetic-model", input_usd_per_million="2",
                                            output_usd_per_million="10", verified_utc="2026-10-24T17:00:00Z", source="https://example.invalid/pricing"))
        rows = [dict(product_id=name, stage=stage, count=12 if stage == "fit" else 6,
                     expected_each_usd="0.1", reservation_each_usd="1", full_native_workflow=True, max_requests_each=4)
                for name in ("CT", "UP") for stage in ("fit", "materials")]
        self.manifest["budget"] = dict(pricing=pricing, rows=rows, paid_scoring_reservation_usd="0", contingency_usd="4",
                                       total_cap_usd="40", accounting_basis="approved_conservative_estimate")
        self.manifest["funding"] = dict(state="pending", approval_reference="", approved_manifest_sha256="")

    def save(self, name, content):
        raw = json.dumps(content).encode()
        path = self.root / (name + ".json")
        path.write_bytes(raw)
        return dict(path=path.name, sha256=hashlib.sha256(raw).hexdigest())

    def approve_declared(self):
        digest = readiness.audit(self.root, self.manifest)["frozen_content_sha256"]
        self.manifest["funding"] = dict(state="approved", approval_reference="synthetic-approval-only", approved_manifest_sha256=digest)

    def test_complete_declarations_never_establish_authority(self):
        self.approve_declared()
        result = readiness.audit(self.root, self.manifest)
        self.assertTrue(result["declared_evidence_complete"])
        self.assertEqual("not_established_by_validator", result["authorization"])
        self.assertEqual("40", result["complete_batch_reservation_usd"])
        self.assertEqual("3.6", result["expected_generation_usd"])

    def test_pending_and_stale_funding_stop(self):
        self.assertIn("funding_not_recorded_approved", readiness.audit(self.root, self.manifest)["blockers"])
        self.approve_declared()
        self.manifest["products"][0]["commit"] = "b" * 40
        blockers = readiness.audit(self.root, self.manifest)["blockers"]
        self.assertIn("funding_binding_missing_or_stale", blockers)
        self.assertIn("CT:native_proof_binding", blockers)

    def test_full_batch_not_cheap_subset(self):
        self.manifest["budget"]["rows"][0]["count"] = 1
        with self.assertRaises(readiness.ReadinessError):
            readiness.audit(self.root, self.manifest)

    def test_caps_include_native_fit_material_review_and_scoring(self):
        self.manifest["budget"]["paid_scoring_reservation_usd"] = "5"
        self.manifest["products"][1]["full_packet_cap_usd"] = "1"
        self.manifest["budget"]["rows"][3]["full_native_workflow"] = False
        blockers = readiness.audit(self.root, self.manifest)["blockers"]
        for expected in ("total_cap_below_complete_batch_reservations", "UP:full_packet_cap_below_fit_plus_materials", "UP:native_cost_incomplete"):
            self.assertIn(expected, blockers)

    def test_changed_evidence_cannot_be_silently_rebound(self):
        (self.root / self.manifest["inputs"]["path"]).write_text("changed")
        with self.assertRaises(readiness.ReadinessError):
            readiness.audit(self.root, self.manifest)

    def test_paths_and_duplicate_json_fail_closed(self):
        for path in ("../outside", "/absolute", "C:/secret", "a\\b", "a//b", "./inputs.json"):
            with self.subTest(path=path), self.assertRaises(readiness.ReadinessError):
                readiness.regular_path(self.root, path)
        path = self.root / "duplicate.json"
        path.write_text('{"schema":1,"schema":2}')
        with self.assertRaises(readiness.ReadinessError):
            readiness.read_json(path)

    def test_controls_and_bool_request_limits_refused(self):
        for key, value in (("automatic_retries", True), ("scored_stage", "after_human_edit"), ("evaluation_date", "2026-10-24"), ("resume_pages", [True, 2])):
            candidate = copy.deepcopy(self.manifest)
            candidate["controls"][key] = value
            with self.subTest(key=key), self.assertRaises(readiness.ReadinessError):
                readiness.audit(self.root, candidate)
        self.manifest["budget"]["rows"][0]["max_requests_each"] = True
        with self.assertRaises(readiness.ReadinessError):
            readiness.audit(self.root, self.manifest)

    def test_pricing_requires_full_real_utc_timestamp(self):
        path = self.root / self.manifest["budget"]["pricing"]["path"]
        pricing = readiness.read_json(path)
        for value in ("2026-10-24Z", "2026-02-30T12:00:00Z", "2026-10-24T17:00:00+00:00"):
            pricing["verified_utc"] = value
            self.manifest["budget"]["pricing"] = self.save("pricing", pricing)
            with self.subTest(value=value), self.assertRaises(readiness.ReadinessError):
                readiness.audit(self.root, self.manifest)

    def test_missing_native_step_and_calibration_cannot_pass(self):
        product = self.manifest["products"][0]
        proof = readiness.read_json(self.root / product["native_proof"]["path"])
        proof["completed_steps"].remove("cover_pdf_delivery")
        product["native_proof"] = self.save("CT-proof", proof)
        self.manifest["reviewers"]["calibration"] = self.save("calibration", dict(schema="claim-calibration-v2",
            codebook_sha256=self.manifest["codebook"]["sha256"], reviewer_ids=["reviewer-one", "reviewer-two"], passed=False))
        blockers = readiness.audit(self.root, self.manifest)["blockers"]
        self.assertIn("CT:native_steps_incomplete", blockers)
        self.assertIn("calibration_incomplete", blockers)


if __name__ == "__main__":
    unittest.main()
