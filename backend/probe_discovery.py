"""Operator-run, generic public discovery trial; no profiles or stored secrets."""
import argparse
from datetime import datetime, timezone
import json
import os

from discovery_sources import Budget, CATALOG, SourceError, feed_candidate, feed_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serper", action="store_true", help="Unavailable: Serper use is suspended.")
    parser.add_argument("--details", action="store_true", help="Inspect at most one public posting per employer without AI or stored profiles (up to 15).")
    args = parser.parse_args()
    if args.serper:
        parser.error("Serper use is suspended pending renewed explicit operator approval.")
    os.environ["CAREERTRELLIS_CONFIG_READ_ONLY"] = "1"
    budget = Budget(lambda: None)
    receipt = {"observed_at": datetime.now(timezone.utc).isoformat(), "feeds": [], "managed": [],
               "scope": "public_feed_sample_details" if args.details else "transport_and_url_controls_only", "beta_ready": False}
    for provider, board in CATALOG:
        try:
            # One page per employer keeps this transport probe fair and bounded.
            # This is not a complete-market or relevance acceptance test.
            rows = feed_rows(provider, board, budget, page_index=0, keywords=["Engineer"])
            candidates = [feed_candidate(provider, board, row) for row in rows]
            eligible = [row for row in candidates if row is not None]
            result = {"provider": provider, "board": board, "rows": len(rows),
                      "eligible": len(eligible), "result": "ok",
                      "page_scope_complete": rows.complete}
            if args.details and eligible:
                from searcher import inspect_job_posting
                detail = inspect_job_posting(eligible[0]["url"])
                fields = detail.get("details", {})
                usable = detail.get("status") == "ok" and all(fields.get(name) for name in ("title", "company", "description"))
                result["detail_sample"] = "ok" if usable else "unusable"
                if not usable:
                    result["result"] = "detail_sample_failed"
            elif args.details:
                result["detail_sample"] = "no_sample"
            receipt["feeds"].append(result)
        except SourceError as exc:
            receipt["feeds"].append({"provider": provider, "board": board, "result": exc.code})
    receipt["requests"] = budget.requests
    receipt["managed_requests"] = budget.serper_requests
    if args.details:
        receipt["detail_sample_limit"] = len(CATALOG)
        receipt["requests_scope"] = "feed_requests_only; detail parser requests excluded"
    receipt["feed_scope"] = "first_page_per_employer; SmartRecruiters keyword Engineer"
    print(json.dumps(receipt, sort_keys=True))
    return 0 if all(row["result"] == "ok" for row in receipt["feeds"] + receipt["managed"]) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print('{"result":"probe_failed"}')
        raise SystemExit(1)
