"""Read-only checks on an explicitly supplied synthetic study freeze.

Checks declared evidence and arithmetic, never authenticity or authorization.
No credential discovery, provider requests, ledger changes, or execution.
The explicitly supplied evidence root must contain synthetic study material only.
"""
import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

MAX_BYTES = 2 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")


class ReadinessError(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReadinessError("duplicate_json_key")
        result[key] = value
    return result


def read_json(path):
    if path.stat().st_size > MAX_BYTES:
        raise ReadinessError("oversized_json")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ReadinessError("oversized_json")
    try:
        return json.loads(raw, object_pairs_hook=unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ReadinessError("nonfinite_json")))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ReadinessError("invalid_json") from error


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise ReadinessError("invalid_fields")


def label(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", value):
        raise ReadinessError("invalid_label")
    return value


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", value):
        raise ReadinessError("invalid_amount")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ReadinessError("invalid_amount") from error
    if result > 1_000_000:
        raise ReadinessError("invalid_amount")
    return result


def regular_path(root, relative):
    if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
            or any(ord(c) < 32 for c in relative)
            or any(p in ("", ".", "..") for p in relative.split("/"))):
        raise ReadinessError("unsafe_path")
    path = Path(root)
    if Path(relative).is_absolute():
        raise ReadinessError("unsafe_path")
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ReadinessError("linked_path")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ReadinessError("missing_or_outside_file")
    return path


def binding(root, value):
    fields(value, "path sha256")
    if not isinstance(value["sha256"], str) or not SHA.fullmatch(value["sha256"]):
        raise ReadinessError("invalid_digest")
    path = regular_path(root, value["path"])
    if path.stat().st_size > MAX_BYTES:
        raise ReadinessError("oversized_evidence")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != value["sha256"]:
        raise ReadinessError("changed_evidence")
    return path


