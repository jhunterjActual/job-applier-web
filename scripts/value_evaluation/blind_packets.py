"""Create-once identity-withheld review copies of explicitly bound study PDFs.

Never discovers outputs, changes originals, scores quality, authenticates receipts,
or dispatches work. Export only the review subdirectory, never its private sibling.
"""
import hashlib
import json
from pathlib import Path
from random import SystemRandom
import re
from uuid import uuid4

import pymupdf

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
STAGE = "first_native_completed_pre_human"
CLASSIFICATION = "real_application_comparison"


class PackagingRefused(ValueError):
    pass


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise PackagingRefused("invalid_fields")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise PackagingRefused("invalid_digest")


def _private_path(path):
    if ".." in Path(path).parts:
        raise PackagingRefused("parent_path_component")
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise PackagingRefused("linked_path")
    if path.exists() and path.is_file() and path.stat().st_nlink != 1:
        raise PackagingRefused("hard_link")
    return path


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackagingRefused("duplicate_json_key")
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(PackagingRefused("nonfinite_json")))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise PackagingRefused("invalid_json") from error


def _bound(root, value):
    _fields(value, "path sha256")
    _hash(value["sha256"])
    name = value["path"]
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or Path(name).is_absolute() or any(ord(c) < 32 for c in name)
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise PackagingRefused("unsafe_source_path")
    path = _private_path(root / name)
    if not path.is_file() or not path.resolve().is_relative_to(root) or path.stat().st_size > MAX_FILE_BYTES:
        raise PackagingRefused("missing_or_oversized_source")
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES or _digest(raw) != value["sha256"]:
        raise PackagingRefused("changed_source")
    return raw


def _pages(document):
    if (document.needs_pass or document.metadata.get("encryption")
            or document.xref_get_key(-1, "Encrypt")[0] != "null"
            or not 1 <= len(document) <= 12 or document.is_form_pdf):
        raise PackagingRefused("unsupported_pdf_structure")
    result = []
    for page in document:
        if not 0 < page.rect.width <= 2000 or not 0 < page.rect.height <= 2000:
            raise PackagingRefused("unsupported_page_geometry")
        text = page.get_text("text", sort=False).encode("utf-8")
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), colorspace=pymupdf.csRGB, alpha=False)
        result.append({"geometry": list(page.rect), "text_order_sha256": _digest(text),
                       "render_sha256": _digest(pixels.samples)})
    return result


def _derivative(raw):
    if not raw.startswith(b"%PDF-"):
        raise PackagingRefused("unreadable_pdf")
    try:
        with pymupdf.open(stream=raw, filetype="pdf") as original:
            before = _pages(original)
            original.set_metadata({})
            original.del_xml_metadata()
            result = original.tobytes(garbage=4, deflate=False, no_new_id=True)
        with pymupdf.open(stream=result, filetype="pdf") as derivative:
            if _pages(derivative) != before:
                raise PackagingRefused("derivative_content_changed")
            if derivative.get_xml_metadata() or any(derivative.metadata.get(k) for k in (
                    "title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate")):
                raise PackagingRefused("metadata_not_cleared")
        return result, before
    except PackagingRefused:
        raise
    except Exception as error:
        raise PackagingRefused("unreadable_pdf") from error


