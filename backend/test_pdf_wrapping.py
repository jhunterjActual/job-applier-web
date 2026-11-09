"""Real Chromium final-PDF regressions for long unbroken user text."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf


class PDFWrappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="pdf-wrap-synthetic-")
        cls.root = Path(cls.temporary.name)
        cls.environment = patch.dict(os.environ, {"CAREERTRELLIS_DATA_DIR": str(cls.root / "data")})
        cls.environment.start()
        try:
            import utils
            cls.renderer = utils
            path = Path(__file__).resolve().parents[1] / "scripts/document_quality.py"
            spec = importlib.util.spec_from_file_location("wrap_document_quality", path)
            cls.diagnostics = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.diagnostics)
        except BaseException:
            cls.environment.stop()
            cls.temporary.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.temporary.cleanup()

    def facts(self, path, kind):
        raw = path.read_bytes()
        report = self.diagnostics.inspect_pdf(raw, document_kind=kind,
            expected_text={"contact": "alex@example.invalid", "body": "Synthetic engineering experience"})
        measured = {check["check"]: check for check in report["checks"]}
        for name in ("pdf_decode", "page_limit", "text_extraction", "expected_text", "page_boundary_text"):
            self.assertEqual("passed", measured[name]["status"], (name, measured[name]))
        # Diagnostic manual/layout checks remain skipped, not an ATS assertion.
        self.assertEqual("incomplete", report["outcome"])
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            text = "\n".join(page.get_text() for page in document)
            sizes = {round(span["size"], 3) for page in document
                for block in page.get_text("dict")["blocks"] if block.get("type") == 0
                for line in block["lines"] for span in line["spans"] if span["text"].strip()}
            links = [link.get("uri") for page in document for link in page.get_links()]
            return text, sizes, links, document.page_count

    def test_long_url_wraps_without_shrinking_or_losing_text(self):
        url = "https://portfolio.example.invalid/work/" + "W" * 500
        normal = "Alex Example\nalex@example.invalid\n\nSynthetic engineering experience building and verifying reliable software."
        for kind, generator, source in (
            ("ordinary_resume", self.renderer.generate_resume_pdf, "# " + normal),
            ("cover_letter", self.renderer.generate_cover_letter_pdf, normal),
        ):
            with self.subTest(kind=kind):
                baseline = self.root / (kind + "-normal.pdf")
                wrapped = self.root / (kind + "-long.pdf")
                generator(source, str(baseline))
                generator(source + "\n\n" + url, str(wrapped))
                original_text, original_sizes, _, original_pages = self.facts(baseline, kind)
                text, sizes, links, pages = self.facts(wrapped, kind)
                self.assertEqual(original_sizes, sizes, "A long token must not shrink the whole document")
                self.assertEqual(1, original_pages)
                self.assertEqual(1, pages)
                # Only PDF line separators are reconstructed; legitimate spaces
                # are never removed to manufacture a match.
                self.assertIn(url, text.replace("\n", "").replace("\r", ""))
                self.assertGreater(text.count("\n"), original_text.count("\n"))
                if kind == "ordinary_resume":
                    self.assertIn(url, links, "Wrapped anchor must retain its complete click target")

    def test_true_page_overflow_still_refuses_output(self):
        path = self.root / "oversized.pdf"
        content = "# Alex Example\n\n" + "\n\n".join(
            f"Synthetic paragraph {index}: verified engineering responsibilities and evidence." for index in range(200))
        with self.assertRaisesRegex(ValueError, r"^Generated document exceeds the 1-page limit after compact formatting\. Shorten the source content and regenerate it\.$"):
            self.renderer.generate_resume_pdf(content, str(path), max_pages=1)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
