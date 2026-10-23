import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import builtins

import pymupdf

from document_quality import inspect_pdf, inspect_file


TEXT = "Alex Example alex@example.invalid Software Engineer Python REST APIs and automated tests."


def pdf(pages=1, text=TEXT, position=(40, 60), rotation=0):
    with pymupdf.open() as document:
        for _ in range(pages):
            page = document.new_page(width=612, height=792)
            if text:
                page.insert_text(position, text, fontsize=10)
            page.set_rotation(rotation)
        return document.tobytes()


def check(report, name):
    return next(item for item in report["checks"] if item["check"] == name)


class DocumentQualityTests(unittest.TestCase):
    def test_valid_bytes_pass_measured_checks_but_manual_checks_remain(self):
        raw = pdf()
        report = inspect_pdf(raw, expected_text={"contact": "alex@example.invalid", "name": "Alex Example"})
        self.assertEqual(report["pdf_sha256"], hashlib.sha256(raw).hexdigest())
        for name in ("pdf_decode", "page_limit", "text_extraction", "expected_text", "page_boundary_text"):
            self.assertEqual(check(report, name)["status"], "passed", name)
        self.assertEqual(report["outcome"], "incomplete")
        self.assertFalse(report["ats_compatibility_claimed"])

    def test_blank_pdf_fails_extraction(self):
        report = inspect_pdf(pdf(text=""))
        self.assertEqual(check(report, "text_extraction")["status"], "failed")
        self.assertEqual(report["outcome"], "failed")

    def test_blank_second_page_cannot_hide_in_aggregate_text(self):
        with pymupdf.open(stream=pdf(), filetype="pdf") as document:
            document.new_page()
            raw = document.tobytes()
        report = inspect_pdf(raw)
        self.assertEqual(check(report, "text_extraction")["empty_text_pages"], [2])

    def test_invalid_bytes_fail_decode(self):
        report = inspect_pdf(b"not a PDF")
        self.assertEqual(report["outcome"], "failed")
        self.assertEqual(check(report, "pdf_decode")["reason"], "unreadable_pdf")

    def test_missing_expected_contact_is_named_without_exposing_text(self):
        report = inspect_pdf(pdf(), expected_text={"contact": "absent@example.invalid"})
        self.assertEqual(check(report, "expected_text")["missing_labels"], ["contact"])
        serialized = json.dumps(report)
        self.assertNotIn("absent@example.invalid", serialized)
        self.assertNotIn("alex@example.invalid", serialized)

    def test_text_crossing_right_boundary_is_detected(self):
        report = inspect_pdf(pdf(position=(590, 60)))
        self.assertEqual(check(report, "page_boundary_text")["status"], "failed")
        self.assertEqual(check(report, "page_boundary_text")["affected_pages"], [1])

    def test_rotated_valid_text_uses_unrotated_geometry(self):
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation):
                report = inspect_pdf(pdf(rotation=rotation))
                self.assertEqual(check(report, "page_boundary_text")["status"], "passed")

    def test_profession_specific_page_policy(self):
        three = pdf(pages=3)
        ordinary = inspect_pdf(three)
        academic = inspect_pdf(three, document_kind="academic_cv")
        letter = inspect_pdf(pdf(pages=2), document_kind="cover_letter")
        self.assertEqual(check(ordinary, "page_limit")["status"], "failed")
        self.assertEqual(check(letter, "page_limit")["status"], "failed")
        self.assertEqual(check(academic, "page_limit")["status"], "skipped")

    def test_encrypted_pdf_requires_password_without_attempting_it(self):
        with pymupdf.open(stream=pdf(), filetype="pdf") as document:
            raw = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                                   owner_pw="synthetic-owner", user_pw="synthetic-user")
        report = inspect_pdf(raw)
        self.assertEqual(check(report, "pdf_decode")["reason"], "password_required")

    def test_file_remains_byte_identical(self):
        raw = pdf()
        with tempfile.TemporaryDirectory(prefix="ct-pdf-quality-") as directory:
            path = Path(directory) / "synthetic.pdf"
            path.write_bytes(raw)
            inspect_file(path)
            self.assertEqual(path.read_bytes(), raw)

    def test_missing_expectations_are_skipped_not_passed(self):
        self.assertEqual(check(inspect_pdf(pdf()), "expected_text")["status"], "skipped")

    def test_invalid_options_refused(self):
        for options in ({"document_kind": "unknown"}, {"minimum_characters": True},
                        {"boundary_tolerance_points": float("nan")},
                        {"expected_text": {"invalid label": "anything"}}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                inspect_pdf(pdf(), **options)

    def test_missing_decoder_is_skipped_not_document_failure(self):
        raw = pdf()
        original = builtins.__import__
        def import_without_decoder(name, *args, **kwargs):
            if name == "pymupdf":
                raise ImportError("synthetic unavailable decoder")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=import_without_decoder):
            report = inspect_pdf(raw)
        self.assertEqual(check(report, "pdf_decode")["status"], "skipped")
        self.assertEqual(report["outcome"], "incomplete")

    def test_extractor_failure_is_skipped_not_missing_text(self):
        raw = pdf()
        with patch.object(pymupdf.Page, "get_text", side_effect=RuntimeError("synthetic extractor failure")):
            report = inspect_pdf(raw, expected_text={"contact": "alex@example.invalid"})
        for name in ("text_extraction", "expected_text", "page_boundary_text"):
            self.assertEqual(check(report, name)["status"], "skipped")
        self.assertEqual(report["outcome"], "incomplete")

    def test_invisible_text_cannot_establish_visual_readability(self):
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((40, 60), TEXT, fontsize=10, render_mode=3)
            raw = document.tobytes()
        report = inspect_pdf(raw)
        self.assertEqual(check(report, "text_extraction")["status"], "passed")
        self.assertEqual(check(report, "visual_readability")["status"], "skipped")
        self.assertEqual(report["outcome"], "incomplete")


if __name__ == "__main__":
    unittest.main()
