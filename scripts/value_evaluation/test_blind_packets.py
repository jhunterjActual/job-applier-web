"""Synthetic controls only: these fixtures are never study quality evidence."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
import blind_packets as subject


class BlindPacketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "packaged"
        self.manifest = {"schema": "blind-packet-source-v2", "study_freeze_sha256": "a" * 64,
            "classification": subject.CLASSIFICATION, "scored_stage": subject.STAGE,
            "shared_inputs": self.bind("facts.txt", b"Synthetic common facts and posting.\n"), "slots": []}
        for product in ("CT", "UP"):
            for number in range(1, 7):
                case = f"V{number:02d}"
                slot = {"product_id": product, "case_id": case, "status": "completed",
                    "reason_code": product.lower() + "_identity_must_stay_private"}
                for kind in ("resume", "cover"):
                    with pymupdf.open() as pdf:
                        page = pdf.new_page(width=300, height=400)
                        page.insert_text((20, 40), f"Synthetic {case} {kind}\nCommon supported fact.")
                        pdf.set_metadata({"author": product, "title": product + " native output"})
                        pdf.set_xml_metadata("<metadata>LEAK_SENTINEL_XMP</metadata>")
                        slot[kind] = self.bind(f"{product}-{case}-{kind}.pdf", pdf.tobytes())
                slot["terminal_receipt"] = self.receipt(slot)
                self.manifest["slots"].append(slot)

    def bind(self, name, raw):
        (self.source / name).write_bytes(raw)
        return {"path": name, "sha256": hashlib.sha256(raw).hexdigest()}

    def receipt(self, slot):
        value = {"schema": "native-completed-application-v2", "classification": subject.CLASSIFICATION,
            "study_freeze_sha256": self.manifest["study_freeze_sha256"],
            "product_id": slot["product_id"], "case_id": slot["case_id"],
            "outcome": slot["status"], "scored_stage": subject.STAGE, "human_corrections": False,
            "documents": {kind: slot[kind]["sha256"] for kind in ("resume", "cover")}
                if slot["status"] == "completed" else {}}
        return self.bind(f"{slot['product_id']}-{slot['case_id']}.json", json.dumps(value).encode())

    def test_derivatives_preserve_every_page_and_originals_hide_labels(self):
        originals = {path.name: path.read_bytes() for path in self.source.iterdir()}
        result = subject.package(self.source, self.manifest, self.output)
        self.assertEqual(12, len(result["slots"]))
        self.assertEqual(24, len(list((self.output / "review").glob("*.pdf"))))
        public = json.dumps(result)
        self.assertNotIn("product_id", public)
        self.assertNotIn("identity_must_stay_private", public)
        mapping = json.loads((self.output / "private" / "mapping.json").read_text())
        for row in result["slots"]:
            original = next(item for item in mapping["packets"] if item["packet_id"] == row["packet_id"])
            for kind, binding in row["documents"].items():
                raw = (self.output / "review" / binding["path"]).read_bytes()
                self.assertNotIn(b"LEAK_SENTINEL_XMP", raw)
                self.assertNotIn(b"native output", raw)
                with pymupdf.open(self.output / "review" / binding["path"]) as pdf:
                    self.assertEqual(subject._pages(pdf), original["equivalence"][kind])
                    self.assertFalse(pdf.metadata["author"])
        self.assertEqual(originals, {path.name: path.read_bytes() for path in self.source.iterdir()})
        with self.assertRaisesRegex(subject.PackagingRefused, "new_output"):
            subject.package(self.source, self.manifest, self.output)

    def test_failed_and_missing_slots_remain_in_denominator(self):
        for slot, status in zip(self.manifest["slots"], ("failed", "missing")):
            slot.update(status=status, resume=None, cover=None)
            slot["terminal_receipt"] = self.receipt(slot) if status == "failed" else None
        result = subject.package(self.source, self.manifest, self.output)
        self.assertEqual(12, result["assigned_packets"])
        self.assertEqual(10, sum(row["review_status"] == "available" for row in result["slots"]))
        self.assertEqual({"failed", "missing"}, {row["source_outcome"] for row in result["slots"] if not row["documents"]})

    def test_scripted_or_corrected_evidence_refused_before_output(self):
        for field, bad in (("classification", "scripted_mechanical_rehearsal"), ("scored_stage", "human_corrected")):
            manifest = copy.deepcopy(self.manifest)
            manifest[field] = bad
            with self.assertRaises(subject.PackagingRefused):
                subject.package(self.source, manifest, self.output)
            self.assertFalse(self.output.exists())

    def test_changed_source_refused_before_output(self):
        slot = self.manifest["slots"][0]
        (self.source / slot["resume"]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(subject.PackagingRefused, "changed_source"):
            subject.package(self.source, self.manifest, self.output)
        self.assertFalse(self.output.exists())

    def test_unreadable_completed_pdf_is_unavailable_without_fallback(self):
        slot = self.manifest["slots"][0]
        slot["resume"] = self.bind(slot["resume"]["path"], b"not a PDF")
        slot["terminal_receipt"] = self.receipt(slot)
        result = subject.package(self.source, self.manifest, self.output)
        unavailable = [row for row in result["slots"] if not row["documents"]]
        self.assertEqual(1, len(unavailable))
        self.assertEqual("completed", unavailable[0]["source_outcome"])
        self.assertEqual("unreadable_pdf", unavailable[0]["reason_code"])

    def test_failure_cannot_supply_earlier_draft(self):
        slot = self.manifest["slots"][0]
        slot["status"] = "failed"
        slot["terminal_receipt"] = self.receipt(slot)
        with self.assertRaisesRegex(subject.PackagingRefused, "failure_cannot"):
            subject.package(self.source, self.manifest, self.output)

    def test_omission_duplicate_and_path_traversal_refused(self):
        for mutation in (lambda m: m["slots"].pop(),
                         lambda m: m["slots"].__setitem__(1, m["slots"][0]),
                         lambda m: m["shared_inputs"].update(path="../facts.txt")):
            manifest = copy.deepcopy(self.manifest)
            mutation(manifest)
            with self.assertRaises(subject.PackagingRefused):
                subject.package(self.source, manifest, self.output)

    def test_failed_receipts_count_toward_total_bound(self):
        for slot in self.manifest["slots"]:
            slot.update(status="failed", resume=None, cover=None)
            slot["terminal_receipt"] = self.receipt(slot)
        with patch.object(subject, "MAX_TOTAL_BYTES", 800):
            with self.assertRaisesRegex(subject.PackagingRefused, "study_input_bound"):
                subject.package(self.source, self.manifest, self.output)

    def test_parent_components_cannot_alias_source_output(self):
        with self.assertRaisesRegex(subject.PackagingRefused, "parent_path_component"):
            subject.package(self.source, self.manifest, self.root / "unused" / ".." / "source" / "output")
        self.assertFalse((self.source / "output").exists())

    def test_encrypted_pdf_cannot_be_decrypted_into_review(self):
        for user_password in ("", "synthetic-user-password"):
            with self.subTest(empty_user_password=not user_password):
                with pymupdf.open() as pdf:
                    pdf.new_page().insert_text((40, 40), "Synthetic encrypted control")
                    raw = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                        owner_pw="synthetic-owner-password", user_pw=user_password)
                with self.assertRaisesRegex(subject.PackagingRefused, "unsupported_pdf_structure"):
                    subject._derivative(raw)


if __name__ == "__main__":
    unittest.main()
