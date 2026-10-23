"""Read-only integrity and denominator checks for synthetic comparison evidence.

An inventory is a declared baseline, not proof of authenticity or approval.
Never discovers files, reads credentials, calls providers, or modifies evidence.
"""
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

MAX_BYTES = 2 * 1024 * 1024


class EvidenceError(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate_json_key")
        result[key] = value
    return result


def decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError("nonfinite_json")))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise EvidenceError("invalid_json") from error


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", value):
        raise EvidenceError("invalid_amount")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise EvidenceError("invalid_amount") from error
    if result > 1_000_000:
        raise EvidenceError("invalid_amount")
    return result


def safe_path(root, name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or any(ord(char) < 32 for char in name)):
        raise EvidenceError("invalid_relative_path")
    parts = PurePosixPath(name)
    if parts.is_absolute() or any(part in (".", "..") for part in name.split("/")):
        raise EvidenceError("invalid_relative_path")
    path = root
    for part in parts.parts:
        path = path / part
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise EvidenceError("linked_path_refused")
    if not path.resolve().is_relative_to(root):
        raise EvidenceError("path_outside_evidence_root")
    return path


def read_bytes(path):
    if path.stat().st_size > MAX_BYTES:
        raise EvidenceError("oversized_evidence")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise EvidenceError("oversized_evidence")
    return raw


def audit(root, inventory):
    """Audit explicitly declared operations; return no source text or raw errors."""
    root = Path(root).resolve(strict=True)
    if (not isinstance(inventory, dict) or set(inventory) != {"schema", "operations"}
            or inventory["schema"] != "synthetic-evidence-inventory-v1"
            or not isinstance(inventory["operations"], list) or not 1 <= len(inventory["operations"]) <= 200):
        raise EvidenceError("invalid_inventory")
    seen, paths = set(), set()
    for row in inventory["operations"]:
        if (not isinstance(row, dict) or set(row) != {"call_id", "receipt", "sha256"}
                or not isinstance(row["call_id"], str)
                or not isinstance(row["receipt"], str)
                or not re.fullmatch(r"(?:CT|UP)-V(?:0[1-9]|1[0-2])-(?:fit|materials)-a[12]", row["call_id"])
                or row["call_id"] in seen or row["receipt"] in paths
                or not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])):
            raise EvidenceError("invalid_operation_declaration")
        safe_path(root, row["receipt"])
        seen.add(row["call_id"])
        paths.add(row["receipt"])
    totals = dict(declared=len(seen), missing=0, invalid=0, blocked=0, attempted=0,
                  product_success=0, attempted_failure=0, unresolved=0, known_cost_operations=0)
    known_cost, retained = Decimal(0), Decimal(0)
    results = []
    for declaration in inventory["operations"]:
        row = {"call_id": declaration["call_id"]}
        path = safe_path(root, declaration["receipt"])
        if not path.exists():
            row["status"] = "missing"
            totals["missing"] += 1
            results.append(row)
            continue
        try:
            raw = read_bytes(path)
            if hashlib.sha256(raw).hexdigest() != declaration["sha256"]:
                raise EvidenceError("receipt_hash_mismatch")
            receipt = decode(raw)
            if not isinstance(receipt, dict) or receipt.get("call_id") != declaration["call_id"]:
                raise EvidenceError("receipt_identity_mismatch")
            dispatches = receipt.get("generation_dispatches")
            outcome = receipt.get("outcome")
            if type(dispatches) is not int or dispatches not in (0, 1):
                raise EvidenceError("invalid_dispatch_count")
            valid_outcomes = {0: {"blocked_before_dispatch"}, 1: {
                "success", "attempted_failure", "paused_pending_reconciliation", "outcome_unknown"}}
            if not isinstance(outcome, str) or outcome not in valid_outcomes[dispatches]:
                raise EvidenceError("outcome_dispatch_mismatch")
            actual = receipt.get("actual_usd")
            reserved = receipt.get("reservation_usd")
            actual_amount = None if actual is None else amount(actual)
            reserved_amount = None if reserved is None else amount(reserved)
            if dispatches and (reserved_amount is None or reserved_amount <= 0):
                raise EvidenceError("missing_reservation")
            if not dispatches and reserved_amount not in (None, Decimal(0)):
                raise EvidenceError("blocked_operation_has_reservation")
            if not dispatches and actual_amount is not None:
                raise EvidenceError("blocked_operation_has_charge")
            output_hash = receipt.get("product_output_sha256")
            product_success = output_hash is not None
            if outcome == "success" and not product_success:
                raise EvidenceError("missing_product_output")
            if product_success:
                if not dispatches or not isinstance(output_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", output_hash):
                    raise EvidenceError("invalid_product_output_binding")
                output = safe_path(root, str(PurePosixPath(declaration["receipt"]).parent / "product-output.json"))
                if hashlib.sha256(read_bytes(output)).hexdigest() != output_hash:
                    raise EvidenceError("product_output_hash_mismatch")
            # All validation precedes aggregation so malformed rows cannot inflate success.
            row.update(status=outcome, product_output_verified=product_success,
                       actual_usd=actual, reservation_usd=reserved)
            totals["attempted" if dispatches else "blocked"] += 1
            totals["product_success"] += int(product_success)
            totals["attempted_failure"] += int(outcome == "attempted_failure")
            if dispatches and actual_amount is None:
                totals["unresolved"] += 1
                retained += reserved_amount
            if actual_amount is not None:
                totals["known_cost_operations"] += 1
                known_cost += actual_amount
        except (EvidenceError, OSError) as error:
            row = {"call_id": declaration["call_id"], "status": "invalid",
                   "reason": str(error) if isinstance(error, EvidenceError) else "unreadable_evidence"}
            totals["invalid"] += 1
        results.append(row)
    return {"schema": "synthetic-evidence-audit-v1", "counts": totals, "operations": results,
            "known_cost_subtotal_usd": str(known_cost), "unresolved_reservation_usd": str(retained),
            "complete_actual_cost_usd": str(known_cost) if not any(totals[k] for k in ("missing", "invalid", "unresolved")) else None,
            "limitations": ["Declared inventory may omit operations; completeness requires reviewer verification",
                            "Hashes establish byte consistency, not authenticity or product quality",
                            "Retained reservation is exposure accounting, not actual provider charge",
                            "No participant, discovery, semantic quality, or visual acceptance measured"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--inventory", required=True)
    args = parser.parse_args()
    try:
        result = audit(args.root, decode(read_bytes(Path(args.inventory))))
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if any(result["counts"][key] for key in ("missing", "invalid", "unresolved")) else 0
    except (EvidenceError, OSError):
        print('{"status":"refused","reason":"invalid_or_unreadable_inventory"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
