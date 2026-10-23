"""Inspect final PDF bytes without changing them or asserting ATS compatibility.

Offline diagnostics only: page limits, extraction, expected text and text crossing
page boundaries. Reading order, internal clipping and template-sensitive layout
remain explicit manual checks. Reports never contain extracted text or metadata.
"""
import hashlib
import re
import unicodedata


MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PAGES = 100


def _check(name, status, reason, **measurements):
    return {"check": name, "status": status, "reason": reason, **measurements}


def _normalized(text):
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def _finish(report):
    statuses = [check["status"] for check in report["checks"]]
    report["outcome"] = "failed" if "failed" in statuses else "incomplete" if "skipped" in statuses else "passed"
    return report


def inspect_pdf(pdf_bytes, *, document_kind="ordinary_resume", expected_text=None,
                minimum_characters=40, boundary_tolerance_points=1.0):
    """Return diagnostics for the exact final bytes; expected_text maps labels to facts.

    The default 40-character floor detects blank/tiny extraction, not quality.
    Labels identify missing expectations without copying personal text into reports.
    Ordinary resumes allow two pages, cover letters one, academic CVs no fixed cap.
    """
    if type(pdf_bytes) is not bytes or not 1 <= len(pdf_bytes) <= MAX_PDF_BYTES:
        raise ValueError("PDF must be nonempty bytes within the supported size")
    if document_kind not in {"ordinary_resume", "cover_letter", "academic_cv"}:
        raise ValueError("Unsupported document kind")
    if type(minimum_characters) is not int or not 1 <= minimum_characters <= 100_000:
        raise ValueError("Invalid extraction threshold")
    if (type(boundary_tolerance_points) not in {int, float}
            or not 0 <= boundary_tolerance_points <= 5):
        raise ValueError("Invalid boundary tolerance")
    expected = {} if expected_text is None else expected_text
    if not isinstance(expected, dict) or len(expected) > 100:
        raise ValueError("Expected text must be a bounded label mapping")
    for label, text in expected.items():
        if (not isinstance(label, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", label)
                or not isinstance(text, str) or not 1 <= len(text) <= 2000 or not text.strip()):
            raise ValueError("Invalid expected-text entry")
    report = {"schema": "document-quality-v1", "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
              "byte_count": len(pdf_bytes), "document_kind": document_kind,
              "checks": [], "pages": [], "ats_compatibility_claimed": False}
    checks = report["checks"]
    try:
        import pymupdf
    except ImportError:
        checks.append(_check("pdf_decode", "skipped", "decoder_unavailable"))
        return _finish(report)
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        checks.append(_check("pdf_decode", "failed", "unreadable_pdf"))
        return _finish(report)
    try:
        if document.needs_pass:
            checks.append(_check("pdf_decode", "failed", "password_required"))
            return _finish(report)
        count = document.page_count
        if not 1 <= count <= MAX_PAGES:
            checks.append(_check("pdf_decode", "failed", "unsupported_page_count", page_count=count))
            return _finish(report)
        checks.append(_check("pdf_decode", "passed", "decoded", page_count=count))
        limit = {"ordinary_resume": 2, "cover_letter": 1, "academic_cv": None}[document_kind]
        if limit is None:
            checks.append(_check("page_limit", "skipped", "academic_cv_has_no_fixed_page_limit", page_count=count))
        else:
            checks.append(_check("page_limit", "passed" if count <= limit else "failed",
                                 "within_limit" if count <= limit else "page_limit_exceeded",
                                 page_count=count, maximum_pages=limit))
        all_text, boundary_pages, blank_pages = [], [], []
        extraction_failed = False
        for index, page in enumerate(document):
            try:
                # Infinite clip retains text objects beyond the visible page edge.
                # PyMuPDF text coordinates are unrotated; compare in that frame.
                content = page.get_text("dict", clip=pymupdf.INFINITE_RECT(), sort=False)
                bounds = page.rect * page.derotation_matrix
                spans = [span for block in content["blocks"] if block.get("type") == 0
                         for line in block.get("lines", []) for span in line.get("spans", [])]
                text = "\n".join(span.get("text", "") for span in spans)
                visible_chars = sum(not char.isspace() for char in text)
                if visible_chars == 0:
                    blank_pages.append(index + 1)
                crossing = 0
                for span in spans:
                    if not span.get("text", "").strip():
                        continue
                    rectangle = pymupdf.Rect(span["bbox"])
                    tolerance = boundary_tolerance_points
                    if (rectangle.x0 < bounds.x0 - tolerance or rectangle.y0 < bounds.y0 - tolerance
                            or rectangle.x1 > bounds.x1 + tolerance or rectangle.y1 > bounds.y1 + tolerance):
                        crossing += 1
                if crossing:
                    boundary_pages.append(index + 1)
                report["pages"].append({"page": index + 1, "extractable_nonspace_characters": visible_chars,
                                        "boundary_crossing_text_spans": crossing})
                all_text.append(text)
            except Exception:
                extraction_failed = True
                report["pages"].append({"page": index + 1, "extraction": "unavailable"})
        if extraction_failed:
            checks.append(_check("text_extraction", "skipped", "extractor_failed"))
            checks.append(_check("expected_text", "skipped", "incomplete_extraction"))
            checks.append(_check("page_boundary_text", "skipped", "incomplete_extraction"))
        else:
            text = "\n".join(all_text)
            characters = sum(not char.isspace() for char in text)
            good = characters >= minimum_characters and not blank_pages
            checks.append(_check("text_extraction", "passed" if good else "failed",
                                 "extractable_text" if good else "insufficient_text_or_empty_page",
                                 nonspace_characters=characters, minimum_characters=minimum_characters,
                                 empty_text_pages=blank_pages))
            if expected:
                normalized = _normalized(text)
                missing = sorted(label for label, fact in expected.items() if _normalized(fact) not in normalized)
                checks.append(_check("expected_text", "failed" if missing else "passed",
                                     "missing_expected_text" if missing else "expected_text_present",
                                     expected_count=len(expected), missing_labels=missing,
                                     comparison="unicode_casefold_whitespace_normalized_presence"))
            else:
                checks.append(_check("expected_text", "skipped", "no_expected_text_supplied"))
            checks.append(_check("page_boundary_text", "failed" if boundary_pages else "passed",
                                 "text_crosses_page_boundary" if boundary_pages else "text_within_page_boundary",
                                 affected_pages=boundary_pages, tolerance_points=boundary_tolerance_points))
        for name, reason in (("visual_readability", "manual_render_review_required"),
                             ("reading_order", "manual_reading_order_review_required"),
                             ("internal_clipping", "clipping_inside_page_not_measured"),
                             ("orphaned_headings", "template_calibration_required"),
                             ("excessive_gaps", "template_calibration_required")):
            checks.append(_check(name, "skipped", reason))
        return _finish(report)
    finally:
        document.close()


def inspect_file(path, **options):
    """Read at most the supported limit; never modify the source PDF."""
    with open(path, "rb") as stream:
        raw = stream.read(MAX_PDF_BYTES + 1)
    return inspect_pdf(raw, **options)