def evidence_root(root):
    root = Path(root)
    for parent in (root, *root.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ReadinessError("linked_root")
    return root.resolve(strict=True)


def audit(root, manifest):
    """Return declared blockers; no return value grants permission to dispatch."""
    root = evidence_root(root)
    fields(manifest, "schema study_id protocol inputs expected_key codebook order runtime controls products reviewers budget funding")
    if manifest["schema"] != "application-study-freeze-v2":
        raise ReadinessError("invalid_schema")
    label(manifest["study_id"])
    controls = manifest["controls"]
    fields(controls, "evaluation_date material_cases no_material_cases scored_stage resume_pages native_page_compliance_separate automatic_retries unknown_outcome_pause retain_first_drafts")
    if (controls["evaluation_date"] != "2026-10-20"
            or controls["material_cases"] != [f"V{i:02d}" for i in range(1, 7)]
            or controls["no_material_cases"] != [f"V{i:02d}" for i in range(7, 13)]
            or controls["scored_stage"] != "first_native_completed_pre_human"
            or not isinstance(controls["resume_pages"], list)
            or any(type(page) is not int for page in controls["resume_pages"])
            or controls["resume_pages"] != [1, 2]
            or controls["native_page_compliance_separate"] is not True
            or controls["automatic_retries"] is not False
            or controls["unknown_outcome_pause"] is not True
            or controls["retain_first_drafts"] is not True):
        raise ReadinessError("invalid_study_controls")
    for key in ("protocol", "inputs", "expected_key", "codebook", "order", "runtime"):
        binding(root, manifest[key])
    if len({manifest[k]["path"] for k in ("inputs", "expected_key", "codebook")}) != 3:
        raise ReadinessError("separate_input_key_codebook_required")
    products = manifest["products"]
    if not isinstance(products, list) or len(products) != 2:
        raise ReadinessError("two_products_required")
    blockers, names = [], set()
    for product in products:
        fields(product, "id commit settings native_proof full_packet_cap_usd")
        name = label(product["id"])
        if name in names or not isinstance(product["commit"], str) or not COMMIT.fullmatch(product["commit"]):
            raise ReadinessError("invalid_product_identity")
        names.add(name)
        binding(root, product["settings"])
        proof = read_json(binding(root, product["native_proof"]))
        fields(proof, "schema product_id product_commit settings_sha256 runtime_sha256 provider_calls terminal completed_steps")
        if (proof["schema"] != "native-mechanics-proof-v2" or proof["product_id"] != name
                or proof["product_commit"] != product["commit"]
                or proof["settings_sha256"] != product["settings"]["sha256"]
                or proof["runtime_sha256"] != manifest["runtime"]["sha256"]):
            blockers.append(name + ":native_proof_binding")
        if type(proof["provider_calls"]) is not int or proof["provider_calls"] != 0 or proof["terminal"] is not True:
            blockers.append(name + ":offline_terminal_proof")
        steps = proof["completed_steps"]
        required = {"review", "resume_pdf_delivery", "cover_pdf_delivery", "extraction", "layout", "every_page_visual", "native_recordkeeping", "unknown_outcome"}
        if not isinstance(steps, list) or any(not isinstance(s, str) for s in steps) or len(steps) != len(set(steps)):
            raise ReadinessError("invalid_native_steps")
        if not required.issubset(steps):
            blockers.append(name + ":native_steps_incomplete")
    reviewers = manifest["reviewers"]
    fields(reviewers, "independent_ids adjudicator_id identity_withheld calibration")
    ids = reviewers["independent_ids"]
    if not isinstance(ids, list) or len(ids) != 2 or len({label(v) for v in ids}) != 2:
        raise ReadinessError("two_independent_reviewers_required")
    label(reviewers["adjudicator_id"])
    if reviewers["adjudicator_id"] in ids:
        blockers.append("independent_adjudicator_required")
    if reviewers["identity_withheld"] is not True:
        blockers.append("identity_withheld_review_required")
    calibration = read_json(binding(root, reviewers["calibration"]))
    fields(calibration, "schema codebook_sha256 reviewer_ids passed")
    if (calibration["schema"] != "claim-calibration-v2" or calibration["codebook_sha256"] != manifest["codebook"]["sha256"]
            or calibration["reviewer_ids"] != ids or calibration["passed"] is not True):
        blockers.append("calibration_incomplete")
    budget = manifest["budget"]
    fields(budget, "pricing rows paid_scoring_reservation_usd contingency_usd total_cap_usd accounting_basis")
    pricing = read_json(binding(root, budget["pricing"]))
    fields(pricing, "schema model input_usd_per_million output_usd_per_million verified_utc source")
    if pricing["schema"] != "verified-study-pricing-v2" or not isinstance(pricing["source"], str) or not pricing["source"].startswith("https://"):
        raise ReadinessError("invalid_pricing")
    label(pricing["model"])
    if (not isinstance(pricing["verified_utc"], str)
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z", pricing["verified_utc"])):
        raise ReadinessError("invalid_pricing_timestamp")
    try:
        datetime.fromisoformat(pricing["verified_utc"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ReadinessError("invalid_pricing_timestamp") from error
    for key in ("input_usd_per_million", "output_usd_per_million"):
        if amount(pricing[key]) <= 0:
            raise ReadinessError("invalid_price")
    if budget["accounting_basis"] not in ("approved_conservative_estimate", "verified_upper_bound"):
        raise ReadinessError("invalid_accounting_basis")
    if not isinstance(budget["rows"], list) or len(budget["rows"]) != 4:
        raise ReadinessError("full_batch_rows_required")
    seen, reservation, expected = set(), Decimal(0), Decimal(0)
    for row in budget["rows"]:
        fields(row, "product_id stage count expected_each_usd reservation_each_usd full_native_workflow max_requests_each")
        identity = (row["product_id"], row["stage"])
        if identity not in {(n, s) for n in names for s in ("fit", "materials")} or identity in seen:
            raise ReadinessError("invalid_budget_row")
        seen.add(identity)
        if type(row["count"]) is not int or row["count"] != (12 if row["stage"] == "fit" else 6):
            raise ReadinessError("full_batch_count_required")
        if type(row["max_requests_each"]) is not int or not 1 <= row["max_requests_each"] <= 100:
            raise ReadinessError("bounded_request_count_required")
        average, ceiling = amount(row["expected_each_usd"]), amount(row["reservation_each_usd"])
        if average <= 0 or ceiling < average:
            raise ReadinessError("invalid_stage_reservation")
        if row["full_native_workflow"] is not True:
            blockers.append(row["product_id"] + ":native_cost_incomplete")
        reservation += ceiling * row["count"]
        expected += average * row["count"]
    for product in products:
        combined = sum((amount(row["reservation_each_usd"]) for row in budget["rows"]
                        if row["product_id"] == product["id"]), Decimal(0))
        if amount(product["full_packet_cap_usd"]) < combined:
            blockers.append(product["id"] + ":full_packet_cap_below_fit_plus_materials")
    scoring = amount(budget["paid_scoring_reservation_usd"])
    contingency = amount(budget["contingency_usd"])
    total = amount(budget["total_cap_usd"])
    reservation += scoring + contingency
    if total < reservation:
        blockers.append("total_cap_below_complete_batch_reservations")
    funding = manifest["funding"]
    fields(funding, "state approval_reference approved_manifest_sha256")
    if funding["state"] not in ("pending", "approved", "declined"):
        raise ReadinessError("invalid_funding_state")
    # Approval binds the content without funding itself, avoiding a circular hash.
    frozen = {k: v for k, v in manifest.items() if k != "funding"}
    digest = hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    if funding["state"] != "approved":
        blockers.append("funding_not_recorded_approved")
    elif (not isinstance(funding["approval_reference"], str) or not funding["approval_reference"].strip()
            or funding["approved_manifest_sha256"] != digest):
        blockers.append("funding_binding_missing_or_stale")
    return {"schema": "study-readiness-result-v2", "study_id": manifest["study_id"],
            "frozen_content_sha256": digest, "declared_evidence_complete": not blockers,
            "blockers": sorted(set(blockers)), "expected_generation_usd": str(expected),
            "complete_batch_reservation_usd": str(reservation), "total_cap_usd": str(total),
            "authorization": "not_established_by_validator",
            "limits": "Hashes and declarations require independent review; no authenticity, pricing freshness, cost bound or human approval is established."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("manifest", help="Explicit relative path inside the evidence root")
    args = parser.parse_args()
    try:
        root = evidence_root(args.root)
        result = audit(args.root, read_json(regular_path(root, args.manifest)))
        print(json.dumps(result, sort_keys=True))
        return 0 if result["declared_evidence_complete"] else 1
    except (ReadinessError, OSError, TypeError, KeyError, RecursionError):
        print(json.dumps({"schema": "study-readiness-result-v2", "error": "invalid_or_unreadable_evidence", "authorization": "not_established_by_validator"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