def _write(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def _write_json(path, value):
    _write(path, (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())


def package(source_root, manifest, output_root):
    """Package all 12 assigned slots; a complete package is never a quality pass."""
    source = _private_path(source_root).resolve(strict=True)
    output = _private_path(output_root)
    if not source.is_dir() or not output.parent.is_dir() or output.exists():
        raise PackagingRefused("new_output_directory_required")
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise PackagingRefused("source_output_must_be_disjoint")
    _fields(manifest, "schema study_freeze_sha256 classification scored_stage shared_inputs slots")
    if (manifest["schema"] != "blind-packet-source-v2" or manifest["classification"] != CLASSIFICATION
            or manifest["scored_stage"] != STAGE):
        raise PackagingRefused("scored_native_completed_evidence_required")
    _hash(manifest["study_freeze_sha256"])
    inputs = _bound(source, manifest["shared_inputs"])
    if len(inputs) > 2 * 1024 * 1024:
        raise PackagingRefused("oversized_shared_inputs")
    try:
        inputs.decode("utf-8")
    except UnicodeError as error:
        raise PackagingRefused("inputs_must_be_utf8") from error
    slots = manifest["slots"]
    if not isinstance(slots, list) or len(slots) != 12:
        raise PackagingRefused("all_twelve_slots_required")
    expected = {(product, f"V{i:02d}") for product in ("CT", "UP") for i in range(1, 7)}
    seen, paths, prepared, total = set(), set(), [], len(inputs)
    for slot in slots:
        _fields(slot, "product_id case_id status terminal_receipt resume cover reason_code")
        if not isinstance(slot["product_id"], str) or not isinstance(slot["case_id"], str):
            raise PackagingRefused("invalid_slot_identity")
        identity = (slot["product_id"], slot["case_id"])
        if identity not in expected or identity in seen:
            raise PackagingRefused("duplicate_or_unassigned_slot")
        seen.add(identity)
        if not isinstance(slot["status"], str) or slot["status"] not in {"completed", "failed", "missing"}:
            raise PackagingRefused("invalid_outcome")
        if not isinstance(slot["reason_code"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", slot["reason_code"]):
            raise PackagingRefused("sanitized_reason_required")
        receipt = None
        if slot["terminal_receipt"] is not None:
            receipt_raw = _bound(source, slot["terminal_receipt"])
            total += len(receipt_raw)
            if total > MAX_TOTAL_BYTES:
                raise PackagingRefused("study_input_bound_exceeded")
            receipt = _json(receipt_raw)
            _fields(receipt, "schema classification study_freeze_sha256 product_id case_id outcome scored_stage human_corrections documents")
            if (receipt["schema"] != "native-completed-application-v2" or receipt["classification"] != CLASSIFICATION
                    or receipt["study_freeze_sha256"] != manifest["study_freeze_sha256"]
                    or (receipt["product_id"], receipt["case_id"]) != identity
                    or receipt["outcome"] != slot["status"] or receipt["scored_stage"] != STAGE
                    or receipt["human_corrections"] is not False):
                raise PackagingRefused("receipt_binding_or_stage_mismatch")
        if slot["status"] != "completed":
            if slot["resume"] is not None or slot["cover"] is not None or (slot["status"] == "failed" and receipt is None):
                raise PackagingRefused("failure_cannot_substitute_draft")
            if receipt is not None and receipt["documents"] != {}:
                raise PackagingRefused("failure_document_declaration")
            prepared.append((slot, {}, "not_available"))
            continue
        if receipt is None:
            raise PackagingRefused("completed_receipt_required")
        _fields(receipt["documents"], "resume cover")
        documents, issue = {}, None
        for kind in ("resume", "cover"):
            raw = _bound(source, slot[kind])
            total += len(raw)
            path = slot[kind]["path"]
            if path in paths or receipt["documents"][kind] != slot[kind]["sha256"]:
                raise PackagingRefused("reused_path_or_document_mismatch")
            paths.add(path)
            try:
                documents[kind] = _derivative(raw)
            except PackagingRefused as error:
                issue = str(error)
        if total > MAX_TOTAL_BYTES:
            raise PackagingRefused("study_input_bound_exceeded")
        prepared.append((slot, documents if issue is None else {}, issue))
    # Validate all source bindings first. Interrupted output is retained, never resumed.
    output.mkdir(mode=0o700)
    private, review = output / "private", output / "review"
    private.mkdir(mode=0o700)
    review.mkdir(mode=0o700)
    _write_json(output / "operation-start.json", {"schema": "blind-package-operation-v2", "status": "started",
        "study_freeze_sha256": manifest["study_freeze_sha256"], "export_only": "review"})
    _write(review / "shared-inputs.txt", inputs)
    public_rows, mapping = [], []
    # Creation timestamps and directory order must not reconstruct caller order.
    SystemRandom().shuffle(prepared)
    for slot, documents, issue in prepared:
        opaque = "p" + uuid4().hex
        row = {"packet_id": opaque, "case_id": slot["case_id"], "source_outcome": slot["status"],
               "review_status": "available" if documents else "unavailable", "documents": {},
               "reason_code": issue or "native_completed"}
        private_row = {"packet_id": opaque, "original_slot": slot, "equivalence": {}}
        for kind, (raw, pages) in documents.items():
            filename = opaque + "_" + kind + ".pdf"
            _write(review / filename, raw)
            row["documents"][kind] = {"path": filename, "sha256": _digest(raw), "pages": len(pages)}
            private_row["equivalence"][kind] = pages
        public_rows.append(row)
        mapping.append(private_row)
    # Input order must not disclose a product order; IDs alone are insufficient.
    public_rows.sort(key=lambda row: row["packet_id"])
    public = {"schema": "identity-withheld-review-packets-v2", "shared_inputs_sha256": _digest(inputs),
              "assigned_packets": 12, "slots": public_rows,
              "limits": "Standard/XMP metadata removed only. Visible text, links, tags and styling retained; product identity may be inferred. Declarations are not authenticated or scored."}
    _write_json(private / "mapping.json", {"schema": "private-blind-packet-mapping-v2", "manifest": manifest, "packets": mapping})
    _write_json(review / "manifest.json", public)
    _write_json(output / "operation-complete.json", {"schema": "blind-package-operation-v2", "status": "complete",
        "review_manifest_sha256": _digest((review / "manifest.json").read_bytes()), "assigned_packets": 12,
        "available_packets": sum(bool(row["documents"]) for row in public_rows),
        "originals_opened_writable": False, "quality_or_authority_established": False})
    return public
